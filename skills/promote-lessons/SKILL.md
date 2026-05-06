---
name: promote-lessons
description: Converts episodic memory (corrections, lessons) into procedural memory (behavioral rules) by synthesizing them via LLM and injecting into SOUL.md or CLAUDE.md. This is the "learning loop" — past mistakes become standing rules.
---

# Promote Lessons

Converts episodic memory (corrections, lessons) into procedural memory (behavioral rules). The script queries `mem` for correction-tagged cells and high-salience lesson cells, synthesizes terse behavioral rules via LLM, and injects them between HTML markers in your agent's config file.

Works with **OpenClaw** (`SOUL.md`), **Claude Code** (`CLAUDE.md`), and any agent platform that reads a project file at startup.

## How It Works

1. `mem search-tag correction` → finds cells tagged with `correction`
2. `mem scene lessons` → finds high-salience cells (sal ≥ 0.8) in the lessons scene
3. Combined cells → LLM prompt → numbered list of terse behavioral rules
4. Rules injected between `<!-- LEARNED_RULES_START -->` / `<!-- LEARNED_RULES_END -->` in target file

## Target File Auto-Detection

The script picks the target in this order:
1. **Explicit `TARGET_FILE` env var**
2. **SOUL.md** in `$WORKSPACE` (OpenClaw convention)
3. **CLAUDE.md** in `$WORKSPACE` (Claude Code convention)

## API Provider Auto-Detection

The script auto-detects the LLM provider:
- If `API_URL` contains `anthropic.com` → uses **Anthropic Messages API** (x-api-key auth)
- Otherwise → uses **OpenAI Chat Completions API** (Bearer auth, works with OpenClaw)

Override with `API_PROVIDER=anthropic` or `API_PROVIDER=openclaw`.

## Setup

### 1. Add HTML markers to your target file

Add this block where you want learned rules to appear:

```markdown
<!-- LEARNED_RULES_START -->
## Learned Rules
*No rules yet — run promote-lessons after tagging some corrections.*
<!-- LEARNED_RULES_END -->
```

### 2. Configure and test

**OpenClaw:**
```bash
export API_TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.openclaw/openclaw.json'))['gateway']['auth']['token'])")
export WORKSPACE="/path/to/your/workspace"
bash promote-lessons.sh
```

**Claude Code (Anthropic API):**
```bash
export API_TOKEN="sk-ant-your-key-here"
export API_URL="https://api.anthropic.com/v1/messages"
export WORKSPACE="/path/to/your/project"
bash promote-lessons.sh
```

### 3. Install cron (after manual test succeeds)

```bash
# Weekly on Sunday at 3am (adjust to taste)
(crontab -l 2>/dev/null; echo "0 3 * * 0 export PATH=\"\$HOME/bin:/usr/local/bin:/usr/bin:/bin:\$PATH\"; API_TOKEN=\$(python3 -c \"import json; print(json.load(open('\$HOME/.openclaw/openclaw.json'))['gateway']['auth']['token'])\") WORKSPACE=/path/to/workspace bash /path/to/promote-lessons.sh >> /tmp/promote-lessons.log 2>&1") | crontab -
```

For Claude Code users with API key in env:
```bash
(crontab -l 2>/dev/null; echo "0 3 * * 0 export PATH=\"\$HOME/bin:/usr/local/bin:/usr/bin:/bin:\$PATH\"; API_TOKEN=\"\$ANTHROPIC_API_KEY\" API_URL=\"https://api.anthropic.com/v1/messages\" WORKSPACE=/path/to/project bash /path/to/promote-lessons.sh >> /tmp/promote-lessons.log 2>&1") | crontab -
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `WORKSPACE` | `$(pwd)` | Workspace directory |
| `TARGET_FILE` | auto-detect | File to inject rules into (SOUL.md or CLAUDE.md) |
| `MEM_CMD` | `mem` | Path to mem binary |
| `API_PROVIDER` | auto-detect | `openclaw` or `anthropic` |
| `API_URL` | `http://127.0.0.1:18789/v1/chat/completions` | LLM endpoint |
| `API_TOKEN` | *(required)* | Bearer token (OpenClaw) or API key (Anthropic) |
| `API_MODEL` | provider default | Model to use for synthesis |
| `MAX_RULES` | `30` | Max rules in output |

## Tagging Corrections

When the agent makes a mistake and gets corrected:

```bash
# Store a correction directly
mem quick-store lessons lesson 0.9 "Never use foo when bar is expected — causes silent failures"
mem tag <id> correction

# Or tag an existing cell
mem search "the mistake"
mem tag <id> correction
```

**Claude Code users:** Tag corrections via `mem` CLI in your terminal, or teach your agent to self-tag when you correct it.

Salience guide:
- `0.9` — serious mistake that affected output
- `0.8` — behavioral drift or repeated error
- `0.7` — minor but worth tracking

## HTML Markers Pattern

The script replaces everything between the markers on each run:

```
<!-- LEARNED_RULES_START -->
## Learned Rules
*Auto-generated 2026-05-06 13:45 CDT from 12 memory cells — do not edit manually*

1. Never call restart directly — always use safe restart scripts.
2. ...
<!-- LEARNED_RULES_END -->
```

Markers must exist in the target file before the first run.
