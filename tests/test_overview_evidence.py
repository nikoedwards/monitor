import json
import sqlite3
import unittest
from unittest.mock import patch

from server import ai
from server.db import SCHEMA
from server.domains.insights import overview_summary
from server.overview_evidence import build_overview_evidence, validate_overview_insights


class OverviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO brands (id,name,created_at,updated_at) VALUES ('b','Nova','2026-01-01','2026-01-01')")
        self.payload = {
            "range": {"start": "2026-10-01", "end": "2026-10-30", "previous_start": "2026-09-01", "previous_end": "2026-09-30"},
            "kpis": {}, "coverage": {"voice": {"status": "ready"}},
            "modules": {"voice": {"status": "ready", "totals": {"total": 1}}, "market": {"status": "ready", "current": {"share": 50}}},
            "previous_modules": {"voice": {"totals": {"total": 0}}},
        }

    def tearDown(self):
        self.conn.close()

    def record(self, record_id, at, *, kind="review", dimension="voc", channel="app", platform="app_store", region="us", link_id="app-us", rating=4, raw=None):
        self.conn.execute(
            "INSERT INTO records (id,source_id,brand_id,link_id,data_type,dimension,channel,platform,title,body,url,region,occurred_at,sentiment,metrics_json,raw_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record_id, "manual", "b", link_id, kind, dimension, channel, platform, "Nova update", "New feature works well", "https://example.com/" + record_id,
             region, at, "positive", json.dumps({"rating": rating, "likes": 10}), json.dumps(raw or {}), at),
        )

    def test_app_ratings_keep_region_identity_endpoints_extrema_and_version(self):
        for record_id, at, region, rating, version in (
            ("us-old", "2026-09-30", "us", 4.8, "1.0"), ("us-dip", "2026-10-26", "us", 3.1, "2.0"),
            ("us-new", "2026-10-29", "us", 4.1, "2.1"), ("cn-new", "2026-10-29", "cn", 4.9, "2.1"),
        ):
            self.record(record_id, at, kind="app_metric", dimension="platform", region=region, rating=rating, raw={"version": version})
        self.record("review", "2026-10-28")
        packet = build_overview_evidence(self.conn, "b", self.payload)
        metrics = [r for r in packet["evidence"] if r["kind"] == "app_metric"]
        self.assertEqual({"us-old", "us-dip", "us-new", "cn-new"}, {r["id"].split(":", 1)[1] for r in metrics})
        self.assertEqual("2.0", next(r for r in metrics if r["id"] == "app_metric:us-dip")["metrics"]["version"])
        self.assertEqual(0, packet["source_coverage"]["platform/app"]["periods"]["current"]["sample_count"])
        self.assertEqual(1, packet["source_coverage"]["voc/all"]["periods"]["current"]["sample_count"])
        self.assertEqual("2026-10-24", packet["recent_window"]["start"])
        self.assertIn("voice", packet["previous_modules"])

    def test_packet_contains_job_ads_web_and_listing_details(self):
        self.conn.execute("INSERT INTO job_postings (id,brand_id,platform,title,jd_text,first_seen,created_at,updated_at) VALUES ('j','b','linkedin','Regional launch lead','Prepare new regional launch','2026-10-24','2026-10-24','2026-10-24')")
        self.conn.execute("INSERT INTO ad_entities (id,brand_id,source_id,ad_external_id,creative_body,first_seen_at,last_seen_at,created_at,updated_at) VALUES ('a','b','manual','ext','New Nova product','2026-10-25','2026-10-29','2026-10-25','2026-10-29')")
        self.conn.execute("INSERT INTO web_snapshots (id,monitor_id,brand_id,snapshot_date,url,title,text_excerpt,summary,created_at) VALUES ('w','m','b','2026-10-25','https://example.com/product','Nova product','Feature details','New landing page','2026-10-25')")
        for record_id, day, rating in (("old", "2026-09-29", 4.8), ("new", "2026-10-26", 3.5)):
            self.conn.execute("INSERT INTO sales_metrics (id,brand_id,link_id,snapshot_date,channel,rating,title,created_at) VALUES (?,'b','listing',?,'amazon',?,'Nova product',?)", (record_id, day, rating, day))
        packet = build_overview_evidence(self.conn, "b", self.payload)
        catalog = {r["id"]: r for r in packet["evidence"]}
        self.assertIn("regional launch", catalog["hiring:j"]["detail"])
        self.assertIn("Nova product", catalog["ads:a"]["detail"])
        self.assertIn("Feature details", catalog["web:w"]["detail"])
        self.assertEqual(4.8, catalog["sales:old"]["metrics"]["rating"])
        self.assertEqual(3.5, catalog["sales:new"]["metrics"]["rating"])

    def test_missing_optional_table_is_visible_as_gap(self):
        self.conn.execute("DROP TABLE ad_entities")
        packet = build_overview_evidence(self.conn, "b", self.payload)
        self.assertTrue(packet["source_coverage"]["ads"]["unavailable"])
        self.assertTrue(any("不可读取" in c for c in packet["caveats"]))

    def packet(self):
        def evidence(eid, domain, group, kind="record", at="2026-10-25"):
            return {"id": eid, "domain": domain, "independence_group": group, "kind": kind, "at": at,
                    "title": eid, "url": "https://example.com/" + eid, "detail": "Stored fact"}
        return {"range": self.payload["range"], "recent_window": {"start": "2026-10-24"}, "source_coverage": {}, "caveats": [], "evidence": [
            evidence("job", "hiring", "hiring", "hiring"), evidence("ad", "marketing", "marketing", "ads"),
            evidence("share", "market", "derived", "module"), evidence("sales", "sales", "sales", "sales"),
            evidence("voice", "voice", "conversation"), evidence("community", "marketing", "conversation"),
            evidence("old", "voice", "app_store", "app_metric", "2026-09-30"),
        ]}

    def insight(self, *ids):
        return {"title": "可能准备新一轮推广", "observation": "岗位与素材同步变化", "inference": "可能准备推广",
                "alternative": "也可能是例行维护", "next_signal": "观察是否上线新落地页", "confidence": "medium", "evidence_ids": list(ids)}

    def test_independent_cross_domain_sources_resolve_to_stored_citations(self):
        item = self.insight("job", "ad", "job", "invented")
        item["evidence"] = [{"url": "https://invented.example"}]
        result = validate_overview_insights({"insights": [item]}, self.packet())
        card = result["insights"][0]
        self.assertEqual(["job", "ad"], card["evidence_ids"])
        self.assertEqual(["hiring", "marketing"], card["domains"])
        self.assertEqual("https://example.com/job", card["evidence"][0]["url"])

    def test_derived_or_reused_sources_cannot_masquerade_as_corroboration(self):
        result = validate_overview_insights({"summary": "unsupported", "insights": [self.insight("share", "sales"), self.insight("voice", "community")]}, self.packet())
        self.assertEqual([], result["insights"])
        self.assertNotEqual("unsupported", result["summary"])

    def test_recent_positive_signal_can_be_single_domain_but_must_be_recent(self):
        recent = self.insight("voice")
        recent["polarity"] = "positive"
        result = validate_overview_insights({"recent_highlights": [recent, self.insight("old")]}, self.packet())
        self.assertEqual(1, len(result["recent_highlights"]))
        self.assertEqual("positive", result["recent_highlights"][0]["polarity"])

    def test_incomplete_or_malformed_hypotheses_are_not_rendered(self):
        missing = self.insight("job", "ad")
        missing.pop("next_signal")
        result = validate_overview_insights({"insights": [None, {"title": "bad", "evidence_ids": [None, {}]}, missing]}, self.packet())
        self.assertEqual([], result["insights"])

    def test_unchanged_app_rating_is_not_a_standout_change(self):
        packet = self.packet()
        for eid, at, rating in (("app1", "2026-10-24", 4.88382), ("app2", "2026-10-27", 4.8836)):
            packet["evidence"].append({"id": eid, "domain": "voice", "independence_group": "app_store", "kind": "app_metric", "at": at,
                                       "platform": "app_store", "region": "us", "metrics": {"track_id": "app", "rating": rating}})
        result = validate_overview_insights({"recent_highlights": [self.insight("app1", "app2")]}, packet)
        self.assertEqual([], result["recent_highlights"])
        packet["evidence"][-1]["metrics"]["rating"] = 3.8
        result = validate_overview_insights({"recent_highlights": [self.insight("app1", "app2")]}, packet)
        self.assertEqual(1, len(result["recent_highlights"]))
        packet["evidence"][-1]["region"] = "cn"
        result = validate_overview_insights({"recent_highlights": [self.insight("app1", "app2")]}, packet)
        self.assertEqual([], result["recent_highlights"])

    @patch("server.ai.get_config", return_value={})
    @patch("server.ai.call_llm")
    def test_llm_receives_packet_and_returns_validated_results(self, llm, _config):
        packet = self.packet()
        llm.return_value = json.dumps({"summary": "可能准备推广", "insights": [self.insight("job", "ad")]})
        result = ai.summarize_overview(self.conn, packet)
        self.assertEqual(1, len(result["insights"]))
        self.assertIn("两个独立来源组", llm.call_args.args[2])
        self.assertIn("Stored fact", llm.call_args.args[2])
        llm.return_value = '{"summary": "truncated"'
        with self.assertRaises(ai.LlmError):
            ai.summarize_overview(self.conn, packet)

    def test_sampling_limits_are_disclosed(self):
        records = [{"id": str(i), "data_type": "review", "occurred_at": "2026-10-25", "body": "fact"} for i in range(1000)]
        with patch("server.overview_evidence.query_records", return_value=records):
            packet = build_overview_evidence(self.conn, "b", self.payload)
        self.assertTrue(packet["source_coverage"]["voc/all"]["possibly_truncated"])
        self.assertTrue(any("1000" in c for c in packet["caveats"]))

    def test_temporary_model_failure_does_not_cache_fallback_for_a_day(self):
        with patch("server.domains.insights._overview_payload", return_value=self.payload), \
             patch("server.domains.insights.ai.is_configured", return_value=True), \
             patch("server.domains.insights.build_overview_evidence", return_value={}), \
             patch("server.domains.insights.ai.summarize_overview", side_effect=ai.LlmError("timeout")), \
             patch("server.domains.insights.ai.put_cached_summary") as put:
            result = overview_summary(brand_id="b", start_date="2026-10-01", end_date="2026-10-30", refresh=True, conn=self.conn)
        self.assertEqual("fallback", result["mode"])
        put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
