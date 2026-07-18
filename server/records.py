"""Unified record read/write helpers shared by collectors and domains."""
from __future__ import annotations

import json
import sqlite3

from .nlp import analyze_text
from .util import clean_text, new_id, utc_now

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
