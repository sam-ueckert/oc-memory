# oc-memory

oc-memory is an MCP memory server that gives AI coding agents persistent, searchable long-term memory.

Works with [Claude Code](https://claude.ai/code), [Cursor](https://cursor.sh), [OpenClaw](https://openclaw.ai), and any MCP-compatible client.

Built on SQLite + FTS5 + ONNX embeddings. Zero external API dependencies.

## Features

- **Streamable HTTP transport** — modern MCP spec (`POST /mcp`), Docker-ready
- **SQLite + FTS5** — fast full-text search, zero external dependencies
- **ONNX embeddings** — semantic search via bge-small-en-v1.5, runs locally, no GPU needed
- **12 MCP tools** — store, search, tag, decay, export, consolidate
- **`mem` CLI** — terminal interface for agents and humans
- **Context digest** — pre-loads top memories into every agent session
- **Promote lessons** — turns past corrections into standing behavioral rules

---

## Quick Start

### Option A: Docker (recommended)

The preferred way — runs as a persistent service, shared across Claude Code, Cursor, OpenClaw, and any other MCP client simultaneously.

```bash
git clone https://github.com/sam-ueckert/oc-memory.git
cd oc-memory
bash setup.sh      # TUI installer — walks through Docker setup + client config
```

The installer will:
1. Start the container (`docker compose up -d`)
2. Auto-configure Claude Code, Cursor, and/or OpenClaw
3. Install the `mem` CLI
4. Set up optional features (context digest, lessons, cron jobs)

Or start it directly:

```bash
docker compose up -d
# Server at http://localhost:8765/mcp
```

**MCP client config (HTTP transport):**

```json
{
  "mcpServers": {
    "oc-memory": {
      "type": "http",
      "url": "http://localhost:8765/mcp"
    }
  }
}
```

### Option B: Local Python

No container — runs in your Python environment. Good for single-user setups or development.

```bash
git clone https://github.com/sam-ueckert/oc-memory.git
cd oc-memory
bash setup.sh --local
```

**MCP client config (stdio transport):**

```json
{
  "mcpServers": {
    "oc-memory": {
      "command": "oc-memory-mcp"
    }
  }
}
```

**Non-interactive / CI installs:**

```bash
NONINTERACTIVE=1 INSTALL_MODE=local bash setup.sh
NONINTERACTIVE=1 INSTALL_MODE=docker bash setup.sh

# Corporate SSL inspection proxy:
OC_MEMORY_SSL_NO_VERIFY=1 bash setup.sh --local
REQUESTS_CA_BUNDLE=/path/to/corp-ca.crt bash setup.sh --local
```

### Option C: Kubernetes

See [`docs/kubernetes.md`](docs/kubernetes.md). Manifest at [`k8s/memory-server.yaml`](k8s/memory-server.yaml).

---

## MCP Transport

oc-memory supports two transports. **Use HTTP for Docker/k8s; use stdio for local installs.**

| Transport | Endpoint | When to use |
|-----------|----------|-------------|
| **Streamable HTTP** (modern) | `POST http://host:8765/mcp` | Docker, k8s, shared server |
| Stdio | `oc-memory-mcp` process | Local single-user install |
| ~~SSE~~ (deprecated) | `GET http://host:8765/sse` | Legacy clients only |

The Docker container serves both `/mcp` (modern) and `/sse` (legacy) on the same port for backward compatibility, but new client configs should use `/mcp`.

---

## MCP Client Configuration

Run `oc-memory mcp-setup` at any time to reprint all config snippets.
Run `oc-memory config --client <claude|cursor|openclaw> [--url <url>]` for machine-readable JSON.

### Claude Code

**Docker (HTTP):**
```json
{
  "mcpServers": {
    "oc-memory": {
      "type": "http",
      "url": "http://localhost:8765/mcp"
    }
  }
}
```

**Local (stdio):**
```json
{
  "mcpServers": {
    "oc-memory": {
      "command": "oc-memory-mcp"
    }
  }
}
```

Add to `~/.claude.json`. The installer can patch this automatically.

### Cursor

**Docker (HTTP):** create/update `~/.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "oc-memory": {
      "type": "http",
      "url": "http://localhost:8765/mcp"
    }
  }
}
```

### OpenClaw

**Docker (HTTP):** add to `openclaw.json` under `mcp`:
```json
{
  "mcp": {
    "servers": [
      {
        "name": "oc-memory",
        "transport": "http",
        "url": "http://localhost:8765/mcp"
      }
    ]
  }
}
```

**Local (stdio):**
```json
{
  "mcp": {
    "servers": [
      {
        "name": "oc-memory",
        "transport": "stdio",
        "command": "oc-memory-mcp",
        "args": []
      }
    ]
  }
}
```

See [`docs/openclaw-integration.md`](docs/openclaw-integration.md) for the full guide including hooks.

---

## `mem` CLI

The `cli/mem` wrapper supports both local mode and MCP server mode.

```bash
# Docker / MCP server mode (default, server must be running)
mem stats
mem search "database choice"
mem store myproject decision 0.8 "Chose PostgreSQL over MySQL"
mem tag 42 database architecture
mem search-tag architecture
mem forget 42
mem scenes
mem scene myproject
mem decay
mem export

# Local Python mode (no server needed)
export MEM_LOCAL=1
mem stats
mem store myproject fact 0.7 "API runs on port 8080"

# Custom server URL
export MEM_MCP_URL="http://myserver:8765"
mem stats
```

`setup.sh` installs this to `~/bin/mem`. Cell types: `fact decision preference task risk plan lesson`. Salience: `0.1` (trivia) → `1.0` (critical).

---

## Skills

### Context Digest

Pre-loads high-salience memories into `CLAUDE.md` (Claude Code) or `MEMORY.md` (OpenClaw) so every session starts with key context — no manual search needed.

Add markers to your context file:
```markdown
<!-- ARCHY_DIGEST_START -->
<!-- ARCHY_DIGEST_END -->
```

Run or schedule:
```bash
WORKSPACE=/path/to/project bash ~/bin/gen-context-digest.sh
# Cron: 0 */3 * * * WORKSPACE=/path/to/project bash ~/bin/gen-context-digest.sh
```

See [`skills/context-digest/SKILL.md`](skills/context-digest/SKILL.md).

### Promote Lessons (Learning Loop)

Tags corrections → weekly LLM synthesis → injects rules into `CLAUDE.md` or `SOUL.md`.

Add markers:
```markdown
<!-- LEARNED_RULES_START -->
## Learned Rules
*No rules yet.*
<!-- LEARNED_RULES_END -->
```

Tag a lesson, then run synthesis:
```bash
MEM_LOCAL=1 mem store lessons lesson 0.9 "Never truncate error output"
MEM_LOCAL=1 mem tag <id> correction

# Claude Code
API_TOKEN=$ANTHROPIC_API_KEY WORKSPACE=$(pwd) bash ~/bin/promote-lessons.sh

# OpenClaw
API_TOKEN=<gateway-token> WORKSPACE=$(pwd) bash ~/bin/promote-lessons.sh
```

See [`skills/promote-lessons/SKILL.md`](skills/promote-lessons/SKILL.md).

### OpenClaw Hooks

Automatic recall + capture on every conversation turn via gateway-level hooks. See [`hooks/README.md`](hooks/README.md).

---

## Memory Model

| Type | When to use |
|------|-------------|
| `fact` | Observable truths, config details, measurements |
| `decision` | Choices made and their reasoning |
| `preference` | User or agent preferences |
| `task` | Pending work, todos |
| `risk` | Warnings, known issues |
| `plan` | Future intentions |
| `lesson` | Learned from mistakes |

**Salience:** `0.1` (trivia) → `0.5` (normal) → `0.8` (important) → `1.0` (critical)

---

## MCP Tool Reference

| Tool | Description |
|------|-------------|
| `memory_store` | Store a memory cell |
| `memory_search` | Hybrid search — vector + FTS5 |
| `memory_search_tag` | Search by tag |
| `memory_forget` | Delete a cell by ID |
| `memory_tag` | Add tags to a cell |
| `memory_stats` | Database statistics |
| `memory_scenes` | List all scenes |
| `memory_scene` | Get scene details and summary |
| `memory_decay` | Fade old low-access memories |
| `memory_export` | Export to markdown + JSON |
| `memory_digest` | Get daily digest |
| `memory_consolidate` | Consolidate scene summaries with LLM |

---

## Architecture

```
AI assistant (Claude Code / Cursor / OpenClaw)
         │
         ▼  MCP tools
  ┌──────────────────────────────────────────┐
  │           oc-memory server               │
  │                                          │
  │  POST /mcp  — Streamable HTTP (modern)  │
  │  GET  /sse  — Legacy SSE (deprecated)   │
  │  GET  /health — Health check             │
  └──────────────┬───────────────────────────┘
                 │
         ┌───────▼────────┐
         │   SQLite DB    │
         │  + FTS5 index  │
         │  + ONNX embeds │
         └────────────────┘
         ~/.oc-memory/memory.db
         (or Docker volume / k8s PVC)
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OC_MEMORY_DB` | `~/.oc-memory/memory.db` | SQLite database path |
| `OC_MEMORY_EXPORT` | `~/.oc-memory/export` | Export directory |
| `OC_MEMORY_BACKEND` | `onnx` | Embedding backend: `onnx` or `ollama` |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `MCP_PORT` | `8765` | HTTP server port |
| `MCP_TRANSPORT` | — | Set `http` to force HTTP mode |
| `OC_MEMORY_SSL_NO_VERIFY` | — | `1` to skip TLS cert verification |
| `REQUESTS_CA_BUNDLE` | — | Path to custom CA bundle |
| `OC_MEMORY_ADMIN_USER` | — | User ID that bypasses ownership filters |
| `OC_MEMORY_OC_CONFIG` | `~/.openclaw/openclaw.json` | Path to openclaw.json |
| `NONINTERACTIVE` | — | `1` or use `--yes` for non-interactive install |
| `INSTALL_MODE` | — | `docker` or `local` to skip mode selection |
| `MEM_LOCAL` | — | `1` to use local library in `mem` CLI |
| `MEM_MCP_URL` | `http://localhost:8765` | MCP server URL for `mem` CLI |

---

## Requirements

- Python 3.11+ (local install or installer)
- Docker (for Docker install)
- No GPU required — ONNX embeddings run on CPU

## License

MIT
