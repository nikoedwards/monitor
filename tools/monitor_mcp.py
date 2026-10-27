"""Minimal MCP JSON-RPC stdio adapter for MonitorHub.

The adapter deliberately has no third-party dependencies. It exposes only
read operations and talks to the configured HTTP API rather than SQLite.
"""
from __future__ import annotations

import json
import sys
from typing import Any

try:  # support both ``python -m tools.monitor_mcp`` and direct execution
    from .monitor_cli import fetch
except ImportError:  # pragma: no cover - direct script convenience
    from monitor_cli import fetch

TOOLS = [
    {"name": "monitor_overview", "description": "Get MonitorHub dataset counts and freshness.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "monitor_brands", "description": "List monitored brands.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}}},
    {"name": "monitor_records", "description": "Search unified MonitorHub records.", "inputSchema": {"type": "object", "properties": {"brand_id": {"type": "string"}, "q": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}, "days": {"type": "integer"}, "limit": {"type": "integer", "maximum": 200}}}},
    {"name": "monitor_query", "description": "Query a read-only MonitorHub dataset.", "inputSchema": {"type": "object", "required": ["dataset"], "properties": {"dataset": {"type": "string"}, "brand_id": {"type": "string"}, "q": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}, "limit": {"type": "integer", "maximum": 200}}}},
]


def _result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "structuredContent": value}


def handle(message: dict[str, Any], base_url: str | None = None) -> dict[str, Any] | None:
    method, request_id = message.get("method"), message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "monitorhub", "version": "1"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}; name = params.get("name"); args = params.get("arguments") or {}
        try:
            if name == "monitor_overview": value = fetch("/api/integrations/overview", base_url=base_url)
            elif name == "monitor_brands": value = fetch("/api/integrations/query", {"dataset": "brands", "limit": args.get("limit", 200)}, base_url=base_url)
            elif name == "monitor_records":
                value = fetch("/api/records", {"limit": 50, **args}, base_url=base_url)
            elif name == "monitor_query":
                value = fetch("/api/integrations/query", {"limit": 50, **args}, base_url=base_url)
            else: raise ValueError(f"Unknown tool: {name}")
            return {"jsonrpc": "2.0", "id": request_id, "result": _result(value)}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main() -> int:
    import os
    base_url = os.environ.get("MONITOR_BASE_URL")
    # MCP messages are UTF-8 JSON.  Windows may expose stdio as the active
    # console code page (often GBK), which crashes when a record contains an
    # emoji or a zero-width joiner and makes the client report a misleading
    # 502.  Read and write bytes so the transport is independent of the shell
    # locale.
    input_stream = getattr(sys.stdin, "buffer", sys.stdin)
    output_stream = getattr(sys.stdout, "buffer", sys.stdout)

    def write(message: dict[str, Any]) -> None:
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        if hasattr(output_stream, "write"):
            try:
                output_stream.write(payload)
            except TypeError:  # StringIO-style streams used by tests.
                output_stream.write(payload.decode("utf-8"))
            output_stream.flush()

    for line in input_stream:
        try:
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            response = handle(json.loads(line), base_url)
            if response is not None:
                write(response)
        except Exception as exc:
            write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
