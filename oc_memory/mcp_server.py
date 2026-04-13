#!/usr/bin/env python3
"""
MCP (Model Context Protocol) server for oc-memory.

Exposes memory operations as tools over stdio using JSON-RPC.
Supports the official MCP SDK if available, falls back to raw JSON-RPC.

Usage:
    python -m oc_memory.mcp_server           # stdio mode (default)
    python -m oc_memory.mcp_server --http    # HTTP/SSE mode
    MCP_TRANSPORT=http python -m oc_memory.mcp_server

Environment variables:
    MCP_TRANSPORT=http     Enable HTTP/SSE transport
    MCP_PORT=8765          Port for HTTP/SSE transport (default: 8765)
    OC_MEMORY_DB       Path to SQLite database
    OC_MEMORY_EXPORT   Export directory
    OLLAMA_URL             Ollama server URL for embeddings
"""

import json
import os
import sys
import traceback
from datetime import date as date_type
from pathlib import Path

# ── DB config ────────────────────────────────────────────────────────────────

DB_PATH = os.environ.get("OC_MEMORY_DB", os.path.expanduser("~/.oc-memory/memory.db"))
EXPORT_DIR = os.environ.get(
    "OC_MEMORY_EXPORT", os.path.expanduser("~/.oc-memory/export")
)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")


def _log(*args, **kwargs):
    """Log to stderr (stdout is the MCP channel)."""
    print(*args, **kwargs, file=sys.stderr)


# ── Lazy singletons ───────────────────────────────────────────────────────────

_db = None
_embedder = None


def get_db():
    global _db
    if _db is None:
        from .db import MemoryDB
        _db = MemoryDB(DB_PATH)
        _log(f"[mcp] MemoryDB initialized at {DB_PATH}")
    return _db


def get_embedder():
    global _embedder
    if _embedder is None:
        from .embeddings import EmbeddingClient
        # Use no-arg constructor → ONNX backend (Ollama is not configured)
        _embedder = EmbeddingClient()
    return _embedder


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "memory_store",
        "description": "Store a memory cell in the database.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The memory content to store"},
                "cell_type": {
                    "type": "string",
                    "description": "Type of memory cell",
                    "enum": ["fact", "decision", "preference", "task", "risk", "plan", "lesson"],
                    "default": "fact",
                },
                "scene": {
                    "type": "string",
                    "description": "Scene/context for the memory (e.g. 'project-alpha', 'personal')",
                    "default": "general",
                },
                "salience": {
                    "type": "number",
                    "description": "Importance score 0.1 (trivia) to 1.0 (critical)",
                    "default": 0.5,
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for the cell",
                    "default": [],
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory_search",
        "description": "Search memories using hybrid scoring (vector + FTS). Returns matching cells.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results", "default": 10, "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_search_tag",
        "description": "Search memories by tag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag to search for"},
                "limit": {"type": "integer", "description": "Max results", "default": 20, "minimum": 1, "maximum": 100},
            },
            "required": ["tag"],
        },
    },
    {
        "name": "memory_forget",
        "description": "Delete a memory cell by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Cell ID to delete"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "memory_tag",
        "description": "Add tags to an existing memory cell.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Cell ID to tag"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to add",
                },
            },
            "required": ["id", "tags"],
        },
    },
    {
        "name": "memory_stats",
        "description": "Return memory database statistics.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "memory_scenes",
        "description": "List all memory scenes.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "memory_scene",
        "description": "Get details for a specific scene, including all its cells.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scene": {"type": "string", "description": "Scene name"},
            },
            "required": ["scene"],
        },
    },
    {
        "name": "memory_decay",
        "description": "Decay old low-access memories by reducing their salience.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days_old": {"type": "integer", "description": "Age threshold in days", "default": 30},
                "decay_factor": {"type": "number", "description": "Decay multiplier (0-1)", "default": 0.9},
            },
        },
    },
    {
        "name": "memory_export",
        "description": "Export memories to markdown + JSON files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "export_dir": {
                    "type": "string",
                    "description": "Export directory (default: OC_MEMORY_EXPORT env var)",
                },
            },
        },
    },
    {
        "name": "memory_digest",
        "description": "Get the daily digest for a given date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "ISO date string (YYYY-MM-DD). Defaults to today.",
                },
            },
        },
    },
    {
        "name": "memory_consolidate",
        "description": "Consolidate scenes by generating summaries. Operates on one scene or all scenes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scene": {"type": "string", "description": "Scene to consolidate. Omit to consolidate all."},
            },
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


# ── Tool implementations ──────────────────────────────────────────────────────

