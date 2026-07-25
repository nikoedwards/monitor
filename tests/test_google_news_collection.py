from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from server.connectors import collectors
from server import scheduler
from server.db import SCHEMA
from server.domains.common import query_records
from server.records import insert_record


def _item(guid: str, published_at: datetime) -> dict:
    return {
        "guid": guid,
        "url": f"https://news.google.com/rss/articles/{guid}",
        "title": f"PLAUD story {guid}",
        "description": "PLAUD media coverage",
        "source_name": "Example Media",
        "source_url": "https://example.com",
        "published_at": published_at.isoformat(),
    }


class GoogleNewsCollectionTests(unittest.TestCase):
    def test_google_news_url_adds_global_recency_operator(self) -> None:
        url = collectors._google_news_url("PLAUD")
        query = parse_qs(urlparse(url).query)

        self.assertEqual(query["q"], ["PLAUD when:7d"])
        self.assertEqual(query["hl"], ["en-US"])
        self.assertEqual(query["gl"], ["US"])

    def test_google_news_url_preserves_explicit_recency_operator(self) -> None:
        url = collectors._google_news_url("PLAUD when:30d")
        self.assertEqual(parse_qs(urlparse(url).query)["q"], ["PLAUD when:30d"])

    def test_collection_filters_old_items_and_dedupes_across_keywords(self) -> None:
        now = datetime.now(timezone.utc)
        recent = _item("recent", now - timedelta(hours=6))
        old = _item("old", now - timedelta(days=20))
        brand = {
            "id": "brand-1",
            "name": "PLAUD",
            "monitoring_keywords_json": json.dumps(["PLAUD", "NotePin"]),
        }
        parse_limits: list[int] = []

        def fake_parse_rss(_raw: bytes, *, limit: int) -> list[dict]:
            parse_limits.append(limit)
            return [old, recent]

        publication = {
            "name": "Example Media",
            "est_monthly_traffic": 1000,
            "tier": "tier_4",
            "authority": 25,
            "country": "US",
            "language": "en",
            "icon_url": "",
        }

        with (
            patch.object(collectors, "fetch_bytes", return_value=b"rss"),
            patch.object(collectors, "parse_rss", side_effect=fake_parse_rss),
            patch.object(collectors, "enrich_publication", return_value=publication),
            patch.object(collectors, "classify_media_property", return_value=("earned", 1.0, [])),
            patch.object(collectors, "estimate_ave", return_value=25),
        ):
            payloads = collectors.collect_google_news(None, brand)

        self.assertEqual(parse_limits, [100, 100])
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["external_id"], "brand-1:recent")
        self.assertEqual(payloads[0]["raw"]["query"], "PLAUD")
        self.assertTrue(payloads[0]["raw"]["body_relevance_validated"])
        self.assertEqual(payloads[0]["raw"]["matched_text"], "PLAUD")

    def test_collection_rejects_google_news_results_without_visible_brand_evidence(self) -> None:
        now = datetime.now(timezone.utc)
        unrelated = {
            "guid": "unrelated",
            "url": "https://news.google.com/rss/articles/unrelated",
            "title": "How to Use Read Aloud in Microsoft Edge - Technobezz",
            "description": "How to Use Read Aloud in Microsoft Edge Technobezz",
            "source_name": "Technobezz",
            "source_url": "https://technobezz.com",
            "published_at": now.isoformat(),
        }
        brand = {
            "id": "brand-1",
            "name": "PLAUD",
            "monitoring_keywords_json": json.dumps(["PLAUD AI"]),
        }

        with (
            patch.object(collectors, "fetch_bytes", return_value=b"rss"),
            patch.object(collectors, "parse_rss", return_value=[unrelated]),
        ):
            payloads = collectors.collect_google_news(None, brand)

        self.assertEqual(payloads, [])

    def test_record_queries_hide_legacy_google_news_false_positives(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO brands (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("brand-1", "PLAUD", "2026-07-25", "2026-07-25"),
        )
        common = {
            "source_id": "google_news",
            "brand_id": "brand-1",
            "data_type": "media_mention",
            "dimension": "marketing",
            "channel": "media",
            "platform": "Technobezz",
            "occurred_at": "2026-07-25T03:00:00+00:00",
        }
        insert_record(conn, {
            **common,
            "external_id": "relevant",
            "title": "PLAUD launches a new recorder",
            "body": "PLAUD product announcement",
            "url": "https://news.google.com/rss/articles/relevant",
            "raw": {"query": "PLAUD AI"},
        })
        insert_record(conn, {
            **common,
            "external_id": "unrelated",
            "title": "How to Use Read Aloud in Microsoft Edge",
            "body": "A general browser tutorial",
            "url": "https://news.google.com/rss/articles/unrelated",
            "raw": {"query": "PLAUD AI"},
        })

        records = query_records(conn, {"channel": "media"}, limit=10)

        self.assertEqual([record["external_id"] for record in records], ["relevant"])
        conn.close()

    def test_daily_collector_due_state_is_per_brand(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE source_brand_runs (source_id TEXT, brand_id TEXT, last_collect_at TEXT, "
            "last_status TEXT, last_error TEXT, item_count INTEGER, PRIMARY KEY (source_id, brand_id))"
        )
        now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
        conn.execute(
            "INSERT INTO source_brand_runs VALUES (?, ?, ?, ?, ?, ?)",
            ("google_web_search", "brand-1", (now - timedelta(hours=23)).isoformat(), "ok", "", 0),
        )

        self.assertFalse(scheduler._collector_is_due(conn, "google_web_search", "brand-1", "daily", now))
        self.assertTrue(scheduler._collector_is_due(conn, "google_web_search", "brand-2", "daily", now))
        self.assertTrue(scheduler._collector_is_due(conn, "google_web_search", "brand-1", "daily", now + timedelta(hours=2)))


if __name__ == "__main__":
    unittest.main()
