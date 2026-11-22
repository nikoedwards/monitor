"""Low-memory Chromium capture without a second, Node.js browser driver."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
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
# These background integrations do not contribute to the captured page. Keep
# storefront scripts, styles, images, and same-origin application APIs enabled.
_BACKGROUND_URLS = [
    "*.mp4*", "*.webm*", "*.m3u8*", "*.gif*",
    "*://*.google-analytics.com/*", "*://*.googletagmanager.com/*",
    "*://connect.facebook.net/*", "*://*.facebook.com/tr/*",
    "*://*.clarity.ms/*", "*://*.hotjar.com/*", "*://*.hotjar.io/*",
    "*://*.klaviyo.com/*", "*://*.klaviyo.net/*",
    "*://analytics.tiktok.com/*", "*://*.doubleclick.net/*",
    "*://*.luckyorange.com/*", "*://*.luckyorange.net/*",
    "*://*.replay.io/*", "*://*.fullstory.com/*",
    "*://*.ptengine.com/*", "*://*.promptmonitor.io/*",
    "*://*.albss.com/*", "*://*.dwin1.com/*", "*/shopifycloud/perf-kit/*",
    "*/wpm/*", "*web-pixels-manager*", "*web-pixels*",
    "*/checkouts/*", "*://shop.app/*", "*/shopify_pay/*",
    "*facebook-pixel*", "*applovin-shop-integration*", "*adtr-shopify*",
    "*flying-pages.js*",
    "*upcart-*", "*essential-cart-drawer*", "*affirm-cart*", "*lb-upsell*",
    "*gift-card-pro*", "*airwallex-fraud-protection*", "*bixgrow*",
    "*affliate-by-secomapp*", "*pushowl*",
]


def _stop_browser(browser, owns_browser_group):
    if getattr(browser, '_monitor_stopped', False):
        return
    if owns_browser_group:
        _kill_capture_process(browser)
    else:
        # This disposable worker is the process-group owner. Its children
        # belong to this capture only; never signal the API's process group.
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit() and int(entry.name) != os.getpid():
                try:
                    if os.getpgid(int(entry.name)) == os.getpgrp():
                        os.kill(int(entry.name), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
    try:
        browser.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    browser._monitor_stopped = True


def _browser_binary() -> str:
    explicit = os.environ.get("SNAPSHOT_BROWSER", "").strip()
    if explicit:
        return explicit
    default_root = str(Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright') if os.name == 'nt' else '/ms-playwright'
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", default_root))
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
        if os.environ.get('MONITOR_DIAGNOSTIC_LIVE_ROOT'):
            print('CDP start '+method, file=sys.stderr, flush=True)
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
                if value.get("resourceType") == "Media":
                    self._send("Fetch.failRequest", {"requestId": value["requestId"], "errorReason": "BlockedByClient"})
                elif self.source_html is not None and not self.source_served:
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

    def wait_for_render(self):
        result = self.evaluate("""() => new Promise(resolve => {
          const deadline = Date.now() + 8000;
          const inspect = () => {
            const overlays = [...document.querySelectorAll(
              '#c-preloaderapp,[id*=preloader i],[class*=preloader i],.loading-overlay'
            )].filter(el => {
              const r=el.getBoundingClientRect(), s=getComputedStyle(el);
              return s.display!=='none' && s.visibility!=='hidden' && +s.opacity>0.05
                && r.width>innerWidth*0.8 && r.height>innerHeight*0.8
                && r.top<innerHeight*0.1 && r.left<innerWidth*0.1;
            });
            const images=[...document.images].filter(el => {
              const r=el.getBoundingClientRect();
              return r.width>100 && r.height>80 && r.bottom>0 && r.top<innerHeight;
            });
            const pending=images.filter(el=>!el.complete).length;
            const ready=document.readyState==='complete' && !overlays.length && !pending;
            if (ready || Date.now()>=deadline) {
              resolve({ready, loading_overlay:overlays.length>0,
                visible_images:images.length, pending_images:pending});
            } else setTimeout(inspect,150);
          };
          inspect();
        })""")
        if result.get('loading_overlay'):
            raise SnapshotCaptureError('Page still displays a full-screen loading overlay')
        return result

    def screenshot(self, path, **_options):
        available = _available_memory_mb()
        if available is not None and available < 160:
            # Discard decoded images/JS garbage from previous tiles before
            # hitting the supervisor's API reserve. DOM content is retained.
            self.call('Memory.simulatePressureNotification', {'level':'critical'})
        if not getattr(self, 'scriptless', False):
            self.wait_for_timeout(500)
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
    safe_mode=True, scriptless=False, trace_stages=False, prefer_prefetched=False,
):
    import websocket

    memory = _available_memory_mb()
    emergency = scriptless or memory is None or memory < CAPTURE_MIN_AVAILABLE_MB
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
            420 if emergency else 900,
        ),
    )
    browser = None
    socket = None
    stage = ""

    def mark_stage(name):
        nonlocal stage
        stage = name
        if trace_stages:
            print(f"SNAPSHOT_STAGE={name}", file=sys.stderr, flush=True)

    mark_stage("launch")
    started = time.monotonic()
    timings = {}
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
                    "--renderer-process-limit=1", "--disk-cache-size=16777216",
                    "--num-raster-threads=1",
                    "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                    f"--user-data-dir={profile}", f"--window-size={width},{height}", "about:blank",
                ]
                if constrained:
                    browser_args.append("--js-flags=--max-old-space-size=128 --optimize-for-size")
                browser = subprocess.Popen(
                    browser_args, stdout=subprocess.DEVNULL, stderr=browser_log,
                    start_new_session=owns_browser_group,
                )
                try:
                    port_file = Path(profile) / "DevToolsActivePort"
                    deadline = time.monotonic() + CDP_STARTUP_TIMEOUT_SECONDS
                    while True:
                        try:
                            port_lines = port_file.read_text().splitlines()
                            if len(port_lines) >= 2:
                                port, endpoint = port_lines[:2]
                                break
                        except OSError:
                            # Chromium may have created the port file before
                            # finishing its write (or releasing its Windows lock).
                            pass
                        if browser.poll() is not None:
                            browser_log.seek(0)
                            raise RuntimeError(f'Chromium exited {browser.returncode}: '+browser_log.read().decode(errors="replace")[-500:])
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                f"Chromium DevTools did not start within {CDP_STARTUP_TIMEOUT_SECONDS} seconds"
                            )
                        time.sleep(0.05)
                    socket = websocket.create_connection(
                        f"ws://127.0.0.1:{port}{endpoint}", timeout=6,
                        suppress_origin=True, enable_multithread=False, http_no_proxy=["127.0.0.1"],
                    )
                    page = _Page(socket)
                    page.scriptless = scriptless
                    targets = page.call("Target.getTargets")["targetInfos"]
                    target = next((t["targetId"] for t in targets if t.get("type") == "page"), None)
                    if target is None:
                        # The DevTools socket can appear before the initial tab
                        # has registered. Create a target instead of failing with
                        # an empty StopIteration startup error.
                        target = page.call("Target.createTarget", {"url":"about:blank"})['targetId']
                    page.session = page.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
                    page.call("Page.enable")
                    page.call("Network.enable", {"maxTotalBufferSize": 0, "maxResourceBufferSize": 0})
                    page.frame = page.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
                    page.call("Emulation.setDeviceMetricsOverride", {
                        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False,
                    })
                    page.call("Network.setBlockedURLs", {
                        "urls": _BACKGROUND_URLS,
                    })
                    page.call("Fetch.enable", {"patterns": [{"resourceType": "Media", "requestStage": "Request"}]})
                    page.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
                    if scriptless:
                        page.call("Emulation.setScriptExecutionDisabled", {"value": True})
                    timings['launch'] = round(time.monotonic()-started, 2)
                    mark_stage("navigation")
                    source_fallback = bool(prefer_prefetched and source_html)

                    def enable_source_html():
                        page.source_html = source_html
                        page.call("Fetch.enable", {"patterns": [{"resourceType": "Document", "requestStage": "Request"}, {"resourceType": "Media", "requestStage": "Request"}]})

                    if source_fallback:
                        enable_source_html()
                        data = page.navigate(url)
                    else:
                        try:
                            data = page.navigate(url)
                        except SnapshotCaptureError:
                            if not source_html:
                                raise
                            enable_source_html()
                            data = page.navigate(url)
                            source_fallback = True
                    mark_stage("prepare")
                    page.evaluate("""() => {
                      for (const v of document.querySelectorAll('video')) { v.pause(); v.preload = 'none'; }
                      const s=document.createElement('style');
                      s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';
                      document.head.appendChild(s);
                    }""")
                    readiness = page.wait_for_render()
                    page.evaluate("""() => {
                      for (const el of document.querySelectorAll(
                        '#onetrust-banner-sdk,#shopify-pc__banner,.cky-consent-container,'+
                        '[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i]'
                      )) {
                        const s=getComputedStyle(el);
                        if (s.position==='fixed' && (/cookie|privacy|consent/i.test(el.innerText||'')
                          || /overlay|backdrop/i.test(el.className||''))) {
                          el.style.setProperty('display','none','important');
                        }
                      }
                      const positioned=[...document.querySelectorAll('body *')].map(el=>({
                        el, position:getComputedStyle(el).position, r:el.getBoundingClientRect()
                      })).filter(x=>x.position==='fixed'||x.position==='sticky');
                      for (const {el,position,r} of positioned) {
                        el.style.setProperty('position',position==='fixed'?'absolute':'relative','important');
                        if (position==='fixed') {
                          el.style.setProperty('top',(r.top+scrollY)+'px','important');
                          el.style.setProperty('bottom','auto','important');
                        } else {
                          el.style.setProperty('top','auto','important');
                        }
                      }
                      const style=document.createElement('style');
                      style.textContent='*{scrollbar-width:none!important}*::-webkit-scrollbar{display:none!important}';
                      document.head.appendChild(style);
                    }""")
                    data = page.evaluate("""() => ({
                      final_url:location.href, title:document.title,
                      text:(document.body?.innerText||'').slice(0,200000)
                    })""")
                    mark_stage("screenshot")
                    timings["navigation"] = round(time.monotonic() - started - timings["launch"], 2)
                    tiles = _capture_chunked_screenshot(
                        page, png_path, height,
                        max_height=CAPTURE_EMERGENCY_MAX_STITCH_HEIGHT if emergency else CAPTURE_MAX_STITCH_HEIGHT,
                        on_tiles_captured=lambda: _stop_browser(browser, owns_browser_group),
                    )
                    mark_stage("archive")
                    _write_fallback_archive(
                        html_path.name, data.get("final_url") or url, data.get("title", ""),
                        source_html[:1_000_000], data.get("text", ""),
                        "低内存模式：真实分块整页截图；资源归档已简化", root=html_path.parent,
                    )
                    return {
                        # Preserve the public method contract while exposing
                        # which low-memory engine produced the image.
                        "method": "playwright", "engine": "chromium_cdp",
                        "timings": dict(timings, total=round(time.monotonic()-started, 2)),
                        "readiness": readiness,
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
                    _stop_browser(browser, owns_browser_group)
    except SnapshotCaptureError:
        raise
    except Exception as exc:
        return {"error": f"{stage}: {type(exc).__name__}: {str(exc)}"[:500], "timings": dict(timings, total=round(time.monotonic()-started, 2))}
