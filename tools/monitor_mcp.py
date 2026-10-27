"""Minimal MCP JSON-RPC stdio adapter for MonitorHub.

The adapter deliberately has no third-party dependencies. It exposes only
read operations and talks to the configured HTTP API rather than SQLite.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from .monitor_cli import fetch

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
            elif name == "monitor_records": value = fetch("/api/records", args, base_url=base_url)
            elif name == "monitor_query": value = fetch("/api/integrations/query", args, base_url=base_url)
            else: raise ValueError(f"Unknown tool: {name}")
            return {"jsonrpc": "2.0", "id": request_id, "result": _result(value)}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main() -> int:
    import os
    base_url = os.environ.get("MONITOR_BASE_URL")
    for line in sys.stdin:
        try:
            response = handle(json.loads(line), base_url)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
