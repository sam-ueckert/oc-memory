# promote-lessons

**Converts episodic memory (corrections, lessons) into procedural memory (behavioral rules).**

The skill queries `mem` for correction-tagged cells and high-salience lesson cells, synthesizes terse behavioral rules via LLM, and injects them between HTML markers in a target file (default: `SOUL.md`).

This is the "learning loop" — past mistakes and insights become standing rules that shape future behavior.

---

## How It Works

1. `mem search-tag correction` → finds cells tagged with `correction`
2. `mem scene lessons` → finds high-salience cells (sal ≥ 0.8) in the lessons scene
3. Combined cells → LLM prompt → numbered list of terse behavioral rules
4. Rules injected between `<!-- LEARNED_RULES_START -->` / `<!-- LEARNED_RULES_END -->` in `TARGET_FILE`

---

## Setup

### 1. Add HTML markers to your target file

Before the `⛔ HARD RULES` section (or wherever you want rules to appear), add:

```markdown
<!-- LEARNED_RULES_START -->
## Learned Rules
*Auto-generated — do not edit manually*
<!-- LEARNED_RULES_END -->
```

### 2. Set environment variables

| Variable | Default | Description |
|---|---|---|
| `WORKSPACE` | `$(pwd)` | Workspace directory |
| `TARGET_FILE` | `$WORKSPACE/SOUL.md` | File to inject rules into |
| `MEM_CMD` | `mem` | Path to mem binary |
| `API_URL` | `http://127.0.0.1:18789/v1/chat/completions` | Chat Completions endpoint |
| `API_TOKEN` | *(required)* | Bearer token for the API |
| `MAX_RULES` | `30` | Max rules in output |

### 3. Test manually

```bash
export API_TOKEN=$(python3 -c "import json; print(json.load(open('/home/swabby/.openclaw-slack/openclaw.json'))['gateway']['auth']['token'])")
export WORKSPACE="/home/swabby/repos/swabby-brain"
export TARGET_FILE="$WORKSPACE/SOUL.md"
bash ~/repos/oc-memory/skills/promote-lessons/scripts/promote-lessons.sh
```

### 4. Install cron (after verifying manually)

```bash
# Run daily at 03:00
(crontab -l 2>/dev/null; echo "0 3 * * * bash ~/repos/swabby-brain/scripts/promote-lessons.sh >> /tmp/promote-lessons.log 2>&1") | crontab -
```

---

## Tagging Corrections

When the agent makes a mistake and gets corrected, tag it:

```bash
# Store a new correction directly
mem quick-store lessons lesson 0.9 "Never use foo when bar is expected — causes silent failures"
# Then tag by ID (from the output of quick-store)
mem tag <id> correction

# Or search for an existing cell and tag it
mem search "gateway restart"
mem tag <id> correction
```

Salience guide for corrections:
- `0.9` — serious mistake that affected output
- `0.8` — behavioral drift or repeated error
- `0.7` — minor but worth tracking

---

## HTML Markers Pattern

The script replaces everything between the markers on each run:

```
<!-- LEARNED_RULES_START -->
## Learned Rules
*Auto-generated 2026-05-06 13:45 CDT from 12 memory cells — do not edit manually*

1. Never call `openclaw gateway restart` directly — always use safe restart scripts.
2. ...
<!-- LEARNED_RULES_END -->
```

The markers must exist in `TARGET_FILE` before the first run. They are NOT auto-created.

---

## Customization

- **Different scene:** Change the `mem scene lessons` query in the script to any scene name
- **More cells:** Increase the `head -40` / `head -30` limits
- **Stricter salience:** Change `sal:(0\.[89]|1\.0)` to `sal:1\.0` for only critical lessons
- **Different target file:** Set `TARGET_FILE=/path/to/RULES.md`
- **More rules:** Set `MAX_RULES=50`

---

## Deployment (swabby-brain)

The wrapper script lives at `~/repos/swabby-brain/scripts/promote-lessons.sh`.
It sets all env vars and calls this skill script.

To run manually:
```bash
bash ~/repos/swabby-brain/scripts/promote-lessons.sh
```