def tool_memory_store(args: dict) -> str:
    content = args.get("content", "")
    if not content:
        raise ValueError("content is required")
    cell = {
        "content": content,
        "cell_type": args.get("cell_type", "fact"),
        "scene": args.get("scene", "general"),
        "salience": float(args.get("salience", 0.5)),
        "tags": args.get("tags", []),
    }
    db = get_db()
    embedder = get_embedder()
    emb = None
    if embedder.is_available():
        try:
            emb = embedder.embed(content)
        except Exception as e:
            _log(f"[mcp] embedding failed: {e}")
    row_id = db.insert_cell(cell, embedding=emb)
    return json.dumps({"id": row_id, "scene": cell["scene"], "cell_type": cell["cell_type"], "stored": True})


def tool_memory_search(args: dict) -> str:
    query = args.get("query", "")
    if not query:
        raise ValueError("query is required")
    limit = int(args.get("limit") or 10)
    db = get_db()
    embedder = get_embedder()
    results = []
    used_vector = False
    if embedder.is_available():
        try:
            qemb = embedder.embed(query)
            results = db.search_vector(qemb, limit=limit)
            used_vector = True
        except Exception as e:
            _log(f"[mcp] vector search failed: {e}")
    if not results:
        results = db.search_fts(query, limit=limit)
    out = []
    for r in results:
        out.append({
            "id": r["id"],
            "scene": r["scene"],
            "cell_type": r["cell_type"],
            "salience": r["salience"],
            "content": r["content"],
            "tags": r.get("tags", "[]"),
            "similarity": r.get("similarity"),
        })
    return json.dumps({"results": out, "count": len(out), "method": "vector" if used_vector else "fts"})


def tool_memory_search_tag(args: dict) -> str:
    tag = args.get("tag", "")
    if not tag:
        raise ValueError("tag is required")
    limit = int(args.get("limit") or 20)
    db = get_db()
    results = db.search_by_tag(tag, limit=limit)
    out = [{"id": r["id"], "scene": r["scene"], "cell_type": r["cell_type"],
            "salience": r["salience"], "content": r["content"], "tags": r.get("tags", "[]")}
           for r in results]
    return json.dumps({"results": out, "count": len(out)})


def tool_memory_forget(args: dict) -> str:
    cell_id = args.get("id")
    if cell_id is None:
        raise ValueError("id is required")
    cell_id = int(cell_id)
    db = get_db()
    # Check cell exists
    row = db.db.execute("SELECT id FROM mem_cells WHERE id = ?", (cell_id,)).fetchone()
    if not row:
        raise ValueError(f"Cell {cell_id} not found")
    db.delete_cell(cell_id)
    return json.dumps({"id": cell_id, "deleted": True})


def tool_memory_tag(args: dict) -> str:
    cell_id = args.get("id")
    tags = args.get("tags", [])
    if cell_id is None:
        raise ValueError("id is required")
    if not tags:
        raise ValueError("tags must be a non-empty list")
    cell_id = int(cell_id)
    db = get_db()
    db.tag_cell(cell_id, tags)
    return json.dumps({"id": cell_id, "tags_added": tags})


def tool_memory_stats(args: dict) -> str:
    db = get_db()
    return json.dumps(db.stats())


def tool_memory_scenes(args: dict) -> str:
    db = get_db()
    scenes = db.list_scenes()
    return json.dumps({"scenes": [dict(s) for s in scenes], "count": len(scenes)})


def tool_memory_scene(args: dict) -> str:
    scene = args.get("scene", "")
    if not scene:
        raise ValueError("scene is required")
    db = get_db()
    info, cells = db.get_scene(scene)
    if not info and not cells:
        return json.dumps({"scene": scene, "found": False, "cells": []})
    return json.dumps({
        "scene": scene,
        "found": True,
        "summary": info["summary"] if info else "",
        "cells": [{"id": c["id"], "cell_type": c["cell_type"], "salience": c["salience"],
                   "content": c["content"], "tags": c.get("tags", "[]")} for c in cells],
        "cell_count": len(cells),
    })


def tool_memory_decay(args: dict) -> str:
    days_old = int(args.get("days_old") or 30)
    decay_factor = float(args.get("decay_factor", 0.9))
    db = get_db()
    affected = db.decay(days_old=days_old, decay_factor=decay_factor)
    return json.dumps({"decayed": affected, "days_old": days_old, "decay_factor": decay_factor})


def tool_memory_export(args: dict) -> str:
    export_dir = args.get("export_dir", EXPORT_DIR)
    db = get_db()
    from .backup import BackupManager
    backup = BackupManager(db, export_dir)
    n_scenes = backup.export_markdown()
    json_path = backup.export_json()
    return json.dumps({"exported_scenes": n_scenes, "json_path": str(json_path), "export_dir": str(export_dir)})


