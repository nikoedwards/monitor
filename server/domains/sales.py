"""Sales metrics (time-series), listing registry, manual entry, and summaries."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException

from .. import ai
from ..connectors.sales.base import canonicalize_metric_changes, metric_rank_value
from ..connectors.sales.runner import run_sales_collection
from ..schemas import SalesListingUpdate, SalesMetricIn
from ..util import new_id, today, utc_now
from .common import fetch_brand, get_conn, resolve_window

router = APIRouter(prefix="/api/sales", tags=["sales"])

CHANNELS = ["amazon", "dtc", "other_ecom", "offline"]


def metric_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    raw_json = item.pop("raw_json", None)
    raw = {}
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                raw = parsed
        except (TypeError, ValueError):
            raw = {}
    # Estimate metadata is stored in dedicated columns when available and in
    # raw_json for older databases/providers. Expose one stable API shape.
    for key in ("estimate_method", "estimate_confidence", "estimate_period_days"):
        if item.get(key) is None and raw.get(key) is not None:
            item[key] = raw[key]
    item["estimate_basis"] = raw.get("estimate_basis") if isinstance(raw.get("estimate_basis"), dict) else None
    item["in_stock"] = bool(item["in_stock"]) if item.get("in_stock") is not None else None
    item["changes"] = json.loads(item.pop("changes_json", None) or "[]")
    return item


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return columns for a table, including compatibility test/legacy schemas."""
    try:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


_LEGACY_CHANGE_FIELDS = (
    "rating", "review_count", "price", "units_est", "revenue_est", "in_stock",
    "category_rank", "subcategory_rank",
)


def _metric_changes(previous: dict | None, current: dict) -> list[dict]:
    """Normalize recorded changes and fill gaps in legacy snapshots.

    Captures written by the current collector already carry authoritative
    ``changes_json`` entries. Older rows often have an empty list, so compare
    the adjacent snapshots only for fields absent from that recorded list.
    Rank and BSR are aliases and are compared as one canonical ``rank`` field.
    """
    changes = canonicalize_metric_changes(current.get("changes") or [])
    if not previous:
        return changes
    known = {str(change.get("field")) for change in changes}
    old_rank, new_rank = metric_rank_value(previous), metric_rank_value(current)
    has_explicit_category = (
        current.get("category_rank") is not None
        or previous.get("category_rank") is not None
    )
    if "rank" not in known and "category_rank" not in known and old_rank != new_rank and not (old_rank is None and new_rank is None):
        if has_explicit_category:
            # When a legacy snapshot only has `rank`/`bsr`, use that value as
            # the baseline for the first explicit category-rank snapshot.
            # This keeps the migration event meaningful instead of reporting
            # a misleading ``None -> new`` transition.
            old_category = previous.get("category_rank")
            new_category = current.get("category_rank")
            if old_category is None:
                old_category = old_rank
            if new_category is None:
                new_category = new_rank
            if old_category != new_category and not (old_category is None and new_category is None):
                changes.append({"field": "category_rank", "from": old_category, "to": new_category})
                known.add("category_rank")
        else:
            changes.append({"field": "rank", "from": old_rank, "to": new_rank})
    for field in _LEGACY_CHANGE_FIELDS:
        if field in known:
            continue
        old_value, new_value = previous.get(field), current.get(field)
        if old_value == new_value or (old_value is None and new_value is None):
            continue
        changes.append({"field": field, "from": old_value, "to": new_value})
    return canonicalize_metric_changes(changes)


def listing_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = dict(row)
    item.pop("config_json", None)
    item["monitor"] = bool(item.get("monitor"))
    latest = conn.execute(
        "SELECT * FROM sales_metrics WHERE link_id = ? ORDER BY snapshot_date DESC, created_at DESC LIMIT 1",
        (item["id"],),
    ).fetchone()
    item["latest"] = metric_to_dict(latest) if latest else None
    item["data_points"] = conn.execute(
        "SELECT COUNT(*) AS c FROM sales_metrics WHERE link_id = ?", (item["id"],)
    ).fetchone()["c"]
    item["has_change"] = bool(item.get("last_change_at"))
    return item


@router.get("/metrics")
def list_metrics(
    brand_id: str | None = None,
    channel: str | None = None,
    product_id: str | None = None,
    days: int = 90,
    conn: sqlite3.Connection = Depends(get_conn),
):
    clauses, params = [], []
    for key, value in (("brand_id", brand_id), ("channel", channel), ("product_id", product_id)):
        if value:
            clauses.append(f"{key} = ?")
            params.append(value)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM sales_metrics{where} ORDER BY snapshot_date DESC LIMIT 1000", params
    ).fetchall()
    return {"metrics": [metric_to_dict(r) for r in rows]}


