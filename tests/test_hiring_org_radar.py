import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from server.connectors.hiring.linkedin import LinkedInJobsProvider
from server.connectors.hiring.runner import _record_snapshot
from server.db import SCHEMA
from server.schemas import HiringHeadcountReferenceIn
from server.domains.hiring import (
    cached_hiring_org_estimate, hiring_org_estimate, hiring_org_radar, save_hiring_headcount_reference,
)


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


    def test_linkedin_locations_use_title_and_recover_the_latest_good_snapshot(self):
        self.conn.executemany(
            """
            INSERT INTO job_postings (
                id,brand_id,platform,title,city,status,first_seen,created_at,updated_at
            ) VALUES (?, 'brand-1', 'linkedin', ?, ?, 'open', '2026-11-01', '2026-11-01', '2026-11-20')
            """,
            [
                ("sf", "Brand &amp; Content at Example — San Francisco, CA | LinkedIn Jobs", ""),
                ("tokyo", "Influencer Marketing - Tokyo — 東京 | LinkedIn求人", ""),
                ("closed", "LinkedIn: Log In or Sign Up", ""),
                ("blocked", "LinkedIn", ""),
                ("unknown", "Business Operations", ""),
                ("explicit", "QA Engineer — Singapore | LinkedIn Jobs", "深圳"),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO job_snapshots (id,posting_id,brand_id,snapshot_date,title,is_open,created_at)
            VALUES ('snap', 'closed', 'brand-1', '2026-11-15',
                    'Sales Manager at Example — Seattle, WA | LinkedIn Jobs', 0, '2026-11-15')
            """
        )
        result = hiring_org_radar("brand-1", scope="all_history", conn=self.conn)
        self.assertEqual(result["overview"]["open_roles"], 4)
        self.assertEqual(result["overview"]["city_count"], 4)
        self.assertEqual(result["data_quality"]["postings_total"], 5)
        self.assertEqual(result["locations"][-1]["city"], "未标注地区")
        by_city = {item["city"]: item for item in result["locations"]}
        self.assertEqual(by_city["Seattle, WA"]["open_roles"], 0)
        self.assertEqual(by_city["深圳"]["evidence"][0]["id"], "explicit")
        self.assertEqual(by_city["東京"]["open_roles"], 1)
        self.assertEqual(by_city["San Francisco, CA"]["evidence"][0]["city_source"], "linkedin_title")
        self.assertEqual(by_city["San Francisco, CA"]["top_roles"][0]["title"], "Brand & Content at Example")

    @patch("server.connectors.hiring.linkedin.render")
    def test_linkedin_login_page_preserves_a_closed_posting(self, render):
        provider = LinkedInJobsProvider()
        render.return_value = SimpleNamespace(status="ok", title="LinkedIn: Log In or Sign Up", meta={}, text="")
        self.conn.execute(
            """
            INSERT INTO job_postings (id,brand_id,platform,title,status,created_at,updated_at)
            VALUES ('job','brand-1','linkedin','Product Manager','closed','2026-11-01','2026-11-03')
            """
        )
        posting = dict(self.conn.execute("SELECT * FROM job_postings").fetchone())
        snapshot = provider.fetch(self.conn, posting)
        self.assertEqual(snapshot.status, "blocked")
        _record_snapshot(self.conn, posting, snapshot)
        stored = self.conn.execute("SELECT title,status FROM job_postings").fetchone()
        self.assertEqual(tuple(stored), ("Product Manager", "closed"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_snapshots").fetchone()[0], 0)

    @patch("server.ai.call_llm", return_value='{"overall":{"low":20,"high":45,"confidence":"low"}}')
    def test_headcount_reference_is_stored_and_used_as_a_scale_anchor(self, call_llm):
        scope = {"brand_id": "brand-1", "scope": "all_history", "conn": self.conn}
        hiring_org_estimate(**scope)
        reference = HiringHeadcountReferenceIn(count=800, as_of="2026-11-22", source="用户提供的最新情报")
        saved = save_hiring_headcount_reference("brand-1", reference, conn=self.conn)
        self.assertEqual(saved, {"count": 800, "as_of": "2026-11-22", "source": "用户提供的最新情报"})
        self.assertEqual(hiring_org_radar(**scope)["headcount_reference"], saved)
        self.assertIsNone(cached_hiring_org_estimate(**scope)["estimate"])
        result = hiring_org_estimate(**scope)
        self.assertIsNone(result["overall"])
        self.assertIn('"count": 800', call_llm.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
