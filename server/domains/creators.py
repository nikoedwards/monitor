"""Influencer/creator dashboard: summary, roster, collection sync, LLM report."""
from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException

from .. import ai
from ..connectors.base import run_collector
from ..connectors.creators import PLATFORM_LABELS, PLATFORMS
from ..connectors.creators.curation import (
    candidate_dashboard,
    candidate_evidence,
    import_candidates,
    list_map_snapshots,
    rebuild_candidates,
    save_map_snapshot,
    update_candidate,
)
from ..connectors.creators.products import rebuild_product_matches
from ..connectors.creators.runner import PLATFORM_SOURCE, rebuild_roster
from ..connectors.registry import get_spec
from ..schemas import CreatorCandidateImportIn, CreatorCandidateUpdate, CreatorMapSnapshotIn
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


def _metric(record: dict, key: str) -> int:
    try:
        return int((record.get("metrics") or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _aggregate_records(records: list[dict], brand_id: str) -> list[dict]:
    """Build an in-memory creator roster for a time/product-scoped record set."""
    agg: dict[tuple[str, str], dict] = {}
    for record in records:
        metrics = record.get("metrics") or {}
        platform = record.get("platform") or "unknown"
        handle = str(metrics.get("author_handle") or record.get("author") or "unknown").strip()
        key = (platform, handle.lower())
        entry = agg.setdefault(key, {
            "id": f"{brand_id}:{platform}:{handle.lower()}",
            "brand_id": brand_id,
            "platform": platform,
            "handle": handle,
            "name": record.get("author") or handle,
            "url": metrics.get("author_url") or "",
            "avatar_url": metrics.get("avatar_url") or "",
            "follower_count": 0,
            "post_count": 0,
            "collab_count": 0,
            "sponsored_count": 0,
            "total_views": 0,
            "total_engagement": 0,
            "first_seen": record.get("occurred_at"),
            "last_seen": record.get("occurred_at"),
            "last_collab_at": None,
        })
        entry["post_count"] += 1
        if metrics.get("is_collab"):
            entry["collab_count"] += 1
        if metrics.get("is_sponsored"):
            entry["sponsored_count"] += 1
        entry["total_views"] += _metric(record, "views")
        entry["total_engagement"] += _metric(record, "engagement")
        entry["follower_count"] = max(entry["follower_count"], _metric(record, "follower_count"))
        occurred = record.get("occurred_at") or ""
        if occurred and (not entry["first_seen"] or occurred < entry["first_seen"]):
            entry["first_seen"] = occurred
        if occurred and (not entry["last_seen"] or occurred > entry["last_seen"]):
            entry["last_seen"] = occurred
        if metrics.get("is_collab") and occurred and (
            not entry["last_collab_at"] or occurred > entry["last_collab_at"]
        ):
            entry["last_collab_at"] = occurred

    roster = []
    for entry in agg.values():
        post_count = entry["post_count"] or 0
        entry["avg_engagement"] = round(entry["total_engagement"] / post_count, 1) if post_count else 0
        follower_count = entry["follower_count"] or 0
        entry["engagement_rate"] = (
            round(entry["total_engagement"] / follower_count, 6) if follower_count else None
        )
        roster.append(entry)
    return sorted(roster, key=lambda item: (-item["collab_count"], -item["total_engagement"], item["name"]))


def _add_shared_brands(conn: sqlite3.Connection, roster: list[dict], brand_id: str) -> list[dict]:
    shared = _shared_creator_map(conn)
    names = _brand_names(conn)
    for item in roster:
        others = shared.get((item.get("platform") or "", (item.get("handle") or "").lower()), set())
        item["shared_brands"] = sorted(names.get(other_id, other_id) for other_id in others if other_id != brand_id)
    return roster


def _product_distribution(conn: sqlite3.Connection, records: list[dict]) -> tuple[list[dict], set[str]]:
    record_ids = [record.get("id") for record in records if record.get("id")]
    if not record_ids:
        return [], set()
    placeholders = ",".join("?" for _ in record_ids)
    rows = conn.execute(
        f"""
        SELECT rpm.record_id, rpm.product_id, p.name
        FROM record_product_matches rpm
        JOIN products p ON p.id = rpm.product_id
        WHERE rpm.record_id IN ({placeholders})
        """,
        record_ids,
    ).fetchall()
    matched_ids: set[str] = set()
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        matched_ids.add(row["record_id"])
        counts[(row["product_id"], row["name"])] += 1
    distribution = [
        {"product_id": product_id, "name": name, "total": total}
        for (product_id, name), total in counts.most_common()
    ]
    return distribution, matched_ids


def _scale(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return [50.0 for _ in values]
    return [round(8 + ((value - low) / (high - low)) * 84, 1) for value in values]


def _creator_map(roster: list[dict]) -> dict:
    points = roster[:30]
    depth_raw = [
        item["collab_count"] * 2 + item["sponsored_count"] * 1.25 + min(item["post_count"], 8) * 0.25
        for item in points
    ]
    effect_raw = []
    reach_raw = []
    for item in points:
        reach = max(item["total_views"], item["follower_count"], 1)
        rate = item["total_engagement"] / reach
        effect_raw.append(math.log1p(item["avg_engagement"]) + rate * 20)
        reach_raw.append(math.log1p(reach))
    xs, ys, sizes = _scale(depth_raw), _scale(effect_raw), _scale(reach_raw)

    quadrant_counts: Counter[str] = Counter()
    mapped = []
    for item, x, y, size in zip(points, xs, ys, sizes):
        if x >= 50 and y >= 50:
            quadrant = "core"
        elif x < 50 and y >= 50:
            quadrant = "potential"
        elif x >= 50 and y < 50:
            quadrant = "scale"
        else:
            quadrant = "observe"
        quadrant_counts[quadrant] += 1
        mapped.append({
            "id": item["id"],
            "name": item["name"],
            "handle": item["handle"],
            "platform": item["platform"],
            "url": item.get("url"),
            "x": x,
            "y": y,
            "size": round(10 + size * 0.18, 1),
            "quadrant": quadrant,
            "collab_count": item["collab_count"],
            "post_count": item["post_count"],
            "total_views": item["total_views"],
            "total_engagement": item["total_engagement"],
            "engagement_rate": item["engagement_rate"],
        })
    return {
        "points": mapped,
        "quadrants": [
            {"key": "core", "label": "核心伙伴", "total": quadrant_counts["core"]},
            {"key": "potential", "label": "潜力黑马", "total": quadrant_counts["potential"]},
            {"key": "scale", "label": "铺量达人", "total": quadrant_counts["scale"]},
            {"key": "observe", "label": "观察池", "total": quadrant_counts["observe"]},
        ],
    }


def _scope_product(conn: sqlite3.Connection, brand_id: str | None, product_id: str | None) -> dict | None:
    if not brand_id or not product_id:
        return None
    row = conn.execute(
        "SELECT id, brand_id, name, sku, category FROM products WHERE id = ? AND brand_id = ?",
        (product_id, brand_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found for this brand")
    return dict(row)


# --------------------------------------------------------------------- summary
@router.get("/summary")
def creators_summary(
    brand_id: str | None = None,
    product_id: str | None = None,
    platform: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    plat = None if platform in (None, "all") else platform
    product = _scope_product(conn, brand_id, product_id)
    if brand_id:
        rebuild_roster(conn, brand_id)
        rebuild_product_matches(conn, brand_id)
        rebuild_candidates(conn, brand_id)
    start, end = resolve_window(days, start_date, end_date)
    filters = {
        "brand_id": brand_id,
        "dimension": "marketing",
        "channel": "creators",
        "platform": plat,
        "start_date": start,
        "end_date": end,
    }
    if product_id:
        filters["matched_product_id"] = product_id
    records = query_records(
        conn,
        filters,
        limit=1000,
    )

    collab_posts = sum(1 for r in records if (r.get("metrics") or {}).get("is_collab"))
    sponsored_posts = sum(1 for r in records if (r.get("metrics") or {}).get("is_sponsored"))
    total_views = sum(_metric(r, "views") for r in records)
    total_engagement = sum(_metric(r, "engagement") for r in records)
    by_platform = Counter(r.get("platform") or "unknown" for r in records)
    collab_types = Counter((r.get("metrics") or {}).get("collab_type") or "none" for r in records)
    scoped_roster = _aggregate_records(records, brand_id or "")
    by_product, matched_record_ids = _product_distribution(conn, records)
    creator_count = sum(1 for item in scoped_roster if item["collab_count"] > 0)
    map_data = _creator_map(scoped_roster)

    return {
        "scope": {"brand_id": brand_id, "product": product},
        "totals": {
            "posts": len(records),
            "collab_posts": collab_posts,
            "sponsored_posts": sponsored_posts,
            "creators": creator_count,
            "total_views": total_views,
            "total_engagement": total_engagement,
            "matched_posts": len(matched_record_ids),
            "unmapped_posts": max(0, len(records) - len(matched_record_ids)),
            "product_match_coverage": round(len(matched_record_ids) / len(records), 4) if records else 0,
        },
        "by_platform": [
            {"platform": k, "label": PLATFORM_LABELS.get(k, k), "total": v}
            for k, v in by_platform.most_common()
        ],
        "by_product": by_product,
        "collab_types": [{"type": k, "total": v} for k, v in collab_types.most_common()],
        "top_creators": sorted(
            scoped_roster,
            key=lambda item: (-item["total_engagement"], -item["collab_count"]),
        )[:10],
        "creator_map": map_data,
        "trend": build_trend(records, start, end),
        "platforms": [{"value": p, "label": PLATFORM_LABELS.get(p, p)} for p in PLATFORMS],
    }


# ---------------------------------------------------------------------- roster
@router.get("/roster")
def creators_roster(
    brand_id: str | None = None,
    product_id: str | None = None,
    platform: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if not brand_id:
        return {"roster": []}
    product = _scope_product(conn, brand_id, product_id)
    rebuild_roster(conn, brand_id)
    plat = None if platform in (None, "all") else platform
    if product_id:
        records = query_records(
            conn,
            {
                "brand_id": brand_id,
                "dimension": "marketing",
                "channel": "creators",
                "platform": plat,
                "matched_product_id": product_id,
            },
            limit=1000,
        )
        roster = _aggregate_records(records, brand_id)
    else:
        rows = conn.execute(
            f"SELECT * FROM creators WHERE brand_id = ?{' AND platform = ?' if plat else ''} "
            "ORDER BY collab_count DESC, total_engagement DESC",
            (brand_id, plat) if plat else (brand_id,),
        ).fetchall()
        roster = [_creator_to_dict(row) for row in rows]
    return {"scope": {"product": product}, "roster": _add_shared_brands(conn, roster, brand_id)}


# --------------------------------------------------------------- curation pool
@router.get("/candidates")
def creators_candidates(
    brand_id: str,
    product_id: str | None = None,
    platform: str | None = None,
    review_status: str | None = None,
    q: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    fetch_brand(conn, brand_id)
    _scope_product(conn, brand_id, product_id)
    return candidate_dashboard(conn, brand_id, product_id, platform, review_status, q)


@router.post("/candidates/rebuild")
def creators_candidates_rebuild(
    brand_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
):
    fetch_brand(conn, brand_id)
    product_matches = rebuild_product_matches(conn, brand_id)
    candidates = rebuild_candidates(conn, brand_id)
    return {"product_matches": product_matches, "candidates": candidates}


@router.post("/candidates/import", status_code=201)
def creators_candidates_import(
    payload: CreatorCandidateImportIn,
    conn: sqlite3.Connection = Depends(get_conn),
):
    fetch_brand(conn, payload.brand_id)
    result = import_candidates(
        conn,
        payload.brand_id,
        [row.model_dump() for row in payload.rows],
    )
    if not result["ids"]:
        raise HTTPException(status_code=400, detail="未识别到有效的 YouTube、Instagram 或 TikTok 红人。")
    return result


@router.put("/candidates/{candidate_id}")
def creators_candidate_update(
    candidate_id: str,
    payload: CreatorCandidateUpdate,
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        return update_candidate(conn, candidate_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/candidates/{candidate_id}/evidence")
def creators_candidate_evidence(
    candidate_id: str,
    product_id: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    row = conn.execute("SELECT id FROM creator_candidates WHERE id = ?", (candidate_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Creator candidate not found")
    return {"evidence": candidate_evidence(conn, candidate_id, product_id)}


@router.get("/map-snapshots")
def creators_map_snapshots(
    brand_id: str,
    product_id: str | None = None,
    platform: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    fetch_brand(conn, brand_id)
    _scope_product(conn, brand_id, product_id)
    return {"snapshots": list_map_snapshots(conn, brand_id, product_id, platform)}


@router.post("/map-snapshots", status_code=201)
def creators_map_snapshot_create(
    payload: CreatorMapSnapshotIn,
    conn: sqlite3.Connection = Depends(get_conn),
):
    fetch_brand(conn, payload.brand_id)
    _scope_product(conn, payload.brand_id, payload.product_id)
    try:
        return save_map_snapshot(
            conn,
            payload.brand_id,
            payload.product_id,
            payload.platform,
            payload.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
    product_matches = rebuild_product_matches(conn, brand_id)
    roster_size = rebuild_roster(conn, brand_id)
    candidates = rebuild_candidates(conn, brand_id)
    return {
        "results": results,
        "roster_size": roster_size,
        "product_matches": product_matches,
        "candidates": candidates,
    }


# ---------------------------------------------------------------------- report
_REPORT_SYSTEM = (
    "你是红人营销情报分析师。基于给定的结构化数据(自家品牌与竞品的红人合作概况、"
    "Top 达人、跨品牌重叠达人)输出一份简洁的中文分析报告,包含:1)整体合作态势;"
    "2)Top 合作达人解读;3)竞品红人库洞察与重叠达人;4)可执行建议。直接输出 Markdown,不要寒暄。"
)


def _roster_brief(
    conn: sqlite3.Connection,
    brand_id: str,
    limit: int = 12,
    product_id: str | None = None,
) -> list[dict]:
    if product_id:
        records = query_records(
            conn,
            {
                "brand_id": brand_id,
                "dimension": "marketing",
                "channel": "creators",
                "matched_product_id": product_id,
            },
            limit=1000,
        )
        return [
            {
                key: item.get(key)
                for key in (
                    "platform", "handle", "name", "follower_count", "post_count",
                    "collab_count", "total_engagement", "last_collab_at",
                )
            }
            for item in _aggregate_records(records, brand_id)[:limit]
        ]
    rows = conn.execute(
        "SELECT platform, handle, name, follower_count, post_count, collab_count, total_engagement, last_collab_at "
        "FROM creators WHERE brand_id = ? ORDER BY collab_count DESC, total_engagement DESC LIMIT ?",
        (brand_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/report")
def creators_report(
    brand_id: str,
    product_id: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    brand = fetch_brand(conn, brand_id)
    product = _scope_product(conn, brand_id, product_id)
    rebuild_roster(conn, brand_id)
    rebuild_product_matches(conn, brand_id)

    competitors = conn.execute(
        "SELECT id, name FROM brands WHERE is_competitor = 1 AND id != ?", (brand_id,)
    ).fetchall()
    context = {
        "brand": {"name": brand.get("name"), "is_competitor": bool(brand.get("is_competitor"))},
        "scope": {"level": "product" if product else "brand", "product": product},
        "roster": _roster_brief(conn, brand_id, product_id=product_id),
        "competitors": [
            {"name": c["name"], "roster": _roster_brief(conn, c["id"], limit=8)}
            for c in competitors
        ],
    }
    if product and not context["roster"]:
        raise HTTPException(status_code=400, detail="该产品暂无红人数据，请先同步或补充产品别名。")
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
