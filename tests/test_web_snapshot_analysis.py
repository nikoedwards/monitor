from __future__ import annotations

import base64
import io
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

from server.domains.web import (
    _capture_one,
    _period_stats,
    _query_snapshots,
    _should_use_browser_after_fetch_error,
    _snapshot_retry_at,
    capture_monitor,
    delete_snapshot,
    next_run_at,
    snapshot_comparison,
    snapshot_to_dict,
)
from server.fetchers import FetchError
from server.snapshot import (
    CAPTURE_SLOT,
    SnapshotCaptureError,
    SnapshotCaptureBusy,
    _capture_error_reason,
    _capture_chunk_height,
    _capture_chunked_screenshot,
    _isolated_playwright_capture,
    _optimize_archive_image,
    _playwright_capture,
    analyze_change,
    _write_fallback_archive,
    capture_artifacts,
    compare_visuals,
    upgrade_snapshot_archives,
    upgrade_snapshot_fallback_images,
)
from server.domains import web as web_domain


class TextDiffTests(unittest.TestCase):
    def test_existing_copy_split_into_a_new_line_is_not_reported_as_added(self):
        phrase = "Trusted by over 2 million professionals globally"
        previous_text = f"Plaud IntelligenceTM {phrase}"
        current_text = f"Plaud IntelligenceTM\n{phrase}"

        score, summary, changes = analyze_change(
            {"title": "Plaud", "text": current_text, "text_hash": "current"},
            {"title": "Plaud", "text_excerpt": previous_text, "text_hash": "previous"},
        )

        self.assertEqual(score, 0.0)
        self.assertNotIn(phrase, [change.get("text") for change in changes])
        self.assertIn("没有变化", summary)

    def test_genuinely_new_copy_is_still_reported(self):
        phrase = "Trusted by over 2 million professionals globally"
        score, _summary, changes = analyze_change(
            {"title": "Plaud", "text": f"Plaud IntelligenceTM\n{phrase}", "text_hash": "current"},
            {"title": "Plaud", "text_excerpt": "Plaud IntelligenceTM", "text_hash": "previous"},
        )

        self.assertGreater(score, 0)
        self.assertIn({"type": "added", "text": phrase}, changes)


