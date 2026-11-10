"""Exercise browser deadlines, resource rejection and process-tree cleanup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from server import browser_jobs as jobs
from server.fetchers import FetchError


class BrowserJobTests(unittest.TestCase):
    def setUp(self):
        jobs._COOLDOWN.clear()
        self.addCleanup(jobs._COOLDOWN.clear)
        self.env = patch.dict(os.environ, {
            "MONITOR_COLLECTOR_TIMEOUT_SECONDS": "1",
            "MONITOR_COLLECTOR_COOLDOWN_SECONDS": "30",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_low_memory_does_not_launch_and_releases_slot(self):
        with patch.object(jobs, "available_memory_mb", return_value=120), patch.object(jobs.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(FetchError, "deferred"):
                jobs.run_browser_job("meta_ads", {})
            launch.assert_not_called()
        self.assertTrue(jobs._SLOT.acquire(False))
        jobs._SLOT.release()

    def test_busy_job_does_not_release_another_jobs_slot(self):
        jobs._SLOT.acquire()
        try:
            with self.assertRaisesRegex(FetchError, "busy"):
                jobs.run_browser_job("instagram", {})
            self.assertFalse(jobs._SLOT.acquire(False))
        finally:
            jobs._SLOT.release()

    def fake_process(self, data=None):
        process = MagicMock()
        process.returncode = 0
        process.pid = 999999
        process.communicate.return_value = (json.dumps(data or {"records": []}).encode(), b"")
        return process

    def test_success_reaps_descendants_and_preserves_partial_results(self):
        result = {"records": [{"id": "1"}], "partial": True, "reason": "budget"}
        process = self.fake_process(result)
        with patch.object(jobs, "available_memory_mb", return_value=800), patch.object(jobs, "process_group_rss_mb", return_value=100), patch.object(jobs.subprocess, "Popen", return_value=process), patch.object(jobs, "kill_process_tree") as kill:
            self.assertEqual(jobs.run_browser_job("meta_ads", {}), result)
            kill.assert_called_once_with(process)

    def test_resource_limit_kills_worker_and_backs_off(self):
        process = self.fake_process()
        with patch.object(jobs, "available_memory_mb", return_value=800), patch.object(jobs, "process_group_rss_mb", return_value=500), patch.object(jobs.subprocess, "Popen", return_value=process), patch.object(jobs, "kill_process_tree") as kill:
            # This branch uses /proc process groups on Linux; force it for the mock.
            with patch.object(jobs.os, "name", "posix"):
                with self.assertRaisesRegex(FetchError, "RSS"):
                    jobs.run_browser_job("meta_ads", {})
            kill.assert_called_once_with(process)
            cooldown_until = jobs._COOLDOWN["meta_ads"]
            with self.assertRaisesRegex(FetchError, "cooling down"):
                jobs.run_browser_job("meta_ads", {})
            self.assertEqual(jobs._COOLDOWN["meta_ads"], cooldown_until)

    def test_worker_error_is_not_success(self):
        process = self.fake_process({"error": "browser unavailable"})
        with patch.object(jobs, "available_memory_mb", return_value=800), patch.object(jobs, "process_group_rss_mb", return_value=0), patch.object(jobs.subprocess, "Popen", return_value=process), patch.object(jobs, "kill_process_tree"):
            with self.assertRaisesRegex(FetchError, "browser unavailable"):
                jobs.run_browser_job("instagram", {})

    def test_media_is_blocked_but_graphql_is_allowed(self):
        context = MagicMock()
        jobs.restrict_resources(context)
        route_request = context.route.call_args.args[1]
        for kind in ["image", "media", "font", "websocket"]:
            route = MagicMock()
            route.request.resource_type = kind
            route_request(route)
            route.abort.assert_called_once()
            route.continue_.assert_not_called()
        route = MagicMock()
        route.request.resource_type = "xhr"
        route_request(route)
        route.continue_.assert_called_once()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux process-group regression")
    def test_real_timeout_kills_browser_child(self):
        original_popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child"
            child_code = "import time; time.sleep(60)"
            worker_code = (
                "import subprocess,sys,time; "
                f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
                f"open({str(marker)!r},'w').write(str(p.pid)); "
                "time.sleep(60)"
            )
            def launch(_args, **kwargs):
                return original_popen([sys.executable, "-c", worker_code], **kwargs)
            with patch.object(jobs.subprocess, "Popen", side_effect=launch), patch.object(jobs, "available_memory_mb", return_value=2048):
                with self.assertRaisesRegex(FetchError, "deadline"):
                    jobs.run_browser_job("meta_ads", {})
            child_pid = int(marker.read_text())
            for _ in range(20):
                stat = Path(f"/proc/{child_pid}/stat")
                if not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                    break
                time.sleep(0.05)
            else:
                self.fail("Chromium-like child survived worker timeout")


class MetaBrowserBudgetTests(unittest.TestCase):
    def fake_playwright(self):
        from types import SimpleNamespace
        context = MagicMock()
        browser = MagicMock()
        browser.new_context.return_value = context
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        module = SimpleNamespace(sync_playwright=lambda: manager)
        return module, context, browser

    def test_meta_limit_is_reported_and_records_are_kept(self):
        from server.connectors import collectors
        module, context, browser = self.fake_playwright()
        ads = [{"ad_archive_id": str(x)} for x in range(220)]
        with patch.dict(sys.modules, {"playwright.sync_api": module}), patch.object(collectors, "_meta_public_ad_count_from_html", return_value=220), patch.object(collectors, "_meta_public_ads_from_html", return_value=ads), patch.object(collectors, "_meta_public_payload", side_effect=lambda ad, *_: {"id": ad["ad_archive_id"]}):
            result = collectors._collect_meta_public_ads_in_process({"name": "Example"}, constrained=True, timeout=60)
        self.assertTrue(result["partial"])
        self.assertEqual(len(result["records"]), 200)
        context.close.assert_called_once()
        browser.close.assert_called_once()

    def test_failed_ad_page_is_not_reported_as_empty_success(self):
        from server.connectors import collectors
        module, context, browser = self.fake_playwright()
        context.new_page.return_value.goto.side_effect = TimeoutError("stuck")
        with patch.dict(sys.modules, {"playwright.sync_api": module}):
            with self.assertRaises(FetchError):
                collectors._collect_meta_public_ads_in_process({"name": "Example"}, constrained=True, timeout=60)
        browser.close.assert_called_once()

    def test_brand_filter_retains_partial_status(self):
        from server.connectors import collectors
        result = {"records": [{"author": "Example", "id": "1"}], "partial": True, "reason": "budget"}
        with patch.dict(collectors.CREDENTIALS, {"facebook_access_token": ""}), patch.object(collectors, "run_browser_job", return_value=result), patch.object(collectors, "_meta_page_ids_from_links", return_value=[]), patch.object(collectors, "_meta_historical_page_ids", return_value=[]), patch.object(collectors, "cleanup_meta_ad_mismatches"):
            batch = collectors.collect_meta_ads(MagicMock(), {"name": "Example"})
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch.status, "partial")
        self.assertEqual(batch.error, "budget")


@unittest.skipUnless(os.environ.get("MONITOR_TEST_BROWSER_RUNTIME") == "1", "Opt-in production Chromium runtime")
class BrowserRuntimeTests(unittest.TestCase):
    def test_instagram_worker_finishes_with_real_chromium_and_resource_guard(self):
        original_popen = subprocess.Popen
        # Serve deterministic public-profile fixtures inside the real browser.
        # Other requests still pass through the production media/resource filter.
        code = '''
import json
from playwright.sync_api import BrowserContext
original_new_page = BrowserContext.new_page
def new_page(context, *args, **kwargs):
    def fixture(route):
        kind = route.request.resource_type
        if kind == "document":
            route.fulfill(content_type="text/html", body='<html><img src="/blocked.png"><body>Profile</body></html>')
        elif kind in {"xhr", "fetch"}:
            route.fulfill(content_type="application/json", body=json.dumps({"data": {"user": {"username": "fixture", "id": "123"}}}))
        else:
            route.fallback()
    context.route("https://www.instagram.com/**", fixture)
    return original_new_page(context, *args, **kwargs)
BrowserContext.new_page = new_page
from server.collector_worker import main
raise SystemExit(main())
'''
        processes = []
        def launch(_args, **kwargs):
            process = original_popen([sys.executable, "-c", code], **kwargs)
            processes.append(process)
            return process
        jobs._COOLDOWN.clear()
        with patch.dict(os.environ, {"MONITOR_COLLECTOR_TIMEOUT_SECONDS": "30", "MONITOR_COLLECTOR_MIN_AVAILABLE_MB": "320"}), patch.object(jobs.subprocess, "Popen", side_effect=launch):
            result = jobs.run_browser_job("instagram", {"handle": "fixture", "limit": 1})
        self.assertEqual(result["profile_status"], 200)
        self.assertEqual(result["profile_data"]["data"]["user"]["username"], "fixture")
        self.assertEqual(jobs.process_group_rss_mb(processes[0].pid), 0)


if __name__ == "__main__":
    unittest.main()
