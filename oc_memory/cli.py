#!/usr/bin/env python3
"""
oc-memory — Self-organizing agent memory for OpenClaw.

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
  oc-memory export                    Export markdown + JSON for git
  oc-memory backup                    Full backup (export + optional sqlite copy)
  oc-memory restore <json_path>       Restore from JSON export
  oc-memory stats                     Show statistics
  oc-memory forget <id>               Delete a cell
  oc-memory decay                     Decay old low-access memories

Environment variables:
  OC_MEMORY_DB         Path to SQLite database (default: ~/.openclaw/memory.db)
  OC_MEMORY_EXPORT     Path to export directory (default: ~/.openclaw/workspace/memory-export)
  OLLAMA_URL           Ollama API URL (default: http://localhost:11434)
  OC_MEMORY_BACKUP_HOST  SSH host for SQLite backup (optional)
"""

import json
import os
import sys
from pathlib import Path

DB_PATH = os.environ.get("OC_MEMORY_DB", os.path.expanduser("~/.openclaw/memory.db"))
EXPORT_DIR = os.environ.get("OC_MEMORY_EXPORT", os.path.expanduser("~/.openclaw/workspace/memory-export"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
BACKUP_HOST = os.environ.get("OC_MEMORY_BACKUP_HOST", None)


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
    return BackupManager(db, EXPORT_DIR, remote_backup_host=BACKUP_HOST)


def _store_cells(db, cells: list[dict]):
    """Store cells with optional embedding."""
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


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    db = get_db()

    if cmd == "store":
        data = json.loads(sys.argv[2]) if len(sys.argv) > 2 else json.load(sys.stdin)
        cells = data if isinstance(data, list) else [data]
        _store_cells(db, cells)

    elif cmd == "store-stdin":
        data = json.load(sys.stdin)
        cells = data if isinstance(data, list) else [data]
        _store_cells(db, cells)

    elif cmd == "extract":
        text = " ".join(sys.argv[2:])
        extractor = get_extractor()
        cells = extractor.extract_cells(text)
        if cells:
            _store_cells(db, cells)
        else:
            print("No cells extracted.")

    elif cmd == "extract-file":
        path = sys.argv[2]
        text = Path(path).read_text()
        extractor = get_extractor()
        cells = extractor.extract_cells(text, source=path)
        if cells:
            _store_cells(db, cells)
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
                print(
                    f"[{r['id']}] [{r['cell_type']}] scene:{r['scene']} "
                    f"sal:{r['salience']:.2f}{sim} — {r['content'][:120]}"
                )
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
                print(
                    f"  [{c['id']}] [{c['cell_type']}] sal:{c['salience']:.2f} — {c['content'][:120]}"
                )
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
                r[0] for r in db.db.execute("SELECT DISTINCT scene FROM mem_cells").fetchall()
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
            print("Ollama not available at", OLLAMA_URL)
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
        backup.export_json()
        print(f"Exported {n_scenes} scenes + JSON to {EXPORT_DIR}")

    elif cmd == "backup":
        backup = get_backup(db)
        n_scenes = backup.export_markdown()
        backup.export_json()
        print(f"Exported {n_scenes} scenes + JSON to {EXPORT_DIR}")
        if BACKUP_HOST:
            ok = backup.backup_sqlite()
            print(f"SQLite backup to {BACKUP_HOST}: {'OK' if ok else 'FAILED'}")
        else:
            print("No OC_MEMORY_BACKUP_HOST set — skipping remote SQLite backup.")

    elif cmd == "restore":
        path = sys.argv[2]
        backup = get_backup(db)
        count = backup.restore_from_json(path)
        print(f"Restored {count} cells from {path}")

    elif cmd == "stats":
        print(json.dumps(db.stats(), indent=2))

    elif cmd == "forget":
        cell_id = int(sys.argv[2])
        db.delete_cell(cell_id)
        print(f"Deleted cell {cell_id}")

    elif cmd == "decay":
        affected = db.decay()
        print(f"Decayed {affected} old memories")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
