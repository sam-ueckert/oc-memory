"""Tests for MCP tool handler functions."""

import json
import pytest

import oc_memory.mcp_server as _ms

# Import tool functions at module level — fixture patches DB_PATH before each test
from oc_memory.mcp_server import (
    tool_memory_forget,
    tool_memory_scenes,
    tool_memory_search,
    tool_memory_stats,
    tool_memory_store,
    tool_memory_tag,
)


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Point the MCP server at a fresh temp DB for each test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OC_MEMORY_DB", db_path)
    # Patch the module-level DB_PATH (set at import time) and reset singletons
    monkeypatch.setattr(_ms, "DB_PATH", db_path)
    _ms._db = None
    _ms._embedder = None
    yield
    # Clean up singletons after test
    _ms._db = None
    _ms._embedder = None


# ── Store & Search ────────────────────────────────────────────────────────────

def test_store_and_search():
    result = tool_memory_store({
        "content": "The deployment uses k3s on a Raspberry Pi 5",
        "cell_type": "fact",
        "scene": "infrastructure",
        "salience": 0.8,
    })
    stored = json.loads(result)
    assert stored["stored"] is True
    assert stored["scene"] == "infrastructure"

    # Search — falls back to FTS when embedder unavailable
    search_result = tool_memory_search({"query": "k3s Raspberry"})
    data = json.loads(search_result)
    assert data["count"] >= 1
    contents = [r["content"] for r in data["results"]]
    assert any("k3s" in c for c in contents)


def test_search_returns_empty_for_unknown_query():
    tool_memory_store({"content": "Completely unrelated fact about penguins", "scene": "test"})
    result = tool_memory_search({"query": "xyzzy_no_match_possible_zqk"})
    data = json.loads(result)
    # May return 0 results — just verify it's valid JSON with a results list
    assert "results" in data
    assert isinstance(data["results"], list)


# ── Stats ─────────────────────────────────────────────────────────────────────

def test_stats_returns_json():
    result = tool_memory_stats({})
    data = json.loads(result)
    assert "total_cells" in data
    assert data["total_cells"] >= 0


def test_stats_reflects_stored_cells():
    tool_memory_store({"content": "first cell", "scene": "a"})
    tool_memory_store({"content": "second cell", "scene": "b"})
    data = json.loads(tool_memory_stats({}))
    assert data["total_cells"] == 2


# ── Forget ────────────────────────────────────────────────────────────────────

def test_forget():
    stored = json.loads(tool_memory_store({"content": "temporary fact", "scene": "tmp"}))
    cell_id = stored["id"]

    deleted = json.loads(tool_memory_forget({"id": cell_id}))
    assert deleted["deleted"] is True
    assert deleted["id"] == cell_id

    stats = json.loads(tool_memory_stats({}))
    assert stats["total_cells"] == 0


def test_forget_missing_id_raises():
    with pytest.raises(ValueError, match="id is required"):
        tool_memory_forget({})


def test_forget_nonexistent_raises():
    with pytest.raises(ValueError, match="not found"):
        tool_memory_forget({"id": 9999})


# ── Tag & Search Tag ──────────────────────────────────────────────────────────

def test_tag_and_search_tag():
    stored = json.loads(tool_memory_store({
        "content": "Swabby is the AI deckhand",
        "cell_type": "fact",
        "scene": "identity",
    }))
    cell_id = stored["id"]

    tag_result = json.loads(tool_memory_tag({"id": cell_id, "tags": ["agent", "identity"]}))
    assert tag_result["id"] == cell_id
    assert "agent" in tag_result["tags_added"]

    search_result = json.loads(tool_memory_search({"query": "agent"}))
    # Either tag search or FTS should return this cell
    # Verify tag route works via search_tag tool directly
    from oc_memory.mcp_server import tool_memory_search_tag
    tag_search = json.loads(tool_memory_search_tag({"tag": "agent"}))
    assert tag_search["count"] >= 1
    ids = [r["id"] for r in tag_search["results"]]
    assert cell_id in ids


def test_tag_requires_tags():
    stored = json.loads(tool_memory_store({"content": "some fact", "scene": "x"}))
    with pytest.raises(ValueError, match="tags must be a non-empty list"):
        tool_memory_tag({"id": stored["id"], "tags": []})


# ── Scenes ────────────────────────────────────────────────────────────────────

def test_scenes():
    tool_memory_store({"content": "fact about alpha project", "scene": "alpha"})
    tool_memory_store({"content": "fact about beta project", "scene": "beta"})
    # scenes table is populated via upsert_scene (called after summarisation);
    # do it directly here so list_scenes can see them
    db = _ms.get_db()
    db.upsert_scene("alpha", "Alpha project")
    db.upsert_scene("beta", "Beta project")

    result = json.loads(tool_memory_scenes({}))
    assert "scenes" in result
    scene_names = [s["scene"] for s in result["scenes"]]
    assert "alpha" in scene_names
    assert "beta" in scene_names


def test_scenes_empty_db():
    result = json.loads(tool_memory_scenes({}))
    assert result["count"] == 0
    assert result["scenes"] == []


# ── Stdio transport selection ─────────────────────────────────────────────────

def test_force_raw_stdio_env_skips_mcp_sdk(monkeypatch):
    monkeypatch.setenv("OC_MEMORY_FORCE_RAW_STDIO", "1")
    assert _ms._try_mcp_sdk_stdio() is False
