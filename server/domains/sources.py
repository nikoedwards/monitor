"""Connector control console: list sources + trigger collection + monitoring status."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..config import CREDENTIALS, SCHEDULER_ENABLED, SCHEDULER_SECONDS
from ..connectors.base import run_collector
from ..connectors.registry import REGISTRY, get_spec
from .common import fetch_brand, get_conn

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


@router.get("")
def list_sources(conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM sources ORDER BY tier, category, name").fetchall()
    sources = []
    for row in rows:
        item = dict(row)
        item["needs_credentials"] = bool(item.get("needs_credentials"))
        sources.append(item)
    credentials = {key: bool(value) for key, value in CREDENTIALS.items()}
    return {"sources": sources, "credentials": credentials}


@router.post("/{source_id}/collect")
def collect_source(source_id: str, brand_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    spec = get_spec(source_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Source not found")
    brand = fetch_brand(conn, brand_id)
    result = run_collector(conn, spec, brand)
    return result


@router.get("/status")
def monitoring_status(
    brand_id: str | None = None,
    dimension: str = "marketing",
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Per-dimension monitoring status: which connectors run, last/next run, scheduler mode."""
    specs = [s for s in REGISTRY if s.dimension == dimension]
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM sources").fetchall()}
    items: list[dict] = []
    last_run: datetime | None = None
    for spec in specs:
        row = rows.get(spec.id, {})
        last_collect_at = row.get("last_collect_at")
        parsed = _parse_dt(last_collect_at)
        if parsed and (last_run is None or parsed > last_run):
            last_run = parsed
        items.append({
            "id": spec.id,
            "name": spec.name,
            "category": spec.category,
            "sync_mode": spec.sync_mode,
            "status": spec.status,
            "cadence": spec.cadence,
            "last_collect_at": last_collect_at,
            "last_status": row.get("last_status"),
            "item_count": row.get("item_count") or 0,
        })

    interval = SCHEDULER_SECONDS
    # Marketing is connector-driven; sales is link-driven (scheduler runs it daily).
    has_active = any(s.status == "ready" and s.collect is not None for s in specs)
    next_run: datetime | None = None
    if SCHEDULER_ENABLED and (has_active or dimension == "sales"):
        now = datetime.now(timezone.utc)
        if dimension == "sales":
            next_run = (last_run or now) + timedelta(days=1)
        elif last_run:
            candidate = last_run + timedelta(seconds=interval)
            next_run = candidate if candidate > now else now + timedelta(seconds=interval)
        else:
            next_run = now + timedelta(seconds=interval)

    return {
        "dimension": dimension,
        "scheduler": {"enabled": SCHEDULER_ENABLED, "interval_seconds": interval},
        "sources": items,
        "ready_count": sum(1 for s in specs if s.status == "ready"),
        "source_count": len(specs),
        "last_run": last_run.isoformat() if last_run else None,
        "next_run_estimate": next_run.isoformat() if next_run else None,
    }


@router.post("/refresh")
def monitoring_refresh(
    brand_id: str,
    dimension: str = "marketing",
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Run all ready connectors for a dimension now (manual refresh button)."""
    brand = fetch_brand(conn, brand_id)
    if dimension == "sales":
        from ..connectors.sales.runner import run_sales_collection

        summary = run_sales_collection(conn, brand)
        return {"dimension": dimension, "results": summary}

    results: list[dict] = []
    created = 0
    for spec in REGISTRY:
        if spec.dimension != dimension or spec.collect is None or spec.status != "ready":
            continue
        res = run_collector(conn, spec, brand)
        created += res.get("created", 0)
        results.append(res)
    return {"dimension": dimension, "created": created, "results": results}
