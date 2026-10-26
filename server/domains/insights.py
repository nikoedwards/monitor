"""Cross-dimension executive overview and cross-brand comparison."""
from __future__ import annotations

import sqlite3
import time
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

from .. import ai
from .common import build_trend, get_conn, query_records, resolve_window

router = APIRouter(prefix="/api", tags=["insights"])

_OVERVIEW_SUMMARY_CACHE: dict[tuple, tuple[float, dict]] = {}
_SUMMARY_TTL_SECONDS = 24 * 60 * 60


def _safe_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def _previous_window(start: str, end: str) -> tuple[str, str]:
    start_day, end_day = _safe_date(start), _safe_date(end)
    span = (end_day - start_day).days + 1
    previous_end = start_day - timedelta(days=1)
    previous_start = previous_end - timedelta(days=span - 1)
    return previous_start.isoformat(), previous_end.isoformat()


def _number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _delta(current, previous):
    if current is None:
        return {"value": None, "previous": previous, "delta": None, "delta_pct": None}
    current_number = _number(current)
    previous_number = _number(previous)
    delta = current_number - previous_number
    pct = None if previous_number == 0 else round(delta / abs(previous_number) * 100, 1)
    return {"value": current, "previous": previous, "delta": round(delta, 2), "delta_pct": pct}


def _status(has_data: bool, configured: bool = False) -> str:
    if has_data:
        return "ready"
    if configured:
        return "configured_no_data"
    return "empty"


def _summary_records(
    conn: sqlite3.Connection,
    brand_id: str | None,
    dimension: str | None,
    channel: str | None,
    start: str,
    end: str,
) -> list[dict]:
    """Load the same record universe that each summary surface represents."""
    if dimension == "voc":
        # VoC is a composed view: manually collected feedback plus the
        # marketing channels that carry customer conversation. Keep this in
        # sync with /api/voc/summary and /api/voc/records.
        from .content import _load_voice_records

        records = _load_voice_records(conn, brand_id, start, end)
        if channel:
            records = [record for record in records if record.get("channel") == channel]
        return records[:150]
    return query_records(
        conn,
        {
            "brand_id": brand_id,
            "dimension": dimension,
            "channel": channel,
            "start_date": start,
            "end_date": end,
        },
        limit=150,
    )


def _brand_snapshot(conn: sqlite3.Connection, brand_id: str | None, start: str, end: str) -> dict:
    records = query_records(conn, {"brand_id": brand_id, "start_date": start, "end_date": end}, limit=2000)
    voc = [r for r in records if r.get("dimension") == "voc"]
    marketing = [r for r in records if r.get("dimension") == "marketing"]
    negative = sum(1 for r in voc if r.get("sentiment") == "negative")
    sales = conn.execute(
        "SELECT COALESCE(SUM(revenue_est),0) AS revenue, COALESCE(SUM(units_est),0) AS units "
        "FROM sales_metrics WHERE (? IS NULL OR brand_id = ?) AND snapshot_date >= ? AND snapshot_date <= ?",
        (brand_id, brand_id, start, end),
    ).fetchone()
    web_changed = conn.execute(
        "SELECT COUNT(*) AS c FROM web_snapshots WHERE (? IS NULL OR brand_id = ?) "
        "AND snapshot_date >= ? AND snapshot_date <= ? "
        "AND (change_score >= 0.15 OR visual_change_score >= 0.025)",
        (brand_id, brand_id, start, end),
    ).fetchone()["c"]
    return {"records_total": len(records), "voc_total": len(voc), "voc_negative": negative, "marketing_total": len(marketing), "sales_revenue": round(sales["revenue"], 2), "sales_units": sales["units"], "web_changes": web_changed}


