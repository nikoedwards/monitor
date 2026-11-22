import json
import sqlite3
import unittest
from unittest.mock import patch

from server import ai
from server.db import SCHEMA
from server.domains.insights import insights_summary
from server.voice_insights import summarize_voice


class VoiceInsightsTests(unittest.TestCase):
    def test_scoped_opening_uses_real_counts_and_resolves_cross_domain_evidence(self):
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO brands (id,name,created_at,updated_at) VALUES ('b','Nova','2026-10-01','2026-10-01')")
            conn.executemany(
                "INSERT INTO records (id,source_id,brand_id,data_type,dimension,channel,title,body,sentiment,occurred_at,created_at) "
                "VALUES (?,?,'b','user_voice','voc',?,'Nova',?,?,?,'2026-10-20')",
                [
                    ("a", "manual_csv", "manual", "The speaker labels disappeared.", "negative", "2026-10-20"),
                    ("b", "manual_csv", "manual", "I can no longer see who spoke.", "negative", "2026-10-21"),
                    ("c", "manual_csv", "manual", "I like the transcription accuracy.", "positive", "2026-10-22"),
                    ("outside", "manual_csv", "manual", "Out of range", "negative", "2026-09-30"),
                    ("other-source", "amazon_reviews", "amazon", "Another source", "positive", "2026-10-20"),
                ],
            )
            conn.execute(
                "INSERT INTO web_snapshots (id,monitor_id,brand_id,snapshot_date,url,title,text_excerpt,created_at) "
                "VALUES ('w','m','b','2026-10-23','https://example.com/nova','Nova 产品页','主打说话人标签','2026-10-23')"
            )

            def mock_model(_config, _system, prompt):
                data = json.loads(prompt.split("\n分析资料：\n", 1)[1])
                self.assertEqual({"positive": 1, "negative": 2, "neutral": 0}, data["sentiment_counts"])
                self.assertEqual({"record:a", "record:b", "record:c"}, {r["id"] for r in data["feedback"]})
                self.assertIn("web:w", {r["id"] for r in data["context"]})
                return json.dumps({
                    "summary": "用户情绪偏负向。",
                    "positive": "用户肯定转录准确度。",
                    "negative": "用户不满说话人标签消失。",
                    "frequent_issues": [
                        {"title": "说话人标签消失", "detail": "复查录音时无法区分发言人。", "evidence_ids": ["record:a", "record:b", "invented"]},
                        {"title": "只有一条反馈", "evidence_ids": ["record:a", "record:a", "web:w"]},
                    ],
                    "strategic_insights": [
                        {"title": "核对功能承诺", "observation": "官网仍在宣传说话人标签。", "implication": "验证新版体验与宣传是否一致。",
                         "evidence_ids": ["record:a", "web:w", "invented"]},
                        {"title": "缺少反馈", "observation": "官网提到了标签。", "implication": "需要调查。", "evidence_ids": ["web:w"]},
                    ],
                })

            args = {"brand_id": "b", "dimension": "voc", "sources": "manual_feedback",
                    "start_date": "2026-10-01", "end_date": "2026-10-30", "conn": conn}
            ai.put_cached_summary(
                conn, 'insights:b|voc||2026-10-01|2026-10-30|{"sources":"manual_feedback"}', {"summary": "旧版"},
            )
            self.assertTrue(insights_summary(**args, cached_only=True)["cache_miss"])
            with patch.object(ai, "is_configured", return_value=True), patch.object(ai, "call_llm", side_effect=mock_model):
                response = insights_summary(**args)
            self.assertEqual(3, response["record_count"])
            opening = response["voice_opening"]
            self.assertEqual([2], [item["count"] for item in opening["frequent_issues"]])
            self.assertEqual(1, len(opening["strategic_insights"]))
            insight = opening["strategic_insights"][0]
            self.assertTrue(insight["cross_domain"])
            self.assertEqual(["record:a", "web:w"], [item["id"] for item in insight["evidence"]])
            self.assertEqual("https://example.com/nova", insight["evidence"][1]["url"])
            self.assertTrue(insights_summary(**args, cached_only=True)["cached"])

    def test_official_and_sponsored_posts_alone_do_not_produce_a_feedback_summary(self):
        records = [
            {"id": "official", "data_type": "social_post", "sentiment": "positive", "title": "促销"},
            {"id": "sponsored", "data_type": "creator_post", "sentiment": "positive", "title": "折扣", "metrics": {"is_sponsored": True}},
        ]
        with patch.object(ai, "call_llm") as model:
            response = summarize_voice(None, records, "2026-10-01", "2026-10-30", {})
        model.assert_not_called()
        self.assertEqual(0, response["record_count"])
        self.assertEqual([], response["voice_opening"]["strategic_insights"])
