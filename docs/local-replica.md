# Local Replica Mode (Litestream)

When the canonical oc-memory server runs on a remote/underpowered host (e.g. a
Raspberry Pi k3s node reached over Tailscale), every `memory_search` call pays
for both the network round trip and Pi-class ONNX embedding inference —
observed in the wild at several seconds per call. Local Replica Mode fixes
this by keeping a continuously-updated local copy of the database and serving
reads from it directly, using local embedding compute.

## How it works

```
┌──────────────────────┐   litestream    ┌───────────────────────┐
│  Primary (remote)     │  replicate ───▶ │  local file replica   │
│  /data/memory.db      │                 │  (on primary's disk)  │
│  (WAL mode)           │                 └───────────┬───────────┘
└───────────────────────┘                              │ rsync -az --delete (SSH)
                                                          ▼
                                            ┌───────────────────────┐
                                            │  litestream-mirror/   │
                                            │  (client host, local) │
                                            └───────────┬───────────┘
                                                          │ litestream restore -f
                                                          │ (file:// URL — no network)
                                                          ▼
                                            ┌───────────────────────┐
                                            │  local-replica.db     │
                                            └───────────┬───────────┘
                                                          │
                    reads ◀───────────────────────────────┤
           oc_memory.local_replica_server                 │
                    writes ──────── remote MCP call ───────┘
```

Network transfer and WAL-segment reconstruction are deliberately split into
two stages:

- **`rsync-mirror-loop.sh`** mirrors the primary's litestream file replica to
  a local directory over SSH on a fixed interval. Litestream's built-in
  `sftp` replica type opens a fresh SFTP round trip per file/stat/read —
  against a resource-constrained or high-latency primary this can be
  extremely slow or hang outright (observed: reads that took 6-8s over MCP
  also caused `litestream restore` against an `sftp://` URL to hang
  indefinitely). rsync's delta-transfer algorithm over one SSH connection
  handles many small LTX segment files far better.
- **`litestream-follow.sh`** then runs `litestream restore -f` against that
  *local* mirror directory (a `file://` URL) — no network I/O in Litestream's
  own path at all, so it can't be affected by primary-host slowness.

Reads (`memory_search`, `memory_search_tag`, `memory_stats`, `memory_scenes`,
`memory_scene`, `memory_digest`, `memory_export`) are served from the local
replica file — no network hop, local CPU for embeddings. Writes
(`memory_store`, `memory_tag`, `memory_forget`, `memory_decay`,
`memory_consolidate`) are forwarded to the canonical remote server over
Streamable HTTP. There is exactly one writer, so no conflict resolution is
needed. Reads lag the primary by roughly the rsync interval plus the follow
interval (tens of seconds) — acceptable for a memory store where writes are
infrequent relative to reads.

## 1. Enable WAL mode on the primary

Required as of this version — `MemoryDB` now sets `PRAGMA journal_mode=WAL`
automatically on connect. No action needed beyond upgrading; the pragma is a
one-time, idempotent change to the DB file's header.

## 2. Run Litestream on the primary

The provided container image (see the parent `swabby-memory` repo's
`Dockerfile` / `docker/entrypoint.sh`) already bundles Litestream and runs
`litestream replicate -config docker/litestream.yml` alongside the MCP
server, shipping to a local file replica at `/data/litestream-replica`.

For a bare-metal (non-container) primary, install Litestream
(https://litestream.io/install/) and run:

```bash
litestream replicate -exec "oc-memory-mcp --http" /path/to/memory.db /path/to/replica-dir
```

## 3. Mirror the replica to the client, then follow it locally

Two processes, both long-running (run under launchd/systemd so they survive
reboots):

```bash
# 1. Keep a local mirror of the primary's replica dir in sync over SSH.
export RSYNC_SOURCE="user@primary-host:/path/to/litestream-replica/"
export MIRROR_DIR="$HOME/.oc-memory/litestream-mirror"
./scripts/rsync-mirror-loop.sh &

# 2. Materialize/keep updating a local SQLite copy from that local mirror —
#    no network access in this step, so it can't hang on a slow primary.
export MIRROR_DIR="$HOME/.oc-memory/litestream-mirror"
export LOCAL_DB="$HOME/.oc-memory/local-replica/memory.db"
./scripts/litestream-follow.sh
```

Don't point `litestream-follow.sh` at an `sftp://` URL directly — Litestream's
SFTP replica client issues a separate round trip per file/stat/read, which
can hang indefinitely against a slow or resource-constrained primary (see
"Why two stages" above). Always mirror locally with rsync first.

## 4. Run the local hybrid server

```bash
export OC_MEMORY_LOCAL_DB="$HOME/.oc-memory/local-replica/memory.db"
export OC_MEMORY_REMOTE_URL="http://primary-host:8765/mcp"   # canonical server's Streamable HTTP endpoint
oc-memory-local            # stdio, for Claude Code / Cursor
# or
MCP_TRANSPORT=http MCP_PORT=8765 oc-memory-local --http   # HTTP, e.g. for another local service to consume
```

`install.py --local-replica` automates steps 3–4 and patches client MCP
configs to point at the local hybrid server instead of the remote URL.

## Limitations

- Local replica mode is **read-mostly**: all writes still require
  connectivity to the primary. If the primary is unreachable, writes fail
  (reads keep serving the last-synced local copy).
- `memory_export` runs against the local replica, so an export taken while
  the replica is behind will reflect that lag — for a guaranteed up-to-date
  export, run it directly against the primary instead.
- Litestream's replica file format is internal (LTX segments) — don't expect
  to read `/data/litestream-replica` directly; always go through
  `litestream restore`/`-f`.
