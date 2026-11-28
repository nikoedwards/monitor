import io
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from zipfile import ZipFile

from tools.package_connect import ROOT, build


PACKAGE = ROOT / "downloads/monitor-connect-v1.zip"


class Overview(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/integrations/overview":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"read_only":true,"counts":{"brands":2,"records":7}}')

    def log_message(self, *args):
        pass


class ConnectorPackageTests(unittest.TestCase):
    def test_download_contains_current_source(self):
        with ZipFile(PACKAGE) as shipped, ZipFile(io.BytesIO(build())) as built:
            self.assertEqual(shipped.read("monitor.cmd"), built.read("monitor.cmd"))
            with ZipFile(io.BytesIO(shipped.read("monitor.pyz"))) as program, ZipFile(io.BytesIO(built.read("monitor.pyz"))) as expected:
                self.assertEqual(program.namelist(), expected.namelist())
                for path in program.namelist():
                    self.assertEqual(program.read(path), expected.read(path))

    def test_fresh_cli_and_desktop_connection_survive_removing_download(self):
        with tempfile.TemporaryDirectory(prefix="monitor guide ") as tmp, HTTPServer(("127.0.0.1", 0), Overview) as api:
            thread = threading.Thread(target=api.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{api.server_port}"
                home = Path(tmp)
                env = {**os.environ, "HOME": tmp, "USERPROFILE": tmp, "APPDATA": tmp, "LOCALAPPDATA": tmp,
                       "XDG_CONFIG_HOME": tmp, "PYTHONIOENCODING": "utf-8"}
                with ZipFile(PACKAGE) as archive:
                    image = Path(archive.extract("monitor.pyz", home / "download"))
                def run(*args, input=None):
                    return subprocess.run([sys.executable, *map(str, args)], input=input, cwd=tmp, env=env,
                                          capture_output=True, text=True, encoding="utf-8", check=True, timeout=15).stdout
                self.assertIn("连接成功", run(image, "connect", "cli", url))
                self.assertEqual(json.loads(run(image, "query", url, "--json", "overview"))["counts"]["brands"], 2)
                config = (home / "Library/Application Support" if sys.platform == "darwin" else home) / "Claude" / "claude_desktop_config.json"
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text('{"theme":"dark","mcpServers":{"existing":{}}}', encoding="utf-8")
                run(image, "connect", "desktop", url)
                run(image, "connect", "desktop", url)
                image.unlink()
                settings = json.loads(config.read_text(encoding="utf-8"))
                self.assertEqual(settings["theme"], "dark")
                self.assertIn("existing", settings["mcpServers"])
                bridge, = [entry for name, entry in settings["mcpServers"].items() if name.startswith("monitor-")]
                msg = {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"monitor_overview","arguments":{}}}
                reply = json.loads(run(*bridge["args"], input=json.dumps(msg) + "\n"))
                self.assertEqual(reply["result"]["structuredContent"]["counts"]["records"], 7)
            finally:
                api.shutdown()
                thread.join()
