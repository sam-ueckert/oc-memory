#!/usr/bin/env bash
# Mirror the primary's litestream file-replica directory to a local directory
# via rsync over SSH, on a fixed interval.
#
# Litestream's built-in sftp replica type opens a fresh SFTP round trip per
# file/stat/read — against a resource-constrained or high-latency primary
# host this can be extremely slow or hang outright. rsync's delta-transfer
# algorithm over a single SSH connection is far better suited to mirroring
# many small LTX segment files. Litestream itself then only ever reads a
# local path (see litestream-follow.sh) — no network I/O in its own restore
# path at all.
#
# Env vars:
#   RSYNC_SOURCE    user@host:/path/to/litestream-replica/ (required, trailing
#                   slash matters — copies the *contents* of the directory)
#   MIRROR_DIR      Local directory to mirror into
#                   (default: ~/.oc-memory/litestream-mirror)
#   INTERVAL        Seconds between rsync passes (default: 15)
set -euo pipefail

: "${RSYNC_SOURCE:?Set RSYNC_SOURCE (user@host:/path/to/litestream-replica/)}"
MIRROR_DIR="${MIRROR_DIR:-$HOME/.oc-memory/litestream-mirror}"
INTERVAL="${INTERVAL:-15}"

mkdir -p "$MIRROR_DIR"

while true; do
  rsync -az --delete -e "ssh -o ConnectTimeout=10" "$RSYNC_SOURCE" "$MIRROR_DIR/" \
    || echo "[rsync-mirror-loop] rsync failed, will retry in ${INTERVAL}s" >&2
  sleep "$INTERVAL"
done
