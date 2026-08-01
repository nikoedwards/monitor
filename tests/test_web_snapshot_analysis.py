from __future__ import annotations

import base64
import io
import json
import re
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from PIL import Image, ImageDraw

from server.domains.web import (
    _capture_one,
    _period_stats,
    _should_use_browser_after_fetch_error,
    _snapshot_retry_at,
    capture_monitor,
    delete_snapshot,
    next_run_at,
    snapshot_to_dict,
)
from server.fetchers import FetchError
from server.snapshot import (
    SnapshotCaptureError,
    _capture_error_reason,
    _optimize_archive_image,
    _playwright_capture,
    _write_fallback_archive,
    capture_artifacts,
    compare_visuals,
    upgrade_snapshot_archives,
)


class VisualDiffTests(unittest.TestCase):
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
                self.assertEqual(result["attempts"], 4)
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

    def test_snapshot_retry_uses_bounded_backoff_then_recovery_interval(self):
        now = datetime.fromisoformat("2026-07-25T00:00:00+00:00")
        expected_minutes = (10, 30, 60, 180, 360, 360, 360, 360, 1440, 1440)
        for failure_count, minutes in enumerate(expected_minutes, start=1):
            self.assertEqual(_snapshot_retry_at(failure_count, now) - now, timedelta(minutes=minutes))


if __name__ == "__main__":
    unittest.main()
