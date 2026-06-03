"""
MCP configuration helpers for oc-memory.

Generates config snippets for Claude Code, Cursor, and OpenClaw.
"""

import json
import os
import sys
from pathlib import Path


def _get_python() -> str:
    """Return the best Python executable path."""
    venv = Path(__file__).parent.parent / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return sys.executable


def _get_oc_memory_cmd() -> list[str]:
    """Return the command to run the MCP server.

    Priority:
    1. oc-memory-mcp entry point (installed by pip/uv into the active env or PATH)
    2. .venv/bin/oc-memory-mcp (local editable install with a venv)
    3. python -m oc_memory.mcp_server (last resort)
    """
    import shutil

    # Check PATH for the pip-installed entry point
    if shutil.which("oc-memory-mcp"):
        return ["oc-memory-mcp"]

    # Check for a local .venv entry point alongside pyproject.toml
    venv_bin = Path(__file__).parent.parent / ".venv" / "bin" / "oc-memory-mcp"
    if venv_bin.exists():
        return [str(venv_bin)]

    # Fall back to running via Python module
    return [_get_python(), "-m", "oc_memory.mcp_server"]


def _get_env_vars() -> dict:
    """Return relevant environment variables."""
    env = {}
    for key in ["OC_MEMORY_DB", "OC_MEMORY_EXPORT", "OLLAMA_URL"]:
        val = os.environ.get(key)
        if val:
            env[key] = val
    return env


def claude_code_config() -> str:
    """Generate Claude Code MCP config snippet for ~/.claude.json or .claude/settings.json."""
    cmd = _get_oc_memory_cmd()
    env = _get_env_vars()
    config = {
        "mcpServers": {
            "oc-memory": {
                "command": cmd[0],
                "args": cmd[1:],
                "env": env,
            }
        }
    }
    return json.dumps(config, indent=2)


def cursor_config() -> str:
    """Generate Cursor MCP config snippet for .cursor/mcp.json."""
    cmd = _get_oc_memory_cmd()
    env = _get_env_vars()
    config = {
        "mcpServers": {
            "oc-memory": {
                "command": cmd[0],
                "args": cmd[1:],
                "env": env,
            }
        }
    }
    return json.dumps(config, indent=2)


def openclaw_config() -> str:
    """Generate OpenClaw MCP config snippet for openclaw.json."""
    cmd = _get_oc_memory_cmd()
    env = _get_env_vars()
    config = {
        "mcp": {
            "servers": [
                {
                    "name": "oc-memory",
                    "transport": "stdio",
                    "command": cmd[0],
                    "args": cmd[1:],
                    "env": env,
                }
            ]
        }
    }
    return json.dumps(config, indent=2)


def claude_code_http_config(url: str = "http://localhost:8765/mcp") -> str:
    """Generate Claude Code MCP config for Streamable HTTP transport."""
    config = {
        "mcpServers": {
            "oc-memory": {
                "type": "http",
                "url": url,
            }
        }
    }
    return json.dumps(config, indent=2)


def cursor_http_config(url: str = "http://localhost:8765/mcp") -> str:
    """Generate Cursor MCP config for Streamable HTTP transport."""
    config = {
        "mcpServers": {
            "oc-memory": {
                "type": "http",
                "url": url,
            }
        }
    }
    return json.dumps(config, indent=2)


def openclaw_http_config(url: str = "http://localhost:8765/mcp") -> str:
    """Generate OpenClaw MCP config for HTTP transport."""
    config = {
        "mcp": {
            "servers": [
                {
                    "name": "oc-memory",
                    "transport": "http",
                    "url": url,
                }
            ]
        }
    }
    return json.dumps(config, indent=2)


def print_setup_instructions():
    """Print setup instructions for all supported MCP clients."""
    print("=" * 70)
    print("oc-memory MCP Server Setup")
    print("=" * 70)
    print()

    print("─" * 70)
    print("TRANSPORT OPTIONS")
    print("─" * 70)
    print()
    print("  A) Docker / remote server  →  Streamable HTTP (recommended)")
    print("     Endpoint: http://localhost:8765/mcp   (POST, modern MCP spec)")
    print()
    print("  B) Local Python process    →  stdio (single-user, no container)")
    print()

    print("─" * 70)
    print("1. CLAUDE CODE  (~/.claude.json or .claude/settings.json)")
    print("─" * 70)
    print()
    print("  A) Docker / HTTP transport (recommended):")
    print()
    config_http = json.loads(claude_code_http_config())
    snippet = json.dumps(config_http["mcpServers"]["oc-memory"], indent=2)
    for line in snippet.splitlines():
        print("    " + line)
    print()
    print("  B) Local stdio transport:")
    print()
    config = json.loads(claude_code_config())
    snippet = json.dumps(config["mcpServers"]["oc-memory"], indent=2)
    for line in snippet.splitlines():
        print("    " + line)
    print()

    print("─" * 70)
    print("2. CURSOR  (.cursor/mcp.json or ~/.cursor/mcp.json)")
    print("─" * 70)
    print()
    print("  A) Docker / HTTP transport:")
    print()
    print(cursor_http_config())
    print()
    print("  B) Local stdio transport:")
    print()
    print(cursor_config())
    print()

    print("─" * 70)
    print("3. OPENCLAW  (openclaw.json)")
    print("─" * 70)
    print()
    print("  A) Docker / HTTP transport:")
    print()
    print(openclaw_http_config())
    print()
    print("  B) Local stdio transport:")
    print()
    print(openclaw_config())
    print()

    print("─" * 70)
    print("ENVIRONMENT VARIABLES (optional overrides)")
    print("─" * 70)
    print("  OC_MEMORY_DB       — Path to SQLite DB (default: ~/.oc-memory/memory.db)")
    print("  OC_MEMORY_EXPORT   — Export directory")
    print("  OLLAMA_URL             — Ollama endpoint for embeddings (default: http://localhost:11434)")
    print()

    print("─" * 70)
    print("AVAILABLE TOOLS (12)")
    print("─" * 70)
    tools = [
        ("memory_store", "Store a memory cell"),
        ("memory_search", "Hybrid search (vector + FTS)"),
        ("memory_search_tag", "Search by tag"),
        ("memory_forget", "Delete a cell by ID"),
        ("memory_tag", "Add tags to a cell"),
        ("memory_stats", "Database statistics"),
        ("memory_scenes", "List all scenes"),
        ("memory_scene", "Get scene details"),
        ("memory_decay", "Decay old low-access memories"),
        ("memory_export", "Export to markdown + JSON"),
        ("memory_digest", "Get daily digest"),
        ("memory_consolidate", "Consolidate scene summaries"),
    ]
    for name, desc in tools:
        print(f"  {name:<25} {desc}")
    print()

    print("─" * 70)
    print("TEST THE SERVER")
    print("─" * 70)
    print("  # List tools:")
    print('  echo \'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\' | oc-memory mcp-serve')
    print()
    print("  # Run tests:")
    print("  pytest tests/test_mcp_server.py -v")
    print()
