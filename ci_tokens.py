#!/usr/bin/env python3
"""Stamp herdr spaces and agent panes with git + forge status as $tokens.

One script for both surfaces, because they want the same PR list and polling
them separately fetched it twice.

Tokens, on both spaces and agent panes:

    $branch    fix/careteam-knowledgebase-gap
    $git       ~2 ↑1   dirty files, unpushed commits; absent when clean
    $pr        #7080   draft PRs are bracketed: [#6110]
    $status    ✓ ◐     CI then review, space-separated, padded — see NONE below
    $ci        ✓ pass  ● running  ✗ fail
    $review    whatever your review_labels config says; empty when unconfigured
    $merged    ⋎ the PR landed — this worktree is cleanup now
    $conflict  ⚠ only when the branch actually conflicts

`branch` and `git_status` are built-in tokens on SPACE rows but rejected on
AGENT rows, which is why the git facts are pushed as custom $tokens — the same
row config then works on either surface.

Usage:
    ci_tokens.py                 one pass, using cache if fresh
    ci_tokens.py --fresh         ignore cache (use on launch)
    ci_tokens.py --watch 30      loop forever, one pass every 30s
    ci_tokens.py --max-age 120   how stale the cached PR list may be, seconds
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import forge
from proc import log, run

TOKENS = ("branch", "git", "pr", "ci", "review", "status", "merged", "conflict")
SOURCE = "ci-tokens"

# The cache stores normalised records, so a shape change has to invalidate it.
# Bumping this is cheaper than a migration and the only cost is one refetch.
CACHE_VERSION = 2

# Glyphs alone can't say which column they belong to, so $status holds both in
# fixed order — CI then review — and pads an absent one rather than dropping it.
# Position is the label. The individual $ci/$review tokens stay available, but
# they collapse when empty and shift everything left, which is exactly what
# makes them unreadable as bare symbols.
#
# The pad is U+2800 BRAILLE PATTERN BLANK, not a space: herdr trims token
# values, so a leading or trailing space is stripped and a whitespace-only value
# is dropped outright — measured, both of which break the alignment this exists
# for. U+2800 renders blank, occupies one cell, and survives the trim.
NONE = "⠀"
GAP = " "

# The PR landed: this worktree is now cleanup, not work in flight.
MERGED = "⋎"

DEFAULTS = {"poll_seconds": 30, "cache_max_age_seconds": 120}


def config_dir():
    return os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or os.path.expanduser(
        "~/.config/herdr-ci-tokens")


def state_dir():
    return os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser(
        "~/.local/state/herdr-ci-tokens")


def load_config():
    """Config is optional. Absent, unreadable or unparseable all mean defaults.

    A broken config must not take the CI column down with it — that column is
    the reason most people install this, and it needs no configuration at all.
    """
    path = os.path.join(config_dir(), "config.toml")
    try:
        import tomllib
    except ImportError:
        if os.path.exists(path):
            log(f"python {sys.version_info.major}.{sys.version_info.minor} has no tomllib "
                f"(needs 3.11+); ignoring {path} and running CI-only")
        return dict(DEFAULTS, review_labels=[], review_gate=None)
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except OSError:
        raw = {}
    except ValueError as e:
        log(f"unparseable {path}: {e}; running with defaults")
        raw = {}
    gate = raw.get("review_gate") or None
    if gate and not (gate.get("label") and gate.get("glyph")):
        log("review_gate needs both `label` and `glyph`; ignoring it")
        gate = None
    return {
        "poll_seconds": raw.get("poll_seconds", DEFAULTS["poll_seconds"]),
        "cache_max_age_seconds": raw.get("cache_max_age_seconds",
                                         DEFAULTS["cache_max_age_seconds"]),
        "review_labels": [r for r in raw.get("review_labels", [])
                          if r.get("match") and r.get("glyph")],
        "review_gate": gate,
    }


def ci_glyph(checks):
    """Worst-wins over the check rollup, allow-listing what counts as passing.

    The allow-list is the point: an unrecognised conclusion (STALE,
    ACTION_REQUIRED, a state the forge adds next year) counts as a failure
    rather than silently reading green, and an empty conclusion means the check
    has not concluded yet — pending, not pass.
    """
    if not checks:
        return ""
    failed = pending = 0
    for concl in checks:
        if concl in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            continue
        if concl in ("", "PENDING", "QUEUED", "IN_PROGRESS", "WAITING"):
            pending += 1
        else:
            failed += 1
    return "✗" if failed else ("●" if pending else "✓")


def review_glyph(labels, rules, gate):
    """First matching rule wins; the gate covers "nobody has looked yet".

    The gate label means *a reviewer has been here*, whatever the verdict. Its
    absence is therefore the only honest way to say "queued" — a PR with no
    verdict label and no gate has not been picked up, while one with the gate
    and no verdict was looked at and had nothing said about it.
    """
    names = set(labels or [])
    for rule in rules:
        if rule["match"] in names:
            return rule["glyph"]
    if gate and gate["label"] not in names:
        return gate["glyph"]
    return ""


def derive(data, cfg):
    """branch -> tokens, from cached normalised PR records.

    Open beats merged: a branch can have a landed PR and a fresh one open on it,
    and the open one is the live work.
    """
    rules, gate = cfg["review_labels"], cfg["review_gate"]
    reviewing = bool(rules or gate)
    out = {}
    for pr in data.get("merged", []):
        out[pr["branch"]] = {"pr": f"#{pr['number']}", "merged": MERGED}
    for pr in data.get("open", []):
        ci = ci_glyph(pr.get("checks"))
        review = review_glyph(pr.get("labels"), rules, gate)
        n = f"#{pr['number']}"
        # With no review config there is no second column, so no pad — a lone
        # CI glyph should not sit next to a blank cell forever.
        cols = (ci, review) if reviewing else (ci,)
        out[pr["branch"]] = {
            "pr": f"[{n}]" if pr.get("draft") else n,
            "ci": ci,
            "review": review,
            "status": GAP.join(g or NONE for g in cols),
            "conflict": "⚠" if pr.get("conflicting") else "",
        }
    return out


def cache_path():
    return os.path.join(state_dir(), "cache.json")


def load_cache():
    try:
        with open(cache_path()) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in cache.items() if v.get("version") == CACHE_VERSION}


def save_cache(cache):
    """Atomic: a crash mid-write leaves unparseable JSON, and the loader above
    silently resets to {} — which is the same total wipe the retry policy in
    forge_status() exists to prevent."""
    try:
        os.makedirs(state_dir(), exist_ok=True)
        tmp = cache_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f)
        os.replace(tmp, cache_path())
    except OSError as e:
        log(f"cache write failed: {e}")


def forge_status(roots, max_age, cfg):
    """({repo_root: {branch: tokens}}, unavailable_roots), refetching only aged-out entries.

    A failed fetch keeps the previous entry — its data *and* its timestamp — so
    the next pass retries immediately instead of caching the failure for
    max_age. The cache holds the last known good answer, and a fetch that could
    not answer never overwrites it. Without this one transient forge failure
    cleared every token and collapsed the sidebar rows for two minutes.

    A root with no good data at all is returned as unavailable so the caller
    leaves its workspaces alone rather than clearing them.

    The cache holds facts, not glyphs, deliberately: caching derived glyphs
    meant a CI transition stayed invisible until the entry aged out, which is a
    confusing half-hour. Glyphs are re-derived every pass, so a config edit
    shows up on the very next one.
    """
    cache, now, unavailable = load_cache(), time.time(), set()
    for root in roots:
        entry = cache.get(root)
        if entry and now - entry.get("at", 0) < max_age:
            continue
        adapter = forge.adapter_for(root)
        if not adapter:
            cache[root] = {"at": now, "version": CACHE_VERSION,
                           "data": {"open": [], "merged": []}}
            continue
        data = adapter.fetch(root)
        if data is None:
            if entry:
                log(f"fetch failed for {root}; serving cache from "
                    f"{int(now - entry.get('at', 0))}s ago")
            else:
                log(f"fetch failed for {root} with no cache; leaving its tokens untouched")
                unavailable.add(root)
            continue
        cache[root] = {"at": now, "version": CACHE_VERSION, "data": data}
    save_cache(cache)
    for root in roots:
        if root not in cache:
            unavailable.add(root)
    return ({r: derive(cache.get(r, {}).get("data", {}), cfg) for r in roots}, unavailable)


def git_facts(path):
    """(branch, dirty/ahead marks) for a checkout, or None when it isn't a repo."""
    branch = run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"]) or ""
    if not branch:
        return None
    dirty = len((run(["git", "-C", path, "status", "--porcelain"]) or "").splitlines())
    # A branch with no upstream has nothing to be ahead of — an answer, not a failure.
    ahead = run(["git", "-C", path, "rev-list", "--count", "@{u}..HEAD"], quiet=True) or ""
    marks = ([f"~{dirty}"] if dirty else []) + ([f"↑{ahead}"] if ahead not in ("", "0") else [])
    return branch, " ".join(marks)


