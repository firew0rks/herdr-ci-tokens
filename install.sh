#!/usr/bin/env bash
# Install and start the ci-tokens poller under systemd (Linux) or launchd (macOS).
#
# Run directly after `herdr plugin install`/`plugin link`, or let the plugin's
# [[startup]] hook run it with --quiet. Idempotent either way: the service is
# rewritten only when the rendered unit actually differs, which matters because
# startup hooks re-fire on every live handoff.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUIET=""
[ "${1:-}" = "--quiet" ] && QUIET=1

say() { [ -n "$QUIET" ] || printf '%s\n' "$*"; }
warn() { printf 'ci-tokens: %s\n' "$*" >&2; }

# herdr is usually in ~/.local/bin, which is NOT on the user service manager's
# boot PATH. Without pinning it the daemon starts fine and every single stamp
# fails with ENOENT — silently, since nothing renders an error.
HERDR_BIN="${HERDR_BIN_PATH:-$(command -v herdr || true)}"
if [ -z "$HERDR_BIN" ]; then
  warn "herdr not found on PATH; install it first"
  exit 1
fi
BIN_DIR="$(dirname "$HERDR_BIN")"
SERVICE_PATH="$BIN_DIR:/usr/local/bin:/usr/bin:/bin"

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  warn "python3 not found on PATH"
  exit 1
fi
# Resolve to the real interpreter. `command -v` under asdf/pyenv/mise hands back
# a shim, and baking a shim into a service unit means the daemon silently
# follows whatever the user switches their global python to later — and on most
# setups the shim cannot even resolve without its version manager on PATH,
# which the service PATH deliberately does not carry.
PY="$("$PY" -c 'import sys; print(sys.executable)' 2>/dev/null || echo "$PY")"
# Prefer an interpreter with a real TOML parser. The plugin falls back to its
# own small parser when tomllib is missing (macOS ships python 3.9), so this is
# a preference and not a requirement — but stdlib parsing is stricter, and a
# Mac with Homebrew python usually has one available under a versioned name
# even when plain `python3` is Apple's.
if ! "$PY" -c 'import tomllib' 2>/dev/null; then
  for candidate in python3.14 python3.13 python3.12 python3.11; do
    cand_path="$(command -v "$candidate" 2>/dev/null || true)"
    if [ -n "$cand_path" ] && "$cand_path" -c 'import tomllib' 2>/dev/null; then
      PY="$("$cand_path" -c 'import sys; print(sys.executable)')"
      break
    fi
  done
fi
command -v gh >/dev/null 2>&1 || warn "gh not found on PATH; no GitHub repo will report status"
gh auth status >/dev/null 2>&1 || warn "gh is not authenticated; run 'gh auth login'"

CONFIG_DIR="${HERDR_PLUGIN_CONFIG_DIR:-$(herdr plugin config-dir firew0rks.ci-tokens 2>/dev/null || echo "$HOME/.config/herdr-ci-tokens")}"
STATE_DIR="${HERDR_PLUGIN_STATE_DIR:-$HOME/.local/state/herdr-ci-tokens}"
mkdir -p "$CONFIG_DIR" "$STATE_DIR"

POLL="$(HERDR_PLUGIN_CONFIG_DIR="$CONFIG_DIR" "$PY" -c \
  "import sys; sys.path.insert(0,'$ROOT'); import ci_tokens; print(ci_tokens.load_config()['poll_seconds'])" \
  2>/dev/null || echo 30)"

# `plugin install` replaces the sources in place under a root named for the
# source spec, not the commit, so an upgrade renders a byte-identical unit and
# the check below would call it current and leave the daemon running the code
# it loaded at boot — an update that silently does nothing. Stamping the
# sources into the unit is what makes "the code changed" visible to a
# comparison that only ever looks at the unit. cksum because it is POSIX;
# sha256sum is not on macOS and shasum is not on every Linux.
CODE_STAMP="$(cat "$ROOT"/ci_tokens.py "$ROOT"/proc.py "$ROOT"/forge/*.py 2>/dev/null |
  cksum | cut -d' ' -f1)"

