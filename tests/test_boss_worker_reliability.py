"""Offline browser/ingestion regressions; never open a real browser or source."""
import asyncio
import contextlib
import io
import json
import sqlite3
import types
import unittest
from unittest.mock import AsyncMock, patch

from server.connectors.hiring.runner import ingest_browser_hiring_capture
from server.db import SCHEMA
from server.schemas import BrowserHiringCaptureIn
from server.util import utc_now
from tools import boss_browser_worker as worker


SOURCE = "https://www.zhipin.com/gongsi/job/example.html"
URLS = [f"https://www.zhipin.com/job_detail/{i}.html" for i in range(3)]


def job(url=URLS[0], **values):
    return {"url": url, "title": "产品经理", "jd_text": "负责产品规划", "is_open": True, **values}


class Page:
    def __init__(self, url=URLS[0], title="产品经理", body="职位描述 负责产品规划", **selectors):
        self.url, self.page_title, self.body = url, title, body
        self.selectors = selectors
        self.close = AsyncMock()
        self.wait_for_timeout = AsyncMock()
        self.goto = AsyncMock()

    async def title(self):
        return self.page_title

    def locator(self, selector):
        text = self.body if selector == "body" else self.selectors.get(selector, "")
        locator = AsyncMock()
        locator.inner_text.return_value = text
        locator.first = locator
        return locator


