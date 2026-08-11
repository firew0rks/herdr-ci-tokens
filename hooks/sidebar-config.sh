#!/usr/bin/env bash
# Print the sidebar rows to paste into herdr's config.toml.
#
# A plugin cannot write that file — herdr owns it — so this is the one manual
# step. Without a row referencing the tokens the poller runs perfectly and
# displays nothing.
#
# Spaces only. The same tokens work on `[ui.sidebar.agents]` rows, but agent
# rows are where people put their own layout, and printing a block to paste
# over it does more harm than the one it saves.
cat <<'ROWS'
# --- ci-tokens: paste into ~/.config/herdr/config.toml ---
#
# $status is two glyphs in fixed order — CI then review — padded so the column
# never shifts. Bare symbols are only readable if position holds.

[ui.sidebar.spaces]
rows = [
	["state_icon", { token = "$status" }, "workspace", "git_status", { token = "$merged" }, { token = "$conflict", dim = false }, { token = "$pr", dim = true }],
]

# The same tokens render on agent rows too — add them to your own
# [ui.sidebar.agents] rows if you want them there. Note that `branch` and
# `git_status` are built-ins on space rows but rejected on agent rows, which is
# why the git facts travel as custom $tokens.
ROWS
