import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server.connectors.hiring.base import ProfileRef, ProfileSnapshot, RenderResult
from server.connectors.hiring.linkedin import LinkedInPeopleProvider
from server.connectors.hiring.runner import discover_linkedin_people_candidates, run_linkedin_people_collection
from server.db import SCHEMA
from server.domains.hiring import (
    create_employee,
    employee_history,
    import_company_employees,
    update_employee,
    update_employee_monitor_selection,
)
from server.schemas import LinkedInMonitorSelectionIn, LinkedInProfileIn, LinkedInProfileUpdate
from server.util import utc_now


class _FakePeopleProvider:
    def expand_profiles(self, conn, link):
        return []

    def fetch_profile(self, conn, profile):
        return ProfileSnapshot(
            name="Ada Example",
            headline="VP of AI",
            title="VP of AI",
            is_active=True,
            raw={"provider": "fake"},
        )

    def fetch_activities(self, conn, profile):
        return []


class _FakeCompanyPeopleProvider(_FakePeopleProvider):
    def expand_profiles(self, conn, link):
        return [
            ProfileRef(
                profile_url="https://www.linkedin.com/in/ada-example/",
                external_id="ada-example",
                name="Ada Example",
            ),
            ProfileRef(
                profile_url="https://www.linkedin.com/in/grace-example/",
                external_id="grace-example",
                name="Grace Example",
            ),
        ]


class HiringPeopleTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        now = utc_now()
        self.conn.execute(
            "INSERT INTO brands (id, name, created_at, updated_at) VALUES ('brand-1', 'Example', ?, ?)",
            (now, now),
        )

    def tearDown(self):
        self.conn.close()

    def test_manual_profile_is_created_as_monitored_focus_person(self):
        profile = create_employee(
            LinkedInProfileIn(
                brand_id="brand-1",
                profile_url="https://www.linkedin.com/in/ada-example/",
                name="Ada Example",
                title="AI Director",
                notes="重点关注 AI 团队",
            ),
            self.conn,
        )

        self.assertTrue(profile["monitor"])
        self.assertEqual(profile["source_type"], "manual")
        self.assertEqual(profile["snapshot_count"], 0)

        updated = update_employee(
            profile["id"], LinkedInProfileUpdate(monitor=False), self.conn
        )
        self.assertFalse(updated["monitor"])

    def test_rejects_non_linkedin_profile_url(self):
        with self.assertRaises(HTTPException) as ctx:
            create_employee(
                LinkedInProfileIn(
                    brand_id="brand-1",
                    profile_url="https://example.com/ada",
                ),
                self.conn,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_collection_records_profile_change_and_preserves_it_on_same_day_rerun(self):
        profile = create_employee(
            LinkedInProfileIn(
                brand_id="brand-1",
                profile_url="https://www.linkedin.com/in/ada-example/",
                name="Ada Example",
                title="AI Director",
            ),
            self.conn,
        )

        with patch(
            "server.connectors.hiring.runner.pick_people_provider",
            return_value=_FakePeopleProvider(),
        ):
            first = run_linkedin_people_collection(self.conn, {"id": "brand-1"})
            second = run_linkedin_people_collection(self.conn, {"id": "brand-1"})

        self.assertEqual(first["profile_changes"], 1)
        self.assertEqual(second["profile_changes"], 0)
        history = employee_history(profile["id"], self.conn)
        self.assertEqual(len(history["snapshots"]), 1)
        self.assertTrue(history["snapshots"][0]["changes"])
        self.assertEqual(history["activities"][0]["activity_type"], "profile_change")

    def test_import_company_people_then_bulk_select_focus_profiles(self):
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO links (id, brand_id, dimension, channel, platform, url, canonical_url,
                cadence, status, config_json, created_at, updated_at)
            VALUES ('people-link', 'brand-1', 'hiring', 'linkedin_people', 'linkedin_people',
                'https://www.linkedin.com/company/example/people/',
                'https://www.linkedin.com/company/example/people', 'daily', 'active', '{}', ?, ?)
            """,
            (now, now),
        )
        manual = create_employee(
            LinkedInProfileIn(
                brand_id="brand-1",
                profile_url="https://www.linkedin.com/in/manual-focus/",
                name="Manual Focus",
            ),
            self.conn,
        )

        with patch(
            "server.connectors.hiring.runner.pick_people_provider",
            return_value=_FakeCompanyPeopleProvider(),
        ):
            summary = discover_linkedin_people_candidates(self.conn, {"id": "brand-1"})
            imported = import_company_employees("brand-1", conn=self.conn)

        self.assertEqual(summary["profiles"], 2)
        self.assertEqual(len(imported["candidates"]), 2)
        self.assertTrue(all(not candidate["monitor"] for candidate in imported["candidates"]))

        selected_id = imported["candidates"][0]["id"]
        result = update_employee_monitor_selection(
            LinkedInMonitorSelectionIn(brand_id="brand-1", profile_ids=[selected_id]),
            self.conn,
        )
        self.assertEqual(result["selected"], 1)
        selected = self.conn.execute(
            "SELECT monitor FROM linkedin_profiles WHERE id = ?", (selected_id,)
        ).fetchone()
        self.assertEqual(selected["monitor"], 1)
        manual_row = self.conn.execute(
            "SELECT monitor FROM linkedin_profiles WHERE id = ?", (manual["id"],)
        ).fetchone()
        self.assertEqual(manual_row["monitor"], 1)

    def test_linkedin_profile_parser_uses_page_metadata(self):
        page = RenderResult(
            text="Ada Example\nVP of AI",
            title="Ada Example - VP of AI | LinkedIn",
            meta={"og:title": "Ada Example - VP of AI | LinkedIn"},
            status="ok",
            method="http",
            final_url="https://www.linkedin.com/in/ada-example/",
        )
        with patch("server.connectors.hiring.linkedin.render", return_value=page):
            snapshot = LinkedInPeopleProvider().fetch_profile(
                self.conn, {"profile_url": page.final_url}
            )

        self.assertEqual(snapshot.name, "Ada Example")
        self.assertEqual(snapshot.title, "VP of AI")
        self.assertEqual(snapshot.status, "ok")

    def test_linkedin_people_parser_imports_anchor_name(self):
        page = RenderResult(
            html="""
            <ul><li><a href="/in/ada-example/" aria-label="View Ada Example's profile">
              <img alt="Ada Example" src="avatar.jpg"><span>Ada Example</span>
            </a></li></ul>
            """,
            anchors=["/in/ada-example/"],
            status="ok",
            final_url="https://www.linkedin.com/company/example/people/",
        )
        with patch("server.connectors.hiring.linkedin.render", return_value=page):
            refs = LinkedInPeopleProvider().expand_profiles(
                self.conn, {"url": page.final_url}
            )

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "Ada Example")


if __name__ == "__main__":
    unittest.main()
