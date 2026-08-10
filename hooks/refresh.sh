#!/usr/bin/env bash
# One sweep, right now.
#
# Default is cache-only (`--max-age` far beyond any plausible cache entry), so
# the event hooks that call this on worktree/workspace/agent creation stamp the
# new surface immediately without ever reaching the forge. The poll loop keeps
# sole ownership of fetching.
#
# `--fresh` bypasses the cache; that is the manual "refresh now" action.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "${1:-}" = "--fresh" ]; then
  exec python3 "$ROOT/ci_tokens.py" --fresh
fi
exec python3 "$ROOT/ci_tokens.py" --max-age 86400
