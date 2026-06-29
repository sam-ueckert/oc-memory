# OpenClaw Hooks for oc-memory

These hooks integrate oc-memory directly into the OpenClaw message lifecycle
for automatic memory recall and capture — no manual intervention needed.

## Hooks

### 🧠 oc-memory-recall (auto-search)

Fires on every inbound message (`message:received`). Searches oc-memory via FTS
for memories relevant to the incoming message and injects them into the agent's
context before it responds.

- **Latency**: <1ms (FTS5, no network)
- **Skips**: Messages under 10 chars, heartbeat polls, single-word queries

### 📝 oc-memory-capture (auto-store)

Fires on every outbound message (`message:sent`). Stores the agent's response
as a raw exchange cell in oc-memory.

- **Latency**: <5ms (local SQLite write)
- **Skips**: NO_REPLY, HEARTBEAT_OK, failed sends, responses under 20 chars
- **Scene**: `conv-YYYY-MM-DD` (date-based)
- **Type**: `exchange`
- **Salience**: 0.5 (default)

### Deferred Processing

Capture stores **raw text only** — no embeddings or LLM extraction at capture
time. Run the existing batch processes on a schedule:

```bash
# Add vector embeddings for semantic search
oc-memory embed

# Optional: extract structured facts from raw exchanges
oc-memory consolidate

# Optional: prune old low-access memories
oc-memory decay
```

## Install

```bash
# Copy hooks to OpenClaw managed hooks directory
cp -r hooks/oc-memory-recall ~/.openclaw/hooks/
cp -r hooks/oc-memory-capture ~/.openclaw/hooks/

# Enable both
openclaw hooks enable oc-memory-recall
openclaw hooks enable oc-memory-capture

# Restart gateway to load hooks
openclaw gateway restart
```

## Configuration

Set `OC_MEMORY_CLI` environment variable if your CLI binary isn't named
`oc-memory` or isn't on PATH:

```json
{
  "hooks": {
    "internal": {
      "entries": {
        "oc-memory-recall": {
          "enabled": true,
          "env": { "OC_MEMORY_CLI": "/path/to/oc-memory" }
        },
        "oc-memory-capture": {
          "enabled": true,
          "env": { "OC_MEMORY_CLI": "/path/to/oc-memory" }
        }
      }
    }
  }
}
```

## Verify

```bash
# Check hooks are registered
openclaw hooks list
# Should show ✓ for both oc-memory-recall and oc-memory-capture

# Check gateway logs
journalctl --user -u openclaw-gateway | grep oc-memory
# Or: openclaw logs | grep oc-memory
```

## Architecture

```
Inbound message
    │
    ├──→ oc-memory-recall: oc-memory search → inject context
    │
    ▼
Agent responds (with memory context)
    │
    ├──→ oc-memory-capture: oc-memory store → SQLite
    │
    ▼
Later (cron/manual):
    oc-memory embed       → add vector embeddings
    oc-memory consolidate → summarize scenes
    oc-memory decay       → prune stale memories
    oc-memory export      → backup to git
```

## Comparison to Cloud Memory Plugins

oc-memory with hooks provides the same auto-recall + auto-capture as
Supermemory and Mem0, but runs 100% locally with zero API costs.

| Feature | oc-memory + hooks | Supermemory | Mem0 |
|---------|------------------|-------------|------|
| Auto-recall | ✅ (FTS, <1ms) | ✅ (cloud) | ✅ (cloud) |
| Auto-capture | ✅ (local) | ✅ (cloud) | ✅ (cloud) |
| Privacy | 100% local | Cloud | Cloud default |
| Cost | Free | Paid | Free tier |
| Offline | ✅ | ❌ | Self-host only |
| Structured types | ✅ | ❌ | ❌ |
| Salience scoring | ✅ | ❌ | ❌ |
