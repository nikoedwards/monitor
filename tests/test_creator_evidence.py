from __future__ import annotations

import sqlite3
import unittest

from server.connectors.creators.evidence import (
    ANALYSIS_NOTE,
    build_collection_evidence,
    enrich_creator_record_evidence,
    parse_transcript_payload,
    transcript_matches,
)
from server.db import SCHEMA
from server.domains.common import query_records
from server.records import insert_record


class CreatorCollectionEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.brand = {"id": "brand-1", "name": "PLAUD", "monitoring_keywords_json": '["NotePin", "PLAUD One"]'}

    def test_title_match_is_explicit_and_does_not_claim_full_video_coverage(self) -> None:
        evidence = build_collection_evidence(title="PLAUD One review", body="A short description without the brand phrase.", brand=self.brand, raw={"query": "PLAUD"})
        self.assertEqual("title", evidence["scope"])
        self.assertEqual("title", evidence["matched_in"])
        self.assertEqual("PLAUD One", evidence["matched_text"])
        self.assertEqual("brand_keyword", evidence["evidence_type"])
        self.assertIn("未分析视频画面", evidence["analysis_note"])
        self.assertIn(ANALYSIS_NOTE, evidence["reason"])

    def test_description_match_is_distinguished_from_title_match(self) -> None:
        evidence = build_collection_evidence(title="A hands-on review", body="I used PLAUD NotePin during the trip.", brand=self.brand, raw={"query": "PLAUD"})
        self.assertEqual("description", evidence["scope"])
        self.assertEqual("body", evidence["matched_in"])
        self.assertEqual("NotePin", evidence["matched_text"])

    def test_product_match_is_kept_as_separate_evidence(self) -> None:
        evidence = build_collection_evidence(title="Travel audio setup", body="The PLAUD One is the recorder I keep in my bag.", brand=self.brand, raw={"query": "PLAUD One"}, product_matches=[{"product_id": "product-1", "product_name": "PLAUD One", "match_type": "product_name", "confidence": 0.96, "evidence": [{"signal": "PLAUD One", "kind": "product_name"}]}])
        self.assertEqual("brand_and_product_keyword", evidence["evidence_type"])
        self.assertEqual("description", evidence["scope"])
        self.assertEqual("body", evidence["product_matches"][0]["matched_in"])

    def test_enrichment_keeps_flat_aliases_for_old_clients(self) -> None:
        record = enrich_creator_record_evidence({"title": "PLAUD field test", "body": "A field test.", "raw": {"query": "PLAUD", "matched_queries": ["PLAUD"]}}, brand=self.brand)
        self.assertEqual("title", record["raw"]["matched_in"])
        self.assertEqual(["PLAUD"], record["raw"]["matched_queries"])
        self.assertEqual("brand_keyword", record["raw"]["evidence_type"])

    def test_query_records_derives_evidence_for_legacy_creator_rows_without_mutation(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO brands (id, name, monitoring_keywords_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)", ("brand-1", "PLAUD", '["NotePin"]', "2026-09-19", "2026-09-19"))
        insert_record(conn, {"id": "legacy-1", "source_id": "youtube_search", "brand_id": "brand-1", "external_id": "legacy-1", "data_type": "creator_post", "dimension": "marketing", "channel": "creators", "platform": "youtube", "title": "A review", "body": "The PLAUD NotePin appears in the description.", "raw": {"query": "PLAUD"}, "occurred_at": "2026-09-19T01:00:00+00:00"})
        records = query_records(conn, {"brand_id": "brand-1", "dimension": "marketing", "channel": "creators", "platform": "youtube", "start_date": "2026-09-18", "end_date": "2026-09-20"}, limit=10)
        self.assertEqual(1, len(records))
        self.assertEqual("description", records[0]["raw"]["scope"])
        self.assertEqual("NotePin", records[0]["raw"]["matched_text"])
        stored = conn.execute("SELECT raw_json FROM records WHERE id = 'legacy-1'").fetchone()[0]
        self.assertNotIn("collection_evidence", stored)
        conn.close()

    def test_transcript_parser_returns_time_bounded_brand_matches(self) -> None:
        cues = parse_transcript_payload("""WEBVTT\n\n00:00:01.000 --> 00:00:04.500\nWelcome back\n\n00:02:10.000 --> 00:02:14.250\nThis PLAUD One is in my travel kit\n""", source="automatic_caption")
        matches = transcript_matches(cues, ["PLAUD", "PLAUD One"])
        self.assertEqual(2, len(cues))
        self.assertEqual(1, len(matches))
        self.assertEqual(130.0, matches[0]["start"])
        self.assertEqual(134.25, matches[0]["end"])
        self.assertEqual("PLAUD One", matches[0]["matched_text"])

    def test_transcript_evidence_scope_exposes_segment_without_claiming_whole_video(self) -> None:
        evidence = build_collection_evidence(title="Travel kit review", body="A compact setup.", brand=self.brand, raw={"query": "PLAUD", "duration_seconds": 900}, transcript_matches_data=[{"start": 130.0, "end": 134.25, "text": "This PLAUD One is in my travel kit", "source": "automatic_caption", "matched_query": "PLAUD One", "matched_text": "PLAUD One"}])
        self.assertEqual("transcript", evidence["scope"])
        self.assertEqual("transcript", evidence["matched_in"])
        self.assertEqual(130.0, evidence["transcript_matches"][0]["start"])
        self.assertIn("字幕可能不完整", evidence["analysis_note"])

    def test_unavailable_transcript_is_explicitly_marked_as_unanalysed(self) -> None:
        evidence = build_collection_evidence(title="PLAUD overview", body="", brand=self.brand, raw={"transcript_status": "unavailable"})
        self.assertEqual("unavailable", evidence["transcript_status"])
        self.assertEqual([], evidence["transcript_matches"])
        self.assertIn("未找到可用公开字幕", evidence["reason"])


if __name__ == "__main__":
    unittest.main()
