import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server.connectors.hiring.base import ProfileSnapshot, RenderResult
from server.connectors.hiring.linkedin import LinkedInPeopleProvider
from server.connectors.hiring.runner import run_linkedin_people_collection
from server.db import SCHEMA
from server.domains.hiring import create_employee, employee_history, update_employee
from server.schemas import LinkedInProfileIn, LinkedInProfileUpdate
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


if __name__ == "__main__":
    unittest.main()
