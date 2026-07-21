from __future__ import annotations

import base64
import io
import re
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from PIL import Image, ImageDraw

from server.domains.web import _period_stats, capture_monitor, delete_snapshot, snapshot_to_dict
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
    def test_failed_capture_keeps_last_success_time_so_scheduler_can_retry(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE web_monitors (
              id TEXT PRIMARY KEY,
              last_check_at TEXT,
              last_snapshot_at TEXT,
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
            "INSERT INTO web_monitors VALUES (?, NULL, ?, ?, ?, 'ok', '', NULL)",
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
        conn.close()


if __name__ == "__main__":
    unittest.main()
