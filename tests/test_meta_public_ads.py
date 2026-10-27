import json
import re
import sqlite3
from unittest.mock import patch

from server.connectors import collectors
from server.connectors.collectors import _meta_public_ads_from_html, _meta_public_payload


def test_meta_public_ssr_parser_and_payload():
    ad = {
        "ad_archive_id": "123456",
        "is_active": True,
        "page_id": "42",
        "page_name": "Example Brand",
        "start_date": 1_700_000_000,
        "end_date": 1_800_000_000,
        "publisher_platform": ["FACEBOOK", "INSTAGRAM"],
        "snapshot": {
            "page_name": "Example Brand",
            "body": {"text": "A public ad creative"},
            "cards": [{
                "link_url": "https://example.com/product",
                "resized_image_url": "https://example.com/image.jpg",
            }],
        },
    }
    html = '<script type="application/json">' + json.dumps({"nested": [ad]}) + "</script>"
    parsed = _meta_public_ads_from_html(html)
    assert len(parsed) == 1
    assert re.fullmatch(r"\d+", str(parsed[0]["ad_archive_id"]))
    payload = _meta_public_payload(parsed[0], {"id": "brand-1"}, "Example")
    assert payload is not None
    assert payload["external_id"] == "brand-1:123456"
    assert payload["active_status"] == "active"
    assert payload["metrics"]["thumbnail_url"] == "https://example.com/image.jpg"
    assert payload["raw"]["thumbnail_url"] == "https://example.com/image.jpg"
    assert payload["metrics"]["ad_creative_link_urls"] == ["https://example.com/product"]


def test_meta_public_parser_reads_authoritative_count_and_page_scope():
    html = '<script type="application/json">' + json.dumps({
        "ad_library_main": {
            "search_results_connection": {"count": 599, "edges": []},
        },
    }) + "</script>"
    assert collectors._meta_public_ad_count_from_html(html) == 599

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE links (brand_id TEXT, channel TEXT, platform TEXT, url TEXT)")
    conn.execute(
        "INSERT INTO links VALUES (?, 'ads', 'meta_ads', ?)",
        ("brand-1", "https://www.facebook.com/ads/library/?view_all_page_id=110414908721120"),
    )
    assert collectors._meta_page_ids_from_links(conn, "brand-1") == ["110414908721120"]


def test_meta_token_collection_follows_paging_and_deduplicates():
    def ad(ad_id):
        return {
            "id": str(ad_id),
            "page_name": "Example Brand",
            "ad_creative_bodies": [f"Creative {ad_id}"],
            "ad_snapshot_url": f"https://www.facebook.com/ads/library/?id={ad_id}",
            "ad_delivery_start_time": "2026-01-01T00:00:00+0000",
            "publisher_platforms": ["facebook"],
        }

    responses = [
        {"data": [ad(1), ad(2)], "paging": {"next": "https://graph.facebook.com/next"}},
        {"data": [ad(2), ad(3)]},
    ]
    with patch.dict(collectors.CREDENTIALS, {"facebook_access_token": "token"}, clear=False), patch.object(
        collectors, "fetch_json", side_effect=responses
    ) as fetch:
        payloads = collectors.collect_meta_ads(None, {"id": "brand-1", "name": "Example", "monitoring_keywords_json": "[\"Example\"]"})
    assert [item["external_id"] for item in payloads] == ["brand-1:1", "brand-1:2", "brand-1:3"]
    assert fetch.call_count == 2
