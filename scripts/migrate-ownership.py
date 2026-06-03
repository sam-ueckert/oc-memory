#!/usr/bin/env python3
"""migrate-ownership.py — Backfill owner_id and visibility on existing memory cells.

Sets owner_id on all unowned cells. Detects shared/public cells by content
patterns and marks them as visibility='shared'. Sensitive cells stay private.

Usage:
    OC_MEMORY_ADMIN_USER=u0am4blbuuw python3 scripts/migrate-ownership.py
    python3 scripts/migrate-ownership.py --admin-user u0am4blbuuw [--db ~/.oc-memory/memory.db] [--dry-run]

Environment:
    OC_MEMORY_ADMIN_USER  User ID to assign as owner on all baseline cells (required)
    OC_MEMORY_DB          Path to SQLite database (default: ~/.oc-memory/memory.db)
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path


# ── Patterns for shared vs private classification ─────────────────────────────

# Cells matching these patterns are marked as shared (visible to all users)
SHARED_PATTERNS = [
    r"\bproject\b.*\bstatus\b",
    r"\bdeployment\b",
    r"\barchitecture\b",
    r"\binfrastructure\b",
    r"\bpublic\b",
    r"\bteam\b",
    r"\bshared\b",
    r"\bdocumentation\b",
    r"\brelease\b",
    r"\bapi\b.*\bendpoint\b",
    r"\bschedule\b",
    r"\bdeadline\b",
]

# Cells matching these patterns are kept private (override shared patterns)
SENSITIVE_PATTERNS = [
    r"\bpassword\b",
    r"\bsecret\b",
    r"\btoken\b",
    r"\bapi[_\s]?key\b",
    r"\bcredential\b",
    r"\bpersonal\b",
    r"\bprivate\b",
    r"\bconfidential\b",
    r"\bssh\b",
    r"\bcert(ificate)?\b",
    r"\bauth\b.*\bkey\b",
]


def is_sensitive(content: str) -> bool:
    text = content.lower()
    return any(re.search(p, text) for p in SENSITIVE_PATTERNS)


def is_shared(content: str) -> bool:
    text = content.lower()
    return any(re.search(p, text) for p in SHARED_PATTERNS)


def migrate(db_path: str, admin_user: str, dry_run: bool = False):
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Ensure columns exist (idempotent)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mem_cells)").fetchall()}
    if "owner_id" not in cols:
        print("owner_id column not found — run oc-memory once to trigger migration first")
        sys.exit(1)

    # Fetch all unowned cells (owner_id is empty or null)
    rows = conn.execute(
        "SELECT id, content, tags, scene, cell_type, visibility FROM mem_cells WHERE owner_id = '' OR owner_id IS NULL"
    ).fetchall()

    print(f"Found {len(rows)} unowned cells to migrate (dry_run={dry_run})")

    baseline_count = 0
    shared_count = 0
    private_count = 0

    for row in rows:
        cell_id = row["id"]
        content = row["content"] or ""
        tags_raw = row["tags"] or "[]"

        try:
            tags = json.loads(tags_raw)
        except (json.JSONDecodeError, TypeError):
            tags = []

        # Determine visibility
        if is_sensitive(content):
            visibility = "private"
            private_count += 1
        elif is_shared(content) or "shared" in tags:
            visibility = "shared"
            shared_count += 1
        else:
            visibility = "private"
            private_count += 1

        if not dry_run:
            conn.execute(
                "UPDATE mem_cells SET owner_id = ?, visibility = ? WHERE id = ?",
                (admin_user, visibility, cell_id),
            )

        baseline_count += 1
        if baseline_count <= 20 or baseline_count % 100 == 0:
            vis_label = "shared" if visibility == "shared" else "private"
            print(f"  [{cell_id}] {vis_label:7s} — {content[:80]!r}")

    if not dry_run:
        conn.commit()
        print(f"\nMigrated {baseline_count} cells → owner={admin_user!r}")
    else:
        print(f"\n[dry-run] Would migrate {baseline_count} cells → owner={admin_user!r}")

    print(f"  private: {private_count}")
    print(f"  shared:  {shared_count}")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill owner_id and visibility on memory cells")
    parser.add_argument(
        "--admin-user",
        default=os.environ.get("OC_MEMORY_ADMIN_USER", ""),
        help="User ID to assign as owner on all baseline cells (required)",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("OC_MEMORY_DB", str(Path.home() / ".oc-memory" / "memory.db")),
        help="Path to SQLite database (default: ~/.oc-memory/memory.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without modifying the database",
    )
    args = parser.parse_args()

    if not args.admin_user:
        print("Error: --admin-user is required (or set OC_MEMORY_ADMIN_USER env var)")
        sys.exit(1)

    migrate(args.db, args.admin_user, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
