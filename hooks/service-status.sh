#!/usr/bin/env bash
# Is the poller alive, and what did it last complain about?
set -euo pipefail
case "$(uname -s)" in
  Linux)
    systemctl --user status herdr-ci-tokens.service --no-pager || true
    echo
    journalctl --user -u herdr-ci-tokens.service -n 30 --no-pager || true
    ;;
  Darwin)
    launchctl print "gui/$UID/com.firew0rks.herdr-ci-tokens" 2>&1 | head -30 || true
    echo
    tail -n 30 "${HERDR_PLUGIN_STATE_DIR:-$HOME/.local/state/herdr-ci-tokens}/stderr.log" 2>/dev/null || true
    ;;
esac
read -rp "press enter to close "
