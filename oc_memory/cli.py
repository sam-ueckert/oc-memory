#!/usr/bin/env python3
"""
oc-memory — Self-organizing agent memory system.

Usage:
  oc-memory store <json>              Store pre-extracted cells
  oc-memory store-stdin               Store cells from stdin (JSON)
  oc-memory extract <text>            Extract cells from text using local LLM
  oc-memory extract-file <path>       Extract cells from a file
  oc-memory search <query>            Search memories (vector + FTS fallback)
  oc-memory scenes                    List all scenes
  oc-memory scene <name>              Get scene details
  oc-memory consolidate [scene]       Consolidate scenes with LLM summaries
  oc-memory embed                     Embed all cells missing embeddings
  oc-memory export                    Export markdown + JSON to git repo
  oc-memory backup [--drive]          Full backup (export + optional Google Drive upload)
  oc-memory backup-drive              Upload memory-export.json and memory.db to Google Drive
  oc-memory backup-list               List files in the Google Drive backup folder
  oc-memory restore <json_path>       Restore from JSON export
  oc-memory stats                     Show statistics
  oc-memory tag <id> <tag> [tag...]    Add tags to a cell
  oc-memory search-tag <tag>          Find cells by tag
  oc-memory forget <id>               Delete a cell
  oc-memory decay                     Decay old low-access memories
  oc-memory summarize-day [date]      Summarize today's (or given date's) cells into a digest
  oc-memory digest [date]             Print the digest for a date (default: today)
  oc-memory mcp-serve                 Start MCP server (stdio JSON-RPC)
  oc-memory mcp-setup                 Print MCP config snippets for Claude Code / Cursor / OpenClaw
"""

import json
import os
import sys
from datetime import date as date_type
from pathlib import Path

DB_PATH = os.environ.get("OC_MEMORY_DB", os.path.expanduser("~/.oc-memory/memory.db"))
EXPORT_DIR = os.environ.get(
    "OC_MEMORY_EXPORT", os.path.expanduser("~/.oc-memory/export")
)
OLLAMA_URL = os.environ.get("OLLAMA_URL", None)  # None = use configured backend (ONNX)


def get_db():
    from .db import MemoryDB
    return MemoryDB(DB_PATH)


def get_embedder():
    from .embeddings import EmbeddingClient
    return EmbeddingClient(OLLAMA_URL)


def get_extractor():
    from .extractor import MemoryExtractor
    return MemoryExtractor(OLLAMA_URL)