class VisualDiffTests(unittest.TestCase):
    def test_capture_rejects_when_another_capture_holds_slot(self):
        self.assertTrue(CAPTURE_SLOT.acquire(blocking=False))
        try:
            with self.assertRaises(SnapshotCaptureBusy):
                capture_artifacts("https://example.com", "Example", "text", "", "busy")
        finally:
            CAPTURE_SLOT.release()

    def test_capture_uses_safe_mode_when_memory_is_low(self):
        with patch("server.snapshot._available_memory_mb", return_value=250), patch(
            "server.snapshot._capture_artifacts_unlocked",
            return_value=("low-memory.png", "low-memory.html", {"method": "playwright"}),
        ) as capture:
            result = capture_artifacts(
                "https://example.com", "Example", "text", "<p>text</p>", "low-memory",
            )
        self.assertEqual(result[2]["method"], "playwright")
        self.assertTrue(capture.call_args.kwargs["safe_mode"])

    def test_isolated_capture_defers_before_launch_below_start_headroom(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("server.snapshot._available_memory_mb", return_value=100),
            patch("server.snapshot.subprocess.Popen") as launch,
        ):
            root = Path(tmp)
            result = _isolated_playwright_capture("https://example.com", root / "a.png", root / "a.html")
            self.assertIn("启动余量", result["error"])
            self.assertEqual(list(root.iterdir()), [])
            launch.assert_not_called()

    def test_running_worker_is_killed_and_staging_removed_on_memory_pressure(self):
        killed = threading.Event()
        def partial_output(request, *, timeout):
            payload = json.loads(request)
            Path(payload["png_path"]).write_bytes(b"partial image")
            killed.wait(timeout=3)
            return b'', b''

        process = MagicMock(pid=1234, returncode=None)
        process.communicate.side_effect = partial_output
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("server.snapshot._available_memory_mb", side_effect=[400, 64, 100]),
            patch("server.snapshot.subprocess.Popen", return_value=process),
            patch("server.snapshot._kill_capture_process", side_effect=lambda _process: killed.set()) as kill,
        ):
            root = Path(tmp)
            result = _isolated_playwright_capture("https://example.com", root / "a.png", root / "a.html")
            self.assertIn("低于保留量", result["error"])
            self.assertEqual(list(root.iterdir()), [])
        kill.assert_called_once_with(process)
        process.wait.assert_called_once_with(timeout=2)
        self.assertEqual(process.communicate.call_count, 1)

    def test_delayed_worker_receives_entire_large_html_payload(self):
        real_popen = subprocess.Popen
        launches = []
        code = '''import sys,time,json
from pathlib import Path
time.sleep(1.2)
request=json.load(sys.stdin)
Path(request['png_path']).write_bytes(b'complete image')
print(json.dumps({'method':'test','input_length':len(request['source_html'])}))
'''
        def launch(_command, **kwargs):
            process = real_popen([sys.executable, '-c', code], **kwargs)
            launches.append(process)
            return process
        with tempfile.TemporaryDirectory() as tmp, patch(
            'server.snapshot.subprocess.Popen', side_effect=launch,
        ), patch('server.snapshot._available_memory_mb', return_value=400), patch(
            'server.snapshot.CAPTURE_JOB_TIMEOUT_SECONDS', 5,
        ):
            root = Path(tmp)
            result = _isolated_playwright_capture(
                'https://example.com', root/'large.png', root/'large.html', 'x'*2_000_000,
            )
            self.assertEqual(result.get('input_length'), 2_000_000, result)
            self.assertTrue((root/'large.png').is_file())
            self.assertEqual(len(launches), 1)
            self.assertIsNotNone(launches[0].returncode)

    def test_memory_pressure_retries_with_fresh_low_memory_browser_after_cleanup(self):
        attempts = []
        def complete_retry(request, staging, *_args):
            attempts.append(request)
            self.assertFalse(request['scriptless'])
            if len(attempts) == 1:
                (staging / "partial").touch()
                return {"error": "memory reserve", "resource_pressure": True}
            self.assertTrue(request["safe_mode"])
            self.assertEqual(list(staging.parent.glob(".capture-*")), [staging])
            Path(request["png_path"]).write_bytes(b"complete PNG")
            return {"method": "playwright", "engine": "chromium_cdp"}

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("server.snapshot.sys.platform", "linux"),
            patch("server.snapshot._available_memory_mb", side_effect=[400, 370]),
            patch("server.snapshot._run_snapshot_worker", side_effect=complete_retry) as worker,
        ):
            root = Path(tmp)
            result = _isolated_playwright_capture("https://example.com", root / "a.png", root / "a.html")
            self.assertEqual(result["engine"], "chromium_cdp")
            self.assertEqual(list(root.iterdir()), [root / "a.png"])
            self.assertEqual(worker.call_count, 2)

    def test_worker_publishes_png_and_archive_after_success(self):
        def write_capture(request, *, timeout):
            payload = json.loads(request)
            Path(payload["png_path"]).write_bytes(b"complete PNG")
            Path(payload["html_path"]).write_text("archive")
            return b'{"method":"playwright"}', b""

        process = MagicMock(pid=1234, returncode=0)
        process.communicate.side_effect = write_capture
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("server.snapshot._available_memory_mb", return_value=400),
            patch("server.snapshot.subprocess.Popen", return_value=process),
            patch("server.snapshot._kill_capture_process"),
        ):
            root = Path(tmp)
            png, html = root / "a.png", root / "a.html"
            result = _isolated_playwright_capture("https://example.com", png, html)
            self.assertEqual(result["method"], "playwright")
            self.assertEqual(png.read_bytes(), b"complete PNG")
            self.assertEqual(html.read_text(), "archive")
            self.assertEqual(sorted(root.iterdir()), [html, png])

    def test_browser_collector_and_screenshots_share_the_same_capacity(self):
        from server import browser_jobs

        with CAPTURE_SLOT:
            with self.assertRaisesRegex(FetchError, "busy"):
                browser_jobs.run_browser_job("meta_ads", {})

    def test_emergency_memory_uses_small_chunks(self):
        self.assertEqual(_capture_chunk_height(64), 420)

    def test_manual_capture_is_queued_without_waiting_for_browser(self):
        row = {"id": "monitor-1", "name": "Example", "url": "https://example.com"}
        with patch.object(web_domain, "_queue_capture") as queue:
            result = web_domain.capture("monitor-1", conn=type("Conn", (), {
                "execute": lambda self, *_args: type("Result", (), {
                    "fetchone": lambda _self: row,
                })(),
            })())
        self.assertTrue(result["queued"])
        queue.assert_called_once_with(row)

    def test_repeated_manual_requests_share_one_worker_and_one_queue_entry(self):
        with (
            patch.object(web_domain, "_manual_pending", {}),
            patch.object(web_domain, "_manual_worker_running", False),
            patch.object(web_domain.threading, "Thread") as thread,
        ):
            web_domain._queue_capture({"id": "first"})
            web_domain._queue_capture({"id": "first"})
            web_domain._queue_capture({"id": "second"})
            self.assertEqual(list(web_domain._manual_pending), ["first", "second"])
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()

    def test_capture_uses_safe_mode_before_raster_fallback(self):
        with patch("server.snapshot._available_memory_mb", return_value=600), patch(
            "server.snapshot._capture_artifacts_unlocked",
            return_value=("safe.png", "safe.html", {"method": "playwright"}),
        ) as capture:
            result = capture_artifacts("https://example.com", "Example", "text", "", "safe")
        self.assertEqual(result[0], "safe.png")
        self.assertTrue(capture.call_args.kwargs["safe_mode"])

    def test_isolated_worker_error_does_not_retry_browser_in_api_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("server.snapshot.SNAPSHOT_DIR", root),
                patch("server.snapshot.CAPTURE_ISOLATED", True),
                patch("server.snapshot._isolated_playwright_capture", return_value={"error": "worker timeout"}),
                patch("server.snapshot._subprocess_capture") as subprocess_capture,
            ):
                # A killed worker may have left partially written artifacts.
                (root / "isolated-error.png").write_bytes(b"partial")
                (root / "isolated-error.html").write_text("partial")
                with self.assertRaisesRegex(SnapshotCaptureError, "worker timeout"):
                    capture_artifacts(
                        "https://example.com", "Example", "text", "<p>text</p>", "isolated-error",
                    )
            self.assertEqual(list(root.iterdir()), [])
            subprocess_capture.assert_not_called()

    def test_chunked_screenshot_stitches_a_long_page(self):
        class FakePage:
            def __init__(self):
                self.scroll_y = 0

            def evaluate(self, expression, *args):
                if "scrollHeight" in expression:
                    return {"width": 1440, "height": 1700, "viewport": 600}
                if "scrollTo" in expression:
                    self.scroll_y = int(args[0]) if args else 0

            def wait_for_timeout(self, _milliseconds):
                return None

            def screenshot(self, path, **_kwargs):
                Image.new("RGB", (1440, 600), (self.scroll_y % 255, 0, 0)).save(path)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stitched.png"
            result = _capture_chunked_screenshot(FakePage(), output, 600)
            with Image.open(output) as image:
                self.assertEqual(image.size, (1440, 1700))
            self.assertEqual(result["chunks"], 3)
            self.assertFalse(result["scaled"])

    def test_identical_images_have_no_visual_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = root / "before.png"
            after = root / "after.png"
            Image.new("RGB", (800, 600), "white").save(before)
            Image.new("RGB", (800, 600), "white").save(after)
            result = compare_visuals(str(after), str(before))
            self.assertTrue(result["available"])
            self.assertEqual(result["score"], 0.0)
            self.assertEqual(result["regions"], [])

    def test_large_visible_region_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = root / "before.png"
            after = root / "after.png"
            Image.new("RGB", (800, 600), "white").save(before)
            changed = Image.new("RGB", (800, 600), "white")
            ImageDraw.Draw(changed).rectangle((80, 80, 520, 320), fill="black")
            changed.save(after)
            result = compare_visuals(str(after), str(before))
            self.assertTrue(result["available"])
            self.assertGreater(result["score"], 0.1)
            self.assertTrue(result["regions"])

    def test_all_browser_failures_raise_instead_of_generating_error_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("server.snapshot.SNAPSHOT_DIR", root),
                patch("server.snapshot.CAPTURE_ISOLATED", False),
                patch("server.snapshot._playwright_capture", return_value=None),
                patch("server.snapshot._subprocess_capture", return_value={"error": "browser unavailable"}),
            ):
                with self.assertRaisesRegex(SnapshotCaptureError, "browser unavailable"):
                    capture_artifacts(
                        "https://example.com", "示例页面", "中文内容", "", "fallback",
                    )
            self.assertEqual(list(root.iterdir()), [])

    def test_legacy_svg_fallbacks_are_migrated_to_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "legacy.svg"
            legacy.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg">'
                '<text>Generated visual snapshot fallback</text>'
                '<text>示例页面</text><text>https://example.com</text>'
                '<text>中文内容</text></svg>',
                encoding="utf-8",
            )
            migrated = upgrade_snapshot_fallback_images(root)
            self.assertEqual(migrated, {"legacy.svg": "legacy.png"})
            self.assertTrue((root / "legacy.png").is_file())
            self.assertTrue(legacy.exists())


