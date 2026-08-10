#!/usr/bin/env bash
# Stop and remove the service. Leaves config, cache and your sidebar rows alone.
set -euo pipefail
case "$(uname -s)" in
  Linux)
    systemctl --user disable --now herdr-ci-tokens.service >/dev/null 2>&1 || true
    rm -f "$HOME/.config/systemd/user/herdr-ci-tokens.service"
    systemctl --user daemon-reload
    ;;
  Darwin)
    label="com.firew0rks.herdr-ci-tokens"
    launchctl bootout "gui/$UID/$label" >/dev/null 2>&1 || true
    rm -f "$HOME/Library/LaunchAgents/$label.plist"
    ;;
esac
echo "service removed. 'herdr plugin uninstall firew0rks.ci-tokens' removes the plugin itself."