def tool_memory_digest(args: dict) -> str:
    target_date = args.get("date") or date_type.today().isoformat()
    db = get_db()
    scene_name = f"digest-{target_date}"
    _, cells = db.get_scene(scene_name)
    if not cells:
        cells = db.search_by_tag("digest")
        cells = [c for c in cells if target_date in c.get("scene", "")]
    if not cells:
        return json.dumps({"date": target_date, "found": False, "digest": None})
    return json.dumps({
        "date": target_date,
        "found": True,
        "digest": cells[0]["content"] if cells else None,
        "cells": [{"id": c["id"], "content": c["content"]} for c in cells],
    })


def tool_memory_consolidate(args: dict) -> str:
    scene_name = args.get("scene")
    db = get_db()
    results = []
    if scene_name:
        scenes_to_process = [scene_name]
    else:
        rows = db.db.execute("SELECT DISTINCT scene FROM mem_cells").fetchall()
        scenes_to_process = [r[0] for r in rows]

    for scene in scenes_to_process:
        _, cells = db.get_scene(scene)
        if not cells:
            continue
        top = sorted(cells, key=lambda c: c.get("salience", 0.5), reverse=True)[:10]
        summary = "; ".join(c["content"][:100] for c in top)[:300]
        db.upsert_scene(scene, summary, None)
        results.append({"scene": scene, "cell_count": len(cells), "summary": summary[:80]})

    return json.dumps({"consolidated": len(results), "scenes": results})


TOOL_HANDLERS = {
    "memory_store": tool_memory_store,
    "memory_search": tool_memory_search,
    "memory_search_tag": tool_memory_search_tag,
    "memory_forget": tool_memory_forget,
    "memory_tag": tool_memory_tag,
    "memory_stats": tool_memory_stats,
    "memory_scenes": tool_memory_scenes,
    "memory_scene": tool_memory_scene,
    "memory_decay": tool_memory_decay,
    "memory_export": tool_memory_export,
    "memory_digest": tool_memory_digest,
    "memory_consolidate": tool_memory_consolidate,
}


# ── MCP JSON-RPC protocol ─────────────────────────────────────────────────────

def make_response(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def handle_request(req: dict) -> dict | None:
    """Handle a single JSON-RPC request. Returns None for notifications."""
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    # Notifications (no id) — handle but don't respond
    if req_id is None:
        _log(f"[mcp] notification: {method}")
        return None

    try:
        if method == "initialize":
            return make_response(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "oc-memory",
                    "version": "0.1.0",
                },
            })

        elif method == "tools/list":
            return make_response(req_id, {"tools": TOOLS})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            if tool_name not in TOOL_HANDLERS:
                return make_error(req_id, -32601, f"Unknown tool: {tool_name}")

            handler = TOOL_HANDLERS[tool_name]
            try:
                result_text = handler(tool_args)
                return make_response(req_id, {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": False,
                })
            except ValueError as e:
                return make_response(req_id, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })
            except Exception as e:
                _log(f"[mcp] tool error ({tool_name}): {e}\n{traceback.format_exc()}")
                return make_response(req_id, {
                    "content": [{"type": "text", "text": f"Internal error: {e}"}],
                    "isError": True,
                })

        elif method == "ping":
            return make_response(req_id, {})

        else:
            return make_error(req_id, -32601, f"Method not found: {method}")

    except Exception as e:
        _log(f"[mcp] request handler error: {e}\n{traceback.format_exc()}")
        return make_error(req_id, -32603, f"Internal error: {e}")


def _write_response(resp: dict):
    """Write a JSON-RPC response to stdout."""
    line = json.dumps(resp)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def run_stdio():
    """Main stdio JSON-RPC loop."""
    _log("[mcp] oc-memory MCP server starting (stdio)")
    # Eagerly init DB
    try:
        get_db()
    except Exception as e:
        _log(f"[mcp] WARNING: DB init failed: {e}")

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError as e:
            _write_response(make_error(None, -32700, f"Parse error: {e}"))
            continue

        resp = handle_request(req)
        if resp is not None:
            _write_response(resp)

    _log("[mcp] stdin closed, exiting")


# ── Try official MCP SDK first (stdio) ────────────────────────────────────────

