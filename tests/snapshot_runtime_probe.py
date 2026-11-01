"""Bounded Linux container check; never points at the production API."""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
from urllib.request import urlopen
from unittest.mock import patch

from server.snapshot import _isolated_playwright_capture


def health():
    with urlopen("http://127.0.0.1:8790/api/health", timeout=3) as response:
        assert json.load(response)["ok"]


def browser_pids():
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                command = (entry / "cmdline").read_bytes()
                if b"--user-data-dir=/tmp/monitor-cdp-" in command:
                    found.append(int(entry.name))
            except OSError:
                pass
    return found


def run_capture(url, root, timeout=90):
    started = time.monotonic()
    samples = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        with patch("server.snapshot.CAPTURE_JOB_TIMEOUT_SECONDS", timeout):
            future = executor.submit(
                _isolated_playwright_capture, url,
                root / "capture.png", root / "capture.html", "", safe_mode=True,
            )
            while not future.done():
                health()
                samples.append(int(Path("/sys/fs/cgroup/memory.current").read_text()))
                time.sleep(0.5)
            result = future.result()
    health()
    assert not browser_pids(), f"Chromium children left behind: {browser_pids()}"
    print(json.dumps({
        "url": url[:100], "elapsed": round(time.monotonic() - started, 2),
        "peak_sampled_mib": round(max(samples, default=0) / 1024**2, 2),
        "result": result,
        "events": Path("/sys/fs/cgroup/memory.events").read_text(),
    }), flush=True)
    return result


def main():
    env = dict(os.environ, HOST="127.0.0.1", PORT="8790", MONITOR_SCHEDULER="0")
    with tempfile.TemporaryFile() as log:
        api = subprocess.Popen([sys.executable, "-m", "server.app"], env=env, stdout=log, stderr=log)
        try:
            for _ in range(60):
                try:
                    health()
                    break
                except Exception:
                    if api.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read().decode(errors="replace"))
                    time.sleep(0.5)
            else:
                raise RuntimeError("Test API did not become healthy")
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = "<html><body><main style='height:1800px;background:linear-gradient(red,blue)'>Runtime PNG</main></body></html>"
                result = run_capture("data:text/html," + quote(source), root)
                assert result.get("engine") == "chromium_cdp", result
                assert (root / "capture.png").stat().st_size > 1000
                # An unresponsive renderer must be killed with the worker,
                # while the separate API continues answering health checks.
                result = run_capture("data:text/html,<script>while(true){}</script>", root, timeout=4)
                assert "error" in result, result
                target = os.environ.get("MONITOR_TEST_SNAPSHOT_URL")
                if target:
                    result = run_capture(target, root)
                    assert result.get("engine") == "chromium_cdp", result
                    assert (root / "capture.png").stat().st_size > 1000
            events = Path("/sys/fs/cgroup/memory.events").read_text()
            oom_kills = int(next(line.split()[1] for line in events.splitlines() if line.startswith("oom_kill ")))
            assert oom_kills <= 1, events
        finally:
            api.terminate()
            try:
                api.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait(timeout=5)


if __name__ == "__main__":
    main()
