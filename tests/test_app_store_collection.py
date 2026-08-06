import sqlite3
import unittest
from unittest.mock import patch

from server.connectors.collectors import collect_app_store


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


class AppStoreCollectorTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    @patch("server.connectors.collectors.fetch_json")
    def test_discovers_official_brand_portfolio_without_configured_links(self, fetch_json):
        fetch_json.return_value = {
            "results": [
                {
                    "trackId": 1635029057,
                    "trackName": "Anker",
                    "sellerName": "Power Mobile Life LLC",
                    "userRatingCount": 1281,
                    "averageUserRating": 4.5,
                    "trackViewUrl": "https://apps.apple.com/us/app/anker/id1635029057",
                },
                {
                    "trackId": 1331876603,
                    "trackName": "soundcore",
                    "sellerName": "Power Mobile Life LLC",
                    "userRatingCount": 39867,
                    "averageUserRating": 4.7,
                    "trackViewUrl": "https://apps.apple.com/us/app/soundcore/id1331876603",
                },
                {
                    "trackId": 1500310402,
                    "trackName": "Anker Buddy",
                    "sellerName": "Unrelated Developer",
                    "userRatingCount": 2,
                    "averageUserRating": 5,
                    "trackViewUrl": "https://apps.apple.com/us/app/anker-buddy/id1500310402",
                },
                {
                    "trackId": 6448311069,
                    "trackName": "ChatGPT",
                    "sellerName": "OpenAI",
                    "userRatingCount": 9000000,
                    "averageUserRating": 4.8,
                    "trackViewUrl": "https://apps.apple.com/us/app/chatgpt/id6448311069",
                },
            ]
        }

        payloads = collect_app_store(self.conn, {"id": "anker", "name": "Anker"})

        self.assertEqual({"US", "CN", "GB"}, {item["region"] for item in payloads})
        self.assertEqual(
            {"Anker", "soundcore"},
            {item["raw"]["track_name"] for item in payloads},
        )
        self.assertTrue(all(item["data_type"] == "app_metric" for item in payloads))
        self.assertTrue(all(item["raw"]["discovery"] == "brand_search" for item in payloads))
        self.assertEqual(3, fetch_json.call_count)

    @patch("server.connectors.collectors.fetch_json")
    def test_configured_link_collects_total_rating_count_and_latest_reviews(self, fetch_json):
        self.conn.execute(
            "INSERT INTO links (id, brand_id, product_id, platform, url, region, status) "
            "VALUES ('plaud-us', 'plaud', NULL, 'app_store', "
            "'https://apps.apple.com/us/app/plaud/id6450364080', 'US', 'active')"
        )

        def response(url, **_kwargs):
            if "/lookup?" in url:
                return {
                    "results": [{
                        "trackId": 6450364080,
                        "trackName": "Plaud: AI Note Taker",
                        "sellerName": "PLAUD LLC",
                        "userRatingCount": 21113,
                        "averageUserRating": 4.88,
                        "trackViewUrl": "https://apps.apple.com/us/app/plaud/id6450364080",
                    }]
                }
            return {
                "feed": {
                    "entry": [{
                        "id": {"label": "review-1"},
                        "title": {"label": "Useful"},
                        "content": {"label": "Works well"},
                        "author": {"name": {"label": "A User"}},
                        "im:rating": {"label": "5"},
                    }]
                }
            }

        fetch_json.side_effect = response
        payloads = collect_app_store(self.conn, {"id": "plaud", "name": "PLAUD"})

        metric = next(item for item in payloads if item["data_type"] == "app_metric")
        review = next(item for item in payloads if item["data_type"] == "user_voice")
        self.assertEqual(21113, metric["metrics"]["rating_count"])
        self.assertEqual("configured_link", metric["raw"]["discovery"])
        self.assertEqual("review-1", review["external_id"])
        status = self.conn.execute("SELECT last_status FROM links WHERE id = 'plaud-us'").fetchone()
        self.assertEqual("ok", status["last_status"])


if __name__ == "__main__":
    unittest.main()
