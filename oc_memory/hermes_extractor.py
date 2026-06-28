"""Hermes session memory extractor.

Reads Hermes state.db, extracts user+assistant messages from recent sessions,
calls an LLM to extract structured memories, and stores them via oc-memory.

Designed for cron-driven batch extraction — complements the inline memory tool
by catching sessions where the agent didn't explicitly store key decisions.
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_HERMES_DB = os.path.expanduser("~/.hermes/state.db")
DEFAULT_STATE_FILE = os.path.expanduser("~/.oc-memory/hermes_extract_state.json")
DEFAULT_API_URL = "http://localhost:18789/v1"
DEFAULT_API_TOKEN = ""  # set via OC_MEMORY_API_TOKEN env var or --api-token
# Direct provider fallback (bypasses OpenClaw gateway) — set OC_MEMORY_API_URL=https://api.deepseek.com/v1
DEFAULT_DEEPSEEK_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "openclaw"  # routes through gateway; use deepseek-chat for direct DeepSeek API
DEFAULT_MAX_INPUT_CHARS = 30000
DEFAULT_MAX_TOKENS = 2000
DEFAULT_TIMEOUT_SECS = 120
DEFAULT_MAX_MEMORIES = 15
DEFAULT_MIN_MESSAGES = 6  # skip tiny sessions

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
    """Extract memories from Hermes session transcripts via LLM."""

    def __init__(
        self,
        hermes_db: str = DEFAULT_HERMES_DB,
        state_file: str = DEFAULT_STATE_FILE,
        api_url: str = DEFAULT_API_URL,
        api_token: str = DEFAULT_API_TOKEN,
        model: str = DEFAULT_MODEL,
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
        self.max_input_chars = max_input_chars
        self.max_tokens = max_tokens
        self.timeout_secs = timeout_secs
        self.max_memories = max_memories
        self.min_messages = min_messages

    # ── State file management ──────────────────────────────────────────────

    def _load_state(self) -> dict:
        """Load processed session tracker."""
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                return json.load(f)
        return {"processed": {}, "last_run": None}

    def _save_state(self, state: dict):
        """Persist processed session tracker."""
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def _is_processed(self, session_id: str, message_count: int) -> bool:
        """Check if a session has already been extracted."""
        state = self._load_state()
        entry = state["processed"].get(session_id)
        if entry is None:
            return False
        # Re-extract if session has grown significantly
        return entry.get("message_count", 0) >= message_count

    def _mark_processed(self, session_id: str, message_count: int, cells_stored: int):
        """Mark a session as processed."""
        state = self._load_state()
        state["processed"][session_id] = {
            "message_count": message_count,
            "cells_stored": cells_stored,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
        # Prune old entries (>90 days)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        state["processed"] = {
            k: v
            for k, v in state["processed"].items()
            if v.get("extracted_at", "") > cutoff
        }
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        self._save_state(state)

    # ── Hermes DB reading ──────────────────────────────────────────────────

    def _get_unprocessed_sessions(self, since_hours: int = 24) -> list[dict]:
        """Find Hermes sessions that haven't been extracted yet."""
        conn = sqlite3.connect(self.hermes_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        since_ts = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp()

        c.execute(
            """SELECT id, source, model, started_at, message_count
               FROM sessions
               WHERE started_at > ?
               ORDER BY started_at DESC""",
            (since_ts,),
        )
        sessions = []
        for row in c.fetchall():
            if not self._is_processed(row["id"], row["message_count"]):
                sessions.append(dict(row))
        conn.close()
        return sessions

    def _read_session_messages(self, session_id: str) -> list[dict]:
        """Read user+assistant messages from a session. Skips tool output."""
        conn = sqlite3.connect(self.hermes_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            """SELECT role, content, timestamp
               FROM messages
               WHERE session_id = ? AND role IN ('user', 'assistant')
               ORDER BY id""",
            (session_id,),
        )
        messages = []
        for row in c.fetchall():
            content = row["content"]
            if not content or not content.strip():
                continue
            # Skip heartbeat-only messages
            if content.strip() in ("NO_REPLY", "HEARTBEAT_OK"):
                continue
            if content.strip().startswith("Read HEARTBEAT"):
                continue
            messages.append({"role": row["role"], "content": content.strip()})
        conn.close()
        return messages

    # ── Text formatting ────────────────────────────────────────────────────

    def _format_transcript(self, messages: list[dict]) -> str:
        """Format messages into a compact transcript for LLM extraction."""
        lines = []
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Assistant"
            content = msg["content"]
            # Truncate very long messages
            if len(content) > 2000:
                content = content[:1000] + "\n[... truncated ...]\n" + content[-1000:]
            lines.append(f"[{role}]: {content}\n")
        text = "".join(lines)

        # Trim to max chars, keeping head and tail
        if len(text) > self.max_input_chars:
            head = int(self.max_input_chars * 0.4)
            tail = self.max_input_chars - head - 60
            text = text[:head] + "\n\n[... middle of transcript truncated for length ...]\n\n" + text[-tail:]

        return text

    # ── LLM call ───────────────────────────────────────────────────────────

    def _call_llm(self, transcript: str) -> list[dict]:
        """Send transcript to LLM, parse JSON response into cells."""
        import urllib.request
        import urllib.error

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0.3,
            "messages": [
                {"role": "user", "content": EXTRACTION_PROMPT + transcript}
            ],
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
        """Parse JSON lines from LLM response into cell dicts."""
        cells = []
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                cell = json.loads(line)
                if not isinstance(cell, dict):
                    continue
                # Normalize fields
                cell.setdefault("cell_type", "fact")
                cell.setdefault("scene", "general")
                cell.setdefault("salience", 0.5)
                # Clamp salience
                cell["salience"] = max(0.1, min(1.0, float(cell["salience"])))
                # Map 'type' to 'cell_type' for backwards compat
                if "type" in cell and "cell_type" not in cell:
                    cell["cell_type"] = cell.pop("type")
                cells.append(cell)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

        # Limit to max memories
        return cells[: self.max_memories]

    # ── Main extraction ────────────────────────────────────────────────────

    def extract_session(self, session: dict) -> tuple[int, list[dict]]:
        """Extract memories from a single session. Returns (count, cells)."""
        session_id = session["id"]
        log.info(f"Extracting session {session_id} ({session['message_count']} msgs)")

        messages = self._read_session_messages(session_id)
        if len(messages) < self.min_messages:
            log.info(f"  Skipping — only {len(messages)} messages (min {self.min_messages})")
            return 0, []

        transcript = self._format_transcript(messages)
        if not transcript.strip():
            return 0, []

        cells = self._call_llm(transcript)
        return len(cells), cells

    def run(self, since_hours: int = 24, max_sessions: int = 5) -> dict:
        """Main entry point — extract from recent unprocessed sessions.

        Returns dict with keys: sessions_processed, total_cells, errors
        """
        sessions = self._get_unprocessed_sessions(since_hours=since_hours)
        log.info(f"Found {len(sessions)} unprocessed sessions in last {since_hours}h")

        results = {"sessions_processed": 0, "total_cells": 0, "errors": 0}

        for session in sessions[:max_sessions]:
            try:
                count, cells = self.extract_session(session)
                if cells:
                    from .db import MemoryDB
                    import os as _os
                    db_path = _os.environ.get("OC_MEMORY_DB", _os.path.expanduser("~/.oc-memory/memory.db"))
                    db = MemoryDB(db_path)
                    stored = 0
                    for cell in cells:
                        try:
                            db.insert_cell(cell, dedup=True)
                            stored += 1
                        except Exception as e:
                            log.warning(f"  Failed to store cell: {e}")
                    results["total_cells"] += stored
                    self._mark_processed(session["id"], session["message_count"], stored)
                    log.info(f"  → {stored} cells stored (of {count} extracted)")
                else:
                    self._mark_processed(session["id"], session["message_count"], 0)
                    log.info(f"  → no cells extracted")

                results["sessions_processed"] += 1

            except Exception as e:
                log.error(f"  Session {session['id']}: {e}")
                results["errors"] += 1

        return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def cmd_extract_hermes(args: list[str] = None):
    """CLI entry point — wrapped by cli.py."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract memories from Hermes session transcripts"
    )
    parser.add_argument(
        "--hermes-db", default=DEFAULT_HERMES_DB, help="Path to Hermes state.db"
    )
    parser.add_argument(
        "--state-file", default=DEFAULT_STATE_FILE, help="State file for dedup tracking"
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("OC_MEMORY_API_URL", DEFAULT_API_URL),
        help="Chat completions API endpoint",
    )
    parser.add_argument(
        "--api-token",
        default=os.environ.get("OC_MEMORY_API_TOKEN", DEFAULT_API_TOKEN),
        help="API auth token",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OC_MEMORY_EXTRACT_MODEL", DEFAULT_MODEL),
        help="Model to use for extraction",
    )
    parser.add_argument(
        "--since-hours", type=int, default=24, help="Look back window in hours"
    )
    parser.add_argument(
        "--max-sessions", type=int, default=5, help="Max sessions to process per run"
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_INPUT_CHARS,
        help="Max transcript chars sent to LLM",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT_SECS, help="API timeout seconds"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Extract but don't store"
    )

    opts = parser.parse_args(args)

    extractor = HermesSessionExtractor(
        hermes_db=opts.hermes_db,
        state_file=opts.state_file,
        api_url=opts.api_url,
        api_token=opts.api_token,
        model=opts.model,
        max_input_chars=opts.max_chars,
        timeout_secs=opts.timeout,
    )

    if opts.dry_run:
        sessions = extractor._get_unprocessed_sessions(since_hours=opts.since_hours)
        print(f"Would process {len(sessions[:opts.max_sessions])} sessions:")
        for s in sessions[: opts.max_sessions]:
            msgs = extractor._read_session_messages(s["id"])
            print(f"  {s['id']}: {len(msgs)} user/assistant msgs ({s['message_count']} total)")
        return

    results = extractor.run(
        since_hours=opts.since_hours, max_sessions=opts.max_sessions
    )
    print(
        f"Processed {results['sessions_processed']} sessions, "
        f"stored {results['total_cells']} cells, "
        f"{results['errors']} errors"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cmd_extract_hermes()
