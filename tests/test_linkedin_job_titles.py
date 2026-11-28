import json
import sqlite3
import unittest
from unittest.mock import patch

from server.connectors.hiring.base import RenderResult
from server.connectors.hiring.linkedin import parse_linkedin_job_title
from server.connectors.hiring.runner import ingest_browser_hiring_capture, run_hiring_collection
from server.db import SCHEMA, _repair_linkedin_login_postings
from server.domains.hiring import list_postings, posting_history


class LinkedInJobTitleTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.brand = {"id": "brand"}
        self.url = "https://www.linkedin.com/jobs/view/123/"

    def test_exact_title_after_decoding_and_suffix_removal(self):
        for value in ("Log In or Sign Up", "LinkedIn: Log In or Sign Up", "Sign In | LinkedIn",
                      "LOG IN&nbsp;OR SIGN UP — Singapore | LinkedIn Jobs"):
            with self.subTest(value=value):
                self.assertEqual(parse_linkedin_job_title(value), ("", ""))
        self.assertEqual(parse_linkedin_job_title("Sign Up Experience Designer — London | LinkedIn Jobs"),
                         ("Sign Up Experience Designer", "London"))

    @patch("server.connectors.hiring.linkedin.render")
    def test_collection_retries_a_blocked_detail_without_listing_it(self, render):
        source = "https://www.linkedin.com/company/example/jobs/"
        self.conn.execute(
            "INSERT INTO links (id, brand_id, dimension, channel, platform, url, status, created_at, updated_at) "
            "VALUES ('link', 'brand', 'hiring', 'linkedin', 'linkedin', ?, 'active', '2026-01-01', '2026-01-01')", (source,)
        )
        listing = RenderResult(anchors=[self.url], final_url=source)
        login = RenderResult(title="Log In or Sign Up", final_url=self.url, html="<h1>Log in</h1>")
        job = RenderResult(title="Data Engineer — London | LinkedIn Jobs", text="About the job", final_url=self.url)
        render.side_effect = [login, listing, login, listing, job, listing, login]
        for expected in (0, 0, 1, 1):
            run_hiring_collection(self.conn, self.brand)
            self.assertEqual(list_postings("brand", conn=self.conn)["total"], expected)
        row = self.conn.execute("SELECT title, city, status, last_status FROM job_postings").fetchone()
        self.assertEqual(tuple(row), ("Data Engineer", "London", "open", "blocked"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM job_snapshots").fetchone()[0], 1)
        self.conn.execute("UPDATE job_postings SET status = 'closed', closed_at = '2026-11-07'")
        render.side_effect = [listing, login, listing, job]
        run_hiring_collection(self.conn, self.brand)
        self.assertEqual(list_postings("brand", status="closed", conn=self.conn)["total"], 1)
        run_hiring_collection(self.conn, self.brand)
        reopened = list_postings("brand", status="open", conn=self.conn)["postings"][0]
        self.assertIsNone(reopened["closed_at"])

    def test_browser_capture_skips_bad_items_and_rejects_redirected_source(self):
        payload = dict(platform="linkedin", source_url=self.url, jobs=[
            {"title": "Log In or Sign Up", "url": self.url, "is_open": True},
            {"title": "Engineer", "url": self.url, "is_open": True, "jd_text": "Build products"},
        ])
        result = ingest_browser_hiring_capture(self.conn, self.brand, **payload)
        self.assertEqual((result["captured"], result["errors"]), (1, 1))
        payload["source_url"] = "https://www.linkedin.com/authwall"
        self.assertEqual(ingest_browser_hiring_capture(self.conn, self.brand, **payload)["status"], "blocked")
        self.assertEqual(self.conn.execute("SELECT title FROM job_postings").fetchone()[0], "Engineer")

    def test_legacy_rows_recover_or_wait_for_a_real_title(self):
        for job in ("recover", "unknown", "diff"):
            self.conn.execute(
                "INSERT INTO job_postings (id, brand_id, platform, url, title, jd_text, created_at, updated_at) "
                "VALUES (?, 'brand', 'linkedin', ?, 'Log In or Sign Up', 'Password (6+ characters)', '', '')",
                (job, self.url),
            )
            changes = [{"field": "title", "from": "Data Scientist", "to": "Log In or Sign Up"}] if job == "diff" else []
            self.conn.execute(
                "INSERT INTO job_snapshots (id, posting_id, brand_id, snapshot_date, title, is_open, changes_json, created_at) "
                "VALUES (?, ?, 'brand', '2026-11-08', 'Log In or Sign Up', 1, ?, '2026-11-08')",
                (job, job, json.dumps(changes)),
            )
        self.conn.execute(
            "INSERT INTO job_snapshots (id, posting_id, brand_id, snapshot_date, title, is_open, created_at) "
            "VALUES ('good', 'recover', 'brand', '2026-11-07', 'Sales Manager — London | LinkedIn Jobs', 0, '2026-11-07')"
        )
        for _ in range(2):
            _repair_linkedin_login_postings(self.conn)
        result = list_postings("brand", conn=self.conn)
        self.assertEqual(result["total"], 2)
        filtered = list_postings("brand", city="London", q="Sales", status="closed", conn=self.conn)
        self.assertEqual(filtered["total"], 1)
        item = filtered["postings"][0]
        self.assertEqual((item["title"], item["data_points"], item["has_change"]), ("Sales Manager", 1, False))
        detail = posting_history("recover", self.conn)
        self.assertEqual(([s["id"] for s in detail["snapshots"]], detail["jd_text"]), (["good"], ""))
        self.assertEqual(list_postings("brand", q="Scientist", conn=self.conn)["total"], 1)
        self.assertEqual(tuple(self.conn.execute("SELECT status, url FROM job_postings WHERE id='unknown'").fetchone()),
                         ("unverified", self.url))
