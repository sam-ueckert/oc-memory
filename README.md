# oc-memory

Self-organizing agent memory for [OpenClaw](https://github.com/openclaw/openclaw). Gives your agent structured, searchable long-term memory backed by SQLite, full-text search, vector embeddings, and LLM-driven extraction.

## Why

OpenClaw agents wake up fresh each session. Markdown files provide basic continuity, but as context grows, finding the right memory becomes slow and imprecise. oc-memory adds a structured layer:

- **SQLite + FTS5** — fast full-text search across all memories, zero external dependencies
- **Vector embeddings** — semantic search via Ollama (optional, gracefully degrades to FTS)
- **LLM extraction** — automatically parse conversations and notes into typed, salience-scored memory cells
- **Scene grouping** — memories clustered by topic with consolidated summaries
- **Git-friendly export** — JSON + markdown exports for version-controlled backup

## Architecture

```
Agent conversations / markdown files
        ↓ extract (LLM)
Typed memory cells (fact, decision, task, preference, ...)
        ↓ store + embed
SQLite DB (FTS5 + vector blobs)
        ↓ search
Relevant context for current task
        ↓ export
JSON + Markdown → git push
```

## Quick Start

```bash
# Install
pip install -e .
# or with uv:
uv sync

# Store a memory directly
oc-memory store '[{"scene":"setup","cell_type":"decision","salience":0.8,"content":"Using SQLite for memory storage"}]'

# Extract memories from text (requires Ollama)
oc-memory extract "We decided to deploy on a 2-core VPS with 2GB RAM"

# Search
oc-memory search "deployment infrastructure"

# Stats
oc-memory stats
```

## Requirements

- **Python 3.11+**
- **SQLite with FTS5** (included in standard Python builds)

### Optional (for full features)

- **Ollama** — for embeddings and LLM extraction (see [Local Model Setup](docs/local-models.md))
- **SSH access to a remote host** — for SQLite backups (see [Backup](docs/backup.md))

## Commands

| Command | Description |
|---------|-------------|
| `store <json>` | Store pre-extracted memory cells |
| `store-stdin` | Store cells from stdin (JSON) |
| `extract <text>` | Extract cells from text using local LLM |
| `extract-file <path>` | Extract cells from a file |
| `search <query>` | Search memories (vector + FTS fallback) |
| `scenes` | List all scenes |
| `scene <name>` | Get scene details and cells |
| `consolidate [scene]` | Generate LLM summaries for scenes |
| `embed` | Embed all cells missing embeddings |
| `export` | Export markdown + JSON for git |
| `backup` | Full backup (export + optional remote SQLite copy) |
| `restore <path>` | Restore from JSON export |
| `stats` | Show memory statistics |
| `forget <id>` | Delete a specific cell |
| `decay` | Decay salience of old, rarely-accessed memories |

## Memory Cell Types

| Type | Description |
|------|-------------|
| `fact` | Factual information, observations |
| `decision` | Choices made and their reasoning |
| `preference` | User or agent preferences |
| `task` | Things to do, pending work |
| `risk` | Potential problems, warnings |
| `plan` | Future intentions, project plans |
| `lesson` | Lessons learned from experience |

## Configuration

All config via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OC_MEMORY_DB` | `~/.openclaw/memory.db` | SQLite database path |
| `OC_MEMORY_EXPORT` | `~/.openclaw/workspace/memory-export` | Export directory for git |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OC_MEMORY_BACKUP_HOST` | *(none)* | SSH host for remote SQLite backup |

## Wiring into OpenClaw

See [docs/openclaw-integration.md](docs/openclaw-integration.md) for detailed instructions on connecting oc-memory to your OpenClaw agent.

## Docs

- [Local Model Setup](docs/local-models.md) — installing Ollama, models, and system requirements
- [OpenClaw Integration](docs/openclaw-integration.md) — wiring memory into your agent
- [Backup Strategy](docs/backup.md) — export, git, and remote backup
- [Design Notes](docs/design.md) — architecture decisions and future directions

## Skills

oc-memory ships with an OpenClaw skill for memory recall:

- **[recall](skills/recall/SKILL.md)** — multi-layer memory retrieval (structured DB → markdown → grep). Drop it into your agent's skills directory or install via OpenClaw.

## License

MIT
