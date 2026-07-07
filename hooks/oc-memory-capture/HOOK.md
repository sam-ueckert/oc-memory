---
name: oc-memory-capture
description: "Auto-capture: stores conversation exchanges in oc-memory after every AI turn"
metadata: { "openclaw": { "emoji": "📝", "events": ["message:sent"] } }
---

# oc-memory Capture Hook

Automatically stores conversation exchanges in oc-memory after every outbound
message. Uses a fast file-based store for writes — embedding and extraction happen
later via the existing batch process (`oc-memory embed`).

## Requirements

- `oc-memory` CLI must be on PATH (or set `OC_MEMORY_CLI` env var)
- Memory database must exist (run `oc-memory stats` to initialize)

## Capture Behavior

1. Kill switch (`OC_MEMORY_CAPTURE_DISABLED`) is checked before the event
   content is read or any subprocess is spawned.
2. Non-blocking `execFile` with argv array — no shell string interpolation.
3. Hard timeout (3s); on timeout the capture fails open without blocking
   message delivery.
4. On error (missing CLI, non-zero exit, timeout), the hook fails open
   silently — no thrown error, message handling continues.
5. Captured data is written to a temp file first, then passed to
   `<cli> store-stdin <file>` — never piped through a shell. The temporary
   payload file and auto-created temp directory are cleaned up after each
   attempt.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OC_MEMORY_CLI` | `oc-memory` | Path to oc-memory CLI |
| `OC_MEMORY_CAPTURE_DISABLED` | (unset) | Set to `1`/`true`/`yes` to disable capture without uninstalling |

## Deferred Processing

Embedding and structured extraction are NOT done at capture time.
Run `oc-memory embed` and optionally `oc-memory consolidate` on a schedule.
