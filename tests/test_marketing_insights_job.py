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


if __name__ == "__main__":
    unittest.main()
