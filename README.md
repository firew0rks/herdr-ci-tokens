# herdr-ci-tokens

Show PR, CI and review status for every worktree in your [herdr](https://github.com/persiyanov/herdr) sidebar.

```
✓ ✓  project-indi          ~2 ↑1  #8090
● ○  fix-timeline-sort            #8061
✗ ✗  feat-village-notify   ~7     [#8102]  ⚠
     main
⋎    fix-grant-type               #7985
```

Reading the columns: CI passed and review is clean · CI running, review not picked up yet · CI failed and review wants fixes, on a draft PR with a merge conflict · no PR · PR landed, this worktree is cleanup now.

When you run several agents in parallel, the question you ask most is "which of these is actually finished?" — and answering it means tabbing through worktrees to run `gh pr checks`. This puts the answer in the row you were already looking at.

## Install

```bash
herdr plugin install firew0rks/herdr-ci-tokens
```

That downloads the plugin and, on the next herdr start, installs a small background poller under systemd (Linux) or launchd (macOS). To skip the wait:

```bash
herdr plugin action invoke install-service --plugin firew0rks.ci-tokens
```

**Then paste the sidebar rows** — this is the one step the plugin cannot do for you, because herdr owns `config.toml`:

```bash
herdr plugin action invoke sidebar-config --plugin firew0rks.ci-tokens
```

Copy its output into `~/.config/herdr/config.toml`. Until you do, the poller runs perfectly and displays nothing.

Requirements: herdr 0.7.5+, Python 3.8+, and [`gh`](https://cli.github.com) authenticated. No third-party packages; config parsing falls back to a built-in parser on pythons without `tomllib`, so stock macOS python works.

## Tokens

| Token | Meaning |
|---|---|
| `$status` | `$ci` then `$review`, fixed order, padded so the column never shifts |
| `$ci` | `✓` pass · `●` running · `✗` fail |
| `$review` | whatever your review-label config says; empty when unconfigured |
| `$pr` | `#7080`, or `[#7080]` for a draft |
| `$branch` | current branch |
| `$git` | `~2 ↑1` — dirty files, unpushed commits; absent when clean |
| `$merged` | `⋎` the PR landed |
| `$conflict` | `⚠` the branch actually conflicts |

Use them in `[ui.sidebar.spaces]` or `[ui.sidebar.agents]` rows, with the usual per-token `fg`, `bold` and `dim` styling. `$status` exists because a lone glyph cannot say which column it belongs to: it renders both, padding an absent one rather than dropping it, so position stays meaningful.

## Review labels

Out of the box only the CI column appears. If your repo has a review bot that applies labels, tell the plugin what they mean in `$(herdr plugin config-dir firew0rks.ci-tokens)/config.toml`:

```toml
poll_seconds = 30
cache_max_age_seconds = 120

# First match wins, so order is precedence.
[[review_labels]]
match = "needs-changes"
glyph = "✗"

[[review_labels]]
match = "approved"
glyph = "✓"

# Optional. The gate means "a reviewer has been here", whatever the verdict.
# Its absence is the only honest way to say "queued".
[review_gate]
label = "reviewed"
glyph = "○"
```

`presets/zealot.toml` is a filled-in example for a `zealot:*` labelled pipeline.

## How it works

A poller asks `gh pr list` once per repo (not per worktree), derives the glyphs, and stamps them onto every space and agent pane with `herdr {workspace,pane} report-metadata --source ci-tokens`. Tokens carry a TTL longer than the poll interval, so they never blink out between passes.

`worktree.created`, `workspace.created` and `pane.agent_detected` trigger an extra sweep, scoped to the workspace that fired the event and reading **only** from cache — so a new worktree lights up the moment it appears, in a fraction of a second, without spending a network call. The poll loop keeps sole ownership of fetching.

Two design decisions worth knowing, because both are load-bearing:

**A failed fetch is not an empty one.** The cache keeps the last good answer *and its timestamp* when the forge cannot be reached, so the next pass retries immediately rather than caching the failure. Without this, one flaky `gh` call blanks every row in the sidebar.

**The cache holds facts, not glyphs.** Glyphs are re-derived every pass, so a CI transition shows up as soon as the data does, and a config edit lands on the very next sweep.

CI status is worst-wins over an allow-list: `SUCCESS`, `NEUTRAL` and `SKIPPED` pass, an unconcluded check is pending, and *anything else* fails. An unrecognised conclusion reads red rather than silently green.

## Other forges

GitHub only, today. The forge boundary is two functions — `supports(root)` and `fetch(root)` returning normalised records — in `forge/`. Everything downstream is forge-agnostic already, so a GitLab or Azure adapter is one file plus a `supports()` clause. PRs welcome.

## Troubleshooting

**Nothing renders.** Almost always the missing sidebar rows — run the `sidebar-config` action above. Confirm the poller is alive with the `ci-tokens: service status` action.

**Tokens vanished.** Check the journal (`journalctl --user -u herdr-ci-tokens -n 50`). `serving cache from Ns ago` means the forge is unreachable and the last known answer is being held, which is working as intended.

**A repo shows no glyphs at all.** Non-GitHub remotes are skipped deliberately, so an unsupported host reads as *no forge* rather than *broken forge*.

**Everything stamps but the values are stale.** Lower `cache_max_age_seconds`. It defaults to 120s to keep API usage modest across many worktrees.

## Uninstall

```bash
bash uninstall.sh                              # stop and remove the service
herdr plugin uninstall firew0rks.ci-tokens     # remove the plugin
```

## License

MIT