def _marketing_module(conn: sqlite3.Connection, brand_id: str, start: str, end: str) -> dict:
    records = query_records(conn, {"brand_id": brand_id, "dimension": "marketing", "start_date": start, "end_date": end}, limit=2000)
    totals = {"views": 0, "likes": 0, "comments": 0, "engagement": 0}
    by_channel = Counter()
    for record in records:
        by_channel[record.get("channel") or "unknown"] += 1
        metrics = record.get("metrics") or {}
        for key in totals:
            totals[key] += int(_number(metrics.get(key)))
    try:
        ads = conn.execute(
            "SELECT * FROM ad_entities WHERE brand_id = ? AND date(first_seen_at) <= date(?) AND date(COALESCE(stopped_at, last_seen_at)) >= date(?)",
            (brand_id, end, start),
        ).fetchall()
    except sqlite3.OperationalError:
        ads = []
    ad_rows = [dict(row) for row in ads]
    active_ads = sum(1 for row in ad_rows if row.get("active_status") == "active")
    new_ads = sum(1 for row in ad_rows if start <= (row.get("first_seen_at") or "")[:10] <= end)
    stopped_ads = sum(1 for row in ad_rows if start <= (row.get("stopped_at") or "")[:10] <= end)
    changed_ads = sum(1 for row in ad_rows if (row.get("first_seen_at") or "")[:10] < start and start <= (row.get("updated_at") or "")[:10] <= end)
    last_seen = max([(row.get("last_seen_at") or "") for row in ad_rows] + [(r.get("occurred_at") or "") for r in records], default=None)
    return {
        "status": _status(bool(records or ad_rows)), "total": len(records),
        "posts": sum(1 for r in records if r.get("data_type") != "community_reply"),
        "replies": sum(1 for r in records if r.get("data_type") == "community_reply"),
        "total_views": totals["views"], "total_likes": totals["likes"], "total_comments": totals["comments"], "total_engagement": totals["engagement"],
        "engagement_rate": round(totals["engagement"] / totals["views"], 4) if totals["views"] else None,
        "by_channel": [{"channel": key, "total": value} for key, value in by_channel.most_common()],
        "ads": {"total": len(ad_rows), "active": active_ads, "new": new_ads, "stopped": stopped_ads, "changed": changed_ads},
        "trend": build_trend(records, start, end), "last_data_at": last_seen,
    }


def _hiring_module(conn: sqlite3.Connection, brand_id: str, start: str, end: str) -> dict:
    postings = [dict(row) for row in conn.execute("SELECT * FROM job_postings WHERE brand_id = ?", (brand_id,)).fetchall()]
    in_window = [row for row in postings if start <= (row.get("first_seen") or "")[:10] <= end or start <= (row.get("closed_at") or "")[:10] <= end or ((row.get("first_seen") or "")[:10] <= end and not row.get("closed_at"))]
    open_rows = [row for row in in_window if row.get("status") != "closed"]
    released = sum(1 for row in postings if start <= (row.get("first_seen") or "")[:10] <= end)
    closed = sum(1 for row in postings if start <= (row.get("closed_at") or "")[:10] <= end)
    durations = []
    for row in postings:
        if row.get("first_seen") and row.get("closed_at"):
            try:
                durations.append(max(0, (_safe_date(row["closed_at"]) - _safe_date(row["first_seen"])).days))
            except ValueError:
                pass
    departments = Counter((row.get("department") or "未分类").strip() or "未分类" for row in open_rows)
    trend = []
    cursor, end_day = _safe_date(start), _safe_date(end)
    while cursor <= end_day:
        day = cursor.isoformat()
        trend.append({"date": day, "released": sum(1 for row in postings if (row.get("first_seen") or "")[:10] == day), "closed": sum(1 for row in postings if (row.get("closed_at") or "")[:10] == day), "active": sum(1 for row in postings if (row.get("first_seen") or "")[:10] <= day and (not row.get("closed_at") or (row.get("closed_at") or "")[:10] > day))})
        cursor += timedelta(days=1)
    links = conn.execute("SELECT COUNT(*) AS c FROM links WHERE brand_id = ? AND dimension = 'hiring'", (brand_id,)).fetchone()["c"]
    return {"status": _status(bool(postings), bool(links)), "total_open": len(open_rows), "total_postings": len(in_window), "released_in_window": released, "closed_in_window": closed, "avg_open_days": round(sum(durations) / len(durations), 1) if durations else None, "by_department": [{"department": key, "count": value} for key, value in departments.most_common(8)], "trend": trend, "last_data_at": max([(row.get("updated_at") or row.get("first_seen") or "") for row in postings], default=None)}


