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

## Create a CLI Wrapper

Create a shell wrapper at `~/bin/mem` that your agent can call quickly:

```bash
#!/bin/bash
# ~/bin/mem — Quick memory interface
export OC_MEMORY_DB="${OC_MEMORY_DB:-$HOME/.openclaw/memory.db}"
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

case "${1:-}" in
    search)
        shift
        # FTS-only search (fast, no Ollama dependency)
        python3 -c "
import sys, os
from oc_memory.db import MemoryDB
db = MemoryDB(os.environ['OC_MEMORY_DB'])
query = ' '.join(sys.argv[1:])
results = db.search_fts(query, limit=15)
if not results:
    print('No results.')
else:
    for r in results:
        print(f'[{r[\"id\"]}] [{r[\"cell_type\"]}] scene:{r[\"scene\"]} sal:{r[\"salience\"]:.2f} — {r[\"content\"][:150]}')
" "$@"
        ;;
    quick-store)
        shift
        SCENE="$1"; TYPE="$2"; SAL="$3"; shift 3; CONTENT="$*"
        python3 -c "
import sys, os
from oc_memory.db import MemoryDB
db = MemoryDB(os.environ['OC_MEMORY_DB'])
cell = {'scene': sys.argv[1], 'cell_type': sys.argv[2], 'salience': float(sys.argv[3]), 'content': ' '.join(sys.argv[4:])}
rid = db.insert_cell(cell)
print(f'Stored cell {rid}: [{cell[\"cell_type\"]}] {cell[\"scene\"]} — {cell[\"content\"][:80]}')
" "$SCENE" "$TYPE" "$SAL" "$CONTENT"
        ;;
    *)
        exec oc-memory "$@"
        ;;
esac
```

```bash
chmod +x ~/bin/mem
```

Make sure `~/bin` is in your PATH (add to `~/.bashrc` if needed):
```bash
export PATH="$HOME/bin:$PATH"
```

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
- **DB:** `~/.openclaw/memory.db`
- **Ollama:** `http://localhost:11434` (optional, for embeddings/extraction)

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

## How the Agent Uses It

During a session, the agent's workflow becomes:

1. **Session start:** Read `MEMORY.md` for high-level context
2. **Before answering recall questions:** `mem search "relevant keywords"`
3. **After learning something new:** `mem quick-store <scene> <type> <sal> "<content>"`
4. **End of session:** Store key facts, update daily notes
5. **Heartbeat:** Periodic `oc-memory export` + git push

The SQLite DB is the fast structured layer. Markdown files remain the human-readable backup. Both coexist.