def get_backup(db):
    from .backup import BackupManager
    return BackupManager(db, EXPORT_DIR)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    db = get_db()

    if cmd == "store":
        data = json.loads(sys.argv[2]) if len(sys.argv) > 2 else json.load(sys.stdin)
        cells = data if isinstance(data, list) else [data]
        embedder = get_embedder()
        use_emb = embedder.is_available()

        for cell in cells:
            emb = None
            if use_emb:
                try:
                    content = cell["content"] if isinstance(cell["content"], str) else json.dumps(cell["content"])
                    emb = embedder.embed(content)
                except Exception as e:
                    print(f"  Warning: embedding failed: {e}", file=sys.stderr)
            row_id = db.insert_cell(cell, embedding=emb)
            print(f"Stored cell {row_id}: [{cell.get('cell_type', 'fact')}] {cell.get('scene', '?')}")

    elif cmd == "store-stdin":
        data = json.load(sys.stdin)
        cells = data if isinstance(data, list) else [data]
        embedder = get_embedder()
        use_emb = embedder.is_available()

        for cell in cells:
            emb = None
            if use_emb:
                try:
                    content = cell["content"] if isinstance(cell["content"], str) else json.dumps(cell["content"])
                    emb = embedder.embed(content)
                except Exception:
                    pass
            row_id = db.insert_cell(cell, embedding=emb)
            print(f"Stored cell {row_id}: [{cell.get('cell_type', 'fact')}] {cell.get('scene', '?')}")

    elif cmd == "extract":
        text = " ".join(sys.argv[2:])
        extractor = get_extractor()
        cells = extractor.extract_cells(text)
        if cells:
            embedder = get_embedder()
            use_emb = embedder.is_available()
            for cell in cells:
                emb = None
                if use_emb:
                    try:
                        emb = embedder.embed(cell["content"])
                    except Exception:
                        pass
                row_id = db.insert_cell(cell, embedding=emb)
                print(f"Extracted cell {row_id}: [{cell.get('cell_type', 'fact')}] {cell.get('scene', '?')} — {cell['content'][:80]}")
        else:
            print("No cells extracted.")

    elif cmd == "extract-file":
        path = sys.argv[2]
        text = Path(path).read_text()
        extractor = get_extractor()
        cells = extractor.extract_cells(text, source=path)
        if cells:
            embedder = get_embedder()
            use_emb = embedder.is_available()
            for cell in cells:
                emb = None
                if use_emb:
                    try:
                        emb = embedder.embed(cell["content"])
                    except Exception:
                        pass
                row_id = db.insert_cell(cell, embedding=emb)
                print(f"Extracted cell {row_id}: [{cell.get('cell_type', 'fact')}] {cell.get('scene', '?')} — {cell['content'][:80]}")
        else:
            print("No cells extracted.")

    elif cmd == "search":
        query = " ".join(sys.argv[2:])
        embedder = get_embedder()

        results = []
        if embedder.is_available():
            try:
                query_emb = embedder.embed(query)
                results = db.search_vector(query_emb)
            except Exception:
                pass

        if not results:
            results = db.search_fts(query)
            if results:
                print("(FTS fallback)\n")

        if results:
            for r in results:
                sim = f" sim:{r['similarity']:.3f}" if "similarity" in r else ""
                tags = r.get("tags", "[]")
                tags_str = f" tags:{tags}" if tags and tags != "[]" else ""
                print(f"[{r['id']}] [{r['cell_type']}] scene:{r['scene']} sal:{r['salience']:.2f}{sim}{tags_str} — {r['content'][:120]}")
        else:
            print("No results found.")

    elif cmd == "scenes":
        for s in db.list_scenes():
            print(f"  {s['scene']} ({s['cell_count']} cells) — {s['summary'][:80]}")

    elif cmd == "scene":
        name = " ".join(sys.argv[2:])
        info, cells = db.get_scene(name)
        if info:
            print(f"Scene: {name}")
            print(f"Summary: {info['summary']}")
            for c in cells:
                print(f"  [{c['id']}] [{c['cell_type']}] sal:{c['salience']:.2f} — {c['content'][:120]}")
        else:
            print(f"Scene '{name}' not found.")

    elif cmd == "consolidate":
        scene_name = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else None
        extractor = get_extractor()
        embedder = get_embedder()
        use_llm = extractor.is_available()
        use_emb = embedder.is_available()

        if scene_name:
            scenes = [scene_name]
        else:
            scenes = [
                r[0]
                for r in db.db.execute("SELECT DISTINCT scene FROM mem_cells").fetchall()
            ]

        for scene in scenes:
            _, cells = db.get_scene(scene)
            if not cells:
                continue

            if use_llm:
                summary = extractor.generate_summary(cells)
            else:
                top = sorted(cells, key=lambda c: c["salience"], reverse=True)[:10]
                summary = "; ".join(c["content"][:100] for c in top)[:300]

            summary_emb = None
            if use_emb:
                try:
                    summary_emb = embedder.embed(summary)
                except Exception:
                    pass

            db.upsert_scene(scene, summary, summary_emb)
            print(f"Consolidated: {scene} ({len(cells)} cells)")

    elif cmd == "embed":
        embedder = get_embedder()
        if not embedder.is_available():
            print("Ollama not available.")
            sys.exit(1)

        cells = db.cells_without_embeddings()
        if not cells:
            print("All cells already embedded.")
            return

        print(f"Embedding {len(cells)} cells...")
        for cell in cells:
            try:
                emb = embedder.embed(cell["content"])
                db.update_embedding(cell["id"], emb)
                print(f"  Embedded cell {cell['id']}")
            except Exception as e:
                print(f"  Failed cell {cell['id']}: {e}", file=sys.stderr)

    elif cmd == "export":
        backup = get_backup(db)
        n_scenes = backup.export_markdown()
        json_path = backup.export_json()
        print(f"Exported {n_scenes} scenes + JSON to {EXPORT_DIR}")

    elif cmd == "backup":
        drive = "--drive" in sys.argv
        backup = get_backup(db)
        n_scenes = backup.export_markdown()
        json_path = backup.export_json()
        print(f"Exported {n_scenes} scenes + JSON to {EXPORT_DIR}")
        ok = backup.backup_sqlite()
        print(f"SQLite backup: {'OK' if ok else 'FAILED'}")
        if drive:
            try:
                results = backup.backup_drive()
                for r in results:
                    size = r.get("size", "?")
                    print(f"Drive upload: {r['name']} ({size} bytes) — id:{r['id']}")
            except Exception as e:
                print(f"Drive backup FAILED: {e}", file=sys.stderr)

    elif cmd == "backup-drive":
        backup = get_backup(db)
        try:
            # Ensure exports exist
            backup.export_json()
            results = backup.backup_drive()
            if results:
                for r in results:
                    size = r.get("size", "?")
                    print(f"Uploaded: {r['name']} ({size} bytes) — id:{r['id']}")
            else:
                print("No files uploaded (nothing found to backup).")
        except Exception as e:
            print(f"Drive backup FAILED: {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "backup-list":
        from .drive_backup import DriveBackupManager
        try:
            mgr = DriveBackupManager()
            files = mgr.list_backups()
            if files:
                print(f"Files in '{mgr.folder_name}':")
                for f in files:
                    size_kb = int(f.get("size", 0)) // 1024
                    print(f"  {f['name']:30s}  {size_kb:6d} KB  modified:{f.get('modifiedTime', '?')}")
            else:
                print("No backups found in Drive.")
        except Exception as e:
            print(f"Failed to list Drive backups: {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "restore":
        path = sys.argv[2]
        backup = get_backup(db)
        count = backup.restore_from_json(path)
        print(f"Restored {count} cells from {path}")

    elif cmd == "stats":
        print(json.dumps(db.stats(), indent=2))

    elif cmd == "tag":
        cell_id = int(sys.argv[2])
        tags = sys.argv[3:]
        if not tags:
            print("Usage: oc-memory tag <id> <tag> [tag...]")
            sys.exit(1)
        db.tag_cell(cell_id, tags)
        print(f"Tagged cell {cell_id} with: {', '.join(tags)}")

    elif cmd == "search-tag":
        tag = sys.argv[2] if len(sys.argv) > 2 else ""
        if not tag:
            print("Usage: oc-memory search-tag <tag>")
            sys.exit(1)
        results = db.search_by_tag(tag)
        if results:
            for r in results:
                tags_str = r.get("tags", "[]")
                print(f"[{r['id']}] [{r['cell_type']}] scene:{r['scene']} sal:{r['salience']:.2f} tags:{tags_str} — {r['content'][:120]}")
        else:
            print(f"No cells tagged '{tag}'.")

    elif cmd == "forget":
        cell_id = int(sys.argv[2])
        db.delete_cell(cell_id)
        print(f"Deleted cell {cell_id}")

    elif cmd == "decay":
        affected = db.decay()
        print(f"Decayed {affected} old memories")

    elif cmd == "summarize-day":
        target_date = sys.argv[2] if len(sys.argv) > 2 else date_type.today().isoformat()
        scene_prefix = f"conv-{target_date}"
        # Gather all cells for the day's conversation scene
        _, cells = db.get_scene(scene_prefix)
        if not cells:
            # Also try raw date-based scenes
            rows = db.db.execute(
                "SELECT id, scene, cell_type, salience, content, source, tags, created_at "
                "FROM mem_cells WHERE scene LIKE ? OR date(created_at) = ? ORDER BY created_at",
                (f"%{target_date}%", target_date),
            ).fetchall()
            cells = [dict(r) for r in rows]
        if not cells:
            print(f"No cells found for {target_date}")
            sys.exit(0)

        extractor = get_extractor()
        if extractor.is_available():
            digest = extractor.generate_summary(cells)
        else:
            # FTS-only fallback: top cells by salience
            top = sorted(cells, key=lambda c: c.get("salience", 0.5), reverse=True)[:10]
            digest = "; ".join(c["content"][:100] for c in top)[:500]

        # Store digest as a special cell
        digest_cell = {
            "scene": f"digest-{target_date}",
            "cell_type": "plan",
            "salience": 0.7,
            "content": digest,
            "source": "summarize-day",
            "tags": ["digest", "daily-summary"],
        }
        row_id = db.insert_cell(digest_cell)
        print(f"Digest stored as cell {row_id} (scene: digest-{target_date})")
        print(f"Summary: {digest}")

    elif cmd == "digest":
        target_date = sys.argv[2] if len(sys.argv) > 2 else date_type.today().isoformat()
        scene_name = f"digest-{target_date}"
        _, cells = db.get_scene(scene_name)
        if not cells:
            # Fallback: search by tag
            cells = db.search_by_tag("digest")
            cells = [c for c in cells if target_date in c.get("scene", "")]
        if cells:
            for c in cells:
                print(f"[{c['id']}] {c['content']}")
        else:
            print(f"No digest found for {target_date}. Run: oc-memory summarize-day {target_date}")

    elif cmd == "mcp-serve":
        from .mcp_server import main as mcp_main
        mcp_main()

    elif cmd == "mcp-setup":
        from .mcp_config import print_setup_instructions
        print_setup_instructions()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