def _market_module(conn: sqlite3.Connection, brand_id: str, start: str, end: str, market_brand_ids: str | None) -> dict:
    from .market_share import market_share as market_share_query
    from .market_share import market_share_trend as market_share_trend_query

    ids = [part.strip() for part in (market_brand_ids or "").split(",") if part.strip()]
    if not ids:
        rows = conn.execute("SELECT id FROM brands WHERE id = ? OR is_competitor = 1 ORDER BY is_primary DESC, name LIMIT 12", (brand_id,)).fetchall()
        ids = [row["id"] for row in rows]
    ids = list(dict.fromkeys(ids))
    if brand_id not in ids:
        ids.insert(0, brand_id)
    if len(ids) < 2:
        return {"status": "needs_competitor", "selected_brand_ids": ids, "brands": [], "trend": [], "current": None}
    try:
        result = market_share_query(brand_ids=",".join(ids), model="balanced", country="all", start_date=start, end_date=end, conn=conn)
        trend = market_share_trend_query(brand_ids=",".join(ids), model="balanced", country="all", start_date=start, end_date=end, conn=conn)
    except (HTTPException, sqlite3.OperationalError, ValueError):
        return {"status": "empty", "selected_brand_ids": ids, "brands": [], "trend": [], "current": None}
    current = next((row for row in result.get("brands", []) if row.get("brand_id") == brand_id), None)
    current_summary = next((row for row in trend.get("summary", []) if row.get("brand_id") == brand_id), None)
    evidence_total = (result.get("confidence") or {}).get("evidence_total") or 0
    return {"status": _status(bool(result.get("brands")) and bool(evidence_total)), "selected_brand_ids": ids, "country": result.get("country"), "confidence": result.get("confidence"), "current": {"name": current.get("name"), "share": current.get("share"), "rank": current.get("rank"), "change_pp": current_summary.get("change_pp") if current_summary else None} if current else None, "brands": result.get("brands", []), "trend": trend.get("points", []), "last_data_at": trend.get("last_snapshot_at")}


def _web_module(conn: sqlite3.Connection, brand_id: str, start: str, end: str) -> dict:
    from .web import web_summary
    try:
        result = web_summary(brand_id=brand_id, start_date=start, end_date=end, conn=conn)
    except (sqlite3.OperationalError, ValueError):
        result = {}
    return {"status": _status(bool(result.get("total_snapshots"))), "changed": int(result.get("changed") or 0), "changed_days": result.get("changed_days") or 0, "average_severity": result.get("average_severity"), "trend": result.get("daily") or [], "last_data_at": result.get("last_snapshot_at")}


def _collect_modules(conn: sqlite3.Connection, brand_id: str, start: str, end: str, market_brand_ids: str | None) -> dict:
    from .content import voc_summary
    from .sales import sales_summary

    sales = sales_summary(brand_id=brand_id, start_date=start, end_date=end, conn=conn)
    voice = voc_summary(brand_id=brand_id, start_date=start, end_date=end, conn=conn)
    try:
        hiring = _hiring_module(conn, brand_id, start, end)
    except sqlite3.OperationalError:
        hiring = {"status": "empty", "total_open": 0, "released_in_window": 0, "closed_in_window": 0, "trend": []}
    marketing = _marketing_module(conn, brand_id, start, end)
    market = _market_module(conn, brand_id, start, end, market_brand_ids)
    web = _web_module(conn, brand_id, start, end)
    voice_totals = voice.get("totals") or {}
    voice["status"] = _status(bool(voice_totals.get("total")))
    voice["last_data_at"] = max((r.get("occurred_at") or "" for r in query_records(conn, {"brand_id": brand_id, "dimension": "voc", "start_date": start, "end_date": end}, limit=1000)), default=None)
    sales["status"] = _status(bool(sales.get("data_points")), bool(sales.get("listing_total")))
    sales["last_data_at"] = (sales.get("global_metrics") or {}).get("latest_date")
    return {"sales": sales, "market": market, "hiring": hiring, "marketing": marketing, "voice": voice, "web": web}


