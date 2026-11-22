from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from server.snapshot_cdp import _browser_binary, capture
from server.snapshot import SnapshotCaptureError


def _has_browser() -> bool:
    try:
        return Path(_browser_binary()).is_file()
    except Exception:
        return False


@unittest.skipUnless(_has_browser(), "Chromium is not installed")
class SnapshotCdpIntegrationTests(unittest.TestCase):
    def test_rejects_fullscreen_loader_instead_of_saving_spinner(self):
        source = """<html><body><main>Real content behind loading mask</main>
          <aside id='c-preloaderapp' style='position:fixed;inset:0;background:white'>
          Loading</aside></body></html>"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(SnapshotCaptureError, 'loading overlay'):
                capture('data:text/html;charset=utf-8,'+quote(source),
                        root/'loader.png', root/'loader.html', source)
            self.assertFalse((root/'loader.png').exists())

    def test_waits_for_application_to_replace_fullscreen_loader(self):
        source = """<html><body>
          <div id='c-preloaderapp' style='position:fixed;inset:0;background:white'>Loading</div>
          <main style='height:1400px;background:#123456'>Initial content</main>
          <script>setTimeout(() => {
            document.querySelector('#c-preloaderapp').style.display='none';
            document.querySelector('main').textContent='Application rendered';
          }, 500)</script></body></html>"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = capture('data:text/html;charset=utf-8,'+quote(source),
                             root/'app.png', root/'app.html', source)
            self.assertNotIn('error', result)
            self.assertIn('Application rendered', result['page']['text'])
            self.assertTrue(result['readiness']['ready'])
            self.assertFalse(result['script_execution_disabled'])
            self.assertTrue((root/'app.png').is_file())

    def test_cdp_emergency_mode_writes_real_chunked_png(self):
        source = (
            "<html><body><main style='height:1400px;background:linear-gradient(red,blue)'>"
            "CDP screenshot</main></body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            png_path = root / "capture.png"
            html_path = root / "capture.html"
            result = capture(
                "data:text/html;charset=utf-8," + quote(source),
                png_path,
                html_path,
                source,
                safe_mode=True,
            )

            self.assertNotIn("error", result)
            self.assertEqual(result["method"], "playwright")
            self.assertEqual(result["engine"], "chromium_cdp")
            self.assertTrue(result["media"]["chunked_screenshot"]["chunks"] >= 2)
            with Image.open(png_path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreater(image.height, 1200)
            self.assertTrue(html_path.is_file())

    def test_cdp_scriptless_mode_still_renders_real_html(self):
        source = "<html><body><main style='height:900px;background:#123456'>Static HTML</main><script>while (true) {}</script></body></html>"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = capture(
                "data:text/html;charset=utf-8," + quote(source),
                root / "capture.png", root / "capture.html", source,
                safe_mode=True, scriptless=True,
            )
            self.assertEqual(result["engine"], "chromium_cdp")
            self.assertTrue(result["script_execution_disabled"])
            with Image.open(root / "capture.png") as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreater(image.height, 800)

    def test_cdp_renders_prefetched_html_at_original_url_without_navigating_remotely(self):
        source = "<html><body><main style='height:1200px;background:#123456'>Prefetched storefront</main></body></html>"
        url = "https://unreachable.example/landing"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = capture(
                url, root / "capture.png", root / "capture.html", source,
                safe_mode=True, scriptless=True, prefer_prefetched=True,
            )
            self.assertTrue(result["source_html_fallback"], result)
            self.assertEqual(result["page"]["final_url"], url)
            self.assertIn("Prefetched storefront", result["page"]["text"])
            with Image.open(root / "capture.png") as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreater(image.height, 1100)

