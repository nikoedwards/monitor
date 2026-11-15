import asyncio
import json
import sqlite3
import unittest
from unittest.mock import patch

from server.db import SCHEMA
from server.domains.hiring import list_postings, router
from server.domains.common import get_conn


class HiringPaginationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for brand_id in ("brand-1", "brand-2"):
            self.conn.execute(
                "INSERT INTO brands (id, name, created_at, updated_at) VALUES (?, ?, '2026-11-15', '2026-11-15')",
                (brand_id, brand_id),
            )
        self.conn.executemany(
            """INSERT INTO job_postings
               (id, brand_id, platform, status, title, city, department, jd_text, last_seen, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-11-15', '2026-11-15', '2026-11-15')""",
            [
                (f"job-{i:04d}", "brand-1", "boss" if i % 2 == 0 else "linkedin",
                 "closed" if i % 3 == 0 else "open", f"工程师 {i}",
                 "深圳" if i % 5 == 0 else "上海", "研发", "负责 AI 产品")
                for i in range(503)
            ] + [("other-brand", "brand-2", "boss", "open", "工程师", "深圳", "研发", "负责 AI 产品")],
        )

    def tearDown(self):
        self.conn.close()

    def listing(self, **filters):
        return list_postings(brand_id="brand-1", conn=self.conn, **filters)

    def test_pages_cover_all_jobs_beyond_previous_500_limit_with_stable_ties(self):
        seen = []
        for page in range(1, 7):
            result = self.listing(page=page, page_size=100)
            self.assertEqual(result["total"], 503)
            self.assertEqual(result["total_pages"], 6)
            self.assertEqual(result["page"], page)
            self.assertEqual(result["page_size"], 100)
            self.assertEqual(len(result["postings"]), 100 if page < 6 else 3)
            seen.extend(item["id"] for item in result["postings"])
        self.assertEqual(seen, [f"job-{i:04d}" for i in range(503)])

    def test_all_filters_apply_to_both_total_and_every_page(self):
        filters = {"platform": "boss", "status": "open", "city": " 深圳 ", "q": " AI ", "page_size": 7}
        expected = [f"job-{i:04d}" for i in range(503) if i % 2 == 0 and i % 3 != 0 and i % 5 == 0]
        first = self.listing(**filters)
        seen = []
        for page in range(1, first["total_pages"] + 1):
            result = self.listing(page=page, **filters)
            self.assertEqual(result["total"], len(expected))
            seen.extend(item["id"] for item in result["postings"])
        self.assertEqual(seen, expected)

    def test_empty_results_and_high_pages_have_valid_boundaries(self):
        empty = self.listing(q="不存在的职位", page=100)
        self.assertEqual((empty["total"], empty["total_pages"], empty["page"], empty["postings"]), (0, 1, 1, []))
        last = self.listing(page=999)
        self.assertEqual((last["page"], last["total_pages"], len(last["postings"])), (11, 11, 3))

    def test_deleted_last_page_clamps_to_remaining_page(self):
        self.conn.execute("DELETE FROM job_postings WHERE id >= 'job-0500' AND brand_id = 'brand-1'")
        result = self.listing(page=11)
        self.assertEqual((result["total"], result["page"], result["total_pages"]), (500, 10, 10))
        self.assertEqual(result["postings"][-1]["id"], "job-0499")

    def test_search_treats_wildcards_and_quotes_literally(self):
        self.conn.execute("UPDATE job_postings SET title = ?, city = ? WHERE id = 'job-0000'", ("100% AI_engineer\\test's", "深_圳"))
        for term in ("100%", "AI_", "\\test", "test's"):
            with self.subTest(term=term):
                result = self.listing(q=term)
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["postings"][0]["id"], "job-0000")
        self.assertEqual(self.listing(city="_")["total"], 1)
        self.assertEqual(self.listing(q="' OR 1=1 --")["total"], 0)

    def test_all_and_blank_filters_keep_default_brand_scope(self):
        result = self.listing(platform="all", status="all", city=" ", q=" ")
        self.assertEqual((result["total"], result["page"], result["page_size"]), (503, 1, 50))
        self.assertEqual(len(result["postings"]), 50)

    def test_only_current_page_is_enriched_with_snapshot_queries(self):
        from server.domains.hiring import posting_to_dict

        with patch("server.domains.hiring.posting_to_dict", wraps=posting_to_dict) as serialize:
            self.listing(page=3, page_size=20)
        self.assertEqual(serialize.call_count, 20)

    def test_http_schema_constrains_paging_parameters(self):
        # Check FastAPI's public contract without importing the application's startup jobs.
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        params = {p["name"]: p["schema"] for p in app.openapi()["paths"]["/api/hiring/postings"]["get"]["parameters"]}
        self.assertEqual(params["page"]["minimum"], 1)
        self.assertEqual(params["page_size"]["minimum"], 1)
        self.assertEqual(params["page_size"]["maximum"], 100)

    def test_http_rejects_invalid_paging_and_returns_metadata(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)

        async def connection():
            yield self.conn

        app.dependency_overrides[get_conn] = connection

        async def request(query):
            messages = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                messages.append(message)

            await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                       "method": "GET", "scheme": "http", "path": "/api/hiring/postings",
                       "query_string": query.encode(), "headers": []}, receive, send)
            return messages[0]["status"], json.loads(b"".join(m.get("body", b"") for m in messages))

        for query in ("page=0", "page=-1", "page=1.5", "page_size=0", "page_size=101", "page_size=abc"):
            with self.subTest(query=query):
                status, _ = asyncio.run(request("brand_id=brand-1&" + query))
                self.assertEqual(status, 422)
        status, result = asyncio.run(request("brand_id=brand-1&page=6&page_size=100"))
        self.assertEqual(status, 200)
        self.assertEqual((result["total"], result["total_pages"], len(result["postings"])), (503, 6, 3))


if __name__ == "__main__":
    unittest.main()
