# Design Notes

## Why This Exists

LLM agents running on OpenClaw wake up stateless each session. Markdown files provide basic continuity, but they don't scale — searching across dozens of files is slow, imprecise, and burns context window tokens.

oc-memory adds a structured layer: typed cells with salience scores, grouped by scene, searchable via FTS5 and optionally via vector similarity. The agent can recall specific facts in milliseconds instead of reading entire files.

## Architecture Decisions

### SQLite + FTS5 as the core

- Zero external dependencies (SQLite ships with Python)
- FTS5 provides fast full-text search without embeddings
- Single-file database — easy to backup, copy, restore
- WAL mode for concurrent reads

### Embeddings are optional

Vector search via Ollama is a nice-to-have, not a requirement. FTS5 handles 90% of recall queries well. This means oc-memory works on minimal hardware (1 core, 512MB) without Ollama.

### Memory cells, not documents

Each memory unit is a single fact, decision, or task — not a paragraph or a page. This makes search results precise and composable. The agent can pull exactly the cells it needs.

### Scene grouping

Scenes cluster related cells by topic. Each scene can have a consolidated summary, which is useful for building context without pulling every individual cell.

### Salience scoring

Every cell has a salience score (0.0–1.0). This enables:
- Prioritized retrieval (highest-salience cells surface first in fallback queries)
- Future decay mechanics (low-salience, rarely-accessed cells fade over time)

### Access counting

Each search hit increments an `access_count` on the returned cells. This creates a usage signal for future features like:
- Decay: reduce salience of cells that are never recalled
- Consolidation: frequently-accessed cells are clearly important
- Pruning: zero-access cells after N days are candidates for removal

### Dual persistence

- **SQLite DB** — fast, structured, searchable (primary)
- **Markdown + JSON exports** — human-readable, git-tracked (backup)

The DB is the working copy. Exports are the safety net. If the DB is lost, restore from JSON. If exports are stale, re-export from the DB.

## Cell Types

| Type | When to use |
|------|-------------|
| `fact` | Observable truths, configuration details, measurements |
| `decision` | Choices made and their reasoning |
| `preference` | User or agent preferences, opinions |
| `task` | Pending work, todos, blocked items |
| `risk` | Warnings, potential problems, known issues |
| `plan` | Future intentions, project outlines |
| `lesson` | Learned from mistakes, operational insights |

## Future Directions

### Salience decay (planned)

Cells that are never recalled should fade. The mechanism:
- Track `last_recalled` timestamp (via access_count updates)
- Periodic decay pass: reduce salience of cells with 0 recalls and age > N days
- Floor at 0.1 — cells are never fully deleted by decay alone

### Semantic deduplication

When storing new cells, check for existing cells with high content similarity. If a near-duplicate exists, update it instead of creating a new one.

### Conversation-level extraction

Currently, extraction works on arbitrary text blocks. A conversation-aware mode could:
- Parse user/assistant turns separately
- Weight user statements higher for preference/decision extraction
- Track which session produced each cell

### Cross-agent memory sharing

Multiple OpenClaw agents could share a memory DB or sync specific scenes. Useful for teams or multi-agent setups where context should propagate.
