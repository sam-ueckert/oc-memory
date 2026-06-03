# Wiring oc-memory into OpenClaw

This guide covers connecting oc-memory to your OpenClaw agent so it uses structured memory for recall, storage, and context building.

## Overview

```
Agent session
  ├── mem search "query"        ← recall before answering
  ├── mem quick-store ...       ← capture new facts
  ├── mem export                ← periodic git backup
  └── markdown files            ← human-readable layer (unchanged)
```

oc-memory runs alongside OpenClaw's existing markdown memory files. It doesn't replace them — it adds a fast, searchable structured layer underneath.

## Installation

On your OpenClaw host:

```bash
# Clone the repo
cd ~/repos
git clone https://github.com/sam-ueckert/oc-memory.git
cd oc-memory

# Install with pip (or uv)
pip install -e .
# or: uv sync && uv pip install -e .
```

Verify:
```bash
oc-memory stats
```

## Install the `mem` CLI

The repo includes two CLI wrappers:

- **`bin/mem`** — lightweight, wraps the `oc-memory` CLI directly (recommended for local installs)
- **`cli/mem`** — full-featured, can talk to a running MCP server (Docker/k8s) or use the local library

For most users, `bin/mem` is all you need:

```bash
# Option 1: symlink into ~/bin
ln -s /path/to/oc-memory/bin/mem ~/bin/mem

# Option 2: add repo bin/ to PATH
export PATH="/path/to/oc-memory/bin:$PATH"  # add to ~/.bashrc
```

Make sure `~/bin` is in your PATH (add to `~/.bashrc` if needed):
```bash
export PATH="$HOME/bin:$PATH"
```

`bash setup.sh` offers to install this automatically.

If you're connecting to a remote server (Docker or k8s), use `cli/mem` instead and set:
```bash
export MEM_MCP_URL="http://<host>:<port>"
```

For local library use (no server): `MEM_LOCAL=1 cli/mem stats`

## Update AGENTS.md

Add memory instructions to your agent's `AGENTS.md` so it knows to use the system:

```markdown
## Memory

You wake up fresh each session. These are your continuity layers:

### Layer 1: Structured Memory (oc-memory / SQLite)
Your primary recall system. Fast, searchable, typed.

\`\`\`bash
# Search memories (instant, FTS)
mem search "topic keywords"

# Store a new memory cell
mem quick-store <scene> <type> <salience> <content>
# Example: mem quick-store infrastructure fact 0.8 "Server has 2GB RAM"

# Check stats
mem stats
\`\`\`

**Use `mem search` before answering questions about prior work, decisions, or context.**

Cell types: fact, decision, preference, task, risk, plan, lesson
Salience: 0.1 (trivia) → 0.5 (normal) → 0.8 (important) → 1.0 (critical)

### Layer 2: Markdown Files (human-readable, git-backed)
- **Daily notes:** `memory/YYYY-MM-DD.md` — raw logs
- **Long-term:** `MEMORY.md` — curated summary, read at session start

### Layer 3: End-of-session ingest
After significant sessions, ingest key facts:
\`\`\`bash
mem quick-store <scene> <type> <salience> "<what happened>"
\`\`\`
```

## Update TOOLS.md

Add the memory system reference:

```markdown
## Memory System (oc-memory)

- **CLI:** `~/bin/mem`
- **DB:** `~/.oc-memory/memory.db`
- **Embeddings:** ONNX bge-small-en-v1.5 (built-in, 384-dim; set `OLLAMA_URL` for Ollama)
- **Ollama:** optional, for LLM extraction (`llama3.2:3b`)

### Quick reference
\`\`\`bash
mem search "query"                              # FTS search (instant)
mem quick-store <scene> <type> <sal> <content>  # Store without embedding
mem stats                                       # Stats
mem scenes                                      # List scenes
mem scene <name>                                # Show scene cells
mem forget <id>                                 # Delete cell
\`\`\`
```

## Seed Initial Memories

Populate the database from your existing markdown files:

```bash
# Store key facts manually
mem quick-store infrastructure fact 0.9 "Server is a 2-core VPS with 2GB RAM"
mem quick-store preferences preference 0.8 "User prefers brevity"

# Or use LLM extraction (requires Ollama)
oc-memory extract-file ~/.openclaw/workspace/MEMORY.md
```

