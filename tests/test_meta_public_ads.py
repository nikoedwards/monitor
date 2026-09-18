import json
import re

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
    assert payload["metrics"]["ad_creative_link_urls"] == ["https://example.com/product"]
