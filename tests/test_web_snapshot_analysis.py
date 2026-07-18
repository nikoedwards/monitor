from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from PIL import Image, ImageDraw

from server.domains.web import _period_stats
from server.snapshot import _playwright_capture, _write_fallback_archive, compare_visuals, upgrade_snapshot_archives


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


class ArchiveFallbackTests(unittest.TestCase):
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
                <html><body style="overflow:hidden">
                  <div role="dialog" aria-modal="true" aria-label="Signup popup" style="position:fixed;inset:0">
                    <button aria-label="Close dialog">×</button>
                    <p>Saved popup</p>
                  </div>
                  <script>
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
            self.assertIn('data-monitor-archive-guard="3"', content)
            self.assertIn("connect-src 'none'", content)
            self.assertIn("Saved popup", content)

            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto((root / "page.html").as_uri())
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
            archive.write_text("<!doctype html><html><head><title>Old</title></head><body>Saved</body></html>", encoding="utf-8")
            self.assertEqual(upgrade_snapshot_archives(root), 1)
            content = archive.read_text(encoding="utf-8")
            self.assertEqual(content.count('data-monitor-archive-guard="3"'), 1)
            self.assertEqual(upgrade_snapshot_archives(root), 0)


if __name__ == "__main__":
    unittest.main()
