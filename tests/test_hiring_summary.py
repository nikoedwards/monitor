import sqlite3
import unittest

from server.db import SCHEMA
from server.domains.hiring import hiring_summary, infer_department


class HiringSummaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO brands (id, name, created_at, updated_at) VALUES ('brand-1', 'Example', ?, ?)",
            ("2026-10-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00"),
        )

    def tearDown(self):
        self.conn.close()

    def test_infers_department_when_sources_leave_it_empty(self):
        self.assertEqual(infer_department("", "AI 产品经理", "负责产品规划"), "产品")
        self.assertEqual(infer_department(None, "后端工程师", "负责服务研发"), "技术研发")
        self.assertEqual(infer_department(None, "海外销售", "拓展客户"), "销售")
        self.assertEqual(infer_department("增长团队", "普通岗位", ""), "增长团队")

        self.conn.execute(
            """
            INSERT INTO job_postings (
                id, brand_id, platform, title, department, city, jd_text, status,
                first_seen, last_seen, business_tags_json, created_at, updated_at
            ) VALUES ('inferred-job', 'brand-1', 'linkedin', '后端工程师', '', '深圳', '负责服务研发', 'open',
                      '2026-10-24T08:00:00+00:00', '2026-10-24T08:00:00+00:00', '[]',
                      '2026-10-24T08:00:00+00:00', '2026-10-24T08:00:00+00:00')
            """
        )
        result = hiring_summary("brand-1", start_date="2026-10-24", end_date="2026-10-24", conn=self.conn)
        self.assertEqual(result["by_department"], [{"department": "技术研发", "count": 1}])
        self.assertEqual(result["released_postings"][0]["department"], "技术研发")

    def test_summary_returns_window_job_details_for_released_and_closed_cards(self):
        self.conn.executemany(
            """
            INSERT INTO job_postings (
                id, brand_id, platform, title, department, city, jd_text, status,
                first_seen, last_seen, closed_at, business_tags_json, created_at, updated_at
            ) VALUES (?, 'brand-1', 'linkedin', ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?)
            """,
            [
                (
                    "new-job", "AI 产品经理", "产品", "深圳", "负责 AI 产品规划",
                    "open", "2026-10-23T08:00:00+00:00", "2026-10-24T08:00:00+00:00", None,
                    "2026-10-23T08:00:00+00:00", "2026-10-24T08:00:00+00:00",
                ),
                (
                    "closed-job", "海外销售", "销售", "上海", "负责海外市场拓展",
                    "closed", "2026-10-20T08:00:00+00:00", "2026-10-24T08:00:00+00:00", "2026-10-24T08:00:00+00:00",
                    "2026-10-20T08:00:00+00:00", "2026-10-24T08:00:00+00:00",
                ),
                (
                    "outside-job", "旧岗位", "工程", "北京", "不应出现在窗口列表",
                    "closed", "2026-09-01T08:00:00+00:00", "2026-09-10T08:00:00+00:00", "2026-09-10T08:00:00+00:00",
                    "2026-09-01T08:00:00+00:00", "2026-09-10T08:00:00+00:00",
                ),
            ],
        )

        result = hiring_summary(
            "brand-1", start_date="2026-10-19", end_date="2026-10-25", conn=self.conn
        )

        self.assertEqual(result["window"], {"start": "2026-10-19", "end": "2026-10-25"})
        self.assertEqual(result["released_in_window"], 2)
        self.assertEqual(result["closed_in_window"], 1)
        self.assertEqual([item["id"] for item in result["released_postings"]], ["new-job", "closed-job"])
        self.assertEqual(result["closed_postings"][0]["id"], "closed-job")
        self.assertEqual(result["closed_postings"][0]["first_seen"], "2026-10-20T08:00:00+00:00")
        self.assertEqual(result["closed_postings"][0]["closed_at"], "2026-10-24T08:00:00+00:00")
        self.assertEqual(result["released_postings"][0]["jd_text"], "负责 AI 产品规划")


if __name__ == "__main__":
    unittest.main()
