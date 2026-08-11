"""Hiring intelligence: job posting registry, daily snapshots, employee feed.

Each target company is a brand. Hiring source links live in `links` with
dimension='hiring' and platform in (boss | linkedin | linkedin_people).
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from io import BytesIO
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .. import ai
from ..config import ROOT
from ..connectors.hiring.runner import (
    discover_linkedin_people_candidates,
    ingest_browser_hiring_capture,
    run_hiring_collection,
    run_linkedin_people_collection,
)
from ..schemas import (
    BrowserHiringCaptureIn,
    LinkedInMonitorSelectionIn,
    LinkedInProfileIn,
    LinkedInProfileUpdate,
)
from ..util import canonical_url, clean_text, new_id, normalize_url, utc_now
from .common import fetch_brand, get_conn, resolve_window

router = APIRouter(prefix="/api/hiring", tags=["hiring"])

PLATFORM_LABEL = {"boss": "Boss 直聘", "linkedin": "LinkedIn", "linkedin_people": "LinkedIn 员工"}


def posting_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = dict(row)
    item.pop("config_json", None)
    item["business_tags"] = json.loads(item.pop("business_tags_json", None) or "[]")
    latest = conn.execute(
        "SELECT * FROM job_snapshots WHERE posting_id = ? ORDER BY snapshot_date DESC, created_at DESC LIMIT 1",
        (item["id"],),
    ).fetchone()
    item["latest"] = snapshot_to_dict(latest) if latest else None
    item["data_points"] = conn.execute(
        "SELECT COUNT(*) AS c FROM job_snapshots WHERE posting_id = ?", (item["id"],)
    ).fetchone()["c"]
    item["has_change"] = bool(item.get("last_change_at"))
    return item


def snapshot_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item.pop("raw_json", None)
    item["is_open"] = bool(item["is_open"]) if item.get("is_open") is not None else None
    item["changes"] = json.loads(item.pop("changes_json", None) or "[]")
    return item


def profile_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = dict(row)
    item.pop("raw_json", None)
    item["monitor"] = bool(item.get("monitor"))
    item["activity_count"] = conn.execute(
        "SELECT COUNT(*) AS c FROM linkedin_activities WHERE profile_id = ?", (item["id"],)
    ).fetchone()["c"]
    item["snapshot_count"] = conn.execute(
        "SELECT COUNT(*) AS c FROM linkedin_profile_snapshots WHERE profile_id = ?", (item["id"],)
    ).fetchone()["c"]
    item["change_count"] = conn.execute(
        """
        SELECT COUNT(*) AS c FROM linkedin_profile_snapshots
        WHERE profile_id = ? AND changes_json IS NOT NULL AND changes_json != '' AND changes_json != '[]'
        """,
        (item["id"],),
    ).fetchone()["c"]
    latest = conn.execute(
        "SELECT * FROM linkedin_profile_snapshots WHERE profile_id = ? ORDER BY snapshot_date DESC, created_at DESC LIMIT 1",
        (item["id"],),
    ).fetchone()
    item["latest_snapshot"] = profile_snapshot_to_dict(latest) if latest else None
    return item


def profile_snapshot_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item.pop("raw_json", None)
    item["changes"] = json.loads(item.pop("changes_json", None) or "[]")
    return item


# ------------------------------------------------------------------- postings
@router.get("/postings")
def list_postings(
    brand_id: str,
    platform: str | None = None,
    status: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    clauses, params = ["brand_id = ?"], [brand_id]
    if platform and platform != "all":
        clauses.append("platform = ?")
        params.append(platform)
    if status and status != "all":
        clauses.append("status = ?")
        params.append(status)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM job_postings WHERE {where} ORDER BY last_change_at DESC, last_seen DESC LIMIT 500",
        params,
    ).fetchall()
    return {"postings": [posting_to_dict(conn, r) for r in rows]}


@router.get("/postings/{posting_id}/history")
def posting_history(posting_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    posting = conn.execute("SELECT * FROM job_postings WHERE id = ?", (posting_id,)).fetchone()
    if not posting:
        raise HTTPException(status_code=404, detail="Posting not found")
    rows = conn.execute(
        "SELECT * FROM job_snapshots WHERE posting_id = ? ORDER BY snapshot_date",
        (posting_id,),
    ).fetchall()
    snapshots = [snapshot_to_dict(r) for r in rows]
    changes = [
        {"date": s["snapshot_date"], "changes": s["changes"]}
        for s in snapshots
        if s.get("changes")
    ]
    return {
        "posting": posting_to_dict(conn, posting),
        "jd_text": posting["jd_text"],
        "snapshots": snapshots,
        "changes": list(reversed(changes)),
    }


@router.delete("/postings/{posting_id}")
def delete_posting(posting_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("DELETE FROM job_snapshots WHERE posting_id = ?", (posting_id,))
    conn.execute("DELETE FROM job_postings WHERE id = ?", (posting_id,))
    return {"deleted": posting_id}


# ----------------------------------------------------------------- collection
@router.post("/sync")
def sync_hiring(brand_id: str, link_id: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    brand = fetch_brand(conn, brand_id)
    return run_hiring_collection(conn, brand, link_id=link_id)


@router.post("/browser-capture")
def browser_capture(payload: BrowserHiringCaptureIn, conn: sqlite3.Connection = Depends(get_conn)):
    brand = fetch_brand(conn, payload.brand_id)
    try:
        return ingest_browser_hiring_capture(
            conn,
            brand,
            platform=payload.platform,
            source_url=payload.source_url,
            source_title=payload.source_title or "",
            page_status=payload.page_status,
            page_error=payload.page_error or "",
            jobs=[item.model_dump() for item in payload.jobs],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/browser-helper.zip")
def download_browser_helper():
    helper_dir = ROOT / "browser_extensions" / "hiring_capture"
    if not helper_dir.is_dir():
        raise HTTPException(status_code=404, detail="浏览器助手文件未部署。")
    files = [path for path in helper_dir.iterdir() if path.is_file()]
    if not files:
        raise HTTPException(status_code=404, detail="浏览器助手文件未部署。")
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=f"monitor-hiring-capture/{path.name}")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="monitor-hiring-capture.zip"'},
    )


@router.post("/employees/sync")
def sync_employees(brand_id: str, link_id: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    brand = fetch_brand(conn, brand_id)
    return run_linkedin_people_collection(conn, brand, link_id=link_id)


@router.post("/employees/import")
def import_company_employees(
    brand_id: str,
    link_id: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    brand = fetch_brand(conn, brand_id)
    summary = discover_linkedin_people_candidates(conn, brand, link_id=link_id)
    rows = conn.execute(
        """
        SELECT * FROM linkedin_profiles
        WHERE brand_id = ? AND source_type = 'company'
        ORDER BY monitor DESC, name, external_id
        LIMIT 300
        """,
        (brand_id,),
    ).fetchall()
    return {**summary, "candidates": [profile_to_dict(conn, row) for row in rows]}


# -------------------------------------------------------------------- employees
@router.get("/employees")
def list_employees(
    brand_id: str,
    monitor: bool | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    clauses, params = ["brand_id = ?"], [brand_id]
    if monitor is not None:
        clauses.append("monitor = ?")
        params.append(int(monitor))
    rows = conn.execute(
        f"""
        SELECT * FROM linkedin_profiles WHERE {' AND '.join(clauses)}
        ORDER BY monitor DESC, last_profile_change_at DESC, last_activity_at DESC, last_seen DESC LIMIT 300
        """,
        params,
    ).fetchall()
    return {"employees": [profile_to_dict(conn, r) for r in rows]}


@router.put("/employees/monitor-selection")
def update_employee_monitor_selection(
    payload: LinkedInMonitorSelectionIn,
    conn: sqlite3.Connection = Depends(get_conn),
):
    fetch_brand(conn, payload.brand_id)
    selected_ids = list(dict.fromkeys(payload.profile_ids))
    if selected_ids:
        placeholders = ",".join("?" for _ in selected_ids)
        rows = conn.execute(
            f"""
            SELECT id FROM linkedin_profiles
            WHERE brand_id = ? AND source_type = 'company' AND id IN ({placeholders})
            """,
            (payload.brand_id, *selected_ids),
        ).fetchall()
        if len(rows) != len(selected_ids):
            raise HTTPException(status_code=400, detail="选择中包含不属于当前企业候选池的人员。")

    now = utc_now()
    conn.execute(
        "UPDATE linkedin_profiles SET monitor = 0, updated_at = ? WHERE brand_id = ? AND source_type = 'company'",
        (now, payload.brand_id),
    )
    if selected_ids:
        placeholders = ",".join("?" for _ in selected_ids)
        conn.execute(
            f"UPDATE linkedin_profiles SET monitor = 1, status = 'active', updated_at = ? WHERE id IN ({placeholders})",
            (now, *selected_ids),
        )
    return {"selected": len(selected_ids)}


def _validated_profile_url(value: str) -> tuple[str, str, str]:
    try:
        url = normalize_url(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="请输入有效的 LinkedIn 个人主页 URL。") from exc
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")) or "/in/" not in parsed.path.lower():
        raise HTTPException(status_code=400, detail="重点人员目前仅支持 LinkedIn /in/ 个人主页 URL。")
    canon = canonical_url(url)
    slug = parsed.path.strip("/").split("/")[-1]
    return url, canon, slug


@router.post("/employees")
def create_employee(payload: LinkedInProfileIn, conn: sqlite3.Connection = Depends(get_conn)):
    fetch_brand(conn, payload.brand_id)
    url, canon, external_id = _validated_profile_url(payload.profile_url)
    existing = conn.execute(
        "SELECT * FROM linkedin_profiles WHERE brand_id = ? AND canonical_url = ? LIMIT 1",
        (payload.brand_id, canon),
    ).fetchone()
    now = utc_now()
    if existing:
        conn.execute(
            """
            UPDATE linkedin_profiles
            SET source_type = 'manual', profile_url = ?, external_id = COALESCE(NULLIF(?, ''), external_id),
                name = COALESCE(NULLIF(?, ''), name), headline = COALESCE(NULLIF(?, ''), headline),
                title = COALESCE(NULLIF(?, ''), title), notes = COALESCE(?, notes),
                monitor = ?, status = 'active', updated_at = ?
            WHERE id = ?
            """,
            (
                url, external_id, clean_text(payload.name), clean_text(payload.headline),
                clean_text(payload.title), payload.notes, int(payload.monitor), now, existing["id"],
            ),
        )
        row = conn.execute("SELECT * FROM linkedin_profiles WHERE id = ?", (existing["id"],)).fetchone()
        return profile_to_dict(conn, row)

    profile_id = new_id()
    conn.execute(
        """
        INSERT INTO linkedin_profiles (id, brand_id, link_id, source_type, external_id, name,
            headline, title, notes, profile_url, canonical_url, status, monitor, first_seen,
            created_at, updated_at)
        VALUES (?, ?, NULL, 'manual', ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
        """,
        (
            profile_id, payload.brand_id, external_id, clean_text(payload.name),
            clean_text(payload.headline), clean_text(payload.title), payload.notes,
            url, canon, int(payload.monitor), now, now, now,
        ),
    )
    return profile_to_dict(conn, conn.execute("SELECT * FROM linkedin_profiles WHERE id = ?", (profile_id,)).fetchone())


@router.put("/employees/{profile_id}")
def update_employee(
    profile_id: str,
    payload: LinkedInProfileUpdate,
    conn: sqlite3.Connection = Depends(get_conn),
):
    existing = conn.execute("SELECT * FROM linkedin_profiles WHERE id = ?", (profile_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Profile not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("status") not in (None, "active", "paused", "inactive"):
        raise HTTPException(status_code=400, detail="无效的人员监控状态。")
    fields = []
    params: list = []
    for key in ("name", "headline", "title", "notes", "status"):
        if key in data:
            fields.append(f"{key} = ?")
            params.append(clean_text(data[key]) if key != "notes" else data[key])
    if "monitor" in data:
        fields.append("monitor = ?")
        params.append(int(data["monitor"]))
    if fields:
        fields.append("updated_at = ?")
        params.extend([utc_now(), profile_id])
        conn.execute(f"UPDATE linkedin_profiles SET {', '.join(fields)} WHERE id = ?", params)
    return profile_to_dict(conn, conn.execute("SELECT * FROM linkedin_profiles WHERE id = ?", (profile_id,)).fetchone())


@router.get("/employees/{profile_id}/history")
def employee_history(profile_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    profile = conn.execute("SELECT * FROM linkedin_profiles WHERE id = ?", (profile_id,)).fetchone()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    snapshots = conn.execute(
        "SELECT * FROM linkedin_profile_snapshots WHERE profile_id = ? ORDER BY snapshot_date DESC, created_at DESC",
        (profile_id,),
    ).fetchall()
    activities = conn.execute(
        "SELECT * FROM linkedin_activities WHERE profile_id = ? ORDER BY COALESCE(posted_at, created_at) DESC LIMIT 100",
        (profile_id,),
    ).fetchall()
    activity_items = []
    for row in activities:
        item = dict(row)
        item.pop("raw_json", None)
        activity_items.append(item)
    return {
        "profile": profile_to_dict(conn, profile),
        "snapshots": [profile_snapshot_to_dict(row) for row in snapshots],
        "activities": activity_items,
    }


@router.delete("/employees/{profile_id}")
def delete_employee(profile_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    existing = conn.execute("SELECT 1 FROM linkedin_profiles WHERE id = ?", (profile_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Profile not found")
    conn.execute("DELETE FROM linkedin_profile_snapshots WHERE profile_id = ?", (profile_id,))
    conn.execute("DELETE FROM linkedin_activities WHERE profile_id = ?", (profile_id,))
    conn.execute("DELETE FROM linkedin_profiles WHERE id = ?", (profile_id,))
    return {"deleted": profile_id}


@router.get("/activities")
def list_activities(
    brand_id: str,
    profile_id: str | None = None,
    limit: int = 100,
    conn: sqlite3.Connection = Depends(get_conn),
):
    clauses, params = ["a.brand_id = ?"], [brand_id]
    if profile_id:
        clauses.append("a.profile_id = ?")
        params.append(profile_id)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT a.*, p.name AS profile_name, p.title AS profile_title, p.profile_url
        FROM linkedin_activities a
        LEFT JOIN linkedin_profiles p ON p.id = a.profile_id
        WHERE {where}
        ORDER BY COALESCE(a.posted_at, a.created_at) DESC
        LIMIT ?
        """,
        (*params, max(1, min(limit, 500))),
    ).fetchall()
    activities = []
    for r in rows:
        item = dict(r)
        item.pop("raw_json", None)
        activities.append(item)
    return {"activities": activities}


