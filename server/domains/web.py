"""Web snapshot monitoring: capture, offline archives, and visual analysis."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from .. import ai
from ..config import DEFAULT_CRAWL_LIMIT, SNAPSHOT_DIR
from ..fetchers import FetchError, fetch_page
from ..schemas import WebMonitorIn, WebMonitorUpdate
from ..snapshot import (
    analyze_change,
    capture_artifacts,
    compare_visuals,
    text_hash,
    visual_comparison_image,
)
from ..util import (
    canonical_url,
    clean_external_link,
    clean_text,
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
VISUAL_CHANGE_THRESHOLD = 0.025
TEXT_CHANGE_THRESHOLD = 0.15


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


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"无效日期：{value}") from exc


def _resolve_window(days: int = 30, start_date: str | None = None, end_date: str | None = None) -> tuple[str, str]:
    end = _parse_date(end_date, datetime.now(timezone.utc).date())
    start = _parse_date(start_date, end - timedelta(days=max(days - 1, 0)))
    if start > end:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
    return start.isoformat(), end.isoformat()


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


def snapshot_to_dict(row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    item["changes"] = json.loads(item.pop("changes_json", None) or "[]")
    item["visual_regions"] = json.loads(item.pop("visual_regions_json", None) or "[]")
    raw = json.loads(item.pop("raw_json", None) or "{}")
    item["capture_method"] = raw.get("method")
    item["archive_self_contained"] = bool((raw.get("archive") or {}).get("self_contained"))
    item["screenshot_url"] = f"/snapshots/{item['screenshot_path']}" if item.get("screenshot_path") else ""
    item["archive_url"] = f"/snapshots/{item['html_path']}" if item.get("html_path") else ""
    item["page_path"] = urlparse(item.get("final_url") or item.get("url") or "").path or "/"
    item["effective_change_score"] = _effective_score(item)
    item["has_meaningful_change"] = _has_meaningful_change(item)
    return item


def _effective_score(snapshot: dict) -> float:
    return round(max(float(snapshot.get("change_score") or 0), float(snapshot.get("visual_change_score") or 0)), 4)


def _has_meaningful_change(snapshot: dict) -> bool:
    return (
        float(snapshot.get("visual_change_score") or 0) >= VISUAL_CHANGE_THRESHOLD
        or float(snapshot.get("change_score") or 0) >= TEXT_CHANGE_THRESHOLD
    )


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
    previous_row = conn.execute(
        "SELECT * FROM web_snapshots WHERE monitor_id = ? AND page_key = ? ORDER BY created_at DESC LIMIT 1",
        (monitor["id"], key),
    ).fetchone()
    previous = dict(previous_row) if previous_row else None
    current = {
        "title": page.get("title"),
        "text": page.get("text"),
        "text_hash": text_hash(page.get("text", "")),
    }
    text_score, summary, changes = analyze_change(current, previous)
    base_name = f"{monitor['id']}_{new_id()[:8]}"
    screenshot, archive, capture_meta = capture_artifacts(
        page.get("final_url") or url,
        page.get("title", ""),
        page.get("text", ""),
        page.get("html", ""),
        base_name,
    )
    visual = compare_visuals(screenshot, previous.get("screenshot_path") if previous else None)
    if visual.get("available"):
        if visual.get("score", 0) >= VISUAL_CHANGE_THRESHOLD:
            summary += f" 视觉变化约 {visual['score'] * 100:.0f}%。"
        elif text_score >= TEXT_CHANGE_THRESHOLD:
            summary += " 截图视觉变化较小，主要差异来自文本。"
    archive_size = int((capture_meta.get("archive") or {}).get("archive_size") or 0)
    snapshot_id = new_id()
    conn.execute(
        """
        INSERT INTO web_snapshots (id, monitor_id, brand_id, snapshot_date, url, page_key,
            final_url, title, screenshot_path, html_path, archive_size, text_hash, text_excerpt,
            change_score, visual_change_score, visual_change_ratio, visual_regions_json, summary,
            changes_json, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id, monitor["id"], monitor.get("brand_id"), today(), url, key,
            page.get("final_url"), page.get("title"), screenshot, archive, archive_size,
            current["text_hash"], (page.get("text") or "")[:20000], text_score,
            visual.get("score") or 0, visual.get("ratio") or 0,
            json.dumps(visual.get("regions") or [], ensure_ascii=False), summary,
            json.dumps(changes, ensure_ascii=False), json.dumps(capture_meta, ensure_ascii=False), utc_now(),
        ),
    )
    return snapshot_to_dict(conn.execute("SELECT * FROM web_snapshots WHERE id = ?", (snapshot_id,)).fetchone())


