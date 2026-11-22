import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server import ai
from server.analysis_jobs import analysis_job
from server.db import SCHEMA
from server.domains.hiring import analyze_hiring, hiring_strategic_signals


class HiringAnalysisJobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "test.db"
        self.conn = self.connect()
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO brands (id, name, created_at, updated_at) VALUES ('brand', 'Example', '2026-11-01', '2026-11-01')")
        self.conn.execute("""INSERT INTO job_postings
            (id, brand_id, platform, title, jd_text, status, first_seen, last_seen, created_at, updated_at)
            VALUES ('job', 'brand', 'linkedin', 'Backend Engineer', 'Build services', 'open',
                    '2026-11-01', '2026-11-01', '2026-11-01', '2026-11-01')""")
        self.release = threading.Event()

    def connect(self):
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def tearDown(self):
        self.release.set()
        self.conn.close()
        self.directory.cleanup()

    def poll(self, read):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = read()
            if result.get("generation", {}).get("status") != "running":
                return result
            time.sleep(.01)
        self.fail("Background analysis never completed")

    def jd(self, **kwargs):
        return analyze_hiring("brand", start_date="2026-11-01", end_date="2026-11-02",
                              background=True, conn=self.conn, **kwargs)

    def test_jd_returns_immediately_coalesces_and_survives_request_connection_close(self):
        entered = threading.Event()

        def slow_model(conn, postings, context):
            entered.set()
            if not self.release.wait(5):
                raise AssertionError("Worker blocked")
            self.assertEqual(conn.execute("SELECT name FROM brands").fetchone()[0], "Example")
            return {"summary": "Generated JD analysis", "business_directions": []}

        with patch("server.domains.hiring.ai.is_configured", return_value=True), \
             patch("server.domains.hiring.ai.analyze_jd", side_effect=slow_model) as model:
            self.assertTrue(self.jd(cached_only=True)["cache_miss"])
            started = time.monotonic()
            first = self.jd(refresh=True)
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(first["generation"]["status"], "running")
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.jd(refresh=True)["generation"]["status"], "running")
            self.conn.close()
            self.conn = self.connect()
            self.release.set()
            result = self.poll(lambda: self.jd(cached_only=True))
            self.assertEqual(result["summary"], "Generated JD analysis")
            self.assertFalse(result["cache_miss"])
            model.assert_called_once()
        # Complete analysis persists independently of the background job state.
        cached = analyze_hiring("brand", start_date="2026-11-01", end_date="2026-11-02", cached_only=True, conn=self.conn)
        self.assertTrue(cached["cached"])

    def test_jd_failure_stops_polling_and_explicit_retry_replaces_fallback(self):
        with patch("server.domains.hiring.ai.is_configured", return_value=True), \
             patch("server.domains.hiring.ai.analyze_jd", side_effect=ai.LlmError("read timed out")):
            self.jd(refresh=True)
            result = self.poll(lambda: self.jd(cached_only=True))
            self.assertEqual(result["generation"]["status"], "completed")
            self.assertEqual(result["analysis_status"], "fallback")
        with patch("server.domains.hiring.ai.is_configured", return_value=True), \
             patch("server.domains.hiring.ai.analyze_jd", return_value={"summary": "Recovered"}):
            self.jd(refresh=True)
            result = self.poll(lambda: self.jd(cached_only=True))
            self.assertEqual(result["summary"], "Recovered")
            self.assertNotEqual(result.get("analysis_status"), "fallback")

    def test_changed_jd_invalidates_completed_result(self):
        with patch("server.domains.hiring.ai.is_configured", return_value=True), \
             patch("server.domains.hiring.ai.analyze_jd", return_value={"summary": "Old"}):
            self.jd(refresh=True)
            self.poll(lambda: self.jd(cached_only=True))
        self.conn.execute("UPDATE job_postings SET jd_text = 'New product focus'")
        result = self.jd(cached_only=True)
        self.assertTrue(result["cache_miss"])
        self.assertNotIn("generation", result)

    def test_polling_keeps_the_jd_job_and_its_input_after_collection_changes(self):
        entered = threading.Event()

        def model(conn, postings, context):
            entered.set()
            self.assertTrue(self.release.wait(5))
            self.assertEqual(postings[0]["jd_text"], "Build services")
            return {"summary": "Analysis of original JD"}

        with patch("server.domains.hiring.ai.is_configured", return_value=True), \
             patch("server.domains.hiring.ai.analyze_jd", side_effect=model) as analyze:
            self.jd(refresh=True)
            self.assertTrue(entered.wait(2))
            self.conn.execute("UPDATE job_postings SET jd_text = 'New product focus'")
            self.assertEqual(self.jd(poll=True)["generation"]["status"], "running")
            self.release.set()
            self.assertEqual(self.poll(lambda: self.jd(poll=True))["summary"], "Analysis of original JD")
            self.assertTrue(self.jd(cached_only=True)["cache_miss"])
            analyze.assert_called_once()

    def test_no_jd_data_is_a_cache_miss_and_poll_does_not_start_work(self):
        self.conn.execute("DELETE FROM job_postings")
        self.assertTrue(self.jd(cached_only=True)["cache_miss"])
        self.assertEqual(self.jd(poll=True), {})

    def test_strategy_cold_load_automatically_generates_and_caches(self):
        entered = threading.Event()

        def model(*args, **kwargs):
            entered.set()
            self.release.wait(5)
            self.assertGreaterEqual(kwargs["timeout"], 90)
            return {"summary": "Completed strategy", "deep_insights": [], "caveats": []}

        def read():
            return hiring_strategic_signals("brand", background=True, conn=self.conn)

        with patch("server.strategic_signals.ai.is_configured", return_value=True), \
             patch("server.strategic_signals.ai.summarize_overview", side_effect=model) as summarize:
            result = read()
            self.assertEqual(result["generation"]["status"], "running")
            self.assertTrue(entered.wait(2))
            self.release.set()
            result = self.poll(read)
            self.assertEqual(result["headline"]["summary"], "Completed strategy")
            self.assertEqual(result["analysis_status"], "complete")
            self.assertTrue(result["timeline"])
            read()
            summarize.assert_called_once()

    def test_strategy_poll_survives_a_history_change_without_restarting(self):
        entered = threading.Event()

        def model(*args, **kwargs):
            entered.set()
            self.assertTrue(self.release.wait(5))
            return {"summary": "First strategy", "deep_insights": []}

        def read(**kwargs):
            return hiring_strategic_signals("brand", background=True, conn=self.conn, **kwargs)

        with patch("server.strategic_signals.ai.is_configured", return_value=True), \
             patch("server.strategic_signals.ai.summarize_overview", side_effect=model) as summarize:
            read()
            self.assertTrue(entered.wait(2))
            self.conn.execute("UPDATE job_postings SET first_seen='2026-10-31', updated_at='2026-11-02'")
            self.assertEqual(read(poll=True)["generation"]["status"], "running")
            self.release.set()
            result = self.poll(lambda: read(poll=True))
            self.assertEqual(result["headline"]["summary"], "First strategy")
            self.assertEqual(result["window"]["start"], "2026-11-01")
            summarize.assert_called_once()

    def test_worker_failure_reports_terminal_status_and_can_retry(self):
        def fail(conn):
            raise RuntimeError("internal detail")

        with self.assertLogs("server.analysis_jobs", level="ERROR"):
            analysis_job(self.conn, "failure", fail)
            result = self.poll(lambda: analysis_job(self.conn, "failure", fail, start=False))
        self.assertEqual(result["generation"]["status"], "failed")
        self.assertNotIn("internal detail", result["generation"]["error"])
        analysis_job(self.conn, "failure", lambda conn: {"summary": "Retried"}, refresh=True)
        result = self.poll(lambda: analysis_job(self.conn, "failure", fail, start=False))
        self.assertEqual(result["summary"], "Retried")

    def test_invalid_model_json_is_a_recoverable_llm_error(self):
        with patch("server.ai.call_llm", return_value="{invalid JSON}"), \
             self.assertRaises(ai.LlmError):
            ai.analyze_jd(self.conn, [], {})


if __name__ == "__main__":
    unittest.main()
