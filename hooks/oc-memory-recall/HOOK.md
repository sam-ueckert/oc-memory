---
name: oc-memory-recall
description: "Auto-recall: searches oc-memory before every AI turn and injects relevant context"
metadata: { "openclaw": { "emoji": "🧠", "events": ["message:received"] } }
---

# oc-memory Recall Hook

Searches oc-memory (SQLite FTS) on every inbound message and injects relevant
memories into the agent's context before it responds. Gives your agent automatic
access to past conversations, decisions, and facts without manual search calls.

## Requirements

- `oc-memory` CLI must be on PATH (or set the `OC_MEMORY_CLI` env var)
- Memory database must exist (run `oc-memory stats` to initialize)
