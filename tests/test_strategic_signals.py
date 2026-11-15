import sqlite3
import unittest
from unittest.mock import patch

from server.db import SCHEMA
from server.domains.hiring import analyze_hiring, hiring_strategic_signals
from server.strategic_signals import build_strategic_signals
from server import ai


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

    def test_strategic_signals_cache_skips_rebuilding_and_model_call(self):
        payload = {
            "range": {"start": "2026-10-01", "end": "2026-10-31", "previous_start": "2026-09-01", "previous_end": "2026-09-30"},
            "modules": {}, "previous_modules": {}, "coverage": {}, "kpis": {}, "signals": [],
        }
        packet = {
            "brand": "Example", "coverage": {}, "source_coverage": {}, "evidence": [], "caveats": [],
        }
        model_result = {"summary": "可缓存研判", "deep_insights": [], "priority_alerts": [], "caveats": []}
        with patch("server.strategic_signals._overview_payload", return_value=payload) as overview, \
             patch("server.strategic_signals.build_overview_evidence", return_value=packet) as evidence, \
             patch("server.strategic_signals.ai.is_configured", return_value=True), \
             patch("server.strategic_signals.ai.summarize_overview", return_value=model_result) as summarize:
            first = build_strategic_signals(self.conn, "brand-1", scope="range", start_date="2026-10-01", end_date="2026-10-31")
            second = build_strategic_signals(self.conn, "brand-1", scope="range", start_date="2026-10-01", end_date="2026-10-31")
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        summarize.assert_called_once()
        overview.assert_called_once()
        evidence.assert_called_once()

    def test_jd_analysis_returns_deterministic_fallback_after_model_failure(self):
        self.conn.execute(
            """
            INSERT INTO job_postings (
                id, brand_id, platform, title, department, city, jd_text, status,
                first_seen, last_seen, business_tags_json, created_at, updated_at
            ) VALUES ('fallback-job', 'brand-1', 'linkedin', 'Backend Engineer', '', '深圳', '负责服务研发', 'open',
                      '2026-10-24T08:00:00+00:00', '2026-10-24T08:00:00+00:00', '[]',
                      '2026-10-24T08:00:00+00:00', '2026-10-24T08:00:00+00:00')
            """
        )
        with patch("server.domains.hiring.ai.is_configured", return_value=True), \
             patch("server.domains.hiring.ai.analyze_jd", side_effect=ai.LlmError("timeout")):
            result = analyze_hiring("brand-1", start_date="2026-10-24", end_date="2026-10-24", conn=self.conn)
        self.assertEqual(result["analysis_status"], "fallback")
        self.assertIn("确定性统计", result["summary"])
        self.assertEqual(result["business_directions"][0]["direction"], "技术研发")


if __name__ == "__main__":
    unittest.main()
