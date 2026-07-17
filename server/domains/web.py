"""Web snapshot monitoring: monitors, capture, snapshots, change summaries."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from ..config import DEFAULT_CRAWL_LIMIT
from ..fetchers import FetchError, fetch_page
from ..schemas import WebMonitorIn, WebMonitorUpdate
from ..snapshot import analyze_change, capture_image, text_hash
from ..util import (
    canonical_url,
    clean_external_link,
    host_key,
    is_html_like_url,
    new_id,
    normalize_url,
    today,
    utc_now,
)
from .common import get_conn

router = APIRouter(prefix="/api/web", tags=["web"])

DEFAULT_INTERVAL_MINUTES = 1440


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _interval_minutes(monitor: dict, kind: str) -> int:
    field = "check_interval_minutes" if kind == "check" else "snapshot_interval_minutes"
    try:
        return max(60, min(int(monitor.get(field) or DEFAULT_INTERVAL_MINUTES), 43200))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MINUTES


def next_run_at(monitor: dict, kind: str) -> datetime:
    last_field = "last_check_at" if kind == "check" else "last_snapshot_at"
    last_value = monitor.get(last_field)
    if kind == "check" and not last_value:
        last_value = monitor.get("last_snapshot_at")
    base = _parse_datetime(last_value) or _parse_datetime(monitor.get("created_at")) or datetime.now(timezone.utc)
    return base + timedelta(minutes=_interval_minutes(monitor, kind))


def monitor_is_due(monitor: dict, kind: str, now: datetime | None = None) -> bool:
    if monitor.get("status") != "active":
        return False
    return next_run_at(monitor, kind) <= (now or datetime.now(timezone.utc))


def snapshot_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["changes"] = json.loads(item.pop("changes_json") or "[]")
    item.pop("raw_json", None)
    item["screenshot_url"] = f"/snapshots/{item['screenshot_path']}" if item.get("screenshot_path") else ""
    item["page_path"] = urlparse(item.get("final_url") or item.get("url") or "").path or "/"
    return item


def monitor_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    monitor = dict(row)
    last = conn.execute(
        "SELECT COUNT(*) AS c, MAX(snapshot_date) AS d FROM web_snapshots WHERE monitor_id = ?",
        (monitor["id"],),
    ).fetchone()
    monitor["snapshot_count"] = last["c"]
    monitor["latest_snapshot_date"] = last["d"]
    if monitor.get("status") == "active":
        now = datetime.now(timezone.utc)
        next_check = next_run_at(monitor, "check")
        next_snapshot = next_run_at(monitor, "snapshot")
        monitor["next_check_at"] = next_check.isoformat()
        monitor["next_snapshot_at"] = next_snapshot.isoformat()
        monitor["seconds_until_check"] = max(0, int((next_check - now).total_seconds()))
        monitor["seconds_until_snapshot"] = max(0, int((next_snapshot - now).total_seconds()))
    else:
        monitor["next_check_at"] = None
        monitor["next_snapshot_at"] = None
        monitor["seconds_until_check"] = None
        monitor["seconds_until_snapshot"] = None
    return monitor


def _discover_pages(start_url: str, limit: int) -> list[str]:
    limit = max(1, min(limit or DEFAULT_CRAWL_LIMIT, 60))
    start = canonical_url(start_url) or normalize_url(start_url)
    root_host = host_key(start)
    queue, seen, discovered = [start], {start}, []
    while queue and len(discovered) < limit:
        current = queue.pop(0)
        discovered.append(current)
        try:
            page = fetch_page(current)
        except FetchError:
            continue
        for href in page.get("anchors", []):
            candidate = clean_external_link(page.get("final_url") or current, href)
            if (
                candidate
                and host_key(candidate) == root_host
                and is_html_like_url(candidate)
                and candidate not in seen
                and len(seen) < limit
            ):
                seen.add(candidate)
                queue.append(candidate)
    return discovered[:limit]


def _capture_one(conn: sqlite3.Connection, monitor: dict, url: str) -> dict:
    page = fetch_page(url)
    key = canonical_url(page.get("final_url") or url)
    previous = conn.execute(
        "SELECT * FROM web_snapshots WHERE monitor_id = ? AND page_key = ? ORDER BY created_at DESC LIMIT 1",
        (monitor["id"], key),
    ).fetchone()
    current = {
        "title": page.get("title"),
        "text": page.get("text"),
        "text_hash": text_hash(page.get("text", "")),
    }
    score, summary, changes = analyze_change(current, dict(previous) if previous else None)
    base_name = f"{monitor['id']}_{new_id()[:8]}"
    screenshot, _meta = capture_image(page.get("final_url") or url, page.get("title", ""), page.get("text", ""), base_name)
    snapshot_id = new_id()
    conn.execute(
        """
        INSERT INTO web_snapshots (id, monitor_id, brand_id, snapshot_date, url, page_key,
            final_url, title, screenshot_path, text_hash, text_excerpt, change_score, summary,
            changes_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id, monitor["id"], monitor.get("brand_id"), today(), url, key,
            page.get("final_url"), page.get("title"), screenshot, current["text_hash"],
            (page.get("text") or "")[:20000], score, summary,
            json.dumps(changes, ensure_ascii=False), utc_now(),
        ),
    )
    return snapshot_to_dict(conn.execute("SELECT * FROM web_snapshots WHERE id = ?", (snapshot_id,)).fetchone())