@router.post("/metrics", status_code=201)
def add_metric(payload: SalesMetricIn, conn: sqlite3.Connection = Depends(get_conn)):
    metric_id = new_id()
    values = {
        "id": metric_id,
        "link_id": payload.link_id,
        "brand_id": payload.brand_id,
        "product_id": payload.product_id,
        "snapshot_date": payload.snapshot_date or today(),
        "channel": payload.channel,
        "platform": payload.platform,
        "price": payload.price,
        "currency": payload.currency,
        "review_count": payload.review_count,
        "rating": payload.rating,
        "rank": payload.rank,
        "category_rank": payload.category_rank,
        "subcategory_rank": payload.subcategory_rank,
        "category_name": payload.category_name,
        "subcategory_name": payload.subcategory_name,
        "units_est": payload.units_est,
        "revenue_est": payload.revenue_est,
        "estimate_method": payload.estimate_method,
        "estimate_confidence": payload.estimate_confidence,
        "estimate_period_days": payload.estimate_period_days,
        "in_stock": None if payload.in_stock is None else int(payload.in_stock),
        "source": payload.source,
        "created_at": utc_now(),
    }
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sales_metrics)")}
    selected = [key for key in values if key in columns]
    placeholders = ", ".join("?" for _ in selected)
    conn.execute(
        f"INSERT INTO sales_metrics ({', '.join(selected)}) VALUES ({placeholders})",
        [values[key] for key in selected],
    )
    return metric_to_dict(conn.execute("SELECT * FROM sales_metrics WHERE id = ?", (metric_id,)).fetchone())


