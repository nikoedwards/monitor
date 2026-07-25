import sqlite3
import unittest
from unittest.mock import patch

from server.domains.content import marketing_summary
from test_publication_traffic import PUBLICATIONS_SCHEMA


class MarketingSummaryTests(unittest.TestCase):
    def test_social_metrics_are_aggregated(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(PUBLICATIONS_SCHEMA)
        records = [
            {
                "channel": "social",
                "platform": "youtube",
                "source_id": "social_accounts",
                "data_type": "social_post",
                "occurred_at": "2026-07-24T10:00:00+00:00",
                "metrics": {"views": 3538, "likes": 27, "comments": 3, "engagement": 30},
            },
            {
                "channel": "social",
                "platform": "youtube",
                "source_id": "social_accounts",
                "data_type": "social_post",
                "occurred_at": "2026-07-23T10:00:00+00:00",
                "metrics": {"views": 1000, "likes": 10, "comments": 2, "engagement": 12},
            },
        ]
        with patch("server.domains.content.query_records", return_value=records), patch(
            "server.domains.content.build_trend", return_value=[]
        ):
            summary = marketing_summary(conn=conn, channel="social")

        self.assertEqual(summary["total_views"], 4538)
        self.assertEqual(summary["total_likes"], 37)
        self.assertEqual(summary["total_comments"], 5)
        self.assertEqual(summary["total_engagement"], 42)
        self.assertEqual(summary["engagement_rate"], round(42 / 4538, 6))

    def test_latest_publication_metrics_override_historical_record_values(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(PUBLICATIONS_SCHEMA)
        conn.execute(
            """INSERT INTO publications
            (domain, name, icon_url, est_monthly_traffic, traffic_lower, traffic_upper,
             popularity_rank, traffic_confidence, traffic_as_of, authority, tier,
             country, language, source, updated_at)
            VALUES ('example.com', 'Example Media', '', 1500000, 700000, 3300000,
                    42000, 'medium', '2026-07-24', 65, 'tier_3', 'US', 'en',
                    'manual', '2026-07-25T00:00:00+00:00')"""
        )
        records = [
            {
                "channel": "media",
                "platform": "Example Media",
                "source_id": "google_news",
                "data_type": "media_mention",
                "occurred_at": "2026-07-24T10:00:00+00:00",
                "metrics": {
                    "publication_domain": "example.com",
                    "monthly_traffic": 80_000,
                    "media_tier": "tier_4",
                    "authority": 40,
                    "country": "",
                },
            },
            {
                "channel": "media",
                "platform": "Example Media",
                "source_id": "google_news",
                "data_type": "media_mention",
                "occurred_at": "2026-07-23T10:00:00+00:00",
                "metrics": {
                    "publication_domain": "example.com",
                    "monthly_traffic": 80_000,
                    "media_tier": "tier_4",
                    "authority": 40,
                    "country": "",
                },
            },
        ]

        with patch("server.domains.content.query_records", return_value=records), patch(
            "server.domains.content.build_trend", return_value=[]
        ):
            summary = marketing_summary(conn=conn)

        publication = summary["by_publication"][0]
        self.assertEqual(1_500_000, publication["monthly_traffic"])
        self.assertEqual(700_000, publication["traffic_lower"])
        self.assertEqual(3_300_000, publication["traffic_upper"])
        self.assertEqual(42_000, publication["popularity_rank"])
        self.assertEqual("manual", publication["traffic_source"])
        self.assertEqual(1_500_000, summary["total_reach"])

    def test_unresolved_legacy_fixed_value_is_not_shown_as_real_traffic(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(PUBLICATIONS_SCHEMA)
        records = [{
            "channel": "media",
            "platform": "Unknown Outlet",
            "source_id": "google_news",
            "data_type": "media_mention",
            "occurred_at": "2026-07-24T10:00:00+00:00",
            "metrics": {
                "publication_domain": "",
                "monthly_traffic": 220_000,
                "media_tier": "tier_3",
            },
        }]

        with patch("server.domains.content.query_records", return_value=records), patch(
            "server.domains.content.build_trend", return_value=[]
        ):
            summary = marketing_summary(conn=conn)

        publication = summary["by_publication"][0]
        self.assertEqual(0, publication["monthly_traffic"])
        self.assertEqual("unavailable", publication["traffic_source"])
        self.assertEqual(0, summary["total_reach"])


if __name__ == "__main__":
    unittest.main()
