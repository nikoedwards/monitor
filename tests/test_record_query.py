import unittest

from server.domains.common import build_record_query


class RecordQueryTests(unittest.TestCase):
    def test_filters_media_records_by_publication_domain(self):
        where, params = build_record_query({
            "brand_id": "brand-1",
            "channel": "media",
            "publication_domain": "Technobezz.COM",
        })

        self.assertIn("brand_id = ?", where)
        self.assertIn("channel = ?", where)
        self.assertIn("json_extract(metrics_json, '$.publication_domain')", where)
        self.assertEqual(params, ["brand-1", "media", "Technobezz.COM"])

    def test_domain_filter_can_include_legacy_domainless_rows_by_name(self):
        where, params = build_record_query({
            "channel": "media",
            "publication_domain": "cnet.com",
            "publication_name": "CNET",
        })

        self.assertIn("OR", where)
        self.assertIn("platform", where)
        self.assertEqual(params, ["media", "cnet.com", "CNET", "cnet.com"])


if __name__ == "__main__":
    unittest.main()
