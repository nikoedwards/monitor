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
    # Supporting tables make the read-only bridge useful for deeper analysis
    # while deliberately leaving the credentials table out of the public list.
    "source_brand_runs": "SELECT * FROM source_brand_runs ORDER BY last_collect_at DESC",
    "sales_listings": "SELECT * FROM sales_listings ORDER BY updated_at DESC",
    "ad_entities": "SELECT * FROM ad_entities ORDER BY last_seen_at DESC",
    "ad_snapshots": "SELECT * FROM ad_snapshots ORDER BY observed_at DESC",
    "ad_events": "SELECT * FROM ad_events ORDER BY event_at DESC",
    "market_share_snapshots": "SELECT * FROM market_share_snapshots ORDER BY snapshot_date DESC",
    "creators": "SELECT * FROM creators ORDER BY updated_at DESC",
    "record_product_matches": "SELECT * FROM record_product_matches ORDER BY updated_at DESC",
    "creator_candidates": "SELECT * FROM creator_candidates ORDER BY updated_at DESC",
    "creator_candidate_evidence": "SELECT * FROM creator_candidate_evidence ORDER BY created_at DESC",
    "creator_map_snapshots": "SELECT * FROM creator_map_snapshots ORDER BY snapshot_date DESC",
    "web_snapshot_analyses": "SELECT * FROM web_snapshot_analyses ORDER BY created_at DESC",
    "voc_actions": "SELECT * FROM voc_actions ORDER BY updated_at DESC",
    "publications": "SELECT * FROM publications ORDER BY updated_at DESC",
    "capture_jobs": "SELECT * FROM capture_jobs ORDER BY created_at DESC",
    "job_snapshots": "SELECT * FROM job_snapshots ORDER BY snapshot_date DESC",
    "linkedin_profiles": "SELECT * FROM linkedin_profiles ORDER BY updated_at DESC",
    "linkedin_profile_snapshots": "SELECT * FROM linkedin_profile_snapshots ORDER BY snapshot_date DESC",
    "community_post_summaries": "SELECT * FROM community_post_summaries ORDER BY created_at DESC",
    "llm_summary_cache": "SELECT * FROM llm_summary_cache ORDER BY created_at DESC",
    "linkedin_activities": "SELECT * FROM linkedin_activities ORDER BY created_at DESC",
}

BRAND_FILTERABLE = {
    "brands", "products", "links", "sales_listings", "sales_metrics", "records",
    "ad_entities", "ad_snapshots", "ad_events", "market_share_snapshots", "creators",
    "record_product_matches", "creator_candidates", "creator_candidate_evidence",
    "creator_map_snapshots", "web_monitors", "web_snapshots", "web_snapshot_analyses",
    "voc_actions", "capture_jobs", "job_postings", "job_snapshots", "linkedin_profiles",
    "linkedin_profile_snapshots", "community_post_summaries", "linkedin_activities",
}

DATE_COLUMNS = {
    "sales_metrics": "snapshot_date",
    "web_snapshots": "snapshot_date",
    "job_postings": "COALESCE(last_seen, first_seen, created_at)",
    "sales_listings": "COALESCE(last_seen, created_at)",
    "ad_entities": "COALESCE(last_seen_at, first_seen_at, created_at)",
    "ad_snapshots": "observed_date",
    "ad_events": "event_at",
    "market_share_snapshots": "snapshot_date",
    "creators": "COALESCE(last_seen, first_seen, created_at)",
    "creator_candidates": "COALESCE(last_seen, first_seen, created_at)",
    "creator_candidate_evidence": "COALESCE(occurred_at, created_at)",
    "creator_map_snapshots": "snapshot_date",
    "web_snapshot_analyses": "created_at",
    "voc_actions": "COALESCE(updated_at, created_at)",
    "capture_jobs": "created_at",
    "job_snapshots": "snapshot_date",
    "linkedin_profiles": "COALESCE(last_seen, created_at)",
    "linkedin_profile_snapshots": "snapshot_date",
    "community_post_summaries": "created_at",
    "llm_summary_cache": "created_at",
    "linkedin_activities": "created_at",
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
    if brand_id and dataset in BRAND_FILTERABLE:
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
            "sales_listings": ["title", "asin", "sku", "url", "platform"],
            "job_snapshots": ["title", "department", "city", "platform"],
            "linkedin_profiles": ["name", "title", "headline", "profile_url"],
            "linkedin_activities": ["text", "activity_type", "url"],
            "voc_actions": ["title", "description", "owner_team", "topic", "product"],
            "publications": ["domain", "name", "tier", "country", "language"],
        }.get(dataset, [])
        if text_columns:
            clauses.append("(" + " OR ".join(f"COALESCE({column}, '') LIKE ?" for column in text_columns) + ")")
            params.extend([like] * len(text_columns))
    date_column = DATE_COLUMNS.get(dataset)
    if date_column:
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
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        # Older MonitorHub volumes may predate an optional table.  Keep the
        # bridge queryable and tell the model why this particular dataset is
        # empty instead of turning a broad cross-dataset question into a 500.
        if "no such table" in str(exc).lower():
            return {"dataset": dataset, "rows": [], "limit": requested, "available": False, "warning": str(exc)}
        raise
    return {"dataset": dataset, "rows": _rows(rows), "limit": requested, "available": True}

