from __future__ import annotations

import sqlite3
import unittest

from server.connectors.registry import sync_to_db
from server.db import SCHEMA
from server.domains.sources import monitoring_status


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


if __name__ == "__main__":
    unittest.main()