def _yesterday(conn: sqlite3.Connection, brand_id: str, end: str) -> dict:
    focus = _safe_date(end) - timedelta(days=1)
    day = focus.isoformat()
    snapshot = _brand_snapshot(conn, brand_id, day, day)
    jobs = conn.execute("SELECT COUNT(*) AS c FROM job_postings WHERE brand_id = ? AND substr(first_seen,1,10) = ?", (brand_id, day)).fetchone()["c"]
    return {"date": day, "has_data": bool(snapshot["records_total"] or snapshot["sales_revenue"] or jobs or snapshot["web_changes"]), "records_total": snapshot["records_total"], "voc_negative": snapshot["voc_negative"], "marketing_total": snapshot["marketing_total"], "sales_revenue": snapshot["sales_revenue"], "sales_units": snapshot["sales_units"], "hiring_released": jobs, "web_changes": snapshot["web_changes"]}


def _fallback_summary(payload: dict) -> dict:
    modules = payload.get("modules") or {}
    voice = modules.get("voice") or {}
    sales = modules.get("sales") or {}
    marketing = modules.get("marketing") or {}
    hiring = modules.get("hiring") or {}
    parts = []
    if sales.get("data_points"):
        parts.append(f"本期销售额估算 {sales.get('total_revenue', 0):,.0f}，销量 {sales.get('total_units', 0):,.0f} 件。")
    if voice.get("totals", {}).get("total"):
        parts.append(f"用户反馈 {voice['totals'].get('total', 0):,.0f} 条，负向率 {voice['totals'].get('negative_rate', 0) * 100:.1f}%。")
    if marketing.get("total") or marketing.get("ads", {}).get("total"):
        parts.append(f"营销记录 {marketing.get('total', 0):,.0f} 条，广告样本 {marketing.get('ads', {}).get('total', 0):,.0f} 条。")
    if hiring.get("total_postings"):
        parts.append(f"当前在招职位 {hiring.get('total_open', 0):,.0f} 个，本期新增 {hiring.get('released_in_window', 0):,.0f} 个。")
    if not parts:
        parts.append("当前周期暂无足够的经营数据形成完整判断。")
    highlights = []
    if voice.get("p0", {}).get("total"):
        highlights.append({"domain": "voice", "severity": "high", "title": "存在 P0 用户风险", "detail": f"发现 {voice['p0']['total']} 条高优先级反馈。"})
    if (sales.get("global_metrics") or {}).get("changed_listings"):
        highlights.append({"domain": "sales", "severity": "medium", "title": "销售 Listing 有变化", "detail": f"{sales['global_metrics']['changed_listings']} 个 Listing 发生字段变化。"})
    if marketing.get("ads", {}).get("changed"):
        highlights.append({"domain": "marketing", "severity": "medium", "title": "广告素材发生变化", "detail": f"检测到 {marketing['ads']['changed']} 条广告在本期更新。"})
    yesterday = payload.get("yesterday") or {}
    return {"mode": "fallback", "summary": "".join(parts), "yesterday_summary": f"{yesterday.get('date')}：销售额估算 {yesterday.get('sales_revenue', 0):,.0f}，营销记录 {yesterday.get('marketing_total', 0):,.0f} 条，负向反馈 {yesterday.get('voc_negative', 0):,.0f} 条。", "highlights": highlights[:6], "focus": [{"domain": "voice", "action": "优先查看高优先级用户反馈", "reason": "用户之声模块提供 P0 与负向反馈明细。"}] if voice.get("p0", {}).get("total") else [], "caveats": ["当前为确定性系统速览；配置大模型后可生成更完整的跨业务总结。"], "range": payload.get("range"), "record_count": payload.get("kpis", {}).get("records_total", 0)}