@router.delete("/metrics/{metric_id}")
def delete_metric(metric_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("DELETE FROM sales_metrics WHERE id = ?", (metric_id,))
    return {"deleted": metric_id}


# ----------------------------------------------------------------- collection
@router.post("/sync")
def sync_sales(brand_id: str, link_id: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    """Run expansion + capture now (immediate first sync after configuring a link)."""
    brand = fetch_brand(conn, brand_id)
    summary = run_sales_collection(conn, brand, link_id=link_id)
    return summary


# -------------------------------------------------------------------- listings
@router.get("/listings")
def list_listings(
    brand_id: str,
    channel: str | None = None,
    product_id: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    clauses, params = ["brand_id = ?"], [brand_id]
    if channel and channel != "all":
        clauses.append("channel = ?")
        params.append(channel)
    if product_id:
        clauses.append("product_id = ?")
        params.append(product_id)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM sales_listings WHERE {where} ORDER BY last_change_at DESC, last_seen DESC",
        params,
    ).fetchall()
    return {"listings": [listing_to_dict(conn, r) for r in rows]}


@router.put("/listings/{listing_id}")
def update_listing(listing_id: str, payload: SalesListingUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    existing = conn.execute("SELECT * FROM sales_listings WHERE id = ?", (listing_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Listing not found")
    data = payload.model_dump(exclude_unset=True)
    monitor = int(data["monitor"]) if "monitor" in data else existing["monitor"]
    status = data.get("status", existing["status"])
    product_id = (data["product_id"] or None) if "product_id" in data else existing["product_id"]
    conn.execute(
        "UPDATE sales_listings SET monitor = ?, status = ?, product_id = ?, updated_at = ? WHERE id = ?",
        (monitor, status, product_id, utc_now(), listing_id),
    )
    # Keep historical metric rows aligned to the listing's product mapping.
    if "product_id" in data:
        conn.execute("UPDATE sales_metrics SET product_id = ? WHERE link_id = ?", (product_id, listing_id))
    return listing_to_dict(conn, conn.execute("SELECT * FROM sales_listings WHERE id = ?", (listing_id,)).fetchone())


@router.post("/listings/automap")
def automap_listings(
    brand_id: str,
    channel: str | None = None,
    only_unmapped: bool = True,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Use the LLM to map listings to products by semantic match (title/ASIN/SKU)."""
    if not ai.is_configured(conn):
        raise HTTPException(status_code=400, detail="尚未配置大模型 Token，请先在设置中填写。")

    products = [
        dict(p)
        for p in conn.execute(
            "SELECT id, name, sku, category FROM products WHERE brand_id = ?", (brand_id,)
        ).fetchall()
    ]
    if not products:
        raise HTTPException(status_code=400, detail="该品牌暂无产品，请先在品牌管理添加产品。")

    clauses, params = ["brand_id = ?"], [brand_id]
    if channel and channel != "all":
        clauses.append("channel = ?")
        params.append(channel)
    if only_unmapped:
        clauses.append("product_id IS NULL")
    where = " AND ".join(clauses)
    listings = [
        dict(r)
        for r in conn.execute(
            f"SELECT id, title, asin, sku, channel FROM sales_listings WHERE {where} ORDER BY last_seen DESC LIMIT 100",
            params,
        ).fetchall()
    ]
    if not listings:
        return {"candidates": 0, "mapped": 0, "results": []}

    try:
        results = ai.automap_listings(conn, listings, products)
    except ai.LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    names = {p["id"]: p["name"] for p in products}
    mapped = 0
    for r in results:
        if r["applied"] and r["product_id"]:
            conn.execute(
                "UPDATE sales_listings SET product_id = ?, updated_at = ? WHERE id = ?",
                (r["product_id"], utc_now(), r["listing_id"]),
            )
            conn.execute(
                "UPDATE sales_metrics SET product_id = ? WHERE link_id = ?",
                (r["product_id"], r["listing_id"]),
            )
            r["product_name"] = names.get(r["product_id"])
            mapped += 1
    return {"candidates": len(listings), "mapped": mapped, "results": results}


@router.delete("/listings/{listing_id}")
def delete_listing(listing_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("DELETE FROM sales_metrics WHERE link_id = ?", (listing_id,))
    conn.execute("DELETE FROM sales_listings WHERE id = ?", (listing_id,))
    return {"deleted": listing_id}


@router.get("/listings/{listing_id}/history")
def listing_history(listing_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    listing = conn.execute("SELECT * FROM sales_listings WHERE id = ?", (listing_id,)).fetchone()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    rows = conn.execute(
        "SELECT * FROM sales_metrics WHERE link_id = ? ORDER BY snapshot_date",
        (listing_id,),
    ).fetchall()
    metrics = [metric_to_dict(r) for r in rows]
    changes = [
        {"date": m["snapshot_date"], "changes": m["changes"]}
        for m in metrics
        if m.get("changes")
    ]
    return {"listing": listing_to_dict(conn, listing), "metrics": metrics, "changes": list(reversed(changes))}


@router.get("/changes")
def sales_changes(
    brand_id: str,
    product_id: str | None = None,
    channel: str | None = None,
    days: int = 90,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 500,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Return a date-range change log across all monitored listings.

    A listing's daily ``changes_json`` is expanded into readable events so the
    global sales view can show rank, rating, review-count and other metric
    changes without opening each listing one at a time.  Listing counts are
    included per day as a separate series, with a delta relative to the prior
    captured day.
    """
    start, end = resolve_window(days, start_date, end_date)
    clauses = ["sm.brand_id = ?", "sm.snapshot_date >= ?", "sm.snapshot_date <= ?"]
    params: list = [brand_id, start, end]
    if product_id:
        clauses.append("sm.product_id = ?")
        params.append(product_id)
    if channel and channel != "all":
        clauses.append("sm.channel = ?")
        params.append(channel)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT sm.*, sl.title AS listing_title, sl.asin AS listing_asin,
               p.name AS product_name
        FROM sales_metrics sm
        LEFT JOIN sales_listings sl ON sl.id = sm.link_id
        LEFT JOIN products p ON p.id = sm.product_id
        WHERE {where}
        ORDER BY sm.snapshot_date DESC, sm.created_at DESC
        LIMIT ?
        """,
        [*params, max(1, min(int(limit or 500), 2000))],
    ).fetchall()

    events: list[dict] = []
    field_counts: dict[str, int] = defaultdict(int)
    changed_listing_ids: set[str] = set()

    # Seed each listing with its latest snapshot before the selected window.
    # Without this, legacy rows whose changes_json predates metric fingerprints
    # lose the change on the first selected day.
    previous_clauses = ["sm.brand_id = ?", "sm.snapshot_date < ?"]
    previous_params: list = [brand_id, start]
    if product_id:
        previous_clauses.append("sm.product_id = ?")
        previous_params.append(product_id)
    if channel and channel != "all":
        previous_clauses.append("sm.channel = ?")
        previous_params.append(channel)
    previous_rows = conn.execute(
        f"""
        SELECT * FROM (
            SELECT sm.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(sm.link_id, sm.id)
                       ORDER BY sm.snapshot_date DESC, sm.created_at DESC
                   ) AS row_number
            FROM sales_metrics sm
            WHERE {' AND '.join(previous_clauses)}
        )
        WHERE row_number = 1
        """,
        previous_params,
    ).fetchall()
    previous_by_listing: dict[str, dict] = {}
    for previous_row in previous_rows:
        previous_metric = metric_to_dict(previous_row)
        previous_metric.pop("row_number", None)
        previous_key = previous_metric.get("link_id") or previous_metric.get("id")
        if previous_key:
            previous_by_listing[previous_key] = previous_metric
    # The query keeps the newest rows under the limit; walk those rows in
    # chronological order to infer deltas from legacy snapshots.
    for row in reversed(rows):
        metric = metric_to_dict(row)
        # Older snapshots may predate metric-aware fingerprints. Reconstruct
        # their rank/rating/review/price deltas from adjacent daily rows so the
        # log is useful immediately after upgrading an existing installation.
        listing_key = metric.get("link_id") or metric.get("id")
        previous = previous_by_listing.get(listing_key) if listing_key else None
        changes = _metric_changes(previous, metric)
        if listing_key:
            previous_by_listing[listing_key] = metric
        if not changes:
            continue
        item = {
            "date": metric["snapshot_date"],
            "listing_id": metric.get("link_id"),
            "listing_title": row["listing_title"] or metric.get("title") or metric.get("asin") or row["listing_asin"],
            "asin": metric.get("asin") or row["listing_asin"],
            "product_id": metric.get("product_id"),
            "product_name": row["product_name"],
            "channel": metric.get("channel"),
            "platform": metric.get("platform"),
            "change_score": metric.get("change_score") or 0,
            "changes": changes,
        }
        events.append(item)
        if metric.get("link_id"):
            changed_listing_ids.add(metric["link_id"])
        for change in changes:
            field = str(change.get("field") or "other")
            field_counts[field] = field_counts.get(field, 0) + 1

    # Storefront expansion creates registry rows before the first successful
    # metric capture. Include those additions so Listing-count changes remain
    # visible even when a provider is blocked on its first fetch.
    listing_clauses = ["sl.brand_id = ?", "substr(COALESCE(sl.first_seen, sl.created_at), 1, 10) >= ?", "substr(COALESCE(sl.first_seen, sl.created_at), 1, 10) <= ?"]
    listing_params: list = [brand_id, start, end]
    if product_id:
        listing_clauses.append("sl.product_id = ?")
        listing_params.append(product_id)
    if channel and channel != "all":
        listing_clauses.append("sl.channel = ?")
        listing_params.append(channel)
    listing_rows = conn.execute(
        f"""
        SELECT sl.id, sl.title, sl.asin, sl.product_id, sl.channel, sl.platform,
               p.name AS product_name, substr(COALESCE(sl.first_seen, sl.created_at), 1, 10) AS date
        FROM sales_listings sl
        LEFT JOIN products p ON p.id = sl.product_id
        WHERE {' AND '.join(listing_clauses)}
        ORDER BY date DESC
        LIMIT ?
        """,
        [*listing_params, max(1, min(int(limit or 500), 2000))],
    ).fetchall()
    for row in listing_rows:
        events.append({
            "date": row["date"],
            "listing_id": row["id"],
            "listing_title": row["title"] or row["asin"],
            "asin": row["asin"],
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "channel": row["channel"],
            "platform": row["platform"],
            "change_score": 0,
            "event_type": "listing_added",
            "changes": [{"field": "listing_count", "from": 0, "to": 1}],
        })
        changed_listing_ids.add(row["id"])
    if listing_rows:
        field_counts["listing_count"] = field_counts.get("listing_count", 0) + len(listing_rows)

    events.sort(key=lambda item: item["date"], reverse=True)

    # Count distinct listings captured or discovered each day. Registry rows
    # are included so a blocked first fetch still produces a Listing-count
    # change in the selected period.
    count_clauses = ["brand_id = ?", "snapshot_date >= ?", "snapshot_date <= ?"]
    count_params: list = [brand_id, start, end]
    if product_id:
        count_clauses.append("product_id = ?")
        count_params.append(product_id)
    if channel and channel != "all":
        count_clauses.append("channel = ?")
        count_params.append(channel)
    daily_listing_ids: dict[str, set[str]] = defaultdict(set)
    metric_daily_rows = conn.execute(
        f"""
        SELECT snapshot_date AS date, id, link_id
        FROM sales_metrics
        WHERE {' AND '.join(count_clauses)}
        ORDER BY snapshot_date
        """,
        count_params,
    ).fetchall()
    for row in metric_daily_rows:
        daily_listing_ids[row["date"]].add(row["link_id"] or f"metric:{row['id']}")

    registry_count_clauses = [
        "brand_id = ?",
        "substr(COALESCE(first_seen, created_at), 1, 10) >= ?",
        "substr(COALESCE(first_seen, created_at), 1, 10) <= ?",
    ]
    registry_count_params: list = [brand_id, start, end]
    if product_id:
        registry_count_clauses.append("product_id = ?")
        registry_count_params.append(product_id)
    if channel and channel != "all":
        registry_count_clauses.append("channel = ?")
        registry_count_params.append(channel)
    registry_daily_rows = conn.execute(
        f"""
        SELECT id, substr(COALESCE(first_seen, created_at), 1, 10) AS date
        FROM sales_listings
        WHERE {' AND '.join(registry_count_clauses)}
        """,
        registry_count_params,
    ).fetchall()
    for row in registry_daily_rows:
        daily_listing_ids[row["date"]].add(row["id"])
    daily = [
        {"date": date, "listing_count": len(listing_ids), "delta": 0}
        for date, listing_ids in sorted(daily_listing_ids.items())
    ]
    previous_date_clauses = ["brand_id = ?", "snapshot_date < ?"]
    previous_date_params: list = [brand_id, start]
    if product_id:
        previous_date_clauses.append("product_id = ?")
        previous_date_params.append(product_id)
    if channel and channel != "all":
        previous_date_clauses.append("channel = ?")
        previous_date_params.append(channel)
    previous_metric_date = conn.execute(
        f"SELECT MAX(snapshot_date) AS date FROM sales_metrics WHERE {' AND '.join(previous_date_clauses)}",
        previous_date_params,
    ).fetchone()["date"]

    previous_registry_clauses = [
        "brand_id = ?",
        "substr(COALESCE(first_seen, created_at), 1, 10) < ?",
    ]
    previous_registry_params: list = [brand_id, start]
    if product_id:
        previous_registry_clauses.append("product_id = ?")
        previous_registry_params.append(product_id)
    if channel and channel != "all":
        previous_registry_clauses.append("channel = ?")
        previous_registry_params.append(channel)
    previous_registry_date = conn.execute(
        "SELECT MAX(substr(COALESCE(first_seen, created_at), 1, 10)) AS date "
        f"FROM sales_listings WHERE {' AND '.join(previous_registry_clauses)}",
        previous_registry_params,
    ).fetchone()["date"]
    previous_dates = [date for date in (previous_metric_date, previous_registry_date) if date]
    previous_date = max(previous_dates) if previous_dates else None
    previous_count = 0
    if previous_date:
        previous_ids: set[str] = set()
        previous_count_clauses = ["brand_id = ?", "snapshot_date = ?"]
        previous_count_params: list = [brand_id, previous_date]
        if product_id:
            previous_count_clauses.append("product_id = ?")
            previous_count_params.append(product_id)
        if channel and channel != "all":
            previous_count_clauses.append("channel = ?")
            previous_count_params.append(channel)
        previous_metric_rows = conn.execute(
            f"SELECT id, link_id FROM sales_metrics "
            f"WHERE {' AND '.join(previous_count_clauses)}",
            previous_count_params,
        ).fetchall()
        for row in previous_metric_rows:
            previous_ids.add(row["link_id"] or f"metric:{row['id']}")

        previous_listing_clauses = [
            "brand_id = ?",
            "substr(COALESCE(first_seen, created_at), 1, 10) = ?",
        ]
        previous_listing_params: list = [brand_id, previous_date]
        if product_id:
            previous_listing_clauses.append("product_id = ?")
            previous_listing_params.append(product_id)
        if channel and channel != "all":
            previous_listing_clauses.append("channel = ?")
            previous_listing_params.append(channel)
        previous_listing_rows = conn.execute(
            f"SELECT id FROM sales_listings WHERE {' AND '.join(previous_listing_clauses)}",
            previous_listing_params,
        ).fetchall()
        previous_ids.update(row["id"] for row in previous_listing_rows)
        previous_count = len(previous_ids)
    for point in daily:
        point["delta"] = point["listing_count"] - previous_count
        previous_count = point["listing_count"]

    return {
        "range": {"start": start, "end": end},
        "events": events,
        "daily": daily,
        "total_events": len(events),
        "changed_listings": len(changed_listing_ids),
        "field_counts": dict(sorted(field_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
    }


@router.get("/summary")
def sales_summary(
    brand_id: str | None = None,
    product_id: str | None = None,
    days: int = 90,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = resolve_window(days, start_date, end_date)
    clauses, params = ["snapshot_date >= ?", "snapshot_date <= ?"], [start, end]
    if brand_id:
        clauses.append("brand_id = ?")
        params.append(brand_id)
    if product_id:
        clauses.append("product_id = ?")
        params.append(product_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM sales_metrics{where} ORDER BY snapshot_date", params
    ).fetchall()
    metrics = [metric_to_dict(r) for r in rows]

    # Seed the legacy-change comparison with each listing's latest snapshot
    # before the selected range. This keeps the summary cards and trend change
    # counts consistent with /changes when the first in-range row has an empty
    # changes_json payload.
    summary_previous_clauses = ["sm.snapshot_date < ?"]
    summary_previous_params: list = [start]
    if brand_id:
        summary_previous_clauses.append("sm.brand_id = ?")
        summary_previous_params.append(brand_id)
    if product_id:
        summary_previous_clauses.append("sm.product_id = ?")
        summary_previous_params.append(product_id)
    summary_previous_rows = conn.execute(
        f"""
        SELECT * FROM (
            SELECT sm.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(sm.link_id, sm.id)
                       ORDER BY sm.snapshot_date DESC, sm.created_at DESC
                   ) AS row_number
            FROM sales_metrics sm
            WHERE {' AND '.join(summary_previous_clauses)}
        )
        WHERE row_number = 1
        """,
        summary_previous_params,
    ).fetchall()
    summary_previous_by_listing: dict[str, dict] = {}
    for previous_row in summary_previous_rows:
        previous_metric = metric_to_dict(previous_row)
        previous_key = previous_metric.get("link_id") or previous_metric.get("id")
        if previous_key:
            summary_previous_by_listing[previous_key] = previous_metric

    by_channel: dict[str, dict] = {
        c: {
            "channel": c,
            "revenue": 0.0,
            "units": 0,
            "data_points": 0,
            "latest_listings": 0,
            "configured_links": 0,
            "configured_listings": 0,
            "revenue_points": 0,
            "units_points": 0,
        }
        for c in CHANNELS
    }
    # Keep the revenue/unit series for the existing sales chart, but also
    # retain listing-level marketplace signals.  Automated providers often
    # return rank/rating/review counts without a revenue estimate; previously
    # that made the global view look empty even though it had data points.
    trend_map: dict[str, dict] = defaultdict(
        lambda: {
            "date": "",
            "revenue": 0.0,
            "units": 0,
            "rank_sum": 0.0,
            "rank_count": 0,
            "category_rank_sum": 0.0,
            "category_rank_count": 0,
            "subcategory_rank_sum": 0.0,
            "subcategory_rank_count": 0,
            "rating_sum": 0.0,
            "rating_count": 0,
            "review_count": 0,
            "review_points": 0,
            "listing_ids": set(),
            "changed_listings": set(),
        }
    )
    product_map: dict[str, dict] = defaultdict(
        lambda: {
            "product_id": None,
            "revenue": 0.0,
            "units": 0,
            "listings": 0,
            "category_rank_avg": None,
            "subcategory_rank_avg": None,
        }
    )

    # The latest row per listing is the only meaningful value for a global
    # snapshot (summing ranks/ratings across all historical rows is invalid).
    latest_by_listing: dict[str, dict] = {}
    first_by_listing: dict[str, dict] = {}
    changed_listing_keys: set[str] = set()
    for m in metrics:
        channel = m["channel"] if m["channel"] in by_channel else "other_ecom"
        bucket = by_channel.setdefault(
            channel,
            {
                "channel": channel,
                "revenue": 0.0,
                "units": 0,
                "data_points": 0,
                "latest_listings": 0,
                "configured_links": 0,
                "configured_listings": 0,
                "revenue_points": 0,
                "units_points": 0,
            },
        )
        bucket["revenue"] += m.get("revenue_est") or 0.0
        bucket["units"] += m.get("units_est") or 0
        bucket["data_points"] += 1
        bucket["revenue_points"] += int(m.get("revenue_est") is not None)
        bucket["units_points"] += int(m.get("units_est") is not None)
        day = trend_map[m["snapshot_date"]]
        day["date"] = m["snapshot_date"]
        day["revenue"] += m.get("revenue_est") or 0.0
        day["units"] += m.get("units_est") or 0
        listing_key = m.get("link_id") or m.get("id") or f"metric:{len(day['listing_ids'])}"
        day["listing_ids"].add(listing_key)
        effective_changes = _metric_changes(summary_previous_by_listing.get(listing_key), m)
        m["_effective_changes"] = effective_changes
        if effective_changes:
            day["changed_listings"].add(listing_key)
            changed_listing_keys.add(listing_key)
        category_rank = m.get("category_rank")
        if category_rank is None:
            category_rank = m.get("bsr") if m.get("bsr") is not None else m.get("rank")
        subcategory_rank = m.get("subcategory_rank")
        if category_rank is not None:
            day["rank_sum"] += category_rank
            day["rank_count"] += 1
            day["category_rank_sum"] += category_rank
            day["category_rank_count"] += 1
        if subcategory_rank is not None:
            day["subcategory_rank_sum"] += subcategory_rank
            day["subcategory_rank_count"] += 1
        if m.get("rating") is not None:
            day["rating_sum"] += m["rating"]
            day["rating_count"] += 1
        if m.get("review_count") is not None:
            day["review_count"] += m["review_count"]
            day["review_points"] += 1
        # A metric row can be keyed by link_id for scraped listings, while
        # manual rows may not have one.  In the latter case each metric is its
        # own series rather than accidentally merging unrelated rows.
        key = listing_key
        if key:
            previous = latest_by_listing.get(key)
            if previous is None or (m.get("snapshot_date", ""), m.get("created_at", "")) > (
                previous.get("snapshot_date", ""), previous.get("created_at", "")
            ):
                latest_by_listing[key] = m
            first = first_by_listing.get(key)
            if first is None or (m.get("snapshot_date", ""), m.get("created_at", "")) < (
                first.get("snapshot_date", ""), first.get("created_at", "")
            ):
                first_by_listing[key] = m
            summary_previous_by_listing[key] = m
        pid = m.get("product_id") or "__unmapped__"
        pbucket = product_map[pid]
        pbucket["product_id"] = m.get("product_id")
        pbucket["revenue"] += m.get("revenue_est") or 0.0
        pbucket["units"] += m.get("units_est") or 0

    # Include product coverage and latest marketplace measurements as well as
    # estimates, so products with scraped data do not appear as empty zeros.
    for pid, bucket in product_map.items():
        product_rows = [m for m in latest_by_listing.values() if (m.get("product_id") or "__unmapped__") == pid]
        ranks = [
            m.get("category_rank")
            if m.get("category_rank") is not None
            else (m.get("bsr") if m.get("bsr") is not None else m.get("rank"))
            for m in product_rows
        ]
        ranks = [value for value in ranks if value is not None]
        subcategory_ranks = [m.get("subcategory_rank") for m in product_rows if m.get("subcategory_rank") is not None]
        ratings = [m["rating"] for m in product_rows if m.get("rating") is not None]
        reviews = [m["review_count"] for m in product_rows if m.get("review_count") is not None]
        product_metrics = [m for m in metrics if (m.get("product_id") or "__unmapped__") == pid]
        bucket["listings"] = len(product_rows)
        bucket["rank_avg"] = round(sum(ranks) / len(ranks), 2) if ranks else None
        bucket["category_rank_avg"] = bucket["rank_avg"]
        bucket["subcategory_rank_avg"] = round(sum(subcategory_ranks) / len(subcategory_ranks), 2) if subcategory_ranks else None
        bucket["rating_avg"] = round(sum(ratings) / len(ratings), 2) if ratings else None
        bucket["review_count"] = sum(reviews) if reviews else None
        bucket["revenue_points"] = sum(m.get("revenue_est") is not None for m in product_metrics)
        bucket["units_points"] = sum(m.get("units_est") is not None for m in product_metrics)

    # Resolve product names for the by-product view.
    names = {}
    if brand_id:
        for p in conn.execute("SELECT id, name FROM products WHERE brand_id = ?", (brand_id,)).fetchall():
            names[p["id"]] = p["name"]
    by_product = []
    for pid, bucket in product_map.items():
        bucket["product_name"] = names.get(bucket["product_id"]) if bucket["product_id"] else "未映射"
        bucket["revenue"] = round(bucket["revenue"], 2)
        by_product.append(bucket)
    by_product.sort(key=lambda b: b["revenue"], reverse=True)

    # Convert the internal set/count accumulators into JSON-safe trend rows.
    trend = []
    for item in sorted(trend_map.values(), key=lambda i: i["date"]):
        rank_count = item.pop("rank_count")
        category_rank_count = item.pop("category_rank_count")
        subcategory_rank_count = item.pop("subcategory_rank_count")
        rating_count = item.pop("rating_count")
        rank_sum = item.pop("rank_sum")
        category_rank_sum = item.pop("category_rank_sum")
        subcategory_rank_sum = item.pop("subcategory_rank_sum")
        rating_sum = item.pop("rating_sum")
        listing_ids = item.pop("listing_ids")
        changed_listings = item.pop("changed_listings")
        item["rank_avg"] = round(rank_sum / rank_count, 2) if rank_count else None
        item["category_rank_avg"] = round(category_rank_sum / category_rank_count, 2) if category_rank_count else None
        item["subcategory_rank_avg"] = round(subcategory_rank_sum / subcategory_rank_count, 2) if subcategory_rank_count else None
        item["rating_avg"] = round(rating_sum / rating_count, 2) if rating_count else None
        if not item.pop("review_points"):
            item["review_count"] = None
        item["listing_count"] = len(listing_ids)
        item["changes"] = len(changed_listings)
        trend.append(item)

    def _signal(row: dict, name: str):
        if name == "rank":
            return (
                row.get("category_rank")
                if row.get("category_rank") is not None
                else (row.get("bsr") if row.get("bsr") is not None else row.get("rank"))
            )
        return row.get(name)

    def _category_rank(row: dict):
        return row.get("category_rank") if row.get("category_rank") is not None else _signal(row, "rank")

    def _subcategory_rank(row: dict):
        return row.get("subcategory_rank")

    latest_rank = [_signal(m, "rank") for m in latest_by_listing.values() if _signal(m, "rank") is not None]
    latest_category_rank = [_category_rank(m) for m in latest_by_listing.values() if _category_rank(m) is not None]
    latest_subcategory_rank = [_subcategory_rank(m) for m in latest_by_listing.values() if _subcategory_rank(m) is not None]
    latest_rating = [_signal(m, "rating") for m in latest_by_listing.values() if _signal(m, "rating") is not None]
    latest_reviews = [_signal(m, "review_count") for m in latest_by_listing.values() if _signal(m, "review_count") is not None]
    rank_deltas, category_rank_deltas, subcategory_rank_deltas, rating_deltas, review_deltas = [], [], [], [], []
    for key, latest in latest_by_listing.items():
        first = first_by_listing.get(key)
        if not first or first.get("id") == latest.get("id"):
            continue
        old_rank, new_rank = _signal(first, "rank"), _signal(latest, "rank")
        if old_rank is not None and new_rank is not None:
            # Positive means the rank number improved (moved closer to #1).
            rank_deltas.append(old_rank - new_rank)
        old_category, new_category = _category_rank(first), _category_rank(latest)
        if old_category is not None and new_category is not None:
            category_rank_deltas.append(old_category - new_category)
        old_subcategory, new_subcategory = _subcategory_rank(first), _subcategory_rank(latest)
        if old_subcategory is not None and new_subcategory is not None:
            subcategory_rank_deltas.append(old_subcategory - new_subcategory)
        old_rating, new_rating = _signal(first, "rating"), _signal(latest, "rating")
        if old_rating is not None and new_rating is not None:
            rating_deltas.append(new_rating - old_rating)
        old_reviews, new_reviews = _signal(first, "review_count"), _signal(latest, "review_count")
        if old_reviews is not None and new_reviews is not None:
            review_deltas.append(new_reviews - old_reviews)

    global_metrics = {
        "latest_date": max((m.get("snapshot_date") for m in latest_by_listing.values()), default=None),
        "listing_count": len(latest_by_listing),
        "rank_avg": round(sum(latest_rank) / len(latest_rank), 2) if latest_rank else None,
        "rank_min": min(latest_rank) if latest_rank else None,
        "category_rank_avg": round(sum(latest_category_rank) / len(latest_category_rank), 2) if latest_category_rank else None,
        "category_rank_min": min(latest_category_rank) if latest_category_rank else None,
        "subcategory_rank_avg": round(sum(latest_subcategory_rank) / len(latest_subcategory_rank), 2) if latest_subcategory_rank else None,
        "rating_avg": round(sum(latest_rating) / len(latest_rating), 2) if latest_rating else None,
        "review_count": sum(latest_reviews) if latest_reviews else None,
        "rank_change": round(sum(rank_deltas) / len(rank_deltas), 2) if rank_deltas else None,
        "category_rank_change": round(sum(category_rank_deltas) / len(category_rank_deltas), 2) if category_rank_deltas else None,
        "subcategory_rank_change": round(sum(subcategory_rank_deltas) / len(subcategory_rank_deltas), 2) if subcategory_rank_deltas else None,
        "rating_change": round(sum(rating_deltas) / len(rating_deltas), 2) if rating_deltas else None,
        "review_change": sum(review_deltas) if review_deltas else 0,
        "changed_listings": len(changed_listing_keys),
        "changes": sum(len(m.get("_effective_changes") or []) for m in metrics),
    }

    for channel, bucket in by_channel.items():
        bucket["latest_listings"] = sum(1 for m in latest_by_listing.values() if (m.get("channel") or "other_ecom") == channel)
        bucket["revenue"] = round(bucket["revenue"], 2)

    listing_filter = ["1 = 1"]
    listing_params: list[str] = []
    if brand_id:
        listing_filter.append("brand_id = ?")
        listing_params.append(brand_id)
    if product_id:
        listing_filter.append("product_id = ?")
        listing_params.append(product_id)
    listing_where = " AND ".join(listing_filter)
    link_filter = ["dimension = 'sales'"]
    link_params: list[str] = []
    if brand_id:
        link_filter.append("brand_id = ?")
        link_params.append(brand_id)
    if product_id:
        link_filter.append("product_id = ?")
        link_params.append(product_id)
    links = conn.execute(
        f"SELECT channel, COUNT(*) AS total FROM links WHERE {' AND '.join(link_filter)} GROUP BY channel",
        link_params,
    ).fetchall()
    link_counts = {str(row["channel"]): int(row["total"] or 0) for row in links}

    # Registry coverage is independent from successful metric captures.  Keep
    # it in the channel payload so a configured Amazon channel remains visible
    # while its first scrape is blocked or still pending.
    listing_counts: dict[str, int] = {}
    listing_columns = _table_columns(conn, "sales_listings")
    if "channel" in listing_columns:
        registry_clauses = ["brand_id = ?"]
        registry_params: list[str] = [brand_id] if brand_id else []
        if not brand_id:
            registry_clauses = ["1 = 1"]
        if product_id and "product_id" in listing_columns:
            registry_clauses.append("product_id = ?")
            registry_params.append(product_id)
        registry_rows = conn.execute(
            f"SELECT channel, COUNT(*) AS total FROM sales_listings WHERE {' AND '.join(registry_clauses)} GROUP BY channel",
            registry_params,
        ).fetchall()
        listing_counts = {str(row["channel"]): int(row["total"] or 0) for row in registry_rows}

    metric_latest_counts = {
        channel: sum(1 for m in latest_by_listing.values() if (m.get("channel") or "other_ecom") == channel)
        for channel in by_channel
    }
    for channel, bucket in by_channel.items():
        metric_latest = metric_latest_counts.get(channel, 0)
        registry_total = listing_counts.get(channel, 0)
        bucket["configured_links"] = link_counts.get(channel, 0)
        bucket["configured_listings"] = registry_total
        bucket["latest_listings"] = max(metric_latest, registry_total)
        bucket["coverage_status"] = (
            "has_data" if bucket["data_points"] else
            "configured_no_snapshot" if registry_total else
            "configured_no_listing" if bucket["configured_links"] else
            "not_configured"
        )
        bucket["revenue"] = round(bucket["revenue"], 2)
    listing_total = conn.execute(
        f"SELECT COUNT(*) AS c FROM sales_listings WHERE {listing_where}",
        listing_params,
    ).fetchone()["c"]
    monitored = conn.execute(
        f"SELECT COUNT(*) AS c FROM sales_listings WHERE monitor = 1 AND status = 'active' AND {listing_where}",
        listing_params,
    ).fetchone()["c"]
    return {
        "channels": list(by_channel.values()),
        "trend": trend,
        "global_metrics": global_metrics,
        "by_product": by_product,
        "link_counts": [{"channel": r["channel"], "total": r["total"]} for r in links],
        "total_revenue": round(sum(c["revenue"] for c in by_channel.values()), 2),
        "total_units": sum(c["units"] for c in by_channel.values()),
        "revenue_points": sum(c["revenue_points"] for c in by_channel.values()),
        "units_points": sum(c["units_points"] for c in by_channel.values()),
        "data_points": len(metrics),
        "listing_total": listing_total,
        "monitored_listings": monitored,
    }
