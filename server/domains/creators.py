"""Influencer/creator dashboard: summary, roster, collection sync, LLM report."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException

from .. import ai
from ..connectors.base import run_collector
from ..connectors.creators import PLATFORM_LABELS, PLATFORMS
from ..connectors.creators.runner import PLATFORM_SOURCE, rebuild_roster
from ..connectors.registry import get_spec
from .common import build_trend, fetch_brand, get_conn, query_records, resolve_window

router = APIRouter(prefix="/api/creators", tags=["creators"])


def _creator_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item.pop("raw_json", None)
    post_count = item.get("post_count") or 0
    item["avg_engagement"] = round(item.get("total_engagement", 0) / post_count, 1) if post_count else 0
    follower = item.get("follower_count") or 0
    item["engagement_rate"] = round(item.get("total_engagement", 0) / follower, 6) if follower else None
    return item


def _shared_creator_map(conn: sqlite3.Connection) -> dict[tuple[str, str], set[str]]:
    """Map (platform, lower-handle) -> set of brand_ids that collaborated with it."""
    rows = conn.execute("SELECT brand_id, platform, handle FROM creators").fetchall()
    mapping: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        key = (row["platform"] or "", (row["handle"] or "").lower())
        mapping.setdefault(key, set()).add(row["brand_id"])
    return mapping


def _brand_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM brands").fetchall()}


# --------------------------------------------------------------------- summary
@router.get("/summary")
def creators_summary(
    brand_id: str | None = None,
    platform: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    plat = None if platform in (None, "all") else platform
    if brand_id:
        rebuild_roster(conn, brand_id)
    start, end = resolve_window(days, start_date, end_date)
    records = query_records(
        conn,
        {"brand_id": brand_id, "dimension": "marketing", "channel": "creators", "platform": plat, "start_date": start, "end_date": end},
        limit=1000,
    )

    def metric(r: dict, key: str) -> int:
        try:
            return int((r.get("metrics") or {}).get(key) or 0)
        except (TypeError, ValueError):
            return 0

    collab_posts = sum(1 for r in records if (r.get("metrics") or {}).get("is_collab"))
    sponsored_posts = sum(1 for r in records if (r.get("metrics") or {}).get("is_sponsored"))
    total_views = sum(metric(r, "views") for r in records)
    total_engagement = sum(metric(r, "engagement") for r in records)
    by_platform = Counter(r.get("platform") or "unknown" for r in records)
    collab_types = Counter((r.get("metrics") or {}).get("collab_type") or "none" for r in records)

    roster_rows = conn.execute(
        f"SELECT * FROM creators WHERE brand_id = ?{' AND platform = ?' if plat else ''} "
        "ORDER BY total_engagement DESC, collab_count DESC LIMIT 10",
        (brand_id, plat) if plat else (brand_id,),
    ).fetchall() if brand_id else []
    creator_count = conn.execute(
        f"SELECT COUNT(*) AS c FROM creators WHERE brand_id = ?{' AND platform = ?' if plat else ''}",
        (brand_id, plat) if plat else (brand_id,),
    ).fetchone()["c"] if brand_id else 0

    return {
        "totals": {
            "posts": len(records),
            "collab_posts": collab_posts,
            "sponsored_posts": sponsored_posts,
            "creators": creator_count,
            "total_views": total_views,
            "total_engagement": total_engagement,
        },
        "by_platform": [
            {"platform": k, "label": PLATFORM_LABELS.get(k, k), "total": v}
            for k, v in by_platform.most_common()
        ],
        "collab_types": [{"type": k, "total": v} for k, v in collab_types.most_common()],
        "top_creators": [_creator_to_dict(r) for r in roster_rows],
        "trend": build_trend(records, start, end),
        "platforms": [{"value": p, "label": PLATFORM_LABELS.get(p, p)} for p in PLATFORMS],
    }


# ---------------------------------------------------------------------- roster
@router.get("/roster")
def creators_roster(
    brand_id: str | None = None,
    platform: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if not brand_id:
        return {"roster": []}
    rebuild_roster(conn, brand_id)
    plat = None if platform in (None, "all") else platform
    rows = conn.execute(
        f"SELECT * FROM creators WHERE brand_id = ?{' AND platform = ?' if plat else ''} "
        "ORDER BY collab_count DESC, total_engagement DESC",
        (brand_id, plat) if plat else (brand_id,),
    ).fetchall()

    shared = _shared_creator_map(conn)
    names = _brand_names(conn)
    roster = []
    for row in rows:
        item = _creator_to_dict(row)
        others = shared.get((row["platform"] or "", (row["handle"] or "").lower()), set())
        item["shared_brands"] = sorted(names.get(bid, bid) for bid in others if bid != brand_id)
        roster.append(item)
    return {"roster": roster}


# ------------------------------------------------------------------------ sync
@router.post("/sync")
def creators_sync(
    brand_id: str,
    platform: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    brand = fetch_brand(conn, brand_id)
    targets = [platform] if platform and platform != "all" else list(PLATFORMS)
    results = []
    for plat in targets:
        spec = get_spec(PLATFORM_SOURCE.get(plat, ""))
        if not spec:
            continue
        outcome = run_collector(conn, spec, brand)
        results.append({"platform": plat, **outcome})
    roster_size = rebuild_roster(conn, brand_id)
    return {"results": results, "roster_size": roster_size}


# ---------------------------------------------------------------------- report
_REPORT_SYSTEM = (
    "你是红人营销情报分析师。基于给定的结构化数据(自家品牌与竞品的红人合作概况、"
    "Top 达人、跨品牌重叠达人)输出一份简洁的中文分析报告,包含:1)整体合作态势;"
    "2)Top 合作达人解读;3)竞品红人库洞察与重叠达人;4)可执行建议。直接输出 Markdown,不要寒暄。"
)


def _roster_brief(conn: sqlite3.Connection, brand_id: str, limit: int = 12) -> list[dict]:
    rows = conn.execute(
        "SELECT platform, handle, name, follower_count, post_count, collab_count, total_engagement, last_collab_at "
        "FROM creators WHERE brand_id = ? ORDER BY collab_count DESC, total_engagement DESC LIMIT ?",
        (brand_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/report")
def creators_report(brand_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    brand = fetch_brand(conn, brand_id)
    rebuild_roster(conn, brand_id)

    competitors = conn.execute(
        "SELECT id, name FROM brands WHERE is_competitor = 1 AND id != ?", (brand_id,)
    ).fetchall()
    context = {
        "brand": {"name": brand.get("name"), "is_competitor": bool(brand.get("is_competitor"))},
        "roster": _roster_brief(conn, brand_id),
        "competitors": [
            {"name": c["name"], "roster": _roster_brief(conn, c["id"], limit=8)}
            for c in competitors
        ],
    }
    if not context["roster"] and not any(c["roster"] for c in context["competitors"]):
        raise HTTPException(status_code=400, detail="暂无红人数据，请先在数据源/红人页发起采集。")

    cfg = ai.get_config(conn)
    if not cfg.get("llm_api_key"):
        raise HTTPException(status_code=400, detail="尚未配置大模型 Token，请先在设置中填写。")
    prompt = "结构化数据如下(JSON)：\n" + json.dumps(context, ensure_ascii=False, indent=2)
    try:
        report = ai.call_llm(cfg, _REPORT_SYSTEM, prompt)
    except ai.LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"report": report}
