"""Disposable Chromium worker for production web snapshots."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .snapshot import _playwright_capture


from .worker_support import _attach_windows_job, _prefer_worker_oom_kill


def main() -> int:
    _attach_windows_job()
    _prefer_worker_oom_kill()
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except OSError:
            pass
    request = json.loads(sys.stdin.read())
    try:
        capture = _playwright_capture
        if os.name == "posix" and request.get("safe_mode"):
            # Chromium's DevTools protocol avoids loading Playwright's Node
            # driver alongside the browser on the small production VPS.
            from .snapshot_cdp import capture as capture_cdp

            capture = capture_cdp
        scriptless = bool(request.get("scriptless"))
        options = {"safe_mode": bool(request.get("safe_mode"))}
        if capture is not _playwright_capture and scriptless:
            options["scriptless"] = True
        result = capture(
            request["url"],
            Path(request["png_path"]),
            Path(request["html_path"]),
            request.get("source_html") or "",
            **options,
        )
        if capture is not _playwright_capture and not scriptless and result and result.get("error"):
            # Retry a transient renderer failure with JavaScript still enabled.
            # Disabling scripts leaves SPA loaders visible and lazy images
            # hidden, producing a PNG that is not a usable webpage capture.
            first_error = result["error"]
            result = capture(
                request["url"], Path(request["png_path"]), Path(request["html_path"]),
                request.get("source_html") or "", safe_mode=True,
            )
            result["previous_browser_error"] = first_error
    except Exception as exc:
        print(json.dumps({"error_type": "snapshot_capture", "error": str(exc)}, ensure_ascii=True))
        return 0
    print(json.dumps(result or {"error": "Playwright 未返回结果"}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
