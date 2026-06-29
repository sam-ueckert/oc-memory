"""Tests for the Hermes session extractor and MCP client (no network/LLM)."""

import json

import pytest

from oc_memory.hermes_extractor import HermesSessionExtractor
from oc_memory import mcp_client


# ── _clean_messages ──────────────────────────────────────────────────────────────

def test_clean_messages_filters_tools_and_heartbeats():
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "tool", "content": "tool output"},
        {"role": "assistant", "content": "NO_REPLY"},
        {"role": "user", "content": "  "},
        {"role": "assistant", "content": "Read HEARTBEAT now"},
    ]
    out = HermesSessionExtractor._clean_messages(msgs)
    assert out == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


# ── _parse_cells ─────────────────────────────────────────────────────────────────

def test_parse_cells_jsonl_and_normalization():
    ex = HermesSessionExtractor(use_cli=False)
    raw = "\n".join([
        '{"cell_type":"fact","scene":"infra","salience":0.9,"content":"a"}',
        'garbage line',
        '{"type":"decision","content":"b","salience":3.0}',  # type→cell_type, salience clamp
        '{"content":"c"}',  # defaults
    ])
    cells = ex._parse_cells(raw)
    assert len(cells) == 3
    assert cells[0]["cell_type"] == "fact"
    assert cells[1]["cell_type"] == "decision"  # mapped from "type"
    assert cells[1]["salience"] == 1.0          # clamped
    assert cells[2]["scene"] == "general"       # default
    assert cells[2]["salience"] == 0.5


def test_parse_cells_respects_max_memories():
    ex = HermesSessionExtractor(use_cli=False, max_memories=2)
    raw = "\n".join('{"content":"x%d"}' % i for i in range(5))
    assert len(ex._parse_cells(raw)) == 2


# ── sink routing ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sink,expect_mcp,expect_local", [
    ("mcp", True, False),
    ("local", False, True),
    ("both", True, True),
])
def test_store_cells_routes_to_correct_sinks(monkeypatch, sink, expect_mcp, expect_local):
    ex = HermesSessionExtractor(use_cli=False, sink=sink)
    called = {"mcp": False, "local": False}
    monkeypatch.setattr(ex, "_store_mcp", lambda cells: called.__setitem__("mcp", True) or len(cells))
    monkeypatch.setattr(ex, "_store_local", lambda cells: called.__setitem__("local", True) or len(cells))
    ex._store_cells([{"content": "x"}], {"source": "slack"})
    assert called["mcp"] is expect_mcp
    assert called["local"] is expect_local


def test_store_cells_tags_with_source(monkeypatch):
    ex = HermesSessionExtractor(use_cli=False, sink="mcp")
    captured = {}
    monkeypatch.setattr(ex, "_store_mcp", lambda cells: captured.update(cells=cells) or len(cells))
    ex._store_cells([{"content": "x"}], {"source": "discord"})
    assert "hermes-discord" in captured["cells"][0]["tags"]


# ── MCP client JSON-RPC parsing (modern Streamable HTTP, no SSE transport) ───────

def test_parse_jsonrpc_plain_json():
    out = mcp_client._parse_jsonrpc('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')
    assert out["result"]["ok"] is True


def test_parse_jsonrpc_event_stream_frame():
    frame = "event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"ok\":1}}\n\n"
    out = mcp_client._parse_jsonrpc(frame)
    assert out["result"]["ok"] == 1


def test_parse_jsonrpc_rejects_garbage():
    with pytest.raises(mcp_client.MCPClientError):
        mcp_client._parse_jsonrpc("not json and not sse")


# ── migrate_local_to_mcp safety (mcp-only migration) ─────────────────────────────

from oc_memory import hermes_extractor as hx


def _seed_local_db(path, n=3):
    from oc_memory.db import MemoryDB
    db = MemoryDB(str(path))
    for i in range(n):
        db.insert_cell(
            {"scene": "infra", "cell_type": "fact", "salience": 0.7, "content": f"cell {i}"},
            dedup=True,
        )
    return db


def test_migrate_is_nondestructive_on_connect_failure(tmp_path, monkeypatch):
    """A failed push must not raise and must leave the local DB fully intact."""
    from oc_memory.db import MemoryDB
    db_path = tmp_path / "memory.db"
    _seed_local_db(db_path, 3)

    def boom(*a, **k):
        raise mcp_client.MCPClientError("refused")
    # Patch the symbol the function imports lazily.
    monkeypatch.setattr("oc_memory.mcp_client.store_cells", boom)

    res = hx.migrate_local_to_mcp(str(db_path), "http://127.0.0.1:59999/mcp", timeout_secs=1)
    assert res["ok"] is False
    assert res["migrated"] == 0
    assert res["total"] == 3
    assert res["error"]
    # local DB untouched — non-destructive copy
    assert len(MemoryDB(str(db_path)).all_cells()) == 3


def test_migrate_dry_run_counts_without_pushing(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    _seed_local_db(db_path, 4)
    called = {"n": 0}
    monkeypatch.setattr("oc_memory.mcp_client.store_cells",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"stored": 4, "errors": 0})
    res = hx.migrate_local_to_mcp(str(db_path), "http://x/mcp", dry_run=True)
    assert res["total"] == 4 and res["migrated"] == 0 and res["ok"] is True
    assert called["n"] == 0  # never hit the network on dry-run


def test_migrate_ok_only_when_all_cells_land(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    _seed_local_db(db_path, 5)
    # Partial success: 3 stored, 2 errored -> ok must be False
    monkeypatch.setattr("oc_memory.mcp_client.store_cells",
                        lambda *a, **k: {"stored": 3, "errors": 2})
    res = hx.migrate_local_to_mcp(str(db_path), "http://x/mcp")
    assert res["migrated"] == 3 and res["errors"] == 2 and res["ok"] is False

    # Full success -> ok True
    monkeypatch.setattr("oc_memory.mcp_client.store_cells",
                        lambda *a, **k: {"stored": 5, "errors": 0})
    res2 = hx.migrate_local_to_mcp(str(db_path), "http://x/mcp")
    assert res2["migrated"] == 5 and res2["ok"] is True
