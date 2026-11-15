import sqlite3
import unittest
from unittest.mock import patch

from server.db import SCHEMA
from server.domains.hiring import hiring_org_estimate, hiring_org_radar, infer_city


class HiringOrgRadarTests(unittest.TestCase):
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

    def test_city_is_inferred_from_provider_title_when_column_is_empty(self):
        self.assertEqual(
            infer_city("", "Senior Product Manager — Singapore | LinkedIn Jobs", ""),
            "Singapore",
        )
        self.assertEqual(
            infer_city(None, "Backend Engineer", "This role is based in Tokyo."),
            "Tokyo",
        )

    def test_aggregates_departments_locations_and_visible_people(self):
        self.conn.executemany(
            """
            INSERT INTO job_postings (
                id, brand_id, platform, title, department, city, jd_text, status,
                first_seen, last_seen, closed_at, business_tags_json, created_at, updated_at
            ) VALUES (?, 'brand-1', 'linkedin', ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?)
            """,
            [
                ("p1", "Backend Engineer", "", "San Francisco", "Build services", "open", "2026-10-20", "2026-10-24", None, "2026-10-20", "2026-10-24"),
                ("p2", "Product Manager", "产品", "San Francisco", "Own roadmap", "open", "2026-10-21", "2026-10-24", None, "2026-10-21", "2026-10-24"),
                ("p3", "Sales Manager", "销售", "New York", "Sell globally", "closed", "2026-10-18", "2026-10-22", "2026-10-22", "2026-10-18", "2026-10-22"),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO linkedin_profiles (
                id, brand_id, source_type, title, headline, status, monitor, first_seen, created_at, updated_at
            ) VALUES ('person-1', 'brand-1', 'company', 'Backend Engineer', 'Engineering', 'active', 1, '2026-10-20', '2026-10-20', '2026-10-20')
            """
        )
        self.conn.execute(
            "INSERT INTO job_snapshots (id, posting_id, brand_id, snapshot_date, is_open, title, department, city, created_at) VALUES ('s1', 'p1', 'brand-1', '2026-10-24', 1, 'Backend Engineer', '', 'San Francisco', '2026-10-24')"
        )

        result = hiring_org_radar("brand-1", start_date="2026-10-18", end_date="2026-10-24", conn=self.conn)

        self.assertEqual(result["overview"]["open_roles"], 2)
        self.assertEqual(result["overview"]["known_people"], 1)
        self.assertEqual(result["overview"]["city_count"], 2)
        departments = {item["department"]: item for item in result["departments"]}
        self.assertEqual(departments["技术研发"]["known_people"], 1)
        self.assertEqual(result["locations"][0]["city"], "San Francisco")
        self.assertEqual(result["trend"][-1]["active"], 1)
        self.assertEqual(len(result["data_quality"]["limitations"]), 2)

    def test_empty_data_is_explicit(self):
        result = hiring_org_radar("brand-1", start_date="2026-10-18", end_date="2026-10-24", conn=self.conn)
        self.assertEqual(result["overview"]["open_roles"], 0)
        self.assertEqual(result["overview"]["known_people"], 0)
        self.assertEqual(result["departments"], [])
        self.assertEqual(result["locations"], [])
        self.assertEqual(result["overview"]["confidence"], "low")

    @patch(
        "server.ai.call_llm",
        return_value='{"summary":"基于招聘信号的区间估算","overall":{"low":20,"high":45,"confidence":"medium","basis":["岗位与员工样本"]},"departments":[{"department":"技术研发","low":8,"high":20,"confidence":"medium","rationale":"在招工程岗位与可见样本","evidence_ids":["p1"]}],"locations":[],"assumptions":["公开员工样本不完整"],"limitations":["招聘岗位不等于员工数"]}',
    )
    def test_model_estimate_is_structured_and_cached(self, _call_llm):
        self.conn.execute(
            """
            INSERT INTO job_postings (
                id, brand_id, platform, title, department, city, jd_text, status,
                first_seen, last_seen, business_tags_json, created_at, updated_at
            ) VALUES ('p1', 'brand-1', 'linkedin', 'Backend Engineer', '', 'San Francisco', 'Build services', 'open',
                      '2026-10-20', '2026-10-24', '[]', '2026-10-20', '2026-10-24')
            """
        )
        first = hiring_org_estimate("brand-1", start_date="2026-10-18", end_date="2026-10-24", conn=self.conn)
        second = hiring_org_estimate("brand-1", start_date="2026-10-18", end_date="2026-10-24", conn=self.conn)
        self.assertEqual(first["overall"]["low"], 20)
        self.assertEqual(first["departments"][0]["evidence_ids"], ["p1"])
        self.assertTrue(second["cached"])
        self.assertEqual(_call_llm.call_count, 1)


if __name__ == "__main__":
    unittest.main()
