from __future__ import annotations

import unittest
import json
import sqlite3

from server.connectors.creators.thirdparty import ThirdPartyCreatorProvider
from server.connectors.creators.base import CreatorPost
from server.connectors.creators.runner import _payload
from server.records import record_to_dict
from server.records import insert_record_if_new


class CreatorThumbnailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = ThirdPartyCreatorProvider("instagram", "token")

    def test_normalizes_display_uri(self):
        post = self.provider._normalize(
            {"id": "post-1", "username": "creator", "display_uri": "https://cdn.example/post.jpg"},
            "plaud",
        )
        self.assertIsNotNone(post)
        self.assertEqual(post.thumbnail_url, "https://cdn.example/post.jpg")

    def test_normalizes_instagram_image_candidates(self):
        post = self.provider._normalize(
            {
                "id": "post-2",
                "username": "creator",
                "image_versions2": {"candidates": [{"url": "https://cdn.example/post-large.jpg"}]},
            },
            "plaud",
        )
        self.assertIsNotNone(post)
        self.assertEqual(post.thumbnail_url, "https://cdn.example/post-large.jpg")

    def test_normalizes_nested_carousel_image(self):
        post = self.provider._normalize(
            {
                "id": "post-3",
                "username": "creator",
                "carousel_media": [
                    {"image_versions2": {"candidates": [{"url": "https://cdn.example/carousel.jpg"}]}}
                ],
            },
            "plaud",
        )
        self.assertIsNotNone(post)
        self.assertEqual(post.thumbnail_url, "https://cdn.example/carousel.jpg")

    def test_normalizes_wrapped_media_image(self):
        post = self.provider._normalize(
            {
                "id": "post-4",
                "username": "creator",
                "media": {"image_versions2": {"candidates": [{"url": "https://cdn.example/wrapped.jpg"}]}},
            },
            "plaud",
        )
        self.assertIsNotNone(post)
        self.assertEqual(post.thumbnail_url, "https://cdn.example/wrapped.jpg")

    def test_payload_persists_thumbnail_for_cards(self):
        payload = _payload(
            {"id": "brand-1"},
            CreatorPost(
                platform="instagram",
                external_id="post-4",
                title="A post",
                body="A post",
                thumbnail_url="https://cdn.example/post-4.jpg",
            ),
            {"is_collab": False, "is_sponsored": False, "collab_type": "none", "mentions": []},
            "instagram_listening",
        )
        self.assertEqual(payload["metrics"]["thumbnail_url"], "https://cdn.example/post-4.jpg")

    def test_read_normalizes_legacy_raw_thumbnail(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE records (
                id TEXT, topics_json TEXT, metrics_json TEXT, raw_json TEXT,
                title TEXT, body TEXT, sentiment TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("r1", "[]", "{}", json.dumps({"display_uri": "https://cdn.example/legacy.jpg"}), "", "", "neutral"),
        )
        item = record_to_dict(conn.execute("SELECT * FROM records").fetchone())
        self.assertEqual(item["metrics"]["thumbnail_url"], "https://cdn.example/legacy.jpg")

    def test_deduped_record_gets_thumbnail_on_later_sync(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE records (
                id TEXT PRIMARY KEY, source_id TEXT, external_id TEXT,
                body TEXT, metrics_json TEXT
            )"""
        )
        conn.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?)", ("r1", "instagram_listening", "post-5", "body", "{}"))
        insert_record_if_new(
            conn,
            {"source_id": "instagram_listening", "external_id": "post-5", "metrics": {"thumbnail_url": "https://cdn.example/refreshed.jpg"}},
        )
        self.assertEqual(conn.execute("SELECT metrics_json FROM records WHERE id = 'r1'").fetchone()[0], '{"thumbnail_url": "https://cdn.example/refreshed.jpg"}')


if __name__ == "__main__":
    unittest.main()
