from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from PIL import Image

from server.snapshot import SnapshotCaptureError
from server.snapshot_cdp import _browser_binary, capture


def _has_browser() -> bool:
    try:
        return Path(_browser_binary()).is_file()
    except Exception:
        return False


@unittest.skipUnless(_has_browser(), "Chromium is not installed")
class SnapshotCdpIntegrationTests(unittest.TestCase):
    def test_preloader_fades_before_tiles_are_captured(self):
        source = """<style>
          body {margin:0; background:#123456}
          main {height:1500px}
          header {position:fixed; top:0; width:100%; height:32px; background:red}
          .c-preloaderapp {position:fixed; inset:0; background:white; pointer-events:none;
            animation:fade .2s .3s forwards}
          @keyframes fade {to {opacity:0}}
        </style><aside aria-hidden="true"><div class="c-preloaderapp"></div></aside>
        <header>Navigation</header><main>Storefront already present behind the loader</main>"""
        for scriptless in (False, True):
            with self.subTest(scriptless=scriptless), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = capture("data:text/html," + quote(source), root / "page.png",
                                 root / "page.html", source, scriptless=scriptless)
                self.assertNotIn("error", result)
                with Image.open(root / "page.png") as image:
                    self.assertEqual(image.getpixel((100, 10)), (255, 0, 0))
                    self.assertEqual(image.crop((100, 32, 101, 1500)).getcolors(), [(1468, (18, 52, 86))])

    def test_persistent_fullscreen_loader_never_writes_a_snapshot(self):
        source = """<main>Storefront behind loader</main>
          <div class="c-preloaderapp" style="position:fixed;inset:0;background:white"></div>"""
        with tempfile.TemporaryDirectory() as tmp, patch("server.snapshot.CAPTURE_RENDER_WAIT_TIMEOUT_MS", 200):
            root = Path(tmp)
            with self.assertRaisesRegex(SnapshotCaptureError, "全屏加载层"):
                capture("data:text/html," + quote(source), root / "page.png", root / "page.html", source)
            self.assertEqual(list(root.iterdir()), [])

    def test_redirect_while_stitching_recaptures_the_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "landing.html").write_text(
                "<main style='height:1500px;background:white'>Landing page</main>"
                "<script>onscroll = () => location.replace('campaign.html')</script>"
            )
            destination = root / "campaign.html"
            destination.write_text("<body style='margin:0;background:#123456'><main>Campaign</main>")
            result = capture((root / "landing.html").as_uri(), root / "page.png", root / "page.html")
            self.assertEqual(result["page"]["final_url"], destination.as_uri())
            self.assertEqual(result["page"]["text"].strip(), "Campaign")
            with Image.open(root / "page.png") as image:
                self.assertEqual(image.getpixel((100, 100)), (18, 52, 86))
                self.assertEqual(image.height, result["media"]["chunked_screenshot"]["chunk_height"])

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

