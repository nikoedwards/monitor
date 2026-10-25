import unittest

from server.domains.content import _highlight_terms, _p0_summary, _problem_summary, _voice_source_key


class VocSourceTests(unittest.TestCase):
    def test_maps_cross_module_records_into_voice_sources(self):
        cases = [
            ({"dimension": "voc", "channel": "amazon", "source_id": "amazon_reviews"}, "sales_reviews"),
            ({"dimension": "marketing", "channel": "social", "platform": "youtube"}, "marketing_videos"),
            ({"dimension": "marketing", "channel": "community", "platform": "reddit"}, "social_posts_comments"),
            ({"dimension": "marketing", "channel": "creators", "platform": "tiktok"}, "creator_comments"),
            ({"dimension": "voc", "channel": "app", "source_id": "app_store_reviews"}, "app_reviews"),
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


if __name__ == "__main__":
    unittest.main()
