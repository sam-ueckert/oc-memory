"""SQLite database layer with FTS5 and vector storage."""

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np


class MemoryDB:
    """Core memory database — SQLite + FTS5 + optional vector embeddings."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS mem_cells (
                id INTEGER PRIMARY KEY,
                scene TEXT NOT NULL,
                cell_type TEXT NOT NULL,
                salience REAL DEFAULT 0.5,
                content TEXT NOT NULL,
                source TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                embedding BLOB,
                access_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mem_scenes (
                scene TEXT PRIMARY KEY,
                summary TEXT DEFAULT '',
                summary_embedding BLOB,
                cell_count INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cells_scene ON mem_cells(scene);
            CREATE INDEX IF NOT EXISTS idx_cells_salience ON mem_cells(salience DESC);
            CREATE INDEX IF NOT EXISTS idx_cells_type ON mem_cells(cell_type);
        """)

        # Migration: add tags column if missing
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(mem_cells)").fetchall()}
        if "tags" not in cols:
            self.db.execute("ALTER TABLE mem_cells ADD COLUMN tags TEXT DEFAULT '[]'")

        # FTS5 virtual table — rebuild if schema changed (tags added)
        try:
            self.db.execute("SELECT * FROM mem_fts LIMIT 0")
            # Check if FTS includes tags column
            fts_cols = [r[1] for r in self.db.execute("PRAGMA table_info(mem_fts)").fetchall()]
            if "tags" not in fts_cols:
                self.db.execute("DROP TABLE mem_fts")
                raise sqlite3.OperationalError("rebuild")
        except sqlite3.OperationalError:
            self.db.execute("""
                CREATE VIRTUAL TABLE mem_fts
                USING fts5(content, scene, cell_type, tags)
            """)
            # Backfill from existing data
            for row in self.db.execute("SELECT id, content, scene, cell_type, tags FROM mem_cells"):
                self.db.execute(
                    "INSERT INTO mem_fts(rowid, content, scene, cell_type, tags) VALUES (?, ?, ?, ?, ?)",
                    (row["id"], row["content"], row["scene"], row["cell_type"], row["tags"] or "[]"),
                )

        self.db.commit()

    def insert_cell(self, cell: dict, embedding: Optional[np.ndarray] = None) -> int:
        """Insert a memory cell. Returns the row ID."""
        now = datetime.utcnow().isoformat()
        content = cell["content"] if isinstance(cell["content"], str) else json.dumps(cell["content"])
        emb_blob = embedding.tobytes() if embedding is not None else None
        tags = cell.get("tags", [])
        tags_json = json.dumps(tags) if isinstance(tags, list) else tags

        cursor = self.db.execute(
            """INSERT INTO mem_cells
               (scene, cell_type, salience, content, source, tags, embedding, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cell["scene"],
                cell.get("cell_type", "fact"),
                cell.get("salience", 0.5),
                content,
                cell.get("source", ""),
                tags_json,
                emb_blob,
                now,
                now,
            ),
        )
        row_id = cursor.lastrowid
        self.db.execute(
            "INSERT INTO mem_fts(rowid, content, scene, cell_type, tags) VALUES (?, ?, ?, ?, ?)",
            (row_id, content, cell["scene"], cell.get("cell_type", "fact"), tags_json),
        )
        self.db.commit()
        return row_id

    def tag_cell(self, cell_id: int, tags: list[str]):
        """Add tags to a cell (merges with existing, deduplicates)."""
        row = self.db.execute("SELECT tags FROM mem_cells WHERE id = ?", (cell_id,)).fetchone()
        if not row:
            return
        existing = json.loads(row["tags"] or "[]")
        merged = sorted(set(existing + [t.lower().strip() for t in tags]))
        tags_json = json.dumps(merged)
        now = datetime.utcnow().isoformat()
        self.db.execute(
            "UPDATE mem_cells SET tags = ?, updated_at = ? WHERE id = ?",
            (tags_json, now, cell_id),
        )
        # Update FTS
        content_row = self.db.execute(
            "SELECT content, scene, cell_type FROM mem_cells WHERE id = ?", (cell_id,)
        ).fetchone()
        self.db.execute("DELETE FROM mem_fts WHERE rowid = ?", (cell_id,))
        self.db.execute(
            "INSERT INTO mem_fts(rowid, content, scene, cell_type, tags) VALUES (?, ?, ?, ?, ?)",
            (cell_id, content_row["content"], content_row["scene"], content_row["cell_type"], tags_json),
        )
        self.db.commit()

    def search_by_tag(self, tag: str, limit: int = 20) -> list[dict]:
        """Find cells matching a tag."""
        pattern = f'%"{tag.lower().strip()}"%'
        rows = self.db.execute(
            """SELECT id, scene, cell_type, salience, content, source, tags, created_at
               FROM mem_cells WHERE tags LIKE ?
               ORDER BY salience DESC LIMIT ?""",
            (pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_embedding(self, cell_id: int, embedding: np.ndarray):
        """Update the embedding for an existing cell."""
        self.db.execute(
            "UPDATE mem_cells SET embedding = ? WHERE id = ?",
            (embedding.tobytes(), cell_id),
        )
        self.db.commit()

    def search_fts(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search. Updates access_count on returned cells."""
        tokens = re.findall(r"[a-zA-Z0-9]+", query)
        if not tokens:
            return []

        fts_query = " OR ".join(tokens)
        rows = self.db.execute(
            """SELECT m.id, m.scene, m.cell_type, m.salience, m.content, m.source, m.tags, m.created_at
               FROM mem_fts f
               JOIN mem_cells m ON f.rowid = m.id
               WHERE mem_fts MATCH ?
               ORDER BY m.salience DESC
               LIMIT ?""",
            (fts_query, limit),
        ).fetchall()

        for row in rows:
            self.db.execute(
                "UPDATE mem_cells SET access_count = access_count + 1 WHERE id = ?",
                (row["id"],),
            )
        self.db.commit()
        return [dict(r) for r in rows]

    def search_vector(self, query_embedding: np.ndarray, limit: int = 10) -> list[dict]:
        """Vector similarity search using cosine similarity.

        Score = 0.7 * cosine_similarity + 0.3 * salience.
        """
        rows = self.db.execute(
            "SELECT id, scene, cell_type, salience, content, source, embedding, created_at "
            "FROM mem_cells WHERE embedding IS NOT NULL"
        ).fetchall()

        if not rows:
            return []

        scored = []
        for row in rows:
            emb = np.frombuffer(row["embedding"], dtype=np.float32)
            sim = float(
                np.dot(query_embedding, emb)
                / (np.linalg.norm(query_embedding) * np.linalg.norm(emb) + 1e-10)
            )
            score = 0.7 * sim + 0.3 * row["salience"]
            scored.append((score, sim, dict(row)))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, sim, row_dict in scored[:limit]:
            row_dict.pop("embedding", None)
            row_dict["similarity"] = round(sim, 4)
            row_dict["score"] = round(score, 4)
            self.db.execute(
                "UPDATE mem_cells SET access_count = access_count + 1 WHERE id = ?",
                (row_dict["id"],),
            )
            results.append(row_dict)

        self.db.commit()
        return results

    def get_scene(self, scene: str) -> tuple[Optional[dict], list[dict]]:
        """Get scene metadata and its cells."""
        row = self.db.execute("SELECT * FROM mem_scenes WHERE scene = ?", (scene,)).fetchone()
        cells = self.db.execute(
            "SELECT id, scene, cell_type, salience, content, source, tags, access_count, created_at "
            "FROM mem_cells WHERE scene = ? ORDER BY salience DESC",
            (scene,),
        ).fetchall()
        return (dict(row) if row else None), [dict(c) for c in cells]

    def list_scenes(self) -> list[dict]:
        """List all scenes."""
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT scene, summary, cell_count, updated_at FROM mem_scenes ORDER BY updated_at DESC"
            ).fetchall()
        ]

    def upsert_scene(self, scene: str, summary: str, summary_embedding: Optional[np.ndarray] = None):
        """Create or update a scene summary."""
        count = self.db.execute(
            "SELECT COUNT(*) FROM mem_cells WHERE scene = ?", (scene,)
        ).fetchone()[0]
        emb_blob = summary_embedding.tobytes() if summary_embedding is not None else None
        self.db.execute(
            """INSERT INTO mem_scenes (scene, summary, summary_embedding, cell_count, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(scene) DO UPDATE SET
                   summary = excluded.summary,
                   summary_embedding = excluded.summary_embedding,
                   cell_count = excluded.cell_count,
                   updated_at = excluded.updated_at""",
            (scene, summary, emb_blob, count, datetime.utcnow().isoformat()),
        )
        self.db.commit()

    def delete_cell(self, cell_id: int):
        """Delete a memory cell."""
        self.db.execute("DELETE FROM mem_fts WHERE rowid = ?", (cell_id,))
        self.db.execute("DELETE FROM mem_cells WHERE id = ?", (cell_id,))
        self.db.commit()

    def decay(self, days_old: int = 30, decay_factor: float = 0.9) -> int:
        """Decay salience of old, rarely-accessed memories.

        Only affects cells older than `days_old` with access_count < 3
        and salience > 0.1 (floor).
        """
        cutoff = (datetime.utcnow() - timedelta(days=days_old)).isoformat()
        self.db.execute(
            """UPDATE mem_cells
               SET salience = salience * ?, updated_at = ?
               WHERE created_at < ? AND access_count < 3 AND salience > 0.1""",
            (decay_factor, datetime.utcnow().isoformat(), cutoff),
        )
        affected = self.db.total_changes
        self.db.commit()
        return affected

    def all_cells(self) -> list[dict]:
        """Get all cells (for export)."""
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT id, scene, cell_type, salience, content, source, tags, access_count, "
                "created_at, updated_at FROM mem_cells ORDER BY id"
            ).fetchall()
        ]

    def cells_without_embeddings(self) -> list[dict]:
        """Get cells that haven't been embedded yet."""
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT id, content FROM mem_cells WHERE embedding IS NULL"
            ).fetchall()
        ]

    def stats(self) -> dict:
        """Memory statistics."""
        total = self.db.execute("SELECT COUNT(*) FROM mem_cells").fetchone()[0]
        scenes = self.db.execute("SELECT COUNT(*) FROM mem_scenes").fetchone()[0]
        embedded = self.db.execute(
            "SELECT COUNT(*) FROM mem_cells WHERE embedding IS NOT NULL"
        ).fetchone()[0]
        types = self.db.execute(
            "SELECT cell_type, COUNT(*) as c FROM mem_cells GROUP BY cell_type ORDER BY c DESC"
        ).fetchall()
        top_scenes = self.db.execute(
            "SELECT scene, COUNT(*) as c FROM mem_cells GROUP BY scene ORDER BY c DESC LIMIT 10"
        ).fetchall()
        return {
            "total_cells": total,
            "embedded_cells": embedded,
            "total_scenes": scenes,
            "by_type": {r[0]: r[1] for r in types},
            "top_scenes": {r[0]: r[1] for r in top_scenes},
        }
