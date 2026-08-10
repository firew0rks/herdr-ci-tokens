#!/usr/bin/env bash
# Print the sidebar rows to paste into herdr's config.toml.
#
# A plugin cannot write that file — herdr owns it — so this is the one manual
# step. Without a row referencing the tokens the poller runs perfectly and
# displays nothing.
cat <<'ROWS'
# --- ci-tokens: paste into ~/.config/herdr/config.toml ---
#
# $status is two glyphs in fixed order — CI then review — padded so the column
# never shifts. Bare symbols are only readable if position holds.

[ui.sidebar.spaces]
rows = [
	["state_icon", { token = "$status" }, "workspace", "git_status", { token = "$merged" }, { token = "$conflict", dim = false }, { token = "$pr", dim = true }],
]

# `branch` and `git_status` are built-in tokens on space rows but rejected on
# agent rows, which is why the git facts arrive as custom $tokens instead.
[ui.sidebar.agents]
rows = [
	["state_icon", { token = "tab", dim = false }, { token = "$status" }, { token = "$merged" }, { token = "$pr", dim = true }, { token = "$conflict" }],
	[{ token = "terminal_title_stripped", dim = true }],
	["workspace"],
]
ROWS
