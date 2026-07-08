"""Tests for the oc-memory CLI's `search` flag parsing and bounding (issue #6)."""

import sys

import pytest

import oc_memory.cli as cli


# ── _parse_search_args (pure function, no I/O) ──────────────────────────────

def test_parse_search_args_no_flags():
    query, limit, min_score, excerpt_max = cli._parse_search_args(
        ["what", "did", "we", "decide"]
    )
    assert query == "what did we decide"
    assert limit is None
    assert min_score is None
    assert excerpt_max is None


def test_parse_search_args_limit_space_form():
    query, limit, _, _ = cli._parse_search_args(["hello", "--limit", "5"])
    assert query == "hello"
    assert limit == 5


def test_parse_search_args_limit_equals_form():
    query, limit, _, _ = cli._parse_search_args(["hello", "--limit=7"])
    assert query == "hello"
    assert limit == 7


def test_parse_search_args_min_score():
    _, _, min_score, _ = cli._parse_search_args(["hello", "--min-score", "0.42"])
    assert min_score == pytest.approx(0.42)


def test_parse_search_args_excerpt_max():
    _, _, _, excerpt_max = cli._parse_search_args(["hello", "--excerpt-max", "40"])
    assert excerpt_max == 40


def test_parse_search_args_flags_interleaved_with_query_words():
    query, limit, min_score, excerpt_max = cli._parse_search_args(
        ["what", "--limit", "3", "did", "--min-score=0.5", "we", "decide", "--excerpt-max", "50"]
    )
    assert query == "what did we decide"
    assert limit == 3
    assert min_score == pytest.approx(0.5)
    assert excerpt_max == 50


def test_parse_search_args_invalid_limit_exits():
    with pytest.raises(SystemExit) as exc:
        cli._parse_search_args(["hello", "--limit", "not-a-number"])
    assert exc.value.code == 2


def test_parse_search_args_zero_limit_exits():
    with pytest.raises(SystemExit) as exc:
        cli._parse_search_args(["hello", "--limit", "0"])
    assert exc.value.code == 2


def test_parse_search_args_negative_limit_exits():
    with pytest.raises(SystemExit) as exc:
        cli._parse_search_args(["hello", "--limit", "-1"])
    assert exc.value.code == 2


def test_parse_search_args_negative_excerpt_max_exits():
    with pytest.raises(SystemExit) as exc:
        cli._parse_search_args(["hello", "--excerpt-max", "-5"])
    assert exc.value.code == 2


def test_parse_search_args_invalid_min_score_exits():
    with pytest.raises(SystemExit) as exc:
        cli._parse_search_args(["hello", "--min-score", "not-a-float"])
    assert exc.value.code == 2


def test_parse_search_args_missing_value_exits():
    with pytest.raises(SystemExit) as exc:
        cli._parse_search_args(["hello", "--limit"])
    assert exc.value.code == 2


def test_parse_search_args_limit_clamped_to_ceiling():
    _, limit, _, _ = cli._parse_search_args(["hello", "--limit", "999999"])
    assert limit == cli.SEARCH_LIMIT_MAX


def test_parse_search_args_min_score_clamped_into_unit_range():
    _, _, min_score, _ = cli._parse_search_args(["hello", "--min-score", "5"])
    assert min_score == 1.0
    _, _, min_score2, _ = cli._parse_search_args(["hello", "--min-score", "-3"])
    assert min_score2 == 0.0


def test_parse_search_args_excerpt_max_clamped_to_ceiling():
    _, _, _, excerpt_max = cli._parse_search_args(["hello", "--excerpt-max", "999999"])
    assert excerpt_max == cli.EXCERPT_MAX_MAX


# ── `search` command end-to-end (fake DB + embedder, no real I/O) ──────────

class _FakeEmbedder:
    def __init__(self, available):
        self._available = available

    def is_available(self):
        return self._available

    def embed(self, text):  # pragma: no cover - only called when available
        return "fake-embedding"


