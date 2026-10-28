from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from server.connectors.creators.x_api import XApiCreatorProvider


class XApiCreatorTests(unittest.TestCase):
    def test_normalizes_tweet_and_public_metrics(self):
        post = XApiCreatorProvider._normalize(
            {
                "id": "tweet-1",
                "text": "PLAUD review #ad",
                "created_at": "2026-10-28T03:04:05Z",
                "author_id": "user-1",
                "public_metrics": {
                    "impression_count": 1234,
                    "like_count": 12,
                    "reply_count": 3,
                    "retweet_count": 4,
                    "quote_count": 2,
                },
                "attachments": {"media_keys": ["3_1"]},
            },
            {
                "user-1": {
                    "id": "user-1",
                    "name": "Creator",
                    "username": "creator",
                    "profile_image_url": "https://cdn.example/avatar.jpg",
                    "public_metrics": {"followers_count": 9876},
                }
            },
            {"3_1": {"media_key": "3_1", "preview_image_url": "https://cdn.example/post.jpg"}},
            "PLAUD",
        )
        self.assertIsNotNone(post)
        self.assertEqual(post.url, "https://x.com/creator/status/tweet-1")
        self.assertEqual(post.author_handle, "creator")
        self.assertEqual(post.views, 1234)
        self.assertEqual(post.shares, 6)
        self.assertEqual(post.follower_count, 9876)
        self.assertEqual(post.thumbnail_url, "https://cdn.example/post.jpg")

    @patch("server.connectors.creators.x_api.fetch_json")
    def test_search_uses_recent_window_and_auth_header(self, fetch_json):
        fetch_json.return_value = {"data": []}
        provider = XApiCreatorProvider("token")
        brand = {"_collection_since": "2026-10-20T00:00:00+00:00"}
        provider.collect(sqlite3.connect(":memory:"), brand, ["PLAUD"])
        url = fetch_json.call_args.args[0]
        kwargs = fetch_json.call_args.kwargs
        self.assertIn("query=PLAUD+-is%3Aretweet", url)
        self.assertIn("start_time=", url)
        self.assertIn("max_results=100", url)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token")


if __name__ == "__main__":
    unittest.main()
