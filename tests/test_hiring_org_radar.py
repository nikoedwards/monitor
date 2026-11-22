import sqlite3
import unittest
from unittest.mock import patch

from server.db import SCHEMA
from server.domains.hiring import cached_hiring_org_estimate, hiring_org_estimate, hiring_org_radar


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
        local_departments = result["locations"][0]["department_details"]
        self.assertEqual({row["department"] for row in local_departments}, {"技术研发", "产品"})
        self.assertEqual({ref["id"] for row in local_departments for ref in row["evidence"]}, {"p1", "p2"})
        self.assertTrue(all(row["known_people"] == 0 for row in local_departments))
        role_families = {item["role_family"]: item for item in result["role_families"]}
        self.assertEqual(role_families["Backend Engineer"]["known_people"], 1)
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

    @patch("server.ai.call_llm")
    def test_all_history_cache_tracks_evidence_and_rejects_wrong_local_citations(self, call_llm):
        call_llm.return_value = (
            '{"overall":null,"locations":['
            '{"city":"London","low":2,"high":12,"evidence_ids":["p1"],'
            '"departments":[{"department":"产品","low":1,"high":6,"evidence_ids":["p2"]}]},'
            '{"city":"Paris","low":2,"high":6,"evidence_ids":["p2"]}]}'
        )
        self.conn.executemany(
            """
            INSERT INTO job_postings (
                id, brand_id, platform, title, department, city, status, first_seen,
                last_seen, closed_at, business_tags_json, created_at, updated_at
            ) VALUES (?, 'brand-1', 'linkedin', 'Product Manager', '产品', ?, 'closed',
                      '2024-01-01', '2024-01-07', '2024-01-07', '[]', '2024-01-01', '2024-01-07')
            """,
            [("p1", "London"), ("p2", "New York")],
        )
        query = {"brand_id": "brand-1", "scope": "all_history", "conn": self.conn}
        result = hiring_org_estimate(**query)
        self.assertIsNone(result["overall"])
        self.assertEqual([row["city"] for row in result["locations"]], ["London"])
        self.assertEqual(result["locations"][0]["departments"], [])
        self.assertEqual(cached_hiring_org_estimate(**query)["estimate"], result)
        self.assertEqual(hiring_org_radar(**query)["window"]["start"], "2024-01-01")

        self.conn.execute("DELETE FROM job_postings WHERE id = 'p2'")
        self.assertIsNone(cached_hiring_org_estimate(**query)["estimate"])
        self.assertEqual(call_llm.call_count, 1)


if __name__ == "__main__":
    unittest.main()
