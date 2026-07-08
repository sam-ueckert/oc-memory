---
name: oc-memory-recall
description: "Same-turn recall: searches oc-memory in before_prompt_build and prepends relevant context to the current prompt"
metadata: { "openclaw": { "emoji": "🧠", "hooks": ["before_prompt_build"] } }
---

# oc-memory Recall Plugin

Registers on OpenClaw's `before_prompt_build` hook (see OpenClaw's
`docs/plugins/hooks.md`) and searches oc-memory (SQLite FTS) before each
model call, returning `{ prependContext }` so relevant memories are folded
into the *same* turn's prompt.

## Why a plugin instead of the `message:received` hook

`hooks/oc-memory-recall` (this repo) reacts to the internal/shell
`message:received` event, which is fire-and-forget from OpenClaw's
perspective — it cannot reliably inject context into the same model turn
that triggered it. `before_prompt_build` runs as part of the prompt-build
pipeline itself, so its `prependContext` return value reaches the model
before it answers. This plugin is the primary, supported path for same-turn
recall; the `message:received` hook remains available for setups that only
need best-effort, next-turn-or-later recall.

This plugin does not patch OpenClaw core and does not make
`message:received` awaited — it uses the seam OpenClaw already exposes for
this purpose.

## Requirements

- `oc-memory` CLI must be on PATH (or set the `OC_MEMORY_CLI` env var)
- Memory database must exist (run `oc-memory stats` to initialize)

## Safety properties

Identical posture to `hooks/oc-memory-recall` (implementation is reused, not
duplicated — see [`../../hooks/oc-memory-recall/handler.ts`](../../hooks/oc-memory-recall/handler.ts)):

- **No shell interpolation.** The query is passed to the CLI as a single
  argument-array element (`execFile(cli, ["search", query])`), never built
  into a shell command string.
- **Hard timeout, fail open.** Bounded to `OC_MEMORY_RECALL_TIMEOUT_MS`
  (default 2500ms, clamped to 500–3000ms). On timeout, non-zero exit, or any
  other error, the handler returns `undefined` — no context is injected, and
  the agent turn proceeds normally.
- **Non-blocking.** Fully async (`child_process.execFile`).
- **Kill switch.** Set `OC_MEMORY_RECALL_DISABLED=1` (or `true`/`yes`) to
  disable recall without removing the plugin. Checked first, before any
  event content is read or any subprocess is spawned.
- **Recall only.** This plugin never writes to oc-memory. No capture/store
  path is invoked from this module (see `hooks/oc-memory-capture` for the
  separate, opt-in capture hook).
- **Bounded recall.** Same bounding/dedupe/telemetry posture as
  `hooks/oc-memory-recall` (shared implementation — see that module's
  "Recall bounding" section for the full rationale, especially why
  `OC_MEMORY_RECALL_MIN_SCORE` defaults to `0.0`).
- **Separate dedupe window from the hook.** The plugin and the
  `message:received` hook are alternate recall paths and each keeps its own
  volatile in-process dedupe state. Running both at once can duplicate recall
  across paths; normally enable this plugin for same-turn recall and leave the
  hook disabled unless you explicitly want both.

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `OC_MEMORY_CLI` | `oc-memory` | Path to the CLI/wrapper to invoke. |
| `OC_MEMORY_RECALL_DISABLED` | unset (enabled) | Kill switch; any of `1`/`true`/`yes` disables recall. |
| `OC_MEMORY_RECALL_TIMEOUT_MS` | `2500` | Hard timeout budget, clamped to 500–3000ms. |
| `OC_MEMORY_CLI_ALLOW_SHELL` | unset (disabled) | Opt-in only, for `.cmd`/`.bat` wrappers on Windows. |
| `OC_MEMORY_RECALL_TOP_K` | `5` | Max results requested from the CLI (`--limit`), clamped to 1–20. |
| `OC_MEMORY_RECALL_MIN_SCORE` | `0.0` | Minimum similarity score (`--min-score`), clamped to 0.0–1.0. Tune empirically via telemetry — see `hooks/oc-memory-recall/HOOK.md`. |
| `OC_MEMORY_RECALL_EXCERPT_MAX_CHARS` | `800` | Max characters of the injected excerpt, clamped to 100–4000. Legacy `OC_MEMORY_RECALL_EXCERPT_MAX` still honored as a fallback name (see HOOK.md for the behavior-change note). |
| `OC_MEMORY_RECALL_DEDUPE_WINDOW` | `3` | Number of past recall attempts checked for duplicate results (in-process, volatile). `0` disables dedupe. Clamped to 0–10. |
| `OC_MEMORY_RECALL_TELEMETRY` | unset (disabled) | Opt-in JSONL telemetry for recall attempts; never logs raw memory content. |
| `OC_MEMORY_RECALL_TELEMETRY_PATH` | `~/.oc-memory/recall-telemetry.jsonl` | Telemetry output path, when enabled. |

## Install

OpenClaw plugin loading conventions vary by version; the two documented
seams referenced by this plugin are `before_prompt_build` (used here for
same-turn recall) and `agent_turn_prepare` / `api.enqueueNextTurnInjection(...)`
(for queued next-turn injection, not used by this plugin). Point your
OpenClaw plugin config at this directory's `index.ts`, e.g.:

```json
{
  "plugins": {
    "entries": {
      "oc-memory-recall": {
        "enabled": true,
        "module": "/path/to/oc-memory/plugins/oc-memory-recall/index.ts",
        "env": { "OC_MEMORY_CLI": "/path/to/oc-memory" }
      }
    }
  }
}
```

Prompt injection can be disabled per plugin (independent of the
`OC_MEMORY_RECALL_DISABLED` kill switch above) via:

```json
{ "plugins": { "entries": { "oc-memory-recall": { "hooks": { "allowPromptInjection": false } } } } }
```

If your OpenClaw version's plugin manifest shape differs from the example
above, treat `index.ts`'s default export (`register(api)` calling
`api.on("before_prompt_build", ...)`) as the stable contract and adjust the
manifest/loader wiring accordingly — the recall logic itself does not
depend on the manifest format.

## Tests

```bash
node --import tsx --test tests/plugins/oc-memory-recall/*.test.ts
```

Covers `buildQuery`, plugin registration, the recall-only guarantee, and
(shared with the hook) `--limit`/`--min-score` argv pass-through, FIFO
dedupe behavior, and the no-empty-`prependContext` guarantee when
everything is filtered/deduped.
