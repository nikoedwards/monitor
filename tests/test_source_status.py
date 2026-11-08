from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from server.connectors.registry import sync_to_db
from server.db import SCHEMA
from server.domains.sources import monitoring_status
from server.connectors.base import run_collector
from server.connectors.registry import get_spec
from server.connectors.creators.base import CreatorPost
from server.fetchers import FetchError
from server.scheduler import _collector_is_due


class SourceStatusTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        sync_to_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_brand_status_uses_brand_run_instead_of_global_source_stats(self):
        self.conn.execute(
            "UPDATE sources SET last_collect_at = ?, last_status = ?, item_count = ? WHERE id = ?",
            ("2026-09-18T01:00:00+00:00", "error", 99, "youtube_search"),
        )
        self.conn.execute(
            """
            INSERT INTO source_brand_runs
                (source_id, brand_id, last_collect_at, last_status, last_error, item_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("youtube_search", "brand-1", "2026-09-18T02:00:00+00:00", "ok", "", 3),
        )

        result = monitoring_status(brand_id="brand-1", dimension="marketing", conn=self.conn)
        youtube = next(item for item in result["sources"] if item["id"] == "youtube_search")
        self.assertEqual(youtube["last_status"], "ok")
        self.assertEqual(youtube["last_collect_at"], "2026-09-18T02:00:00+00:00")
        self.assertEqual(youtube["item_count"], 3)

        other = monitoring_status(brand_id="brand-2", dimension="marketing", conn=self.conn)
        other_youtube = next(item for item in other["sources"] if item["id"] == "youtube_search")
        self.assertIsNone(other_youtube["last_status"])
        self.assertIsNone(other_youtube["last_collect_at"])
        self.assertEqual(other_youtube["item_count"], 0)

    def social_link(self, platform, status="ok", last=None):
        self.conn.execute(
            "INSERT INTO links (id, brand_id, dimension, channel, platform, url, status, last_status, last_collect_at, last_error, created_at, updated_at) "
            "VALUES (?, 'brand-1', 'marketing', 'social', ?, ?, 'active', ?, ?, ?, '2026-01-01', '2026-01-01')",
            (platform, platform, f"https://www.{platform}.com/example/", status, last, "test error" if status == "error" else ""),
        )

    @patch("server.connectors.social._public_social_posts", side_effect=FetchError("HTTP 429"))
    @patch("server.connectors.social._youtube_posts", return_value=([CreatorPost(platform="youtube", external_id="v1", url="https://youtube.com/watch?v=v1", title="Post", body="Post")], "channel"))
    def test_partial_failure_still_inserts_successful_posts_and_surfaces_error(self, youtube, instagram):
        self.social_link("instagram")
        self.social_link("youtube")
        result = run_collector(self.conn, get_spec("social_accounts"), {"id": "brand-1", "_force_collect": True})
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["status"], "partial")
        self.assertIn("instagram", result["error"])
        run = self.conn.execute("SELECT * FROM source_brand_runs WHERE source_id='social_accounts'").fetchone()
        self.assertEqual(run["last_status"], "partial")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM records").fetchone()[0], 1)

    @patch("server.connectors.social._public_social_posts", side_effect=FetchError("HTTP 429"))
    def test_all_accounts_failing_is_error_even_when_batch_empty(self, instagram):
        self.social_link("instagram")
        result = run_collector(self.conn, get_spec("social_accounts"), {"id": "brand-1", "_force_collect": True})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["created"], 0)

    def test_failed_account_retries_hourly_despite_successful_daily_brand_run(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        last = (now - timedelta(hours=2)).isoformat()
        self.social_link("instagram", "error", last)
        self.social_link("youtube", "ok", last)
        self.conn.execute("INSERT INTO source_brand_runs VALUES ('social_accounts','brand-1',?,'ok','',20)", (last,))
        self.assertTrue(_collector_is_due(self.conn, "social_accounts", "brand-1", "daily", now))
        self.assertFalse(_collector_is_due(self.conn, "social_accounts", "brand-1", "daily", now - timedelta(minutes=90)))
        result = monitoring_status(brand_id="brand-1", dimension="marketing", conn=self.conn)
        social = next(s for s in result["sources"] if s["id"] == "social_accounts")
        self.assertEqual(social["last_status"], "partial")
        self.assertEqual(len(social["accounts"]), 2)
        self.assertEqual(social["next_collect_at"], (now - timedelta(hours=1)).isoformat())

    def test_status_reports_source_specific_next_due_time(self):
        self.conn.execute(
            """
            INSERT INTO source_brand_runs
                (source_id, brand_id, last_collect_at, last_status, last_error, item_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            # Keep the deadline in the future so this assertion stays
            # deterministic as the calendar advances.
            ("youtube_search", "brand-1", "2099-01-01T00:00:00+00:00", "ok", "", 0),
        )

        result = monitoring_status(brand_id="brand-1", dimension="marketing", conn=self.conn)
        youtube = next(item for item in result["sources"] if item["id"] == "youtube_search")

        self.assertEqual(youtube["next_collect_at"], "2099-01-02T00:00:00+00:00")
        self.assertFalse(youtube["due"])


if __name__ == "__main__":
    unittest.main()
