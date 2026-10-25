import unittest
from unittest.mock import patch

from server.domains.content import _highlight_terms, _issue_priority, _load_voice_records, _p0_summary, _problem_summary, _topic_breakdown, _voice_source_key
from server.domains.common import build_trend
from server.domains.insights import _summary_records


class VocSourceTests(unittest.TestCase):
    def test_maps_cross_module_records_into_voice_sources(self):
        cases = [
            ({"dimension": "voc", "channel": "amazon", "source_id": "amazon_reviews"}, "sales_reviews"),
            ({"dimension": "marketing", "channel": "social", "platform": "youtube"}, "marketing_videos"),
            ({"dimension": "marketing", "channel": "community", "platform": "reddit"}, "social_posts_comments"),
            ({"dimension": "marketing", "channel": "creators", "platform": "tiktok"}, "creator_comments"),
            ({"dimension": "voc", "channel": "app", "source_id": "app_store_reviews"}, "app_reviews"),
            ({"dimension": "platform", "channel": "app", "source_id": "app_store_reviews", "platform": "app_store"}, "app_reviews"),
            ({"dimension": "voc", "source_id": "manual_csv"}, "manual_feedback"),
        ]

        for record, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, _voice_source_key(record))

    def test_p0_scanner_groups_critical_feedback_and_assigns_team(self):
        records = [
            {
                "id": "one",
                "title": "Critical privacy issue",
                "body": "This looks like a data breach",
                "sentiment": "negative",
                "occurred_at": "2026-07-25T08:00:00+00:00",
                "voice_source": "social_posts_comments",
            },
            {
                "id": "two",
                "title": "普通建议",
                "body": "希望增加导出格式",
                "sentiment": "negative",
                "occurred_at": "2026-07-25T07:00:00+00:00",
                "voice_source": "manual_feedback",
            },
        ]

        result = _p0_summary(records)

        self.assertEqual(1, result["total"])
        self.assertEqual("privacy_security", result["issues"][0]["key"])
        self.assertEqual("experience_team", result["issues"][0]["owner_team"])

    def test_problem_summary_is_specific_and_highlights_sentiment_evidence(self):
        record = {
            "title": "Plaud doesn't fit on the iPhone Duo",
            "body": "The magnetic case doesn't work on it. It's a little too big for the back panel and hangs past the bottom edge.",
            "sentiment": "negative",
            "topics": ["support"],
        }
        self.assertEqual(
            "磁吸保护壳与 iPhone Duo 尺寸不兼容，保护壳过大并影响手机折叠。",
            _problem_summary(record),
        )
        self.assertIn("too big", _highlight_terms(record))

    def test_insights_summary_records_include_marketing_voice_sources(self):
        records_by_filter = {
            ("voc", None): [{"id": "manual", "dimension": "voc", "source_id": "manual_csv", "body": "manual feedback"}],
            ("marketing", "social"): [{"id": "video", "dimension": "marketing", "channel": "social", "platform": "youtube", "body": "video feedback"}],
            ("marketing", "community"): [{"id": "community", "dimension": "marketing", "channel": "community", "platform": "reddit", "body": "community feedback"}],
            ("marketing", "creators"): [{"id": "creator", "dimension": "marketing", "channel": "creators", "platform": "tiktok", "body": "creator feedback"}],
            ("platform", "app"): [{"id": "app-metric", "dimension": "platform", "channel": "app", "source_id": "app_store_reviews", "platform": "app_store", "body": "US App Store rating count"}],
        }

        def fake_query_records(_conn, filters, limit=200):
            return records_by_filter.get((filters.get("dimension"), filters.get("channel")), [])

        with patch("server.domains.content.query_records", side_effect=fake_query_records):
            records = _summary_records(None, "brand-1", "voc", None, "2026-01-01", "2026-01-31")

        self.assertEqual({"manual", "video", "community", "creator", "app-metric"}, {record["id"] for record in records})

    def test_topic_breakdown_merges_same_problem_and_keeps_all_teams(self):
        records = [
            {
                "id": "ads-post",
                "title": "Plaud doesn't fit on the iPhone Duo",
                "body": "The magnetic case doesn't work on it. It's a little too big for the back panel and hangs past the bottom edge, so it...",
                "sentiment": "negative",
                "topics": ["ads"],
            },
            {
                "id": "support-post",
                "title": "Plaud doesn't fit on the iPhone Duo",
                "body": "The magnetic case doesn't work on it. It's a little too big for the back panel and hangs past the bottom edge, so it...",
                "sentiment": "negative",
                "topics": ["support"],
            },
        ]

        result = _topic_breakdown(records)

        self.assertEqual(1, len(result))
        self.assertEqual(2, result[0]["total"])
        self.assertEqual(["ads", "support"], result[0]["topics"])
        self.assertEqual(["marketing_team", "support_team"], result[0]["suggested_teams"])

    def test_voice_trend_contains_total_and_each_sentiment_series(self):
        trend = build_trend(
            [
                {"occurred_at": "2026-10-24T01:00:00+00:00", "sentiment": "positive"},
                {"occurred_at": "2026-10-24T02:00:00+00:00", "sentiment": "neutral"},
                {"occurred_at": "2026-10-24T03:00:00+00:00", "sentiment": "negative"},
            ],
            "2026-10-24",
            "2026-10-24",
        )

        self.assertEqual(
            {"total": 3, "positive": 1, "neutral": 1, "negative": 1},
            {key: trend[0][key] for key in ("total", "positive", "neutral", "negative")},
        )

    def test_voice_records_exclude_app_store_rating_metrics(self):
        records_by_filter = {
            ("platform", "app"): [
                {"id": "metric", "dimension": "platform", "channel": "app", "source_id": "app_store_reviews", "data_type": "app_metric"},
                {"id": "review", "dimension": "voc", "channel": "app", "source_id": "app_store_reviews", "data_type": "user_voice", "body": "The app is useful."},
            ],
        }

        def fake_query_records(_conn, filters, limit=200):
            return records_by_filter.get((filters.get("dimension"), filters.get("channel")), [])

        with patch("server.domains.content.query_records", side_effect=fake_query_records):
            records = _load_voice_records(None, "brand-1", "2026-01-01", "2026-01-31")

        self.assertEqual(["review"], [record["id"] for record in records])

    def test_negative_high_reach_feedback_is_high_priority(self):
        records = [{
            "sentiment": "negative",
            "intent": "complaint",
            "metrics": {"views": 25_000, "comments": 8, "likes": 120},
        }]

        self.assertEqual("high", _issue_priority(records))

    def test_negative_high_discussion_feedback_is_high_priority(self):
        records = [{
            "sentiment": "negative",
            "intent": "observation",
            "metrics": {"comments": 120},
        }]

        self.assertEqual("high", _issue_priority(records))


if __name__ == "__main__":
    unittest.main()
