import sqlite3
import unittest

from server.domains.sales import sales_changes


class SalesChangeLogTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE sales_metrics (
              id TEXT, link_id TEXT, brand_id TEXT, product_id TEXT,
              snapshot_date TEXT, channel TEXT, platform TEXT, price REAL,
              currency TEXT, review_count INTEGER, rating REAL, rank INTEGER,
              units_est INTEGER, revenue_est REAL, in_stock INTEGER,
              asin TEXT, bsr INTEGER, title TEXT, image_url TEXT,
              change_score REAL, changes_json TEXT, source TEXT,
              raw_json TEXT, created_at TEXT
            );
            CREATE TABLE sales_listings (id TEXT, title TEXT, asin TEXT, brand_id TEXT, product_id TEXT,
              channel TEXT, platform TEXT, first_seen TEXT, created_at TEXT);
            CREATE TABLE products (id TEXT, name TEXT);
            """
        )
        self.conn.execute("INSERT INTO sales_listings VALUES ('l1', 'Demo listing', 'A1', 'b1', 'p1', 'amazon', 'Amazon', '2026-09-10', '2026-09-10')")
        self.conn.execute("INSERT INTO products VALUES ('p1', 'Demo product')")
        for date, rank, rating, reviews in (("2026-09-10", 100, 4.2, 10), ("2026-09-11", 80, 4.4, 14)):
            self.conn.execute(
                """INSERT INTO sales_metrics
                VALUES (?, 'l1', 'b1', 'p1', ?, 'amazon', 'Amazon', 10, 'USD', ?, ?, ?,
                        NULL, NULL, 1, 'A1', NULL, 'Demo listing', NULL, 0, '[]', 'scrape', '{}', ?)
                """,
                (f"m-{date}", date, reviews, rating, rank, f"{date}T00:00:00Z"),
            )

    def tearDown(self):
        self.conn.close()

    def test_infers_metric_changes_and_listing_delta(self):
        result = sales_changes(
            brand_id="b1",
            start_date="2026-09-10",
            end_date="2026-09-11",
            conn=self.conn,
        )
        self.assertEqual(1, result["daily"][0]["listing_count"])
        self.assertEqual(1, result["daily"][0]["delta"])
        event = result["events"][0]
        self.assertEqual("2026-09-11", event["date"])
        self.assertEqual({"rank", "rating", "review_count"}, {c["field"] for c in event["changes"]})
        self.assertEqual(1, result["changed_listings"])
        self.assertEqual(1, result["field_counts"]["rank"])

    def test_infers_first_selected_day_from_previous_snapshot(self):
        result = sales_changes(
            brand_id="b1",
            product_id="p1",
            channel="amazon",
            start_date="2026-09-11",
            end_date="2026-09-11",
            conn=self.conn,
        )

        self.assertEqual(1, len(result["events"]))
        self.assertEqual("2026-09-11", result["events"][0]["date"])
        self.assertEqual(
            {"rank", "rating", "review_count"},
            {change["field"] for change in result["events"][0]["changes"]},
        )

    def test_listing_delta_uses_nearest_prior_capture_with_filters(self):
        # l2 was captured historically but not on the nearest prior day. It
        # must not inflate the baseline for the first selected day's delta.
        self.conn.execute(
            """INSERT INTO sales_metrics
            VALUES ('old-l2', 'l2', 'b1', 'p1', '2026-09-08', 'amazon', 'Amazon',
                    10, 'USD', 1, 4, 200, NULL, NULL, 1, 'A2', NULL,
                    'Old listing', NULL, 0, '[]', 'scrape', '{}', '2026-09-08T00:00:00Z')"""
        )
        self.conn.execute(
            """INSERT INTO sales_metrics
            VALUES ('prior-l1', 'l1', 'b1', 'p1', '2026-09-09', 'amazon', 'Amazon',
                    10, 'USD', 9, 4.1, 110, NULL, NULL, 1, 'A1', NULL,
                    'Demo listing', NULL, 0, '[]', 'scrape', '{}', '2026-09-09T00:00:00Z')"""
        )

        result = sales_changes(
            brand_id="b1",
            product_id="p1",
            channel="amazon",
            start_date="2026-09-10",
            end_date="2026-09-11",
            conn=self.conn,
        )

        self.assertEqual(1, result["daily"][0]["listing_count"])
        self.assertEqual(0, result["daily"][0]["delta"])

    def test_registry_only_listing_is_included_in_daily_count(self):
        self.conn.execute(
            "INSERT INTO sales_listings VALUES "
            "('l2', 'Blocked listing', 'A2', 'b1', 'p1', 'amazon', 'Amazon', '2026-09-11', '2026-09-11')"
        )

        result = sales_changes(
            brand_id="b1",
            product_id="p1",
            channel="amazon",
            start_date="2026-09-10",
            end_date="2026-09-11",
            conn=self.conn,
        )

        self.assertEqual([1, 2], [point["listing_count"] for point in result["daily"]])
        self.assertEqual([1, 1], [point["delta"] for point in result["daily"]])
        self.assertTrue(any(event.get("event_type") == "listing_added" and event["listing_id"] == "l2" for event in result["events"]))


if __name__ == "__main__":
    unittest.main()
