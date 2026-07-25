import json
import sqlite3
import unittest
from unittest.mock import patch

from server.connectors.social import (
    _compact_count,
    _refresh_existing_social_records,
    _youtube_web_video_metrics,
    collect_social_accounts,
)
from server.fetchers import FetchError
from server.util import today


YOUTUBE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom"
      xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <id>yt:video:test-video-1</id>
    <yt:videoId>test-video-1</yt:videoId>
    <yt:channelId>UC1234567890123456789012</yt:channelId>
    <title>Product launch</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=test-video-1"/>
    <author><name>Example Brand</name><uri>https://www.youtube.com/channel/UC1234567890123456789012</uri></author>
    <published>2026-07-24T10:00:00+00:00</published>
    <media:group>
      <media:description>Launch announcement</media:description>
      <media:thumbnail url="https://img.youtube.com/test.jpg" width="480" height="360"/>
      <media:community>
        <media:starRating count="25" average="5.00" min="1" max="5"/>
        <media:statistics views="1200"/>
      </media:community>
    </media:group>
  </entry>
</feed>
"""


YOUTUBE_CHANNEL_DATA = {
    "metadata": {"channelMetadataRenderer": {"title": "Example Brand"}},
    "contents": {
        "richGridRenderer": {
            "contents": [{
                "richItemRenderer": {
                    "content": {
                        "lockupViewModel": {
                            "contentId": "page-video-1",
                            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
                            "contentImage": {
                                "thumbnailViewModel": {
                                    "image": {"sources": [{"url": "https://img.youtube.com/page-test.jpg"}]}
                                }
                            },
                            "metadata": {
                                "lockupMetadataViewModel": {
                                    "title": {"content": "Production fallback video"},
                                    "metadata": {
                                        "contentMetadataViewModel": {
                                            "metadataRows": [{
                                                "metadataParts": [
                                                    {"text": {"content": "3.4K views"}},
                                                    {"text": {"content": "1 month ago"}},
                                                ]
                                            }]
                                        }
                                    },
                                }
                            },
                        }
                    }
                }
            }]
        }
    },
}
YOUTUBE_CHANNEL_HTML = f"<html><script>var ytInitialData = {json.dumps(YOUTUBE_CHANNEL_DATA)};</script></html>"


class SocialAccountCollectorTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE links (
              id TEXT PRIMARY KEY, brand_id TEXT, dimension TEXT, channel TEXT,
              platform TEXT, url TEXT, status TEXT, last_collect_at TEXT,
              last_status TEXT, last_error TEXT, config_json TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
            CREATE TABLE records (
              id TEXT PRIMARY KEY, source_id TEXT, external_id TEXT, title TEXT,
              author TEXT, body TEXT, url TEXT, metrics_json TEXT, raw_json TEXT
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def add_link(self, platform: str, url: str, last_collect_at=None):
        self.conn.execute(
            """
            INSERT INTO links (id, brand_id, dimension, channel, platform, url, status,
                               last_collect_at, config_json, created_at, updated_at)
            VALUES (?, 'brand-1', 'marketing', 'social', ?, ?, 'active', ?, '{}', '2026-07-01', '2026-07-01')
            """,
            (f"link-{platform}", platform, url, last_collect_at),
        )

    @patch("server.connectors.social.fetch_bytes", return_value=YOUTUBE_FEED)
    @patch("server.connectors.social.fetch_page")
    def test_youtube_feed_becomes_social_records(self, fetch_page, _fetch_bytes):
        fetch_page.return_value = {
            "meta": {"og:url": "https://www.youtube.com/channel/UC1234567890123456789012"},
            "final_url": "https://www.youtube.com/@example",
            "html": "",
            "title": "Example Brand",
        }
        self.add_link("youtube", "https://www.youtube.com/@example")

        records = collect_social_accounts(self.conn, {"id": "brand-1", "_force_collect": True})

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["channel"], "social")
        self.assertEqual(records[0]["data_type"], "social_post")
        self.assertEqual(records[0]["link_id"], "link-youtube")
        self.assertEqual(records[0]["metrics"]["views"], 1200)
        self.assertEqual(records[0]["metrics"]["rating_count"], 25)
        status = self.conn.execute("SELECT last_status, last_error FROM links WHERE id = 'link-youtube'").fetchone()
        self.assertEqual(status["last_status"], "ok")
        self.assertEqual(status["last_error"], "")
        config = self.conn.execute("SELECT config_json FROM links WHERE id = 'link-youtube'").fetchone()["config_json"]
        self.assertIn("UC1234567890123456789012", config)

    @patch("server.connectors.social._enrich_youtube_web_metrics")
    @patch("server.connectors.social.fetch_bytes", return_value=YOUTUBE_FEED)
    @patch("server.connectors.social.fetch_page")
    def test_youtube_atom_feed_also_enriches_engagement_metrics(self, fetch_page, _fetch_bytes, enrich):
        fetch_page.return_value = {"html": "channel-page", "title": "Example Brand"}
        self.add_link("youtube", "https://www.youtube.com/channel/UC1234567890123456789012")

        records = collect_social_accounts(self.conn, {"id": "brand-1", "_force_collect": True})

        self.assertEqual(len(records), 1)
        enrich.assert_called_once()
        posts, html = enrich.call_args.args
        self.assertEqual(posts[0].external_id, "test-video-1")
        self.assertEqual(html, "channel-page")

    @patch("server.connectors.social.fetch_page")
    @patch("server.connectors.social.fetch_bytes", side_effect=FetchError("HTTP Error 404: Not Found"))
    def test_youtube_channel_page_is_used_when_atom_feed_fails(self, _fetch_bytes, fetch_page):
        fetch_page.return_value = {
            "meta": {},
            "final_url": "https://www.youtube.com/channel/UC1234567890123456789012/videos",
            "html": YOUTUBE_CHANNEL_HTML,
            "title": "Example Brand - YouTube",
        }
        self.add_link("youtube", "https://www.youtube.com/channel/UC1234567890123456789012")

        records = collect_social_accounts(self.conn, {"id": "brand-1", "_force_collect": True})

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["external_id"], "brand-1:link-youtube:youtube:page-video-1")
        self.assertEqual(records[0]["metrics"]["views"], 3400)
        self.assertEqual(records[0]["raw"]["collection_method"], "youtube_channel_page")
        status = self.conn.execute("SELECT last_status, last_error FROM links WHERE id = 'link-youtube'").fetchone()
        self.assertEqual(status["last_status"], "ok")
        self.assertEqual(status["last_error"], "")

    def test_paid_platform_without_token_is_explained(self):
        self.add_link("instagram", "https://www.instagram.com/example/")
        with patch("server.connectors.creators.CREDENTIALS", {"ensembledata_token": ""}):
            records = collect_social_accounts(self.conn, {"id": "brand-1", "_force_collect": True})

        self.assertEqual(records, [])
        status = self.conn.execute("SELECT last_status, last_error FROM links WHERE id = 'link-instagram'").fetchone()
        self.assertEqual(status["last_status"], "needs_credential")
        self.assertIn("ensembledata_token", status["last_error"])

    def test_localized_youtube_view_count_is_parsed(self):
        self.assertEqual(_compact_count("收看次數：3.5K 次"), 3500)

    @patch("server.connectors.social.fetch_json_post")
    def test_youtube_web_metrics_include_views_likes_and_comments(self, fetch_json_post):
        fetch_json_post.side_effect = [
            {
                "contents": {
                    "videoPrimaryInfoRenderer": {
                        "viewCount": {"videoViewCountRenderer": {"viewCount": {"simpleText": "3,538 views"}}},
                        "videoActions": {
                            "segmentedLikeDislikeButtonViewModel": {
                                "defaultButtonViewModel": {
                                    "buttonViewModel": {"iconName": "LIKE", "title": "27"}
                                }
                            }
                        },
                    },
                    "itemSectionRenderer": {
                        "targetId": "comments-section",
                        "contents": [{
                            "continuationItemRenderer": {
                                "continuationEndpoint": {
                                    "continuationCommand": {"token": "comment-token"}
                                }
                            }
                        }],
                    },
                }
            },
            {"commentsHeaderRenderer": {"countText": {"runs": [{"text": "3"}, {"text": " Comments"}]}}},
        ]
        config = {"api_key": "public-web-key", "client_version": "test", "context": {"client": {}}}

        metrics = _youtube_web_video_metrics(config, "video-1")

        self.assertEqual(metrics, {"views": 3538, "likes": 27, "comments": 3})
        self.assertEqual(fetch_json_post.call_count, 2)

    def test_existing_social_record_metrics_are_refreshed(self):
        self.conn.execute(
            """
            INSERT INTO records
            (id, source_id, external_id, title, author, body, url, metrics_json, raw_json)
            VALUES ('record-1', 'social_accounts', 'brand-1:link-youtube:youtube:video-1',
                    'Old', 'Brand', 'Old', 'https://youtube.com/watch?v=video-1',
                    '{"views": 100}', '{"collection_method": "youtube_channel_page"}')
            """
        )
        payload = {
            "source_id": "social_accounts",
            "external_id": "brand-1:link-youtube:youtube:video-1",
            "title": "Updated",
            "author": "Brand",
            "body": "Updated",
            "url": "https://youtube.com/watch?v=video-1",
            "metrics": {"views": 3538, "likes": 27, "comments": 3, "engagement": 30},
            "raw": {"metrics_collection_method": "youtube_web_next"},
        }

        remaining = _refresh_existing_social_records(self.conn, [payload])

        self.assertEqual(remaining, [])
        row = self.conn.execute("SELECT title, metrics_json, raw_json FROM records WHERE id = 'record-1'").fetchone()
        self.assertEqual(row["title"], "Updated")
        metrics = json.loads(row["metrics_json"])
        self.assertEqual(metrics["views"], 3538)
        self.assertEqual(metrics["likes"], 27)
        self.assertEqual(metrics["comments"], 3)
        self.assertEqual(json.loads(row["raw_json"])["metrics_collection_method"], "youtube_web_next")

    def test_unimplemented_platform_is_not_left_waiting(self):
        self.add_link("linkedin", "https://www.linkedin.com/company/example/")

        records = collect_social_accounts(self.conn, {"id": "brand-1", "_force_collect": True})

        self.assertEqual(records, [])
        status = self.conn.execute("SELECT last_status, last_error FROM links WHERE id = 'link-linkedin'").fetchone()
        self.assertEqual(status["last_status"], "unsupported")
        self.assertIn("尚未接入", status["last_error"])

    @patch("server.connectors.social.fetch_page")
    def test_scheduler_skips_account_already_checked_today(self, fetch_page):
        self.add_link("youtube", "https://www.youtube.com/@example", last_collect_at=f"{today()}T01:00:00+00:00")

        records = collect_social_accounts(self.conn, {"id": "brand-1"})

        self.assertEqual(records, [])
        fetch_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
