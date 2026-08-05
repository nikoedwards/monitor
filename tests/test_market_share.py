import json
import sqlite3
import unittest

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
  occurred_at TEXT,
  metrics_json TEXT,
  raw_json TEXT
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

    def add_record(self, record_id, brand_id, *, dimension, channel, platform, source_id, metrics):
        self.conn.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id,
                source_id,
                brand_id,
                f"link-{brand_id}",
                "user_voice" if dimension == "voc" else "social_post",
                dimension,
                channel,
                platform,
                record_id,
                "useful brand signal",
                "https://example.com",
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
        self.assertEqual(result["model"]["active_weights"], result["model"]["base_weights"])

    def test_missing_signals_are_reweighted_to_available_data(self):
        self.add_record("a-1", "a", dimension="marketing", channel="media", platform="Example", source_id="manual", metrics={})
        self.add_record("a-2", "a", dimension="marketing", channel="media", platform="Example", source_id="manual", metrics={})
        self.add_record("b-1", "b", dimension="marketing", channel="media", platform="Example", source_id="manual", metrics={})

        result = market_share(
            brand_ids="a,b",
            model="balanced",
            start_date="2026-07-01",
            end_date="2026-07-31",
            conn=self.conn,
        )

        self.assertEqual(result["model"]["active_weights"]["conversation"], 1.0)
        self.assertEqual(result["model"]["active_weights"]["sales"], 0.0)
        alpha = next(row for row in result["brands"] if row["brand_id"] == "a")
        beta = next(row for row in result["brands"] if row["brand_id"] == "b")
        self.assertAlmostEqual(alpha["share"], 66.67, places=2)
        self.assertAlmostEqual(beta["share"], 33.33, places=2)


if __name__ == "__main__":
    unittest.main()
