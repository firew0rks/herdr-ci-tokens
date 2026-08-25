"""GitHub adapter, via the `gh` CLI.

`gh` rather than the REST API because it already owns the auth the user set up,
including SSO and enterprise hosts, and because failing when `gh` is missing is
a better error than failing when a token is stale.
"""
import json
from concurrent.futures import ThreadPoolExecutor

from proc import log, run

# Each rollup call is one round-trip that spends nearly all of its time waiting,
# so the width hides latency rather than using cores. Eight covers a typical
# worktree count in a single batch.
ROLLUP_WORKERS = 8


def supports(root):
    url = run(["git", "-C", root, "remote", "get-url", "origin"], quiet=True) or ""
    return "github.com" in url or "github" in url


def fetch(root, branches):
    """Open and merged PRs, with check rollups only for `branches`.

    The rollup is the one expensive field, and the only one whose cost scales
    with the repo's backlog instead of with what the sidebar shows. Asking for
    it alongside the open list makes `gh` send a single GraphQL page covering
    every open PR in the repo; past ~50 PRs GitHub gives up and answers 504,
    which freezes every token for as long as the backlog stays that size.

    So the lists stay whole and cheap, and the rollup is fetched per PR, for the
    branches someone actually has checked out. That is a number that grows with
    the user's worktrees, not with the repo's age.

    Merged PRs need two fields and never needed a rollup, so they stay one call.
    """
    open_ = run(["gh", "pr", "list", "--state", "open", "--limit", "200", "--json",
                 "number,headRefName,isDraft,mergeable,labels"], cwd=root)
    merged = run(["gh", "pr", "list", "--state", "merged", "--limit", "200",
                  "--json", "number,headRefName"], cwd=root)
    # Either list failing fails the whole fetch. A half-result would read as
    # "every open PR just closed", which is worse than serving the previous
    # answer a little longer.
    if open_ is None or merged is None:
        return None
    try:
        open_prs = [_open_pr(pr) for pr in json.loads(open_ or "[]")]
        merged_prs = [{"number": pr["number"], "branch": pr["headRefName"]}
                      for pr in json.loads(merged or "[]")]
    except (ValueError, KeyError, TypeError) as e:
        log(f"unparseable gh output for {root}: {e}")
        return None

    wanted = set(branches)
    failed = _add_checks(root, [pr for pr in open_prs if pr["branch"] in wanted])
    return {
        "open": open_prs,
        "merged": merged_prs,
        # What this answer covers. A branch whose rollup failed is not covered,
        # so the caller asks again next pass rather than trusting a blank glyph
        # for the whole cache lifetime. A branch with no open PR *is* covered —
        # "no PR here" is an answer.
        "checked": sorted(wanted - failed),
    }


def _add_checks(root, prs):
    """Fill in `checks` on each PR, concurrently. Returns the branches that failed.

    A failed rollup leaves `checks` None, which renders as no glyph — an honest
    "not known" rather than a false pass — and drops its branch from the covered
    set so the next pass retries it.

    Deliberately not the all-or-nothing rule the two lists get. With one call per
    PR, that rule would hand a single flaky call a veto over every other row's
    refresh, which is the staleness this split exists to remove. It is safe here
    precisely because the unit is one PR: a blank glyph on one row states
    nothing false, while a truncated *list* would claim PRs had closed.
    """
    if not prs:
        return set()
    with ThreadPoolExecutor(max_workers=ROLLUP_WORKERS) as pool:
        rollups = list(pool.map(lambda pr: _rollup(root, pr["number"]), prs))
    failed = set()
    for pr, rollup in zip(prs, rollups):
        if rollup is None:
            failed.add(pr["branch"])
        else:
            pr["checks"] = _latest_checks(rollup)
    return failed


def _rollup(root, number):
    """One PR's check rollup, or None if it could not be fetched.

    Same node shape `gh pr list --json statusCheckRollup` returns, so
    `_latest_checks` reads it unchanged.
    """
    out = run(["gh", "pr", "view", str(number), "--json", "statusCheckRollup"], cwd=root)
    if out is None:
        return None
    try:
        return json.loads(out or "{}").get("statusCheckRollup") or []
    except ValueError as e:
        log(f"unparseable rollup for {root}#{number}: {e}")
        return None


def _open_pr(pr):
    return {
        "number": pr["number"],
        "branch": pr["headRefName"],
        "draft": bool(pr.get("isDraft")),
        # Stays None for every PR nobody has checked out, and for one whose
        # rollup failed. Both mean "not known", which derives to no glyph.
        "checks": None,
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