def capture_monitor(conn: sqlite3.Connection, monitor: dict) -> list[dict]:
    if monitor.get("scope") == "domain":
        urls = _discover_pages(monitor["url"], monitor.get("crawl_limit") or DEFAULT_CRAWL_LIMIT)
    else:
        urls = [monitor["url"]]
    snapshots, error = [], ""
    for url in urls:
        try:
            snapshots.append(_capture_one(conn, monitor, url))
        except FetchError as exc:
            error = str(exc)
    now = utc_now()
    top_change = max(((item.get("change_score") or 0, item.get("summary") or "") for item in snapshots), default=(0, ""))
    conn.execute(
        """
        UPDATE web_monitors
        SET last_check_at = ?, last_snapshot_at = ?, last_change_score = ?,
            last_change_summary = ?, last_status = ?, last_error = ?, updated_at = ?
        WHERE id = ?
        """,
        (now, now, top_change[0], top_change[1], "ok" if snapshots else "error", error, now, monitor["id"]),
    )
    return snapshots


def check_monitor(conn: sqlite3.Connection, monitor: dict) -> dict:
    if monitor.get("scope") == "domain":
        urls = _discover_pages(monitor["url"], monitor.get("crawl_limit") or DEFAULT_CRAWL_LIMIT)
    else:
        urls = [monitor["url"]]

    checked = 0
    changed = 0
    max_score = 0.0
    errors: list[str] = []
    for url in urls:
        try:
            page = fetch_page(url)
            key = canonical_url(page.get("final_url") or url)
            previous = conn.execute(
                "SELECT * FROM web_snapshots WHERE monitor_id = ? AND page_key = ? ORDER BY created_at DESC LIMIT 1",
                (monitor["id"], key),
            ).fetchone()
            current = {
                "title": page.get("title"),
                "text": page.get("text"),
                "text_hash": text_hash(page.get("text", "")),
            }
            score, _summary, changes = analyze_change(current, dict(previous) if previous else None)
            checked += 1
            max_score = max(max_score, score)
            if score >= 0.02 or changes:
                changed += 1
        except FetchError as exc:
            errors.append(str(exc))

    if changed:
        summary = f"{changed} 个页面相对最近快照发生变化"
    elif checked:
        summary = "未发现相对最近快照的可见文本变化"
    else:
        summary = "页面检查失败"
    now = utc_now()
    conn.execute(
        """
        UPDATE web_monitors
        SET last_check_at = ?, last_change_score = ?, last_change_summary = ?,
            last_status = ?, last_error = ?, updated_at = ?
        WHERE id = ?
        """,
        (now, max_score, summary, "ok" if checked else "error", "; ".join(errors)[:500], now, monitor["id"]),
    )
    return {"checked": checked, "changed": changed, "change_score": max_score, "summary": summary}


