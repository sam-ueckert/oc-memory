# oc-memory

oc-memory is an MCP memory server that gives AI assistants persistent, searchable long-term memory. It runs three ways: as a local Python process, in a Docker container, or on a Kubernetes cluster.

Memory is stored in SQLite with FTS5 full-text search and local ONNX vector embeddings — no external APIs, no GPU required.

Works with [Claude Code](https://claude.ai/code), [Cursor](https://cursor.sh), [OpenClaw](https://openclaw.ai), and any MCP-compatible client.

## Features

- **SQLite + FTS5** — fast full-text search, zero external dependencies
- **ONNX embeddings** — semantic search via bge-small-en-v1.5, runs locally, no GPU needed
- **MCP protocol** — works with any MCP-compatible client
- **12 memory tools** — store, search, tag, decay, export, consolidate
- **`mem` CLI** — quick command-line interface for agents and humans alike
- **Docker & k8s ready** — run as a persistent service, shared across multiple agents

## Deployment Modes

### Option A: Local Python process

Best for: single-machine setups, Claude Code, Cursor.

```bash
git clone https://github.com/sam-ueckert/oc-memory.git
cd oc-memory
bash setup.sh
```

`setup.sh` installs the package, downloads the ONNX model (~24MB), prints your MCP config, and optionally installs the `mem` CLI wrapper.

### Option B: Docker (recommended for persistent service)

Best for: running oc-memory as a background service your agent always connects to.

```bash
git clone https://github.com/sam-ueckert/oc-memory.git
cd oc-memory
docker compose up -d
```

The server runs at `http://localhost:8765/sse`.

### Option C: Kubernetes (k3s/k8s)

Best for: multi-agent setups, always-on homelab or VPS deployments.

See [`docs/kubernetes.md`](docs/kubernetes.md) for full build and deploy instructions. The manifest is at [`k8s/memory-server.yaml`](k8s/memory-server.yaml).

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

`bash setup.sh` offers to patch `~/.claude.json` automatically.

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

## `mem` CLI

`mem` is a shell wrapper for interacting with the memory server from the command line or from agent shell commands.

### Installation

```bash
# After cloning the repo:
cp cli/mem ~/bin/mem
chmod +x ~/bin/mem

# Make sure ~/bin is in your PATH:
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

`setup.sh` offers to do this automatically.

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

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MEM_MCP_URL` | `http://localhost:8765` | MCP server URL |
| `MEM_LOCAL=1` | — | Use local Python library directly (no server) |

For Docker: default URL works out of the box.
For k8s: set `MEM_MCP_URL=http://<node-ip>:<nodeport>`.

---

## `oc-memory` CLI

The `oc-memory` CLI works directly against the local database (no server required):

```bash
# Store
oc-memory store --scene myproject --type decision --salience 0.8 "Chose PostgreSQL"

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

---

## OpenClaw Hooks

For automatic recall and capture on every conversation turn, see [`hooks/README.md`](hooks/README.md).

## Requirements

- Python 3.11+
- Docker (for Docker install) — or Python environment for local install
- No GPU required — ONNX embeddings run on CPU
- Ollama optional (for `memory_consolidate` LLM extraction)

## License

MIT
