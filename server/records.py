"""Unified record read/write helpers shared by collectors and domains."""
from __future__ import annotations

import json
import hashlib
import sqlite3

from .nlp import analyze_text
from .util import clean_text, new_id, utc_now


def upsert_ad_observation(conn: sqlite3.Connection, payload: dict) -> dict | None:
    """Persist the ad lifecycle separately from de-duplicated content records."""
    if payload.get("data_type") != "ad":
        return None
    brand_id = payload.get("brand_id")
    source_id = payload.get("source_id") or "meta_ads"
    external_id = payload.get("external_id")
    if not brand_id or not external_id:
        return None
    metrics = payload.get("metrics") or {}
    raw = payload.get("raw") or {}
    body = clean_text(payload.get("body"))
    creative_hash = hashlib.sha256(body.encode("utf-8")).hexdigest() if body else ""
    observed_at = payload.get("observed_at") or utc_now()
    observed_date = str(observed_at)[:10]
    started_at = payload.get("started_at") or payload.get("occurred_at")
    stopped_at = payload.get("stopped_at") or raw.get("ad_delivery_stop_time")
    status = payload.get("active_status") or metrics.get("active_status") or ("inactive" if stopped_at else "active")
    platforms = metrics.get("publisher_platforms") or []
    link_urls = metrics.get("ad_creative_link_urls") or []
    row = conn.execute(
        "SELECT * FROM ad_entities WHERE brand_id = ? AND source_id = ? AND ad_external_id = ?",
        (brand_id, source_id, external_id),
    ).fetchone()
    now = utc_now()
    events: list[tuple[str, dict]] = []
    if row is None:
        entity_id = new_id()
        conn.execute(
            """INSERT INTO ad_entities
            (id, brand_id, source_id, ad_external_id, platform, page_id, page_name,
             snapshot_url, creative_body, creative_hash, started_at, stopped_at,
             active_status, publisher_platforms_json, link_urls_json, raw_json,
             first_seen_at, last_seen_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entity_id, brand_id, source_id, external_id, payload.get("platform") or "meta",
             raw.get("page_id"), payload.get("author") or raw.get("page_name"), payload.get("url"),
             body, creative_hash, started_at, stopped_at, status,
             json.dumps(platforms, ensure_ascii=False), json.dumps(link_urls, ensure_ascii=False),
             json.dumps(raw, ensure_ascii=False), observed_at, observed_at, now, now),
        )
        events.append(("discovered", {"active_status": status}))
        entity = conn.execute("SELECT * FROM ad_entities WHERE id = ?", (entity_id,)).fetchone()
    else:
        entity_id = row["id"]
        if row["creative_hash"] and creative_hash and row["creative_hash"] != creative_hash:
            events.append(("creative_changed", {"previous_hash": row["creative_hash"], "creative_hash": creative_hash}))
        if row["active_status"] != status:
            events.append(("status_changed", {"from": row["active_status"], "to": status}))
        if row["started_at"] != started_at and started_at:
            events.append(("start_time_changed", {"from": row["started_at"], "to": started_at}))
        conn.execute(
            """UPDATE ad_entities SET page_id = ?, page_name = ?, snapshot_url = ?,
               creative_body = ?, creative_hash = ?, started_at = COALESCE(?, started_at),
               stopped_at = ?, active_status = ?, publisher_platforms_json = ?,
               link_urls_json = ?, raw_json = ?, last_seen_at = ?, updated_at = ? WHERE id = ?""",
            (raw.get("page_id"), payload.get("author") or raw.get("page_name"), payload.get("url"),
             body, creative_hash, started_at, stopped_at, status,
             json.dumps(platforms, ensure_ascii=False), json.dumps(link_urls, ensure_ascii=False),
             json.dumps(raw, ensure_ascii=False), observed_at, now, entity_id),
        )
    conn.execute(
        """INSERT OR IGNORE INTO ad_snapshots
        (id, entity_id, brand_id, observed_at, observed_date, active_status, started_at,
         stopped_at, creative_body, creative_hash, publisher_platforms_json, metrics_json,
         raw_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (new_id(), entity_id, brand_id, observed_at, observed_date, status, started_at,
         stopped_at, body, creative_hash, json.dumps(platforms, ensure_ascii=False),
         json.dumps(metrics, ensure_ascii=False), json.dumps(raw, ensure_ascii=False), now),
    )
    for event_type, details in events:
        conn.execute(
            "INSERT INTO ad_events (id, entity_id, brand_id, event_type, event_at, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id(), entity_id, brand_id, event_type, observed_at, json.dumps(details, ensure_ascii=False), now),
        )
    return {"entity_id": entity_id, "events": [kind for kind, _ in events]}

RECORD_TEXT_KEYS = ("body", "text", "content", "comment", "review", "description")


def _resolve_body(payload: dict) -> str:
    for key in RECORD_TEXT_KEYS:
        value = clean_text(payload.get(key))
        if value:
            return value
    return clean_text(payload.get("title"))


def build_record(payload: dict) -> dict:
    body = _resolve_body(payload)
    if not body:
        raise ValueError("Record requires text content")
    analysis = analyze_text(f"{payload.get('title', '')} {body}")
    return {
        "id": payload.get("id") or new_id(),
        "source_id": payload.get("source_id") or "manual_csv",
        "brand_id": payload.get("brand_id"),
        "product_id": payload.get("product_id"),
        "link_id": payload.get("link_id"),
        "external_id": payload.get("external_id"),
        "data_type": payload.get("data_type") or "user_voice",
        "dimension": payload.get("dimension"),
        "channel": payload.get("channel"),
        "platform": clean_text(payload.get("platform")),
        "title": clean_text(payload.get("title")),
        "author": clean_text(payload.get("author")),
        "body": body,
        "url": clean_text(payload.get("url")),
        "region": clean_text(payload.get("region")),
        "language": clean_text(payload.get("language")),
        "occurred_at": payload.get("occurred_at") or utc_now(),
        "sentiment": payload.get("sentiment") or analysis["sentiment"],
        "sentiment_score": payload.get("sentiment_score")
        if payload.get("sentiment_score") is not None
        else analysis["sentiment_score"],
        "intent": payload.get("intent") or analysis["intent"],
        "topics_json": json.dumps(payload.get("topics") or analysis["topics"], ensure_ascii=False),
        "metrics_json": json.dumps(payload.get("metrics") or {}, ensure_ascii=False),
        "raw_json": json.dumps(payload.get("raw") or {}, ensure_ascii=False),
        "created_at": utc_now(),
    }


_COLUMNS = (
    "id", "source_id", "brand_id", "product_id", "link_id", "external_id",
    "data_type", "dimension", "channel", "platform", "title", "author", "body",
    "url", "region", "language", "occurred_at", "sentiment", "sentiment_score",
    "intent", "topics_json", "metrics_json", "raw_json", "created_at",
)


def insert_record(conn: sqlite3.Connection, payload: dict) -> dict:
    record = build_record(payload)
    placeholders = ", ".join("?" for _ in _COLUMNS)
    conn.execute(
        f"INSERT INTO records ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
        tuple(record[col] for col in _COLUMNS),
    )
    return record


def insert_record_if_new(conn: sqlite3.Connection, payload: dict) -> dict | None:
    external_id = payload.get("external_id")
    source_id = payload.get("source_id")
    if external_id:
        existing = conn.execute(
            "SELECT id FROM records WHERE source_id = ? AND external_id = ?",
            (source_id, external_id),
        ).fetchone()
        if existing:
            return None
    return insert_record(conn, payload)


def record_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["topics"] = json.loads(item.pop("topics_json") or "[]")
    item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
    item["raw"] = json.loads(item.pop("raw_json") or "{}")
    # Older social records stored the media URL only in the raw provider
    # payload.  Normalize it at read time so the card renderer can recover
    # their cover without requiring a full recollection.
    if not item["metrics"].get("thumbnail_url"):
        for key in ("thumbnail_url", "cover_url", "display_url", "display_uri", "thumbnail_src", "image_url", "media_url"):
            value = item["raw"].get(key)
            if isinstance(value, str) and value.strip():
                item["metrics"]["thumbnail_url"] = value.strip()
                break
    analysis = analyze_text(f"{item.get('title') or ''} {item.get('body') or ''}")
    if item.get("sentiment") == analysis["sentiment"]:
        item["sentiment_explanation"] = analysis["sentiment_explanation"]
    else:
        item["sentiment_explanation"] = {
            "method": "采集源或导入数据",
            "reason": "该情绪标签由采集源或导入数据直接提供，当前记录未包含更详细的判定依据。",
            "positive_terms": [],
            "negative_terms": [],
            "negation_terms": [],
            "evidence": [],
        }
    return item
