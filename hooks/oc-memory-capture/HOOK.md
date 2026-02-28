---
name: oc-memory-capture
description: "Auto-capture: stores conversation exchanges in oc-memory after every AI turn"
metadata: { "openclaw": { "emoji": "📝", "events": ["message:sent"] } }
---

# oc-memory Capture Hook

Automatically stores conversation exchanges in oc-memory after every outbound
message. Uses a quick-store for fast writes — embedding and extraction happen
later via the existing batch process (`oc-memory embed`).

## Requirements

- `oc-memory` CLI must be on PATH (or set `OC_MEMORY_CLI` env var)
- Memory database must exist (run `oc-memory stats` to initialize)

## Deferred Processing

Embedding and structured extraction are NOT done at capture time.
Run `oc-memory embed` and optionally `oc-memory consolidate` on a schedule.
