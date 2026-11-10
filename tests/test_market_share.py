import json
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server.domains.market_share import market_share, market_share_trend, sync_market_share_snapshots
from server.domains.common import query_records


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
  region TEXT,
  url TEXT,
  status TEXT
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
CREATE TABLE market_share_snapshots (
  id TEXT PRIMARY KEY,
  snapshot_date TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  country TEXT NOT NULL DEFAULT 'all',
  app_downloads_est INTEGER NOT NULL DEFAULT 0,
  app_downloads_low INTEGER NOT NULL DEFAULT 0,
  app_downloads_high INTEGER NOT NULL DEFAULT 0,
  app_download_basis TEXT NOT NULL DEFAULT 'unavailable',
  app_reviews INTEGER NOT NULL DEFAULT 0,
  app_rating REAL,
  app_store_apps INTEGER NOT NULL DEFAULT 0,
  source_updated_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (snapshot_date, brand_id, country)
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
        occurred_at="2026-07-15T00:00:00+00:00",
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
                occurred_at,
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
        self.assertEqual(result["model"]["active_weights"], {"sales": 0, "app": 0, "conversation": 1, "engagement": 0})

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
        self.assertEqual(4.8, alpha["raw"]["app_rating"])
        self.assertEqual(1, alpha["raw"]["app_store_apps"])
        self.assertTrue(alpha["raw"]["app_rating_count_observed"])
        self.assertEqual(2100000, alpha["raw"]["app_downloads_est"])
        self.assertEqual("review_proxy", alpha["raw"]["app_download_basis"])
        self.assertEqual(1.0, result["confidence"]["download_proxy_ratio"])
        self.assertLessEqual(result["confidence"]["score"], 70)

    def test_daily_snapshots_drive_brand_and_cohort_trends(self):
        self.add_record(
            "a-day-1", "a", dimension="platform", channel="app", platform="app_store",
            source_id="app_store_reviews", data_type="app_metric",
            metrics={"rating_count": 100, "rating": 4.5}, occurred_at="2026-07-14T08:00:00+00:00",
        )
        self.add_record(
            "b-day-1", "b", dimension="platform", channel="app", platform="app_store",
            source_id="app_store_reviews", data_type="app_metric",
            metrics={"rating_count": 100, "rating": 4.0}, occurred_at="2026-07-14T08:00:00+00:00",
        )
        self.add_record(
            "a-day-2", "a", dimension="platform", channel="app", platform="app_store",
            source_id="app_store_reviews", data_type="app_metric",
            metrics={"rating_count": 200, "rating": 4.6}, occurred_at="2026-07-15T08:00:00+00:00",
        )
        self.add_record(
            "b-day-2", "b", dimension="platform", channel="app", platform="app_store",
            source_id="app_store_reviews", data_type="app_metric",
            metrics={"rating_count": 100, "rating": 4.0}, occurred_at="2026-07-15T08:00:00+00:00",
        )

        synced = sync_market_share_snapshots(self.conn, brand_ids=["a", "b"], include_history=True)
        self.assertIn("2026-07-14", synced["captured_dates"])
        self.assertIn("2026-07-15", synced["captured_dates"])

        result = market_share_trend(
            brand_ids="a,b",
            country="US",
            start_date="2026-07-14",
            end_date="2026-07-16",
            conn=self.conn,
        )

        self.assertEqual(["2026-07-14", "2026-07-15", "2026-07-16"], [point["date"] for point in result["points"]])
        self.assertEqual(50.0, result["points"][0]["shares"]["a"])
        self.assertAlmostEqual(66.67, result["points"][1]["shares"]["a"], places=2)
        self.assertEqual(200, result["points"][1]["public_metrics"]["a"]["rating_count"])
        self.assertEqual(4.6, result["points"][1]["public_metrics"]["a"]["average_rating"])
        self.assertEqual(result["points"][1]["shares"], result["points"][2]["shares"])
        self.assertTrue(result["points"][2]["is_carried_forward"])
        alpha = next(row for row in result["summary"] if row["brand_id"] == "a")
        self.assertAlmostEqual(16.67, alpha["change_pp"], places=2)
        self.assertEqual(100, alpha["rating_count_change"])

    def test_google_play_global_count_is_deduplicated_and_not_a_country_metric(self):
        for country in ("US", "JP"):
            self.add_record(
                f"play-{country}", "a", dimension="platform", channel="app", platform="google_play",
                source_id="google_play_reviews", data_type="app_metric", region=country,
                metrics={"rating_count": 747422, "rating": 4},
            )
            self.conn.execute("UPDATE records SET raw_json = ?, link_id = ? WHERE id = ?", (
                json.dumps({"package_name": "sg.omi"}), f"auto-google-play:{country}:sg.omi", f"play-{country}",
            ))
        result = market_share(brand_ids="a,b", start_date="2026-07-01", end_date="2026-07-31", conn=self.conn)
        alpha = next(row for row in result["brands"] if row["brand_id"] == "a")
        self.assertEqual(747422, alpha["raw"]["app_reviews"])
        country_result = market_share(brand_ids="a,b", country="US", start_date="2026-07-01", end_date="2026-07-31", conn=self.conn)
        alpha = next(row for row in country_result["brands"] if row["brand_id"] == "a")
        self.assertEqual(0, alpha["raw"]["app_reviews"])

    def test_configured_identity_excludes_wrong_history_and_rebuilds_snapshots(self):
        self.add_record("wrong", "a", dimension="platform", channel="app", platform="app_store", source_id="app_store_reviews", data_type="app_metric", metrics={"rating_count": 1000000})
        self.conn.execute("UPDATE records SET url = 'https://apps.apple.com/us/app/id1347676495' WHERE id = 'wrong'")
        sync_market_share_snapshots(self.conn, brand_ids=["a"], include_history=True)
        self.conn.execute("INSERT INTO links (id, brand_id, platform, url, status) VALUES ('official', 'a', 'app_store', 'https://apps.apple.com/us/app/id6502156163', 'active')")
        self.add_record("right", "a", dimension="platform", channel="app", platform="app_store", source_id="app_store_reviews", data_type="app_metric", metrics={"rating_count": 151})
        self.conn.execute("UPDATE records SET url = 'https://apps.apple.com/us/app/id6502156163' WHERE id = 'right'")
        sync_market_share_snapshots(self.conn, brand_ids=["a"], include_history=True)
        result = market_share(brand_ids="a,b", start_date="2026-07-01", end_date="2026-07-31", conn=self.conn)
        alpha = next(row for row in result["brands"] if row["brand_id"] == "a")
        self.assertEqual(151, alpha["raw"]["app_reviews"])
        self.assertEqual(1, alpha["raw"]["excluded_app_records"])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0])
        self.assertEqual(151, self.conn.execute("SELECT app_reviews FROM market_share_snapshots WHERE brand_id = 'a' AND country = 'all' AND snapshot_date = '2026-07-15'").fetchone()[0])
        with patch("server.domains.common.record_to_dict", side_effect=dict):
            self.assertEqual(["right"], [r["id"] for r in query_records(self.conn, {"brand_id": "a"})])

    def test_proxy_downloads_do_not_compete_with_observed_downloads(self):
        self.conn.execute("INSERT INTO brands VALUES ('c', 'Gamma', 'AI App', 0, 1)")
        for brand, metrics in [("a", {"downloads": 2000, "rating_count": 20}), ("b", {"downloads": 1000, "rating_count": 10}), ("c", {"rating_count": 100000})]:
            self.add_record(brand, brand, dimension="platform", channel="app", platform="app_store", source_id="app_store_reviews", data_type="app_metric", metrics=metrics)
        result = market_share(brand_ids="a,b,c", start_date="2026-07-01", end_date="2026-07-31", conn=self.conn)
        gamma = next(row for row in result["brands"] if row["brand_id"] == "c")
        self.assertEqual(0, gamma["signal_shares"]["app"])
        self.assertEqual(10000000, gamma["raw"]["app_downloads_est"])
        self.assertEqual(0.65, result["model"]["active_weights"]["app"])

    def test_latest_app_count_can_decrease_and_duplicate_links_do_not_double_count(self):
        for record_id, count, day in [("older", 200, "14"), ("newer", 100, "15"), ("duplicate", 100, "15")]:
            self.add_record(record_id, "a", dimension="platform", channel="app", platform="app_store", source_id="app_store_reviews", data_type="app_metric", metrics={"rating_count": count}, occurred_at=f"2026-07-{day}T08:00:00+00:00")
            self.conn.execute("UPDATE records SET url = 'https://apps.apple.com/us/app/id6502156163', link_id = ? WHERE id = ?", (record_id, record_id))
        result = market_share(brand_ids="a,b", start_date="2026-07-01", end_date="2026-07-31", conn=self.conn)
        alpha = next(row for row in result["brands"] if row["brand_id"] == "a")
        self.assertEqual(100, alpha["raw"]["app_reviews"])


if __name__ == "__main__":
    unittest.main()