def stamp(kind, target, values, ttl_ms):
    """One report-metadata call: set what we have, clear what we don't."""
    args = ["herdr", kind, "report-metadata", target, "--source", SOURCE,
            "--ttl-ms", str(ttl_ms)]
    for name in TOKENS:
        v = values.get(name)
        args += ["--token", f"{name}={v}"] if v else ["--clear-token", name]
    run(args)


def sweep(max_age, ttl_ms, cfg):
    spaces = json.loads(run(["herdr", "workspace", "list"]) or '{"result":{"workspaces":[]}}')
    panes = json.loads(run(["herdr", "pane", "list"]) or '{"result":{"panes":[]}}')
    spaces = spaces["result"]["workspaces"]

    roots = {w["worktree"]["repo_root"] for w in spaces if w.get("worktree")}
    prs, unavailable = forge_status(sorted(roots), max_age, cfg)

    # One git call per distinct checkout, shared by the space and every pane in it.
    facts, by_path = {}, {}
    for w in spaces:
        wt = w.get("worktree")
        if not wt:
            continue
        # No forge answer for this repo and nothing cached: leave its tokens to
        # live out their TTL rather than blanking the row on our way past.
        if wt["repo_root"] in unavailable:
            continue
        path = wt["checkout_path"]
        if path not in facts:
            facts[path] = git_facts(path)
        got = facts[path]
        if not got:
            continue
        branch, marks = got
        vals = {"branch": branch, "git": marks,
                **prs.get(wt["repo_root"], {}).get(branch, {})}
        by_path[path] = vals
        stamp("workspace", w["workspace_id"], vals, ttl_ms)

    for p in panes["result"]["panes"]:
        if p.get("agent") and p.get("cwd") in by_path:
            stamp("pane", p["pane_id"], by_path[p["cwd"]], ttl_ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, metavar="SECONDS")
    ap.add_argument("--max-age", type=int, help="cached PR list lifetime, seconds")
    ap.add_argument("--fresh", action="store_true", help="ignore the cache this pass")
    a = ap.parse_args()

    cfg = load_config()
    watch = a.watch if a.watch is not None else None
    max_age = a.max_age if a.max_age is not None else cfg["cache_max_age_seconds"]
    # The TTL has to outlive the gap between passes or tokens blink out between them.
    interval = watch or cfg["poll_seconds"]
    while True:
        sweep(0 if a.fresh else max_age, (interval + 60) * 1000, cfg)
        if watch is None:
            return
        time.sleep(watch)


if __name__ == "__main__":
    main()
