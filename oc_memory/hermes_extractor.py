"""Hermes session memory extractor.

Pulls recent sessions from the Hermes agent harness, uses an LLM to distill them
into structured memory cells, and pushes those cells to an oc-memory / archy
store. Designed for cron-driven batch extraction — it complements the agent's
inline memory tools by catching sessions where key decisions were never stored.

API-native by design:
  - EXTRACT via Hermes's own CLI:  `hermes sessions export` (JSONL) when the
    `hermes` binary is on PATH; falls back to read-only SQLite on state.db.
  - PUSH via archy's MCP API:      `memory_store` over Streamable HTTP.

Sinks (``--sink``):
  - ``mcp``    push only to the remote archy MCP server (default)
  - ``both``   push to MCP *and* keep a local oc-memory SQLite copy
  - ``local``  local SQLite only (legacy behaviour)

Because the extractor runs co-located with Hermes (same place the `hermes` CLI
and state.db live), the read side is identical whether Hermes runs in k3s or
natively — only the push target (an MCP URL) crosses the network.
"""

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_HERMES_DB = os.path.expanduser("~/.hermes/state.db")
DEFAULT_STATE_FILE = os.path.expanduser("~/.oc-memory/hermes_extract_state.json")
DEFAULT_API_URL = "http://localhost:18789/v1"  # LLM (OpenAI-compatible) endpoint
DEFAULT_API_TOKEN = ""
DEFAULT_MODEL = "openclaw"
# archy MCP server (push target). In-cluster default; override for native hosts.
DEFAULT_MCP_URL = "http://memory-server.swabby-memory.svc.cluster.local:8765/mcp"
DEFAULT_SINK = "mcp"  # mcp | both | local
DEFAULT_OWNER_ID = "hermes"
DEFAULT_VISIBILITY = "shared"
DEFAULT_MAX_INPUT_CHARS = 30000
DEFAULT_MAX_TOKENS = 2000
DEFAULT_TIMEOUT_SECS = 120
DEFAULT_MAX_MEMORIES = 15
DEFAULT_MIN_MESSAGES = 6

EXTRACTION_PROMPT = """You are a memory extraction system. Analyze this AI agent session transcript and extract structured, durable memories.

For each memory, output a JSON object on its own line with these fields:
- "cell_type": one of "fact", "decision", "preference", "task", "risk", "plan", "lesson"
- "salience": 0.1 (trivia) to 1.0 (critical)
- "scene": short topic label, lowercase (e.g. "infrastructure", "gateway-config", "k8s", "workflow")
- "content": concise 1-2 sentence statement, self-contained and factual

Rules:
- Extract 3-15 memories. Quality over quantity.
- Focus on: decisions made, facts discovered, preferences expressed, tasks assigned, lessons learned, risks identified, infrastructure changes
- Skip: routine tool output, heartbeat checks, pleasantries, temporary/transient context
- Each memory must stand alone — readable without the original conversation
- Use present tense for facts/preferences, past tense for events/decisions
- Scene names should be reusable across sessions — prefer broad categories over one-off labels

Output ONLY JSON lines, nothing else.

SESSION TRANSCRIPT:
"""


