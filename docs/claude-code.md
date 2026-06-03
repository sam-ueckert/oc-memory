# Using oc-memory with Claude Code

oc-memory gives Claude Code persistent, searchable memory across sessions. This guide covers setup, the context priming loop, the learning loop, and multi-user usage.

---

## 1. Setup

### Install

```bash
cd ~/repos/oc-memory
bash setup.sh
```

`setup.sh` offers to:
- Install `oc-memory` and the `mem` CLI shortcut
- Patch `~/.claude.json` with the MCP server config
- Install the context-digest and promote-lessons scripts
- Enable multi-user isolation, Google Drive backup, and cron jobs

### Auto-patch `~/.claude.json`

The setup script can add the MCP server automatically:

```json
{
  "mcpServers": {
    "oc-memory": {
      "command": "oc-memory-mcp"
    }
  }
}
```

Or do it manually: `oc-memory mcp-setup` prints the config snippet.

### CLAUDE.md markers

Add these marker blocks to your `CLAUDE.md` so the digest and learned rules are injected automatically:

```markdown
<!-- ARCHY_DIGEST_START -->
<!-- ARCHY_DIGEST_END -->

<!-- LEARNED_RULES_START -->
## Learned Rules
*No rules yet.*
<!-- LEARNED_RULES_END -->
```

Both blocks are replaced in-place by their respective cron scripts.

The digest block uses `ARCHY_DIGEST_*` markers; the learned-rules block uses `LEARNED_RULES_*`. Do not rename them — the scripts look for these exact strings.

---

## 2. Context Priming (context-digest skill)

### How it works

`gen-context-digest.sh` queries oc-memory for high-salience cells grouped by scene, then rewrites the `<!-- ARCHY_DIGEST_START/END -->` block in your `CLAUDE.md`.

Because Claude Code injects `CLAUDE.md` into every session, Claude sees the latest memory digest **before it makes a single tool call** — no extra MCP request needed.

### Install

```bash
# Setup script installs it, or do it manually:
cp skills/context-digest/scripts/gen-context-digest.sh ~/bin/gen-context-digest.sh
chmod +x ~/bin/gen-context-digest.sh
```

### Test it

```bash
WORKSPACE=/path/to/your/workspace bash ~/bin/gen-context-digest.sh
```

Check your `CLAUDE.md` — the digest block should be populated.

### Cron (every 3 hours)

```bash
0 */3 * * * OC_MEMORY_WORKSPACE=/path/to/workspace bash ~/bin/gen-context-digest.sh
```

Or let `scripts/install-crons.sh` add it for you.

---

## 3. Lessons / Learning Loop (promote-lessons skill)

### How it works

1. **Tag a correction**: when Claude makes a mistake or you correct it, store the lesson and tag it:
   ```bash
   MEM_LOCAL=1 mem store lessons lesson 0.9 "Never truncate error messages — always show full output"
   MEM_LOCAL=1 mem tag <id> correction
   ```

2. **promote-lessons.sh** runs weekly, queries all `correction`-tagged cells, synthesizes behavioral rules via the Anthropic API, and injects them into `CLAUDE.md` between the `<!-- LEARNED_RULES_START/END -->` markers.

3. Next session, Claude reads the rules from `CLAUDE.md` and adjusts its behavior.

### Key difference from OpenClaw

For Claude Code (not OpenClaw), use Anthropic API directly:

```bash
API_TOKEN=$ANTHROPIC_API_KEY \
API_URL=https://api.anthropic.com/v1/messages \
WORKSPACE=/path/to/workspace \
bash ~/bin/promote-lessons.sh
```

OpenClaw users use the gateway token and URL instead.

### Install

```bash
# Setup script installs it, or manually:
cp skills/promote-lessons/scripts/promote-lessons.sh ~/bin/promote-lessons.sh
chmod +x ~/bin/promote-lessons.sh
```

### Weekly cron

```bash
0 3 * * 0 API_TOKEN=$ANTHROPIC_API_KEY WORKSPACE=/path/to/workspace bash ~/bin/promote-lessons.sh
```

Or let `scripts/install-crons.sh` configure it interactively.

---

## 4. Manual Recall

In a terminal, search memory and paste results into Claude Code:

```bash
# FTS search (fast, no embedding needed)
mem search "kubernetes deployment"

# Search by tag
mem search-tag "correction"

# Show all cells in a scene
mem scene infrastructure
```

Or use the MCP tools directly from within Claude Code — Claude can call `memory_search` automatically when `~/.claude.json` is configured.

---

## 5. Multi-User (if relevant)

If multiple Claude Code instances share one oc-memory server (e.g., on a shared machine):

```bash
# Set your admin bypass ID (one user who sees all cells)
export OC_MEMORY_ADMIN_USER="your-user-id"
```

Store cells with ownership via MCP tool call:

Or via MCP tool call from Claude Code:
```json
{
  "name": "memory_store",
  "arguments": {
    "content": "Shared deployment decision",
    "scene": "projects",
    "visibility": "shared",
    "owner_id": "your-user-id"
  }
}
```

Pass `caller_id` when searching to scope results:
```json
{
  "name": "memory_search",
  "arguments": {"query": "deployment", "caller_id": "your-user-id"}
}
```

See [docs/multi-user.md](multi-user.md) for full details.

---

## Quick Reference

```bash
# Store a memory (local mode — no server required)
MEM_LOCAL=1 mem store <scene> <type> <salience> "<content>"
MEM_LOCAL=1 mem store projects fact 0.8 "API uses JWT auth with 24h expiry"

# Search
MEM_LOCAL=1 mem search "JWT auth"
MEM_LOCAL=1 mem search-tag "correction"

# Tag for learning loop
MEM_LOCAL=1 mem tag <id> correction

# Stats
MEM_LOCAL=1 mem stats
MEM_LOCAL=1 mem scenes

# Trigger digest update now
WORKSPACE=$(pwd) bash ~/bin/gen-context-digest.sh

# Run promote-lessons now
API_TOKEN=$ANTHROPIC_API_KEY WORKSPACE=$(pwd) bash ~/bin/promote-lessons.sh

# Export for git backup
MEM_LOCAL=1 mem export

# Set MEM_LOCAL globally to avoid prefixing every command
export MEM_LOCAL=1
mem stats
mem search "JWT auth"
```