@router.get("/monitors")
def list_monitors(brand_id: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute(
        "SELECT * FROM web_monitors WHERE (? IS NULL OR brand_id = ?) ORDER BY created_at DESC",
        (brand_id, brand_id),
    ).fetchall()
    return {"monitors": [monitor_to_dict(conn, r) for r in rows]}


@router.post("/monitors", status_code=201)
def create_monitor(payload: WebMonitorIn, conn: sqlite3.Connection = Depends(get_conn)):
    url = normalize_url(payload.url)
    now = utc_now()
    monitor_id = new_id()
    conn.execute(
        """
        INSERT INTO web_monitors (id, brand_id, product_id, name, url, scope, crawl_limit,
            cadence, check_interval_minutes, snapshot_interval_minutes, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (
            monitor_id, payload.brand_id, payload.product_id,
            payload.name or host_key(url), url, payload.scope, payload.crawl_limit,
            payload.cadence, payload.check_interval_minutes, payload.snapshot_interval_minutes, now, now,
        ),
    )
    monitor = dict(conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone())
    snapshots = []
    if payload.capture_now:
        snapshots = capture_monitor(conn, monitor)
    result = monitor_to_dict(conn, conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone())
    result["snapshots"] = snapshots
    return result


@router.put("/monitors/{monitor_id}")
def update_monitor(monitor_id: str, payload: WebMonitorUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    existing = conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Monitor not found")
    existing_data = dict(existing)
    update_data = payload.model_dump(exclude_none=True)
    if "url" in update_data:
        try:
            update_data["url"] = normalize_url(update_data["url"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    data = {**existing_data, **update_data, "updated_at": utc_now()}
    data["name"] = clean_text(data.get("name")) or host_key(data["url"])
    conn.execute(
        """
        UPDATE web_monitors
        SET name = ?, url = ?, status = ?, scope = ?, crawl_limit = ?, cadence = ?,
            check_interval_minutes = ?, snapshot_interval_minutes = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            data["name"], data["url"], data["status"], data["scope"], data["crawl_limit"], data["cadence"],
            data["check_interval_minutes"], data["snapshot_interval_minutes"], data["updated_at"], monitor_id,
        ),
    )
    if data["url"] != existing_data["url"]:
        conn.execute(
            """
            UPDATE web_monitors
            SET last_check_at = NULL, last_snapshot_at = NULL, last_change_score = 0,
                last_change_summary = NULL
            WHERE id = ?
            """,
            (monitor_id,),
        )
    return monitor_to_dict(conn, conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone())


@router.delete("/monitors/{monitor_id}")
def delete_monitor(monitor_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("DELETE FROM web_snapshots WHERE monitor_id = ?", (monitor_id,))
    conn.execute("DELETE FROM web_monitors WHERE id = ?", (monitor_id,))
    return {"deleted": monitor_id}


@router.post("/monitors/{monitor_id}/capture")
def capture(monitor_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Monitor not found")
    snapshots = capture_monitor(conn, dict(row))
    return {"snapshots": snapshots, "captured": len(snapshots)}


@router.get("/snapshots")
def list_snapshots(
    brand_id: str | None = None,
    monitor_id: str | None = None,
    days: int = 30,
    conn: sqlite3.Connection = Depends(get_conn),
):
    clauses, params = [], []
    if brand_id:
        clauses.append("brand_id = ?")
        params.append(brand_id)
    if monitor_id:
        clauses.append("monitor_id = ?")
        params.append(monitor_id)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days - 1, 0))).date().isoformat()
    clauses.append("snapshot_date >= ?")
    params.append(cutoff)
    where = " WHERE " + " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM web_snapshots{where} ORDER BY snapshot_date DESC, created_at DESC LIMIT 300", params
    ).fetchall()
    return {"snapshots": [snapshot_to_dict(r) for r in rows]}


@router.get("/summary")
def web_summary(brand_id: str | None = None, days: int = 30, conn: sqlite3.Connection = Depends(get_conn)):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days - 1, 0))).date().isoformat()
    rows = conn.execute(
        "SELECT * FROM web_snapshots WHERE (? IS NULL OR brand_id = ?) AND snapshot_date >= ? ORDER BY snapshot_date DESC",
        (brand_id, brand_id, cutoff),
    ).fetchall()
    snapshots = [snapshot_to_dict(r) for r in rows]
    changed = [s for s in snapshots if (s.get("change_score") or 0) >= 0.15]
    daily: dict[str, dict] = {}
    for snap in snapshots:
        day = daily.setdefault(snap["snapshot_date"], {"date": snap["snapshot_date"], "captures": 0, "changed": 0})
        day["captures"] += 1
        if (snap.get("change_score") or 0) >= 0.15:
            day["changed"] += 1
    return {
        "total_snapshots": len(snapshots),
        "changed": len(changed),
        "highlights": changed[:10],
        "daily": sorted(daily.values(), key=lambda i: i["date"]),
    }
