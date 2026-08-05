import sqlite3
import unittest
from unittest.mock import patch

from server.connectors.creators.base import CreatorPost
from server.connectors.creators.products import (
    creator_product_queries,
    load_product_signals,
    match_record_to_products,
    rebuild_product_matches,
)
from server.connectors.creators.runner import _collect_platform
from server.db import SCHEMA
from server.domains.content import list_records
from server.domains.creators import creators_roster, creators_summary
from server.records import insert_record


class CreatorProductAttributionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO brands (id, name, created_at, updated_at) VALUES ('brand-1', 'Example', '2026-08-01', '2026-08-01')"
        )
        self.conn.execute(
            """
            INSERT INTO products (id, brand_id, name, sku, category, notes, created_at, updated_at)
            VALUES ('p-alpha', 'brand-1', 'Alpha Cam', 'AC-1', 'Camera',
                    'Alpha Pro, 阿尔法相机', '2026-08-01', '2026-08-01')
            """
        )
        self.conn.execute(
            """
            INSERT INTO products (id, brand_id, name, category, created_at, updated_at)
            VALUES ('p-beta', 'brand-1', 'Beta Light', 'Lighting', '2026-08-01', '2026-08-01')
            """
        )
        self._record(
            "r-alpha",
            "Alpha Pro hands-on review",
            "The Alpha Pro is featured throughout this sponsored video.",
            author="Alice",
            handle="alice",
            views=1000,
            engagement=120,
        )
        self._record(
            "r-multi",
            "Alpha Cam vs Beta Light",
            "A practical comparison between Alpha Cam and Beta Light.",
            author="Alice",
            handle="alice",
            views=800,
            engagement=80,
        )
        self._record(
            "r-brand",
            "Example company update",
            "General brand news without a specific product.",
            author="Bob",
            handle="bob",
            views=300,
            engagement=10,
            is_collab=False,
        )

    def tearDown(self):
        self.conn.close()

    def _record(
        self,
        record_id,
        title,
        body,
        *,
        author,
        handle,
        views,
        engagement,
        is_collab=True,
        product_id=None,
    ):
        insert_record(
            self.conn,
            {
                "id": record_id,
                "source_id": "youtube_search",
                "brand_id": "brand-1",
                "product_id": product_id,
                "external_id": record_id,
                "data_type": "creator_post",
                "dimension": "marketing",
                "channel": "creators",
                "platform": "youtube",
                "title": title,
                "body": body,
                "author": author,
                "occurred_at": "2026-08-03T10:00:00+00:00",
                "metrics": {
                    "author_handle": handle,
                    "author_url": f"https://youtube.com/@{handle}",
                    "follower_count": 5000,
                    "views": views,
                    "engagement": engagement,
                    "is_collab": is_collab,
                    "is_sponsored": is_collab,
                    "collab_type": "mention" if is_collab else "none",
                },
            },
        )

    def test_rebuild_matches_supports_aliases_and_multiple_products(self):
        result = rebuild_product_matches(self.conn, "brand-1")

        self.assertEqual(3, result["records"])
        self.assertEqual(2, result["matched_records"])
        matches = self.conn.execute(
            "SELECT record_id, product_id, match_type FROM record_product_matches ORDER BY record_id, product_id"
        ).fetchall()
        self.assertEqual(
            [
                ("r-alpha", "p-alpha", "alias"),
                ("r-multi", "p-alpha", "product_name"),
                ("r-multi", "p-beta", "product_name"),
            ],
            [(row["record_id"], row["product_id"], row["match_type"]) for row in matches],
        )

    def test_summary_roster_and_content_filter_use_product_matches(self):
        brand_summary = creators_summary(
            brand_id="brand-1",
            start_date="2026-08-01",
            end_date="2026-08-05",
            conn=self.conn,
        )
        self.assertEqual(3, brand_summary["totals"]["posts"])
        self.assertEqual(2, brand_summary["totals"]["matched_posts"])
        self.assertEqual(1, brand_summary["totals"]["unmapped_posts"])
        self.assertEqual(
            {"p-alpha": 2, "p-beta": 1},
            {item["product_id"]: item["total"] for item in brand_summary["by_product"]},
        )

        product_summary = creators_summary(
            brand_id="brand-1",
            product_id="p-alpha",
            start_date="2026-08-01",
            end_date="2026-08-05",
            conn=self.conn,
        )
        self.assertEqual(2, product_summary["totals"]["posts"])
        self.assertEqual(1, product_summary["totals"]["creators"])
        self.assertEqual("Alpha Cam", product_summary["scope"]["product"]["name"])
        self.assertEqual(1, len(product_summary["creator_map"]["points"]))

        roster = creators_roster(brand_id="brand-1", product_id="p-beta", conn=self.conn)["roster"]
        self.assertEqual(1, len(roster))
        self.assertEqual("Alice", roster[0]["name"])
        self.assertEqual(1, roster[0]["post_count"])

        records = list_records(
            brand_id="brand-1",
            product_id="p-beta",
            dimension="marketing",
            channel="creators",
            start_date="2026-08-01",
            end_date="2026-08-05",
            conn=self.conn,
        )["records"]
        self.assertEqual(["r-multi"], [record["id"] for record in records])

    def test_existing_record_product_id_is_preserved_as_manual_match(self):
        self._record(
            "r-manual",
            "A creator video with no product wording",
            "The product is only visible on screen.",
            author="Cara",
            handle="cara",
            views=500,
            engagement=50,
            product_id="p-beta",
        )

        rebuild_product_matches(self.conn, "brand-1")
        row = self.conn.execute(
            "SELECT confidence, match_type FROM record_product_matches WHERE record_id = 'r-manual'"
        ).fetchone()
        self.assertEqual("manual", row["match_type"])
        self.assertEqual(1.0, row["confidence"])

    def test_search_query_metadata_is_not_product_evidence(self):
        products = load_product_signals(self.conn, "brand-1")
        matches = match_record_to_products(
            {
                "title": "An unrelated camera review",
                "body": "No catalog product is named here.",
                "raw": {"query": "Alpha Cam", "matched_queries": ["Example Alpha Cam"]},
            },
            products,
        )
        self.assertEqual([], matches)

    def test_product_queries_cover_products_and_collector_drops_search_noise(self):
        queries = creator_product_queries(self.conn, "brand-1", limit=2)
        self.assertEqual(["Example Alpha Cam", "Example Beta Light"], queries)

        class FakeProvider:
            def collect(self, conn, brand, search_queries):
                return [
                    CreatorPost(
                        platform="youtube",
                        external_id="relevant",
                        title="Alpha Cam field review",
                        body="A practical look at Alpha Cam.",
                        author="Reviewer",
                        raw={"query": search_queries[0], "matched_queries": search_queries},
                    ),
                    CreatorPost(
                        platform="youtube",
                        external_id="noise",
                        title="An unrelated lighting tutorial",
                        body="General filming advice only.",
                        author="Other Reviewer",
                        raw={"query": search_queries[0], "matched_queries": search_queries},
                    ),
                ]

        brand = {"id": "brand-1", "name": "Example", "monitoring_keywords": "[]"}
        with patch("server.connectors.creators.runner.pick_provider", return_value=FakeProvider()):
            payloads = _collect_platform(self.conn, brand, "youtube")
        self.assertEqual(["brand-1:youtube:relevant"], [item["external_id"] for item in payloads])


if __name__ == "__main__":
    unittest.main()
