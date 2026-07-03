#!/usr/bin/env bash
# Continuously mirror the remote Archy/oc-memory SQLite DB to a local file via
# Litestream's follow mode. Intended to run as a long-lived daemon (launchd,
# systemd, or supervisord) alongside oc_memory.local_replica_server, which
# reads from LOCAL_DB and forwards writes to REMOTE_URL. See
# docs/local-replica.md for the full setup.
#
# Env vars:
#   LITESTREAM_REPLICA_URL   sftp:// URL of the primary's litestream file
#                            replica directory (required)
#                            e.g. sftp://user@host:22/path/to/litestream-replica
#   LOCAL_DB                 Local path to restore/follow into
#                            (default: ~/.oc-memory/local-replica/memory.db)
#   FOLLOW_INTERVAL          Poll interval for new segments (default: 10s)
set -euo pipefail

: "${LITESTREAM_REPLICA_URL:?Set LITESTREAM_REPLICA_URL (sftp://user@host:port/path)}"
LOCAL_DB="${LOCAL_DB:-$HOME/.oc-memory/local-replica/memory.db}"
FOLLOW_INTERVAL="${FOLLOW_INTERVAL:-10s}"

mkdir -p "$(dirname "$LOCAL_DB")"

command -v litestream >/dev/null 2>&1 || {
  echo "litestream not found on PATH — install: https://litestream.io/install/" >&2
  exit 1
}

exec litestream restore \
  -f \
  -follow-interval "$FOLLOW_INTERVAL" \
  -if-replica-exists \
  -o "$LOCAL_DB" \
  "$LITESTREAM_REPLICA_URL"
