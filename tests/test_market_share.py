import json
import sqlite3
import unittest

from fastapi import HTTPException

from server.domains.market_share import market_share


SCHEMA = """
CREATE TABLE brands (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT,
  is_primary INTEGER NOT NULL DEFAULT 0,
  is_competitor INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE records (
  id TEXT PRIMARY KEY,
  source_id TEXT,
  brand_id TEXT,
  link_id TEXT,
  data_type TEXT,
  dimension TEXT,
  channel TEXT,
  platform TEXT,
  title TEXT,
  body TEXT,
  url TEXT,
  region TEXT,
  occurred_at TEXT,
  metrics_json TEXT,
  raw_json TEXT
);
CREATE TABLE links (
  id TEXT PRIMARY KEY,
  brand_id TEXT,
  channel TEXT,
  platform TEXT,
  region TEXT
);
CREATE TABLE sales_metrics (
  id TEXT PRIMARY KEY,
  link_id TEXT,
  brand_id TEXT,
  snapshot_date TEXT,
  revenue_est REAL,
  units_est INTEGER,
  review_count INTEGER
);
"""


class MarketShareTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.executemany(
            "INSERT INTO brands (id, name, category, is_primary, is_competitor) VALUES (?, ?, ?, ?, ?)",
            [("a", "Alpha", "AI App", 1, 0), ("b", "Beta", "AI App", 0, 1)],
        )

    def tearDown(self):
        self.conn.close()

    def add_record(
        self,
        record_id,
        brand_id,
        *,
        dimension,
        channel,
        platform,
        source_id,
        metrics,
        region="US",
        data_type=None,
    ):
        self.conn.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id,
                source_id,
                brand_id,
                f"link-{brand_id}",
                data_type or ("user_voice" if dimension == "voc" else "social_post"),
                dimension,
                channel,
                platform,
                record_id,
                "useful brand signal",
                "https://example.com",
                region,
                "2026-07-15T00:00:00+00:00",
                json.dumps(metrics),
                "{}",
            ),
        )

    def test_combines_signals_and_estimates_app_download_range(self):
        self.add_record("a-app-1", "a", dimension="voc", channel="app", platform="app_store", source_id="app_store_reviews", metrics={"rating": 5})
        self.add_record("a-app-2", "a", dimension="voc", channel="app", platform="app_store", source_id="app_store_reviews", metrics={"rating": 4})
        self.add_record("b-app-1", "b", dimension="voc", channel="app", platform="app_store", source_id="app_store_reviews", metrics={"rating": 4})
        self.add_record("a-social", "a", dimension="marketing", channel="social", platform="youtube", source_id="social_accounts", metrics={"views": 1000, "likes": 40, "comments": 10, "engagement": 50})
        self.add_record("b-social", "b", dimension="marketing", channel="social", platform="youtube", source_id="social_accounts", metrics={"views": 500, "likes": 10, "comments": 5, "engagement": 15})
        self.conn.executemany(
            "INSERT INTO sales_metrics VALUES (?, ?, ?, ?, ?, ?, ?)",
            [("sa", "sale-a", "a", "2026-07-15", 1000, 100, 20), ("sb", "sale-b", "b", "2026-07-15", 500, 50, 10)],
        )

        result = market_share(
            brand_ids="a,b",
            model="balanced",
            start_date="2026-07-01",
            end_date="2026-07-31",
            conn=self.conn,
        )

        self.assertAlmostEqual(sum(row["share"] for row in result["brands"]), 100.0, places=1)
        alpha = next(row for row in result["brands"] if row["brand_id"] == "a")
        self.assertGreater(alpha["share"], 50)
        self.assertEqual(alpha["raw"]["app_downloads_est"], 200)
        self.assertEqual(alpha["raw"]["app_downloads_low"], 100)
        self.assertEqual(alpha["raw"]["app_downloads_high"], 400)
        self.assertEqual(alpha["raw"]["app_download_basis"], "review_proxy")
        self.assertEqual(
            result["model"]["base_weights"],
            {"sales": 0.0, "app": 0.65, "conversation": 0.35, "engagement": 0.0},
        )
        self.assertEqual(result["model"]["active_weights"], result["model"]["base_weights"])

    def test_missing_signals_are_reweighted_to_available_data(self):
        self.add_record(
            "a-downloads",
            "a",
            dimension="platform",
            channel="app",
            platform="app_store",
            source_id="app_store_reviews",
            data_type="app_metric",
            metrics={"downloads": 2000},
        )
        self.add_record(
            "b-downloads",
            "b",
            dimension="platform",
            channel="app",
            platform="app_store",
            source_id="app_store_reviews",
            data_type="app_metric",
            metrics={"downloads": 1000},
        )

        result = market_share(
            brand_ids="a,b",
            model="balanced",
            start_date="2026-07-01",
            end_date="2026-07-31",
            conn=self.conn,
        )

        self.assertEqual(result["model"]["active_weights"]["app"], 1.0)
        self.assertEqual(result["model"]["active_weights"]["sales"], 0.0)
        alpha = next(row for row in result["brands"] if row["brand_id"] == "a")
        beta = next(row for row in result["brands"] if row["brand_id"] == "b")
        self.assertAlmostEqual(alpha["share"], 66.67, places=2)
        self.assertAlmostEqual(beta["share"], 33.33, places=2)

    def test_filters_app_downloads_and_reviews_by_country(self):
        self.add_record("a-us", "a", dimension="voc", channel="app", platform="app_store", source_id="app_store_reviews", metrics={"downloads": 1000, "rating": 5}, region="US")
        self.add_record("b-us", "b", dimension="voc", channel="app", platform="app_store", source_id="app_store_reviews", metrics={"downloads": 500, "rating": 4}, region="US")
        self.add_record("a-gb", "a", dimension="voc", channel="app", platform="app_store", source_id="app_store_reviews", metrics={"downloads": 100, "rating": 4}, region="GB")
        self.add_record("a-unknown", "a", dimension="voc", channel="app", platform="app_store", source_id="app_store_reviews", metrics={"downloads": 999999, "rating": 5}, region="")
        for index in range(4):
            self.add_record(f"b-gb-{index}", "b", dimension="voc", channel="app", platform="app_store", source_id="app_store_reviews", metrics={"downloads": 900 if index == 0 else 0, "rating": 4}, region="GB")

        result = market_share(
            brand_ids="a,b",
            model="balanced",
            country="US",
            start_date="2026-07-01",
            end_date="2026-07-31",
            conn=self.conn,
        )

        self.assertEqual(result["country"], "US")
        self.assertEqual(result["countries"], ["GB", "US"])
        alpha = next(row for row in result["brands"] if row["brand_id"] == "a")
        beta = next(row for row in result["brands"] if row["brand_id"] == "b")
        self.assertEqual(alpha["raw"]["app_downloads_est"], 1000)
        self.assertEqual(beta["raw"]["app_downloads_est"], 500)
        self.assertEqual(alpha["raw"]["app_reviews"], 1)
        self.assertGreater(alpha["share"], beta["share"])

    def test_rejects_invalid_country_code(self):
        with self.assertRaisesRegex(HTTPException, "国家代码必须是两位 ISO 代码"):
            market_share(
                brand_ids="a,b",
                country="USA",
                start_date="2026-07-01",
                end_date="2026-07-31",
                conn=self.conn,
            )

    def test_app_metric_rating_count_drives_review_and_download_estimates(self):
        self.add_record(
            "a-metric",
            "a",
            dimension="platform",
            channel="app",
            platform="app_store",
            source_id="app_store_reviews",
            data_type="app_metric",
            metrics={"rating": 4.8, "rating_count": 21000},
        )
        self.add_record(
            "b-metric",
            "b",
            dimension="platform",
            channel="app",
            platform="app_store",
            source_id="app_store_reviews",
            data_type="app_metric",
            metrics={"rating": 4.5, "rating_count": 1000},
        )

        result = market_share(
            brand_ids="a,b",
            country="US",
            start_date="2026-07-01",
            end_date="2026-07-31",
            conn=self.conn,
        )

        alpha = next(row for row in result["brands"] if row["brand_id"] == "a")
        self.assertEqual(21000, alpha["raw"]["app_reviews"])
        self.assertEqual(2100000, alpha["raw"]["app_downloads_est"])
        self.assertEqual("review_proxy", alpha["raw"]["app_download_basis"])
        self.assertEqual(1.0, result["confidence"]["download_proxy_ratio"])
        self.assertLessEqual(result["confidence"]["score"], 70)


if __name__ == "__main__":
    unittest.main()
