import sqlite3
import unittest
from unittest.mock import patch

from server.db import SCHEMA
from server.domains.hiring import analyze_hiring, hiring_strategic_signals
from server.strategic_signals import _model_packet, build_strategic_signals
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
            first = build_strategic_signals(self.conn, "brand-1", scope="range", start_date="2026-10-01", end_date="2026-10-31", refresh=True)
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

    def test_model_packet_keeps_each_source_without_sending_full_evidence(self):
        domains = ["hiring", "voice", "sales", "marketing", "web"]
        rows = [
            {"id": f"{domain}-{index}", "domain": domain, "kind": "record",
             "detail": "证据" * 500, "metrics": {"count": index, "raw": ["unused"]}}
            for domain in domains for index in range(50)
        ]
        result = _model_packet({"evidence": rows})["evidence"]
        self.assertEqual(len(result), 36)
        self.assertEqual({row["domain"] for row in result}, set(domains))
        self.assertTrue(all(len(row["detail"]) <= 340 and "raw" not in row["metrics"] for row in result))
        self.assertEqual(len(rows[0]["detail"]), 1000)

    def test_previous_strategy_survives_a_changed_history_window(self):
        ai.put_cached_summary(
            self.conn, "strategic-signals:strategic-signals-v3|brand-1|all_history|2026-01-01|2026-10-01|old",
            {"headline": {"summary": "Earlier verified result"}, "analysis_status": "complete",
             "window": {"scope": "all_history", "start": "2026-01-01", "end": "2026-10-01"}},
        )
        result = build_strategic_signals(self.conn, "brand-1")
        self.assertEqual(result["headline"]["summary"], "Earlier verified result")
        self.assertEqual(result["window"]["end"], "2026-10-01")
        self.assertTrue(result["previous_result"])
        self.assertTrue(result["generated_at"])
        other_range = build_strategic_signals(self.conn, "brand-1", scope="range", start_date="2026-11-01", end_date="2026-11-20")
        self.assertFalse(other_range.get("previous_result"))


if __name__ == "__main__":
    unittest.main()