class HermesSessionExtractor:
    """Extract memories from Hermes session transcripts via LLM, push to a store."""

    def __init__(
        self,
        hermes_db: str = DEFAULT_HERMES_DB,
        state_file: str = DEFAULT_STATE_FILE,
        api_url: str = DEFAULT_API_URL,
        api_token: str = DEFAULT_API_TOKEN,
        model: str = DEFAULT_MODEL,
        sink: str = DEFAULT_SINK,
        mcp_url: str = DEFAULT_MCP_URL,
        mcp_token: str = "",
        owner_id: str = DEFAULT_OWNER_ID,
        visibility: str = DEFAULT_VISIBILITY,
        source_filter: str = "",
        use_cli: bool = True,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout_secs: int = DEFAULT_TIMEOUT_SECS,
        max_memories: int = DEFAULT_MAX_MEMORIES,
        min_messages: int = DEFAULT_MIN_MESSAGES,
    ):
        self.hermes_db = hermes_db
        self.state_file = state_file
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.model = model
        self.sink = sink
        self.mcp_url = mcp_url
        self.mcp_token = mcp_token
        self.owner_id = owner_id
        self.visibility = visibility
        self.source_filter = source_filter
        # Prefer the Hermes CLI export API when the binary is available.
        self.use_cli = use_cli and shutil.which("hermes") is not None
        self.max_input_chars = max_input_chars
        self.max_tokens = max_tokens
        self.timeout_secs = timeout_secs
        self.max_memories = max_memories
        self.min_messages = min_messages

    # ── State file (dedup tracking) ─────────────────────────────────────────
    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                return json.load(f)
        return {"processed": {}, "last_run": None}

    def _save_state(self, state: dict):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def _is_processed(self, session_id: str, message_count: int) -> bool:
        entry = self._load_state()["processed"].get(session_id)
        if entry is None:
            return False
        return entry.get("message_count", 0) >= message_count

    def _mark_processed(self, session_id: str, message_count: int, cells_stored: int):
        state = self._load_state()
        state["processed"][session_id] = {
            "message_count": message_count,
            "cells_stored": cells_stored,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        state["processed"] = {
            k: v for k, v in state["processed"].items()
            if v.get("extracted_at", "") > cutoff
        }
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        self._save_state(state)

    # ── Session discovery + reading ─────────────────────────────────────────
    def iter_sessions(self, since_hours: int = 24) -> list[dict]:
        """Return unprocessed sessions (newest first) with messages attached.

        Uses the Hermes CLI export API when available, else read-only SQLite.
        Each returned dict has: id, source, model, started_at, message_count,
        and 'messages' (list of {role, content}).
        """
        if self.use_cli:
            try:
                return self._iter_sessions_cli(since_hours)
            except Exception as e:
                log.warning(f"hermes CLI export failed ({e}); falling back to SQLite")
        return self._iter_sessions_sqlite(since_hours)

    def _iter_sessions_cli(self, since_hours: int) -> list[dict]:
        """Discover + read sessions via `hermes sessions export` (JSONL API)."""
        since_ts = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp()
        cmd = ["hermes", "sessions", "export"]
        if self.source_filter:
            cmd += ["--source", self.source_filter]
        with tempfile.NamedTemporaryFile("r+", suffix=".jsonl", delete=False) as tf:
            out_path = tf.name
        try:
            cmd.append(out_path)
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
            sessions = []
            with open(out_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if float(s.get("started_at") or 0) <= since_ts:
                        continue
                    mc = int(s.get("message_count") or len(s.get("messages") or []))
                    if self._is_processed(s["id"], mc):
                        continue
                    sessions.append({
                        "id": s["id"],
                        "source": s.get("source"),
                        "model": s.get("model"),
                        "started_at": s.get("started_at"),
                        "message_count": mc,
                        "messages": self._clean_messages(s.get("messages") or []),
                    })
            sessions.sort(key=lambda x: x.get("started_at") or 0, reverse=True)
            return sessions
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def _iter_sessions_sqlite(self, since_hours: int) -> list[dict]:
        """Fallback: discover + read sessions directly from state.db."""
        if not os.path.exists(self.hermes_db):
            log.warning(f"Hermes state.db not found at {self.hermes_db}. Is Hermes installed?")
            return []
        since_ts = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp()
        conn = sqlite3.connect(self.hermes_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        q = "SELECT id, source, model, started_at, message_count FROM sessions WHERE started_at > ?"
        args = [since_ts]
        if self.source_filter:
            q += " AND source = ?"
            args.append(self.source_filter)
        q += " ORDER BY started_at DESC"
        c.execute(q, args)
        rows = c.fetchall()
        sessions = []
        for row in rows:
            if self._is_processed(row["id"], row["message_count"]):
                continue
            mc = c.execute(
                "SELECT role, content FROM messages WHERE session_id=? AND role IN ('user','assistant') ORDER BY id",
                (row["id"],),
            ).fetchall()
            sessions.append({
                "id": row["id"],
                "source": row["source"],
                "model": row["model"],
                "started_at": row["started_at"],
                "message_count": row["message_count"],
                "messages": self._clean_messages([dict(m) for m in mc]),
            })
        conn.close()
        return sessions

    @staticmethod
    def _clean_messages(messages: list[dict]) -> list[dict]:
        """Keep user/assistant text, drop tool output and heartbeats."""
        out = []
        for m in messages:
            if m.get("role") not in ("user", "assistant"):
                continue
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if content in ("NO_REPLY", "HEARTBEAT_OK") or content.startswith("Read HEARTBEAT"):
                continue
            out.append({"role": m["role"], "content": content})
        return out

    # ── Transcript formatting ───────────────────────────────────────────────
    def _format_transcript(self, messages: list[dict]) -> str:
        lines = []
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Assistant"
            content = msg["content"]
            if len(content) > 2000:
                content = content[:1000] + "\n[... truncated ...]\n" + content[-1000:]
            lines.append(f"[{role}]: {content}\n")
        text = "".join(lines)
        if len(text) > self.max_input_chars:
            head = int(self.max_input_chars * 0.4)
            tail = self.max_input_chars - head - 60
            text = text[:head] + "\n\n[... middle of transcript truncated for length ...]\n\n" + text[-tail:]
        return text

    # ── LLM extraction ──────────────────────────────────────────────────────
    def _call_llm(self, transcript: str) -> list[dict]:
        import urllib.error
        import urllib.request

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": EXTRACTION_PROMPT + transcript}],
        }
        req = urllib.request.Request(
            f"{self.api_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_token}",
            },
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout_secs)
            body = json.loads(resp.read().decode("utf-8"))
            raw = body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            log.error(f"LLM API HTTP {e.code}: {e.read().decode()[:500]}")
            return []
        except Exception as e:
            log.error(f"LLM API call failed: {e}")
            return []
        return self._parse_cells(raw)

    def _parse_cells(self, raw: str) -> list[dict]:
        cells = []
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                cell = json.loads(line)
                if not isinstance(cell, dict):
                    continue
                if "type" in cell and "cell_type" not in cell:
                    cell["cell_type"] = cell.pop("type")
                cell.setdefault("cell_type", "fact")
                cell.setdefault("scene", "general")
                cell.setdefault("salience", 0.5)
                cell["salience"] = max(0.1, min(1.0, float(cell["salience"])))
                cells.append(cell)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
        return cells[: self.max_memories]

    # ── Sinks ───────────────────────────────────────────────────────────────
    def _store_cells(self, cells: list[dict], session: dict) -> int:
        """Store cells via the configured sink(s). Returns count stored to any sink."""
        scene_tag = f"hermes-{session.get('source') or 'session'}"
        for cell in cells:
            cell.setdefault("tags", [])
            if scene_tag not in cell["tags"]:
                cell["tags"].append(scene_tag)

        if self.sink in ("mcp", "both"):
            mcp_stored = self._store_mcp(cells)
        if self.sink in ("local", "both"):
            local_stored = self._store_local(cells)

        if self.sink == "mcp":
            return mcp_stored
        if self.sink == "local":
            return local_stored
        # both: prefer the larger count; warn on divergence
        if mcp_stored != local_stored:
            log.warning(f"sink divergence: mcp={mcp_stored} local={local_stored}")
        return max(mcp_stored, local_stored)

    def _store_mcp(self, cells: list[dict]) -> int:
        from .mcp_client import MCPClientError, store_cells

        payload = [{
            "content": cell.get("content", ""),
            "cell_type": cell.get("cell_type", "fact"),
            "scene": cell.get("scene", "general"),
            "salience": float(cell.get("salience", 0.5)),
            "tags": cell.get("tags", []),
            "owner_id": self.owner_id,
            "visibility": self.visibility,
        } for cell in cells]
        try:
            res = store_cells(self.mcp_url, payload, token=self.mcp_token, timeout=self.timeout_secs)
        except MCPClientError as e:
            log.error(f"  MCP push failed ({self.mcp_url}): {e}")
            return 0
        if res.get("errors"):
            log.warning(f"  MCP push: {res['errors']} cell(s) failed")
        return res.get("stored", 0)

    def _store_local(self, cells: list[dict]) -> int:
        from .db import MemoryDB

        db_path = os.environ.get("OC_MEMORY_DB", os.path.expanduser("~/.oc-memory/memory.db"))
        db = MemoryDB(db_path)
        stored = 0
        for cell in cells:
            try:
                db.insert_cell(cell, dedup=True)
                stored += 1
            except Exception as e:
                log.warning(f"  local store failed: {e}")
        return stored

    # ── Main extraction ─────────────────────────────────────────────────────
    def extract_session(self, session: dict) -> tuple[int, list[dict]]:
        messages = session.get("messages") or []
        log.info(f"Extracting session {session['id']} ({len(messages)} user/assistant msgs)")
        if len(messages) < self.min_messages:
            log.info(f"  Skipping — only {len(messages)} messages (min {self.min_messages})")
            return 0, []
        transcript = self._format_transcript(messages)
        if not transcript.strip():
            return 0, []
        cells = self._call_llm(transcript)
        return len(cells), cells

    def run(self, since_hours: int = 24, max_sessions: int = 5) -> dict:
        sessions = self.iter_sessions(since_hours=since_hours)
        src = "hermes CLI" if self.use_cli else "SQLite"
        log.info(f"Found {len(sessions)} unprocessed sessions in last {since_hours}h (via {src})")
        results = {"sessions_processed": 0, "total_cells": 0, "errors": 0}
        for session in sessions[:max_sessions]:
            try:
                count, cells = self.extract_session(session)
                if cells:
                    stored = self._store_cells(cells, session)
                    results["total_cells"] += stored
                    self._mark_processed(session["id"], session["message_count"], stored)
                    log.info(f"  → {stored} cells stored to '{self.sink}' (of {count} extracted)")
                else:
                    self._mark_processed(session["id"], session["message_count"], 0)
                    log.info("  → no cells extracted")
                results["sessions_processed"] += 1
            except Exception as e:
                log.error(f"  Session {session['id']}: {e}")
                results["errors"] += 1
        return results


# ── Local → MCP migration ───────────────────────────────────────────────────────

def migrate_local_to_mcp(
    db_path: str,
    mcp_url: str,
    mcp_token: str = "",
    owner_id: str = DEFAULT_OWNER_ID,
    visibility: str = DEFAULT_VISIBILITY,
    timeout_secs: int = DEFAULT_TIMEOUT_SECS,
    dry_run: bool = False,
) -> dict:
    """Push every cell from a local oc-memory SQLite DB to a remote MCP server.

    Used by the installer when a user switches an existing local store to the
    MCP-only sink. The local DB is only ever *read* — this is a non-destructive
    copy, so a failed push never loses data (the local cells remain intact).

    Returns {total, migrated, errors, ok, error}. ``ok`` is True only when every
    cell was pushed successfully; ``error`` carries a message on connect failure.
    """
    from .mcp_client import MCPClientError, store_cells

    cells = _read_all_cells(db_path)
    results = {"total": len(cells), "migrated": 0, "errors": 0, "ok": True, "error": ""}
    if dry_run or not cells:
        return results

    payload = []
    for cell in cells:
        tags = cell.get("tags")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                tags = [t for t in tags.split(",") if t]
        payload.append({
            "content": cell.get("content", ""),
            "cell_type": cell.get("cell_type", "fact"),
            "scene": cell.get("scene", "general"),
            "salience": float(cell.get("salience", 0.5)),
            "tags": tags or [],
            "owner_id": cell.get("owner_id") or owner_id,
            "visibility": cell.get("visibility") or visibility,
        })
    try:
        res = store_cells(mcp_url, payload, token=mcp_token, timeout=timeout_secs)
    except MCPClientError as e:
        # Connect/transport failure: nothing was migrated, local DB untouched.
        results["ok"] = False
        results["errors"] = len(payload)
        results["error"] = str(e)
        return results
    results["migrated"] = res.get("stored", 0)
    results["errors"] = res.get("errors", 0)
    results["ok"] = results["errors"] == 0 and results["migrated"] == results["total"]
    return results


def _read_all_cells(db_path: str) -> list[dict]:
    """Read all cells from a local oc-memory DB."""
    if not os.path.exists(db_path):
        return []
    try:
        from .db import MemoryDB
        return MemoryDB(db_path).all_cells()
    except Exception:
        # Defensive fallback if the MemoryDB schema/helper changes.
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(mem_cells)")}
            wanted = [c for c in ("content", "cell_type", "scene", "salience", "tags",
                                  "owner_id", "visibility") if c in cols]
            rows = conn.execute(f"SELECT {', '.join(wanted)} FROM mem_cells").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def cmd_extract_hermes(args: list[str] = None):
    import argparse

    p = argparse.ArgumentParser(description="Extract memories from Hermes sessions and push to a store")
    p.add_argument("--hermes-db", default=os.environ.get("OC_MEMORY_HERMES_DB", DEFAULT_HERMES_DB))
    p.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    p.add_argument("--source", default="", help="Filter Hermes sessions by source (slack, cron, cli, …)")
    p.add_argument("--no-cli", action="store_true", help="Force SQLite reads instead of the hermes CLI export API")
    # LLM (extraction) endpoint
    p.add_argument("--api-url", default=os.environ.get("OC_MEMORY_API_URL", DEFAULT_API_URL))
    p.add_argument("--api-token", default=os.environ.get("OC_MEMORY_API_TOKEN", DEFAULT_API_TOKEN))
    p.add_argument("--model", default=os.environ.get("OC_MEMORY_EXTRACT_MODEL", DEFAULT_MODEL))
    # Sink (push) config
    p.add_argument("--sink", choices=["mcp", "both", "local"],
                   default=os.environ.get("OC_MEMORY_HERMES_SINK", DEFAULT_SINK))
    p.add_argument("--mcp-url", default=os.environ.get("OC_MEMORY_MCP_URL", DEFAULT_MCP_URL))
    p.add_argument("--mcp-token", default=os.environ.get("OC_MEMORY_MCP_TOKEN", ""))
    p.add_argument("--owner-id", default=os.environ.get("OC_MEMORY_OWNER_ID", DEFAULT_OWNER_ID))
    p.add_argument("--visibility", choices=["private", "shared"], default=DEFAULT_VISIBILITY)
    # Run controls
    p.add_argument("--since-hours", type=int, default=24)
    p.add_argument("--max-sessions", type=int, default=5)
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_INPUT_CHARS)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECS)
    p.add_argument("--dry-run", action="store_true", help="List what would be processed; do not extract or store")
    opts = p.parse_args(args)

    extractor = HermesSessionExtractor(
        hermes_db=opts.hermes_db,
        state_file=opts.state_file,
        api_url=opts.api_url,
        api_token=opts.api_token,
        model=opts.model,
        sink=opts.sink,
        mcp_url=opts.mcp_url,
        mcp_token=opts.mcp_token,
        owner_id=opts.owner_id,
        visibility=opts.visibility,
        source_filter=opts.source,
        use_cli=not opts.no_cli,
        max_input_chars=opts.max_chars,
        timeout_secs=opts.timeout,
    )

    if not extractor.use_cli and not os.path.exists(opts.hermes_db):
        print(f"Hermes state.db not found at {opts.hermes_db} and `hermes` CLI not on PATH.")
        print("Run where Hermes lives, or pass --hermes-db /path/to/.hermes/state.db")
        return

    if opts.dry_run:
        sessions = extractor.iter_sessions(since_hours=opts.since_hours)
        src = "hermes CLI" if extractor.use_cli else "SQLite"
        print(f"[{src}] would process {len(sessions[:opts.max_sessions])} of {len(sessions)} sessions "
              f"→ sink={opts.sink} ({opts.mcp_url if opts.sink != 'local' else 'local db'}):")
        for s in sessions[: opts.max_sessions]:
            print(f"  {s['id']}: {len(s['messages'])} user/assistant msgs (source={s.get('source')})")
        return

    results = extractor.run(since_hours=opts.since_hours, max_sessions=opts.max_sessions)
    print(f"Processed {results['sessions_processed']} sessions, "
          f"stored {results['total_cells']} cells to '{opts.sink}', "
          f"{results['errors']} errors")