def _overview_payload(conn: sqlite3.Connection, brand_id: str, start: str, end: str, market_brand_ids: str | None) -> dict:
    previous_start, previous_end = _previous_window(start, end)
    modules = _collect_modules(conn, brand_id, start, end, market_brand_ids)
    previous = _collect_modules(conn, brand_id, previous_start, previous_end, market_brand_ids)
    sales, previous_sales = modules["sales"], previous["sales"]
    voice_totals, previous_voice = modules["voice"].get("totals") or {}, previous["voice"].get("totals") or {}
    marketing, previous_marketing = modules["marketing"], previous["marketing"]
    hiring, previous_hiring = modules["hiring"], previous["hiring"]
    current_market = modules["market"].get("current") or {} if modules["market"].get("status") == "ready" else {}
    previous_market = previous["market"].get("current") or {} if previous["market"].get("status") == "ready" else {}
    records_total = len(query_records(conn, {"brand_id": brand_id, "start_date": start, "end_date": end}, limit=2000))
    kpis = {"records_total": records_total, "voc_total": voice_totals.get("total", 0), "voc_negative": voice_totals.get("negative", 0), "marketing_total": marketing.get("total", 0), "sales_revenue": sales.get("total_revenue", 0), "sales_units": sales.get("total_units", 0), "web_changes": modules["web"].get("changed", 0), "sales": _delta(sales.get("total_revenue"), previous_sales.get("total_revenue")), "sales_units_comparison": _delta(sales.get("total_units"), previous_sales.get("total_units")), "negative_rate": _delta(voice_totals.get("negative_rate"), previous_voice.get("negative_rate")), "marketing": _delta(marketing.get("total"), previous_marketing.get("total")), "hiring_open": _delta(hiring.get("total_open"), previous_hiring.get("total_open")), "market_share": _delta(current_market.get("share"), previous_market.get("share"))}
    signals = []
    if voice_totals.get("negative", 0):
        signals.append({"domain": "voice", "severity": "high" if voice_totals.get("negative_rate", 0) > 0.2 else "medium", "title": "负向用户反馈", "detail": f"本期有 {voice_totals.get('negative', 0)} 条负向反馈。", "href": f"/brand/{brand_id}/voice"})
    if (sales.get("global_metrics") or {}).get("changed_listings"):
        signals.append({"domain": "sales", "severity": "medium", "title": "销售 Listing 发生变化", "detail": f"{sales['global_metrics']['changed_listings']} 个 Listing 有字段变化。", "href": f"/brand/{brand_id}/sales"})
    if current_market.get("change_pp") is not None and current_market.get("change_pp") < 0:
        signals.append({"domain": "market", "severity": "high", "title": "市场份额下降", "detail": f"本期份额变化 {current_market['change_pp']:.1f} 个百分点。", "href": f"/brand/{brand_id}/market-share"})
    if marketing.get("ads", {}).get("changed"):
        signals.append({"domain": "marketing", "severity": "medium", "title": "广告投放有新变化", "detail": f"{marketing['ads']['changed']} 条广告发生更新。", "href": f"/brand/{brand_id}/marketing"})
    if hiring.get("released_in_window"):
        signals.append({"domain": "hiring", "severity": "low", "title": "招聘投入增加", "detail": f"本期新增 {hiring['released_in_window']} 个职位。", "href": f"/brand/{brand_id}/hiring"})
    if modules["web"].get("changed"):
        signals.append({"domain": "web", "severity": "medium", "title": "官网出现变化", "detail": f"检测到 {modules['web']['changed']} 次网页变化。", "href": f"/brand/{brand_id}/web"})
    severity_order = {"high": 0, "medium": 1, "low": 2}
    signals.sort(key=lambda item: severity_order.get(item["severity"], 9))
    coverage = {key: {"status": value.get("status", "empty"), "last_data_at": value.get("last_data_at")} for key, value in modules.items()}
    return {"range": {"start": start, "end": end, "previous_start": previous_start, "previous_end": previous_end}, "kpis": kpis, "modules": modules, "coverage": coverage, "yesterday": _yesterday(conn, brand_id, end), "signals": signals[:12], "trend": modules["voice"].get("trend", []), "sentiment": [], "top_topics": modules["voice"].get("topics", []), "high_signal": []}


