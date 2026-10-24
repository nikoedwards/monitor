import sqlite3
import unittest
from unittest.mock import patch

from server.domains.content import (
    _marketing_channel_snapshot,
    _marketing_fallback_summary,
    _marketing_previous_window,
)


class MarketingOverviewTests(unittest.TestCase):
    def test_previous_window_matches_equal_length(self):
        self.assertEqual(("2026-09-01", "2026-09-30"), _marketing_previous_window("2026-10-01", "2026-10-30"))

    def test_social_channel_snapshot_keeps_metrics_and_empty_state(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        records = [
            {"data_type": "social_post", "occurred_at": "2026-10-02T00:00:00Z", "metrics": {"views": 100, "likes": 5, "comments": 2, "engagement": 7}},
            {"data_type": "social_post", "occurred_at": "2026-10-03T00:00:00Z", "metrics": {"views": 50, "likes": 3, "comments": 1, "engagement": 4}},
        ]
        with patch("server.domains.content.query_records", return_value=records), patch("server.domains.content.build_trend", return_value=[]):
            result = _marketing_channel_snapshot(conn, "brand-1", "social", "2026-10-01", "2026-10-03")
        self.assertEqual("ready", result["status"])
        self.assertEqual(150, result["total_views"])
        self.assertEqual(11, result["total_engagement"])

    def test_fallback_summary_mentions_yesterday_and_signals(self):
        payload = {
            "range": {"start": "2026-10-01", "end": "2026-10-03"},
            "modules": {"media": {"total": 2}, "creators": {"total": 0}, "social": {"total": 1}, "community": {"total": 0}, "ads": {"total_ads": 3}},
            "signals": [{"domain": "ads", "severity": "medium", "title": "广告素材有生命周期变化", "detail": "新增 2 条。"}],
            "yesterday": {"date": "2026-10-02", "has_data": True, "channels": {"ads": {"total_ads": 1}}},
        }
        result = _marketing_fallback_summary(payload)
        self.assertEqual("fallback", result["mode"])
        self.assertIn("营销共收录 3 条", result["summary"])
        self.assertEqual("2026-10-02", result["yesterday_summary"].split("：")[0])
        self.assertEqual("ads", result["highlights"][0]["domain"])


if __name__ == "__main__":
    unittest.main()
