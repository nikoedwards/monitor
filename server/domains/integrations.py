"""Read-only integration endpoints for external AI tools.

The CLI and MCP adapters intentionally use this small, stable API instead of
opening the SQLite file directly.  This keeps the database behind the same
connection and serialization rules as the web application and prevents an
external model from executing arbitrary SQL or mutating MonitorHub data.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .common import get_conn, query_records

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

TABLES = {
    "records": "",
    "brands": "SELECT id, name, slug, is_competitor, is_primary, official_website, category, description, created_at, updated_at FROM brands ORDER BY name",
    "products": "SELECT id, brand_id, name, sku, category, notes, created_at, updated_at FROM products ORDER BY name",
    "links": "SELECT id, brand_id, product_id, dimension, channel, platform, url, label, status, last_collect_at, last_status FROM links ORDER BY updated_at DESC",
    "sources": "SELECT id, name, category, tier, vendor, status, needs_credentials, cadence, last_collect_at, last_status, item_count, notes FROM sources ORDER BY tier, name",
    "sales_metrics": "SELECT id, brand_id, product_id, link_id, snapshot_date, channel, platform, price, currency, review_count, rating, rank, units_est, revenue_est, estimate_confidence, in_stock, asin, title, change_score, source FROM sales_metrics ORDER BY snapshot_date DESC, created_at DESC",
    "job_postings": "SELECT id, brand_id, link_id, platform, external_id, url, title, department, city, jd_text, status, posted_at, refreshed_at, first_seen, last_seen, closed_at, last_change_at FROM job_postings ORDER BY COALESCE(last_seen, first_seen) DESC",
    "web_monitors": "SELECT id, brand_id, product_id, name, url, scope, status, cadence, check_interval_minutes, snapshot_interval_minutes, last_check_at, last_snapshot_at, last_change_score, last_change_summary, last_status FROM web_monitors ORDER BY updated_at DESC",
    "web_snapshots": "SELECT id, monitor_id, brand_id, snapshot_date, url, final_url, title, change_score, visual_change_score, visual_change_ratio, summary, created_at FROM web_snapshots ORDER BY snapshot_date DESC, created_at DESC",
}


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        item = dict(row)
        for key, value in list(item.items()):
            if key.endswith("_json"):
                item[key.removesuffix("_json")] = _json(value)
                del item[key]
        result.append(item)
    return result


@router.get("/manifest")
def manifest(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "name": "MonitorHub",
        "version": "1",
        "read_only": True,
        "api_base": f"{base}/api/integrations",
        "datasets": sorted(TABLES),
        "capabilities": ["overview", "list_brands", "search_records", "query_dataset"],
    }


@router.get("/overview")
def overview(conn: sqlite3.Connection = Depends(get_conn)):
    counts: dict[str, int] = {}
    for table in ("brands", "products", "records", "sales_metrics", "job_postings", "web_monitors", "web_snapshots"):
        try:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
        except sqlite3.OperationalError:
            counts[table] = 0
    latest = conn.execute("SELECT MAX(occurred_at) AS value FROM records").fetchone()["value"]
    return {"read_only": True, "counts": counts, "latest_record_at": latest}


@router.get("/query")
def query_dataset(
    dataset: str,
    brand_id: str | None = None,
    q: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if dataset not in TABLES:
        raise HTTPException(status_code=400, detail=f"Unsupported dataset. Choose one of: {', '.join(sorted(TABLES))}")
    requested = max(1, min(int(limit), 200))
    if dataset == "records":
        filters = {"brand_id": brand_id, "q": q, "start_date": start_date, "end_date": end_date}
        return {"dataset": dataset, "rows": query_records(conn, filters, limit=requested), "limit": requested}

    sql = TABLES[dataset]
    clauses: list[str] = []
    params: list[Any] = []
    if brand_id and dataset not in {"sources"}:
        clauses.append("brand_id = ?")
        params.append(brand_id)
    if q:
        like = f"%{q}%"
        text_columns = {
            "brands": ["name", "description", "category"],
            "products": ["name", "sku", "category", "notes"],
            "links": ["label", "url", "platform", "channel"],
            "sources": ["name", "category", "vendor", "notes"],
            "sales_metrics": ["title", "asin", "platform", "channel"],
            "job_postings": ["title", "department", "city", "jd_text", "platform"],
            "web_monitors": ["name", "url", "last_change_summary"],
            "web_snapshots": ["title", "url", "summary"],
        }[dataset]
        clauses.append("(" + " OR ".join(f"COALESCE({column}, '') LIKE ?" for column in text_columns) + ")")
        params.extend([like] * len(text_columns))
    date_column = "snapshot_date" if dataset in {"sales_metrics", "web_snapshots"} else "COALESCE(occurred_at, first_seen, created_at)"
    if dataset not in {"brands", "products", "links", "sources", "web_monitors"}:
        if start_date:
            clauses.append(f"substr({date_column}, 1, 10) >= ?")
            params.append(start_date)
        if end_date:
            clauses.append(f"substr({date_column}, 1, 10) <= ?")
            params.append(end_date)
    if clauses:
        sql = sql.replace(" ORDER BY", " WHERE " + " AND ".join(clauses) + " ORDER BY", 1)
    sql += " LIMIT ?"
    params.append(requested)
    return {"dataset": dataset, "rows": _rows(conn.execute(sql, params).fetchall()), "limit": requested}