def capture_monitor(conn: sqlite3.Connection, monitor: dict) -> list[dict]:
    urls = (
        _discover_pages(monitor["url"], monitor.get("crawl_limit") or DEFAULT_CRAWL_LIMIT)
        if monitor.get("scope") == "domain"
        else [monitor["url"]]
    )
    snapshots, errors = [], []
    for url in urls:
        try:
            snapshots.append(_capture_one(conn, monitor, url))
        except FetchError as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    top_change = max(((item.get("effective_change_score") or 0, item.get("summary") or "") for item in snapshots), default=(0, ""))
    now = utc_now()
    conn.execute(
        """
        UPDATE web_monitors
        SET last_check_at = ?, last_snapshot_at = ?, last_change_score = ?,
            last_change_summary = ?, last_status = ?, last_error = ?, updated_at = ?
        WHERE id = ?
        """,
        (now, now, top_change[0], top_change[1], "ok" if snapshots else "error", "; ".join(errors)[:500], now, monitor["id"]),
    )
    return snapshots


def check_monitor(conn: sqlite3.Connection, monitor: dict) -> dict:
    urls = (
        _discover_pages(monitor["url"], monitor.get("crawl_limit") or DEFAULT_CRAWL_LIMIT)
        if monitor.get("scope") == "domain"
        else [monitor["url"]]
    )
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


def _query_snapshots(
    conn: sqlite3.Connection,
    start: str,
    end: str,
    brand_id: str | None = None,
    monitor_id: str | None = None,
    descending: bool = True,
    limit: int = 1000,
) -> list[dict]:
    clauses = ["snapshot_date >= ?", "snapshot_date <= ?"]
    params: list = [start, end]
    if brand_id:
        clauses.append("brand_id = ?")
        params.append(brand_id)
    if monitor_id:
        clauses.append("monitor_id = ?")
        params.append(monitor_id)
    order = "DESC" if descending else "ASC"
    rows = conn.execute(
        f"SELECT * FROM web_snapshots WHERE {' AND '.join(clauses)} ORDER BY snapshot_date {order}, created_at {order} LIMIT ?",
        [*params, limit],
    ).fetchall()
    return [snapshot_to_dict(row) for row in rows]


def _period_stats(snapshots: list[dict], start: str, end: str) -> dict:
    start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    range_days = (end_day - start_day).days + 1
    changed = [snapshot for snapshot in snapshots if _has_meaningful_change(snapshot)]
    changed_dates = sorted({snapshot["snapshot_date"] for snapshot in changed})
    intervals = [
        (date.fromisoformat(right) - date.fromisoformat(left)).days
        for left, right in zip(changed_dates, changed_dates[1:])
    ]
    daily: dict[str, dict] = {}
    cursor = start_day
    while cursor <= end_day:
        day = cursor.isoformat()
        daily[day] = {"date": day, "captures": 0, "changed": 0, "severity": 0.0}
        cursor += timedelta(days=1)
    pages: dict[str, dict] = {}
    for snapshot in snapshots:
        day = daily.setdefault(snapshot["snapshot_date"], {"date": snapshot["snapshot_date"], "captures": 0, "changed": 0, "severity": 0.0})
        day["captures"] += 1
        score = _effective_score(snapshot)
        if _has_meaningful_change(snapshot):
            day["changed"] += 1
            day["severity"] = max(day["severity"], score)
        page = pages.setdefault(snapshot.get("page_path") or "/", {"page": snapshot.get("page_path") or "/", "captures": 0, "changed": 0, "total_score": 0.0})
        page["captures"] += 1
        if _has_meaningful_change(snapshot):
            page["changed"] += 1
            page["total_score"] += score
    page_activity = []
    for page in pages.values():
        page_activity.append({
            "page": page["page"],
            "captures": page["captures"],
            "changed": page["changed"],
            "average_severity": round(page["total_score"] / max(page["changed"], 1), 4),
        })
    page_activity.sort(key=lambda item: (item["changed"], item["average_severity"]), reverse=True)
    return {
        "start_date": start,
        "end_date": end,
        "range_days": range_days,
        "total_snapshots": len(snapshots),
        "changed": len(changed),
        "changed_days": len(changed_dates),
        "change_day_rate": round(len(changed_dates) / max(range_days, 1), 4),
        "capture_change_rate": round(len(changed) / max(len(snapshots), 1), 4),
        "average_interval_days": round(mean(intervals), 1) if intervals else None,
        "average_severity": round(mean(_effective_score(snapshot) for snapshot in changed), 4) if changed else 0.0,
        "major_changes": sum(1 for snapshot in changed if _effective_score(snapshot) >= 0.25),
        "daily": list(daily.values()),
        "page_activity": page_activity[:10],
        "highlights": sorted(changed, key=_effective_score, reverse=True)[:10],
    }


