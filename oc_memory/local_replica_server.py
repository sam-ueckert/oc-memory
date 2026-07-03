#!/usr/bin/env python3
"""
Local hybrid MCP server: reads served from a Litestream-replicated local copy
of the database, writes forwarded to the canonical remote server.

Why: the canonical DB lives on a remote host (e.g. a Raspberry Pi k3s node)
reached over Tailscale/LAN. Both the network hop and Pi-class ONNX embedding
inference make reads slow. A `litestream restore -f` process (run separately,
see scripts/litestream-follow.sh) keeps a local copy of the SQLite file fresh
within a few seconds. This server:

  - Serves all READ tools (memory_search, memory_search_tag, memory_stats,
    memory_scenes, memory_scene, memory_digest, memory_export) against that
    local copy, using local embedding compute — no network round trip.
  - Forwards all WRITE tools (memory_store, memory_tag, memory_forget,
    memory_decay, memory_consolidate) to the remote canonical server over
    Streamable HTTP, so there is a single source of truth and no
    conflict-resolution logic. Reads may lag the follow interval (seconds)
    behind the most recent write — acceptable since writes are infrequent.

Usage:
    python -m oc_memory.local_replica_server            # stdio
    python -m oc_memory.local_replica_server --http     # Streamable HTTP + SSE

Environment variables:
    OC_MEMORY_LOCAL_DB     Path to the Litestream-restored replica file
                            (default: ~/.oc-memory/local-replica/memory.db)
    OC_MEMORY_REMOTE_URL   Streamable HTTP URL of the canonical remote server
                            (required for write tools; e.g.
                            http://100.119.254.83:30765/mcp)
    MCP_TRANSPORT=http     Enable HTTP transport
    MCP_PORT               Port for HTTP transport (default: 8765)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import mcp_server as upstream
from .mcp_client import MCPClientError, _StreamableHttpClient

LOCAL_DB_PATH = os.environ.get(
    "OC_MEMORY_LOCAL_DB", os.path.expanduser("~/.oc-memory/local-replica/memory.db")
)
REMOTE_URL = os.environ.get("OC_MEMORY_REMOTE_URL", "")

LOCAL_TOOLS = {
    "memory_search",
    "memory_search_tag",
    "memory_stats",
    "memory_scenes",
    "memory_scene",
    "memory_digest",
    # memory_export writes files to a directory on whatever host runs it —
    # route it locally so the export lands on this machine, not inside the
    # remote pod's filesystem. The local replica is a complete copy (modulo
    # follow-interval lag), so it's a valid source for a full export.
    "memory_export",
}
WRITE_TOOLS = {
    "memory_store",
    "memory_tag",
    "memory_forget",
    "memory_decay",
    "memory_consolidate",
}


def _log(*args, **kwargs):
    print(*args, **kwargs, file=sys.stderr)


# ── Local read path — reopen only when the replica file changes ─────────────

_local_cache = {"mtime": None, "db": None}


def get_local_db():
    """Return a MemoryDB bound to the replica file, refreshed when it changes.

    `litestream restore -f` replaces the file's contents in place as new
    segments arrive; we detect that via mtime rather than holding one
    connection open indefinitely, so a stale schema/cache is never served.
    """
    if not os.path.isfile(LOCAL_DB_PATH):
        raise RuntimeError(
            f"Local replica not found at {LOCAL_DB_PATH} — is `litestream restore -f` running?"
        )
    mtime = os.path.getmtime(LOCAL_DB_PATH)
    if _local_cache["db"] is None or _local_cache["mtime"] != mtime:
        from .db import MemoryDB

        try:
            _local_cache["db"] = MemoryDB(LOCAL_DB_PATH)
            _local_cache["mtime"] = mtime
        except Exception as e:
            # Litestream may be mid-write; fall back to the last-good connection
            # rather than fail the request outright.
            if _local_cache["db"] is None:
                raise
            _log(f"[local-replica] reopen failed ({e}), reusing last-good connection")
    return _local_cache["db"]


# Snapshot the original (local-DB-backed) handlers before rebinding anything
# below — route_tool() calls back into these for LOCAL_TOOLS.
_ORIGINAL_HANDLERS = dict(upstream.TOOL_HANDLERS)

# Every handler in _ORIGINAL_HANDLERS calls the module-global name `get_db()`,
# resolved from oc_memory.mcp_server's own namespace at call time — reassigning
# the attribute here redirects all of them to the local replica.
upstream.get_db = get_local_db


def _call_local(tool_name: str, args: dict) -> str:
    return _ORIGINAL_HANDLERS[tool_name](args)


# ── Remote write path ────────────────────────────────────────────────────────


def _call_remote(tool_name: str, args: dict) -> str:
    if not REMOTE_URL:
        raise RuntimeError(
            "OC_MEMORY_REMOTE_URL is not set — cannot forward write tool "
            f"'{tool_name}' to the canonical server."
        )
    with _StreamableHttpClient(REMOTE_URL) as client:
        result = client.call_tool(tool_name, args)
    content = result.get("content", [])
    return content[0]["text"] if content else "{}"


def route_tool(tool_name: str, args: dict) -> str:
    if tool_name in LOCAL_TOOLS:
        return _call_local(tool_name, args)
    if tool_name in WRITE_TOOLS:
        try:
            return _call_remote(tool_name, args)
        except MCPClientError as e:
            raise RuntimeError(f"remote write failed: {e}") from e
    raise ValueError(f"Unknown tool: {tool_name}")


# Patch the handler dispatch table so both stdio and HTTP transports (which
# call into `upstream`'s TOOL_HANDLERS / TOOLS / handle_request machinery)
# route through our local/remote split instead of a single local DB.
upstream.TOOL_HANDLERS = {name: (lambda args, _n=name: route_tool(_n, args)) for name in upstream.TOOL_NAMES}


def main():
    """Entry point — delegates to oc_memory.mcp_server's transport machinery,
    which now dispatches through route_tool() via the patched TOOL_HANDLERS."""
    Path(LOCAL_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    _log(f"[local-replica] local DB: {LOCAL_DB_PATH}")
    _log(f"[local-replica] remote (writes): {REMOTE_URL or '(not set — writes will fail)'}")
    upstream.main()


def main_stdio():
    os.environ.pop("MCP_TRANSPORT", None)
    main()


if __name__ == "__main__":
    main()
