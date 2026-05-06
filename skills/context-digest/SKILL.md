---
name: context-digest
description: Prepopulate agent context with high-salience oc-memory cells by auto-generating a Memory Digest section in MEMORY.md or CLAUDE.md. Use when setting up oc-memory for the first time, wiring memory into a new agent workspace, or when the agent should have top memories pre-loaded at session start without running manual searches.
---

# Context Digest

Keeps a `## Memory Digest` section in your agent's context file fresh so every new session starts with high-salience memories already loaded — no manual search needed.

Works with **OpenClaw** (`MEMORY.md`), **Claude Code** (`CLAUDE.md`), and any agent platform that auto-injects a project file at session start.

## How It Works

1. A cron job (or manual run) executes `gen-context-digest.sh` every 2-3 hours
2. The script queries `mem scene <name>` for each configured scene, filters by salience ≥ 0.8, and rewrites a bounded block in the target file
3. Your agent platform auto-injects the file at session start → digest is in context before the first user message

## Target File Auto-Detection

The script picks the target file in this order:
1. **Explicit argument**: `bash gen-context-digest.sh /path/to/file.md`
2. **MEMORY.md** in `$WORKSPACE` (OpenClaw convention)
3. **CLAUDE.md** in `$WORKSPACE` (Claude Code convention)
4. Falls back to `$WORKSPACE/MEMORY.md` (creates on first cron run)

## Setup

### 1. Copy the script

```bash
cp skills/context-digest/scripts/gen-context-digest.sh ~/bin/gen-context-digest.sh
chmod +x ~/bin/gen-context-digest.sh
```

### 2. Test it

```bash
WORKSPACE=/path/to/your/workspace bash ~/bin/gen-context-digest.sh
```

Verify the `## Memory Digest` block appears in your target file.

### 3. Add HTML markers to your target file

Add this block where you want the digest to appear:

```markdown
<!-- ARCHY_DIGEST_START -->
<!-- ARCHY_DIGEST_END -->
```

The script will replace between the markers on next run. If no markers exist, it appends to the end of the file.

### 4. Install cron

```bash
crontab -e
```

Add (adjust paths):

```
0 */3 * * * export PATH="$HOME/bin:/usr/local/bin:/usr/bin:/bin:$PATH"; WORKSPACE=/path/to/workspace bash ~/bin/gen-context-digest.sh >> /tmp/context-digest.log 2>&1
```

## Platform-Specific Notes

### OpenClaw

OpenClaw auto-injects `MEMORY.md` at session start. Add the markers to `MEMORY.md` and the digest is available before the first user message.

For `AGENTS.md` integration:

```markdown
## First-Message Recall

**Memory Digest is pre-loaded** in `## Memory Digest` at the bottom of `MEMORY.md`.
Use it immediately. Run `mem search "<topic>"` only for specific topics not covered by the digest.
```

### Claude Code

Claude Code auto-injects `CLAUDE.md` at session start. Add the markers to `CLAUDE.md`:

```markdown
# My Project

... your existing CLAUDE.md content ...

<!-- ARCHY_DIGEST_START -->
<!-- ARCHY_DIGEST_END -->
```

The digest will be in context for every `claude` session without any MCP tool calls.

For projects using both MEMORY.md and CLAUDE.md, point the script at whichever file your agent reads first:

```bash
WORKSPACE=/path/to/project bash gen-context-digest.sh /path/to/project/CLAUDE.md
```

## Customizing Scenes

The script queries these scenes by default: `lessons`, `config`, `infrastructure`, `projects`, `foreman`, `commodore`.

Edit the scene list in `gen-context-digest.sh` to match your memory schema:

```bash
LESSONS=$("$MEM_CMD" scene "my-lessons" ...)
INFRA=$(  "$MEM_CMD" scene "my-infra" ...)
```

Run `mem scenes` to list all scenes in your store.

## Force Refresh

```bash
WORKSPACE=/path/to/workspace bash ~/bin/gen-context-digest.sh
```
