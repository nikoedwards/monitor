import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import media, media_cache
from server.fetchers import FetchError

JPEG = b"\xff\xd8\xff" + b"test-image" * 10


class SocialMediaCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name)
        self.patch_cache = patch.object(media_cache, "CACHE_DIR", path)
        self.patch_route = patch.object(media, "CACHE_DIR", path)
        self.patch_cache.start()
        self.patch_route.start()
        app = FastAPI()
        app.include_router(media.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.patch_route.stop()
        self.patch_cache.stop()
        self.directory.cleanup()

    @patch("server.media_cache.fetch_bytes", return_value=JPEG)
    def test_cached_cover_survives_cdn_expiration_without_refetch(self, fetch):
        post = "https://www.instagram.com/p/ABC/"
        url = media_cache.cache_social_cover(post, "https://cdn.example/fresh.jpg")
        self.assertEqual(media_cache.cache_social_cover(post, "https://cdn.example/expired.jpg"), url)
        fetch.assert_called_once()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, JPEG)
        self.assertEqual(response.headers["content-type"], "image/jpeg")

    @patch("server.media._instagram_embed_cover", return_value=JPEG)
    @patch("server.media.fetch_bytes", side_effect=FetchError("HTTP 403"))
    def test_legacy_expired_url_recovers_from_public_post_embed_and_caches(self, fetch, embed):
        params = {"url": "https://cdn.example/expired.jpg", "post_url": "https://www.instagram.com/p/ABC/"}
        first = self.client.get("/api/media/thumbnail", params=params)
        second = self.client.get("/api/media/thumbnail", params=params)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.content, JPEG)
        fetch.assert_called_once()
        embed.assert_called_once_with(params["post_url"])

    def test_html_cannot_be_persisted_as_jpg_and_cache_path_is_validated(self):
        with self.assertRaises(FetchError):
            media_cache.save_cover("post", b"<html>login</html>")
        self.assertEqual(media._media_type("https://cdn.example/test.jpg", b"<!DOCTYPE html>login"), "application/octet-stream")
        self.assertEqual(self.client.get("/api/media/cache/not-a-hash").status_code, 404)
        with self.assertRaises(FetchError):
            media._instagram_embed_cover("https://evil.example/p/ABC/")

    @patch("server.media.fetch_bytes")
    def test_public_embed_image_element_is_used(self, fetch):
        fetch.side_effect = [b'<img class="EmbeddedMediaImage" src="https://cdn.example/fresh.jpg">', JPEG]
        self.assertEqual(media._instagram_embed_cover("https://www.instagram.com/p/ABC/"), JPEG)


if __name__ == "__main__":
    unittest.main()
