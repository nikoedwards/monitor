import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from server import ai
from server.compare_summary import build_compare_packet, compare_summary, validate_compare_result
from server.db import SCHEMA
from server.domains.insights import compare_strategy_summary


class CompareSummaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for bid, name in (("a", "Alpha"), ("b", "Beta"), ("c", "No data")):
            self.conn.execute("INSERT INTO brands (id,name,created_at,updated_at) VALUES (?,?,?,?)", (bid, name, "2026-01-01", "2026-01-01"))
        self.record("a-now", "a", "2026-10-25", "Alpha product feedback")
        self.record("a-old", "a", "2026-09-25", "Previous feedback")
        self.record("b-now", "b", "2026-10-26", "Beta product feedback")
        self.record("future", "a", "2026-11-01", "Future, must not leak")
        self.record("ancient", "a", "2026-08-01", "Too old, must not leak")

    def tearDown(self):
        self.conn.close()

    def record(self, rid, bid, at, body):
        self.conn.execute("INSERT INTO records (id,brand_id,source_id,data_type,dimension,channel,platform,title,body,occurred_at,created_at) VALUES (?,?, 'manual','review','voc','app','app_store',?,?,?,?)", (rid, bid, body, body, at, at))

    def packet(self):
        brands = self.conn.execute("SELECT * FROM brands ORDER BY name").fetchall()
        return build_compare_packet(self.conn, brands, "2026-10-01", "2026-10-30")

    def finding(self, *ids, **extra):
        return {"title": "共同关注产品体验", "observation": "两品牌本期反馈", "implication": "可能转向体验竞争", "next_signal": "观察产品更新", "confidence": "medium", "evidence_ids": list(ids), **extra}

    def result(self):
        return {"summary": "本期监测品牌出现体验竞争信号。", "industry_changes": [self.finding("a:record:a-now", "b:record:b-now")], "brand_moves": [self.finding("a:record:a-now", brand_id="a")], "opportunities": [], "risks": [], "caveats": []}

    def test_packet_includes_all_brands_and_only_current_or_explicit_previous(self):
        packet = self.packet()
        self.assertEqual({"a", "b", "c"}, {b["brand_id"] for b in packet["brands"]})
        refs = {ref["id"]: ref for ref in packet["evidence"]}
        self.assertEqual({"a:record:a-now", "a:record:a-old", "b:record:b-now"}, set(refs))
        self.assertEqual("previous", refs["a:record:a-old"]["period"])
        self.assertEqual("current", refs["a:record:a-now"]["period"])
        self.assertEqual("2026-09-01", packet["range"]["previous_start"])
        empty = next(b for b in packet["brands"] if b["brand_id"] == "c")
        self.assertTrue(all(c["evidence_count"] == 0 for c in empty["coverage"]))

    def test_ongoing_jobs_are_explicitly_marked_not_new_events(self):
        self.conn.execute("INSERT INTO job_postings (id,brand_id,platform,title,first_seen,created_at,updated_at) VALUES ('job','a','manual','Engineer','2026-08-01','2026-08-01','2026-10-25')")
        job = next(ref for ref in self.packet()["evidence"] if ref["kind"] == "hiring")
        self.assertEqual("active_in_window", job["period"])
        self.assertEqual("2026-08-01", job["at"])

    def test_cross_brand_findings_resolve_real_sources(self):
        result = self.result()
        result["industry_changes"][0]["evidence"] = [{"url": "https://invented.example"}]
        result["industry_changes"][0]["evidence_ids"].append("invented-id")
        validated = validate_compare_result(result, self.packet())
        finding = validated["industry_changes"][0]
        self.assertEqual({"a", "b"}, set(finding["brand_ids"]))
        self.assertEqual(["a:record:a-now", "b:record:b-now"], finding["evidence_ids"])
        self.assertFalse(any(ref["url"] == "https://invented.example" for ref in finding["evidence"]))

    def test_rejects_single_brand_industry_claim_and_previous_only_claim(self):
        result = {"summary": "Unsupported", "industry_changes": [self.finding("a:record:a-now")], "risks": [self.finding("a:record:a-old")]}
        validated = validate_compare_result(result, self.packet())
        self.assertEqual([], validated["industry_changes"])
        self.assertEqual([], validated["risks"])
        self.assertNotEqual("Unsupported", validated["summary"])

    def test_brand_moves_cannot_borrow_other_brands_evidence(self):
        result = {"brand_moves": [self.finding("a:record:a-now", brand_id="b"), self.finding("a:record:a-now", brand_id="unknown")]}
        self.assertEqual([], validate_compare_result(result, self.packet())["brand_moves"])

    @patch("server.compare_summary.build_compare_packet")
    @patch("server.compare_summary.ai.call_llm")
    def test_uncached_window_skips_evidence_and_model(self, llm, build_packet):
        result = compare_summary(self.conn, "2026-10-01", "2026-10-30", cached_only=True)
        self.assertTrue(result["cache_miss"])
        llm.assert_not_called()
        build_packet.assert_not_called()

    @patch("server.compare_summary.ai.is_configured", return_value=True)
    @patch("server.compare_summary.ai.call_llm")
    def test_generation_cache_refresh_and_data_changes(self, llm, configured):
        llm.return_value = json.dumps(self.result())
        first = compare_summary(self.conn, "2026-10-01", "2026-10-30")
        self.assertEqual("ai", first["mode"])
        self.assertEqual(3, len(first["brands"]))
        self.assertTrue(compare_summary(self.conn, "2026-10-01", "2026-10-30")["cached"])
        self.assertEqual(1, llm.call_count)
        compare_summary(self.conn, "2026-10-01", "2026-10-30", refresh=True)
        self.assertEqual(2, llm.call_count)
        self.conn.execute("UPDATE records SET body='New evidence content' WHERE id='a-now'")
        self.assertTrue(compare_summary(self.conn, "2026-10-01", "2026-10-30", cached_only=True)["cache_miss"])
        compare_summary(self.conn, "2026-10-01", "2026-10-30")
        self.assertEqual(3, llm.call_count)
        self.assertTrue(compare_summary(self.conn, "2026-10-02", "2026-10-30", cached_only=True)["cache_miss"])

    @patch("server.compare_summary.ai.is_configured", return_value=False)
    def test_missing_configuration_is_actionable(self, configured):
        with self.assertRaises(HTTPException) as context:
            compare_summary(self.conn, "2026-10-01", "2026-10-30")
        self.assertEqual(400, context.exception.status_code)
        self.assertIn("设置", context.exception.detail)

    @patch("server.compare_summary.ai.call_llm")
    def test_empty_window_does_not_call_model(self, llm):
        result = compare_summary(self.conn, "2026-12-01", "2026-12-30")
        self.assertEqual("empty", result["mode"])
        self.assertEqual([], result["industry_changes"])
        llm.assert_not_called()

    @patch("server.compare_summary.ai.is_configured", return_value=True)
    @patch("server.compare_summary.ai.call_llm")
    def test_model_errors_and_malformed_output_are_not_cached(self, llm, configured):
        for raw in ('{"summary":"truncated"', '[]', '{"summary": []}'):
            llm.return_value = raw
            with self.assertRaises(HTTPException) as context:
                compare_summary(self.conn, "2026-10-01", "2026-10-30")
            self.assertEqual(502, context.exception.status_code)
        llm.side_effect = ai.LlmError("upstream failure")
        with self.assertRaises(HTTPException):
            compare_summary(self.conn, "2026-10-01", "2026-10-30")
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM llm_summary_cache").fetchone()[0])

    def test_route_normalizes_date_range(self):
        with patch("server.compare_summary.compare_summary", return_value={}) as summarize:
            compare_strategy_summary(start_date="2026-10-30", end_date="2026-10-01", conn=self.conn)
        summarize.assert_called_once_with(self.conn, "2026-10-01", "2026-10-30", refresh=False, cached_only=False)

    def test_background_generation_can_be_polled_without_duplicate_work(self):
        release = threading.Event()
        params = {"start_date": "2026-10-01", "end_date": "2026-10-30", "background": True}

        def summarize(conn, start, end, *, refresh):
            self.assertTrue(release.wait(3))
            return {"mode": "ai", "range": [start, end]}

        with tempfile.TemporaryDirectory() as directory:
            with patch("server.compare_summary.compare_summary", side_effect=summarize) as generate:
                conn = sqlite3.connect(Path(directory) / "compare.db")
                try:
                    for _ in range(2):
                        result = compare_strategy_summary(**params, refresh=True, conn=conn)
                        self.assertEqual("running", result["generation"]["status"])
                    self.assertEqual("running", compare_strategy_summary(**params, conn=conn)["generation"]["status"])
                    release.set()
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        result = compare_strategy_summary(**params, conn=conn)
                        if result["generation"]["status"] != "running":
                            break
                        time.sleep(0.01)
                    self.assertEqual("completed", result["generation"]["status"])
                    self.assertEqual(["2026-10-01", "2026-10-30"], result["range"])
                    generate.assert_called_once()
                finally:
                    release.set()
                    conn.close()

    def test_sampling_preserves_low_volume_brands_and_sources(self):
        for index in range(100):
            self.record(f"busy-{index}", "a", "2026-10-29", "Many records")
        self.conn.execute("INSERT INTO web_snapshots (id,monitor_id,brand_id,snapshot_date,url,title,created_at) VALUES ('web','monitor','a','2026-10-25','https://example.com','New product','2026-10-25')")
        packet = self.packet()
        self.assertIn("b:record:b-now", {ref["id"] for ref in packet["evidence"]})
        self.assertIn("a:web:web", {ref["id"] for ref in packet["evidence"]})


if __name__ == "__main__":
    unittest.main()
