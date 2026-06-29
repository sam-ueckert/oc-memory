# Hermes Session Extractor

Pull recent sessions from the [Hermes agent harness](https://github.com/NousResearch/hermes-agent),
distill them into structured memory cells with an LLM, and push those cells to an
oc-memory / archy store.

This complements Hermes's inline memory tools — it catches sessions where key
decisions, facts, or lessons were never explicitly saved.

## API-native by design

| Stage | API used | Fallback |
|-------|----------|----------|
| **Extract** | `hermes sessions export` (JSONL) when the `hermes` CLI is on PATH | read-only SQLite on `~/.hermes/state.db` |
| **Push** | archy `memory_store` over **modern MCP Streamable HTTP** (`POST /mcp`) | stdlib Streamable HTTP client (no SDK needed) |

The extractor runs **co-located with Hermes** (same place the `hermes` CLI and
`state.db` live), so the read side is identical whether Hermes runs in k3s or
natively. Only the push target — an MCP URL — crosses the network.

> The legacy SSE transport (`GET /sse`) is never used. The push path prefers the
> official MCP Python SDK (`streamablehttp_client`); when it isn't installed, a
> stdlib-only Streamable HTTP client takes over.

## Sinks

| `--sink` | Behaviour |
|----------|-----------|
| `mcp` (default) | Push only to the remote archy MCP server (central source of truth) |
| `both` | Push to MCP **and** keep a local oc-memory SQLite copy as cache/fallback |
| `local` | Local SQLite only (legacy behaviour) |

## Usage

```bash
# Default: extract last 24h, push to MCP (uses OC_MEMORY_MCP_URL or built-in default)
oc-memory extract-hermes

# Explicit: keep a local copy too, filter to Slack sessions, custom MCP server
oc-memory extract-hermes \
  --sink both \
  --source slack \
  --mcp-url http://memory-server.swabby-memory.svc.cluster.local:8765/mcp \
  --since-hours 48 --max-sessions 10

# Preview without extracting or storing
oc-memory extract-hermes --dry-run --since-hours 168
```

### MCP endpoint

| Where Hermes runs | MCP URL |
|-------------------|---------|
| k3s (in-cluster)  | `http://memory-server.swabby-memory.svc.cluster.local:8765/mcp` |
| native (Tailscale)| `http://100.119.254.83:30765/mcp` |

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OC_MEMORY_MCP_URL` | in-cluster service | archy MCP push target |
| `OC_MEMORY_MCP_TOKEN` | _(none)_ | bearer token for the MCP server |
| `OC_MEMORY_HERMES_SINK` | `mcp` | `mcp` \| `both` \| `local` |
| `OC_MEMORY_HERMES_DB` | `~/.hermes/state.db` | SQLite fallback path |
| `OC_MEMORY_API_URL` | `http://localhost:18789/v1` | LLM (OpenAI-compatible) endpoint for extraction |
| `OC_MEMORY_API_TOKEN` | _(none)_ | LLM API token |
| `OC_MEMORY_EXTRACT_MODEL` | `openclaw` | extraction model |
| `OC_MEMORY_OWNER_ID` | `hermes` | owner_id stamped on stored cells |

The installer writes these defaults to `~/.oc-memory/hermes.env` for reuse in cron jobs.

## Migrating an existing local store to MCP

When switching to MCP-only, push your existing local memories to the server first:

```bash
oc-memory migrate-to-mcp --db ~/.oc-memory/memory.db \
  --mcp-url http://memory-server.swabby-memory.svc.cluster.local:8765/mcp
# add --dry-run to preview the count first
```

Migration is a **non-destructive copy** — the local DB is only ever read, so a
failed or partial push never loses data. The command exits non-zero unless
*every* cell landed on the server, so callers (and the installer) can tell a
clean migration from a partial one.

The TUI installer detects an existing local DB and offers to run this
automatically when you choose the MCP-only sink. Its safety rules:

- **Failed/partial migration → it will not switch you to MCP-only.** It keeps
  the `both` sink so the un-migrated local cells aren't orphaned, and points you
  at the manual `migrate-to-mcp` command to retry.
- **Successful migration** is recorded (`OC_MEMORY_MCP_MIGRATED` in
  `hermes.env`) so a later re-run won't blindly re-push and create duplicates.

## Re-running the installer (reconfigure)

The installer is idempotent and safe to run again. On a second run it reads the
existing `~/.oc-memory/hermes.env` and offers the current sink / MCP URL /
source as the defaults, so you can change one setting without resetting the
rest. If a migration was already done to the same server, it detects that and
defaults to *not* re-migrating. (Client-config patching and cron installation
are likewise skip-if-present.)

## Deployment

- **k3s:** run inside (or as a sidecar of) the Hermes pod so it shares the
  `hermes-data` PVC and the `hermes` CLI; point `--mcp-url` at the in-cluster
  `memory-server` service. A Hermes cron session can simply invoke
  `oc-memory extract-hermes`.
- **native:** an OS cron / systemd timer running `oc-memory extract-hermes` with
  `--mcp-url` pointed at the server's Tailscale/NodePort URL.

Dedup state lives in `~/.oc-memory/hermes_extract_state.json` (keyed by session id
+ message count) so re-runs only process new or grown sessions.
