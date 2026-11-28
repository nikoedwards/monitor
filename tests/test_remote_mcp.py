import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import app as monitor
from server import db
from server import mcp_keys
from server.access_gate import AccessGate


class RemoteMcpTests(unittest.TestCase):
    def test_authenticated_http_tools_query_same_database_as_rest(self):
        headers = {"Accept": "application/json, text/event-stream"}
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        with tempfile.TemporaryDirectory() as folder, (
            patch.object(db, "DB_PATH", Path(folder) / "monitor.db")
        ), patch.object(monitor, "SCHEDULER_ENABLED", False), patch.object(monitor, "start_scheduler"):
            app = AccessGate(monitor.app, code="test-only")
            with TestClient(app, base_url="http://localhost", headers=headers) as client:
                self.assertEqual(client.post("/mcp", json=rpc).status_code, 401)
                self.assertEqual(client.post(mcp_keys.KEY_PATH).status_code, 401)
                client.post("/_access", data={"code": "test-only"}, follow_redirects=False)
                self.assertEqual(client.post(mcp_keys.KEY_PATH, headers={"Origin": "https://other.invalid"}).status_code, 403)
                issued = client.post(mcp_keys.KEY_PATH, headers={"Origin": "http://localhost"})
                self.assertIn("no-store", issued.headers["cache-control"])
                key = issued.json()
                self.assertFalse(mcp_keys.accepts(key["key"], b"rotated-site-code"))
                client.cookies.clear()
                client.headers["Authorization"] = f"Bearer {key['key']}"
                self.assertEqual(client.post("/mcp", json=rpc).status_code, 200)
                self.assertEqual(client.get("/api/integrations/overview").status_code, 401)
                self.assertEqual(client.post(mcp_keys.KEY_PATH).status_code, 401)
                client.headers["Authorization"] = "Bearer test-only"
                self.assertEqual(client.delete(f"{mcp_keys.KEY_PATH}/{key['id']}").status_code, 204)
                client.headers["Authorization"] = f"Bearer {key['key']}"
                self.assertEqual(client.post("/mcp", json=rpc).status_code, 401)
                client.headers["Authorization"] = "Bearer test-only"
                self.assertEqual(client.post("/mcp", json=rpc, headers={"Origin": "https://other.invalid"}).status_code, 403)
                handshake = client.post("/mcp", json={**rpc, "method": "initialize", "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                }}).json()["result"]
                client.headers["MCP-Protocol-Version"] = handshake["protocolVersion"]
                notice = {"jsonrpc": "2.0", "method": "notifications/initialized"}
                self.assertEqual(client.post("/mcp", json=notice).status_code, 202)
                listed = client.post("/mcp", json=rpc).json()["result"]["tools"]
                cases = {
                    "monitor_overview": ({}, "/api/integrations/overview"),
                    "monitor_brands": ({"limit": 1}, "/api/integrations/query?dataset=brands&limit=1"),
                    "monitor_records": ({"limit": 1}, "/api/records?limit=1"),
                    "monitor_query": ({"dataset": "products"}, "/api/integrations/query?dataset=products"),
                }
                self.assertEqual({tool["name"] for tool in listed}, set(cases))
                for name, (args, url) in cases.items():
                    with self.subTest(name=name):
                        message = {**rpc, "method": "tools/call", "params": {"name": name, "arguments": args}}
                        result = client.post("/mcp", json=message).json()["result"]
                        self.assertEqual(result["structuredContent"], client.get(url).json())
