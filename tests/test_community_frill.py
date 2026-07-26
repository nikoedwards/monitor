import json
import sqlite3
import unittest
from unittest.mock import call, patch

from server.connectors import collectors


def _next_html(apollo: dict, *, frill_signature: bool = True) -> str:
    data = {"props": {"apolloState": {"data": apollo}}}
    signature = "Powered by frill.co" if frill_signature else ""
    return f'<html>{signature}<script id="__NEXT_DATA__">{json.dumps(data)}</script></html>'


class FrillCollectorTests(unittest.TestCase):
    def test_collects_individual_ideas_from_latest_ideas_listing(self):
        board = {
            "__typename": "Board",
            "idx": "board_nvk7pg0r",
            "slug": "feature-ideas",
        }
        latest_apollo = {
            "board_nvk7pg0r.Board": board,
            "follower_new.CompanyFollower": {
                "__typename": "CompanyFollower",
                "idx": "follower_new",
                "name": "Jordan W",
            },
            "idea_new.CommentEntity": {
                "__typename": "Idea",
                "idx": "idea_new",
                "number": 4869,
                "name": "Plaud should turn on when you open your app",
                "excerpt": "Turn it on from the application.",
                "slug": "plaud-should-turn-on-when-you-open-your-app",
                "created_at": "2026-07-26T01:29:30.000000Z",
                "vote_count": 1,
                "comment_count": 0,
                "follower_count": 1,
                "author": {"__ref": "follower_new.CompanyFollower"},
            },
            "idea_old.CommentEntity": {
                "__typename": "Idea",
                "idx": "idea_old",
                "number": 4868,
                "name": "Integrate with CLOZE",
                "excerpt": "",
                "slug": "integrate-with-cloze",
                "created_at": "2026-07-25T17:53:01.000000Z",
                "vote_count": 1,
                "comment_count": 0,
                "follower_count": 1,
            },
        }
        base_page = {
            "final_url": "https://feedback.plaud.ai/b/nvk7pg0r/feature-ideas",
            "html": _next_html({"board_nvk7pg0r.Board": board}),
        }
        latest_page = {
            "final_url": "https://feedback.plaud.ai/b/nvk7pg0r/feature-ideas?sortBy=created_at",
            "html": _next_html(latest_apollo),
        }

        with patch.object(collectors, "fetch_page", side_effect=[base_page, latest_page]) as fetch_page:
            payloads = collectors._frill_payloads(
                "https://feedback.plaud.ai/", {"id": "brand-1"}, "link-1"
            )

        self.assertEqual(
            fetch_page.call_args_list,
            [
                call("https://feedback.plaud.ai/", timeout=22),
                call(
                    "https://feedback.plaud.ai/b/nvk7pg0r/feature-ideas?sortBy=created_at",
                    timeout=25,
                ),
            ],
        )
        self.assertEqual([item["raw"]["number"] for item in payloads], [4869, 4868])
        newest = payloads[0]
        self.assertEqual(newest["title"], "Plaud should turn on when you open your app")
        self.assertEqual(newest["author"], "Jordan W")
        self.assertEqual(newest["platform"], "feedback.plaud.ai")
        self.assertEqual(newest["metrics"]["vote_count"], 1)
        self.assertEqual(newest["raw"]["collection_method"], "frill_latest_ideas")
        self.assertEqual(
            newest["url"],
            "https://feedback.plaud.ai/b/nvk7pg0r/feature-ideas/plaud-should-turn-on-when-you-open-your-app",
        )

    def test_recognized_frill_parse_failure_does_not_create_generic_snapshot(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE links (
              id TEXT PRIMARY KEY, brand_id TEXT, channel TEXT, platform TEXT, url TEXT,
              status TEXT, last_collect_at TEXT, last_status TEXT, last_error TEXT, updated_at TEXT
            );
            CREATE TABLE records (
              source_id TEXT, brand_id TEXT, link_id TEXT, platform TEXT, external_id TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO links (id, brand_id, channel, platform, url, status, updated_at) "
            "VALUES ('link-1', 'brand-1', 'community', 'self_hosted', "
            "'https://feedback.plaud.ai/', 'active', '2026-07-26')"
        )
        conn.execute(
            "INSERT INTO records (source_id, brand_id, link_id, platform, external_id) "
            "VALUES ('community_site', 'brand-1', 'link-1', 'frill', "
            "'brand-1:frill:feedback.plaud.ai:idea_existing')"
        )

        with (
            patch.object(collectors, "_discourse_payloads", return_value=[]),
            patch.object(collectors, "_frill_payloads", return_value=[]),
            patch.object(collectors, "_rss_payloads") as rss_payloads,
            patch.object(collectors, "_generic_site_payload") as generic_payload,
        ):
            payloads = collectors.collect_community_sites(conn, {"id": "brand-1"})

        self.assertEqual(payloads, [])
        rss_payloads.assert_not_called()
        generic_payload.assert_not_called()
        status = conn.execute(
            "SELECT last_status, last_error FROM links WHERE id = 'link-1'"
        ).fetchone()
        self.assertEqual(status["last_status"], "empty")
        self.assertIn("Latest Ideas", status["last_error"])
        migrated = conn.execute("SELECT platform FROM records").fetchone()
        self.assertEqual(migrated["platform"], "feedback.plaud.ai")
        conn.close()


if __name__ == "__main__":
    unittest.main()
