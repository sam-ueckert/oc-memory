"""SQLite database layer with FTS5 and vector storage."""

import hashlib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# Admin user who bypasses all ownership filters (set OC_MEMORY_ADMIN_USER to your user ID)
ADMIN_USER_ID = os.environ.get("OC_MEMORY_ADMIN_USER", "")


def _safe_embedding(raw) -> Optional[np.ndarray]:
    """Parse an embedding from DB — handles both proper blobs and legacy JSON strings."""
    if raw is None:
        return None
    if isinstance(raw, bytes) and len(raw) > 0:
        return np.frombuffer(raw, dtype=np.float32)
    if isinstance(raw, str):
        try:
            arr = np.array(json.loads(raw), dtype=np.float32)
            log.warning("Embedding stored as JSON string (legacy) — should be re-embedded")
            return arr
        except (json.JSONDecodeError, ValueError):
            log.error("Unparseable embedding string in DB")
            return None
    log.error(f"Unexpected embedding type: {type(raw)}")
    return None


# Type-based TTL in days (None = permanent)
CELL_TYPE_TTL: dict[str, Optional[int]] = {
    "exchange": 7,
    "task": 30,
    "plan": 90,
    "risk": 60,
    "fact": None,
    "decision": None,
    "preference": None,
    "lesson": None,
    "session_summary": 14,  # reasonable default for summaries
}

# Types that are permanent (no TTL, no decay below floor)
PERMANENT_TYPES = {"fact", "decision", "preference", "lesson"}

# Type weights for hybrid scoring
TYPE_WEIGHTS: dict[str, float] = {
    "decision": 2.0,
    "lesson": 2.0,
    "session_summary": 0.5,
    "exchange": 0.5,
}


