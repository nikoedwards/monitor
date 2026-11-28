"""Exercise remote discovery and the overview without logging data or credentials."""
import json
import os
import sys
from urllib.request import HTTPRedirectHandler, Request, build_opener


def check_tools(url, code):
    opener = build_opener(HTTPRedirectHandler)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    def send(method, params, request_id=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        request = Request(url, data=json.dumps(payload).encode(), headers=headers)
        if code:
            request.add_unredirected_header("Authorization", f"Bearer {code}")
        with opener.open(request, timeout=20) as response:
            if request_id is None and response.status == 202:
                return
            return json.load(response)["result"]

    handshake = send("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                    "clientInfo": {"name": "release-check", "version": "1"}}, 1)
    headers["MCP-Protocol-Version"] = handshake["protocolVersion"]
    send("notifications/initialized", {})
    names = {item["name"] for item in send("tools/list", {}, 2)["tools"]}
    if names != {"monitor_brands", "monitor_records", "monitor_query", "monitor_overview"}:
        raise RuntimeError("Remote MCP tool discovery failed")
    result = send("tools/call", {"name": "monitor_overview", "arguments": {}}, 3)
    overview = result["structuredContent"]
    if overview.get("read_only") is not True or not all(
        isinstance(overview["counts"][key], int) for key in ("brands", "records")
    ):
        raise RuntimeError("Remote MCP overview failed")
    print("Remote MCP discovery and overview passed")


def check(url):
    site_code = os.environ.get("MONITOR_ACCESS_CODE")
    if not site_code:
        return check_tools(url, None)
    keys_url = url.removesuffix("/mcp") + "/api/integrations/mcp-keys"
    opener = build_opener(HTTPRedirectHandler)
    issue = Request(keys_url, method="POST")
    issue.add_unredirected_header("Authorization", f"Bearer {site_code}")
    with opener.open(issue, timeout=20) as response:
        issued = json.load(response)
    try:
        if not issued["key"].startswith("mon_"):
            raise RuntimeError("Expected a dedicated MCP key")
        check_tools(url, issued["key"])
    finally:
        revoke = Request(keys_url + "/" + issued["id"], method="DELETE")
        revoke.add_unredirected_header("Authorization", f"Bearer {site_code}")
        with opener.open(revoke, timeout=20) as response:
            if response.status != 204:
                raise RuntimeError("Could not retire the release check key")


if __name__ == "__main__":
    check(sys.argv[1])
