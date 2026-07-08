---
name: oc-memory-recall
description: "Auto-recall: searches oc-memory before every AI turn and injects relevant context"
metadata: { "openclaw": { "emoji": "🧠", "events": ["message:received"] } }
---

# oc-memory Recall Hook

Searches oc-memory (SQLite FTS) on every inbound message and injects relevant
memories into the agent's context before it responds. Gives your agent automatic
access to past conversations, decisions, and facts without manual search calls.

> **Note:** `message:received` is fire-and-forget from OpenClaw's
> perspective, so this hook cannot reliably inject context into the *same*
> model turn that triggered it. For same-turn recall, use the
> [`oc-memory-recall` plugin](../../plugins/oc-memory-recall/PLUGIN.md)
> instead, which registers on OpenClaw's `before_prompt_build` hook. This
> hook remains available for setups that only need best-effort recall.

## Requirements

- `oc-memory` CLI must be on PATH (or set the `OC_MEMORY_CLI` env var)
- Memory database must exist (run `oc-memory stats` to initialize)

## Safety properties

- **No shell interpolation.** The search query is passed to the CLI as a
  single argument-array element (`execFile(cli, ["search", query])`), never
  built into a shell command string. Quotes, backticks, `$()`, `;`, `|`, `&`,
  and other shell metacharacters in an inbound message travel safely.
- **Hard timeout, fail open.** The subprocess is killed if it runs longer
  than `OC_MEMORY_RECALL_TIMEOUT_MS` (default 2500ms, clamped to 500–3000ms).
  On timeout, non-zero exit, or any other error, the hook injects nothing and
  message handling continues normally — it never blocks the gateway.
- **Non-blocking.** Execution is fully async (`child_process.execFile`); the
  event loop keeps processing other work while the search subprocess runs.
- **Kill switch.** Set `OC_MEMORY_RECALL_DISABLED=1` (or `true`/`yes`) to turn
  the hook off entirely without uninstalling it. Checked first, before any
  other work.
- **Bounded recall.** Results are bounded server-side via `--limit`/
  `--min-score` flags passed through to `oc-memory search`, and deduped
  in-process (volatile session state, not persisted to the DB) so the same
  memory isn't re-injected on every turn. See "Recall bounding" below.

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `OC_MEMORY_CLI` | `oc-memory` | Path to the CLI/wrapper to invoke. |
| `OC_MEMORY_RECALL_DISABLED` | unset (enabled) | Kill switch; any of `1`/`true`/`yes` disables recall. |
| `OC_MEMORY_RECALL_TIMEOUT_MS` | `2500` | Hard timeout budget, clamped to 500–3000ms. |
| `OC_MEMORY_CLI_ALLOW_SHELL` | unset (disabled) | Opt-in only. See "Windows `.cmd`/`.bat` wrappers" below. |
| `OC_MEMORY_RECALL_TOP_K` | `5` | Max results requested from the CLI (`--limit`), clamped to 1–20. |
| `OC_MEMORY_RECALL_MIN_SCORE` | `0.0` | Minimum similarity score (`--min-score`), clamped to 0.0–1.0. See note below. |
| `OC_MEMORY_RECALL_EXCERPT_MAX_CHARS` | `800` | Max characters of the *injected* excerpt, clamped to 100–4000. Preferred name; see legacy note below. |
| `OC_MEMORY_RECALL_DEDUPE_WINDOW` | `3` | Number of past recall attempts to check for duplicate results against. `0` disables dedupe. Clamped to 0–10. |
| `OC_MEMORY_RECALL_TELEMETRY` | unset (disabled) | Opt-in; any of `1`/`true`/`yes` enables JSONL telemetry. See "Telemetry" below. |
| `OC_MEMORY_RECALL_TELEMETRY_PATH` | `~/.oc-memory/recall-telemetry.jsonl` | Telemetry output path, when telemetry is enabled. |

### Recall bounding

Added in issue #6, on top of PR #5's baseline hook:

- `OC_MEMORY_RECALL_TOP_K`, `OC_MEMORY_RECALL_MIN_SCORE`, and
  `OC_MEMORY_RECALL_EXCERPT_MAX_CHARS` are passed through to
  `oc-memory search` as `--limit`/`--min-score`/`--excerpt-max` — bounding,
  filtering, and per-result CLI output truncation happen before injection,
  not by fetching more than needed and discarding it client-side.
