"""Cross-dimension overview (single brand) and cross-brand comparison."""
from __future__ import annotations

import sqlite3
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException

from .. import ai
from .common import build_trend, get_conn, query_records, resolve_window

router = APIRouter(prefix="/api", tags=["insights"])


def _brand_snapshot(conn: sqlite3.Connection, brand_id: str | None, start: str, end: str) -> dict:
    records = query_records(conn, {"brand_id": brand_id, "start_date": start, "end_date": end}, limit=2000)
    voc = [r for r in records if r.get("dimension") == "voc"]
    marketing = [r for r in records if r.get("dimension") == "marketing"]
    negative = sum(1 for r in voc if r.get("sentiment") == "negative")
    sales = conn.execute(
        "SELECT COALESCE(SUM(revenue_est),0) AS revenue, COALESCE(SUM(units_est),0) AS units "
        "FROM sales_metrics WHERE (? IS NULL OR brand_id = ?) AND snapshot_date >= ? AND snapshot_date <= ?",
        (brand_id, brand_id, start, end),
    ).fetchone()
    web_changed = conn.execute(
        "SELECT COUNT(*) AS c FROM web_snapshots WHERE (? IS NULL OR brand_id = ?) "
        "AND snapshot_date >= ? AND snapshot_date <= ? "
        "AND (change_score >= 0.15 OR visual_change_score >= 0.025)",
        (brand_id, brand_id, start, end),
    ).fetchone()["c"]
    return {
        "records_total": len(records),
        "voc_total": len(voc),
        "voc_negative": negative,
        "marketing_total": len(marketing),
        "sales_revenue": round(sales["revenue"], 2),
        "sales_units": sales["units"],
        "web_changes": web_changed,
    }


@router.get("/overview")
def overview(
    brand_id: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = resolve_window(days, start_date, end_date)
    records = query_records(conn, {"brand_id": brand_id, "start_date": start, "end_date": end}, limit=2000)
    sentiment = Counter(r.get("sentiment") or "neutral" for r in records)
    topics = Counter()
    for record in records:
        topics.update(record.get("topics") or [])
    return {
        "kpis": _brand_snapshot(conn, brand_id, start, end),
        "trend": build_trend(records, start, end),
        "sentiment": [{"sentiment": k, "total": v} for k, v in sentiment.items()],
        "top_topics": [{"topic": k, "total": v} for k, v in topics.most_common(8)],
        "high_signal": [
            r for r in records if r.get("sentiment") == "negative" or (r.get("metrics") or {}).get("media_tier") in {"tier_1", "tier_2"}
        ][:20],
    }


@router.get("/insights/summary")
def insights_summary(
    brand_id: str | None = None,
    dimension: str | None = None,
    channel: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if not ai.is_configured(conn):
        raise HTTPException(status_code=400, detail="尚未配置大模型 Token，请先在设置中填写。")
    start, end = resolve_window(None, start_date, end_date)
    records = query_records(
        conn,
        {"brand_id": brand_id, "dimension": dimension, "channel": channel, "start_date": start, "end_date": end},
        limit=150,
    )
    if not records:
        return {
            "summary": "所选时间范围与渠道下暂无数据，无法生成总结。",
            "highlights": [], "themes": [], "sentiment": {}, "representative": [],
            "record_count": 0, "range": {"start": start, "end": end},
        }
    try:
        result = ai.summarize_records(conn, records, {"start": start, "end": end, "channel": channel, "dimension": dimension})
    except ai.LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    result["record_count"] = len(records)
    result["range"] = {"start": start, "end": end}
    return result


@router.get("/compare")
def compare(
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = resolve_window(days, start_date, end_date)
    brands = conn.execute("SELECT * FROM brands ORDER BY is_primary DESC, name").fetchall()
    rows = []
    for brand in brands:
        snapshot = _brand_snapshot(conn, brand["id"], start, end)
        rows.append({
            "brand_id": brand["id"],
            "name": brand["name"],
            "is_competitor": bool(brand["is_competitor"]),
            "is_primary": bool(brand["is_primary"]),
            **snapshot,
        })
    return {"brands": rows}