## Heartbeat Integration

Add memory maintenance to your `HEARTBEAT.md`:

```markdown
# Periodic memory maintenance (every few heartbeats):
# - Run: oc-memory export
# - Commit exports if changed: cd workspace && git add -A && git commit -m "memory backup" && git push
```

## Backup Strategy

oc-memory exports to JSON + markdown files that you can commit to git:

```bash
# Export scenes as markdown + full JSON dump
oc-memory export

# Full backup (includes optional remote SQLite copy)
OC_MEMORY_BACKUP_HOST=my-backup-server oc-memory backup
```

Add the export directory to your workspace `.gitignore` exceptions or track it directly.

## Multi-User / Multi-Agent Setups

When multiple OpenClaw agents share the same oc-memory server, enable per-agent isolation:

```bash
# Set admin bypass on the host running oc-memory
export OC_MEMORY_ADMIN_USER="your-primary-agent-id"
```

Each agent should pass its own `caller_id` when calling MCP tools:

```json
{"name": "memory_search", "arguments": {"query": "...", "caller_id": "agent-bob"}}
```

Shared project context should be stored with `visibility: "shared"` so all agents can read it.
Sensitive/personal context should use `visibility: "private"` (default).

See [docs/multi-user.md](multi-user.md) for full details.

## How the Agent Uses It

During a session, the agent's workflow becomes:

1. **Session start:** Read `MEMORY.md` for high-level context
2. **Before answering recall questions:** `mem search "relevant keywords"`
3. **After learning something new:** `mem quick-store <scene> <type> <sal> "<content>"`
4. **End of session:** Store key facts, update daily notes
5. **Heartbeat:** Periodic `oc-memory export` + git push

The SQLite DB is the fast structured layer. Markdown files remain the human-readable backup. Both coexist.

## Automatic Memory via Hooks (Recommended)

oc-memory includes OpenClaw hooks that automate recall and capture with zero
agent-side configuration.

### Install hooks

```bash
cp -r hooks/oc-memory-recall ~/.openclaw/hooks/
cp -r hooks/oc-memory-capture ~/.openclaw/hooks/
openclaw hooks enable oc-memory-recall
openclaw hooks enable oc-memory-capture
openclaw gateway restart
```

### How they work

**oc-memory-recall** (`message:received`):
1. Takes inbound message text
2. Runs `oc-memory search` (FTS5, <1ms)
3. Injects results as context before the agent responds
4. Agent sees past decisions, facts, preferences automatically

**oc-memory-capture** (`message:sent`):
1. Takes outbound response text
2. Skips trivial responses (heartbeats, NO_REPLY, short messages)
3. Stores as `exchange` cell in `conv-YYYY-MM-DD` scene
4. Raw text only — embedding/extraction deferred to batch

### Batch processing

The hooks handle the hot path. Run these on a schedule for full features:

```bash
# Embed new cells for semantic search (every few hours)
oc-memory embed

# Extract structured facts from raw exchanges (daily)
oc-memory consolidate

# Decay old low-access memories (weekly)
oc-memory decay

# Export for git backup (daily)
oc-memory export
```

### Architecture with hooks

```
┌──────────────────────────────────────────────────┐
│                 OpenClaw Gateway                  │
│                                                  │
│  message:received ──→ oc-memory-recall hook       │
│     │                    │                        │
│     │               oc-memory search (FTS)        │
│     │                    │                        │
│     ▼                    ▼                        │
│  Agent turn  ◄── [injected memory context]       │
│     │                                             │
│     ▼                                             │
│  message:sent ───→ oc-memory-capture hook         │
│                       │                           │
│                  oc-memory store                   │
└───────────────────────┼──────────────────────────┘
                        ▼
┌──────────────────────────────────────────────────┐
│              oc-memory (Python)                  │
│                                                  │
│  db.py ←→ SQLite + FTS5                         │
│  embeddings.py ←→ ONNX bge-small-en-v1.5 (default) │
│                    or Ollama nomic-embed-text (opt-in) │
│  extractor.py ←→ Ollama (any local LLM)         │
│  backup.py → JSON + markdown export             │
└──────────────────────────────────────────────────┘
```
