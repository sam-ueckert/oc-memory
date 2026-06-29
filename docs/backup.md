# Backup Strategy

oc-memory supports three backup layers, matching the "write it down" philosophy.

## Layer 1: JSON Export (git-tracked)

Full dump of all cells and scenes to a single JSON file.

```bash
oc-memory export
# Creates: ~/.oc-memory/export/memory-export.json
# Creates: ~/.oc-memory/export/scenes-index.md
# Creates: ~/.oc-memory/export/scene-<name>.md (per scene)
```

The export directory defaults to `~/.oc-memory/export` and is configured via `OC_MEMORY_EXPORT`. Point it at a git-tracked directory if you want to commit exports:
```bash
export OC_MEMORY_EXPORT=~/my-memory-repo/export
```

Commit and push after export:
```bash
cd ~/my-memory-repo
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

## Layer 3: Remote SQLite Copy (optional, Python API only)

Copy the raw SQLite database to a backup server via `scp`. This is **not** wired
into the `oc-memory backup` CLI (that command only runs the JSON/markdown export);
use the Python API and pass `remote_backup_host`:

```python
from oc_memory.backup import BackupManager
from oc_memory.db import MemoryDB

backup = BackupManager(MemoryDB("~/.oc-memory/memory.db"),
                       export_dir="~/.oc-memory/export",
                       remote_backup_host="my-server")
backup.backup_sqlite()   # scp memory.db → my-server:~/backups/memory.db
```

Requires SSH key access. For most setups, prefer the Google Drive backup (Layer 4).

## Restoring from Backup

### From JSON

```bash
# Delete or move the current DB
mv ~/.oc-memory/memory.db ~/.oc-memory/memory.db.bak

# Restore from JSON export
oc-memory restore path/to/memory-export.json
```

Note: Embeddings are not included in JSON exports. Run `oc-memory embed` after restoring to regenerate them (uses the built-in ONNX embedder, or Ollama if `OLLAMA_URL` is set).

### From SQLite copy

```bash
# Just copy the DB file back
scp my-server:~/backups/memory.db ~/.oc-memory/memory.db
```

This preserves everything including embeddings.

## Layer 4: Google Drive Backup (optional)

Upload `memory-export.json` and `memory.db` to Google Drive for off-machine backup.

### Install

```bash
pip install oc-memory[drive]
# or
uv pip install oc-memory[drive]
```

### Set up OAuth2 credentials

1. Go to [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
2. Click **Create Credentials → OAuth 2.0 Client ID**
3. Application type: **Desktop application**
4. Download the JSON file and save it to:
   ```
   ~/.oc-memory/drive-client-creds.json
   ```
5. Override the path via env var: `OC_MEMORY_DRIVE_CLIENT_CREDS=/path/to/creds.json`

On first run, a browser window opens for authorization. The token is saved to
`~/.oc-memory/drive-token.json` (override: `OC_MEMORY_DRIVE_TOKEN`).

### Usage

```bash
# Upload memory-export.json + memory.db to Drive
oc-memory backup-drive

# List files in the backup folder
oc-memory backup-list

# Full backup with Drive upload
oc-memory backup --drive
```

### Programmatic usage

```python
from oc_memory.backup import BackupManager
from oc_memory.db import MemoryDB

db = MemoryDB("~/.oc-memory/memory.db")
backup = BackupManager(db, export_dir="~/.oc-memory/export")

# Export JSON first, then upload
backup.export_json()
results = backup.backup_drive()
for r in results:
    print(f"Uploaded: {r['name']} ({r.get('size', '?')} bytes)")
```

### Cron example

```bash
# Daily at 2am — export + Drive upload
0 2 * * * cd ~/.oc-memory && oc-memory export && oc-memory backup-drive >> /tmp/oc-drive-backup.log 2>&1
```

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
# Every 6 hours, export and git push (set OC_MEMORY_EXPORT to a git-tracked dir)
0 */6 * * * OC_MEMORY_EXPORT=~/my-memory-repo/export oc-memory export && cd ~/my-memory-repo && git add -A && git commit -m "auto memory backup" && git push 2>/dev/null
```