- **`OC_MEMORY_RECALL_MIN_SCORE` defaults to `0.0` (no filtering)
  deliberately.** Similarity score distributions vary by embedding model
  and corpus — a "safe-looking" non-zero default (e.g. `0.3`) could
  silently filter out everything for one deployment and nothing for
  another. Tune it empirically for your deployment using
  `OC_MEMORY_RECALL_TELEMETRY=1` (below) to see the `topScore` your real
  queries produce before picking a threshold.
- Dedupe is in-process, volatile session state (a FIFO window over the last
  `OC_MEMORY_RECALL_DEDUPE_WINDOW` recall attempts' result identifiers) —
  not written to the database. It resets when the hook process restarts.
  If every candidate for a turn is deduped (or the CLI returns nothing
  usable), the hook injects nothing — it never sends an empty
  `[oc-memory Recall]` header.
- The hook and the same-turn plugin are alternate recall paths and each owns
  its own volatile dedupe window. Running both at once can therefore inject
  the same memory through the other path even after one path dedupes it;
  operators should usually enable the plugin for same-turn recall and leave
  this hook disabled unless they intentionally want both behaviors.
- **Legacy `OC_MEMORY_RECALL_EXCERPT_MAX` env var:** still read as a
  fallback when `OC_MEMORY_RECALL_EXCERPT_MAX_CHARS` is unset, so existing
  PR #5 configs keep working. Behavior change (documented, not silent):
  the effective value now goes through the tighter 100–4000 clamp and the
  new 800-char default, instead of PR #5's 1500-char default / 10 000-char
  ceiling — this brings it in line with the other bounding knobs added
  here. Prefer the new name going forward.

### Telemetry

Set `OC_MEMORY_RECALL_TELEMETRY=1` to append one compact JSON line per
recall attempt to `OC_MEMORY_RECALL_TELEMETRY_PATH` (default
`~/.oc-memory/recall-telemetry.jsonl`): query length/token estimate,
candidate/deduped/injected counts, injected character length, top score
(when available), elapsed time, and the effective `topK`/`minScore` for
that attempt. **Raw memory content is never logged.** Telemetry is
off by default and best-effort — file errors are swallowed and never
affect the fail-open recall path.

## Windows `.cmd`/`.bat` wrappers

Windows can't launch a `.cmd`/`.bat` file directly — `CreateProcess` needs
`cmd.exe` as an interpreter. If `OC_MEMORY_CLI` points at a `.cmd`/`.bat` file
on Windows (e.g. `C:/Users/heyni/bin/oc-memory-cherry.cmd`), the hook will
**not** use a shell by default, and the search will fail (safely — fail open,
no recall, no crash).

To allow it, set `OC_MEMORY_CLI_ALLOW_SHELL=1`. This still passes the query
as a single argument-array element to Node's `child_process`, which applies
its own argument escaping when invoking `cmd.exe` (Node ≥18.20.2/20.12.2,
post [CVE-2024-27980](https://github.com/advisories/GHSA-25gc-vhvw-jr3q)) —
meaningfully safer than hand-built shell strings, but it is still a shell
invocation, so it's opt-in rather than automatic. Non-`.cmd`/`.bat` paths and
non-Windows platforms never use a shell, regardless of this setting.

## Tests

```bash
node --import tsx --test tests/hooks/oc-memory-recall/handler.test.ts
```

Covers: argv-safety (quotes/shell metacharacters preserved as a single argv
element through a real, shell-free child process), timeout/kill/fail-open
behavior against a real stalling subprocess (with an event-loop-liveness
check), non-zero-exit fail-open, the kill switch, the `.cmd`/`.bat` shell
opt-in wiring, env clamping for `OC_MEMORY_RECALL_TOP_K`/`_MIN_SCORE`/
`_EXCERPT_MAX_CHARS`/`_DEDUPE_WINDOW`, the `--limit`/`--min-score` argv
pass-through, FIFO dedupe behavior, and the no-empty-header guarantee when
everything is filtered/deduped.
