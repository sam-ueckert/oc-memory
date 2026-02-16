# Backup Strategy

oc-memory supports three backup layers, matching the "write it down" philosophy.

## Layer 1: JSON Export (git-tracked)

Full dump of all cells and scenes to a single JSON file.

```bash
oc-memory export
# Creates: ~/.openclaw/workspace/memory-export/memory-export.json
# Creates: ~/.openclaw/workspace/memory-export/scenes-index.md
# Creates: ~/.openclaw/workspace/memory-export/scene-<name>.md (per scene)
```

The export directory is configured via `OC_MEMORY_EXPORT`:
```bash
export OC_MEMORY_EXPORT=~/.openclaw/workspace/memory-export
```

Commit and push after export:
```bash
cd ~/.openclaw/workspace
git add -A && git commit -m "memory export" && git push
```

## Layer 2: Markdown Scene Files

Each scene is exported as a human-readable markdown file:

```
memory-export/
├── scenes-index.md          # Overview of all scenes
├── scene-infrastructure.md  # One file per scene
├── scene-preferences.md
├── scene-projects.md
└── memory-export.json       # Full JSON dump
```

These are designed for human review. You can read them directly to understand what the agent remembers.

## Layer 3: Remote SQLite Copy (optional)

Copy the raw SQLite database to a backup server:

```bash
OC_MEMORY_BACKUP_HOST=my-server oc-memory backup
```

This runs `scp` to copy `memory.db` to `~/backups/memory.db` on the remote host. Requires SSH key access.

## Restoring from Backup

### From JSON

```bash
# Delete or move the current DB
mv ~/.openclaw/memory.db ~/.openclaw/memory.db.bak

# Restore from JSON export
oc-memory restore path/to/memory-export.json
```

Note: Embeddings are not included in JSON exports. Run `oc-memory embed` after restoring to regenerate them (requires Ollama).

### From SQLite copy

```bash
# Just copy the DB file back
scp my-server:~/backups/memory.db ~/.openclaw/memory.db
```

This preserves everything including embeddings.

## Automation

### Via HEARTBEAT.md

Add to your agent's heartbeat checklist:

```markdown
# Periodic: export and commit memory backup
# oc-memory export && cd workspace && git add -A && git commit -m "memory backup" && git push
```

### Via Cron

For automated backup without agent involvement:

```bash
# Every 6 hours, export and git push
0 */6 * * * cd ~/.openclaw/workspace && oc-memory export && git add -A && git commit -m "auto memory backup" && git push 2>/dev/null
```
