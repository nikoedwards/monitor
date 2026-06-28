"""Sales metrics (time-series), listing registry, manual entry, and summaries."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException

from .. import ai
from ..connectors.sales.runner import run_sales_collection
from ..schemas import SalesListingUpdate, SalesMetricIn
from ..util import new_id, today, utc_now
from .common import fetch_brand, get_conn, resolve_window

router = APIRouter(prefix="/api/sales", tags=["sales"])

CHANNELS = ["amazon", "dtc", "other_ecom", "offline"]


def metric_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item.pop("raw_json", None)
    item["in_stock"] = bool(item["in_stock"]) if item.get("in_stock") is not None else None
    item["changes"] = json.loads(item.pop("changes_json", None) or "[]")
    return item


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
    conn.execute(
        """
        INSERT INTO sales_metrics (id, link_id, brand_id, product_id, snapshot_date, channel,
            platform, price, currency, review_count, rating, rank, units_est, revenue_est,
            in_stock, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metric_id, payload.link_id, payload.brand_id, payload.product_id,
            payload.snapshot_date or today(), payload.channel, payload.platform, payload.price,
            payload.currency, payload.review_count, payload.rating, payload.rank,
            payload.units_est, payload.revenue_est,
            None if payload.in_stock is None else int(payload.in_stock), payload.source, utc_now(),
        ),
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

    by_channel: dict[str, dict] = {c: {"channel": c, "revenue": 0.0, "units": 0, "data_points": 0} for c in CHANNELS}
    trend_map: dict[str, dict] = defaultdict(lambda: {"date": "", "revenue": 0.0, "units": 0})
    product_map: dict[str, dict] = defaultdict(lambda: {"product_id": None, "revenue": 0.0, "units": 0, "listings": 0})
    for m in metrics:
        channel = m["channel"] if m["channel"] in by_channel else "other_ecom"
        bucket = by_channel.setdefault(channel, {"channel": channel, "revenue": 0.0, "units": 0, "data_points": 0})
        bucket["revenue"] += m.get("revenue_est") or 0.0
        bucket["units"] += m.get("units_est") or 0
        bucket["data_points"] += 1
        day = trend_map[m["snapshot_date"]]
        day["date"] = m["snapshot_date"]
        day["revenue"] += m.get("revenue_est") or 0.0
        day["units"] += m.get("units_est") or 0
        pid = m.get("product_id") or "__unmapped__"
        pbucket = product_map[pid]
        pbucket["product_id"] = m.get("product_id")
        pbucket["revenue"] += m.get("revenue_est") or 0.0
        pbucket["units"] += m.get("units_est") or 0

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

    links = conn.execute(
        "SELECT channel, COUNT(*) AS total FROM links WHERE dimension = 'sales' AND (? IS NULL OR brand_id = ?) GROUP BY channel",
        (brand_id, brand_id),
    ).fetchall()
    listing_total = conn.execute(
        "SELECT COUNT(*) AS c FROM sales_listings WHERE (? IS NULL OR brand_id = ?)",
        (brand_id, brand_id),
    ).fetchone()["c"]
    monitored = conn.execute(
        "SELECT COUNT(*) AS c FROM sales_listings WHERE monitor = 1 AND status = 'active' AND (? IS NULL OR brand_id = ?)",
        (brand_id, brand_id),
    ).fetchone()["c"]
    return {
        "channels": list(by_channel.values()),
        "trend": sorted(trend_map.values(), key=lambda i: i["date"]),
        "by_product": by_product,
        "link_counts": [{"channel": r["channel"], "total": r["total"]} for r in links],
        "total_revenue": round(sum(c["revenue"] for c in by_channel.values()), 2),
        "total_units": sum(c["units"] for c in by_channel.values()),
        "data_points": len(metrics),
        "listing_total": listing_total,
        "monitored_listings": monitored,
    }
