import sqlite3
import unittest
from unittest.mock import patch

from server.connectors.collectors import _reddit_listing_payloads, _reddit_rss_payloads, collect_reddit
from server.db import SCHEMA
from server.domains.common import query_records
from server.records import insert_record
from server.relevance import query_match_evidence, reddit_post_id, search_query_parts


class RedditRelevanceTests(unittest.TestCase):
    def test_alias_matching_and_post_url_validation(self):
        self.assertEqual(search_query_parts("NotePin"), ["Note", "Pin"])
        self.assertIsNotNone(query_match_evidence("NotePin", "My NotePin review"))
        self.assertIsNotNone(query_match_evidence("NotePin", "", "Using the Note Pin at work"))
        self.assertIsNone(query_match_evidence("NotePin", "Remember to note this and pin it later"))
        self.assertIsNotNone(query_match_evidence("Anker", "Anker charger review"))
        self.assertIsNone(query_match_evidence("Anker", "A banker reviews a charger"))
        self.assertEqual(
            reddit_post_id("https://www.reddit.com/r/PLAUDAI/comments/abc123/a_post/"),
            "abc123",
        )
        self.assertEqual(reddit_post_id("https://www.reddit.com/r/PLAUDAI/"), "")

    def test_json_search_payloads_drop_unrelated_results(self):
        data = {"data": {"children": [
            {"data": {
                "id": "good1",
                "permalink": "/r/plaud/comments/good1/review/",
                "title": "PLAUD NotePin review",
                "selftext": "Useful for meetings.",
            }},
            {"data": {
                "id": "bad1",
                "permalink": "/r/guitar/comments/bad1/guitar/",
                "title": "Which acoustic guitar should I buy?",
                "selftext": "I wrote a note and pinned the options later.",
            }},
        ]}}

        payloads = _reddit_listing_payloads(data, {"id": "brand-1"}, "PLAUD")

        self.assertEqual([item["title"] for item in payloads], ["PLAUD NotePin review"])
        self.assertEqual(payloads[0]["raw"]["matched_in"], "title")

    @patch("server.connectors.collectors.parse_rss")
    @patch("server.connectors.collectors.fetch_bytes", return_value=b"feed")
    def test_rss_search_requires_post_url_and_visible_match(self, _fetch, parse):
        parse.return_value = [
            {
                "title": "Plaud Note Pin workflow",
                "description": "A real product discussion",
                "url": "https://www.reddit.com/r/plaud/comments/good2/workflow/",
                "guid": "good2",
                "published_at": "2026-07-25T00:00:00+00:00",
            },
            {
                "title": "Plaud community",
                "description": "A community result, not a post",
                "url": "https://www.reddit.com/r/PlaudNoteUsers/",
                "guid": "community",
                "published_at": "2026-07-25T00:00:00+00:00",
            },
            {
                "title": "Which acoustic guitar should I buy?",
                "description": "Remember to note the choice and pin it later",
                "url": "https://www.reddit.com/r/guitar/comments/bad2/guitar/",
                "guid": "bad2",
                "published_at": "2026-07-25T00:00:00+00:00",
            },
        ]

        payloads = _reddit_rss_payloads({"id": "brand-1"}, "PLAUD")

        self.assertEqual([item["title"] for item in payloads], ["Plaud Note Pin workflow"])

    def test_collector_separates_brand_search_from_official_hub(self):
        calls = []

        def fake_get(path, params):
            calls.append((path, params))
            if path == "/search.json":
                return {"data": {"children": [{"data": {
                    "id": "search1",
                    "permalink": "/r/gadgets/comments/search1/plaud_review/",
                    "title": "PLAUD review from another community",
                    "selftext": "A brand discussion",
                }}]}}
            if path == "/r/PLAUDAI/new.json":
                return {"data": {"children": [{"data": {
                    "id": "hub1",
                    "permalink": "/r/PLAUDAI/comments/hub1/announcement/",
                    "title": "Community announcement without the brand name",
                    "selftext": "All official-hub posts are accepted.",
                    "subreddit": "PLAUDAI",
                }}]}}
            raise AssertionError(f"unexpected Reddit request: {path}")

        brand = {
            "id": "brand-1",
            "name": "PLAUD",
            "monitoring_keywords_json": '["PLAUD", "NotePin", "Note Pro"]',
        }
        links = [{"id": "link-1", "url": "https://www.reddit.com/r/PLAUDAI/"}]
        with (
            patch("server.connectors.collectors._reddit_get", side_effect=fake_get),
            patch("server.connectors.collectors.community_links", return_value=links),
            patch("server.connectors.collectors._touch_link"),
        ):
            payloads = collect_reddit(None, brand)

        self.assertEqual([item["title"] for item in payloads], [
            "PLAUD review from another community",
            "Community announcement without the brand name",
        ])
        self.assertEqual(calls, [
            ("/search.json", {"q": "PLAUD", "sort": "new", "limit": 25, "type": "link"}),
            ("/r/PLAUDAI/new.json", {"limit": 100}),
        ])
        self.assertEqual(payloads[0]["raw"]["collection_mode"], "site_search")
        self.assertEqual(payloads[1]["raw"]["collection_mode"], "official_hub")

    def test_record_queries_hide_old_product_searches_but_keep_official_hub(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO brands (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("brand-1", "PLAUD", "2026-07-25", "2026-07-25"),
        )
        common = {
            "source_id": "reddit_search",
            "brand_id": "brand-1",
            "data_type": "community_post",
            "dimension": "marketing",
            "channel": "community",
            "platform": "reddit",
        }
        insert_record(conn, {
            **common,
            "external_id": "good",
            "title": "PLAUD NotePin review",
            "body": "A product review",
            "url": "https://www.reddit.com/r/plaud/comments/good/review/",
            "occurred_at": "2026-07-25T03:00:00+00:00",
            "raw": {"query": "PLAUD", "scope": "site"},
        })
        insert_record(conn, {
            **common,
            "external_id": "legacy-product-query",
            "title": "A detailed NotePin review",
            "body": "This matched an old product-keyword search",
            "url": "https://www.reddit.com/r/gadgets/comments/legacy/notepin/",
            "occurred_at": "2026-07-25T02:00:00+00:00",
            "raw": {"query": "NotePin", "scope": "site"},
        })
        insert_record(conn, {
            **common,
            "external_id": "feed",
            "title": "Community announcement",
            "body": "An intentionally broad configured-community post",
            "url": "https://www.reddit.com/r/PLAUDAI/comments/feed/announcement/",
            "occurred_at": "2026-07-25T01:00:00+00:00",
            "raw": {"subreddit": "PLAUDAI", "scope": "subreddit:PLAUDAI"},
        })

        records = query_records(conn, {"channel": "community"}, limit=10)

        self.assertEqual([record["external_id"] for record in records], ["good", "feed"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