class _FakeDB:
    def __init__(self, fts_rows=None, vec_rows=None):
        self._fts_rows = fts_rows or []
        self._vec_rows = vec_rows
        self.fts_calls = []
        self.vec_calls = []
        self.fts_queries = []

    def search_fts(self, query, limit=10, caller_id=None):
        self.fts_calls.append(limit)
        self.fts_queries.append(query)
        return self._fts_rows[:limit]

    def search_vector(self, query_emb, limit=10, caller_id=None):
        self.vec_calls.append(limit)
        return (self._vec_rows or [])[:limit]


def _row(id_, content, similarity=None):
    r = {
        "id": id_,
        "cell_type": "fact",
        "scene": "test",
        "salience": 0.5,
        "content": content,
        "tags": "[]",
    }
    if similarity is not None:
        r["similarity"] = similarity
    return r


def _run_search(monkeypatch, capsys, argv, fake_db, embedder_available=False):
    monkeypatch.setattr(cli, "get_db", lambda: fake_db)
    monkeypatch.setattr(cli, "get_embedder", lambda: _FakeEmbedder(embedder_available))
    monkeypatch.setattr(sys, "argv", ["oc-memory", "search"] + argv)
    cli.main()
    return capsys.readouterr().out


def test_search_default_backward_compatible(monkeypatch, capsys):
    """No flags: limit defaults to 10 (unchanged), excerpt defaults to 120 chars."""
    fake_db = _FakeDB(fts_rows=[_row(1, "x" * 200)])
    out = _run_search(monkeypatch, capsys, ["hello", "world"], fake_db)
    assert fake_db.fts_calls == [10]
    assert "x" * 120 in out
    assert "x" * 121 not in out


def test_search_no_results_message(monkeypatch, capsys):
    fake_db = _FakeDB(fts_rows=[])
    out = _run_search(monkeypatch, capsys, ["nothing", "matches"], fake_db)
    assert "No results found." in out


def test_search_limit_flag_bounds_db_query(monkeypatch, capsys):
    fake_db = _FakeDB(fts_rows=[_row(i, f"content {i}") for i in range(5)])
    _run_search(monkeypatch, capsys, ["hello", "--limit", "2"], fake_db)
    assert fake_db.fts_calls == [2]


def test_search_excerpt_max_flag_truncates_output(monkeypatch, capsys):
    fake_db = _FakeDB(fts_rows=[_row(1, "y" * 50)])
    out = _run_search(monkeypatch, capsys, ["hello", "--excerpt-max", "10"], fake_db)
    assert "y" * 10 in out
    assert "y" * 11 not in out


def test_search_min_score_ignored_for_fts_rows_without_similarity(monkeypatch, capsys):
    """FTS-fallback rows carry no similarity score, so --min-score must not
    wipe them out — it only applies where a real score is available."""
    fake_db = _FakeDB(fts_rows=[_row(1, "no similarity field on this row")])
    out = _run_search(monkeypatch, capsys, ["hello", "--min-score", "0.99"], fake_db)
    assert "No results found." not in out
    assert "no similarity field on this row" in out


def test_search_min_score_filters_vector_rows(monkeypatch, capsys):
    fake_db = _FakeDB(
        vec_rows=[
            _row(1, "keep me", similarity=0.9),
            _row(2, "drop me", similarity=0.1),
        ]
    )
    out = _run_search(
        monkeypatch, capsys, ["hello", "--min-score", "0.5"], fake_db, embedder_available=True
    )
    assert "keep me" in out
    assert "drop me" not in out


def test_search_min_score_boundary_is_inclusive(monkeypatch, capsys):
    fake_db = _FakeDB(vec_rows=[_row(1, "exactly at threshold", similarity=0.5)])
    out = _run_search(
        monkeypatch, capsys, ["hello", "--min-score", "0.5"], fake_db, embedder_available=True
    )
    assert "exactly at threshold" in out


def test_search_flags_do_not_leak_into_query(monkeypatch, capsys):
    fake_db = _FakeDB(fts_rows=[])
    _run_search(
        monkeypatch,
        capsys,
        ["what", "did", "--limit", "3", "we", "decide"],
        fake_db,
    )
    # The DB should see the query with flags stripped out.
    assert fake_db.fts_calls == [3]
    assert fake_db.fts_queries == ["what did we decide"]
