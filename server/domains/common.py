"""Shared dependencies, serializers, and the record query builder."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

from fastapi import HTTPException

from ..db import db as _db
from ..records import record_to_dict


def get_conn() -> Iterator[sqlite3.Connection]:
    with _db() as conn:
        yield conn


def fetch_brand(conn: sqlite3.Connection, brand_id: str) -> dict:
    row = conn.execute("SELECT * FROM brands WHERE id = ?", (brand_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Brand not found")
    return dict(row)


def require(value, message: str):
    if not value:
        raise HTTPException(status_code=400, detail=message)
    return value


# --------------------------------------------------------------- record query
RECORD_FIELD_MAP = {
    "brand_id": "brand_id",
    "product_id": "product_id",
    "link_id": "link_id",
    "source_id": "source_id",
    "data_type": "data_type",
    "dimension": "dimension",
    "channel": "channel",
    "platform": "platform",
    "sentiment": "sentiment",
    "intent": "intent",
    "region": "region",
}


def build_record_query(filters: dict) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    for key, column in RECORD_FIELD_MAP.items():
        value = filters.get(key)
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)

    days = filters.get("days")
    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    if start_date:
        clauses.append("substr(occurred_at, 1, 10) >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("substr(occurred_at, 1, 10) <= ?")
        params.append(end_date)
    if days and not start_date and not end_date:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(int(days) - 1, 0))).date().isoformat()
        clauses.append("substr(occurred_at, 1, 10) >= ?")
        params.append(cutoff)

    q = filters.get("q")
    if q:
        like = f"%{q}%"
        clauses.append("(body LIKE ? OR title LIKE ? OR author LIKE ? OR platform LIKE ?)")
        params.extend([like, like, like, like])

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def query_records(conn: sqlite3.Connection, filters: dict, limit: int = 200) -> list[dict]:
    where, params = build_record_query(filters)
    rows = conn.execute(
        f"SELECT * FROM records{where} ORDER BY occurred_at DESC LIMIT ?",
        (*params, max(1, min(limit, 1000))),
    ).fetchall()
    return [record_to_dict(row) for row in rows]


def trend_by_day(records: list[dict], days: int = 14) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    buckets = {(today - timedelta(days=offset)).isoformat(): {"date": (today - timedelta(days=offset)).isoformat(), "total": 0, "negative": 0} for offset in range(days)}
    for record in records:
        day = (record.get("occurred_at") or "")[:10]
        if day in buckets:
            buckets[day]["total"] += 1
            if record.get("sentiment") == "negative":
                buckets[day]["negative"] += 1
    return sorted(buckets.values(), key=lambda item: item["date"])


# --------------------------------------------------------------- time window
def _safe_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def resolve_window(days: int | None = None, start_date: str | None = None, end_date: str | None = None) -> tuple[str, str]:
    """Normalize (days | start_date | end_date) into a concrete [start, end] ISO date pair.

    Explicit start/end win; otherwise fall back to a trailing `days` window (default 30).
    """
    today = datetime.now(timezone.utc).date()
    end = _safe_date(end_date) or today
    start = _safe_date(start_date)
    if start is None:
        span = int(days) if days else 30
        start = end - timedelta(days=max(span - 1, 0))
    if start > end:
        start, end = end, start
    return start.isoformat(), end.isoformat()


def build_trend(records: list[dict], start: str, end: str, weekly_threshold_days: int = 70) -> list[dict]:
    """Zero-filled sentiment trend across [start, end]; auto-switch to weekly buckets for long spans."""
    start_d = _safe_date(start) or (datetime.now(timezone.utc).date() - timedelta(days=29))
    end_d = _safe_date(end) or datetime.now(timezone.utc).date()
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    weekly = (end_d - start_d).days + 1 > weekly_threshold_days

    def bucket_key(d: date) -> date:
        return d - timedelta(days=d.weekday()) if weekly else d

    buckets: dict[str, dict] = {}
    cursor = bucket_key(start_d)
    step = timedelta(days=7 if weekly else 1)
    while cursor <= end_d:
        key = cursor.isoformat()
        buckets[key] = {"date": key, "total": 0, "negative": 0, "estimated_reach": 0}
        cursor += step

    for record in records:
        day = _safe_date((record.get("occurred_at") or "")[:10])
        if not day:
            continue
        key = bucket_key(day).isoformat()
        bucket = buckets.get(key)
        if bucket:
            bucket["total"] += 1
            if record.get("sentiment") == "negative":
                bucket["negative"] += 1
            metrics = record.get("metrics") or {}
            try:
                bucket["estimated_reach"] += int(metrics.get("monthly_traffic") or metrics.get("estimated_reach") or 0)
            except (TypeError, ValueError):
                pass
    return sorted(buckets.values(), key=lambda item: item["date"])
