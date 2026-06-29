# Multi-User Isolation

oc-memory supports per-user ownership on memory cells via `owner_id` and `visibility` columns. This is useful when multiple users or agents share the same memory server.

## What it does

Each cell gains two new fields:

| Field | Type | Description |
|---|---|---|
| `owner_id` | `TEXT` | User/agent identifier. Empty string = unowned (admin-only). |
| `visibility` | `TEXT` | `'private'` (owner only) or `'shared'` (all users). |

When a `caller_id` is provided to search/tag/delete operations:
- Cells owned by the caller are always visible to them
- Cells with `visibility='shared'` are visible to everyone
- Cells owned by another user are hidden (filtered out)
- Deleting or tagging another user's cell raises `PermissionError`

The **admin user** bypasses all ownership filters and can see/edit all cells.

## How to enable

Set the `OC_MEMORY_ADMIN_USER` environment variable to your admin user ID:

```bash
# In ~/.bashrc or ~/.zshrc
export OC_MEMORY_ADMIN_USER="your-user-id"

# Example for Slack user IDs:
export OC_MEMORY_ADMIN_USER="u0am4blbuuw"
```

The migration runs automatically on startup — no manual schema changes needed.

## Cell visibility

### Private (default)
```json
{"visibility": "private", "owner_id": "u0am4blbuuw"}
```
Only the owning user (and admin) can see this cell.

### Shared
```json
{"visibility": "shared", "owner_id": "u0am4blbuuw"}
```
All authenticated callers can see this cell. Useful for shared project facts, team decisions, etc.

## How caller_id works in MCP tools

Pass `caller_id` in MCP tool calls to scope results to the caller:

```json
{
  "name": "memory_search",
  "arguments": {
    "query": "project status",
    "caller_id": "u0am4blbuuw"
  }
}
```

Omit `caller_id` (or leave it empty) for admin-level access — all cells are returned.

`caller_id` is supported in: `memory_search`, `memory_search_tag`, `memory_forget`, `memory_tag`. (`memory_store` instead takes `owner_id` and `visibility` — see below.)

## Storing with ownership

```json
{
  "name": "memory_store",
  "arguments": {
    "content": "My personal API key rotation reminder",
    "scene": "ops",
    "cell_type": "task",
    "owner_id": "u0am4blbuuw",
    "visibility": "private"
  }
}
```

```json
{
  "name": "memory_store",
  "arguments": {
    "content": "Project Alpha launches 2026-Q3",
    "scene": "projects",
    "cell_type": "fact",
    "owner_id": "u0am4blbuuw",
    "visibility": "shared"
  }
}
```

## Migration: backfill existing cells

If you have an existing database, backfill ownership with the migration script:

```bash
# Dry run first to see what would change
python3 scripts/migrate-ownership.py --admin-user YOUR_USER_ID --dry-run

# Apply the migration
python3 scripts/migrate-ownership.py --admin-user YOUR_USER_ID

# With explicit DB path
OC_MEMORY_DB=~/.oc-memory/memory.db \
python3 scripts/migrate-ownership.py --admin-user YOUR_USER_ID
```

The script:
- Sets `owner_id = YOUR_USER_ID` on all unowned cells
- Marks cells with project/team/deployment content as `shared`
- Keeps sensitive cells (passwords, tokens, credentials) as `private`

## Claude Code usage

Set `OC_MEMORY_ADMIN_USER` in your environment, then pass `caller_id` from tool calls:

```python
# In your Claude Code CLAUDE.md or AGENTS.md, document:
# - Store shared project facts with visibility="shared"
# - Store personal context with visibility="private"
# - Always pass caller_id when searching (your user ID)
```

Or operate in admin mode (no `caller_id`) to see all cells regardless of ownership.

## Python API

```python
from oc_memory.db import MemoryDB

db = MemoryDB("~/.oc-memory/memory.db")

# Store with ownership
db.insert_cell(
    {"scene": "projects", "cell_type": "fact", "content": "Project launch: Q3"},
    owner_id="user-a",
    visibility="shared",
)

# Search as user-a (sees own + shared cells)
results = db.search_fts("launch", caller_id="user-a")

# Search as admin (sees all cells)
results = db.search_fts("launch")  # no caller_id = admin access

# Tag a cell (enforces ownership)
try:
    db.tag_cell(42, ["important"], caller_id="user-b")
except PermissionError as e:
    print(f"Access denied: {e}")

# Delete a cell (enforces ownership)
db.delete_cell(42, caller_id="user-a")
```
