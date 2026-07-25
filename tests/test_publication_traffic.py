import sqlite3
import unittest
from unittest.mock import patch

from server.connectors.publications import (
    _fetch_tranco_rank,
    _traffic_range_for_rank,
    enrich_publication,
)
from server.fetchers import FetchError


PUBLICATIONS_SCHEMA = """
CREATE TABLE publications (
  domain TEXT PRIMARY KEY,
  name TEXT,
  icon_url TEXT,
  est_monthly_traffic INTEGER NOT NULL DEFAULT 0,
  traffic_lower INTEGER NOT NULL DEFAULT 0,
  traffic_upper INTEGER NOT NULL DEFAULT 0,
  popularity_rank INTEGER,
  traffic_confidence TEXT NOT NULL DEFAULT 'low',
  traffic_as_of TEXT,
  authority INTEGER NOT NULL DEFAULT 0,
  tier TEXT,
  country TEXT,
  language TEXT,
  source TEXT NOT NULL DEFAULT 'heuristic',
  updated_at TEXT NOT NULL
)
"""


def publication_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(PUBLICATIONS_SCHEMA)
    return conn


class PublicationTrafficTests(unittest.TestCase):
    def test_tranco_parser_uses_latest_rank(self):
        payload = {
            "ranks": [
                {"date": "2026-07-23", "rank": 640},
                {"date": "2026-07-24", "rank": 626},
            ]
        }
        with patch("server.connectors.publications.fetch_json", return_value=payload):
            result = _fetch_tranco_rank("cnet.com")
        self.assertEqual(626, result["rank"])
        self.assertEqual("2026-07-24", result["as_of"])

    def test_tranco_lookup_falls_back_to_registrable_domain(self):
        def response(url, **_kwargs):
            if "finance.biggo.com" in url:
                return {"ranks": []}
            if "biggo.com" in url:
                return {"ranks": [{"date": "2026-07-24", "rank": 12_345}]}
            raise AssertionError(url)

        with patch("server.connectors.publications.fetch_json", side_effect=response) as mocked:
            result = _fetch_tranco_rank("finance.biggo.com")
        self.assertEqual("biggo.com", result["ranked_domain"])
        self.assertEqual(2, mocked.call_count)

    def test_rank_model_returns_broad_monotonic_ranges(self):
        cnet_lower, cnet_midpoint, cnet_upper = _traffic_range_for_rank(626)
        tail_lower, tail_midpoint, tail_upper = _traffic_range_for_rank(3_240_310)
        self.assertLess(cnet_lower, cnet_midpoint)
        self.assertLess(cnet_midpoint, cnet_upper)
        self.assertLess(tail_lower, tail_midpoint)
        self.assertLess(tail_midpoint, tail_upper)
        self.assertGreater(cnet_midpoint, 10_000_000)
        self.assertLess(tail_midpoint, 100_000)
        self.assertGreater(cnet_midpoint, tail_midpoint)

    def test_legacy_fixed_heuristic_refreshes_immediately(self):
        conn = publication_db()
        conn.execute(
            """INSERT INTO publications
            (domain, name, icon_url, est_monthly_traffic, authority, tier, country,
             language, source, updated_at)
            VALUES ('example.com', 'Example', '', 80000, 40, 'tier_4', '', '',
                    'heuristic', '2026-07-25T00:00:00+00:00')"""
        )
        with patch(
            "server.connectors.publications._fetch_tranco_rank",
            return_value={"rank": 50_000, "as_of": "2026-07-24", "ranked_domain": "example.com"},
        ):
            result = enrich_publication(conn, "Example", "example.com")
        self.assertEqual("tranco_model", result["source"])
        self.assertNotEqual(80_000, result["est_monthly_traffic"])
        self.assertEqual(50_000, result["popularity_rank"])

    def test_network_failure_returns_unknown_instead_of_fake_traffic(self):
        conn = publication_db()
        with patch("server.connectors.publications.fetch_json", side_effect=FetchError("offline")):
            result = enrich_publication(conn, "Small Outlet", "small-outlet.example")
        self.assertEqual(0, result["est_monthly_traffic"])
        self.assertEqual("low", result["traffic_confidence"])
        self.assertEqual("unavailable", result["source"])


if __name__ == "__main__":
    unittest.main()
