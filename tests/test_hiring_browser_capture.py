import sqlite3
import unittest
import json
from urllib.error import URLError
from unittest.mock import patch

from server.connectors.hiring.runner import ingest_browser_hiring_capture, run_hiring_collection
from server.db import SCHEMA, _cleanup_fake_boss_login_postings
from server.util import utc_now
from tools.boss_browser_worker import CaptureResult, _post_capture, _rows_to_links


class HiringBrowserCaptureTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        now = utc_now()
        self.brand = {"id": "brand-1", "name": "Example"}
        self.conn.execute(
            "INSERT INTO brands (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (self.brand["id"], self.brand["name"], now, now),
        )

    def tearDown(self):
        self.conn.close()

    def capture(self, **overrides):
        payload = {
            "platform": "boss",
            "source_url": "https://www.zhipin.com/gongsi/example.html",
            "source_title": "Example 招聘",
            "jobs": [{
                "url": "https://www.zhipin.com/job_detail/abc123.html?ka=search_list_jname_1_blank",
                "title": "产品经理",
                "city": "深圳",
                "jd_text": "负责 AI 产品规划",
                "is_open": True,
            }],
        }
        payload.update(overrides)
        return ingest_browser_hiring_capture(self.conn, self.brand, **payload)

    def test_browser_capture_creates_paused_helper_source_and_snapshot(self):
        result = self.capture()
        self.assertEqual(result["captured"], 1)
        posting = self.conn.execute("SELECT * FROM job_postings").fetchone()
        self.assertEqual(posting["external_id"], "abc123")
        self.assertEqual(posting["title"], "产品经理")
        link = self.conn.execute("SELECT * FROM links WHERE id = ?", (posting["link_id"],)).fetchone()
        self.assertEqual(link["status"], "paused")
        self.assertEqual(link["cadence"], "manual")
        snapshot = self.conn.execute("SELECT * FROM job_snapshots").fetchone()
        self.assertEqual(snapshot["is_open"], 1)
        config = json.loads(link["config_json"] or "{}")
        self.assertTrue(config["browser_automation"])

    def test_boss_browser_source_is_skipped_by_cookie_runner(self):
        self.capture()
        result = run_hiring_collection(self.conn, self.brand)
        self.assertEqual(result["links"], 0)
        self.assertEqual(result["captured"], 0)

    def test_linkedin_source_still_uses_cookie_runner(self):
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO links (id, brand_id, dimension, channel, platform, url, canonical_url,
                cadence, status, config_json, created_at, updated_at)
            VALUES ('linkedin-link', ?, 'hiring', 'linkedin', 'linkedin',
                'https://www.linkedin.com/company/example/jobs/',
                'https://www.linkedin.com/company/example/jobs/', 'daily', 'active', '{}', ?, ?)
            """,
            (self.brand["id"], now, now),
        )

        class _Provider:
            def expand(self, conn, link):
                return []

            def fetch(self, conn, posting):
                raise AssertionError("no postings should be fetched")

        with patch("server.connectors.hiring.runner.pick_provider", return_value=_Provider()) as pick:
            result = run_hiring_collection(self.conn, self.brand)

        self.assertEqual(result["links"], 1)
        pick.assert_called_once()
        self.assertEqual(pick.call_args.args[0], "linkedin")

    def test_partial_list_capture_preserves_existing_jd(self):
        self.capture()
        result = self.capture(jobs=[{
            "url": "https://www.zhipin.com/job_detail/abc123.html",
            "title": "产品经理",
            "jd_text": "",
            "is_open": True,
        }])
        posting = self.conn.execute("SELECT * FROM job_postings").fetchone()
        self.assertEqual(posting["jd_text"], "负责 AI 产品规划")
        self.assertEqual(result["changed"], 0)

    def test_detail_capture_can_mark_job_closed(self):
        self.capture()
        result = self.capture(jobs=[{
            "url": "https://www.zhipin.com/job_detail/abc123.html",
            "title": "产品经理",
            "jd_text": "负责 AI 产品规划",
            "is_open": False,
        }])
        self.assertEqual(result["closed"], 1)
        posting = self.conn.execute("SELECT * FROM job_postings").fetchone()
        self.assertEqual(posting["status"], "closed")

    def test_blocked_page_records_status_without_fake_job(self):
        result = self.capture(page_status="blocked", page_error="安全验证", jobs=[])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS c FROM job_postings").fetchone()["c"], 0)
        self.assertEqual(self.conn.execute("SELECT * FROM links").fetchone()["last_status"], "blocked")

    def test_blocked_page_preserves_jobs_captured_before_challenge(self):
        result = self.capture(page_status="blocked", page_error="详情页安全验证")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["captured"], 1)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS c FROM job_postings").fetchone()["c"], 1)
        link = self.conn.execute("SELECT * FROM links").fetchone()
        self.assertEqual(link["last_status"], "blocked")
        self.assertIn("安全验证", link["last_error"])

    def test_local_api_failure_falls_back_to_direct_database_ingestion(self):
        capture = CaptureResult(
            source_url="https://www.zhipin.com/gongsi/example.html",
            source_title="Example 招聘",
            page_status="ok",
            page_error="",
            jobs=[{
                "url": "https://www.zhipin.com/job_detail/abc123.html",
                "title": "产品经理",
                "city": "深圳",
                "jd_text": "负责 AI 产品规划",
                "is_open": True,
            }],
        )
        with (
            patch("tools.boss_browser_worker.urlopen", side_effect=URLError("offline")),
            patch("tools.boss_browser_worker.connect", return_value=self.conn),
        ):
            result = _post_capture("http://127.0.0.1:8790", capture, self.brand["id"])
        self.assertEqual(result["delivery"], "direct_db")
        self.assertEqual(result["captured"], 1)

    def test_zhipin_url_is_accepted_even_with_legacy_platform_value(self):
        links = _rows_to_links([{
            "id": "boss-link",
            "brand_id": self.brand["id"],
            "url": "https://www.zhipin.com/gongsi/example.html",
            "platform": "legacy",
        }])
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].platform, "boss")

    def test_browser_capture_reuses_legacy_zhipin_link(self):
        now = utc_now()
        source_url = "https://www.zhipin.com/gongsi/example.html"
        self.conn.execute(
            """
            INSERT INTO links (id, brand_id, dimension, channel, platform, url, canonical_url,
                status, config_json, created_at, updated_at)
            VALUES ('legacy-boss', ?, 'hiring', 'legacy', 'legacy', ?, ?, 'active', '{}', ?, ?)
            """,
            (self.brand["id"], source_url, source_url, now, now),
        )
        result = self.capture()
        self.assertEqual(result["captured"], 1)
        link = self.conn.execute("SELECT * FROM links WHERE id = 'legacy-boss'").fetchone()
        self.assertEqual(link["platform"], "boss")
        self.assertEqual(link["channel"], "boss")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS c FROM links").fetchone()["c"], 1)

    def test_non_job_url_is_rejected(self):
        result = self.capture(jobs=[{"url": "https://www.zhipin.com/web/user/", "title": "登录", "is_open": True}])
        self.assertEqual(result["captured"], 0)
        self.assertEqual(result["errors"], 1)

    def test_legacy_fake_login_posting_is_removed(self):
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO job_postings (id, brand_id, platform, url, title, status, created_at, updated_at)
            VALUES ('fake', ?, 'boss', 'https://www.zhipin.com/web/user/',
                    '【BOSS直聘注册登录】boss直聘在线注册登录', 'open', ?, ?)
            """,
            (self.brand["id"], now, now),
        )
        _cleanup_fake_boss_login_postings(self.conn)
        self.assertIsNone(self.conn.execute("SELECT * FROM job_postings WHERE id = 'fake'").fetchone())


if __name__ == "__main__":
    unittest.main()
