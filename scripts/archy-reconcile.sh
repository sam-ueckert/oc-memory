#!/usr/bin/env bash
# Wrapper for archy-reconcile.py: prevents overlapping runs and logs output.
# Intended to run periodically via launchd (StartInterval), not KeepAlive —
# each invocation is a single reconcile pass, not a long-lived daemon.
set -uo pipefail

LOCK_FILE="/tmp/archy-reconcile.lock"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$LOCK_FILE" ]; then
    pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "archy-reconcile already running (pid=$pid), exiting"
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

python3 "$SCRIPT_DIR/archy-reconcile.py"