def cmd_migrate_to_mcp(args: list[str] = None):
    import argparse

    p = argparse.ArgumentParser(description="Migrate a local oc-memory DB to a remote MCP server")
    p.add_argument("--db", default=os.environ.get("OC_MEMORY_DB", os.path.expanduser("~/.oc-memory/memory.db")))
    p.add_argument("--mcp-url", default=os.environ.get("OC_MEMORY_MCP_URL", DEFAULT_MCP_URL))
    p.add_argument("--mcp-token", default=os.environ.get("OC_MEMORY_MCP_TOKEN", ""))
    p.add_argument("--owner-id", default=os.environ.get("OC_MEMORY_OWNER_ID", DEFAULT_OWNER_ID))
    p.add_argument("--visibility", choices=["private", "shared"], default=DEFAULT_VISIBILITY)
    p.add_argument("--dry-run", action="store_true")
    opts = p.parse_args(args)

    results = migrate_local_to_mcp(
        db_path=opts.db,
        mcp_url=opts.mcp_url,
        mcp_token=opts.mcp_token,
        owner_id=opts.owner_id,
        visibility=opts.visibility,
        dry_run=opts.dry_run,
    )
    verb = "Would migrate" if opts.dry_run else "Migrated"
    print(f"{verb} {results['migrated']}/{results['total']} cells "
          f"from {opts.db} → {opts.mcp_url} ({results['errors']} errors)")
    if results.get("error"):
        print(f"  error: {results['error']}")
    if opts.dry_run:
        return 0
    # Non-zero exit signals the installer (and shell callers) that the local
    # store was NOT fully migrated, so it's unsafe to drop the local copy.
    return 0 if results.get("ok") else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cmd_extract_hermes()
