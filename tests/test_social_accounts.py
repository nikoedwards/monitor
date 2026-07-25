import sqlite3
import unittest
from unittest.mock import patch

from server.connectors.social import collect_social_accounts
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

    def test_paid_platform_without_token_is_explained(self):
        self.add_link("instagram", "https://www.instagram.com/example/")
        with patch("server.connectors.creators.CREDENTIALS", {"ensembledata_token": ""}):
            records = collect_social_accounts(self.conn, {"id": "brand-1", "_force_collect": True})

        self.assertEqual(records, [])
        status = self.conn.execute("SELECT last_status, last_error FROM links WHERE id = 'link-instagram'").fetchone()
        self.assertEqual(status["last_status"], "needs_credential")
        self.assertIn("ensembledata_token", status["last_error"])

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