@router.get("/overview")
def overview(brand_id: str | None = None, days: int = 30, start_date: str | None = None, end_date: str | None = None, market_brand_ids: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    start, end = resolve_window(days, start_date, end_date)
    if not brand_id:
        records = query_records(conn, {"start_date": start, "end_date": end}, limit=2000)
        sentiment = Counter(r.get("sentiment") or "neutral" for r in records)
        topics = Counter()
        for record in records:
            topics.update(record.get("topics") or [])
        return {"kpis": _brand_snapshot(conn, brand_id, start, end), "trend": build_trend(records, start, end), "sentiment": [{"sentiment": k, "total": v} for k, v in sentiment.items()], "top_topics": [{"topic": k, "total": v} for k, v in topics.most_common(8)], "high_signal": []}
    return _overview_payload(conn, brand_id, start, end, market_brand_ids)


@router.get("/overview/summary")
def overview_summary(brand_id: str, days: int = 30, start_date: str | None = None, end_date: str | None = None, market_brand_ids: str | None = None, refresh: bool = False, conn: sqlite3.Connection = Depends(get_conn)):
    start, end = resolve_window(days, start_date, end_date)
    payload = _overview_payload(conn, brand_id, start, end, market_brand_ids)
    cache_key = (brand_id, start, end, market_brand_ids or "")
    now = time.monotonic()
    persistent_key = "overview:" + "|".join(cache_key)
    if not refresh:
        persistent = ai.get_cached_summary(conn, persistent_key)
        if persistent is not None:
            _OVERVIEW_SUMMARY_CACHE[cache_key] = (now, persistent)
            return {**persistent, "cached": True}
    cached = _OVERVIEW_SUMMARY_CACHE.get(cache_key)
    if not refresh and cached and now - cached[0] < _SUMMARY_TTL_SECONDS:
        return {**cached[1], "cached": True}
    if not ai.is_configured(conn):
        result = _fallback_summary(payload)
    else:
        try:
            evidence = {
                "range": payload["range"],
                "kpis": payload["kpis"],
                "coverage": payload["coverage"],
                "yesterday": payload["yesterday"],
                "signals": payload["signals"],
                "modules": {
                    key: {
                        field: value for field, value in module.items()
                        if field not in {"trend", "brands"}
                    }
                    for key, module in payload["modules"].items()
                },
            }
            result = {"mode": "ai", **ai.summarize_overview(conn, evidence)}
        except ai.LlmError:
            result = _fallback_summary(payload)
            result["caveats"] = ["大模型暂时不可用，当前展示确定性系统速览。"]
    result.update({"range": payload["range"], "cached": False})
    _OVERVIEW_SUMMARY_CACHE[cache_key] = (now, result)
    ai.put_cached_summary(conn, persistent_key, result)
    return result


@router.get("/insights/summary")
def insights_summary(brand_id: str | None = None, dimension: str | None = None, channel: str | None = None, start_date: str | None = None, end_date: str | None = None, refresh: bool = False, conn: sqlite3.Connection = Depends(get_conn)):
    if not ai.is_configured(conn):
        raise HTTPException(status_code=400, detail="尚未配置大模型 Token，请先在设置中填写。")
    start, end = resolve_window(None, start_date, end_date)
    cache_key = "insights:" + "|".join((brand_id or "", dimension or "", channel or "", start, end))
    if not refresh:
        cached = ai.get_cached_summary(conn, cache_key)
        if cached is not None:
            return {**cached, "cached": True}
    records = _summary_records(conn, brand_id, dimension, channel, start, end)
    if not records:
        return {"summary": "所选时间范围与渠道下暂无数据，无法生成总结。", "highlights": [], "themes": [], "sentiment": {}, "representative": [], "record_count": 0, "range": {"start": start, "end": end}}
    try:
        result = ai.summarize_records(conn, records, {"start": start, "end": end, "channel": channel, "dimension": dimension})
    except ai.LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    result["record_count"] = len(records)
    result["range"] = {"start": start, "end": end}
    result["cached"] = False
    ai.put_cached_summary(conn, cache_key, result)
    return result


@router.get("/compare")
def compare(days: int = 30, start_date: str | None = None, end_date: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    start, end = resolve_window(days, start_date, end_date)
    brands = conn.execute("SELECT * FROM brands ORDER BY is_primary DESC, name").fetchall()
    rows = []
    for brand in brands:
        snapshot = _brand_snapshot(conn, brand["id"], start, end)
        rows.append({"brand_id": brand["id"], "name": brand["name"], "is_competitor": bool(brand["is_competitor"]), "is_primary": bool(brand["is_primary"]), **snapshot})
    return {"brands": rows}
