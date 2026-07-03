# Local Replica Mode (Litestream)

When the canonical oc-memory server runs on a remote/underpowered host (e.g. a
Raspberry Pi k3s node reached over Tailscale), every `memory_search` call pays
for both the network round trip and Pi-class ONNX embedding inference —
observed in the wild at several seconds per call. Local Replica Mode fixes
this by keeping a continuously-updated local copy of the database and serving
reads from it directly, using local embedding compute.

## How it works

```
┌─────────────────────┐         litestream          ┌──────────────────────┐
│  Primary (remote)    │  replicate ──────────────▶  │  local file replica  │
│  /data/memory.db      │                              │  (on primary's disk) │
│  (WAL mode)           │                              └──────────┬───────────┘
└─────────────────────┘                                            │ sftp pull
                                                                     ▼
                                                       ┌──────────────────────┐
                                                       │  litestream restore  │
                                                       │  -f  (client host)   │
                                                       └──────────┬───────────┘
                                                                     │
                                                                     ▼
                                                       ┌──────────────────────┐
                                                       │  local-replica.db    │
                                                       └──────────┬───────────┘
                                                                     │
                              reads ◀───────────────────────────────┤
                     oc_memory.local_replica_server                 │
                              writes ──────── remote MCP call ──────┘
```

- **Reads** (`memory_search`, `memory_search_tag`, `memory_stats`,
  `memory_scenes`, `memory_scene`, `memory_digest`, `memory_export`) are
  served from the local replica file — no network hop, local CPU for
  embeddings.
- **Writes** (`memory_store`, `memory_tag`, `memory_forget`, `memory_decay`,
  `memory_consolidate`) are forwarded to the canonical remote server over
  Streamable HTTP. There is exactly one writer, so no conflict resolution is
  needed. Reads lag the primary by the follow interval (a few seconds to
  tens of seconds) — acceptable for a memory store where writes are
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

## 3. Pull the replica on the client

Install Litestream on the client machine, then run (or use
`scripts/litestream-follow.sh`):

```bash
export LITESTREAM_REPLICA_URL="sftp://user@primary-host:22/path/to/litestream-replica"
export LOCAL_DB="$HOME/.oc-memory/local-replica/memory.db"
./scripts/litestream-follow.sh
```

This uses Litestream's follow mode (`-f -follow-interval`) — a single
long-running process that keeps `LOCAL_DB` continuously up to date, no cron
loop needed. Run it under your service supervisor of choice (launchd,
systemd) so it survives reboots.

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
