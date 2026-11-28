import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server import ai
from server.db import SCHEMA
from server.domains.insights import insights_summary
from server.domains.content import marketing_overview, marketing_overview_summary


class MarketingInsightsJobTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.db"
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("""INSERT INTO settings (key, value, updated_at)
                             VALUES ('llm_api_key', 'test-token', '2026-11-22')""")
        query = patch("server.domains.insights.query_records", return_value=[
            {"platform": "reddit", "title": "Community post"},
            {"platform": "feedback.plaud.ai", "title": "A product request"},
        ])
        query.start()
        self.addCleanup(query.stop)

    def summary(self, **options):
        return insights_summary(brand_id="plaud", dimension="marketing", channel="community",
                                start_date="2026-10-24", end_date="2026-11-22", platforms="reddit",
                                conn=self.conn, **options)

    def wait_for_job(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = self.summary(background=True, cached_only=True)
            if state["generation"]["status"] != "running":
                return state
            time.sleep(0.01)
        self.fail("Background summary did not finish")

    def test_generation_outlives_start_request_and_persists_the_model_result(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_model(worker, records, context):
            self.assertEqual(str(self.path), worker.execute("PRAGMA database_list").fetchone()[2])
            self.assertEqual(["reddit"], [record["platform"] for record in records])
            entered.set()
            if not release.wait(5):
                raise AssertionError("Model request was not released")
            return {"summary": "Fresh model summary"}

        with patch.object(ai, "summarize_records", side_effect=slow_model) as model:
            self.assertTrue(self.summary(background=True, cached_only=True)["cache_miss"])
            model.assert_not_called()
            try:
                self.assertEqual("running", self.summary(background=True)["generation"]["status"])
                self.assertTrue(entered.wait(3))
                self.assertEqual("running", self.summary(background=True, refresh=True)["generation"]["status"])
            finally:
                release.set()
            finished = self.wait_for_job()
            model.assert_called_once()
        self.assertEqual("completed", finished["generation"]["status"])
        self.assertEqual("Fresh model summary", finished["summary"])
        self.assertEqual(1, finished["record_count"])
        cached = self.summary(cached_only=True)
        self.assertTrue(cached["cached"])
        self.assertEqual(finished["summary"], cached["summary"])

    def test_failed_refresh_preserves_the_previous_model_summary(self):
        with patch.object(ai, "summarize_records", return_value={"summary": "Previous model summary"}):
            self.summary()
        with patch.object(ai, "summarize_records", side_effect=ai.LlmError("timed out")), \
             self.assertLogs("server.analysis_jobs", level="ERROR"):
            started = self.summary(background=True, refresh=True)
            self.assertEqual("running", started["generation"]["status"])
            self.assertEqual("Previous model summary", started["summary"])
            failed = self.wait_for_job()
        self.assertEqual("failed", failed["generation"]["status"])
        self.assertEqual("Previous model summary", failed["summary"])
        self.assertTrue(failed["cached"])

    def test_marketing_dashboard_first_response_uses_only_same_scope_summary(self):
        ai.put_cached_summary(self.conn, "marketing-overview:plaud|2026-10-24|2026-11-22", {"mode": "ai", "summary": "已保存"})
        with patch("server.domains.content._marketing_overview_payload", side_effect=lambda *args: {}):
            def preview(brand):
                return marketing_overview(brand, start_date="2026-10-24", end_date="2026-11-22", conn=self.conn)["summary_preview"]
            self.assertEqual("已保存", preview("plaud")["summary"])
            self.assertEqual("fallback", preview("other")["mode"])

    def test_marketing_overview_refresh_survives_reload_and_reuses_one_job(self):
        options = dict(brand_id="plaud", start_date="2026-10-24", end_date="2026-11-22", conn=self.conn, background=True)
        data = {"range": {}, "coverage": {}, "yesterday": {}, "signals": [], "modules": {}}
        release = threading.Event()

        def model(worker, evidence):
            self.assertTrue(release.wait(3))
            return {"summary": "updated"}

        ai.put_cached_summary(self.conn, "marketing-overview:plaud|2026-10-24|2026-11-22", {"mode": "ai", "summary": "previous"})
        with patch("server.domains.content._marketing_overview_payload", return_value=data) as prepare, \
             patch.object(ai, "summarize_marketing_overview", side_effect=model) as upstream:
            try:
                self.assertEqual("previous", marketing_overview_summary(**options)["summary"])
                prepare.assert_not_called()
                marketing_overview_summary(**options, refresh=True)
                for flags in ({}, {"refresh": True}, {"cached_only": True}):
                    running = marketing_overview_summary(**options, **flags)
                    self.assertEqual("running", running["generation"]["status"])
                    self.assertEqual("previous", running["summary"])
            finally:
                release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = marketing_overview_summary(**options, cached_only=True)
                if state["generation"]["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual("updated", state["summary"])
            prepare.assert_called_once()
            upstream.assert_called_once()


if __name__ == "__main__":
    unittest.main()