def _summary_payload(conn: sqlite3.Connection, brand_id: str | None, monitor_id: str | None, start: str, end: str) -> dict:
    snapshots = _query_snapshots(conn, start, end, brand_id, monitor_id)
    current = _period_stats(snapshots, start, end)
    start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    period_days = (end_day - start_day).days + 1
    previous_end = start_day - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    previous_snapshots = _query_snapshots(conn, previous_start.isoformat(), previous_end.isoformat(), brand_id, monitor_id)
    previous = _period_stats(previous_snapshots, previous_start.isoformat(), previous_end.isoformat())
    if current["changed_days"] > previous["changed_days"]:
        trend = "more_active"
    elif current["changed_days"] < previous["changed_days"]:
        trend = "more_stable"
    else:
        trend = "flat"
    if previous["changed"]:
        frequency_delta_pct = round((current["changed"] - previous["changed"]) / previous["changed"] * 100, 1)
    else:
        frequency_delta_pct = None if not current["changed"] else 100.0
    return {
        **current,
        "previous_period": previous,
        "comparison": {
            "trend": trend,
            "changed_delta": current["changed"] - previous["changed"],
            "changed_days_delta": current["changed_days"] - previous["changed_days"],
            "frequency_delta_pct": frequency_delta_pct,
            "severity_delta": round(current["average_severity"] - previous["average_severity"], 4),
        },
        "ai_configured": ai.is_configured(conn),
    }


@router.get("/monitors")
def list_monitors(brand_id: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute(
        "SELECT * FROM web_monitors WHERE (? IS NULL OR brand_id = ?) ORDER BY created_at DESC",
        (brand_id, brand_id),
    ).fetchall()
    return {"monitors": [monitor_to_dict(conn, row) for row in rows]}


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
            monitor_id, payload.brand_id, payload.product_id, payload.name or host_key(url), url,
            payload.scope, payload.crawl_limit, payload.cadence, payload.check_interval_minutes,
            payload.snapshot_interval_minutes, now, now,
        ),
    )
    monitor = dict(conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone())
    snapshots = capture_monitor(conn, monitor) if payload.capture_now else []
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
            "UPDATE web_monitors SET last_check_at = NULL, last_snapshot_at = NULL, last_change_score = 0, last_change_summary = NULL WHERE id = ?",
            (monitor_id,),
        )
    return monitor_to_dict(conn, conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone())


def _delete_snapshot_files(rows) -> None:
    root = SNAPSHOT_DIR.resolve()
    for row in rows:
        for filename in (row["screenshot_path"], row["html_path"]):
            if not filename:
                continue
            path = (SNAPSHOT_DIR / filename).resolve()
            if path.parent == root and path.is_file():
                path.unlink(missing_ok=True)


