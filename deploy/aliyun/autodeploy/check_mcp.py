"""Read-only, post-deployment smoke check. Keep business data out of CI logs."""
import json
from pathlib import Path
import subprocess
import sys


def check():
    root = Path(__file__).resolve().parents[3]
    messages = [
        {"id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "release-check", "version": "1"},
        }},
        {"method": "notifications/initialized"},
        {"id": 2, "method": "tools/list"},
    ]
    queries = [("monitor_overview", {}), ("monitor_brands", {"limit": 1}), ("monitor_records", {"limit": 1})]
    for request_id, (name, args) in enumerate(queries, start=3):
        messages.append({"id": request_id, "method": "tools/call", "params": {"name": name, "arguments": args}})
    process = subprocess.run(
        [sys.executable, "-B", str(root / "tools" / "monitor_mcp.py")],
        input="".join(json.dumps({"jsonrpc": "2.0", **m}) + "\n" for m in messages),
        capture_output=True, text=True, encoding="utf-8", timeout=120, check=True,
    )
    replies = {r["id"]: r for r in map(json.loads, process.stdout.splitlines())}
    for message in messages:
        if "id" in message and "result" not in replies.get(message["id"], {}):
            raise RuntimeError(f"MCP request {message['id']} failed")
    if replies[1]["result"]["serverInfo"]["name"] != "monitorhub":
        raise RuntimeError("Unexpected MCP server")
    tools = {tool["name"] for tool in replies[2]["result"]["tools"]}
    if not all(name in tools for name, _ in queries):
        raise RuntimeError("MCP tool discovery failed")
    counts = replies[3]["result"]["structuredContent"]["counts"]
    for request_id, dataset, field in [(4, "brands", "rows"), (5, "records", "records")]:
        rows = replies[request_id]["result"]["structuredContent"][field]
        if not isinstance(rows, list) or (counts[dataset] > 0 and not rows):
            raise RuntimeError(f"MCP {dataset} query returned inconsistent data")
        if rows and not rows[0].get("id"):
            raise RuntimeError(f"MCP {dataset} query returned an invalid row")
        print(f"MCP {dataset} query passed ({'row received' if rows else 'empty dataset'})")


if __name__ == "__main__":
    check()
