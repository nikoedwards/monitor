import sqlite3
import unittest
from unittest.mock import patch

from server.connectors.collectors import (
    _GOOGLE_PLAY_REVIEW_COUNTRIES,
    _google_play_review_payloads,
    collect_google_play,
)


SCHEMA = """
CREATE TABLE links (
  id TEXT PRIMARY KEY,
  brand_id TEXT,
  product_id TEXT,
  platform TEXT,
  url TEXT,
  region TEXT,
  status TEXT,
  last_collect_at TEXT,
  last_status TEXT,
  last_error TEXT,
  updated_at TEXT
);
"""


class GooglePlayCollectorTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    @patch("server.connectors.collectors._google_play_reviews")
    def test_review_payload_preserves_public_rating_fields(self, reviews):
        reviews.return_value = [{
            "review_id": "review-1",
            "author": "A User",
            "body": "Works well",
            "rating": 4,
            "thumbs_up": 3,
            "occurred_at": "2026-10-27T02:26:39+00:00",
        }]

        payload = _google_play_review_payloads(
            {"id": "brand", "name": "Brand"},
            "com.example.app",
            "US",
        )[0]

        self.assertEqual("google_play_reviews", payload["source_id"])
        self.assertEqual("google_play", payload["platform"])
        self.assertEqual(4, payload["metrics"]["rating"])
        self.assertEqual("US", payload["region"])
        self.assertIn("com.example.app", payload["url"])

    @patch("server.connectors.collectors._google_play_detail")
    @patch("server.connectors.collectors._google_play_review_payloads")
    @patch("server.connectors.collectors._google_play_discover_app_ids")
    def test_collect_discovers_public_app_and_fetches_us_jp(self, discover, reviews, detail):
        discover.return_value = ["com.example.app"]
        detail.side_effect = lambda brand, app_id, country, **kwargs: {
            "data_type": "app_metric",
            "platform": "google_play",
            "region": country,
        }
        reviews.side_effect = lambda brand, app_id, country, **kwargs: [{
            "source_id": "google_play_reviews",
            "data_type": "user_voice",
            "platform": "google_play",
            "region": country,
        }]

        payloads = collect_google_play(self.conn, {"id": "brand", "name": "Brand"})

        self.assertEqual(set(_GOOGLE_PLAY_REVIEW_COUNTRIES), {
            item["region"] for item in payloads if item["data_type"] == "user_voice"
        })
        self.assertEqual(2, reviews.call_count)
        self.assertEqual(2, detail.call_count)


if __name__ == "__main__":
    unittest.main()
