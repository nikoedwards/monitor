"""Hiring intelligence: job posting registry, daily snapshots, employee feed.

Each target company is a brand. Hiring source links live in `links` with
dimension='hiring' and platform in (boss | linkedin | linkedin_people).
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

from .. import ai
from ..connectors.hiring.runner import run_hiring_collection, run_linkedin_people_collection
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


@router.post("/employees/sync")
def sync_employees(brand_id: str, link_id: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    brand = fetch_brand(conn, brand_id)
    return run_linkedin_people_collection(conn, brand, link_id=link_id)


# -------------------------------------------------------------------- employees
@router.get("/employees")
def list_employees(brand_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute(
        "SELECT * FROM linkedin_profiles WHERE brand_id = ? ORDER BY last_activity_at DESC, last_seen DESC LIMIT 300",
        (brand_id,),
    ).fetchall()
    return {"employees": [profile_to_dict(conn, r) for r in rows]}


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
