import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import ai
from server.community_summaries import summarize_community_records
from server.db import SCHEMA
from server.domains.common import get_conn
from server.domains.content import prepare_voice_summaries, router
from server.records import insert_record


class CommunitySummaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        insert_record(self.conn, {"id": "siri", "channel": "community", "title": "Start/stop recordings via Siri",
                                  "body": "Please expose recording controls as App Intents for Apple Shortcuts and Siri."})
        insert_record(self.conn, {"id": "case", "channel": "community", "title": "Plaud doesn't fit on the iPhone Duo",
                                  "body": "The magnetic case overhangs the phone and catches when it folds."})
        insert_record(self.conn, {"id": "media", "channel": "media", "body": "Unrelated media article"})
        self.config = patch("server.community_summaries.ai.get_config", return_value={"llm_api_key": "test"})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.addCleanup(self.conn.close)

    def response(self, **summaries):
        return json.dumps({"summaries": [{"id": rid, "summary": text} for rid, text in summaries.items()]})

    def test_maps_summaries_by_id_and_reuses_persistent_cache(self):
        text = self.response(case="用户反馈磁吸保护壳在 iPhone Duo 折叠时卡住", siri="用户希望通过 Siri 和快捷指令控制录音")
        with patch("server.community_summaries.ai.call_llm", return_value=text) as llm:
            first = summarize_community_records(self.conn, ["siri", "case"])
            second = summarize_community_records(self.conn, ["case", "siri"])
        self.assertEqual(first, second)
        self.assertEqual(1, llm.call_count)
        self.assertEqual("用户希望通过 Siri 和快捷指令控制录音。", first["summaries"]["siri"]["summary"])
        prompt = json.loads(llm.call_args.args[2])
        self.assertIn("App Intents", prompt["posts"][0]["body"])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM community_post_summaries").fetchone()[0])

    def test_changed_body_invalidates_only_its_summary(self):
        with patch("server.community_summaries.ai.call_llm", return_value=self.response(siri="用户希望用 Siri 开始录音")):
            summarize_community_records(self.conn, ["siri"])
        self.conn.execute("UPDATE records SET body = 'Siri recording now works, but stopping fails.' WHERE id = 'siri'")
        with patch("server.community_summaries.ai.call_llm", return_value=self.response(siri="用户反馈 Siri 已能开始录音但仍无法停止")) as llm:
            result = summarize_community_records(self.conn, ["siri"])
        self.assertEqual(1, llm.call_count)
        self.assertIn("仍无法停止", result["summaries"]["siri"]["summary"])

    def test_independent_batches_overlap_and_shared_records_reuse_generation(self):
        entered = {rid: threading.Event() for rid in ("siri", "case")}
        release = threading.Event()

        def model(cfg, system, prompt):
            rid = json.loads(prompt)["posts"][0]["id"]
            entered[rid].set()
            self.assertTrue(release.wait(3))
            return self.response(**{rid: "用户希望调整录音设置"})

        with patch("server.community_summaries.ai.call_llm", side_effect=model) as upstream, ThreadPoolExecutor(3) as pool:
            first = pool.submit(summarize_community_records, self.conn, ["siri"])
            try:
                self.assertTrue(entered["siri"].wait(2))
                duplicate = pool.submit(summarize_community_records, self.conn, ["siri"])
                other = pool.submit(summarize_community_records, self.conn, ["case"])
                self.assertTrue(entered["case"].wait(2))
            finally:
                release.set()
            self.assertEqual(first.result(2), duplicate.result(2))
            self.assertEqual("ready", other.result(2)["summaries"]["case"]["status"])
            self.assertEqual(2, upstream.call_count)

    def test_voice_feedback_caches_headline_and_summary_for_non_community_records(self):
        insert_record(self.conn, {"id": "review", "dimension": "voc", "channel": "app",
                                  "body": "Après un retour depuis une note, tous mes filtres disparaissent."})
        response = json.dumps({"summaries": [{
            "id": "review", "headline": "返回列表后筛选条件消失",
            "summary": "用户从笔记返回后发现所有筛选条件被清空",
        }]})
        with patch("server.community_summaries.ai.call_llm", return_value=response) as llm:
            first = summarize_community_records(self.conn, ["review", "media"], voice=True)
            second = summarize_community_records(self.conn, ["review"], voice=True)
        self.assertEqual(1, llm.call_count)
        self.assertEqual(first["summaries"]["review"], second["summaries"]["review"])
        self.assertEqual("返回列表后筛选条件消失", second["summaries"]["review"]["headline"])
        self.assertEqual("unavailable", first["summaries"]["media"]["status"])

    def test_malformed_batch_recovers_valid_records_individually(self):
        malformed = self.response(siri="用户希望开始录音")
        with patch(
            "server.community_summaries.ai.call_llm",
            side_effect=[malformed, self.response(case="用户反馈磁吸保护壳在折叠手机时会卡住")],
        ) as llm:
            result = summarize_community_records(self.conn, ["siri", "case"])
        self.assertEqual(2, llm.call_count)
        self.assertEqual(["case"], [p["id"] for p in json.loads(llm.call_args.args[2])["posts"]])
        self.assertEqual("用户希望开始录音。", result["summaries"]["siri"]["summary"])
        self.assertEqual("ready", result["summaries"]["siri"]["status"])
        self.assertEqual("ready", result["summaries"]["case"]["status"])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM community_post_summaries").fetchone()[0])

    def test_voice_first_response_includes_prepared_translation_until_source_changes(self):
        self.conn.execute("INSERT INTO brands(id, name, created_at, updated_at) VALUES ('brand', 'Brand', '', '')")
        original = "Returning from a note clears every filter."
        headline, summary = "返回列表后筛选条件消失", "从笔记返回列表后所有筛选条件被清空。"
        insert_record(self.conn, {"id": "filters", "brand_id": "brand", "dimension": "voc",
                                  "body": original, "sentiment": "negative"})
        rule_text = "Cannot log in after resetting my password."
        insert_record(self.conn, {"id": "login", "brand_id": "brand", "dimension": "voc",
                                  "body": rule_text, "sentiment": "negative"})
        response = json.dumps({"summaries": [{"id": "filters", "headline": headline, "summary": summary}]})
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_conn] = lambda: self.conn
        with TestClient(app) as client, patch("server.community_summaries.ai.call_llm", return_value=response) as llm:
            def board():
                return client.get("/api/voc/summary", params={"brand_id": "brand"}).json()

            cold = board()
            self.assertTrue(any(not item["problem_summary"] for item in cold["topics"]))
            llm.assert_not_called()
            prepare_voice_summaries(self.conn, "brand")
            self.assertEqual(["filters"], [post["id"] for post in json.loads(llm.call_args.args[2])["posts"]])
            self.conn.execute("UPDATE community_post_summaries SET created_at = '2000-01-01T00:00:00Z'")
            prepare_voice_summaries(self.conn, "brand")
            warm = board()
            topic = next(item for item in warm["topics"] if item["representative_record_id"] == "filters")
            self.assertEqual((headline, summary), (topic["title"], topic["problem_summary"]))
            self.assertEqual(original, topic["evidence"][0]["text"])
            examples = [example for source in warm["source_catalog"] for example in source["examples"]]
            self.assertIn(summary, [example["summary"] for example in examples])
            records = client.get("/api/voc/records", params={"brand_id": "brand"}).json()["records"]
            issue = next(r["voice_issue"] for r in records if r["id"] == "filters")
            self.assertEqual((headline, summary), (issue["title"], issue["summary"]))
            self.conn.execute("UPDATE records SET title = 'A different experience' WHERE id = 'filters'")
            self.assertFalse(next(t for t in board()["topics"] if t["representative_record_id"] == "filters")["problem_summary"])
            llm.assert_called_once()

    def test_missing_empty_and_other_channel_records_are_not_sent_to_model(self):
        self.conn.execute("UPDATE records SET title = '', body = '' WHERE id = 'siri'")
        with patch("server.community_summaries.ai.call_llm") as llm:
            result = summarize_community_records(self.conn, ["siri", "media", "missing"])
        llm.assert_not_called()
        self.assertTrue(all(item["status"] == "unavailable" for item in result["summaries"].values()))

    def test_no_configuration_or_model_failure_does_not_invent_summary(self):
        with patch("server.community_summaries.ai.get_config", return_value={}):
            result = summarize_community_records(self.conn, ["siri"])
        self.assertEqual("unconfigured", result["summaries"]["siri"]["status"])
        with patch("server.community_summaries.ai.call_llm", side_effect=ai.LlmError("failed")):
            result = summarize_community_records(self.conn, ["siri"])
        self.assertEqual({"status": "error", "summary": None}, result["summaries"]["siri"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM community_post_summaries").fetchone()[0])

    def test_rejects_wrong_ids_missing_ids_english_multiple_sentences_and_long_output(self):
        invalid = [
            self.response(other="用户请求控制录音"), self.response(),
            self.response(siri="The user asks for Siri controls."),
            self.response(siri="用户希望开始录音。还希望停止录音。"),
            self.response(siri="用户希望\n控制录音"),
            self.response(siri="用户希望" + "录" * 121),
            "not json",
        ]
        for text in invalid:
            with self.subTest(text=text), patch("server.community_summaries.ai.call_llm", return_value=text):
                result = summarize_community_records(self.conn, ["siri"])
                self.assertEqual("error", result["summaries"]["siri"]["status"])

    def test_cache_is_deleted_with_record(self):
        with patch("server.community_summaries.ai.call_llm", return_value=self.response(siri="用户希望用 Siri 控制录音")):
            summarize_community_records(self.conn, ["siri"])
        self.conn.execute("DELETE FROM records WHERE id = 'siri'")
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM community_post_summaries").fetchone()[0])

    def test_http_batch_validation_and_response(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_conn] = lambda: self.conn
        with TestClient(app) as client, patch("server.community_summaries.ai.call_llm", return_value=self.response(siri="用户希望用 Siri 控制录音")):
            self.assertEqual(422, client.post("/api/records/community-summaries", json={"record_ids": []}).status_code)
            self.assertEqual(422, client.post("/api/records/community-summaries", json={"record_ids": ["siri"] * 13}).status_code)
            result = client.post("/api/records/community-summaries", json={"record_ids": ["siri"]})
            self.assertEqual(200, result.status_code)
            self.assertEqual("ready", result.json()["summaries"]["siri"]["status"])
            cached = client.post("/api/records/community-summaries", json={"record_ids": ["siri"]})
            self.assertEqual(200, cached.status_code)
            self.assertEqual("ready", cached.json()["summaries"]["siri"]["status"])


if __name__ == "__main__":
    unittest.main()
