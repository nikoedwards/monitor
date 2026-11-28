import io
import json
import threading
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import patch
from urllib.error import URLError

from server import ai


class AiRequestTests(unittest.TestCase):
    def test_snapshot_report_recovers_malformed_json_using_original_evidence(self):
        events = [{"date": "2026-11-28", "page": "/", "text_changes": ["New product"],
                   "image": {"media_type": "image/jpeg", "data": "image-data"}}]
        for malformed in ('{"summary":"Incomplete","major_events":[{', "No structured result", '{"summary": []}'):
            with self.subTest(malformed=malformed), patch.object(ai, "get_config", return_value={}), \
                 patch.object(ai, "call_llm_content", side_effect=[malformed, "首页增加了新品介绍。"]) as model:
                report = ai.analyze_web_snapshots(None, events, {})
                self.assertEqual(report["summary"], "首页增加了新品介绍。")
                self.assertTrue(report["caveats"])
                self.assertEqual(report["major_events"], [])
                original, recovery = [call.args[2] for call in model.call_args_list]
                self.assertEqual(original[1:], recovery[1:])
                self.assertIn("New product", recovery[0]["text"])
                self.assertNotIn("请严格返回以下 JSON", recovery[0]["text"])

    def test_snapshot_report_keeps_structured_results_and_rejects_unreadable_recovery(self):
        with patch.object(ai, "get_config", return_value={}):
            with patch.object(ai, "call_llm_content", return_value='{"summary":"报告","highlights":["首页变化"]}') as model:
                result = ai.analyze_web_snapshots(None, [], {})
                self.assertEqual(result["highlights"], ["首页变化"])
                model.assert_called_once()
            with patch.object(ai, "call_llm_content", side_effect=["{", '{"incomplete":']), self.assertRaises(ai.LlmError):
                ai.analyze_web_snapshots(None, [], {})

    def test_overlapping_calls_share_only_the_same_request_and_can_retry(self):
        cfg = {"llm_api_key": "test", "llm_base_url": "http://model.test"}
        for fail in (False, True):
            with self.subTest(fail=fail):
                entered, joined, release = threading.Event(), threading.Event(), threading.Event()

                class ObservedFuture(Future):
                    def result(self, *args, **kwargs):
                        joined.set()
                        return super().result(*args, **kwargs)

                def provider(request, timeout):
                    if request.get_header("Authorization") == "Bearer test":
                        entered.set()
                        self.assertTrue(release.wait(3))
                        if fail:
                            raise URLError("offline")
                    return io.BytesIO(b'{"content":[{"type":"text","text":"ok"}]}')

                with patch.object(ai, "Future", ObservedFuture), patch.object(ai, "urlopen", side_effect=provider) as upstream, ThreadPoolExecutor(3) as pool:
                    first = pool.submit(ai.call_llm, cfg, "system", "prompt")
                    try:
                        self.assertTrue(entered.wait(2))
                        second = pool.submit(ai.call_llm, cfg, "system", "prompt")
                        self.assertTrue(joined.wait(2))
                        other = {**cfg, "llm_api_key": "other"}
                        self.assertEqual("ok", pool.submit(ai.call_llm, other, "system", "prompt").result(2))
                        self.assertEqual(2, upstream.call_count)
                    finally:
                        release.set()
                    for response in (first, second):
                        if fail:
                            with self.assertRaises(ai.LlmError):
                                response.result(2)
                        else:
                            self.assertEqual("ok", response.result(2))
                with patch.object(ai, "urlopen", return_value=io.BytesIO(b'{"content":"fresh"}')) as retry:
                    self.assertEqual("fresh", ai.call_llm(cfg, "system", "prompt"))
                    retry.assert_called_once()

    def test_record_reference_resolves_only_to_a_supplied_source(self):
        url = "https://example.org/original"
        result = {"representative": [{"id": "R1", "title": "Example", "why": "Reason", "url": "invented"},
                                     {"id": "R2", "title": "Outside the sample"}]}
        with patch.object(ai, "get_config", return_value={}), patch.object(ai, "call_llm", return_value=json.dumps(result)):
            summary = ai.summarize_records(None, [{"url": url}], {})
        self.assertEqual([{"title": "Example", "why": "Reason", "url": url}], summary["representative"])