class PeriodStatsTests(unittest.TestCase):
    def test_period_stats_count_change_days_and_interval(self):
        snapshots = [
            {"snapshot_date": "2026-07-01", "page_path": "/", "change_score": 0, "visual_change_score": 0},
            {"snapshot_date": "2026-07-03", "page_path": "/", "change_score": 0.2, "visual_change_score": 0.01},
            {"snapshot_date": "2026-07-06", "page_path": "/pricing", "change_score": 0.01, "visual_change_score": 0.2},
        ]
        result = _period_stats(snapshots, "2026-07-01", "2026-07-07")
        self.assertEqual(result["total_snapshots"], 3)
        self.assertEqual(result["changed"], 2)
        self.assertEqual(result["changed_days"], 2)
        self.assertEqual(result["average_interval_days"], 3.0)
        self.assertEqual(len(result["daily"]), 7)

    def test_snapshot_archive_url_carries_replay_cache_version(self):
        result = snapshot_to_dict(
            {
                "id": "snapshot-1",
                "url": "https://example.com",
                "final_url": "https://example.com/pricing",
                "screenshot_path": "snapshot.png",
                "html_path": "snapshot.html",
                "changes_json": "[]",
                "visual_regions_json": "[]",
                "raw_json": '{"archive":{"self_contained":true}}',
                "change_score": 0,
                "visual_change_score": 0,
            }
        )
        self.assertEqual(result["archive_url"], "/snapshots/snapshot.html?v=5")

    def test_snapshot_comparison_returns_a_focused_jpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = root / "before.png"
            after = root / "after.png"
            Image.new("RGB", (800, 600), "white").save(before)
            changed = Image.new("RGB", (800, 600), "white")
            ImageDraw.Draw(changed).rectangle((80, 80, 520, 320), fill="black")
            changed.save(after)
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                CREATE TABLE web_snapshots (
                  id TEXT PRIMARY KEY,
                  monitor_id TEXT,
                  page_key TEXT,
                  created_at TEXT,
                  screenshot_path TEXT,
                  visual_regions_json TEXT,
                  raw_json TEXT DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "INSERT INTO web_snapshots (id, monitor_id, page_key, created_at, screenshot_path, visual_regions_json) VALUES (?, ?, ?, ?, ?, ?)",
                ("before", "monitor-1", "page-1", "2026-08-01T00:00:00+00:00", str(before), "[]"),
            )
            conn.execute(
                "INSERT INTO web_snapshots (id, monitor_id, page_key, created_at, screenshot_path, visual_regions_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "after",
                    "monitor-1",
                    "page-1",
                    "2026-08-02T00:00:00+00:00",
                    str(after),
                    '[{"x":0.1,"y":0.1,"width":0.55,"height":0.4,"change_ratio":0.8}]',
                ),
            )

            response = snapshot_comparison("after", region=0, conn=conn)

            self.assertEqual(response.media_type, "image/jpeg")
            self.assertTrue(response.body.startswith(b"\xff\xd8"))
            conn.close()

    def test_snapshot_query_recalculates_old_false_positive_evidence(self):
        phrase = "Trusted by over 2 million professionals globally"
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE web_snapshots (
              id TEXT PRIMARY KEY,
              monitor_id TEXT,
              brand_id TEXT,
              snapshot_date TEXT,
              url TEXT,
              page_key TEXT,
              title TEXT,
              screenshot_path TEXT,
              text_hash TEXT,
              text_excerpt TEXT,
              change_score REAL,
              summary TEXT,
              changes_json TEXT,
              raw_json TEXT,
              created_at TEXT
            )
            """
        )
        rows = [
            (
                "before", "monitor-1", "brand-1", "2026-08-01", "https://plaud.ai", "https://plaud.ai",
                "Plaud", "before.png", "before-hash", f"Plaud IntelligenceTM {phrase}", 0, "基线", "[]", "{}",
                "2026-08-01T00:00:00+00:00",
            ),
            (
                "after", "monitor-1", "brand-1", "2026-08-02", "https://plaud.ai", "https://plaud.ai",
                "Plaud", "after.png", "after-hash", f"Plaud IntelligenceTM\n{phrase}", 0.2, "旧误报",
                f'[{json.dumps({"type": "added", "text": phrase})}]', "{}", "2026-08-02T00:00:00+00:00",
            ),
        ]
        conn.executemany("INSERT INTO web_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

        snapshots = _query_snapshots(conn, "2026-08-01", "2026-08-02", brand_id="brand-1")
        latest = snapshots[0]

        self.assertEqual(latest["id"], "after")
        self.assertEqual(latest["change_score"], 0.0)
        self.assertNotIn(phrase, [change.get("text") for change in latest["changes"]])
        self.assertEqual(latest["comparison_url"], "/api/web/snapshots/after/comparison")

        # A legacy failure in between must neither appear as a snapshot nor
        # become the preceding text baseline, even when copied to a monitor.
        for method in ("raster_fallback", "shared_recent_capture"):
            failed = list(rows[0])
            failed[0] = "failed"
            failed[9] = "Browser launch failed"
            failed[13] = json.dumps({"method": method, "error": "launch timeout"})
            failed[14] = "2026-08-01T12:00:00+00:00"
            conn.execute("INSERT OR REPLACE INTO web_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", failed)
            snapshots = _query_snapshots(conn, "2026-08-01", "2026-08-02", brand_id="brand-1")
            self.assertEqual([item["id"] for item in snapshots], ["after", "before"])
            self.assertEqual(snapshots[0]["previous_snapshot_id"], "before")
            self.assertEqual(snapshots[0]["change_score"], 0)
        conn.close()


class SnapshotDeletionTests(unittest.TestCase):
    def test_delete_snapshot_removes_files_record_and_cached_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            screenshot = root / "capture.png"
            archive = root / "capture.html"
            screenshot.write_bytes(b"png")
            archive.write_text("<html></html>", encoding="utf-8")
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE web_snapshots (
                  id TEXT PRIMARY KEY,
                  monitor_id TEXT,
                  brand_id TEXT,
                  screenshot_path TEXT,
                  html_path TEXT
                );
                CREATE TABLE web_snapshot_analyses (
                  id TEXT PRIMARY KEY,
                  monitor_id TEXT,
                  brand_id TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO web_snapshots VALUES (?, ?, ?, ?, ?)",
                ("snapshot-1", "monitor-1", "brand-1", screenshot.name, archive.name),
            )
            conn.execute(
                "INSERT INTO web_snapshot_analyses VALUES (?, ?, ?)",
                ("analysis-1", None, "brand-1"),
            )
            with patch("server.domains.web.SNAPSHOT_DIR", root):
                result = delete_snapshot("snapshot-1", conn)
            self.assertEqual(result["deleted"], "snapshot-1")
            self.assertFalse(screenshot.exists())
            self.assertFalse(archive.exists())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM web_snapshots").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM web_snapshot_analyses").fetchone()[0], 0)
            conn.close()


class ArchiveFallbackTests(unittest.TestCase):
    @staticmethod
    def _large_png() -> bytes:
        image = Image.effect_noise((1800, 1200), 90).convert("RGB")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def test_archive_image_optimizer_reduces_large_raster(self):
        original = self._large_png()
        mime, optimized = _optimize_archive_image("image/png", original)
        self.assertEqual(mime, "image/webp")
        self.assertLess(len(optimized), len(original) * 0.5)
        with Image.open(io.BytesIO(optimized)) as image:
            self.assertLessEqual(max(image.size), 1600)

    def test_capture_error_reason_rejects_rate_limit_placeholder_only(self):
        self.assertEqual(
            _capture_error_reason(200, "https://example.com", "local_rate_limited"),
            "local_rate_limited",
        )
        self.assertIn("HTTP 429", _capture_error_reason(429, "https://example.com", ""))
        self.assertEqual(_capture_error_reason(404, "https://example.com/missing", "Not found"), "")
        self.assertEqual(
            _capture_error_reason(
                200,
                "https://example.com/guide",
                "Our API guide explains rate limits and how customers can request higher limits.",
            ),
            "",
        )

    def test_rejected_playwright_page_is_not_replaced_with_fake_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("server.snapshot.SNAPSHOT_DIR", root),
                patch(
                    "server.snapshot._playwright_capture",
                    side_effect=SnapshotCaptureError("local_rate_limited"),
                ),
                patch("server.snapshot._subprocess_capture") as subprocess_capture,
            ):
                with self.assertRaisesRegex(SnapshotCaptureError, "local_rate_limited"):
                    capture_artifacts(
                        "https://example.com",
                        "Example",
                        "Real page text",
                        "<main>Real page</main>",
                        "capture",
                    )
            subprocess_capture.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_prefetch_failure_requires_a_validated_playwright_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("server.snapshot.SNAPSHOT_DIR", root),
                patch("server.snapshot._playwright_capture", return_value=None),
                patch("server.snapshot._subprocess_capture") as subprocess_capture,
            ):
                with self.assertRaisesRegex(
                    SnapshotCaptureError,
                    "HTML 预抓失败.*HTTP Error 429.*Chromium 直接访问也失败",
                ):
                    capture_artifacts(
                        "https://example.com",
                        "Example",
                        "",
                        "",
                        "capture",
                        prefetch_error="HTTP Error 429: Too Many Requests",
                    )
            subprocess_capture.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_prefetch_failure_accepts_validated_playwright_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def rendered_capture(_url, png_path, html_path, _source_html):
                Image.new("RGB", (100, 100), "white").save(png_path)
                html_path.write_text("<html><body>Rendered page</body></html>", encoding="utf-8")
                return {
                    "method": "playwright",
                    "archive": {"self_contained": True, "archive_size": html_path.stat().st_size},
                    "page": {
                        "final_url": "https://www.example.com/",
                        "title": "Rendered title",
                        "text": "Rendered page",
                        "html": "<html><body>Rendered page</body></html>",
                    },
                }

            with (
                patch("server.snapshot.SNAPSHOT_DIR", root),
                patch("server.snapshot._playwright_capture", side_effect=rendered_capture),
                patch("server.snapshot._subprocess_capture") as subprocess_capture,
            ):
                screenshot, archive, meta = capture_artifacts(
                    "https://example.com",
                    "Example",
                    "",
                    "",
                    "capture",
                    prefetch_error="HTTP Error 429: Too Many Requests",
                )
            self.assertEqual(screenshot, "capture.png")
            self.assertEqual(archive, "capture.html")
            self.assertTrue(meta["browser_prefetch_fallback"])
            self.assertIn("HTTP Error 429", meta["prefetch_error"])
            subprocess_capture.assert_not_called()

    def test_playwright_rejects_local_rate_limit_page_before_writing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            try:
                with patch("server.snapshot.CAPTURE_RETRY_DELAYS_MS", (0, 0)):
                    result = _playwright_capture(
                        "data:text/html;charset=utf-8," + quote("<html><body><pre>local_rate_limited</pre></body></html>"),
                        root / "page.png",
                        root / "page.html",
                    )
            except SnapshotCaptureError as exc:
                self.assertIn("local_rate_limited", str(exc))
            else:
                if result is None:
                    self.skipTest("Playwright Chromium unavailable")
                self.fail(f"Rate-limit placeholder was accepted: {result}")
            self.assertFalse((root / "page.png").exists())
            self.assertFalse((root / "page.html").exists())

    def test_playwright_renders_fetched_html_when_live_navigation_is_rate_limited(self):
        class RateLimitedHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html><body><pre>local_rate_limited</pre></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), RateLimitedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source_html = "<html><body><main style='min-height:3200px'>" + ("Recovered real page content. " * 20) + "</main></body></html>"
                with patch("server.snapshot.CAPTURE_RETRY_DELAYS_MS", (0, 0)):
                    result = _playwright_capture(
                        f"http://127.0.0.1:{server.server_port}/",
                        root / "page.png",
                        root / "page.html",
                        source_html,
                    )
                if result is None:
                    self.skipTest("Playwright Chromium unavailable")
                self.assertNotIn("error", result)
                self.assertTrue(result["source_html_fallback"])
                self.assertEqual(result["attempts"], 3)
                self.assertTrue((root / "page.png").stat().st_size > 0)
                self.assertIn("Recovered real page content", (root / "page.html").read_text(encoding="utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_fallback_archive_is_offline_and_contains_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("server.snapshot.SNAPSHOT_DIR", Path(tmp)):
                filename = _write_fallback_archive(
                    "archive.html",
                    "https://example.com",
                    "Example",
                    "<main>Hello</main>",
                    "Hello",
                )
                content = (Path(tmp) / filename).read_text(encoding="utf-8")
        self.assertIn("default-src 'none'", content)
        self.assertIn("Hello", content)
        self.assertIn("原始 HTML 源码", content)

    def test_playwright_generates_png_and_self_contained_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = """
                <html class="lock" style="overflow:hidden;height:100%"><head><style>html.lock { overflow: hidden !important; }</style></head><body style="overflow:hidden;position:fixed;top:0;height:100%;width:100%;padding-right:15px">
                  <div role="dialog" aria-modal="true" aria-label="Signup popup" style="position:fixed;inset:0">
                    <button aria-label="Close dialog">×</button>
                    <p>Saved popup</p>
                  </div>
                  <main style="min-height:3200px;padding-top:100px">Tall archived page</main>
                  <section id="shopify-pc__banner" class="shopify-pc__banner__dialog" role="alertdialog" aria-labelledby="cookie-title" style="position:fixed;left:0;right:0;bottom:0;z-index:20;background:white">
                    <p id="cookie-title">Cookies on our site</p>
                    <button id="shopify-pc__banner__btn-decline">Decline</button>
                  </section>
                  <script>
                    setTimeout(() => {
                      document.documentElement.style.overflow = 'hidden';
                      document.body.style.overflow = 'hidden';
                      document.body.style.position = 'fixed';
                    }, 30);
                    document.addEventListener('click', (event) => {
                      if (!event.target.closest('[aria-label="Close dialog"]')) return;
                      setTimeout(() => {
                        const dialog = document.querySelector('[role="dialog"]');
                        if (dialog) {
                          dialog.style.display = 'flex';
                          dialog.style.visibility = 'visible';
                        }
                      }, 20);
                    });
                  </script>
                </body></html>
            """
            result = _playwright_capture(
                "data:text/html;charset=utf-8," + quote(source),
                root / "page.png",
                root / "page.html",
            )
            if not result or result.get("error"):
                self.skipTest(f"Playwright Chromium unavailable: {result}")
            self.assertTrue((root / "page.png").stat().st_size > 0)
            self.assertTrue(result["page"]["final_url"].startswith("data:text/html;charset=utf-8,"))
            self.assertEqual(result["page"]["title"], "")
            self.assertIn("Tall archived page", result["page"]["text"])
            content = (root / "page.html").read_text(encoding="utf-8")
            self.assertIn("data-monitor-archive-url", content)
            self.assertIn('data-monitor-archive-guard="5"', content)
            self.assertIn("connect-src 'none'", content)
            self.assertIn("Saved popup", content)

            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto((root / "page.html").as_uri())
                page.wait_for_timeout(150)
                overflow = page.evaluate(
                    "[getComputedStyle(document.documentElement).overflowY, getComputedStyle(document.body).overflowY]"
                )
                self.assertNotIn("hidden", overflow)
                self.assertNotEqual(page.evaluate("getComputedStyle(document.body).position"), "fixed")
                page.evaluate("window.scrollTo(0, 900)")
                page.wait_for_timeout(50)
                self.assertGreater(page.evaluate("window.scrollY"), 0)
                page.get_by_role("button", name="Decline").click()
                page.wait_for_timeout(100)
                self.assertFalse(page.locator("#shopify-pc__banner").is_visible())
                page.evaluate("window.scrollTo(0, 1500)")
                page.wait_for_timeout(50)
                self.assertGreater(page.evaluate("window.scrollY"), 900)
                self.assertEqual(page.get_by_role("dialog", name="Signup popup").count(), 1)
                page.get_by_role("button", name="Close dialog").click()
                page.wait_for_timeout(100)
                self.assertFalse(page.get_by_role("dialog", name="Signup popup").is_visible())
                self.assertNotEqual(page.evaluate("document.body.style.overflow"), "hidden")
                browser.close()

    def test_existing_archive_is_upgraded_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "existing.html"
            original_image = self._large_png()
            encoded_image = base64.b64encode(original_image).decode("ascii")
            archive.write_text(
                '<!doctype html><html><head><title>Old</title>'
                '<script data-monitor-archive-guard="4">window.oldGuard=true;</script>'
                '<script src="data:text/javascript;base64,Y29uc29sZS5sb2coMSk="></script>'
                f'</head><body><img src="data:image/png;base64,{encoded_image}">Saved</body></html>',
                encoding="utf-8",
            )
            self.assertEqual(upgrade_snapshot_archives(root), 1)
            content = archive.read_text(encoding="utf-8")
            self.assertEqual(content.count('data-monitor-archive-guard="5"'), 1)
            self.assertNotIn('data-monitor-archive-guard="4"', content)
            self.assertGreater(content.index("data:text/javascript"), content.index("<body"))
            self.assertRegex(content, r'<script[^>]*src="data:text/javascript[^"]*"[^>]*\bdefer\b')
            optimized_match = re.search(r"data:image/webp;base64,([A-Za-z0-9+/=]+)", content)
            self.assertIsNotNone(optimized_match)
            self.assertLess(len(base64.b64decode(optimized_match.group(1))), len(original_image) * 0.5)
            self.assertEqual(upgrade_snapshot_archives(root), 0)


class SnapshotSchedulingTests(unittest.TestCase):
    def test_successful_text_check_does_not_clear_pending_screenshot_failure(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('''CREATE TABLE web_monitors (
            id TEXT, last_check_at TEXT, last_change_score REAL, last_change_summary TEXT,
            last_status TEXT, last_error TEXT, updated_at TEXT,
            next_snapshot_retry_at TEXT, snapshot_retry_count INTEGER)''')
        conn.execute("INSERT INTO web_monitors VALUES ('monitor-1',NULL,0,'','error','renderer timeout',NULL,'2026-11-22T00:00:00+00:00',2)")
        with patch.object(web_domain, 'fetch_page', return_value={'text':'Page content'}), patch.object(
            web_domain, '_latest_valid_snapshot', return_value=None,
        ):
            result = web_domain.check_monitor(conn, {'id':'monitor-1','url':'https://example.com'})
        row = dict(conn.execute('SELECT * FROM web_monitors').fetchone())
        self.assertEqual(result['checked'], 1)
        self.assertEqual(row['last_status'], 'error')
        self.assertEqual(row['last_error'], 'renderer timeout')
        self.assertEqual(row['snapshot_retry_count'], 2)
        self.assertEqual(row['next_snapshot_retry_at'], '2026-11-22T00:00:00+00:00')
        self.assertTrue(row['last_check_at'])
        conn.close()

    def test_error_images_are_neither_reused_nor_used_as_baseline(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE web_snapshots (
            id TEXT, monitor_id TEXT, page_key TEXT, screenshot_path TEXT,
            html_path TEXT, raw_json TEXT, created_at TEXT)""")
        now = datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "error.png").write_bytes(b"error-image")
            (root / "error.html").write_text("error archive")
            for metadata in (
                {"method": "raster_fallback"},
                {"method": "shared_recent_capture", "error": "launch timeout"},
            ):
                with self.subTest(metadata=metadata):
                    conn.execute("DELETE FROM web_snapshots")
                    conn.execute("INSERT INTO web_snapshots VALUES (?,?,?,?,?,?,?)", (
                        "failed", "other-monitor", "https://example.com", "error.png",
                        "error.html", json.dumps(metadata), now,
                    ))
                    with patch("server.domains.web.SNAPSHOT_DIR", root):
                        self.assertIsNone(web_domain._reuse_recent_shared_capture(
                            conn, {"id": "current-monitor"}, "https://example.com/", "copy",
                        ))
                    self.assertIsNone(web_domain._latest_valid_snapshot(
                        conn, "other-monitor", "https://example.com",
                    ))
                    conn.execute("INSERT INTO web_snapshots VALUES (?,?,?,?,?,?,?)", (
                        "valid", "other-monitor", "https://example.com", "valid.png",
                        "valid.html", '{"method":"playwright"}', "2020-01-01",
                    ))
                    self.assertEqual(web_domain._latest_valid_snapshot(
                        conn, "other-monitor", "https://example.com",
                    )["id"], "valid")
            self.assertFalse((root / "copy.png").exists())
        conn.close()

    def test_browser_fallback_only_handles_transient_or_blocking_http_errors(self):
        self.assertTrue(_should_use_browser_after_fetch_error("HTTP Error 429: Too Many Requests"))
        self.assertTrue(_should_use_browser_after_fetch_error("HTTP Error 503: Service Unavailable"))
        self.assertTrue(_should_use_browser_after_fetch_error("The read operation timed out"))
        self.assertFalse(_should_use_browser_after_fetch_error("HTTP Error 404: Not Found"))
        self.assertFalse(_should_use_browser_after_fetch_error("Blocked or unresolvable host: 127.0.0.1"))

    def test_http_429_prefetch_falls_back_to_rendered_browser_content(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE web_snapshots (
              id TEXT PRIMARY KEY,
              monitor_id TEXT NOT NULL,
              brand_id TEXT,
              snapshot_date TEXT NOT NULL,
              url TEXT NOT NULL,
              page_key TEXT,
              final_url TEXT,
              title TEXT,
              screenshot_path TEXT,
              html_path TEXT,
              archive_size INTEGER,
              text_hash TEXT,
              text_excerpt TEXT,
              change_score REAL,
              visual_change_score REAL,
              visual_change_ratio REAL,
              visual_regions_json TEXT,
              summary TEXT,
              changes_json TEXT,
              raw_json TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        monitor = {"id": "monitor-1", "brand_id": "brand-1"}
        rendered = {
            "method": "playwright",
            "archive": {"self_contained": True, "archive_size": 1234},
            "page": {
                "final_url": "https://www.example.com/",
                "title": "Rendered title",
                "text": "Rendered browser content",
                "html": "<main>Rendered browser content</main>",
            },
        }
        with (
            patch("server.domains.web.fetch_page", side_effect=FetchError("HTTP Error 429: Too Many Requests")),
            patch("server.domains.web.capture_artifacts", return_value=("capture.png", "capture.html", rendered)) as capture,
            patch(
                "server.domains.web.compare_visuals",
                return_value={"available": False, "score": 0.0, "ratio": 0.0, "regions": []},
            ),
        ):
            result = _capture_one(conn, monitor, "https://example.com/")
        self.assertEqual(result["title"], "Rendered title")
        self.assertEqual(result["final_url"], "https://www.example.com/")
        row = conn.execute("SELECT * FROM web_snapshots").fetchone()
        self.assertEqual(row["text_excerpt"], "Rendered browser content")
        raw = json.loads(row["raw_json"])
        self.assertNotIn("page", raw)
        self.assertEqual(capture.call_args.kwargs["prefetch_error"], "HTTP Error 429: Too Many Requests")
        conn.close()

    def test_duplicate_monitor_reuses_recent_capture_without_second_site_request(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE web_snapshots (
              id TEXT PRIMARY KEY,
              monitor_id TEXT NOT NULL,
              brand_id TEXT,
              snapshot_date TEXT NOT NULL,
              url TEXT NOT NULL,
              page_key TEXT,
              final_url TEXT,
              title TEXT,
              screenshot_path TEXT,
              html_path TEXT,
              archive_size INTEGER,
              text_hash TEXT,
              text_excerpt TEXT,
              change_score REAL,
              visual_change_score REAL,
              visual_change_ratio REAL,
              visual_regions_json TEXT,
              summary TEXT,
              changes_json TEXT,
              raw_json TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source.png").write_bytes(b"png-data")
            (root / "source.html").write_text("<main>Plaud page</main>", encoding="utf-8")
            conn.execute(
                """
                INSERT INTO web_snapshots (
                  id, monitor_id, brand_id, snapshot_date, url, page_key, final_url, title,
                  screenshot_path, html_path, archive_size, text_hash, text_excerpt,
                  change_score, visual_change_score, visual_change_ratio, visual_regions_json,
                  summary, changes_json, raw_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "source-snapshot", "monitor-1", "brand-1", "2026-08-01",
                    "https://www.plaud.ai/", "https://plaud.ai", "https://www.plaud.ai/",
                    "Plaud", "source.png", "source.html", 23, "source-hash", "Plaud page",
                    0, 0, 0, "[]", "Initial snapshot", "[]",
                    json.dumps({"method": "playwright", "archive": {"self_contained": True}}),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            monitor = {"id": "monitor-2", "brand_id": "brand-2"}
            with (
                patch("server.domains.web.SNAPSHOT_DIR", root),
                patch("server.domains.web.fetch_page") as fetch,
                patch("server.domains.web.capture_artifacts") as capture,
                patch(
                    "server.domains.web.compare_visuals",
                    return_value={"available": False, "score": 0.0, "ratio": 0.0, "regions": []},
                ),
            ):
                result = _capture_one(conn, monitor, "https://www.plaud.ai/")
            fetch.assert_not_called()
            capture.assert_not_called()
            self.assertEqual(result["capture_method"], "shared_recent_capture")
            self.assertNotEqual(result["screenshot_path"], "source.png")
            self.assertNotEqual(result["html_path"], "source.html")
            self.assertEqual((root / result["screenshot_path"]).read_bytes(), b"png-data")
            self.assertEqual((root / result["html_path"]).read_text(encoding="utf-8"), "<main>Plaud page</main>")
            row = conn.execute("SELECT * FROM web_snapshots WHERE id != 'source-snapshot'").fetchone()
            self.assertEqual(row["text_hash"], "source-hash")
            self.assertEqual(json.loads(row["raw_json"])["shared_source_snapshot_id"], "source-snapshot")
        conn.close()

    def test_failed_capture_keeps_last_success_time_so_scheduler_can_retry(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE web_monitors (
              id TEXT PRIMARY KEY,
              last_check_at TEXT,
              last_snapshot_at TEXT,
              snapshot_retry_count INTEGER NOT NULL DEFAULT 0,
              next_snapshot_retry_at TEXT,
              last_snapshot_attempt_at TEXT,
              last_change_score REAL,
              last_change_summary TEXT,
              last_status TEXT,
              last_error TEXT,
              updated_at TEXT
            )
            """
        )
        previous_snapshot_at = "2026-07-20T14:17:16+00:00"
        conn.execute(
            "INSERT INTO web_monitors VALUES (?, NULL, ?, 0, NULL, NULL, ?, ?, 'ok', '', NULL)",
            ("monitor-1", previous_snapshot_at, 0.25, "Previous valid change"),
        )
        monitor = {
            "id": "monitor-1",
            "brand_id": "brand-1",
            "url": "https://example.com",
            "scope": "single_page",
        }
        with patch(
            "server.domains.web._capture_one",
            side_effect=SnapshotCaptureError("local_rate_limited"),
        ):
            self.assertEqual(capture_monitor(conn, monitor), [])
        row = conn.execute("SELECT * FROM web_monitors WHERE id = 'monitor-1'").fetchone()
        self.assertEqual(row["last_snapshot_at"], previous_snapshot_at)
        self.assertEqual(row["last_change_score"], 0.25)
        self.assertEqual(row["last_change_summary"], "Previous valid change")
        self.assertEqual(row["last_status"], "error")
        self.assertIn("local_rate_limited", row["last_error"])
        self.assertTrue(row["last_check_at"])
        self.assertEqual(row["snapshot_retry_count"], 1)
        self.assertTrue(row["next_snapshot_retry_at"])
        retry_delay = (
            datetime.fromisoformat(row["next_snapshot_retry_at"])
            - datetime.fromisoformat(row["last_snapshot_attempt_at"])
        )
        self.assertEqual(retry_delay, timedelta(minutes=10))
        conn.close()

    def test_snapshot_retry_deadline_overrides_normal_schedule(self):
        monitor = {
            "status": "active",
            "created_at": "2026-07-01T00:00:00+00:00",
            "last_snapshot_at": "2026-07-24T00:00:00+00:00",
            "snapshot_interval_minutes": 1440,
            "last_status": "error",
            "next_snapshot_retry_at": "2026-07-25T00:10:00+00:00",
        }
        self.assertEqual(next_run_at(monitor, "snapshot").isoformat(), "2026-07-25T00:10:00+00:00")

    def test_rate_limit_circuit_probes_hourly_before_full_retry(self):
        monitor = {
            "status": "active",
            "created_at": "2026-07-01T00:00:00+00:00",
            "last_check_at": "2026-08-04T23:12:06+00:00",
            "last_snapshot_at": "2026-07-30T18:24:38+00:00",
            "last_snapshot_attempt_at": "2026-08-04T23:12:06+00:00",
            "check_interval_minutes": 1440,
            "snapshot_interval_minutes": 1440,
            "snapshot_retry_count": 12,
            "last_status": "error",
            "last_error": "HTTP Error 429: Too Many Requests",
            "next_snapshot_retry_at": "2026-08-05T23:12:06+00:00",
        }
        self.assertEqual(next_run_at(monitor, "check").isoformat(), "2026-08-05T00:12:06+00:00")
        self.assertEqual(next_run_at(monitor, "snapshot").isoformat(), "2026-08-05T23:12:06+00:00")

    def test_non_rate_limit_circuit_keeps_configured_check_interval(self):
        monitor = {
            "status": "active",
            "created_at": "2026-07-01T00:00:00+00:00",
            "last_check_at": "2026-08-04T23:12:06+00:00",
            "check_interval_minutes": 1440,
            "snapshot_retry_count": 12,
            "last_status": "error",
            "last_error": "HTTP Error 404: Not Found",
        }
        self.assertEqual(next_run_at(monitor, "check").isoformat(), "2026-08-05T23:12:06+00:00")

    def test_snapshot_retry_uses_bounded_backoff_then_recovery_interval(self):
        now = datetime.fromisoformat("2026-07-25T00:00:00+00:00")
        expected_minutes = (10, 30, 60, 180, 360, 360, 360, 360, 1440, 1440)
        for failure_count, minutes in enumerate(expected_minutes, start=1):
            self.assertEqual(_snapshot_retry_at(failure_count, now) - now, timedelta(minutes=minutes))


if __name__ == "__main__":
    unittest.main()
