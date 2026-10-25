import json
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import ai
from server.community_summaries import summarize_community_records
from server.db import SCHEMA
from server.domains.common import get_conn
from server.domains.content import router
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


if __name__ == "__main__":
    unittest.main()
