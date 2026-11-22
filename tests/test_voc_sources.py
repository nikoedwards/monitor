import unittest
from unittest.mock import patch

from server.domains.content import _issue_impact, _issue_priority, _load_voice_records, _p0_summary, _topic_breakdown, _voice_source_key
from server.domains.common import build_trend
from server.domains.insights import _summary_records
from server.voc_issues import analyze_issue


class VocSourceTests(unittest.TestCase):
    def test_maps_cross_module_records_into_voice_sources(self):
        cases = [
            ({"dimension": "voc", "channel": "amazon", "source_id": "amazon_reviews"}, "sales_reviews"),
            ({"dimension": "marketing", "channel": "social", "platform": "youtube"}, "marketing_videos"),
            ({"dimension": "marketing", "channel": "community", "platform": "reddit"}, "social_posts_comments"),
            ({"dimension": "marketing", "channel": "creators", "platform": "tiktok"}, "creator_comments"),
            ({"dimension": "voc", "channel": "app", "source_id": "app_store_reviews"}, "app_reviews"),
            ({"dimension": "platform", "channel": "app", "source_id": "app_store_reviews", "platform": "app_store"}, "app_reviews"),
            ({"dimension": "voc", "channel": "app", "source_id": "google_play_reviews", "platform": "google_play"}, "app_reviews"),
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
        issue = analyze_issue(record)
        self.assertEqual("磁吸保护壳与 iPhone Duo 尺寸不匹配", issue["title"])
        self.assertIn("too big", issue["evidence"]["excerpt"])
        self.assertTrue(all(text in record["body"] for text in issue["evidence"]["highlights"]))

    def test_separates_issue_titles_and_keeps_multiple_original_reviews(self):
        reviews = [
            {"id": "apple", "sentiment": "negative", "topics": ["experience"], "voice_source": "app_reviews",
             "body": "Excellent tool except how come this app has no dark mode! It is a pain to read long summaries at night.",
             "url": "https://apps.apple.com/us/app/example/id123"},
            {"id": "play", "sentiment": "negative", "topics": ["feature"], "voice_source": "app_reviews",
             "body": "Please add dark mode for reading.", "external_id": "US:8ff5eb30-fbac-431f-b852-0754ff9f039f",
             "url": "https://play.google.com/store/apps/details?id=com.example&gl=us"},
            {"id": "manual", "sentiment": "negative", "topics": ["experience"],
             "body": "Cannot log in after resetting my password."},
            {"id": "legacy-play", "sentiment": "negative", "topics": ["experience"],
             "body": "Cannot connect to Wi-Fi.", "external_id": "US:com.example:US:feedback",
             "url": "https://play.google.com/store/apps/details?id=com.example&gl=us"},
        ]
        issues = {issue["id"]: issue for issue in _topic_breakdown(reviews)}
        self.assertEqual({"dark_mode", "login", "wifi"}, set(issues))
        dark = issues["dark_mode"]
        self.assertEqual("App 缺少深色模式", dark["title"])
        self.assertIn("夜间阅读长篇摘要", dark["problem_summary"])
        self.assertEqual(2, dark["total"])
        apple, play = dark["evidence"]
        self.assertEqual("how come this app has no dark mode!", apple["highlights"][0])
        self.assertIn("read long summaries at night", apple["excerpt"])
        self.assertEqual("store", apple["url_kind"])
        self.assertEqual("review", play["url_kind"])
        self.assertIn("reviewId=8ff5eb30-fbac-431f-b852-0754ff9f039f", play["url"])
        self.assertEqual("store", issues["wifi"]["evidence"][0]["url_kind"])

    def test_french_feature_requests_surface_from_neutral_feedback(self):
        feedback = [
            {"id": "template", "sentiment": "neutral", "title": "Petites icones pour les modèles personnalisés",
             "body": "Présenter les modèles personnalisés en petites icones ou en liste, pour pouvoir en voir plus d'un coup, au lieu de devoir défiler"},
            {"id": "vocabulary", "sentiment": "neutral", "title": "Créer plusieurs vocabulaires personnalisés à affecter à chaque dossier",
             "body": "Créer plusieurs vocabulaires personnalisés à affecter à chaque dossier, que l'on pourrait activer et désactiver selon le dossier."},
        ]
        issues = {issue["id"]: issue for issue in _topic_breakdown(feedback)}
        self.assertIn("减少逐个滚动", issues["template_view"]["problem_summary"])
        self.assertIn("按文件夹启用或停用", issues["folder_vocabulary"]["problem_summary"])
        self.assertEqual(0, issues["folder_vocabulary"]["negative"])

    def test_insights_summary_records_include_marketing_voice_sources(self):
        records_by_filter = {
            ("voc", None): [{"id": "manual", "dimension": "voc", "source_id": "manual_csv", "body": "manual feedback"}],
            ("marketing", "social"): [{"id": "video", "dimension": "marketing", "channel": "social", "platform": "youtube", "body": "video feedback"}],
            ("marketing", "community"): [{"id": "community", "dimension": "marketing", "channel": "community", "platform": "reddit", "body": "community feedback"}],
            ("marketing", "creators"): [{"id": "creator", "dimension": "marketing", "channel": "creators", "platform": "tiktok", "body": "creator feedback"}],
            ("platform", "app"): [{"id": "app-metric", "dimension": "platform", "channel": "app", "source_id": "app_store_reviews", "platform": "app_store", "body": "US App Store rating count"}],
        }

        def fake_query_records(_conn, filters, limit=200, *, include_sentiment_explanation=True):
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
                {"id": "play-metric", "dimension": "platform", "channel": "app", "source_id": "google_play_reviews", "platform": "google_play", "data_type": "app_metric"},
                {"id": "review", "dimension": "voc", "channel": "app", "source_id": "app_store_reviews", "data_type": "user_voice", "body": "The app is useful."},
            ],
        }

        def fake_query_records(_conn, filters, limit=200, *, include_sentiment_explanation=True):
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

    def test_issue_priority_uses_relative_post_metrics(self):
        negative = [{
            "sentiment": "negative",
            "intent": "observation",
            "metrics": {"views": 900},
        }]
        comparison = negative + [
            {"sentiment": "positive", "metrics": {"views": 10}},
            {"sentiment": "neutral", "metrics": {"views": 20}},
            {"sentiment": "positive", "metrics": {"views": 50}},
            {"sentiment": "neutral", "metrics": {"views": 100}},
        ]

        impact = _issue_impact(negative, comparison)

        self.assertEqual("high", _issue_priority(negative, comparison))
        self.assertEqual(1.0, impact["relative"]["views"]["percentile"])
        self.assertEqual(5, impact["comparison_count"])

    def test_issue_priority_does_not_use_absolute_views_alone(self):
        negative = [{
            "sentiment": "negative",
            "intent": "observation",
            "metrics": {"views": 10_000},
        }]
        comparison = negative + [
            {"sentiment": "positive", "metrics": {"views": 20_000}},
            {"sentiment": "neutral", "metrics": {"views": 30_000}},
            {"sentiment": "positive", "metrics": {"views": 40_000}},
            {"sentiment": "neutral", "metrics": {"views": 50_000}},
        ]

        impact = _issue_impact(negative, comparison)

        self.assertEqual("low", _issue_priority(negative, comparison))
        self.assertLess(impact["relative"]["views"]["percentile"], 0.75)

if __name__ == "__main__":
    unittest.main()
