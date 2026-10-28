import sqlite3
import unittest

from server.connectors.base import ConnectorSpec, run_collector
from server.db import SCHEMA
from server.domains.common import query_records
from server.records import insert_record
from server.relevance import marketing_payload_relevance


class MarketingRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO brands (id, name, monitoring_keywords_json, created_at, updated_at) "
            "VALUES ('brand-1', 'PLAUD', '[\"PLAUD AI\"]', '2026-10-01', '2026-10-01')"
        )

    def tearDown(self):
        self.conn.close()

    def test_google_news_requires_visible_brand_evidence(self):
        base = {
            "dimension": "marketing",
            "channel": "media",
            "source_id": "google_news",
            "raw": {"query": "PLAUD"},
        }
        unrelated = {**base, "title": "Two murders and a chilling confession", "body": "A viral video"}
        relevant = {**base, "title": "PLAUD launches a new recorder", "body": "PLAUD AI"}
        self.assertFalse(marketing_payload_relevance(unrelated, {"name": "PLAUD"})[0])
        self.assertTrue(marketing_payload_relevance(relevant, {"name": "PLAUD"})[0])

    def test_configured_social_scope_does_not_require_brand_token(self):
        allowed, reason = marketing_payload_relevance(
            {
                "dimension": "marketing",
                "channel": "social",
                "source_id": "social_accounts",
                "title": "A tutorial",
                "body": "The brand name is omitted from the caption.",
            },
            {"name": "PLAUD"},
        )
        self.assertTrue(allowed)
        self.assertEqual("configured_scope", reason)

    def test_shared_runner_filters_explicitly_unrelated_discovery_rows(self):
        self.conn.execute(
            "INSERT INTO sources (id, name, category, created_at) "
            "VALUES ('discovery-test', 'Discovery test', 'media', '2026-10-01')"
        )

        def collect(_conn, _brand):
            return [
                {
                    "source_id": "discovery-test",
                    "brand_id": "brand-1",
                    "external_id": "noise-1",
                    "data_type": "media_mention",
                    "dimension": "marketing",
                    "channel": "media",
                    "title": "A completely unrelated story",
                    "body": "No matching brand evidence.",
                    "raw": {"query": "PLAUD"},
                }
            ]

        spec = ConnectorSpec(
            id="discovery-test",
            name="Discovery test",
            category="media",
            dimension="marketing",
            tier=1,
            collect=collect,
        )
        result = run_collector(self.conn, spec, {"id": "brand-1", "name": "PLAUD"})
        self.assertEqual(1, result["filtered"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0])

    def test_record_queries_hide_legacy_discovery_noise(self):
        for record_id, title in (("noise-1", "Unrelated story"), ("hit-1", "PLAUD AI product news")):
            insert_record(
                self.conn,
                {
                    "id": record_id,
                    "source_id": "legacy_discovery",
                    "brand_id": "brand-1",
                    "external_id": record_id,
                    "data_type": "media_mention",
                    "dimension": "marketing",
                    "channel": "media",
                    "title": title,
                    "body": title,
                    "occurred_at": "2026-10-28T00:00:00+00:00",
                    "raw": {"query": "PLAUD"},
                },
            )
        rows = query_records(self.conn, {"brand_id": "brand-1", "channel": "media"}, limit=10)
        self.assertEqual(["hit-1"], [row["id"] for row in rows])


if __name__ == "__main__":
    unittest.main()
