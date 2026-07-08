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

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `OC_MEMORY_CLI` | `oc-memory` | Path to the CLI/wrapper to invoke. |
| `OC_MEMORY_RECALL_DISABLED` | unset (enabled) | Kill switch; any of `1`/`true`/`yes` disables recall. |
| `OC_MEMORY_RECALL_TIMEOUT_MS` | `2500` | Hard timeout budget, clamped to 500–3000ms. |
| `OC_MEMORY_CLI_ALLOW_SHELL` | unset (disabled) | Opt-in only. See "Windows `.cmd`/`.bat` wrappers" below. |

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
node --test tests/hooks/oc-memory-recall/handler.test.ts
```

Covers: argv-safety (quotes/shell metacharacters preserved as a single argv
element through a real, shell-free child process), timeout/kill/fail-open
behavior against a real stalling subprocess (with an event-loop-liveness
check), non-zero-exit fail-open, the kill switch, and the `.cmd`/`.bat`
shell opt-in wiring.
