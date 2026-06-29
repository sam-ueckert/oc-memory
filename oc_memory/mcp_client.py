"""MCP client for pushing memory cells to a remote oc-memory / archy server.

Modern MCP only — uses the **Streamable HTTP** transport (``POST /mcp``). The
legacy SSE transport (``GET /sse``) is never used.

Primary path is the official MCP Python SDK (``streamablehttp_client`` +
``ClientSession``) — the canonical modern client. When the SDK isn't installed,
a stdlib-only fallback talks Streamable HTTP directly over urllib so the
extractor still runs on bare hosts with no extra dependencies.
"""

import json
import urllib.error
import urllib.request
from typing import Optional

PROTOCOL_VERSION = "2024-11-05"  # matches the oc-memory server's advertised version


class MCPClientError(RuntimeError):
    pass


# ── Public API ──────────────────────────────────────────────────────────────────

def store_cells(
    url: str,
    cells: list[dict],
    *,
    token: str = "",
    timeout: int = 60,
) -> dict:
    """Push cells to a remote MCP server's ``memory_store`` tool.

    Each cell is a dict with keys: content, cell_type, scene, salience, tags,
    owner_id, visibility. Returns {"stored": int, "errors": int}.

    Prefers the official MCP SDK (modern Streamable HTTP); falls back to the
    stdlib Streamable HTTP client if the SDK is unavailable.
    """
    try:
        return _store_via_sdk(url, cells, token=token, timeout=timeout)
    except ImportError:
        return _store_via_stdlib(url, cells, token=token, timeout=timeout)


# ── SDK path (preferred, modern MCP) ─────────────────────────────────────────────

def _store_via_sdk(url: str, cells: list[dict], *, token: str, timeout: int) -> dict:
    import asyncio

    # ImportError here propagates to store_cells() which falls back to stdlib.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def _run() -> dict:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        result = {"stored": 0, "errors": 0}
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for cell in cells:
                    try:
                        res = await session.call_tool("memory_store", cell)
                        if getattr(res, "isError", False):
                            result["errors"] += 1
                        else:
                            result["stored"] += 1
                    except Exception:
                        result["errors"] += 1
        return result

    return asyncio.run(_run())


# ── Stdlib fallback (Streamable HTTP over urllib) ────────────────────────────────

def _store_via_stdlib(url: str, cells: list[dict], *, token: str, timeout: int) -> dict:
    result = {"stored": 0, "errors": 0}
    try:
        with _StreamableHttpClient(url, timeout=timeout, token=token) as client:
            for cell in cells:
                try:
                    client.call_tool("memory_store", cell)
                    result["stored"] += 1
                except MCPClientError:
                    result["errors"] += 1
    except MCPClientError as e:
        raise MCPClientError(f"MCP connect failed ({url}): {e}") from e
    return result


class _StreamableHttpClient:
    """Single-session JSON-RPC over MCP Streamable HTTP (POST /mcp). No SSE transport."""

    def __init__(self, url: str, timeout: int = 60, token: str = ""):
        self.url = url
        self.timeout = timeout
        self.token = token
        self.session_id: Optional[str] = None
        self.protocol_version = PROTOCOL_VERSION
        self._id = 0

    def _headers(self) -> dict:
        # Streamable HTTP requires the client to accept both encodings; the
        # server may answer with application/json or an event-stream frame.
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
            h["MCP-Protocol-Version"] = self.protocol_version
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _post(self, body: dict, want_result: bool = True) -> Optional[dict]:
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"),
            headers=self._headers(), method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise MCPClientError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}") from e
        except Exception as e:
            raise MCPClientError(str(e)) from e
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        raw = resp.read().decode("utf-8", errors="replace")
        if not want_result or not raw.strip():
            return None
        return _parse_jsonrpc(raw)

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def connect(self) -> None:
        res = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "oc-memory-hermes-extractor", "version": "1.0"},
            },
        })
        if res and isinstance(res.get("result"), dict):
            self.protocol_version = res["result"].get("protocolVersion", self.protocol_version)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                   want_result=False)

    def call_tool(self, name: str, arguments: dict) -> dict:
        resp = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        if resp is None:
            raise MCPClientError("empty response to tools/call")
        if "error" in resp:
            raise MCPClientError(f"tools/call error: {resp['error']}")
        result = resp.get("result", {})
        if result.get("isError"):
            text = "".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
            raise MCPClientError(f"tool '{name}' error: {text[:300]}")
        return result

    def close(self) -> None:
        if not self.session_id:
            return
        try:
            urllib.request.urlopen(
                urllib.request.Request(self.url, headers=self._headers(), method="DELETE"),
                timeout=self.timeout,
            )
        except Exception:
            pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


def _parse_jsonrpc(raw: str) -> dict:
    """Decode a JSON-RPC reply from plain JSON or a Streamable-HTTP event frame."""
    text = raw.strip()
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    data_lines = [ln[5:].lstrip() for ln in text.splitlines() if ln.startswith("data:")]
    if not data_lines:
        raise MCPClientError(f"unparseable MCP response: {text[:200]}")
    return json.loads("".join(data_lines))
