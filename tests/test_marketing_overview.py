import json
import sqlite3
import unittest
from unittest.mock import patch

from server.ai import LlmError, summarize_marketing_overview
from server.db import SCHEMA
from server.marketing_insights import grounded_findings, marketing_evidence, rule_findings
from server.records import upsert_ad_observation
from server.domains.content import (
    _marketing_channel_snapshot,
    _marketing_ads_snapshot,
    _marketing_fallback_summary,
    _marketing_previous_window,
    marketing_overview_summary,
)


class MarketingOverviewTests(unittest.TestCase):
    @patch("server.domains.content._marketing_overview_payload")
    @patch("server.domains.content.ai.get_cached_summary", return_value={"summary": "已有分析"})
    def test_cached_summary_does_not_rebuild_overview(self, _cache, payload):
        result = marketing_overview_summary("brand-1", start_date="2026-10-01", end_date="2026-10-30", conn=object())
        self.assertEqual({"summary": "已有分析", "cached": True}, result)
        payload.assert_not_called()

    @patch("server.domains.content.ai.put_cached_summary")
    @patch("server.domains.content.ai.summarize_marketing_overview", side_effect=LlmError("temporary error"))
    @patch("server.domains.content.ai.is_configured", return_value=True)
    @patch("server.domains.content._marketing_overview_payload", return_value={
        "range": {"start": "2026-10-01", "end": "2026-10-30"},
        "research": {"evidence": {"current.media": {"text": "本期媒体"}}}, "candidates": [],
    })
    def test_model_failure_returns_fallback_without_caching_it(self, _payload, _config, _model, save):
        result = marketing_overview_summary("brand-1", start_date="2026-10-01", end_date="2026-10-30",
                                            refresh=True, conn=object())
        self.assertEqual("fallback", result["mode"])
        save.assert_not_called()

    def test_previous_window_matches_equal_length(self):
        self.assertEqual(("2026-09-01", "2026-09-30"), _marketing_previous_window("2026-10-01", "2026-10-30"))

    def test_social_channel_snapshot_keeps_metrics_and_empty_state(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        records = [
            {"data_type": "social_post", "occurred_at": "2026-10-02T00:00:00Z", "metrics": {"views": 100, "likes": 5, "comments": 2, "engagement": 7}},
            {"data_type": "social_post", "occurred_at": "2026-10-03T00:00:00Z", "metrics": {"views": 50, "likes": 3, "comments": 1, "engagement": 4}},
        ]
        with patch("server.domains.content.query_records", return_value=records), patch("server.domains.content.build_trend", return_value=[]):
            result = _marketing_channel_snapshot(conn, "brand-1", "social", "2026-10-01", "2026-10-03")
        self.assertEqual("ready", result["status"])
        self.assertEqual(150, result["total_views"])
        self.assertEqual(11, result["total_engagement"])

    def test_fallback_explains_change_in_mix_and_skips_missing_baseline(self):
        current = {"community": dict(status="ready", total=20, posts=16, replies=4)}
        previous = {"community": dict(status="ready", total=30, posts=9, replies=21)}
        research = marketing_evidence(current, previous, {})
        result = _marketing_fallback_summary({"range": {}, "research": research, "candidates": rule_findings(current, previous)})
        self.assertEqual("fallback", result["mode"])
        self.assertIn("下降了 50 个百分点", result["summary"])
        self.assertEqual(2, len(result["findings"][0]["evidence"]))
        self.assertFalse(rule_findings(current, {}))
        self.assertFalse(rule_findings({}, previous))

    def test_creator_frequency_comparison_requires_complete_author_counts(self):
        current = dict(total=47, identified_accounts=44, identified_posts=47, single_post_accounts=41)
        prior = dict(total=69, identified_accounts=45, identified_posts=69, single_post_accounts=21)
        card, = rule_findings({"creators": current}, {"creators": prior})
        self.assertEqual(["current.creators", "previous.creators"], card["evidence_ids"])
        current["identified_posts"] = 46
        card, = rule_findings({"creators": current}, {"creators": prior})
        self.assertEqual(["current.creators"], card["evidence_ids"])

    def test_creative_updates_exclude_reobservation_and_use_historical_copy(self):
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA)
            for day, body in [(1, "Launch"), (2, "Launch"), (3, "Meeting notes"), (4, "Meeting recap"), (5, "Future offer")]:
                upsert_ad_observation(conn, dict(data_type="ad", brand_id="brand", external_id="ad-1",
                    body=body, observed_at=f"2026-10-{day:02d}T12:00:00Z"))
            unchanged = _marketing_ads_snapshot(conn, "brand", "2026-10-02", "2026-10-02")
            changes = _marketing_ads_snapshot(conn, "brand", "2026-10-03", "2026-10-04", with_examples=True)
        self.assertEqual(0, unchanged["changed_ads"])
        self.assertEqual(1, changes["changed_ads"])
        self.assertIn("Meeting notes", str(changes["examples"]))
        self.assertNotIn("Future offer", str(changes["examples"]))

    @patch("server.ai.get_config", return_value={})
    @patch("server.ai.call_llm")
    def test_model_cites_original_samples_and_discards_invalid_or_duplicate_findings(self, model, _config):
        evidence = {"current.creators.1": {"text": "11-25 达人 · 会议转写演示", "url": "https://example.org/post"},
                    "current.ads.1": {"text": "11-26 广告 · 会议转写"}}
        card = {"title": "达人演示和广告同时突出会议转写", "hypothesis": "品牌可能在测试会议场景。",
                "check": "下期看其他达人的内容是否也采用该场景。", "evidence_ids": list(evidence)}
        rows = [dict(card, evidence_ids=["invented"]), dict(card, check=""), card, card]
        model.return_value = json.dumps({"findings": rows})
        result = summarize_marketing_overview(None, {"range": {}, "evidence": evidence})
        self.assertEqual(1, len(result["findings"]))
        self.assertEqual(list(evidence.values()), result["findings"][0]["evidence"])
        self.assertNotIn("https://example.org", model.call_args.args[2])
        self.assertEqual([], grounded_findings([dict(card, evidence_ids=["previous.ads"])], {"previous.ads": {}}))


if __name__ == "__main__":
    unittest.main()
