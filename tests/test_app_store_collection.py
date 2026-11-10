import sqlite3
import unittest
from unittest.mock import patch

from server.connectors.collectors import _APP_STORE_REVIEW_COUNTRIES, _DEFAULT_APP_STORE_COUNTRIES, _select_official_apps, collect_app_store


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
        search_results = [
                {
                    "trackId": 1635029057,
                    "trackName": "Anker",
                    "sellerName": "Power Mobile Life LLC",
                    "sellerUrl": "https://www.anker.com",
                    "userRatingCount": 1281,
                    "averageUserRating": 4.5,
                    "trackViewUrl": "https://apps.apple.com/us/app/anker/id1635029057",
                },
                {
                    "trackId": 1331876603,
                    "trackName": "soundcore",
                    "sellerName": "Power Mobile Life LLC",
                    "sellerUrl": "https://www.anker.com",
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

        def response(url, **_kwargs):
            if "/search?" in url:
                return {"results": search_results}
            if "/rss/customerreviews/" in url:
                return {"feed": {"entry": [{
                    "id": {"label": f"review-{url.rsplit('/', 3)[-3]}"},
                    "title": {"label": "Recent"},
                    "content": {"label": "Works well"},
                    "author": {"name": {"label": "A User"}},
                    "updated": {"label": "2026-10-20T12:34:56-07:00"},
                    "im:rating": {"label": "5"},
                }]}}
            return {"results": [
                {**search_results[0], "trackName": "Anker localized", "userRatingCount": 100},
                {**search_results[1], "trackName": "soundcore localized", "userRatingCount": 200},
            ]}

        fetch_json.side_effect = response

        payloads = collect_app_store(self.conn, {"id": "anker", "name": "Anker", "official_website": "https://anker.com"})

        self.assertEqual(set(_DEFAULT_APP_STORE_COUNTRIES), {item["region"] for item in payloads})
        self.assertEqual(
            {"Anker localized", "soundcore localized"},
            {item["raw"]["track_name"] for item in payloads if item["data_type"] == "app_metric"},
        )
        metrics = [item for item in payloads if item["data_type"] == "app_metric"]
        self.assertTrue(metrics)
        self.assertTrue(all(item["raw"]["discovery"] == "portfolio_market_lookup" for item in metrics))
        self.assertTrue(all(item["metrics"]["rating_count_observed"] for item in metrics))
        self.assertEqual(
            1 + len(_DEFAULT_APP_STORE_COUNTRIES) + len(_APP_STORE_REVIEW_COUNTRIES) * 2,
            fetch_json.call_count,
        )
        reviews = [item for item in payloads if item["data_type"] == "user_voice"]
        self.assertEqual(set(_APP_STORE_REVIEW_COUNTRIES), {item["region"] for item in reviews})
        self.assertTrue(any("/jp/" in item["url"] for item in reviews))

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
                        "updated": {"label": "2026-10-20T12:34:56-07:00"},
                        "im:rating": {"label": "5"},
                    }]
                }
            }

        fetch_json.side_effect = response
        payloads = collect_app_store(self.conn, {"id": "plaud", "name": "PLAUD"})

        metric = next(item for item in payloads if item["data_type"] == "app_metric")
        review = next(item for item in payloads if item["data_type"] == "user_voice")
        self.assertEqual(21113, metric["metrics"]["rating_count"])
        self.assertTrue(metric["metrics"]["rating_count_observed"])
        self.assertEqual("configured_link_market_lookup", metric["raw"]["discovery"])
        self.assertEqual(set(_DEFAULT_APP_STORE_COUNTRIES), {
            item["region"] for item in payloads if item["data_type"] == "app_metric"
        })
        self.assertEqual("review-1", review["external_id"])
        self.assertEqual("2026-10-20T12:34:56-07:00", review["occurred_at"])
        self.assertEqual(set(_APP_STORE_REVIEW_COUNTRIES), {
            item["region"] for item in payloads if item["data_type"] == "user_voice"
        })
        status = self.conn.execute("SELECT last_status FROM links WHERE id = 'plaud-us'").fetchone()
        self.assertEqual("ok", status["last_status"])

    @patch("server.connectors.collectors.fetch_json")
    def test_missing_storefront_never_substitutes_an_unconfigured_app(self, fetch_json):
        self.conn.execute(
            "INSERT INTO links (id, brand_id, product_id, platform, url, region, status) "
            "VALUES ('plaud-us', 'plaud', NULL, 'app_store', "
            "'https://apps.apple.com/us/app/plaud/id6450364080', 'US', 'active')"
        )

        def response(url, **_kwargs):
            if "/lookup?" in url and "country=cn" in url:
                return {"results": []}
            if "/search?" in url and "country=cn" in url:
                return {"results": [{
                    "trackId": 6748500037,
                    "trackName": "Plaud",
                    "sellerName": "Shenzhen Smart Connect Technology Co., LTD",
                    "userRatingCount": 50,
                    "averageUserRating": 4.82,
                    "trackViewUrl": "https://apps.apple.com/cn/app/plaud/id6748500037",
                }]}
            if "/lookup?" in url:
                return {"results": [{
                    "trackId": 6450364080,
                    "trackName": "Plaud: AI Note Taker",
                    "sellerName": "PLAUD LLC",
                    "userRatingCount": 21113,
                    "averageUserRating": 4.88,
                    "trackViewUrl": "https://apps.apple.com/us/app/plaud/id6450364080",
                }]}
            return {"feed": {"entry": []}}

        fetch_json.side_effect = response
        payloads = collect_app_store(self.conn, {"id": "plaud", "name": "PLAUD"})

        self.assertFalse(any(item["region"] == "CN" for item in payloads))
        self.assertFalse(any("/search?" in call.args[0] for call in fetch_json.call_args_list))

    def test_homonymous_popular_apps_are_not_official(self):
        for name, website, title, seller_url in [
            ("Omi", "https://omi.me", "Omi - Date & Meet Friends", "https://omi.sg"),
            ("Friend", "https://friend.com", "Hoop - make new friends", "https://hoop.photo"),
        ]:
            with self.subTest(name=name):
                self.assertEqual([], _select_official_apps(
                    {"name": name, "official_website": website},
                    [{"trackId": 1, "trackName": title, "sellerName": "Other", "sellerUrl": seller_url, "userRatingCount": 999999}],
                ))


if __name__ == "__main__":
    unittest.main()