class WorkerReliabilityTests(unittest.TestCase):
    def collect(self, results, *, max_jobs=3, pages=None):
        listing = worker.CaptureResult(SOURCE, "公司招聘", "ok", "", [], discovered_jobs=3)
        browser = AsyncMock()
        browser.new_page.side_effect = [Page(SOURCE), *(pages or [Page(url) for url in URLS])]
        with (
            patch.object(worker, "_extract_listing", AsyncMock(return_value=(listing, URLS))),
            patch.object(worker, "_extract_detail", AsyncMock(side_effect=results)),
        ):
            return asyncio.run(worker._collect_link(browser, worker.HiringLink("source", "brand", SOURCE), max_jobs, 0))

    def test_one_detail_failure_keeps_valid_jobs_and_reports_partial(self):
        capture = self.collect([{"job": job()}, RuntimeError("timeout"), {"job": job(URLS[2])}])
        self.assertEqual(capture.page_status, "partial")
        self.assertEqual(capture.failed_details, 1)
        self.assertEqual(len(capture.jobs), 2)

    def test_max_jobs_reports_skipped_count(self):
        capture = self.collect([{"job": job()}], max_jobs=1)
        self.assertEqual(capture.page_status, "partial")
        self.assertEqual(capture.skipped_details, 2)
        self.assertEqual(capture.discovered_jobs, 3)

    def test_challenge_stops_remaining_details_and_preserves_prior_jobs(self):
        capture = self.collect([{"job": job()}, {"blocked": True, "error": "安全验证"}])
        self.assertEqual(capture.page_status, "blocked")
        self.assertEqual(capture.failed_details, 1)
        self.assertEqual(capture.skipped_details, 1)
        self.assertEqual(len(capture.jobs), 1)

    def test_timeout_landing_on_challenge_is_globally_blocked(self):
        capture = self.collect([RuntimeError("timeout")], pages=[Page(body="安全验证")])
        self.assertEqual(capture.page_status, "blocked")
        self.assertEqual(capture.jobs, [])

    def test_missing_jd_is_partial(self):
        capture = self.collect([{"job": job(jd_text="")}], max_jobs=1)
        self.assertEqual(capture.page_status, "partial")

    def test_unknown_actual_url_never_falls_back_to_requested_url(self):
        for url in ("", "about:blank", "https://other.example/job_detail/0.html", "https://[broken"):
            with self.subTest(url=url):
                detail = asyncio.run(worker._extract_detail(Page(url), URLS[0]))
                self.assertTrue(detail["blocked"])
                self.assertNotIn("job", detail)

    def test_redirects_errors_and_empty_pages_never_become_jobs(self):
        for page in (Page(SOURCE), Page(URLS[1]), Page(body="系统异常"), Page(body="")):
            with self.subTest(url=page.url, body=page.body):
                result = asyncio.run(worker._extract_detail(page, URLS[0]))
                self.assertNotIn("job", result)

    def test_valid_detail_has_source_timestamp_and_fields(self):
        page = Page(**{"h1": "产品经理", ".job-sec-text": "负责产品规划", ".location-address": "深圳"})
        result = asyncio.run(worker._extract_detail(page, URLS[0], "公司招聘"))["job"]
        self.assertEqual(result["city"], "深圳")
        self.assertEqual(result["title"], "产品经理")
        self.assertEqual(result["jd_text"], "负责产品规划")
        self.assertTrue(result["raw"]["captured_at"])

    def test_explicit_closed_detail_without_title_is_recordable(self):
        result = asyncio.run(worker._extract_detail(Page(body="职位已下线"), URLS[0]))
        self.assertFalse(result["job"]["is_open"])

    def test_close_error_does_not_erase_results(self):
        page = Page()
        page.close.side_effect = RuntimeError("browser closed")
        capture = self.collect([{"job": job()}], pages=[page], max_jobs=1)
        self.assertEqual(len(capture.jobs), 1)

    def test_global_stop_does_not_visit_next_source(self):
        links = [worker.HiringLink(str(i), "brand", SOURCE) for i in range(2)]
        args = worker.build_parser().parse_args([])
        blocked = worker.CaptureResult(SOURCE, "", "blocked", "无法确认当前网址", [])
        with (
            patch.object(worker, "_collect_link", AsyncMock(return_value=blocked)) as collect,
            patch.object(worker, "_post_capture", return_value={"status": "blocked"}) as post,
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()),
        ):
            code = asyncio.run(worker._collect_sources(None, links, args))
        self.assertEqual(code, 1)
        self.assertEqual(collect.await_count, 1)
        self.assertEqual(post.call_count, 1)

    def test_partial_run_returns_nonzero_but_can_visit_next_source(self):
        args = worker.build_parser().parse_args([])
        capture = worker.CaptureResult(SOURCE, "", "partial", "详情失败", [], failed_details=1)
        with (
            patch.object(worker, "_collect_link", AsyncMock(return_value=capture)) as collect,
            patch.object(worker, "_post_capture", return_value={"status": "partial"}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = asyncio.run(worker._collect_sources(None, [worker.HiringLink(str(i), "brand", SOURCE) for i in range(2)], args))
        self.assertEqual(code, 1)
        self.assertEqual(collect.await_count, 2)

    def test_http_payload_preserves_failure_counts(self):
        response = contextlib.nullcontext(io.BytesIO(b'{"status":"partial"}'))
        capture = worker.CaptureResult(SOURCE, "", "partial", "上限", [], 1, 2, 3)
        with patch.object(worker, "urlopen", return_value=response) as post:
            worker._post_capture("https://monitor.example", capture, "brand")
        payload = BrowserHiringCaptureIn.model_validate(json.loads(post.call_args.args[0].data))
        self.assertEqual((payload.failed_details, payload.skipped_details, payload.discovered_jobs), (1, 2, 3))

    def test_login_only_requires_manual_headed_wait(self):
        for argv in (["--login-only"], ["--login-only", "--headed", "--wait-for-login", "-1"]):
            with patch("sys.argv", ["worker", *argv]), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(worker.main(), 2)

    def test_login_only_never_reads_sources_or_delivers_capture(self):
        args = worker.build_parser().parse_args(["--login-only", "--headed", "--wait-for-login", "1"])
        context = AsyncMock()
        context.pages = [Page("about:blank")]
        playwright = AsyncMock()
        playwright.chromium.launch_persistent_context.return_value = context
        manager = AsyncMock()
        manager.__aenter__.return_value = playwright
        module = types.ModuleType("playwright.async_api")
        module.async_playwright = lambda: manager
        with (
            patch.dict("sys.modules", {"playwright.async_api": module}),
            patch.object(worker, "_read_links") as read,
            patch.object(worker, "_post_capture") as post,
            patch.object(worker.Path, "mkdir"),
            patch.object(worker.asyncio, "sleep", AsyncMock()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(asyncio.run(worker._run(args)), 0)
        read.assert_not_called()
        post.assert_not_called()
        launch = playwright.chromium.launch_persistent_context.call_args
        self.assertEqual(launch.args[0], str(worker.DEFAULT_PROFILE_DIR.resolve()))
        self.assertFalse(launch.kwargs["headless"])
        context.close.assert_awaited_once()


class CaptureReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.brand = {"id": "brand", "name": "Example"}
        self.conn.execute("INSERT INTO brands (id,name,created_at,updated_at) VALUES (?,?,?,?)", ("brand", "Example", utc_now(), utc_now()))

    def ingest(self, **overrides):
        return ingest_browser_hiring_capture(self.conn, self.brand, **{
            "platform": "boss", "source_url": SOURCE, "jobs": [job()], **overrides,
        })

    def test_partial_counts_survive_ingestion_and_snapshot(self):
        result = self.ingest(page_status="partial", page_error="详情失败", failed_details=1, skipped_details=2, discovered_jobs=4)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["skipped_details"], 2)
        link = self.conn.execute("SELECT * FROM links").fetchone()
        self.assertEqual(link["last_status"], "partial")
        self.assertIn("详情失败", link["last_error"])
        raw = json.loads(self.conn.execute("SELECT raw_json FROM job_snapshots").fetchone()[0])
        self.assertEqual(raw["source_url"], SOURCE)
        self.assertTrue(raw["captured_at"])
        self.assertEqual(raw["capture_stats"]["failed_details"], 1)

    def test_one_rejected_item_makes_result_partial(self):
        result = self.ingest(jobs=[job(), job(URLS[1], title="系统异常")])
        self.assertEqual((result["status"], result["captured"], result["errors"]), ("partial", 1, 1))

    def test_discovery_count_alone_can_reveal_incomplete_capture(self):
        result = self.ingest(discovered_jobs=3)
        self.assertEqual(result["status"], "partial")

    def test_login_source_cannot_claim_success_with_job_payload(self):
        result = self.ingest(source_url="https://www.zhipin.com/web/user/")
        self.assertEqual((result["status"], result["captured"]), ("blocked", 0))

    def test_login_error_and_empty_items_are_rejected_even_at_job_url(self):
        for title in ("请先登录", "安全验证", "页面不存在", ""):
            with self.subTest(title=title):
                self.assertEqual(self.ingest(jobs=[job(title=title, jd_text="")])["captured"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_postings").fetchone()[0], 0)

    def test_job_path_in_query_does_not_create_job(self):
        result = self.ingest(jobs=[job("https://www.zhipin.com/login?redirect=/job_detail/fake.html")])
        self.assertEqual(result["captured"], 0)

    def test_partial_items_do_not_close_unseen_jobs_or_erase_jd(self):
        self.ingest(jobs=[job(), job(URLS[1])])
        result = self.ingest(jobs=[job(jd_text="")], page_status="partial")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_postings WHERE status='open'").fetchone()[0], 2)
        self.assertTrue(all(row[0] == "负责产品规划" for row in self.conn.execute("SELECT jd_text FROM job_postings")))

    def test_invalid_status_rejected_before_creating_source(self):
        with self.assertRaises(ValueError):
            self.ingest(page_status="unknown")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0], 0)

    def test_api_route_forwards_partial_counts(self):
        from server.domains.hiring import browser_capture

        payload = BrowserHiringCaptureIn(
            brand_id="brand", source_url=SOURCE, page_status="partial",
            failed_details=1, skipped_details=2, discovered_jobs=4, jobs=[job()],
        )
        result = browser_capture(payload, self.conn)
        self.assertEqual(result["status"], "partial")
        self.assertEqual((result["failed_details"], result["skipped_details"], result["discovered_jobs"]), (1, 2, 4))


if __name__ == "__main__":
    unittest.main()
