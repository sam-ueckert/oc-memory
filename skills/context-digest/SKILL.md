---
name: context-digest
description: Prepopulate agent context with high-salience oc-memory cells by auto-generating a Memory Digest section in MEMORY.md. Use when setting up oc-memory for the first time, wiring memory into a new agent workspace, or when the agent should have top memories pre-loaded at session start without running manual searches.
---

# Context Digest

Keeps a `## Memory Digest` section in your `MEMORY.md` fresh so every new agent session starts with high-salience memories already in context — no manual search needed.

## How It Works

1. A cron job runs `gen-context-digest.sh` every 2-3 hours
2. The script queries `mem scene <name>` for each configured scene, filters by salience ≥ 0.8, and rewrites a bounded block in `MEMORY.md`
3. OpenClaw (or any agent platform) auto-injects `MEMORY.md` at session start → digest is in context before the first user message

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

Verify the `## Memory Digest` block appears at the bottom of your `MEMORY.md`.

### 3. Install cron

```bash
crontab -e
```

Add (adjust path as needed):

```
0 */3 * * * WORKSPACE=/path/to/workspace bash /home/<user>/bin/gen-context-digest.sh >> /tmp/context-digest.log 2>&1
```

**Required:** Set `PATH` explicitly if `mem` isn't on the default cron PATH:

```
0 */3 * * * export PATH="/home/<user>/bin:/usr/local/bin:/usr/bin:/bin"; WORKSPACE=/path/to/workspace bash /home/<user>/bin/gen-context-digest.sh >> /tmp/context-digest.log 2>&1
```

### 4. Add HTML markers to MEMORY.md (optional)

If you want to pre-position the digest (e.g., at the end of the file):

```bash
cat >> /path/to/MEMORY.md << 'EOF'

<!-- ARCHY_DIGEST_START -->
<!-- ARCHY_DIGEST_END -->
EOF
```

The script will replace between the markers on next run.

## Customizing Scenes

The script queries these scenes by default: `lessons`, `config`, `infrastructure`, `projects`, `foreman`, `commodore`.

Edit the scene list in `gen-context-digest.sh` to match your memory schema:

```bash
# Add or remove scenes — scene names must match what you used in mem quick-store
LESSONS=$("$MEM_CMD" scene "lessons" ...)
INFRA=$(  "$MEM_CMD" scene "infrastructure" ...)
# etc.
```

Run `mem scenes` to list all scenes in your store.

## AGENTS.md Integration

Add this to your `AGENTS.md` first-message section so agents know to use the digest and only go deeper when needed:

```markdown
## First-Message Recall

**Archy Digest is pre-loaded** in `## Memory Digest` at the bottom of `MEMORY.md`.
Use it immediately. Run `mem search "<topic>"` only for specific topics not covered by the digest.
```

## Force Refresh

```bash
WORKSPACE=/path/to/workspace bash ~/bin/gen-context-digest.sh
```
