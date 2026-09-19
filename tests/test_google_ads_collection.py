"""Offline tests for the public Google Ads Transparency collector.

The collector talks to public Google RPC endpoints in production.  These tests
stub those boundaries and exercise the response decoding and payload shaping
without making network requests.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

from server.connectors import collectors
from server.db import SCHEMA
from server.records import cleanup_google_ad_mismatches, insert_record_if_new, upsert_ad_observation


def _creative_item(
    *,
    advertiser_id: str = "AR123",
    creative_id: str = "CR456",
    advertiser_name: str = "PLAUD LLC",
    start_seconds: str = "1700000000",
    last_seen_seconds: str = "1700003600",
    preview_url: str = "https://preview.test/creative.js",
    image_url: str | None = None,
) -> dict:
    preview: dict = {"1": {"4": preview_url}}
    if image_url:
        preview["3"] = {"2": f'<img src="{image_url}" height="160">'}
    return {
        "1": advertiser_id,
        "2": creative_id,
        "3": preview,
        "4": 2,
        "6": {"1": start_seconds, "2": 123000000},
        "7": {"1": last_seen_seconds, "2": 456000000},
        "12": advertiser_name,
    }


def test_google_timestamp_decodes_wire_shape_and_rejects_invalid_values() -> None:
    assert collectors._google_timestamp({"1": "1700000000", "2": 123}) == "2023-11-14T22:13:20+00:00"
    assert collectors._google_timestamp({"seconds": "1700000000", "nanos": 0}) == "2023-11-14T22:13:20+00:00"
    assert collectors._google_timestamp_seconds({"1": "1700000000"}) == 1700000000.0
    assert collectors._google_timestamp_seconds({"seconds": "1700000000", "nanos": 0}) == 1700000000.0
    assert collectors._google_timestamp(None) is None
    assert collectors._google_timestamp({"1": "not-a-number"}) is None
    assert collectors._google_timestamp_seconds("not-a-number") is None


def test_google_js_unescape_decodes_hex_unicode_and_url_escapes() -> None:
    value = r"Hello\x26\u4e16\u754c\=ok\/path\'s\\slash"
    assert collectors._google_js_unescape(value) == "Hello&世界=ok/path's\\slash"


def test_google_name_matching_accepts_brand_prefix_and_rejects_other_brands() -> None:
    assert collectors._google_name_matches_query("PLAUD AI", "PLAUD")
    assert collectors._google_name_matches_query("PLAUD LLC", "PLAUD AI", "PLAUD")
    assert collectors._google_name_matches_query("Plaud", "unrelated keyword", "PLAUD")
    assert not collectors._google_name_matches_query("Pocket AI", "PLAUD")
    assert not collectors._google_name_matches_query("", "PLAUD")


def test_google_advertiser_suggestions_decodes_nested_public_rpc_response() -> None:
    response = {
        "1": [
            {"1": {"1": "Plaud LLC", "2": "AR123", "3": "US"}},
            {"1": {"1": "PLAUD Labs", "2": "AR999"}},
            {"1": {"1": "Missing id"}},
            {"2": "not-an-advertiser"},
        ]
    }
    with patch.object(collectors, "fetch_form_json", return_value=response) as fetch:
        suggestions = collectors._google_advertiser_suggestions("PLAUD")

    assert suggestions == [
        {"id": "AR123", "name": "Plaud LLC", "country": "US", "raw": {"1": "Plaud LLC", "2": "AR123", "3": "US"}},
        {"id": "AR999", "name": "PLAUD Labs", "country": "US", "raw": {"1": "PLAUD Labs", "2": "AR999"}},
    ]
    url, form = fetch.call_args.args[:2]
    assert url.endswith("/SearchService/SearchSuggestions?authuser=")
    request = json.loads(form["f.req"])
    assert request["1"] == "PLAUD"
    assert request["4"] == [2840]


def test_google_search_creatives_builds_region_and_advertiser_filter() -> None:
    response = {"1": [_creative_item(image_url="https://img.test/ad.jpg")], "2": "cursor"}
    with patch.object(collectors, "fetch_form_json", return_value=response) as fetch:
        result = collectors._google_search_creatives(["AR123", "AR999"], offset=40, page_token="previous-page")

    assert result == response
    url, form = fetch.call_args.args[:2]
    assert url.endswith("/SearchService/SearchCreatives?authuser=")
    request = json.loads(form["f.req"])
    assert request["2"] == 40
    assert request["3"]["8"] == [2840]
    assert request["3"]["13"]["1"] == ["AR123", "AR999"]
    assert request["7"] == {"1": 1, "2": 40, "3": 2840}
    assert request["4"] == "previous-page"


def test_google_preview_fields_extracts_copy_urls_and_media_and_caches_result() -> None:
    # Keep a generic renderer fragment first: the parser should scope copy
    # fields to the final adData object instead of returning this description.
    script = r"""
      var generic = {description: 'renderer helper text'};
      google_template_data = {adData: [{
        headline: 'Trusted by 2+ Million Users',
        longHeadline: 'AI note taking for every meeting',
        description: 'Transcription\x26summaries in seconds',
        destination_url: 'https:\/\/plaud.ai\/products?src=google\x26x=1',
        final_url: 'https:\/\/plaud.ai\/final',
        visible_url: 'plaud.ai/products',
        thumbnail: 'https:\/\/img.test\/thumb.jpg',
        highResThumbnail: 'https:\/\/img.test\/thumb@2x.jpg',
        video: 'https:\/\/video.test\/ad.mp4'
      }]};
    """
    cache: dict[str, dict] = {}
    with patch.object(collectors, "fetch_bytes", return_value=script.encode("utf-8")) as fetch:
        first = collectors._google_preview_fields("https://preview.test/creative.js", cache)
        second = collectors._google_preview_fields("https://preview.test/creative.js", cache)

    assert first == second
    assert first["headline"] == "Trusted by 2+ Million Users"
    assert first["long_headline"] == "AI note taking for every meeting"
    assert first["description"] == "Transcription&summaries in seconds"
    assert first["destination_url"] == "https://plaud.ai/products?src=google&x=1"
    assert first["final_url"] == "https://plaud.ai/final"
    assert first["visible_url"] == "plaud.ai/products"
    assert first["thumbnail_url"] == "https://img.test/thumb.jpg"
    assert first["high_res_thumbnail_url"] == "https://img.test/thumb@2x.jpg"
    assert first["video_url"] == "https://video.test/ad.mp4"
    fetch.assert_called_once()


def test_google_public_payload_maps_lifecycle_copy_media_and_landing_url() -> None:
    item = _creative_item(image_url=None)
    preview_url = item["3"]["1"]["4"]
    preview_fields = {
        "headline": "Trusted by 2+ Million Users",
        "long_headline": "AI note taking for every meeting",
        "description": "Transcription and summaries in seconds",
        "destination_url": "https://plaud.ai/products?src=google",
        "thumbnail_url": "https://img.test/thumb.jpg",
        "high_res_thumbnail_url": "https://img.test/thumb@2x.jpg",
        "video_url": "https://video.test/ad.mp4",
    }
    now = datetime.fromtimestamp(1700003600 + 3600, tz=timezone.utc)
    payload = collectors._google_public_payload(
        item,
        {"id": "brand-1", "name": "PLAUD"},
        "PLAUD",
        preview_cache={preview_url: preview_fields},
        now=now,
    )

    assert payload is not None
    assert payload["source_id"] == "google_ads"
    assert payload["external_id"] == "brand-1:AR123:CR456"
    assert payload["title"] == "PLAUD LLC"
    assert payload["body"] == "Trusted by 2+ Million Users · AI note taking for every meeting · Transcription and summaries in seconds"
    assert payload["started_at"] == "2023-11-14T22:13:20+00:00"
    assert payload["stopped_at"] is None
    assert payload["active_status"] == "active"
    assert payload["url"].endswith("/advertiser/AR123/creative/CR456?region=US")
    assert payload["metrics"]["thumbnail_url"] == "https://img.test/thumb@2x.jpg"
    assert payload["metrics"]["video_url"] == "https://video.test/ad.mp4"
    assert payload["metrics"]["ad_landing_url"] == "https://plaud.ai/products?src=google"
    assert payload["metrics"]["ad_creative_link_urls"] == ["https://plaud.ai/products?src=google"]
    assert payload["raw"]["thumbnail_url"] == "https://img.test/thumb@2x.jpg"
    assert payload["raw"]["video_url"] == "https://video.test/ad.mp4"
    assert payload["raw"]["landing_url"] == "https://plaud.ai/products?src=google"
    assert payload["raw"]["page_name"] == "PLAUD LLC"


def test_google_public_payload_marks_old_creative_inactive_and_handles_image_html() -> None:
    item = _creative_item(
        start_seconds="1690000000",
        last_seen_seconds="1700000000",
        image_url="https://img.test/archive.jpg",
    )
    now = datetime.fromtimestamp(1700000000 + 3 * 86400 + 1, tz=timezone.utc)
    payload = collectors._google_public_payload(item, {"id": "brand-1"}, "PLAUD", now=now)

    assert payload is not None
    assert payload["active_status"] == "inactive"
    assert payload["stopped_at"] == "2023-11-14T22:13:20+00:00"
    assert payload["metrics"]["thumbnail_url"] == "https://img.test/archive.jpg"
    assert payload["metrics"]["ad_landing_url"] is None
    assert collectors._google_html_image('<img class="creative" src="https://img.test/archive.jpg">') == "https://img.test/archive.jpg"
    assert collectors._google_html_image("<div>no image</div>") is None


def test_collect_google_public_ads_dedupes_queries_advertisers_and_creatives_without_network() -> None:
    item_a = _creative_item(advertiser_id="AR123", creative_id="CR1", image_url="https://img.test/1.jpg")
    item_b = _creative_item(advertiser_id="AR123", creative_id="CR2", image_url="https://img.test/2.jpg")
    item_other = _creative_item(advertiser_id="AR999", creative_id="CR3", image_url="https://img.test/3.jpg")
    suggestions = [
        {"id": "AR123", "name": "PLAUD LLC"},
        {"id": "AR123", "name": "Plaud LLC"},
        {"id": "AR999", "name": "PLAUD Labs"},
    ]

    def creatives(ids: list[str], *, offset: int = 0, page_token: str | None = None) -> dict:
        if ids == ["AR123"]:
            return {"1": [item_a, item_b, item_a]}
        return {"1": [item_other]}

    brand = {
        "id": "brand-1",
        "name": "PLAUD",
        "monitoring_keywords_json": json.dumps(["PLAUD"]),
    }
    with (
        patch.object(collectors, "_google_advertiser_suggestions", return_value=suggestions),
        patch.object(collectors, "_google_search_creatives", side_effect=creatives) as search,
    ):
        payloads = collectors._collect_google_public_ads(brand)

    assert [payload["external_id"] for payload in payloads] == [
        "brand-1:AR123:CR1",
        "brand-1:AR123:CR2",
        "brand-1:AR999:CR3",
    ]
    assert search.call_count == 2


def test_collect_google_public_ads_follows_continuation_pages_and_dedupes_items() -> None:
    items = [
        _creative_item(creative_id="CR1", image_url="https://img.test/1.jpg"),
        _creative_item(creative_id="CR2", image_url="https://img.test/2.jpg"),
        _creative_item(creative_id="CR3", image_url="https://img.test/3.jpg"),
        _creative_item(creative_id="CR4", image_url="https://img.test/4.jpg"),
    ]
    calls: list[tuple[int, str | None]] = []

    def paged_search(ids: list[str], *, offset: int = 0, page_token: str | None = None) -> dict:
        assert ids == ["AR123"]
        calls.append((offset, page_token))
        if (offset, page_token) == (0, None):
            return {"1": [items[0], items[1]], "2": "page-1"}
        if (offset, page_token) == (2, "page-1"):
            # CR2 repeats across pages; the collector should retain it once.
            return {"1": [items[1], items[2]], "2": "page-2"}
        if (offset, page_token) == (4, "page-2"):
            return {"1": [items[3]]}
        raise AssertionError(f"unexpected page request: {(offset, page_token)}")

    brand = {"id": "brand-1", "name": "PLAUD", "monitoring_keywords_json": "[]"}
    with (
        patch.object(collectors, "_GOOGLE_ADS_PAGE_SIZE", 2),
        patch.object(collectors, "_GOOGLE_ADS_MAX_PAGES", 3),
        patch.object(collectors, "_google_advertiser_suggestions", return_value=[{"id": "AR123", "name": "PLAUD LLC"}]),
        patch.object(collectors, "_google_search_creatives", side_effect=paged_search),
    ):
        payloads = collectors._collect_google_public_ads(brand)

    assert calls == [(0, None), (2, "page-1"), (4, "page-2")]
    assert [payload["external_id"] for payload in payloads] == [
        "brand-1:AR123:CR1",
        "brand-1:AR123:CR2",
        "brand-1:AR123:CR3",
        "brand-1:AR123:CR4",
    ]


def test_collect_google_public_ads_does_not_use_unmatched_first_suggestion() -> None:
    brand = {"id": "brand-1", "name": "PLAUD", "monitoring_keywords_json": "[]"}
    suggestions = [
        {"id": "AR999", "name": "Pocket AI"},
        {"id": "AR888", "name": "Another advertiser"},
    ]
    with (
        patch.object(collectors, "_google_advertiser_suggestions", return_value=suggestions),
        patch.object(collectors, "_google_search_creatives") as search,
    ):
        payloads = collectors._collect_google_public_ads(brand)

    assert payloads == []
    search.assert_not_called()


def test_google_collection_report_marks_complete_crawl_safe_for_cleanup() -> None:
    item = _creative_item(image_url="https://img.test/1.jpg")
    brand = {"id": "brand-1", "name": "PLAUD", "monitoring_keywords_json": "[]"}
    with (
        patch.object(collectors, "_google_advertiser_suggestions", return_value=[{"id": "AR123", "name": "PLAUD LLC"}]),
        patch.object(collectors, "_google_search_creatives", return_value={"1": [item]}),
        patch.object(collectors, "_google_preview_fields", return_value={}),
    ):
        payloads, report = collectors._collect_google_public_ads_with_report(brand)

    assert len(payloads) == 1
    assert report["matched_advertiser_ids"] == ["AR123"]
    assert report["queries_completed"] == report["query_count"] == 1
    assert report["creative_count"] == 1
    assert report["safe_to_cleanup"] is True


def test_google_collection_report_skips_cleanup_when_suggestions_fail() -> None:
    brand = {"id": "brand-1", "name": "PLAUD", "monitoring_keywords_json": "[]"}
    with patch.object(collectors, "_google_advertiser_suggestions", side_effect=collectors.FetchError("429")):
        payloads, report = collectors._collect_google_public_ads_with_report(brand)

    assert payloads == []
    assert report["suggestion_failed"] is True
    assert report["safe_to_cleanup"] is False


def test_google_collection_report_skips_cleanup_when_creatives_fail() -> None:
    brand = {"id": "brand-1", "name": "PLAUD", "monitoring_keywords_json": "[]"}
    with (
        patch.object(collectors, "_google_advertiser_suggestions", return_value=[{"id": "AR123", "name": "PLAUD LLC"}]),
        patch.object(collectors, "_google_search_creatives", side_effect=collectors.FetchError("network")),
    ):
        payloads, report = collectors._collect_google_public_ads_with_report(brand)

    assert payloads == []
    assert report["creative_failed"] is True
    assert report["safe_to_cleanup"] is False


def test_google_collection_report_allows_cleanup_when_creative_pagination_is_capped() -> None:
    item = _creative_item(image_url="https://img.test/1.jpg")
    brand = {"id": "brand-1", "name": "PLAUD", "monitoring_keywords_json": "[]"}
    with (
        patch.object(collectors, "_GOOGLE_ADS_MAX_PAGES", 1),
        patch.object(collectors, "_google_advertiser_suggestions", return_value=[{"id": "AR123", "name": "PLAUD LLC"}]),
        patch.object(collectors, "_google_search_creatives", return_value={"1": [item], "2": "next-page"}),
        patch.object(collectors, "_google_preview_fields", return_value={}),
    ):
        payloads, report = collectors._collect_google_public_ads_with_report(brand)

    assert len(payloads) == 1
    assert report["pagination_truncated"] is True
    # Pagination limits creative history, but the advertiser ID set is still
    # complete as long as the suggestion query and advertiser cap succeeded.
    assert report["safe_to_cleanup"] is True


def test_google_collection_report_skips_cleanup_when_advertiser_cap_is_reached() -> None:
    item = _creative_item(image_url="https://img.test/1.jpg")
    brand = {"id": "brand-1", "name": "PLAUD", "monitoring_keywords_json": "[]"}
    suggestions = [
        {"id": "AR123", "name": "PLAUD LLC"},
        {"id": "AR456", "name": "PLAUD Labs"},
    ]
    with (
        patch.object(collectors, "_GOOGLE_ADS_MAX_ADVERTISERS", 1),
        patch.object(collectors, "_google_advertiser_suggestions", return_value=suggestions),
        patch.object(collectors, "_google_search_creatives", return_value={"1": [item]}),
        patch.object(collectors, "_google_preview_fields", return_value={}),
    ):
        payloads, report = collectors._collect_google_public_ads_with_report(brand)

    assert len(payloads) == 1
    assert report["advertiser_cap_reached"] is True
    assert report["safe_to_cleanup"] is False


def test_google_collection_report_skips_cleanup_when_no_creatives_are_found() -> None:
    brand = {"id": "brand-1", "name": "PLAUD", "monitoring_keywords_json": "[]"}
    with (
        patch.object(collectors, "_google_advertiser_suggestions", return_value=[{"id": "AR123", "name": "PLAUD LLC"}]),
        patch.object(collectors, "_google_search_creatives", return_value={"1": []}),
    ):
        payloads, report = collectors._collect_google_public_ads_with_report(brand)

    assert payloads == []
    assert report["creative_count"] == 0
    assert report["safe_to_cleanup"] is False


def test_cleanup_google_ad_mismatches_removes_only_explicitly_stale_rows_and_dependencies() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    brand = {"id": "brand-1", "name": "PLAUD"}

    def seed(advertiser_id: str | None, external_id: str, *, raw_json: str | None = None) -> None:
        payload = {
            "source_id": "google_ads",
            "brand_id": brand["id"],
            "external_id": external_id,
            "data_type": "ad",
            "platform": "google",
            "author": "PLAUD LLC",
            "body": "creative",
            "url": "https://adstransparency.google.com/creative/x",
            "occurred_at": "2026-09-19T00:00:00+00:00",
            "raw": {"advertiser_id": advertiser_id} if advertiser_id is not None else {},
        }
        upsert_ad_observation(conn, payload)
        if raw_json is not None:
            conn.execute(
                "UPDATE ad_entities SET raw_json = ? WHERE ad_external_id = ?",
                (raw_json, external_id),
            )
        insert_record_if_new(conn, payload)

    seed("AR_GOOD", "brand-1:AR_GOOD:CR1")
    seed("AR_OLD", "brand-1:AR_OLD:CR2")
    seed(None, "brand-1:UNKNOWN:CR3")
    seed("AR_BAD_JSON", "brand-1:BAD:CR4", raw_json="{bad json")
    meta_payload = {
        "source_id": "meta_ads",
        "brand_id": brand["id"],
        "external_id": "brand-1:META:CR5",
        "data_type": "ad",
        "platform": "meta",
        "author": "Other page",
        "body": "meta creative",
        "url": "https://facebook.com/ads/library/?id=5",
        "occurred_at": "2026-09-19T00:00:00+00:00",
        "raw": {"page_name": "Other page"},
    }
    upsert_ad_observation(conn, meta_payload)
    insert_record_if_new(conn, meta_payload)

    removed = cleanup_google_ad_mismatches(conn, brand, {"AR_GOOD"})

    assert removed == 1
    assert conn.execute("SELECT COUNT(*) FROM ad_entities WHERE ad_external_id = 'brand-1:AR_GOOD:CR1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ad_entities WHERE ad_external_id = 'brand-1:AR_OLD:CR2'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ad_entities WHERE ad_external_id = 'brand-1:UNKNOWN:CR3'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ad_entities WHERE ad_external_id = 'brand-1:BAD:CR4'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ad_snapshots WHERE entity_id NOT IN (SELECT id FROM ad_entities)").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ad_events WHERE entity_id NOT IN (SELECT id FROM ad_entities)").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM records WHERE external_id = 'brand-1:AR_OLD:CR2'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM records WHERE external_id = 'brand-1:AR_GOOD:CR1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ad_entities WHERE source_id = 'meta_ads'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM records WHERE source_id = 'meta_ads'").fetchone()[0] == 1


def test_google_public_payload_rejects_items_without_advertiser_or_creative_id() -> None:
    assert collectors._google_public_payload({}, {"id": "brand-1"}, "PLAUD") is None
    assert collectors._google_public_payload({"1": "AR123"}, {"id": "brand-1"}, "PLAUD") is None


def test_ad_upsert_preserves_google_media_when_a_later_preview_is_incomplete() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    base = {
        "source_id": "google_ads",
        "brand_id": "brand-1",
        "external_id": "brand-1:AR123:CR456",
        "data_type": "ad",
        "platform": "google",
        "author": "PLAUD LLC",
        "body": "A rich creative",
        "url": "https://adstransparency.google.com/creative/CR456",
        "occurred_at": "2026-09-19T00:00:00+00:00",
        "started_at": "2026-09-01T00:00:00+00:00",
        "active_status": "active",
        "metrics": {
            "publisher_platforms": ["google"],
            "ad_creative_link_urls": ["https://plaud.ai/product"],
        },
        "raw": {
            "collection_method": "google_ads_transparency_public_rpc",
            "thumbnail_url": "https://img.test/creative.jpg",
            "landing_url": "https://plaud.ai/product",
            "preview_fields": {"headline": "A rich creative"},
            "page_name": "PLAUD LLC",
        },
    }
    upsert_ad_observation(conn, base)
    later = dict(base)
    later["body"] = "PLAUD LLC"
    later["metrics"] = {"publisher_platforms": ["google"], "ad_creative_link_urls": []}
    later["raw"] = {
        "collection_method": "google_ads_transparency_public_rpc",
        "thumbnail_url": None,
        "landing_url": None,
        "preview_fields": {},
        "page_name": "PLAUD LLC",
    }
    upsert_ad_observation(conn, later)

    row = conn.execute(
        "SELECT creative_body, link_urls_json, raw_json FROM ad_entities WHERE ad_external_id = ?",
        (base["external_id"],),
    ).fetchone()
    assert row["creative_body"] == "A rich creative"
    assert json.loads(row["link_urls_json"]) == ["https://plaud.ai/product"]
    stored_raw = json.loads(row["raw_json"])
    assert stored_raw["thumbnail_url"] == "https://img.test/creative.jpg"
    assert stored_raw["landing_url"] == "https://plaud.ai/product"
    assert stored_raw["preview_fields"]["headline"] == "A rich creative"
