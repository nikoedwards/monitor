import sqlite3
import unittest

from server.db import SCHEMA
from server.domains.hiring import hiring_strategic_signals


class StrategicSignalsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO brands (id, name, created_at, updated_at) VALUES ('brand-1', 'Example', ?, ?)",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )

    def tearDown(self):
        self.conn.close()

    def test_all_history_is_independent_of_current_picker_window(self):
        self.conn.executemany(
            """
            INSERT INTO job_postings (
                id, brand_id, platform, title, department, city, jd_text, status,
                first_seen, last_seen, business_tags_json, created_at, updated_at
            ) VALUES (?, 'brand-1', 'linkedin', ?, ?, ?, ?, 'open', ?, ?, '[]', ?, ?)
            """,
            [
                ("old-job", "Backend Engineer", "", "San Francisco", "Build services", "2026-01-05", "2026-01-10", "2026-01-05", "2026-01-10"),
                ("new-job", "Sales Manager", "销售", "New York", "Sell globally", "2026-11-10", "2026-11-15", "2026-11-10", "2026-11-15"),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO records (
                id, source_id, brand_id, data_type, dimension, channel, title, body,
                occurred_at, created_at
            ) VALUES ('voice-1', 'source-1', 'brand-1', 'review', 'voc', 'platform', '体验反馈', '功能体验反馈', '2026-11-15', '2026-11-15')
            """
        )

        result = hiring_strategic_signals("brand-1", scope="all_history", conn=self.conn)

        self.assertEqual(result["window"]["scope"], "all_history")
        self.assertEqual(result["window"]["start"], "2026-01-05")
        self.assertEqual(result["window"]["end"], "2026-11-15")
        self.assertEqual(result["schema_version"], "strategic-signals-v1")
        self.assertIn("organization_estimate", result["details"])
        self.assertIn("timeline", result)

    def test_explicit_range_is_available_for_secondary_detail(self):
        result = hiring_strategic_signals(
            "brand-1",
            scope="range",
            start_date="2026-10-01",
            end_date="2026-10-31",
            conn=self.conn,
        )
        self.assertEqual(result["window"], {"scope": "range", "start": "2026-10-01", "end": "2026-10-31"})

    def test_invalid_scope_is_rejected(self):
        with self.assertRaises(Exception):
            hiring_strategic_signals("brand-1", scope="7d", conn=self.conn)


if __name__ == "__main__":
    unittest.main()
