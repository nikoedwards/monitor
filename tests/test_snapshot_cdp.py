from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from server.snapshot_cdp import _browser_binary, capture


def _has_browser() -> bool:
    try:
        return Path(_browser_binary()).is_file()
    except Exception:
        return False


@unittest.skipUnless(_has_browser(), "Chromium is not installed")
class SnapshotCdpIntegrationTests(unittest.TestCase):
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

