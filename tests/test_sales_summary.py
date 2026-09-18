import sqlite3
import unittest

from server.domains.sales import sales_summary


class SalesSummaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE sales_metrics (
              id TEXT PRIMARY KEY, link_id TEXT, brand_id TEXT, product_id TEXT,
              snapshot_date TEXT, channel TEXT, platform TEXT, price REAL,
              currency TEXT, review_count INTEGER, rating REAL, rank INTEGER,
              units_est INTEGER, revenue_est REAL, in_stock INTEGER, asin TEXT,
              bsr INTEGER, title TEXT, image_url TEXT, change_score REAL,
              changes_json TEXT, source TEXT, raw_json TEXT, created_at TEXT
            );
            CREATE TABLE products (id TEXT PRIMARY KEY, brand_id TEXT, name TEXT);
            CREATE TABLE links (id TEXT PRIMARY KEY, brand_id TEXT, product_id TEXT,
              dimension TEXT, channel TEXT, status TEXT);
            CREATE TABLE sales_listings (id TEXT PRIMARY KEY, brand_id TEXT,
              product_id TEXT, monitor INTEGER, status TEXT);
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_global_metrics_use_latest_rank_rating_reviews_without_revenue(self):
        rows = [
            ("m1", "l1", "b1", None, "2026-09-17", "amazon", "amazon", 0, "USD", 10, 4.2, 120, None, None, 1, "A", 120, "A", None, 0, "[]", "scrape", "{}", "2026-09-17T01:00:00"),
            ("m2", "l1", "b1", None, "2026-09-18", "amazon", "amazon", 0, "USD", 15, 4.4, 100, None, None, 1, "A", 100, "A", None, 0, '[{"field":"rank","from":120,"to":100}]', "scrape", "{}", "2026-09-18T01:00:00"),
        ]
        self.conn.executemany("INSERT INTO sales_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.execute("INSERT INTO links VALUES ('link','b1',NULL,'sales','amazon','active')")
        self.conn.execute("INSERT INTO sales_listings VALUES ('l1','b1',NULL,1,'active')")

        result = sales_summary(brand_id="b1", days=30, conn=self.conn)

        self.assertEqual(0, result["total_revenue"])
        self.assertEqual(1, result["global_metrics"]["listing_count"])
        self.assertEqual(100, result["global_metrics"]["rank_avg"])
        self.assertEqual(4.4, result["global_metrics"]["rating_avg"])
        self.assertEqual(15, result["global_metrics"]["review_count"])
        self.assertEqual(20, result["global_metrics"]["rank_change"])
        self.assertEqual(5, result["global_metrics"]["review_change"])
        self.assertEqual(2, len(result["trend"]))
        self.assertEqual(100, result["trend"][-1]["rank_avg"])
        self.assertEqual(1, result["trend"][-1]["changes"])

    def test_product_filter_limits_listing_counts(self):
        self.conn.execute("INSERT INTO products VALUES ('p1','b1','Product 1')")
        self.conn.execute("INSERT INTO products VALUES ('p2','b1','Product 2')")
        self.conn.execute("INSERT INTO links VALUES ('link-1','b1','p1','sales','amazon','active')")
        self.conn.execute("INSERT INTO links VALUES ('link-2','b1','p2','sales','dtc','active')")
        self.conn.execute("INSERT INTO sales_listings VALUES ('l1','b1','p1',1,'active')")
        self.conn.execute("INSERT INTO sales_listings VALUES ('l2','b1','p2',1,'active')")
        result = sales_summary(brand_id="b1", product_id="p1", days=30, conn=self.conn)
        self.assertEqual(1, result["listing_total"])
        self.assertEqual(1, result["monitored_listings"])
        self.assertEqual([{"channel": "amazon", "total": 1}], result["link_counts"])

    def test_single_snapshot_listing_does_not_dilute_change_average(self):
        rows = [
            ("m1", "l1", "b1", None, "2026-09-17", "amazon", "amazon", 0, "USD", 10, 4.2, 120, None, None, 1, "A", 120, "A", None, 0, "[]", "scrape", "{}", "2026-09-17T01:00:00"),
            ("m2", "l1", "b1", None, "2026-09-18", "amazon", "amazon", 0, "USD", 15, 4.4, 100, None, None, 1, "A", 100, "A", None, 0, "[]", "scrape", "{}", "2026-09-18T01:00:00"),
            ("m3", "l2", "b1", None, "2026-09-18", "amazon", "amazon", 0, "USD", 30, 4.8, 50, None, None, 1, "B", 50, "B", None, 0, "[]", "scrape", "{}", "2026-09-18T01:00:00"),
        ]
        self.conn.executemany("INSERT INTO sales_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

        result = sales_summary(brand_id="b1", days=30, conn=self.conn)

        self.assertEqual(20, result["global_metrics"]["rank_change"])
        self.assertAlmostEqual(0.2, result["global_metrics"]["rating_change"])
        self.assertEqual(5, result["global_metrics"]["review_change"])


if __name__ == "__main__":
    unittest.main()