# --- migrate off a hand-rolled predecessor -----------------------------------
# Two pollers would fight over the same --source tokens, each clearing what the
# other set.
if systemctl --user list-unit-files herdr-tokens.service >/dev/null 2>&1 &&
   systemctl --user is-enabled herdr-tokens.service >/dev/null 2>&1; then
  warn "disabling the older herdr-tokens.service (its script is left on disk for you to remove)"
  systemctl --user disable --now herdr-tokens.service >/dev/null 2>&1 || true
fi

install_systemd() {
  local unit="$HOME/.config/systemd/user/herdr-ci-tokens.service" rendered
  rendered="$(cat <<UNIT
# source $CODE_STAMP — rendered by install.sh, compared not read
[Unit]
Description=Stamp herdr spaces and agent panes with git/CI/review status tokens
After=default.target

[Service]
Type=simple
Environment=PATH=$SERVICE_PATH
Environment=HERDR_PLUGIN_CONFIG_DIR=$CONFIG_DIR
Environment=HERDR_PLUGIN_STATE_DIR=$STATE_DIR
# --watch loops in-process, so the token TTL is derived from the interval and
# tokens never lapse between passes. The PR list is cached per repo, so a 30s
# sweep still only reaches the forge every cache_max_age_seconds.
ExecStart=$PY $ROOT/ci_tokens.py --watch $POLL
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
UNIT
)"
  mkdir -p "$(dirname "$unit")"
  if [ -f "$unit" ] && [ "$(cat "$unit")" = "$rendered" ]; then
    systemctl --user start herdr-ci-tokens.service >/dev/null 2>&1 || true
    say "service already current"
    return
  fi
  printf '%s\n' "$rendered" > "$unit"
  systemctl --user daemon-reload
  systemctl --user enable --now herdr-ci-tokens.service >/dev/null
  systemctl --user restart herdr-ci-tokens.service
  say "installed and started herdr-ci-tokens.service"
}

install_launchd() {
  local label="com.firew0rks.herdr-ci-tokens"
  local plist="$HOME/Library/LaunchAgents/$label.plist" rendered
  rendered="$(cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- source $CODE_STAMP — rendered by install.sh, compared not read -->
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$ROOT/ci_tokens.py</string>
    <string>--watch</string>
    <string>$POLL</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$SERVICE_PATH</string>
    <key>HERDR_PLUGIN_CONFIG_DIR</key><string>$CONFIG_DIR</string>
    <key>HERDR_PLUGIN_STATE_DIR</key><string>$STATE_DIR</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$STATE_DIR/stderr.log</string>
</dict>
</plist>
PLIST
)"
  mkdir -p "$(dirname "$plist")"
  if [ -f "$plist" ] && [ "$(cat "$plist")" = "$rendered" ]; then
    say "service already current"
    return
  fi
  printf '%s\n' "$rendered" > "$plist"
  launchctl bootout "gui/$UID/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$UID" "$plist"
  say "installed and started $label"
}

case "$(uname -s)" in
  Linux) install_systemd ;;
  Darwin) install_launchd ;;
  *) warn "unsupported platform $(uname -s)"; exit 1 ;;
esac

# Populate the sidebar now rather than at the first tick.
"$PY" "$ROOT/ci_tokens.py" --fresh >/dev/null 2>&1 || true

# --- the one thing this cannot install for you -------------------------------
# herdr owns config.toml; a plugin may not write it. Without a row referencing
# these tokens the poller runs perfectly and shows nothing, which is the single
# most confusing way this can fail.
HERDR_CONFIG="${HERDR_CONFIG_PATH:-$HOME/.config/herdr/config.toml}"
if ! grep -q '\$status\|\$ci\|\$pr' "$HERDR_CONFIG" 2>/dev/null; then
  warn "no ci-tokens row found in $HERDR_CONFIG — nothing will be visible until you add one:"
  bash "$ROOT/hooks/sidebar-config.sh" >&2
else
  say "sidebar rows already reference the tokens"
fi
