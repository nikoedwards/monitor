from __future__ import annotations

import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from server.connectors.base import ConnectorSpec, run_collector
from server.connectors.creators.base import CreatorPost, occurred_in_collection_window
from server.connectors.creators.public_search import discover_profile_urls
from server.connectors.creators.youtube import YouTubePublicProvider
from server.connectors.registry import BY_ID


class CreatorPublicSearchTests(unittest.TestCase):
    def test_public_connectors_are_ready_without_api_keys(self):
        self.assertEqual(BY_ID["youtube_search"].status, "ready")
        self.assertEqual(BY_ID["instagram_listening"].status, "ready")
        self.assertEqual(BY_ID["tiktok_listening"].status, "ready")

    def test_public_youtube_normalizes_recent_result(self):
        item = {
            "id": "video-1",
            "upload_date": datetime.now(timezone.utc).strftime("%Y%m%d"),
            "webpage_url": "https://www.youtube.com/watch?v=video-1",
            "title": "PLAUD review",
            "description": "PLAUD review #ad",
            "channel": "Creator",
            "channel_id": "channel-1",
            "view_count": 100,
            "like_count": 10,
            "comment_count": 2,
        }
        post = YouTubePublicProvider._normalize(item, "PLAUD")
        self.assertIsNotNone(post)
        self.assertEqual(post.platform, "youtube")
        self.assertEqual(post.external_id, "video-1")
        self.assertEqual(post.views, 100)

    def test_collection_window_rejects_old_posts(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        brand = {"_collection_since": (now - timedelta(days=1)).isoformat()}
        recent = CreatorPost(platform="youtube", external_id="new", occurred_at=now.isoformat())
        old = CreatorPost(
            platform="youtube",
            external_id="old",
            occurred_at=(now - timedelta(days=2)).isoformat(),
        )
        self.assertTrue(occurred_in_collection_window(recent, brand, now=now))
        self.assertFalse(occurred_in_collection_window(old, brand, now=now))

    def test_runner_passes_daily_window_to_collector(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE sources (id TEXT PRIMARY KEY, last_collect_at TEXT, last_status TEXT,
                last_error TEXT, item_count INTEGER DEFAULT 0);
            CREATE TABLE source_brand_runs (source_id TEXT, brand_id TEXT, last_collect_at TEXT,
                last_status TEXT, last_error TEXT, item_count INTEGER DEFAULT 0,
                PRIMARY KEY (source_id, brand_id));
            """
        )
        conn.execute("INSERT INTO sources (id) VALUES ('window-test')")
        seen = {}

        def collect(_conn, brand):
            seen.update(brand)
            return []

        spec = ConnectorSpec(
            id="window-test",
            name="Window test",
            category="creators",
            dimension="marketing",
            tier=1,
            cadence="daily",
            collect=collect,
        )
        result = run_collector(conn, spec, {"id": "brand-1", "name": "PLAUD"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(seen["_collection_cadence"], "daily")
        since = datetime.fromisoformat(seen["_collection_since"])
        age = datetime.now(timezone.utc) - since
        self.assertGreater(age, timedelta(hours=23, minutes=59))
        self.assertLess(age, timedelta(days=1, minutes=1))
        conn.close()

    @patch("server.connectors.creators.public_search.fetch_page")
    def test_bing_results_are_reduced_to_public_profiles(self, fetch_page):
        fetch_page.return_value = {
            "anchors": [
                "https://www.instagram.com/creator_one/",
                "https://www.instagram.com/p/abc123/",
                "https://www.instagram.com/creator_one/reel/def456/",
                "https://example.com/not-instagram",
            ]
        }
        profiles = discover_profile_urls("instagram", "PLAUD", cadence="daily")
        self.assertEqual(profiles, [("https://www.instagram.com/creator_one/", "creator_one")])
        request_url = fetch_page.call_args.args[0]
        self.assertIn("freshness=Day", request_url)


if __name__ == "__main__":
    unittest.main()