@router.delete("/monitors/{monitor_id}")
def delete_monitor(monitor_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    files = conn.execute(
        "SELECT screenshot_path, html_path FROM web_snapshots WHERE monitor_id = ?",
        (monitor_id,),
    ).fetchall()
    _delete_snapshot_files(files)
    conn.execute("DELETE FROM web_snapshot_analyses WHERE monitor_id = ?", (monitor_id,))
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


@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM web_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    item = dict(row)
    _delete_snapshot_files([item])
    conn.execute(
        "DELETE FROM web_snapshot_analyses WHERE monitor_id = ? OR brand_id = ?",
        (item.get("monitor_id"), item.get("brand_id")),
    )
    conn.execute("DELETE FROM web_snapshots WHERE id = ?", (snapshot_id,))
    return {"deleted": snapshot_id, "monitor_id": item.get("monitor_id")}


@router.get("/snapshots")
def list_snapshots(
    brand_id: str | None = None,
    monitor_id: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = _resolve_window(days, start_date, end_date)
    return {"snapshots": _query_snapshots(conn, start, end, brand_id, monitor_id, limit=500), "range": {"start": start, "end": end}}


@router.get("/summary")
def web_summary(
    brand_id: str | None = None,
    monitor_id: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = _resolve_window(days, start_date, end_date)
    return _summary_payload(conn, brand_id, monitor_id, start, end)


@router.post("/analyze")
def analyze_snapshots(
    brand_id: str | None = None,
    monitor_id: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    refresh: bool = False,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = _resolve_window(days, start_date, end_date)
    snapshots = _query_snapshots(conn, start, end, brand_id, monitor_id, descending=False)
    if not snapshots:
        raise HTTPException(status_code=400, detail="所选时间范围内没有网页快照")
    snapshot_ids = [snapshot["id"] for snapshot in snapshots]
    if not refresh:
        cached = conn.execute(
            """
            SELECT * FROM web_snapshot_analyses
            WHERE brand_id IS ? AND monitor_id IS ? AND start_date = ? AND end_date = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (brand_id, monitor_id, start, end),
        ).fetchone()
        if cached and json.loads(cached["snapshot_ids_json"] or "[]") == snapshot_ids:
            result = json.loads(cached["result_json"] or "{}")
            result.update({"cached": True, "created_at": cached["created_at"], "analysis_id": cached["id"]})
            return result

    summary = _summary_payload(conn, brand_id, monitor_id, start, end)
    candidates = sorted(
        [snapshot for snapshot in snapshots if _has_meaningful_change(snapshot)],
        key=_effective_score,
        reverse=True,
    )[:6]
    events = []
    for snapshot in candidates:
        previous = conn.execute(
            """
            SELECT * FROM web_snapshots
            WHERE monitor_id = ? AND page_key = ? AND created_at < ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (snapshot["monitor_id"], canonical_url(snapshot.get("final_url") or snapshot.get("url") or ""), snapshot["created_at"]),
        ).fetchone()
        if not previous or not previous["screenshot_path"] or not snapshot.get("screenshot_path"):
            continue
        events.append({
            "date": snapshot["snapshot_date"],
            "page": snapshot.get("page_path") or "/",
            "visual_score": snapshot.get("visual_change_score") or 0,
            "text_score": snapshot.get("change_score") or 0,
            "text_changes": snapshot.get("changes") or [],
            "image": visual_comparison_image(previous["screenshot_path"], snapshot["screenshot_path"], snapshot.get("visual_regions") or []),
        })

    monitor_name = "全部网页监控"
    if monitor_id:
        monitor = conn.execute("SELECT name FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone()
        monitor_name = monitor["name"] if monitor else monitor_name
    if not events:
        result = {
            "summary": "所选时间范围内没有检测到足够明确的视觉或文本变化。",
            "highlights": [],
            "change_categories": [],
            "major_events": [],
            "frequency_assessment": "当前数据不足以形成可靠的变化频率判断。",
            "business_signals": [],
            "caveats": ["至少需要两张可正常读取的同页面截图才能进行视觉对比。"],
            "model": "deterministic",
        }
    else:
        if not ai.is_configured(conn):
            raise HTTPException(status_code=400, detail="尚未配置支持图片输入的大模型 Token，请先在设置中填写。")
        try:
            compact_stats = {
                "range_days": summary["range_days"],
                "total_snapshots": summary["total_snapshots"],
                "changed": summary["changed"],
                "changed_days": summary["changed_days"],
                "change_day_rate": summary["change_day_rate"],
                "average_interval_days": summary["average_interval_days"],
                "average_severity": summary["average_severity"],
                "major_changes": summary["major_changes"],
                "comparison": summary["comparison"],
                "previous_period": {
                    "start_date": summary["previous_period"]["start_date"],
                    "end_date": summary["previous_period"]["end_date"],
                    "total_snapshots": summary["previous_period"]["total_snapshots"],
                    "changed": summary["previous_period"]["changed"],
                    "changed_days": summary["previous_period"]["changed_days"],
                    "average_interval_days": summary["previous_period"]["average_interval_days"],
                    "average_severity": summary["previous_period"]["average_severity"],
                },
            }
            result = ai.analyze_web_snapshots(
                conn,
                events,
                {"start": start, "end": end, "monitor": monitor_name, "stats": compact_stats},
            )
        except ai.LlmError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    analysis_id = new_id()
    created_at = utc_now()
    conn.execute(
        """
        INSERT INTO web_snapshot_analyses
            (id, brand_id, monitor_id, start_date, end_date, snapshot_ids_json, result_json, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            analysis_id, brand_id, monitor_id, start, end,
            json.dumps(snapshot_ids, ensure_ascii=False), json.dumps(result, ensure_ascii=False), result.get("model"), created_at,
        ),
    )
    result.update({"cached": False, "created_at": created_at, "analysis_id": analysis_id, "range": {"start": start, "end": end}})
    return result
