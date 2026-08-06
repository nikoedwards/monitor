import sqlite3
import unittest

from server.connectors.hiring.runner import ingest_browser_hiring_capture
from server.db import SCHEMA, _cleanup_fake_boss_login_postings
from server.util import utc_now


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