class MemoryDB:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        self._init_schema()
        self._migrate_schema()
        self._migrate_add_ownership()

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

            CREATE TABLE IF NOT EXISTS mem_edges (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                edge_type TEXT NOT NULL DEFAULT 'related',
                weight REAL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES mem_cells(id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES mem_cells(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_cells_scene ON mem_cells(scene);
            CREATE INDEX IF NOT EXISTS idx_cells_salience ON mem_cells(salience DESC);
            CREATE INDEX IF NOT EXISTS idx_cells_type ON mem_cells(cell_type);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON mem_edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON mem_edges(target_id);
        """)

        # FTS5 table — rebuild if schema changed (tags added)
        try:
            self.db.execute("SELECT * FROM mem_fts LIMIT 0")
            fts_cols = [r[1] for r in self.db.execute("PRAGMA table_info(mem_fts)").fetchall()]
            if "tags" not in fts_cols:
                self.db.execute("DROP TABLE mem_fts")
                raise sqlite3.OperationalError("rebuild")
        except sqlite3.OperationalError:
            self.db.execute("""
                CREATE VIRTUAL TABLE mem_fts
                USING fts5(content, scene, cell_type, tags)
            """)
            for row in self.db.execute("SELECT id, content, scene, cell_type, tags FROM mem_cells"):
                self.db.execute(
                    "INSERT INTO mem_fts(rowid, content, scene, cell_type, tags) VALUES (?, ?, ?, ?, ?)",
                    (row["id"], row["content"], row["scene"], row["cell_type"], row["tags"] or "[]"),
                )

        self.db.commit()

    def _migrate_schema(self):
        """Add new columns/tables if they don't exist (safe migrations)."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(mem_cells)").fetchall()}

        # Add tags column if missing
        if "tags" not in cols:
            self.db.execute("ALTER TABLE mem_cells ADD COLUMN tags TEXT DEFAULT '[]'")
            cols.add("tags")

        # Add content_hash column if missing
        if "content_hash" not in cols:
            self.db.execute("ALTER TABLE mem_cells ADD COLUMN content_hash TEXT")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_cells_hash ON mem_cells(content_hash)")
            # Backfill hashes for existing rows
            rows = self.db.execute("SELECT id, content FROM mem_cells WHERE content_hash IS NULL").fetchall()
            for row in rows:
                h = hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
                self.db.execute("UPDATE mem_cells SET content_hash = ? WHERE id = ?", (h, row["id"]))

        # Ensure mem_edges exists (may have been created before FK support)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS mem_edges (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                edge_type TEXT NOT NULL DEFAULT 'related',
                weight REAL DEFAULT 1.0,
                created_at TEXT NOT NULL
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON mem_edges(source_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON mem_edges(target_id)")

        # Stats tracking table
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS mem_stats (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            )
        """)
        for key in ("dedup_blocked", "ttl_expirations"):
            self.db.execute(
                "INSERT OR IGNORE INTO mem_stats (key, value) VALUES (?, 0)", (key,)
            )

        self.db.commit()

    def _migrate_add_ownership(self):
        """Add owner_id and visibility columns for multi-user isolation."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(mem_cells)").fetchall()}

        if "owner_id" not in cols:
            self.db.execute(
                "ALTER TABLE mem_cells ADD COLUMN owner_id TEXT DEFAULT ''"
            )
            self.db.execute(
                "UPDATE mem_cells SET owner_id = '' WHERE owner_id IS NULL"
            )

        if "visibility" not in cols:
            self.db.execute(
                "ALTER TABLE mem_cells ADD COLUMN visibility TEXT DEFAULT 'private'"
            )
            self.db.execute(
                "UPDATE mem_cells SET visibility = 'private' WHERE visibility IS NULL"
            )

        self.db.execute("CREATE INDEX IF NOT EXISTS idx_owner ON mem_cells(owner_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_visibility ON mem_cells(visibility)")
        self.db.commit()

    # -------------------------------------------------------------------------
    # Core CRUD
    # -------------------------------------------------------------------------

    def insert_cell(
        self,
        cell: dict,
        embedding: Optional[np.ndarray] = None,
        dedup: bool = True,
        owner_id: str = "",
        visibility: str = "private",
    ) -> int:
        """Insert a memory cell. Returns row id. Skips on duplicate if dedup=True."""
        now = datetime.utcnow().isoformat()
        content = cell["content"] if isinstance(cell["content"], str) else json.dumps(cell["content"])
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        emb_blob = embedding.tobytes() if embedding is not None else None
        tags = cell.get("tags", [])
        tags_json = json.dumps(tags) if isinstance(tags, list) else tags

        # Deduplication check
        if dedup:
            dup = self.check_duplicate(content, embedding)
            if dup["is_duplicate"]:
                self._increment_stat("dedup_blocked")
                self.db.commit()
                return dup["duplicate_of"]  # return existing cell id

        cursor = self.db.execute(
            """INSERT INTO mem_cells
               (scene, cell_type, salience, content, source, tags, embedding, content_hash, owner_id, visibility, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cell["scene"],
                cell.get("cell_type", "fact"),
                cell.get("salience", 0.5),
                content,
                cell.get("source", ""),
                tags_json,
                emb_blob,
                content_hash,
                cell.get("owner_id", owner_id),
                cell.get("visibility", visibility),
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

        # Auto-relate if embedding provided
        if embedding is not None:
            self.auto_relate(row_id, embedding)

        return row_id

    def tag_cell(self, cell_id: int, tags: list[str], caller_id: Optional[str] = None):
        """Add tags to a cell (merges with existing, deduplicates)."""
        row = self.db.execute("SELECT tags, owner_id FROM mem_cells WHERE id = ?", (cell_id,)).fetchone()
        if not row:
            return
        if caller_id is not None and ADMIN_USER_ID and caller_id != ADMIN_USER_ID:
            if row["owner_id"] != caller_id:
                raise PermissionError(f"Caller {caller_id!r} does not own cell {cell_id}")
        existing = json.loads(row["tags"] or "[]")
        merged = sorted(set(existing + [t.lower().strip() for t in tags]))
        tags_json = json.dumps(merged)
        now = datetime.utcnow().isoformat()
        self.db.execute(
            "UPDATE mem_cells SET tags = ?, updated_at = ? WHERE id = ?",
            (tags_json, now, cell_id),
        )
        content_row = self.db.execute(
            "SELECT content, scene, cell_type FROM mem_cells WHERE id = ?", (cell_id,)
        ).fetchone()
        self.db.execute("DELETE FROM mem_fts WHERE rowid = ?", (cell_id,))
        self.db.execute(
            "INSERT INTO mem_fts(rowid, content, scene, cell_type, tags) VALUES (?, ?, ?, ?, ?)",
            (cell_id, content_row["content"], content_row["scene"], content_row["cell_type"], tags_json),
        )
        self.db.commit()

    def search_by_tag(self, tag: str, limit: int = 20, caller_id: Optional[str] = None) -> list[dict]:
        """Find cells matching a tag."""
        pattern = f'%"{tag.lower().strip()}"%'
        if caller_id is not None and ADMIN_USER_ID and caller_id != ADMIN_USER_ID:
            rows = self.db.execute(
                """SELECT id, scene, cell_type, salience, content, source, tags, created_at
                   FROM mem_cells WHERE tags LIKE ?
                   AND (owner_id = ? OR visibility = 'shared')
                   ORDER BY salience DESC LIMIT ?""",
                (pattern, caller_id, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT id, scene, cell_type, salience, content, source, tags, created_at
                   FROM mem_cells WHERE tags LIKE ?
                   ORDER BY salience DESC LIMIT ?""",
                (pattern, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_embedding(self, cell_id: int, embedding: np.ndarray):
        self.db.execute(
            "UPDATE mem_cells SET embedding = ? WHERE id = ?",
            (embedding.tobytes(), cell_id),
        )
        self.db.commit()

    def delete_cell(self, cell_id: int, caller_id: Optional[str] = None):
        if caller_id is not None and ADMIN_USER_ID and caller_id != ADMIN_USER_ID:
            row = self.db.execute("SELECT owner_id FROM mem_cells WHERE id = ?", (cell_id,)).fetchone()
            if row and row["owner_id"] != caller_id:
                raise PermissionError(f"Caller {caller_id!r} does not own cell {cell_id}")
        self.db.execute("DELETE FROM mem_fts WHERE rowid = ?", (cell_id,))
        self.db.execute("DELETE FROM mem_edges WHERE source_id = ? OR target_id = ?", (cell_id, cell_id))
        self.db.execute("DELETE FROM mem_cells WHERE id = ?", (cell_id,))
        self.db.commit()

    # -------------------------------------------------------------------------
    # Search
    # -------------------------------------------------------------------------

    def search_fts(self, query: str, limit: int = 10, caller_id: Optional[str] = None) -> list[dict]:
        """Full-text search fallback."""
        tokens = re.findall(r"[a-zA-Z0-9]+", query)
        if not tokens:
            return []

        fts_query = " OR ".join(tokens)
        if caller_id is not None and ADMIN_USER_ID and caller_id != ADMIN_USER_ID:
            rows = self.db.execute(
                """SELECT m.id, m.scene, m.cell_type, m.salience, m.content, m.source, m.tags, m.created_at
                   FROM mem_fts f
                   JOIN mem_cells m ON f.rowid = m.id
                   WHERE mem_fts MATCH ?
                   AND (m.owner_id = ? OR m.visibility = 'shared')
                   ORDER BY m.salience DESC
                   LIMIT ?""",
                (fts_query, caller_id, limit),
            ).fetchall()
        else:
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

    def search_vector(self, query_embedding: np.ndarray, limit: int = 10, caller_id: Optional[str] = None) -> list[dict]:
        """Vector similarity search using cosine similarity."""
        if caller_id is not None and ADMIN_USER_ID and caller_id != ADMIN_USER_ID:
            rows = self.db.execute(
                "SELECT id, scene, cell_type, salience, content, source, embedding, created_at FROM mem_cells WHERE embedding IS NOT NULL AND (owner_id = ? OR visibility = 'shared')",
                (caller_id,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id, scene, cell_type, salience, content, source, embedding, created_at FROM mem_cells WHERE embedding IS NOT NULL"
            ).fetchall()

        if not rows:
            return []

        scored = []
        for row in rows:
            emb = _safe_embedding(row["embedding"])
            if emb is None:
                continue
            sim = float(np.dot(query_embedding, emb) / (np.linalg.norm(query_embedding) * np.linalg.norm(emb) + 1e-10))
            # Blend similarity with salience: 70% semantic, 30% salience
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

    def search_hybrid(
        self,
        query_text: str,
        query_embedding: Optional[np.ndarray] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Hybrid search: 70% vector + 30% FTS, with type weights and salience multiplier.

        Falls back to FTS-only when no embedding provided.
        Discards results below 0.25 combined score.
        """
        if query_embedding is None:
            # FTS-only fallback
            results = self.search_fts(query_text, limit=limit)
            for r in results:
                r["similarity"] = 0.0
                r["fts_rank"] = r.get("salience", 0.5)
                r["score"] = r["fts_rank"]
            return results

        # --- FTS pass ---
        fts_scores: dict[int, float] = {}
        # Guard against bytes being passed in (e.g. from sqlite row values)
        if isinstance(query_text, bytes):
            query_text = query_text.decode("utf-8", errors="replace")
        tokens = re.findall(r"[a-zA-Z0-9]+", query_text)
        if tokens:
            fts_query = " OR ".join(tokens)
            fts_rows = self.db.execute(
                """SELECT m.id, m.salience,
                          rank as fts_rank
                   FROM mem_fts f
                   JOIN mem_cells m ON f.rowid = m.id
                   WHERE mem_fts MATCH ?
                   ORDER BY rank
                   LIMIT 100""",
                (fts_query,),
            ).fetchall()

            # FTS5 rank is negative BM25 (lower = better); normalize to [0,1]
            ranks = [r["fts_rank"] for r in fts_rows]
            if ranks:
                min_r, max_r = min(ranks), max(ranks)
                span = max_r - min_r if max_r != min_r else 1.0
                for r in fts_rows:
                    # Invert: best (most negative) → 1.0
                    normalized = 1.0 - (r["fts_rank"] - min_r) / span
                    fts_scores[r["id"]] = normalized

        # --- Vector pass ---
        vec_rows = self.db.execute(
            "SELECT id, scene, cell_type, salience, content, source, tags, created_at, embedding FROM mem_cells WHERE embedding IS NOT NULL"
        ).fetchall()

        combined: dict[int, dict] = {}

        for row in vec_rows:
            emb = _safe_embedding(row["embedding"])
            if emb is None:
                continue
            sim = float(
                np.dot(query_embedding, emb)
                / (np.linalg.norm(query_embedding) * np.linalg.norm(emb) + 1e-10)
            )
            fts_rank = fts_scores.get(row["id"], 0.0)

            # Blend: 70% vector + 30% FTS
            blended = 0.7 * max(sim, 0.0) + 0.3 * fts_rank

            # Type weight
            type_w = TYPE_WEIGHTS.get(row["cell_type"], 1.0)

            # Salience multiplier
            salience = row["salience"]

            final_score = blended * type_w * salience

            row_dict = dict(row)
            row_dict.pop("embedding", None)
            row_dict["similarity"] = round(sim, 4)
            row_dict["fts_rank"] = round(fts_rank, 4)
            row_dict["score"] = round(final_score, 4)
            combined[row["id"]] = row_dict

        # Also include FTS-only hits (no embedding) that scored well
        fts_only_ids = set(fts_scores.keys()) - set(combined.keys())
        if fts_only_ids:
            placeholders = ",".join("?" * len(fts_only_ids))
            fts_only_rows = self.db.execute(
                f"SELECT id, scene, cell_type, salience, content, source, tags, created_at FROM mem_cells WHERE id IN ({placeholders})",
                list(fts_only_ids),
            ).fetchall()
            for row in fts_only_rows:
                fts_rank = fts_scores.get(row["id"], 0.0)
                type_w = TYPE_WEIGHTS.get(row["cell_type"], 1.0)
                salience = row["salience"]
                final_score = 0.3 * fts_rank * type_w * salience
                row_dict = dict(row)
                row_dict["similarity"] = 0.0
                row_dict["fts_rank"] = round(fts_rank, 4)
                row_dict["score"] = round(final_score, 4)
                combined[row["id"]] = row_dict

        # Apply abstention floor
        results = [r for r in combined.values() if r["score"] >= 0.25]
        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:limit]

        # Update access counts
        for r in results:
            self.db.execute(
                "UPDATE mem_cells SET access_count = access_count + 1 WHERE id = ?",
                (r["id"],),
            )
        self.db.commit()
        return results

    # -------------------------------------------------------------------------
    # Deduplication
    # -------------------------------------------------------------------------

    def check_duplicate(
        self, content: str, embedding: Optional[np.ndarray] = None
    ) -> dict:
        """Three-layer duplicate detection.

        Returns dict with keys: is_duplicate, duplicate_of, method, similarity.
        """
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Layer 1: Exact hash match
        exact = self.db.execute(
            "SELECT id FROM mem_cells WHERE content_hash = ? LIMIT 1", (content_hash,)
        ).fetchone()
        if exact:
            return {
                "is_duplicate": True,
                "duplicate_of": exact["id"],
                "method": "exact",
                "similarity": 1.0,
            }

        # Layer 2: Semantic similarity (cosine >= 0.85)
        if embedding is not None:
            rows = self.db.execute(
                "SELECT id, embedding FROM mem_cells WHERE embedding IS NOT NULL"
            ).fetchall()
            best_sim = 0.0
            best_id = None
            for row in rows:
                emb = _safe_embedding(row["embedding"])
                if emb is None:
                    continue
                sim = float(
                    np.dot(embedding, emb)
                    / (np.linalg.norm(embedding) * np.linalg.norm(emb) + 1e-10)
                )
                if sim > best_sim:
                    best_sim = sim
                    best_id = row["id"]
            if best_sim >= 0.85:
                return {
                    "is_duplicate": True,
                    "duplicate_of": best_id,
                    "method": "semantic",
                    "similarity": round(best_sim, 4),
                }

        # Layer 3: Jaccard word similarity >= 0.80
        words_new = set(re.findall(r"[a-zA-Z0-9]+", content.lower()))
        if words_new:
            all_rows = self.db.execute("SELECT id, content FROM mem_cells").fetchall()
            best_jac = 0.0
            best_id = None
            for row in all_rows:
                words_existing = set(re.findall(r"[a-zA-Z0-9]+", row["content"].lower()))
                if not words_existing:
                    continue
                intersection = len(words_new & words_existing)
                union = len(words_new | words_existing)
                jac = intersection / union if union > 0 else 0.0
                if jac > best_jac:
                    best_jac = jac
                    best_id = row["id"]
            if best_jac >= 0.80:
                return {
                    "is_duplicate": True,
                    "duplicate_of": best_id,
                    "method": "jaccard",
                    "similarity": round(best_jac, 4),
                }

        return {
            "is_duplicate": False,
            "duplicate_of": None,
            "method": None,
            "similarity": 0.0,
        }

    # -------------------------------------------------------------------------
    # TTL / Cleanup
    # -------------------------------------------------------------------------

    def cleanup_expired(self) -> int:
        """Delete cells past their type-based TTL. Returns count deleted."""
        now = datetime.utcnow()
        total_deleted = 0

        for cell_type, ttl_days in CELL_TYPE_TTL.items():
            if ttl_days is None:
                continue  # permanent
            cutoff = (now - timedelta(days=ttl_days)).isoformat()
            rows = self.db.execute(
                "SELECT id FROM mem_cells WHERE cell_type = ? AND created_at < ?",
                (cell_type, cutoff),
            ).fetchall()
            for row in rows:
                self.db.execute("DELETE FROM mem_fts WHERE rowid = ?", (row["id"],))
                self.db.execute("DELETE FROM mem_edges WHERE source_id = ? OR target_id = ?", (row["id"], row["id"]))
                self.db.execute("DELETE FROM mem_cells WHERE id = ?", (row["id"],))
                total_deleted += 1

        if total_deleted > 0:
            self._increment_stat("ttl_expirations", total_deleted)

        self.db.commit()
        return total_deleted

    def decay(self, days_old: int = 30, decay_factor: float = 0.9) -> int:
        """Decay salience of old, rarely-accessed cells. Permanent types floor at 0.3."""
        cutoff = (datetime.utcnow() - timedelta(days=days_old)).isoformat()

        # Non-permanent types: decay normally, floor at 0.1
        non_permanent = [t for t in CELL_TYPE_TTL if t not in PERMANENT_TYPES]
        if non_permanent:
            placeholders = ",".join("?" * len(non_permanent))
            self.db.execute(
                f"""UPDATE mem_cells
                   SET salience = MAX(0.1, salience * ?), updated_at = ?
                   WHERE created_at < ? AND access_count < 3 AND salience > 0.1
                   AND cell_type IN ({placeholders})""",
                [decay_factor, datetime.utcnow().isoformat(), cutoff] + non_permanent,
            )

        # Permanent types: decay but floor at 0.3
        perm_list = list(PERMANENT_TYPES)
        if perm_list:
            placeholders = ",".join("?" * len(perm_list))
            self.db.execute(
                f"""UPDATE mem_cells
                   SET salience = MAX(0.3, salience * ?), updated_at = ?
                   WHERE created_at < ? AND access_count < 3 AND salience > 0.3
                   AND cell_type IN ({placeholders})""",
                [decay_factor, datetime.utcnow().isoformat(), cutoff] + perm_list,
            )

        affected = self.db.total_changes
        self.db.commit()
        return affected

    # -------------------------------------------------------------------------
    # Graph edges
    # -------------------------------------------------------------------------

    def add_edge(
        self,
        source_id: int,
        target_id: int,
        edge_type: str = "related",
        weight: float = 1.0,
    ) -> int:
        """Add a directed edge between two cells. Returns edge id."""
        now = datetime.utcnow().isoformat()
        # Avoid duplicate edges of same type
        existing = self.db.execute(
            "SELECT id FROM mem_edges WHERE source_id = ? AND target_id = ? AND edge_type = ?",
            (source_id, target_id, edge_type),
        ).fetchone()
        if existing:
            # Update weight
            self.db.execute(
                "UPDATE mem_edges SET weight = ? WHERE id = ?",
                (weight, existing["id"]),
            )
            self.db.commit()
            return existing["id"]

        cursor = self.db.execute(
            "INSERT INTO mem_edges (source_id, target_id, edge_type, weight, created_at) VALUES (?, ?, ?, ?, ?)",
            (source_id, target_id, edge_type, weight, now),
        )
        self.db.commit()
        return cursor.lastrowid

    def get_edges(
        self,
        cell_id: int,
        direction: str = "both",
        edge_type: Optional[str] = None,
    ) -> list[dict]:
        """Get edges connected to a cell.

        direction: "out" (source), "in" (target), or "both".
        edge_type: optional filter.
        """
        conditions = []
        params: list = []

        if direction == "out":
            conditions.append("source_id = ?")
            params.append(cell_id)
        elif direction == "in":
            conditions.append("target_id = ?")
            params.append(cell_id)
        else:  # both
            conditions.append("(source_id = ? OR target_id = ?)")
            params.extend([cell_id, cell_id])

        if edge_type:
            conditions.append("edge_type = ?")
            params.append(edge_type)

        where = " AND ".join(conditions)
        rows = self.db.execute(
            f"SELECT id, source_id, target_id, edge_type, weight, created_at FROM mem_edges WHERE {where}",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def auto_relate(
        self,
        cell_id: int,
        embedding: np.ndarray,
        top_k: int = 3,
        min_similarity: float = 0.45,
    ) -> list[int]:
        """Find top-k similar cells and create 'related' edges. Returns list of edge ids."""
        rows = self.db.execute(
            "SELECT id, embedding FROM mem_cells WHERE embedding IS NOT NULL AND id != ?",
            (cell_id,),
        ).fetchall()

        scored = []
        for row in rows:
            emb = _safe_embedding(row["embedding"])
            if emb is None:
                continue
            sim = float(
                np.dot(embedding, emb)
                / (np.linalg.norm(embedding) * np.linalg.norm(emb) + 1e-10)
            )
            if sim >= min_similarity:
                scored.append((sim, row["id"]))

        scored.sort(reverse=True)
        edge_ids = []
        for sim, related_id in scored[:top_k]:
            eid = self.add_edge(cell_id, related_id, edge_type="related", weight=round(sim, 4))
            edge_ids.append(eid)
        return edge_ids

    # -------------------------------------------------------------------------
    # Scene operations
    # -------------------------------------------------------------------------

    def get_scene(self, scene: str) -> tuple[Optional[dict], list[dict]]:
        row = self.db.execute("SELECT * FROM mem_scenes WHERE scene = ?", (scene,)).fetchone()
        cells = self.db.execute(
            "SELECT id, scene, cell_type, salience, content, source, tags, access_count, created_at FROM mem_cells WHERE scene = ? ORDER BY salience DESC",
            (scene,),
        ).fetchall()
        return (dict(row) if row else None), [dict(c) for c in cells]

    def list_scenes(self) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT scene, summary, cell_count, updated_at FROM mem_scenes ORDER BY updated_at DESC"
            ).fetchall()
        ]

    def upsert_scene(self, scene: str, summary: str, summary_embedding: Optional[np.ndarray] = None):
        count = self.db.execute("SELECT COUNT(*) FROM mem_cells WHERE scene = ?", (scene,)).fetchone()[0]
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

    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------

    def all_cells(self) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT id, scene, cell_type, salience, content, source, tags, access_count, created_at, updated_at FROM mem_cells ORDER BY id"
            ).fetchall()
        ]

    def cells_without_embeddings(self) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT id, content FROM mem_cells WHERE embedding IS NULL"
            ).fetchall()
        ]

    def stats(self) -> dict:
        total = self.db.execute("SELECT COUNT(*) FROM mem_cells").fetchone()[0]
        scenes = self.db.execute("SELECT COUNT(*) FROM mem_scenes").fetchone()[0]
        embedded = self.db.execute("SELECT COUNT(*) FROM mem_cells WHERE embedding IS NOT NULL").fetchone()[0]
        edges = self.db.execute("SELECT COUNT(*) FROM mem_edges").fetchone()[0]
        types = self.db.execute(
            "SELECT cell_type, COUNT(*) as c FROM mem_cells GROUP BY cell_type ORDER BY c DESC"
        ).fetchall()
        top_scenes = self.db.execute(
            "SELECT scene, COUNT(*) as c FROM mem_cells GROUP BY scene ORDER BY c DESC LIMIT 10"
        ).fetchall()

        dedup_blocked = (
            self.db.execute("SELECT value FROM mem_stats WHERE key = 'dedup_blocked'").fetchone() or [0]
        )[0]
        ttl_expirations = (
            self.db.execute("SELECT value FROM mem_stats WHERE key = 'ttl_expirations'").fetchone() or [0]
        )[0]

        return {
            "total_cells": total,
            "embedded_cells": embedded,
            "total_scenes": scenes,
            "edge_count": edges,
            "dedup_blocked": dedup_blocked,
            "ttl_expirations": ttl_expirations,
            "by_type": {r[0]: r[1] for r in types},
            "top_scenes": {r[0]: r[1] for r in top_scenes},
        }

    def _increment_stat(self, key: str, amount: int = 1):
        self.db.execute(
            "INSERT INTO mem_stats (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = value + ?",
            (key, amount, amount),
        )