def _try_mcp_sdk_stdio():
    """Attempt to use the official MCP SDK over stdio. Returns True if successful."""
    try:
        import asyncio
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        import mcp.types as types

        server = Server("oc-memory")

        @server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return [
                types.Tool(
                    name=t["name"],
                    description=t["description"],
                    inputSchema=t["inputSchema"],
                )
                for t in TOOLS
            ]

        @server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
            if name not in TOOL_HANDLERS:
                raise ValueError(f"Unknown tool: {name}")
            try:
                result = TOOL_HANDLERS[name](arguments)
                return [types.TextContent(type="text", text=result)]
            except Exception as e:
                return [types.TextContent(type="text", text=f"Error: {e}")]

        async def main():
            _log("[mcp] Using official MCP SDK (stdio)")
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

        asyncio.run(main())
        return True
    except ImportError:
        return False
    except Exception as e:
        _log(f"[mcp] SDK error: {e}, falling back to raw JSON-RPC")
        return False


# ── HTTP/SSE transport ────────────────────────────────────────────────────────

def run_http(host: str = "0.0.0.0", port: int = 8765):
    """Run the MCP server with HTTP/SSE transport using FastMCP."""
    import asyncio

    try:
        from mcp.server.fastmcp import FastMCP
        from starlette.requests import Request
        from starlette.responses import JSONResponse
    except ImportError as e:
        _log(f"[mcp] HTTP mode requires mcp[cli] with uvicorn/starlette: {e}")
        sys.exit(1)

    _log(f"[mcp] oc-memory MCP server starting (HTTP/SSE on {host}:{port})")

    # Eagerly init DB
    try:
        get_db()
    except Exception as e:
        _log(f"[mcp] WARNING: DB init failed: {e}")

    mcp = FastMCP("oc-memory", host=host, port=port)

    # Register health check endpoint
    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "oc-memory"})

    # Register all tools with typed signatures derived from inputSchema
    for tool_def in TOOLS:
        tool_name = tool_def["name"]
        handler = TOOL_HANDLERS[tool_name]
        description = tool_def["description"]
        input_schema = tool_def.get("inputSchema", {"type": "object", "properties": {}})
        props = input_schema.get("properties", {})
        required_fields = input_schema.get("required", [])

        # Map JSON schema type to a simple Python type name (no Optional to avoid typing issues)
        type_map = {"string": "str", "integer": "int", "boolean": "bool",
                    "number": "float", "array": "list", "object": "dict"}

        param_parts = []
        for pname, pdef in props.items():
            pytype = type_map.get(pdef.get("type", "string"), "str")
            if pname not in required_fields:
                param_parts.append(f"{pname}: {pytype} = None")
            else:
                param_parts.append(f"{pname}: {pytype}")

        params_str = ", ".join(param_parts) if param_parts else ""
        all_param_names = list(props.keys())
        # Use **kwargs passthrough to avoid Pydantic coercion issues with optional params.
        # Build an explicit args dict filtering out None values so handlers see only
        # what was actually provided.
        all_names_repr = repr(all_param_names)
        func_code = (
            f"async def {tool_name}({params_str}) -> str:\n"
            f"    _all = {all_names_repr}\n"
            f"    _locals = locals()\n"
            f"    _args = {{k: _locals[k] for k in _all if _locals.get(k) is not None}}\n"
            f"    return _handler(_args)\n"
        )
        namespace = {"_handler": handler}
        exec(compile(func_code, "<mcp_tool>", "exec"), namespace)
        tool_func = namespace[tool_name]
        tool_func.__doc__ = description

        registered = mcp._tool_manager.add_tool(
            tool_func,
            name=tool_name,
            description=description,
        )
        registered.parameters = input_schema

    _log(f"[mcp] Registered {len(TOOLS)} tools")
    _log(f"[mcp] SSE endpoint: http://{host}:{port}/sse")
    _log(f"[mcp] Health check: http://{host}:{port}/health")

    asyncio.run(mcp.run_sse_async())


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    """Entry point: parse args/env to choose transport."""
    import argparse

    parser = argparse.ArgumentParser(description="oc-memory MCP server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Use HTTP/SSE transport instead of stdio",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_PORT", "8765")),
        help="Port for HTTP/SSE transport (default: 8765, or MCP_PORT env var)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host for HTTP/SSE transport (default: 0.0.0.0)",
    )
    args = parser.parse_args()

    use_http = args.http or os.environ.get("MCP_TRANSPORT", "").lower() == "http"

    if use_http:
        run_http(host=args.host, port=args.port)
    else:
        # stdio mode: try MCP SDK, fall back to raw JSON-RPC
        if not _try_mcp_sdk_stdio():
            _log("[mcp] Using raw JSON-RPC stdio (mcp SDK not available)")
            run_stdio()


def main_stdio():
    """Entry point for stdio-only mode (used by Claude Code / Cursor MCP config)."""
    # Force stdio mode
    os.environ.pop("MCP_TRANSPORT", None)
    main()


if __name__ == "__main__":
    main()
