import json
import sqlite3
import unittest
from unittest.mock import patch

from server.connectors.sales.amazon import _extract_rank_levels
from server.connectors.sales.amazon import ScrapeAmazonProvider
from server.connectors.sales.dtc import ScrapeDtcProvider
from server.connectors.sales.sellersprite import SellerSpriteProvider
from server.connectors.sales.base import ListingSnapshot, metric_rank_value
from server.domains.sales import _metric_changes
from server.connectors.sales.runner import _record_snapshot
from server.db import SCHEMA
from server.connectors.sales.estimates import (
    backfill_sales_metrics,
    estimate_amazon_sales,
    estimate_dtc_sales,
)
from server.domains.sales import metric_to_dict, sales_summary


class SalesEstimateTests(unittest.TestCase):
    def test_rank_compatibility_prefers_explicit_category_then_legacy_bsr(self):
        self.assertEqual(50, metric_rank_value({"category_rank": 50, "bsr": 100, "rank": 200}))
        self.assertEqual(100, metric_rank_value({"bsr": 100, "rank": 200}))

    def test_legacy_rank_delta_is_kept_when_explicit_rank_columns_are_empty(self):
        previous = {"category_rank": None, "subcategory_rank": None, "rank": 120, "bsr": 120}
        current = {"category_rank": None, "subcategory_rank": None, "rank": 100, "bsr": 100}

        changes = _metric_changes(previous, current)

        self.assertEqual({"rank"}, {change["field"] for change in changes})
        self.assertEqual({"from": 120, "to": 100}, {k: changes[0][k] for k in ("from", "to")})

    def test_explicit_category_and_subcategory_deltas_are_not_collapsed(self):
        previous = {"category_rank": 120, "subcategory_rank": 12, "rank": 120, "bsr": 120}
        current = {"category_rank": 100, "subcategory_rank": 8, "rank": 100, "bsr": 100}

        changes = _metric_changes(previous, current)

        self.assertEqual({"category_rank", "subcategory_rank"}, {change["field"] for change in changes})

    def test_legacy_rank_is_used_as_baseline_when_category_rank_is_introduced(self):
        previous = {"rank": 120, "bsr": 120, "category_rank": None, "subcategory_rank": None}
        current = {"rank": 100, "bsr": 100, "category_rank": 100, "subcategory_rank": 8}

        changes = _metric_changes(previous, current)

        self.assertEqual(
            {"category_rank", "subcategory_rank"},
            {change["field"] for change in changes},
        )
        category = next(change for change in changes if change["field"] == "category_rank")
        self.assertEqual({"from": 120, "to": 100}, {k: category[k] for k in ("from", "to")})

    def test_amazon_rank_parser_keeps_large_and_nested_category(self):
        text = (
            "Best Sellers Rank #123 in Electronics "
            "(See Top 100 in Electronics) #45 in Headphones "
            "(See Top 100 in Headphones)"
        )

        result = _extract_rank_levels(text, "")

        self.assertEqual(
            {
                "category_rank": 123,
                "subcategory_rank": 45,
                "category_name": "Electronics",
                "subcategory_name": "Headphones",
            },
            result,
        )

    def test_amazon_rank_parser_handles_detail_bullets_html_and_localized_separators(self):
        html = (
            '<div id="detailBulletsWrapper_feature_div">'
            '<span class="a-text-bold">Best Sellers Rank</span>: '
            '# 1.234 in Elektronik<br>'
            '# 56 in Kopfhörer'
            '</div>'
        )

        result = _extract_rank_levels("", html)

        self.assertEqual(1234, result["category_rank"])
        self.assertEqual(56, result["subcategory_rank"])

    def test_amazon_rank_parser_handles_salesrank_container_without_in_connector(self):
        html = (
            '<div id="SalesRank">'
            '<span>#1,234</span> Electronics &amp; Photo<br>'
            '<span>#56</span> Headphones &amp; Earbuds'
            '</div>'
        )

        result = _extract_rank_levels("", html)

        self.assertEqual(1234, result["category_rank"])
        self.assertEqual(56, result["subcategory_rank"])
        self.assertIn("Electronics", result["category_name"])
        self.assertIn("Headphones", result["subcategory_name"])

    def test_amazon_rank_parser_handles_line_wrapped_subcategory(self):
        text = (
            "Best Sellers Rank\n"
            "#2,391 in Electronics (\nSee Top 100 in Electronics\n)\n"
            "#12 in\nDigital Voice Recorders\nASIN"
        )

        result = _extract_rank_levels(text, "")

        self.assertEqual(2391, result["category_rank"])
        self.assertEqual(12, result["subcategory_rank"])
        self.assertIn("Digital Voice Recorders", result["subcategory_name"])

    def test_amazon_rank_parser_handles_wrapped_subcategory_name(self):
        text = (
            "Best Sellers Rank\n"
            "#2,391 in Electronics (See Top 100 in Electronics)\n"
            "#12 in\nDigital Voice Recorders\n"
            "ASIN\nB0CZWQV8P5"
        )

        result = _extract_rank_levels(text, "")

        self.assertEqual(2391, result["category_rank"])
        self.assertEqual(12, result["subcategory_rank"])
        self.assertEqual("Digital Voice Recorders", result["subcategory_name"])

    def test_amazon_bsr_estimate_is_monotonic_and_labelled(self):
        estimate = estimate_amazon_sales(rank=100, price=19.99, marketplace="US")

        self.assertEqual(100, estimate["units_est"])
        self.assertEqual(1999.0, estimate["revenue_est"])
        self.assertEqual("amazon_bsr_curve", estimate["estimate_method"])
        self.assertEqual("low", estimate["estimate_confidence"])
        self.assertEqual(100, estimate["estimate_basis"]["rank"])

        # A better BSR should not produce a lower estimate.
        self.assertGreaterEqual(
            estimate_amazon_sales(rank=10)["units_est"],
            estimate_amazon_sales(rank=100)["units_est"],
        )

    def test_dtc_review_velocity_estimate_uses_snapshot_interval(self):
        estimate = estimate_dtc_sales(
            price=20,
            review_count=120,
            previous_review_count=100,
            previous_date="2026-09-01",
            current_date="2026-09-11",
        )

        self.assertEqual(100, estimate["units_est"])
        self.assertEqual(2000.0, estimate["revenue_est"])
        self.assertEqual("dtc_review_velocity", estimate["estimate_method"])
        self.assertEqual(10.0, estimate["estimate_period_days"])
        self.assertEqual(20, estimate["estimate_basis"]["review_delta"])

    def test_dtc_first_snapshot_falls_back_to_review_stock(self):
        estimate = estimate_dtc_sales(price=20, review_count=100)

        self.assertEqual("dtc_review_stock", estimate["estimate_method"])
        self.assertGreater(estimate["units_est"], 0)
        self.assertEqual(estimate["units_est"] * 20, estimate["revenue_est"])

    @patch("server.connectors.sales.amazon.fetch_page")
    def test_amazon_provider_emits_rank_levels_and_estimate(self, fetch_page):
        fetch_page.return_value = {
            "html": (
                '<span id="productTitle">Example</span>'
                '<div id="corePrice_feature_div"><span class="a-offscreen">$20.00</span></div>'
                '<span id="acrPopover" title="4.5 out of 5 stars"></span>'
                '<span id="acrCustomerReviewText" aria-label="100 Reviews">100</span>'
            ),
            "text": (
                "4.5 out of 5 100 Reviews "
                "Best Sellers Rank #100 in Electronics (See Top 100 in Electronics) "
                "#20 in Headphones (See Top 100 in Headphones)"
            ),
            "meta": {"og:title": "Example"},
            "title": "Example",
            "final_url": "https://www.amazon.com/dp/B000000000",
        }

        snapshot = ScrapeAmazonProvider().fetch(
            None,
            {"url": "https://www.amazon.com/dp/B000000000", "asin": "B000000000", "marketplace": "US"},
        )

        self.assertEqual(100, snapshot.category_rank)
        self.assertEqual(20, snapshot.subcategory_rank)
        self.assertEqual(100, snapshot.units_est)
        self.assertEqual(2000.0, snapshot.revenue_est)
        self.assertEqual("amazon_bsr_curve", snapshot.estimate_method)

    @patch("server.connectors.sales.dtc.fetch_page")
    def test_dtc_provider_emits_review_based_estimate(self, fetch_page):
        fetch_page.return_value = {
            "html": (
                '<meta property="og:title" content="DTC Product">'
                '<script type="application/ld+json">'
                '{"@type":"Product","name":"DTC Product","offers":{"price":"25","priceCurrency":"USD"},'
                '"aggregateRating":{"ratingValue":"4.7","reviewCount":"100"}}'
                '</script>'
            ),
            "json_ld": [
                {
                    "@type": "Product",
                    "name": "DTC Product",
                    "offers": {"price": "25", "priceCurrency": "USD"},
                    "aggregateRating": {"ratingValue": "4.7", "reviewCount": "100"},
                }
            ],
            "meta": {"og:title": "DTC Product"},
            "title": "DTC Product",
            "final_url": "https://example.com/products/demo",
            "anchors": [],
        }

        snapshot = ScrapeDtcProvider().fetch(None, {"url": "https://example.com/products/demo"})

        self.assertGreater(snapshot.units_est or 0, 0)
        self.assertEqual((snapshot.units_est or 0) * 25, snapshot.revenue_est)
        self.assertEqual("dtc_review_stock", snapshot.estimate_method)
        self.assertEqual("low", snapshot.estimate_confidence)

    @patch("server.connectors.sales.dtc.fetch_page")
    def test_dtc_provider_falls_back_to_rendered_review_markup(self, fetch_page):
        fetch_page.return_value = {
            "html": (
                '<meta property="og:title" content="DTC Product">'
                '<div class="spr-badge" data-rating="4.6">'
                '<span class="spr-badge-caption">37 reviews</span>'
                '</div>'
                '<div data-review-count="37"></div>'
                '<div class="price">$25</div>'
            ),
            "text": "DTC Product 4.6 stars 37 reviews $25",
            "json_ld": [],
            "meta": {"og:title": "DTC Product", "og:price:amount": "25"},
            "title": "DTC Product",
            "final_url": "https://example.com/products/demo",
            "anchors": [],
        }

        snapshot = ScrapeDtcProvider().fetch(None, {"url": "https://example.com/products/demo"})

        self.assertEqual(4.6, snapshot.rating)
        self.assertEqual(37, snapshot.review_count)
        self.assertGreater(snapshot.units_est or 0, 0)
        self.assertEqual("html_fallback", snapshot.raw["review_signal_source"])

    @patch("server.connectors.sales.sellersprite.fetch_json")
    def test_sellersprite_observed_estimate_wins_with_medium_confidence(self, fetch_json):
        fetch_json.return_value = {
            "data": {
                "asinDetail": {"title": "Example", "rating": "4.5", "ratings": "100"},
                "dailyItemList": [{
                    "bsr": "100",
                    "categoryRank": "50",
                    "subcategoryRank": "5",
                    "sales": "20",
                    "amount": "400",
                    "price": "20",
                }],
            }
        }

        snapshot = SellerSpriteProvider("secret").fetch(
            None,
            {"asin": "B000000000", "marketplace": "US"},
        )

        self.assertEqual(50, snapshot.category_rank)
        self.assertEqual(5, snapshot.subcategory_rank)
        self.assertEqual(20, snapshot.units_est)
        self.assertEqual(400.0, snapshot.revenue_est)
        self.assertEqual("sellersprite", snapshot.estimate_method)
        self.assertEqual("medium", snapshot.estimate_confidence)

    def test_runner_persists_rank_levels_and_estimate_metadata(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute(
            """
            INSERT INTO sales_listings
              (id, brand_id, link_id, channel, platform, asin, url, canonical_url,
               status, monitor, config_json, created_at, updated_at)
            VALUES ('listing-1', 'brand-1', 'link-1', 'amazon', 'Amazon', 'B000000000',
                    'https://amazon.test/dp/B000000000', 'https://amazon.test/dp/B000000000',
                    'active', 1, '{}', '2026-09-19T00:00:00', '2026-09-19T00:00:00')
            """
        )
        listing = dict(conn.execute("SELECT * FROM sales_listings WHERE id = 'listing-1'").fetchone())
        snapshot = ListingSnapshot(
            title="Example",
            sku="B000000000",
            price=20,
            category_rank=100,
            subcategory_rank=20,
            category_name="Electronics",
            subcategory_name="Headphones",
            units_est=100,
            revenue_est=2000,
            estimate_method="amazon_bsr_curve",
            estimate_confidence="low",
            estimate_period_days=1,
            estimate_basis={"rank": 100},
        )

        _record_snapshot(conn, listing, snapshot)
        row = conn.execute("SELECT * FROM sales_metrics WHERE link_id = 'listing-1'").fetchone()

        self.assertEqual(100, row["category_rank"])
        self.assertEqual(20, row["subcategory_rank"])
        self.assertEqual(100, row["units_est"])
        self.assertEqual("amazon_bsr_curve", row["estimate_method"])
        self.assertEqual("low", row["estimate_confidence"])
        self.assertEqual(1.0, row["estimate_period_days"])
        self.assertEqual(100, json.loads(row["raw_json"])["estimate_basis"]["rank"])
        api_metric = metric_to_dict(row)
        self.assertEqual("amazon_bsr_curve", api_metric["estimate_method"])
        self.assertEqual({"rank": 100}, api_metric["estimate_basis"])
        conn.close()

    def test_backfill_is_idempotent_and_preserves_observed_estimates(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.executemany(
            """
            INSERT INTO sales_metrics (
              id, link_id, brand_id, snapshot_date, channel, platform, price,
              review_count, rank, bsr, units_est, revenue_est, raw_json,
              source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "legacy-amazon", "amz-listing", "brand-1", "2026-09-19",
                    "amazon", "Amazon", 20, 100, 100, 100, None, None,
                    '{"provider":"amazon_scrape"}', "scrape", "2026-09-19T01:00:00",
                ),
                (
                    "legacy-dtc-1", "dtc-listing", "brand-1", "2026-09-17",
                    "dtc", "DTC", 25, 100, None, None, None, None,
                    '{}', "scrape", "2026-09-17T01:00:00",
                ),
                (
                    "legacy-dtc-2", "dtc-listing", "brand-1", "2026-09-19",
                    "dtc", "DTC", 25, 102, None, None, None, None,
                    '{}', "scrape", "2026-09-19T01:00:00",
                ),
                # Existing observations are authoritative and must not be
                # re-labelled as a fallback model estimate.
                (
                    "observed-amazon", "observed-listing", "brand-1", "2026-09-19",
                    "amazon", "Amazon", 20, 100, 100, 100, 7, 140,
                    '{}', "sellersprite", "2026-09-19T01:00:00",
                ),
                (
                    "observed-units", "observed-units-listing", "brand-1", "2026-09-19",
                    "amazon", "Amazon", 20, 100, 100, 100, 7, None,
                    '{}', "sellersprite", "2026-09-19T01:00:00",
                ),
            ],
        )

        changed = backfill_sales_metrics(conn)
        self.assertGreater(changed, 0)
        amazon = conn.execute("SELECT * FROM sales_metrics WHERE id = 'legacy-amazon'").fetchone()
        self.assertEqual(100, amazon["units_est"])
        self.assertEqual(2000.0, amazon["revenue_est"])
        self.assertEqual("amazon_bsr_curve", amazon["estimate_method"])
        dtc = conn.execute("SELECT * FROM sales_metrics WHERE id = 'legacy-dtc-2'").fetchone()
        self.assertEqual(50, dtc["units_est"])
        self.assertEqual(1250.0, dtc["revenue_est"])
        observed = conn.execute("SELECT * FROM sales_metrics WHERE id = 'observed-amazon'").fetchone()
        self.assertEqual(7, observed["units_est"])
        self.assertEqual(140.0, observed["revenue_est"])
        self.assertIsNone(observed["estimate_method"])
        observed_units = conn.execute("SELECT * FROM sales_metrics WHERE id = 'observed-units'").fetchone()
        self.assertEqual(140.0, observed_units["revenue_est"])
        self.assertIsNone(observed_units["estimate_method"])

        # A second run makes no writes and reports no changes.
        self.assertEqual(0, backfill_sales_metrics(conn))
        conn.close()

    def test_backfill_supports_legacy_tuple_rows_and_recovers_saved_rank_levels(self):
        conn = sqlite3.connect(":memory:")
        # Deliberately keep the default tuple row factory while retaining the
        # additive columns introduced by the feature.
        conn.executescript(
            """
            CREATE TABLE sales_metrics (
              id TEXT PRIMARY KEY, link_id TEXT, channel TEXT, price REAL,
              review_count INTEGER, snapshot_date TEXT, units_est INTEGER,
              revenue_est REAL, rank INTEGER, bsr INTEGER, raw_json TEXT,
              category_rank INTEGER, subcategory_rank INTEGER,
              estimate_method TEXT, estimate_confidence TEXT,
              estimate_period_days REAL, created_at TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO sales_metrics
              (id, link_id, channel, price, review_count, snapshot_date,
               units_est, revenue_est, rank, bsr, raw_json, created_at)
            VALUES ('legacy-row', 'listing-1', 'amazon', 20, NULL, '2026-09-19',
                    NULL, NULL, NULL, NULL,
                    '{"rank_levels":{"category_rank":400,"subcategory_rank":9}}',
                    '2026-09-19T01:00:00')
            """
        )

        changed = backfill_sales_metrics(conn)

        self.assertEqual(1, changed)
        row = conn.execute("SELECT * FROM sales_metrics WHERE id = 'legacy-row'").fetchone()
        # 1000 / sqrt(400) = 50 units/day.
        self.assertEqual(50, row[6])
        self.assertEqual(1000.0, row[7])
        self.assertEqual(400, row[11])
        self.assertEqual(9, row[12])
        conn.close()


class SalesSummaryChannelCoverageTests(unittest.TestCase):
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
            CREATE TABLE links (
              id TEXT PRIMARY KEY, brand_id TEXT, product_id TEXT,
              dimension TEXT, channel TEXT, status TEXT
            );
            CREATE TABLE sales_listings (
              id TEXT PRIMARY KEY, brand_id TEXT, product_id TEXT,
              channel TEXT, monitor INTEGER, status TEXT
            );
            """
        )
        self.conn.executescript(
            """
            ALTER TABLE sales_metrics ADD COLUMN category_rank INTEGER;
            ALTER TABLE sales_metrics ADD COLUMN subcategory_rank INTEGER;
            ALTER TABLE sales_metrics ADD COLUMN category_name TEXT;
            ALTER TABLE sales_metrics ADD COLUMN subcategory_name TEXT;
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_configured_amazon_and_dtc_channels_survive_without_snapshots(self):
        self.conn.executemany(
            "INSERT INTO links VALUES (?, ?, NULL, 'sales', ?, 'active')",
            [("amazon-link", "b1", "amazon"), ("dtc-link", "b1", "dtc")],
        )
        self.conn.execute(
            "INSERT INTO sales_listings VALUES ('amazon-listing', 'b1', NULL, 'amazon', 1, 'active')"
        )

        result = sales_summary(brand_id="b1", days=30, conn=self.conn)
        channels = {item["channel"]: item for item in result["channels"]}

        self.assertIn("amazon", channels)
        self.assertIn("dtc", channels)
        self.assertEqual(0, channels["amazon"]["data_points"])
        self.assertEqual(0, channels["dtc"]["data_points"])
        self.assertEqual(1, channels["amazon"]["configured_listings"])
        self.assertEqual("configured_no_snapshot", channels["amazon"]["coverage_status"])
        self.assertEqual(
            {"amazon", "dtc"},
            {item["channel"] for item in result["link_counts"]},
        )

    def test_summary_exposes_category_and_subcategory_rank_averages(self):
        self.conn.execute(
            """
            INSERT INTO sales_metrics (
              id, link_id, brand_id, snapshot_date, channel, platform,
              review_count, rating, rank, bsr, category_rank, subcategory_rank,
              changes_json, source, raw_json, created_at
            ) VALUES (
              'metric-1', 'listing-1', 'b1', '2026-09-19', 'amazon', 'Amazon',
              20, 4.5, 10, 10, 10, 3, '[]', 'scrape', '{}', '2026-09-19T01:00:00'
            )
            """
        )

        result = sales_summary(brand_id="b1", days=30, conn=self.conn)

        self.assertEqual(10, result["global_metrics"]["category_rank_avg"])
        self.assertEqual(3, result["global_metrics"]["subcategory_rank_avg"])
        self.assertEqual(10, result["trend"][-1]["category_rank_avg"])
        self.assertEqual(3, result["trend"][-1]["subcategory_rank_avg"])


if __name__ == "__main__":
    unittest.main()
