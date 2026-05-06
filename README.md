# oc-memory

oc-memory is an MCP memory server that gives AI coding agents persistent, searchable long-term memory. It runs as:

- A **local Python process** (no container needed)
- A **Docker container**
- A **Kubernetes pod**

Built on SQLite + FTS5 + ONNX embeddings. Zero external API dependencies.

Works with [Claude Code](https://claude.ai/code), [Cursor](https://cursor.sh), [OpenClaw](https://openclaw.ai), and any MCP-compatible client.

## Features

- **SQLite + FTS5** — fast full-text search, zero external dependencies
- **ONNX embeddings** — semantic search via bge-small-en-v1.5, runs locally, no GPU needed
- **MCP protocol** — works with any MCP-compatible client
- **12 memory tools** — store, search, tag, decay, export, consolidate
- **`mem` CLI** — quick command-line interface for agents and humans alike
- **Docker & k8s ready** — run as a persistent service, shared across multiple agents

---

## Quick Start

### Option A: Local (no container)

```bash
git clone https://github.com/sam-ueckert/oc-memory.git
cd oc-memory
pip install -e .
python -c "from oc_memory.embedding_backends import download_onnx_model; download_onnx_model()"
oc-memory stats   # verify installation
```

Or use the setup script, which does all of the above plus optional extras:

```bash
bash setup.sh
```

### Option B: Docker

```bash
git clone https://github.com/sam-ueckert/oc-memory.git
cd oc-memory
docker compose up -d
```

The server runs at `http://localhost:8765/sse`.

### Option C: Kubernetes (k3s/k8s)

See [`docs/kubernetes.md`](docs/kubernetes.md) for full build and deploy instructions. The manifest is at [`k8s/memory-server.yaml`](k8s/memory-server.yaml).

---

## `mem` CLI

The repo includes `bin/mem` — a convenience shell wrapper around the `oc-memory` CLI.

### Installation

```bash
# Option 1: symlink into your PATH
ln -s /path/to/oc-memory/bin/mem ~/bin/mem

# Option 2: add repo bin/ to PATH
echo 'export PATH="/path/to/oc-memory/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

`setup.sh` offers to install this automatically.

### Usage

```bash
# Search memories (vector + FTS)
mem search "database choice"

# Store a memory
mem store myproject decision 0.8 "Chose PostgreSQL over MySQL for better JSON support"
mem quick-store myproject fact 0.7 "API runs on port 8080"   # alias

# Tag a cell
mem tag 42 database architecture

# Search by tag
mem search-tag architecture

# Delete a cell
mem forget 42

# Stats and listing
mem stats
mem scenes
mem scene myproject

# Maintenance
mem decay       # fade old low-access memories
mem export      # export to markdown + JSON
```

There's also an MCP-aware version at `cli/mem` that can talk to a running MCP server (Docker/k8s) or use the local library directly. See `cli/mem --help` for details.

---

## Running the MCP Server

Three ways to start the MCP server locally (no container):

### 1. Stdio mode (entrypoint)

```bash
oc-memory-mcp
```

This is the entrypoint registered in `pyproject.toml`. Use this in MCP client configs (Claude Code, Cursor, OpenClaw stdio transport).

### 2. Stdio mode (module)

```bash
python -m oc_memory.mcp_server
```

Same as above — useful when you want to run from a specific Python environment.

### 3. HTTP/SSE mode

```bash
python -m oc_memory.mcp_server --http
# or: MCP_TRANSPORT=http python -m oc_memory.mcp_server
```

Starts an HTTP server on `0.0.0.0:8765` with SSE transport. Use `--port` and `--host` to customize. This is what Docker and Kubernetes deployments use.

---

## MCP Client Configuration

### Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "oc-memory": {
      "command": "oc-memory-mcp"
    }
  }
}
```

Or point at Docker/k8s (HTTP/SSE):

```json
{
  "mcpServers": {
    "oc-memory": {
      "type": "sse",
      "url": "http://localhost:8765/sse"
    }
  }
}
```

`setup.sh` offers to patch `~/.claude.json` automatically.

### Cursor

Create or update `.cursor/mcp.json` (or `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "oc-memory": {
      "command": "oc-memory-mcp"
    }
  }
}
```

### OpenClaw

Add under the `mcp` key in `openclaw.json`:

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

See [`docs/openclaw-integration.md`](docs/openclaw-integration.md) for the full OpenClaw setup guide.

---

## `oc-memory` CLI

The `oc-memory` CLI works directly against the local database (no server required):

```bash
# Store
oc-memory quick-store myproject decision 0.8 "Chose PostgreSQL"

# Search
oc-memory search "database choice"

# Stats / scenes
oc-memory stats
oc-memory scenes

# Tag / decay / export
oc-memory tag <id> database architecture
oc-memory decay
oc-memory export
```

---

## Memory Model

Memory is stored as typed **cells** with salience scores:

| Type | When to use |
|------|-------------|
| `fact` | Observable truths, config details, measurements |
| `decision` | Choices made and their reasoning |
| `preference` | User or agent preferences |
| `task` | Pending work, todos |
| `risk` | Warnings, known issues |
| `plan` | Future intentions |
| `lesson` | Learned from mistakes |

**Salience:** 0.1 (trivia) → 0.5 (normal) → 0.8 (important) → 1.0 (critical)

---

## MCP Tool Reference

| Tool | Description |
|------|-------------|
| `memory_store` | Store a memory cell (content, type, scene, salience, tags) |
| `memory_search` | Hybrid search — vector + FTS5 |
| `memory_search_tag` | Search by tag |
| `memory_forget` | Delete a cell by ID |
| `memory_tag` | Add tags to a cell |
| `memory_stats` | Database statistics |
| `memory_scenes` | List all scenes |
| `memory_scene` | Get scene details and summary |
| `memory_decay` | Fade old low-access memories |
| `memory_export` | Export to markdown + JSON |
| `memory_digest` | Get daily digest of recent memories |
| `memory_consolidate` | Consolidate scene summaries with LLM |

---

## Architecture

```
Your AI assistant
    │
    ▼ (MCP tools)
oc-memory MCP server
    │
    ├── SQLite + FTS5 ── fast full-text search
    ├── ONNX embeddings ── semantic similarity (bge-small-en-v1.5, local)
    └── memory.db ──── ~/.oc-memory/memory.db (or Docker volume / k8s PVC)
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OC_MEMORY_DB` | `~/.oc-memory/memory.db` | SQLite database path |
| `OC_MEMORY_EXPORT` | `~/.oc-memory/export` | Export directory |
| `OC_MEMORY_BACKEND` | `onnx` | Embedding backend: `onnx` or `ollama` |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint (if using ollama backend) |
| `MCP_PORT` | `8765` | Port for HTTP/SSE mode |
| `MCP_TRANSPORT` | — | Set to `http` to force HTTP/SSE mode |

---

## Skills

### Context Digest

Pre-loads high-salience memories into your agent's context file (`MEMORY.md` or `CLAUDE.md`) so every session starts with key memories — no manual search needed.

```bash
# Install
cp skills/context-digest/scripts/gen-context-digest.sh ~/bin/
chmod +x ~/bin/gen-context-digest.sh

# Run (auto-detects MEMORY.md or CLAUDE.md)
WORKSPACE=/path/to/project bash ~/bin/gen-context-digest.sh

# Cron (every 3h)
0 */3 * * * WORKSPACE=/path/to/project bash ~/bin/gen-context-digest.sh
```

See [`skills/context-digest/SKILL.md`](skills/context-digest/SKILL.md) for full docs.

### Promote Lessons (Learning Loop)

Converts corrections and lessons stored in memory into behavioral rules, injected into `SOUL.md` (OpenClaw) or `CLAUDE.md` (Claude Code). This creates a feedback loop where past mistakes become standing rules for future sessions.

```bash
# OpenClaw
API_TOKEN=<gateway-token> WORKSPACE=/path/to/workspace bash promote-lessons.sh

# Claude Code (Anthropic API)
API_TOKEN=$ANTHROPIC_API_KEY API_URL=https://api.anthropic.com/v1/messages WORKSPACE=/path/to/project bash promote-lessons.sh
```

See [`skills/promote-lessons/SKILL.md`](skills/promote-lessons/SKILL.md) for full docs.

### Recall (MCP-based)

Teaches agents how to use oc-memory's MCP tools for search, store, and maintenance. See [`skills/recall/SKILL.md`](skills/recall/SKILL.md).

## OpenClaw Hooks

For automatic recall and capture on every conversation turn, see [`hooks/README.md`](hooks/README.md).

## Requirements

- Python 3.11+
- Docker (for Docker install) — or Python environment for local install
- No GPU required — ONNX embeddings run on CPU
- Ollama optional (for `memory_consolidate` LLM extraction)

## License

MIT
