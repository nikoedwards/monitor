"""Low-memory Chromium capture without a second, Node.js browser driver."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from .snapshot import (
    CAPTURE_EMERGENCY_MAX_STITCH_HEIGHT,
    CAPTURE_EMERGENCY_VIEWPORT_WIDTH,
    CAPTURE_MAX_STITCH_HEIGHT,
    CAPTURE_MIN_AVAILABLE_MB,
    SnapshotCaptureError,
    _available_memory_mb,
    _browser_candidates,
    _capture_chunk_height,
    _capture_chunked_screenshot,
    _capture_error_reason,
    _kill_capture_process,
    _write_fallback_archive,
)

CDP_STARTUP_TIMEOUT_SECONDS = int(os.environ.get("MONITOR_CAPTURE_CDP_STARTUP_TIMEOUT_SECONDS", "20"))


def _browser_binary() -> str:
    explicit = os.environ.get("SNAPSHOT_BROWSER", "").strip()
    if explicit:
        return explicit
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright"))
    for pattern in (
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
        "chromium-*/chrome-linux64/chrome",
        "chromium-*/chrome-win64/chrome.exe",
    ):
        binaries = sorted(root.glob(pattern), reverse=True)
        if binaries:
            return str(binaries[0])
    candidates = _browser_candidates()
    if candidates:
        return candidates[0]
    raise RuntimeError("No Chromium executable available for low-memory capture")


class _Page:
    """The small page surface needed by the shared screenshot stitcher."""

    def __init__(self, socket):
        self.socket = socket
        self.sequence = 0
        self.session = None
        self.status = None
        self.frame = None
        self.source_html = None
        self.source_served = False

    def _send(self, method, params):
        self.sequence += 1
        message = {"id": self.sequence, "method": method, "params": params}
        if self.session:
            message["sessionId"] = self.session
        self.socket.send(json.dumps(message))
        return self.sequence

    def call(self, method, params=None, timeout=12):
        identifier = self._send(method, params or {})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.socket.settimeout(max(0.1, deadline - time.monotonic()))
            message = json.loads(self.socket.recv())
            if message.get("id") == identifier:
                if message.get("error"):
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            # Ignore another target's events. Commands share the browser
            # connection, but the attached page is our only capture source.
            if self.session and message.get("sessionId") != self.session:
                continue
            event = message.get("method")
            value = message.get("params", {})
            if event == "Page.frameNavigated" and not value["frame"].get("parentId"):
                self.frame = value["frame"]["id"]
            elif event == "Network.responseReceived" and value.get("type") == "Document":
                if value.get("frameId") == self.frame:
                    self.status = int(value["response"]["status"])
            elif event == "Fetch.requestPaused":
                if self.source_html is not None and not self.source_served:
                    self.source_served = True
                    self._send("Fetch.fulfillRequest", {
                        "requestId": value["requestId"], "responseCode": 200,
                        "responseHeaders": [{"name": "Content-Type", "value": "text/html; charset=utf-8"}],
                        "body": base64.b64encode(self.source_html.encode()).decode(),
                    })
                else:
                    self._send("Fetch.continueRequest", {"requestId": value["requestId"]})
        raise TimeoutError(f"Chromium command timed out: {method}")

    def evaluate(self, expression, *args):
        if args or expression.lstrip().startswith(("()", "(scrollY)")):
            expression = f"({expression})({','.join(json.dumps(x) for x in args)})"
        response = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        if response.get("exceptionDetails"):
            raise RuntimeError(str(response["exceptionDetails"])[:300])
        return response.get("result", {}).get("value")

    def wait_for_timeout(self, milliseconds):
        time.sleep(milliseconds / 1000)

    def screenshot(self, path, **_options):
        result = self.call("Page.captureScreenshot", {
            "format": "png", "captureBeyondViewport": False, "fromSurface": True,
        }, timeout=15)
        Path(path).write_bytes(base64.b64decode(result["data"], validate=True))

    def navigate(self, url):
        self.status = None
        result = self.call("Page.navigate", {"url": url}, timeout=20)
        if result.get("errorText"):
            raise SnapshotCaptureError(result["errorText"])
        deadline = time.monotonic() + 15
        data = {}
        while time.monotonic() < deadline:
            data = self.evaluate("""() => ({
              final_url: location.href, title: document.title,
              text: (document.body?.innerText || '').slice(0, 200000),
              ready: document.readyState
            })""") or {}
            if data.get("text", "").strip() and data.get("ready") != "loading":
                break
            self.wait_for_timeout(200)
        reason = _capture_error_reason(self.status, data.get("final_url", ""), data.get("text", ""))
        if reason or not data.get("text", "").strip():
            raise SnapshotCaptureError(reason or "Chromium rendered an empty page")
        return data


def capture(
    url: str, png_path: Path, html_path: Path, source_html="", *,
    safe_mode=True, scriptless=False,
):
    import websocket

    memory = _available_memory_mb()
    emergency = memory is None or memory < CAPTURE_MIN_AVAILABLE_MB
    constrained = memory is None or memory < int(
        os.environ.get("MONITOR_CAPTURE_SAFE_MEMORY_MB", "768")
    )
    width = max(
        320,
        min(CAPTURE_EMERGENCY_VIEWPORT_WIDTH if constrained else 1440, 1440),
    )
    height = max(
        240,
        min(
            _capture_chunk_height(0 if memory is None else memory),
            420 if constrained else 900,
        ),
    )
    browser = None
    socket = None
    stage = "launch"
    # The outer isolated worker owns a process group. Keep Chromium in it so
    # the supervisor's timeout/SIGKILL reaches every browser child as well.
    # Standalone diagnostics need their own browser group for the same reason.
    owns_browser_group = os.name != "posix" or os.getpgrp() != os.getpid()
    try:
        # Chromium can leave cache files briefly locked after a forced kill
        # (especially on Windows, where taskkill must tear down the whole
        # child tree).  The PNG is already durable before cleanup starts, so
        # do not turn a successful capture into an error merely because a
        # disposable profile cannot be removed immediately.
        with tempfile.TemporaryDirectory(
            prefix="monitor-cdp-", ignore_cleanup_errors=True,
        ) as profile:
            with tempfile.TemporaryFile() as browser_log:
                browser_args = [
                    _browser_binary(), "--headless", "--no-sandbox", "--disable-gpu",
                    "--disable-dev-shm-usage", "--disable-background-networking",
                    "--disable-extensions", "--disable-sync", "--no-first-run", "--mute-audio",
                    "--renderer-process-limit=1",
                    "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                    f"--user-data-dir={profile}", f"--window-size={width},{height}", "about:blank",
                ]
                if constrained:
                    browser_args.append("--js-flags=--max-old-space-size=128")
                browser = subprocess.Popen(
                    browser_args, stdout=subprocess.DEVNULL, stderr=browser_log,
                    start_new_session=owns_browser_group,
                )
                try:
                    port_file = Path(profile) / "DevToolsActivePort"
                    deadline = time.monotonic() + CDP_STARTUP_TIMEOUT_SECONDS
                    while not port_file.is_file():
                        if browser.poll() is not None:
                            browser_log.seek(0)
                            raise RuntimeError(browser_log.read().decode(errors="replace")[-500:])
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                f"Chromium DevTools did not start within {CDP_STARTUP_TIMEOUT_SECONDS} seconds"
                            )
                        time.sleep(0.05)
                    port, endpoint = port_file.read_text().splitlines()[:2]
                    socket = websocket.create_connection(
                        f"ws://127.0.0.1:{port}{endpoint}", timeout=6,
                        suppress_origin=True, enable_multithread=False, http_no_proxy=["127.0.0.1"],
                    )
                    page = _Page(socket)
                    target = page.call("Target.createTarget", {"url": "about:blank"})["targetId"]
                    page.session = page.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
                    page.call("Page.enable")
                    page.call("Network.enable", {"maxTotalBufferSize": 0, "maxResourceBufferSize": 0})
                    page.frame = page.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
                    page.call("Emulation.setDeviceMetricsOverride", {
                        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False,
                    })
                    page.call("Network.setBlockedURLs", {
                        "urls": ["*.mp4", "*.webm", "*.m3u8", "*.gif"],
                    })
                    if scriptless:
                        page.call("Emulation.setScriptExecutionDisabled", {"value": True})
                    stage = "navigation"
                    source_fallback = False
                    try:
                        data = page.navigate(url)
                    except SnapshotCaptureError:
                        if not source_html:
                            raise
                        page.source_html = source_html
                        page.call("Fetch.enable", {"patterns": [{"resourceType": "Document", "requestStage": "Request"}]})
                        data = page.navigate(url)
                        source_fallback = True
                    page.evaluate("""() => {
                      for (const v of document.querySelectorAll('video')) { v.pause(); v.preload = 'none'; }
                      const s=document.createElement('style');
                      s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';
                      document.head.appendChild(s);
                    }""")
                    stage = "screenshot"
                    tiles = _capture_chunked_screenshot(
                        page, png_path, height,
                        max_height=CAPTURE_EMERGENCY_MAX_STITCH_HEIGHT if emergency else CAPTURE_MAX_STITCH_HEIGHT,
                    )
                    _write_fallback_archive(
                        html_path.name, data.get("final_url") or url, data.get("title", ""),
                        source_html[:1_000_000], data.get("text", ""),
                        "低内存模式：真实分块整页截图；资源归档已简化", root=html_path.parent,
                    )
                    return {
                        # Preserve the public method contract while exposing
                        # which low-memory engine produced the image.
                        "method": "playwright", "engine": "chromium_cdp",
                        "script_execution_disabled": scriptless,
                        "source_html_fallback": source_fallback,
                        "page": data, "media": {
                            "safe_mode": True, "constrained_mode": constrained,
                            "emergency_mode": emergency, "available_memory_mb": memory,
                            "viewport_width": width, "chunked_screenshot": tiles,
                        },
                        "archive": {"self_contained": False, "archive_size": html_path.stat().st_size},
                    }
                finally:
                    # Do not let a hung graceful Browser.close discard a PNG
                    # already written to disk. Kill the entire browser group.
                    if socket is not None:
                        try:
                            socket.close(timeout=0)
                        except Exception:
                            pass
                    if owns_browser_group:
                        _kill_capture_process(browser)
                    else:
                        # The outer supervisor kills remaining group members
                        # after reading our result (including success).
                        try:
                            browser.kill()
                        except (OSError, ProcessLookupError):
                            pass
                        # Reap the failed attempt before any scriptless
                        # retry. All of these processes belong to this
                        # disposable worker's own group, never the API.
                        for entry in Path("/proc").iterdir():
                            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                                continue
                            try:
                                if os.getpgid(int(entry.name)) == os.getpgrp():
                                    os.kill(int(entry.name), signal.SIGKILL)
                            except (OSError, ProcessLookupError):
                                pass
                    try:
                        browser.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
    except SnapshotCaptureError:
        raise
    except Exception as exc:
        return {"error": f"{stage}: {str(exc)}"[:500]}
