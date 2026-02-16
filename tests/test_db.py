"""Tests for the memory database."""

import numpy as np
import pytest

from oc_memory.db import MemoryDB


@pytest.fixture
def db(tmp_path):
    return MemoryDB(tmp_path / "test.db")


def test_insert_and_search_fts(db):
    db.insert_cell({
        "scene": "test",
        "cell_type": "fact",
        "salience": 0.8,
        "content": "The sky is blue on clear days",
    })
    results = db.search_fts("sky blue")
    assert len(results) == 1
    assert results[0]["content"] == "The sky is blue on clear days"


def test_vector_search(db):
    emb1 = np.random.randn(768).astype(np.float32)
    emb1 /= np.linalg.norm(emb1)

    emb2 = np.random.randn(768).astype(np.float32)
    emb2 /= np.linalg.norm(emb2)

    db.insert_cell({"scene": "a", "cell_type": "fact", "content": "hello"}, embedding=emb1)
    db.insert_cell({"scene": "b", "cell_type": "fact", "content": "world"}, embedding=emb2)

    results = db.search_vector(emb1, limit=2)
    assert len(results) == 2
    assert results[0]["content"] == "hello"


def test_scene_operations(db):
    db.insert_cell({"scene": "proj", "cell_type": "task", "salience": 0.7, "content": "Build thing"})
    db.insert_cell({"scene": "proj", "cell_type": "fact", "salience": 0.5, "content": "Uses Python"})
    db.upsert_scene("proj", "A project about building things")

    info, cells = db.get_scene("proj")
    assert info is not None
    assert info["cell_count"] == 2
    assert len(cells) == 2


def test_decay(db):
    db.insert_cell({"scene": "old", "cell_type": "fact", "salience": 0.5, "content": "old fact"})
    db.db.execute("UPDATE mem_cells SET created_at = '2020-01-01T00:00:00'")
    db.db.commit()

    affected = db.decay(days_old=1)
    assert affected >= 1

    row = db.db.execute("SELECT salience FROM mem_cells WHERE scene = 'old'").fetchone()
    assert row[0] < 0.5


def test_delete(db):
    row_id = db.insert_cell({"scene": "tmp", "cell_type": "fact", "content": "delete me"})
    db.delete_cell(row_id)
    assert db.db.execute("SELECT COUNT(*) FROM mem_cells").fetchone()[0] == 0


def test_stats(db):
    db.insert_cell({"scene": "a", "cell_type": "fact", "content": "one"})
    db.insert_cell({"scene": "b", "cell_type": "decision", "content": "two"})
    s = db.stats()
    assert s["total_cells"] == 2
    assert s["embedded_cells"] == 0


def test_access_count_increments_on_fts(db):
    db.insert_cell({"scene": "test", "cell_type": "fact", "content": "unique searchterm here"})
    db.search_fts("searchterm")
    db.search_fts("searchterm")
    row = db.db.execute("SELECT access_count FROM mem_cells").fetchone()
    assert row[0] == 2
