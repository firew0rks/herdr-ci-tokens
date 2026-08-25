"""GitHub adapter, via the `gh` CLI.

`gh` rather than the REST API because it already owns the auth the user set up,
including SSO and enterprise hosts, and because failing when `gh` is missing is
a better error than failing when a token is stale.
"""
import json

from proc import log, run


def supports(root):
    url = run(["git", "-C", root, "remote", "get-url", "origin"], quiet=True) or ""
    return "github.com" in url or "github" in url


def fetch(root):
    """Two queries, not one `--state all`.

    The open set carries the check rollup, which is the slow part of the call.
    Running it over every merged PR in a long-lived repo's history costs seconds
    to answer a question the merged set only needs two fields for ("is this
    worktree finished?").
    """
    open_ = run(["gh", "pr", "list", "--state", "open", "--limit", "200", "--json",
                 "number,headRefName,isDraft,statusCheckRollup,mergeable,labels"], cwd=root)
    merged = run(["gh", "pr", "list", "--state", "merged", "--limit", "200",
                  "--json", "number,headRefName"], cwd=root)
    # Either half failing fails the whole fetch. A half-result would read as
    # "every open PR just closed", which is worse than serving the previous
    # answer a little longer.
    if open_ is None or merged is None:
        return None
    try:
        return {
            "open": [_open_pr(pr) for pr in json.loads(open_ or "[]")],
            "merged": [{"number": pr["number"], "branch": pr["headRefName"]}
                       for pr in json.loads(merged or "[]")],
        }
    except (ValueError, KeyError, TypeError) as e:
        log(f"unparseable gh output for {root}: {e}")
        return None


def _open_pr(pr):
    return {
        "number": pr["number"],
        "branch": pr["headRefName"],
        "draft": bool(pr.get("isDraft")),
        "checks": _latest_checks(pr.get("statusCheckRollup") or []),
        "conflicting": pr.get("mergeable") == "CONFLICTING",
        "labels": [l["name"] for l in pr.get("labels") or [] if "name" in l],
    }


def _latest_checks(rollup):
    """One conclusion per check, because the rollup keeps the superseded runs.

    A re-run does not replace its predecessor: both rows come back for the same
    commit, so a cancelled first attempt would pin a PR red forever while the
    web UI and `gh pr checks` — which both collapse by name — read it green.

    The key is `gh`'s: the context for a commit status, name *plus workflow* for
    a check run, since two workflows may legitimately define the same job name
    and collapsing those together would hide one of them. Newest start wins,
    completion breaking a tie; the timestamps are ISO-8601 in UTC, so comparing
    them as strings is comparing them chronologically.
    """
    latest = {}
    for c in rollup:
        key = c.get("context") or (c.get("name"), c.get("workflowName"))
        at = (c.get("startedAt") or c.get("createdAt") or "", c.get("completedAt") or "")
        # Checks report `conclusion`; older commit statuses report `state`. An
        # entry carrying neither has not concluded, which is pending, not pass.
        if key in latest and at < latest[key][0]:
            continue
        latest[key] = (at, (c.get("conclusion") or c.get("state") or "").upper())
    return [concl for _, concl in latest.values()]
