#!/usr/bin/env bash
# Continuously materialize a local SQLite copy from a *local* litestream file
# replica via Litestream's follow mode. Pair with rsync-mirror-loop.sh, which
# keeps MIRROR_DIR synced from the remote primary — this script never talks
# to the network itself, so primary-host slowness/SFTP quirks can't affect it.
#
# Intended to run as a long-lived daemon (launchd, systemd, or supervisord)
# alongside oc_memory.local_replica_server, which reads from LOCAL_DB and
# forwards writes to REMOTE_URL. See docs/local-replica.md for the full setup.
#
# Env vars:
#   MIRROR_DIR        Local directory rsync-mirror-loop.sh keeps in sync with
#                      the primary's litestream file replica
#                      (default: ~/.oc-memory/litestream-mirror)
#   LOCAL_DB           Local path to restore/follow into
#                      (default: ~/.oc-memory/local-replica/memory.db)
#   FOLLOW_INTERVAL    Poll interval for new segments (default: 10s)
set -euo pipefail

MIRROR_DIR="${MIRROR_DIR:-$HOME/.oc-memory/litestream-mirror}"
LOCAL_DB="${LOCAL_DB:-$HOME/.oc-memory/local-replica/memory.db}"
FOLLOW_INTERVAL="${FOLLOW_INTERVAL:-10s}"

mkdir -p "$(dirname "$LOCAL_DB")" "$MIRROR_DIR"

command -v litestream >/dev/null 2>&1 || {
  echo "litestream not found on PATH — install: https://litestream.io/install/" >&2
  exit 1
}

exec litestream restore \
  -f \
  -follow-interval "$FOLLOW_INTERVAL" \
  -if-replica-exists \
  -o "$LOCAL_DB" \
  "file://$MIRROR_DIR"
