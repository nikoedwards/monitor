import unittest
from unittest.mock import patch

from server.domains.insights import _fallback_summary, _overview_payload, _previous_window


class OverviewAggregationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
