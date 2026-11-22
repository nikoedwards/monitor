import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server import ai
from server.db import SCHEMA
from server.domains.insights import _fallback_summary, _overview_payload, _previous_window, overview, overview_summary


class OverviewAggregationTests(unittest.TestCase):
    @patch("server.domains.insights._overview_payload")
    @patch("server.domains.insights.ai.get_cached_summary", return_value={"summary": "已有分析"})
    def test_cached_summary_does_not_rebuild_overview(self, _cache, payload):
        result = overview_summary("brand-1", start_date="2026-10-01", end_date="2026-10-30", conn=object())
        self.assertEqual({"summary": "已有分析", "cached": True}, result)
        payload.assert_not_called()

    def test_previous_window_matches_equal_length(self):
        self.assertEqual(("2026-09-01", "2026-09-30"), _previous_window("2026-10-01", "2026-10-30"))

    @patch("server.domains.insights._yesterday", return_value={"date": "2026-09-30", "has_data": True})
    @patch("server.domains.insights.query_records", return_value=[{"id": "r1"}, {"id": "r2"}])
    @patch("server.domains.insights._collect_modules")
    def test_payload_contains_period_comparisons_and_priority_signals(self, collect, _records, _yesterday):
        current = {
            "sales": {"status": "ready", "total_revenue": 120, "total_units": 12, "data_points": 2, "global_metrics": {"changed_listings": 2}, "last_data_at": "2026-10-01"},
            "market": {"status": "ready", "current": {"share": 40, "change_pp": -2}, "last_data_at": "2026-10-01"},
            "hiring": {"status": "ready", "total_open": 8, "released_in_window": 3, "closed_in_window": 1},
            "marketing": {"status": "ready", "total": 9, "ads": {"changed": 2}},
            "voice": {"status": "ready", "totals": {"total": 6, "negative": 3, "negative_rate": 0.5}, "p0": {"total": 1}, "topics": []},
            "web": {"status": "empty", "changed": 0},
        }
        previous = {
            "sales": {"total_revenue": 100, "total_units": 10},
            "market": {"status": "ready", "current": {"share": 42}},
            "hiring": {"total_open": 5},
            "marketing": {"total": 4},
            "voice": {"totals": {"total": 4, "negative_rate": 0.25}},
            "web": {"changed": 0},
        }
        collect.side_effect = [current, previous]

        payload = _overview_payload(object(), "brand-1", "2026-10-01", "2026-10-30", "brand-1,brand-2")

        self.assertEqual("2026-09-01", payload["range"]["previous_start"])
        self.assertEqual(20, payload["kpis"]["sales"]["delta"])
        self.assertEqual(3, payload["kpis"]["hiring_open"]["delta"])
        self.assertTrue(any(signal["domain"] == "market" for signal in payload["signals"]))
        self.assertTrue(any(signal["domain"] == "voice" and signal["severity"] == "high" for signal in payload["signals"]))

    def test_fallback_summary_is_available_without_llm(self):
        payload = {
            "range": {"start": "2026-10-01", "end": "2026-10-30"},
            "kpis": {"records_total": 3},
            "yesterday": {"date": "2026-10-29", "sales_revenue": 10, "marketing_total": 2, "voc_negative": 1},
            "modules": {
                "sales": {"data_points": 1, "total_revenue": 10, "total_units": 1},
                "voice": {"totals": {"total": 1, "negative_rate": 1}, "p0": {"total": 0}},
                "marketing": {"total": 2, "ads": {"total": 1, "changed": 0}},
                "hiring": {"total_postings": 0},
            },
        }
        result = _fallback_summary(payload)
        self.assertEqual("fallback", result["mode"])
        self.assertIn("销售额估算", result["summary"])
        self.assertEqual("2026-10-29", result["yesterday_summary"].split("：")[0])


class OverviewBackgroundSummaryTests(unittest.TestCase):
    def test_preview_and_cached_summary_survive_slow_and_failed_model(self):
        payload = {"modules": {}, "kpis": {}, "coverage": {}, "range": {"start": "2026-10-01", "end": "2026-10-30"}}
        entered = threading.Event()
        release = threading.Event()

        def slow_model(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release model")
            return {"summary": "完成研判"}

        with tempfile.TemporaryDirectory() as directory:
            conn = sqlite3.connect(Path(directory) / "overview.db", isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA)
            params = {"brand_id": "brand", "start_date": "2026-10-01", "end_date": "2026-10-30", "conn": conn}

            def read():
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    result = overview_summary(**params, background=True, cached_only=True)
                    if result["generation"]["status"] != "running":
                        return result
                    time.sleep(0.01)
                self.fail("Background summary did not complete")

            try:
                with patch("server.domains.insights._overview_payload", side_effect=lambda *args: dict(payload)), \
                     patch("server.domains.insights.build_overview_evidence", return_value={}), \
                     patch("server.domains.insights.ai.is_configured", return_value=True), \
                     patch("server.domains.insights.ai.summarize_overview", side_effect=slow_model) as model:
                    self.assertEqual("fallback", overview(**params)["summary_preview"]["mode"])
                    first = overview_summary(**params, background=True)
                    try:
                        self.assertEqual("running", first["generation"]["status"])
                        self.assertTrue(entered.wait(2))
                        resumed = overview_summary(**params, background=True, cached_only=True)
                        self.assertEqual("running", resumed["generation"]["status"])
                    finally:
                        release.set()
                    self.assertEqual("完成研判", read()["summary"])
                    model.assert_called_once()

                    model.side_effect = ai.LlmError("timed out")
                    refresh = overview_summary(**params, background=True, refresh=True)
                    self.assertEqual("完成研判", refresh["summary"])
                    result = read()
                    self.assertEqual("completed", result["generation"]["status"])
                    self.assertEqual("完成研判", result["summary"])
                    self.assertIn("上次成功", result["summary_notice"])
                    self.assertTrue(overview_summary(**params)["cached"])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
