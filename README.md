# oc-memory

Persistent memory MCP server for [Claude Code](https://claude.ai/code), [Cursor](https://cursor.sh), and [OpenClaw](https://github.com/openclaw/openclaw).

Gives your AI assistant structured, searchable long-term memory backed by SQLite, full-text search, and local vector embeddings — with zero external API dependencies.

## Why

AI assistants forget everything between sessions. `oc-memory` adds a persistent layer:

- **SQLite + FTS5** — fast full-text search, zero external dependencies
- **ONNX embeddings** — semantic search via bge-small-en-v1.5, runs locally, no GPU needed
- **MCP protocol** — works with any MCP-compatible client (Claude Code, Cursor, OpenClaw, etc.)
- **12 memory tools** — store, search, tag, decay, export, consolidate
- **Docker-ready** — one command to run as a persistent service

## Quick Start

### Option A: Docker (recommended)

```bash
git clone https://github.com/sam-ueckert/oc-memory.git
cd oc-memory
docker compose up -d
```

The server runs at `http://localhost:8765/sse`.

### Option B: Local install

```bash
git clone https://github.com/sam-ueckert/oc-memory.git
cd oc-memory
bash setup.sh
```

`setup.sh` installs the package, downloads the ONNX model (~24MB), and prints your MCP config.

## Claude Code Integration

Add to `~/.claude.json` (or run `bash setup.sh` which offers to patch it automatically):

```json
{
  "mcpServers": {
    "oc-memory": {
      "command": "oc-memory-mcp"
    }
  }
}
```

Or point at the Docker server (HTTP/SSE):

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

Then restart Claude Code. The memory tools will be available in every session.

## Cursor Integration

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

## OpenClaw Integration

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

Run `oc-memory setup` at any time to regenerate these snippets for your current install path.

## CLI Usage

```bash
# Store a memory
oc-memory store --scene myproject --type decision --salience 0.8 "Chose PostgreSQL over MySQL for better JSON support"

# Search
oc-memory search "database choice"

# Stats
oc-memory stats

# List scenes
oc-memory scenes

# Tag a cell
oc-memory tag <id> database architecture

# Decay old low-access memories
oc-memory decay

# Export to markdown + JSON
oc-memory export
```

### Quick store (no flags, scene required)

```bash
oc-memory quick-store myproject fact 0.7 "API runs on port 8080"
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OC_MEMORY_DB` | `~/.oc-memory/memory.db` | SQLite database path |
| `OC_MEMORY_EXPORT` | `~/.oc-memory/export` | Export directory |
| `OC_MEMORY_BACKEND` | `onnx` | Embedding backend: `onnx` or `ollama` |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint (if using ollama backend) |

## Architecture

```
Your AI assistant
    │
    ▼ (MCP tools)
oc-memory MCP server
    │
    ├── SQLite + FTS5 ── fast full-text search
    ├── ONNX embeddings ── semantic similarity (bge-small-en-v1.5, local)
    └── memory.db ──── ~/.oc-memory/memory.db (or Docker volume)
```

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

## Self-Hosting (Kubernetes)

See [`k8s/memory-server.yaml`](k8s/memory-server.yaml) for a minimal k3s/k8s deployment. Update the image reference and storage class for your cluster.

## OpenClaw Hooks

For automatic recall and capture on every conversation turn, see [`hooks/README.md`](hooks/README.md).

## Requirements

- Python 3.11+
- Docker (for Docker install) — or Python environment for local install
- No GPU required — ONNX embeddings run on CPU
- Ollama optional (for `memory_consolidate` LLM extraction)

## License

MIT