# --------------------------------------------------------------------- summary
@router.get("/summary")
def hiring_summary(
    brand_id: str,
    platform: str | None = None,
    days: int = 90,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = resolve_window(days, start_date, end_date)
    clauses, params = ["brand_id = ?"], [brand_id]
    if platform and platform != "all":
        clauses.append("platform = ?")
        params.append(platform)
    where = " AND ".join(clauses)
    postings = [dict(r) for r in conn.execute(
        f"SELECT * FROM job_postings WHERE {where}", params
    ).fetchall()]

    # Zero-filled day buckets across the window.
    trend = _empty_trend(start, end)
    trend_index = {t["date"]: t for t in trend}

    dept_counts: dict[str, int] = defaultdict(int)
    platform_counts: dict[str, dict] = defaultdict(lambda: {"platform": "", "open": 0, "closed": 0})
    total_open = total_closed = 0
    open_durations: list[int] = []

    for p in postings:
        released = (p.get("first_seen") or "")[:10]
        if released in trend_index:
            trend_index[released]["released"] += 1
        closed_day = (p.get("closed_at") or "")[:10]
        if closed_day and closed_day in trend_index:
            trend_index[closed_day]["closed"] += 1
        pf = platform_counts[p.get("platform") or "unknown"]
        pf["platform"] = p.get("platform") or "unknown"
        if p.get("status") == "closed":
            total_closed += 1
            pf["closed"] += 1
        else:
            total_open += 1
            pf["open"] += 1
            dept = (p.get("department") or "").strip() or "未分类"
            dept_counts[dept] += 1
        # Open duration for closed postings (JD lifetime).
        if p.get("closed_at") and p.get("first_seen"):
            try:
                d0 = date.fromisoformat(p["first_seen"][:10])
                d1 = date.fromisoformat(p["closed_at"][:10])
                open_durations.append(max(0, (d1 - d0).days))
            except ValueError:
                pass

    # Active open postings per day from the snapshot time-series.
    snap_rows = conn.execute(
        f"""
        SELECT snapshot_date, SUM(CASE WHEN is_open = 1 THEN 1 ELSE 0 END) AS active
        FROM job_snapshots
        WHERE brand_id = ? AND snapshot_date >= ? AND snapshot_date <= ?
        GROUP BY snapshot_date
        """,
        (brand_id, start, end),
    ).fetchall()
    for r in snap_rows:
        day = r["snapshot_date"]
        if day in trend_index:
            trend_index[day]["active"] = r["active"] or 0

    by_department = sorted(
        [{"department": k, "count": v} for k, v in dept_counts.items()],
        key=lambda i: i["count"], reverse=True,
    )[:12]

    avg_open_days = round(sum(open_durations) / len(open_durations), 1) if open_durations else None

    return {
        "trend": trend,
        "by_department": by_department,
        "by_platform": list(platform_counts.values()),
        "total_postings": len(postings),
        "total_open": total_open,
        "total_closed": total_closed,
        "avg_open_days": avg_open_days,
        "released_in_window": sum(t["released"] for t in trend),
        "closed_in_window": sum(t["closed"] for t in trend),
    }


def _empty_trend(start: str, end: str) -> list[dict]:
    try:
        d0 = date.fromisoformat(start)
        d1 = date.fromisoformat(end)
    except ValueError:
        return []
    if d0 > d1:
        d0, d1 = d1, d0
    out = []
    cursor = d0
    while cursor <= d1:
        out.append({"date": cursor.isoformat(), "released": 0, "closed": 0, "active": 0})
        cursor += timedelta(days=1)
    return out


# --------------------------------------------------------------------- LLM analysis
@router.post("/analyze")
def analyze_hiring(
    brand_id: str,
    days: int = 90,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if not ai.is_configured(conn):
        raise HTTPException(status_code=400, detail="尚未配置大模型 Token，请先在设置中填写。")
    brand = fetch_brand(conn, brand_id)
    start, end = resolve_window(days, start_date, end_date)
    rows = conn.execute(
        """
        SELECT title, department, city, platform, status, first_seen, jd_text
        FROM job_postings
        WHERE brand_id = ? AND substr(first_seen, 1, 10) >= ? AND substr(first_seen, 1, 10) <= ?
        ORDER BY first_seen DESC LIMIT 80
        """,
        (brand_id, start, end),
    ).fetchall()
    postings = [dict(r) for r in rows]
    if not postings:
        raise HTTPException(status_code=400, detail="该时间窗内暂无职位数据，请先采集。")
    try:
        result = ai.analyze_jd(conn, postings, {"brand": brand.get("name"), "start": start, "end": end})
    except ai.LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return result
