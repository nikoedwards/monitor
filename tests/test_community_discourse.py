import sqlite3
import unittest
from unittest.mock import patch

from server.connectors import collectors
from server.db import SCHEMA
from server.records import insert_record, insert_record_if_new


class DiscourseCollectorTests(unittest.TestCase):
    def test_topic_body_comes_from_original_cooked_post_and_replies_reuse_detail(self):
        latest = {"topic_list": {"topics": [{
            "id": 42,
            "slug": "recorded-time-confusion",
            "title": "Recorded Time confusion",
            "posts_count": 2,
            "reply_count": 1,
            "views": 7,
            "created_at": "2026-11-08T01:00:00Z",
        }]}}
        detail = {"post_stream": {"posts": [
            {"id": 100, "username": "alice", "created_at": "2026-11-08T01:00:00Z",
             "cooked": "<p>The recorded time is shown one hour earlier after changing time zones.</p>"},
            {"id": 101, "username": "bob", "created_at": "2026-11-08T02:00:00Z",
             "cooked": "<p>I can reproduce this on the desktop app.</p>", "reply_to_post_number": 1},
        ]}}
        with patch.object(collectors, "fetch_json", side_effect=[latest, detail]) as fetch:
            payloads = collectors._discourse_payloads(
                "https://community.example.com/", {"id": "brand-1"}, "link-1"
            )
        self.assertEqual(2, len(payloads))
        self.assertEqual(
            "The recorded time is shown one hour earlier after changing time zones.",
            payloads[0]["body"],
        )
        self.assertEqual("alice", payloads[0]["author"])
        self.assertEqual("I can reproduce this on the desktop app.", payloads[1]["body"])
        self.assertEqual(2, fetch.call_count)

    def test_existing_title_only_community_record_is_enriched(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        insert_record(conn, {
            "id": "old", "source_id": "community_site",
            "external_id": "brand-1:discourse:community.example.com:42",
            "channel": "community", "title": "Recorded Time confusion",
            "body": "Recorded Time confusion",
        })
        inserted = insert_record_if_new(conn, {
            "source_id": "community_site",
            "external_id": "brand-1:discourse:community.example.com:42",
            "channel": "community", "title": "Recorded Time confusion",
            "author": "alice",
            "body": "The recorded time is shown one hour earlier after changing time zones.",
            "occurred_at": "2026-11-08T01:00:00+00:00",
        })
        self.assertIsNone(inserted)
        row = conn.execute("SELECT body, author FROM records WHERE id = 'old'").fetchone()
        self.assertEqual(
            "The recorded time is shown one hour earlier after changing time zones.",
            row["body"],
        )
        self.assertEqual("alice", row["author"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
