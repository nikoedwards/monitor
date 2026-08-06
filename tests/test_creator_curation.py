import sqlite3
import unittest

from server.connectors.creators.curation import (
    candidate_dashboard,
    candidate_evidence,
    detect_platform,
    import_candidates,
    list_map_snapshots,
    rebuild_candidates,
    save_map_snapshot,
    update_candidate,
)
from server.connectors.creators.products import rebuild_product_matches
from server.db import SCHEMA
from server.records import insert_record


class CreatorCurationTests(unittest.TestCase):
    def test_platform_detection_rejects_spoofed_social_domains(self):
        self.assertEqual(detect_platform("https://www.youtube.com/@real"), "youtube")
        self.assertEqual(detect_platform("https://youtube.com.evil.test/@fake"), "")
        self.assertEqual(detect_platform("https://instagram.com.evil.test/fake"), "")
        self.assertEqual(detect_platform("https://tiktok.com.evil.test/@fake"), "")

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO brands (id, name, created_at, updated_at) VALUES ('brand-1', 'Example', '2026-08-01', '2026-08-01')"
        )
        self.conn.execute(
            """
            INSERT INTO products (id, brand_id, name, notes, created_at, updated_at)
            VALUES ('product-1', 'brand-1', 'Alpha Cam', 'Alpha Pro', '2026-08-01', '2026-08-01')
            """
        )
        self._record(
            "record-1",
            "Alpha Cam sponsored review",
            "Example sent me the Alpha Cam for a full review.",
            handle="alice",
            views=12000,
            engagement=900,
            matched_queries=["Example Alpha Cam"],
        )
        self._record(
            "record-2",
            "Alpha Pro follow-up",
            "A second look at Alpha Pro after two weeks.",
            handle="alice",
            views=8000,
            engagement=500,
            matched_queries=["Example Alpha Pro"],
        )

    def tearDown(self):
        self.conn.close()

    def _record(self, record_id, title, body, *, handle, views, engagement, matched_queries):
        insert_record(
            self.conn,
            {
                "id": record_id,
                "source_id": "youtube_search",
                "brand_id": "brand-1",
                "external_id": record_id,
                "data_type": "creator_post",
                "dimension": "marketing",
                "channel": "creators",
                "platform": "youtube",
                "title": title,
                "body": body,
                "author": "Alice Reviews",
                "url": f"https://youtube.com/watch?v={record_id}",
                "occurred_at": "2026-08-03T10:00:00+00:00",
                "metrics": {
                    "author_handle": handle,
                    "author_url": f"https://youtube.com/@{handle}",
                    "follower_count": 25000,
                    "views": views,
                    "engagement": engagement,
                    "is_collab": True,
                    "is_sponsored": True,
                    "collab_type": "mention",
                    "mentions": ["example"],
                },
                "raw": {"matched_queries": matched_queries},
            },
        )

    def test_records_become_pending_candidates_with_product_evidence(self):
        rebuild_product_matches(self.conn, "brand-1")
        result = rebuild_candidates(self.conn, "brand-1")
        self.assertEqual(1, result["candidates"])
        self.assertEqual(2, result["records"])

        dashboard = candidate_dashboard(self.conn, "brand-1")
        self.assertEqual(1, dashboard["totals"]["pending"])
        candidate = dashboard["candidates"][0]
        self.assertEqual("alice", candidate["identity_key"])
        self.assertEqual(2, candidate["evidence_count"])
        self.assertEqual(["Alpha Cam"], [item["name"] for item in candidate["products"]])

        evidence = candidate_evidence(self.conn, candidate["id"], "product-1")
        self.assertEqual(2, len(evidence))
        self.assertIn("Example Alpha", evidence[0]["query"])
        self.assertGreaterEqual(evidence[0]["confidence"], 0.9)

    def test_review_state_survives_rebuild_and_snapshot_uses_curated_only(self):
        rebuild_product_matches(self.conn, "brand-1")
        rebuild_candidates(self.conn, "brand-1")
        candidate_id = candidate_dashboard(self.conn, "brand-1")["candidates"][0]["id"]
        update_candidate(
            self.conn,
            candidate_id,
            {"review_status": "priority", "relationship_status": "collaborating", "notes": "Launch partner"},
        )
        rebuild_candidates(self.conn, "brand-1")

        dashboard = candidate_dashboard(self.conn, "brand-1", product_id="product-1")
        self.assertEqual(1, dashboard["totals"]["priority"])
        self.assertEqual(1, len(dashboard["curated_map"]["points"]))
        self.assertEqual(candidate_id, dashboard["curated_map"]["points"][0]["id"])

        snapshot = save_map_snapshot(self.conn, "brand-1", "product-1", "youtube", "August landscape")
        self.assertEqual(1, len(snapshot["map"]["points"]))
        self.assertEqual("brand-1", snapshot["brand_id"])
        self.assertEqual("product-1", snapshot["product_id"])
        self.assertEqual("youtube", snapshot["platform"])
        self.assertTrue(snapshot["created_at"])
        history = list_map_snapshots(self.conn, "brand-1", "product-1", "youtube")
        self.assertEqual("August landscape", history[0]["title"])

    def test_manual_import_autodetects_instagram_and_tiktok_and_dedupes(self):
        first = import_candidates(
            self.conn,
            "brand-1",
            [
                {"url": "https://www.instagram.com/creator_one/", "name": "Creator One"},
                {"url": "https://www.tiktok.com/@creator_two", "notes": "TikTok shortlist"},
            ],
        )
        self.assertEqual(2, first["created"])
        second = import_candidates(
            self.conn,
            "brand-1",
            [{"platform": "ig", "handle": "creator_one", "name": "Creator One Updated"}],
        )
        self.assertEqual(1, second["updated"])

        dashboard = candidate_dashboard(self.conn, "brand-1")
        self.assertEqual(2, dashboard["totals"]["all"])
        platforms = {item["platform"] for item in dashboard["candidates"]}
        self.assertEqual({"instagram", "tiktok"}, platforms)
        instagram = next(item for item in dashboard["candidates"] if item["platform"] == "instagram")
        update_candidate(self.conn, instagram["id"], {"review_status": "approved"})
        curated = candidate_dashboard(self.conn, "brand-1")["curated_map"]["points"]
        self.assertEqual("observe", curated[0]["quadrant"])


if __name__ == "__main__":
    unittest.main()
