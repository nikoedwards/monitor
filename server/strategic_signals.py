"""Cross-board strategic signal normalization for the hiring surface."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta

from . import ai
from .overview_evidence import build_overview_evidence
from .domains.common import resolve_window
from .domains.insights import _overview_payload


_MAX_HISTORY_DAYS = 3650


def _table_history(conn: sqlite3.Connection, table: str, column: str, brand_id: str) -> tuple[str | None, str | None]:
    try:
        row = conn.execute(
            f"SELECT MIN(substr({column}, 1, 10)), MAX(substr({column}, 1, 10)) FROM {table} WHERE brand_id = ? AND {column} IS NOT NULL AND {column} != ''",
            (brand_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None, None
    return (row[0] if row else None), (row[1] if row else None)


def history_window(conn: sqlite3.Connection, brand_id: str) -> tuple[str, str]:
    """Return the bounded earliest/latest observed dates across Monitor sources."""
    ranges = [
        _table_history(conn, "records", "occurred_at", brand_id),
        _table_history(conn, "job_postings", "first_seen", brand_id),
        _table_history(conn, "sales_metrics", "snapshot_date", brand_id),
        _table_history(conn, "web_snapshots", "snapshot_date", brand_id),
        _table_history(conn, "ad_entities", "first_seen_at", brand_id),
    ]
    starts = [value for value, _ in ranges if value]
    ends = [value for _, value in ranges if value]
    if not starts or not ends:
        return resolve_window(365)
    start, end = min(starts), max(ends)
    # Keep an accidental bad import from creating an unbounded request.
    try:
        start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
        if (end_day - start_day).days + 1 > _MAX_HISTORY_DAYS:
            start = (end_day - timedelta(days=_MAX_HISTORY_DAYS - 1)).isoformat()
    except ValueError:
        return resolve_window(365)
    return start, end


def _posture(signals: list[dict]) -> str:
    directions = {str(item.get("polarity") or "").lower() for item in signals}
    if "mixed" in directions or ("positive" in directions and "negative" in directions):
        return "pivot"
    if "negative" in directions:
        return "pressure"
    if "positive" in directions:
        return "expansion"
    return "watch"


def _timeline(evidence: list[dict], limit: int = 12) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in evidence:
        if item.get("kind") == "module":
            continue
        at = str(item.get("at") or "")[:10]
        if at:
            grouped[at].append(item)
    rows = []
    for day in sorted(grouped, reverse=True)[:limit]:
        events = []
        for item in grouped[day][:6]:
            events.append({
                "domain": item.get("domain"),
                "event_type": item.get("kind"),
                "label": item.get("title"),
                "evidence_ids": [item.get("id")],
            })
        rows.append({"date": day, "events": events})
    return rows


def _fallback_signals(payload: dict, packet: dict) -> list[dict]:
    """Build conservative observations when no model is configured."""
    output = []
    for raw in payload.get("signals") or []:
        domain = raw.get("domain")
        refs = [item for item in packet.get("evidence", []) if item.get("domain") == domain and item.get("kind") != "module"]
        if not refs:
            continue
        output.append({
            "id": f"fallback-{domain}",
            "title": raw.get("title") or "待核查业务信号",
            "direction": "pressure" if raw.get("severity") == "high" else "watch",
            "priority": "high" if raw.get("severity") == "high" else "medium",
            "confidence": "low",
            "observation": raw.get("detail") or "检测到一项业务变化。",
            "inference": "当前只有单板块观察，尚不足以形成跨板块战略研判。",
            "business_impact": "需要结合其他来源继续核查。",
            "alternative": "也可能来自采集覆盖或口径变化。",
            "next_signal": "等待第二个独立来源组出现同向证据。",
            "time_horizon": "未来 7-30 天",
            "domains": [domain],
            "evidence_ids": [item.get("id") for item in refs[:6]],
            "evidence": refs[:6],
        })
    return output[:3]


def build_strategic_signals(
    conn: sqlite3.Connection,
    brand_id: str,
    *,
    scope: str = "all_history",
    start_date: str | None = None,
    end_date: str | None = None,
    refresh: bool = False,
) -> dict:
    if scope == "all_history" and not start_date and not end_date:
        start, end = history_window(conn, brand_id)
    else:
        start, end = resolve_window(365 if scope == "all_history" else 30, start_date, end_date)
        scope = "range"

    payload = _overview_payload(conn, brand_id, start, end, None)
    packet = build_overview_evidence(conn, brand_id, payload)
    result = None
    if ai.is_configured(conn):
        try:
            result = ai.summarize_overview(conn, packet)
        except ai.LlmError:
            result = None
    if not isinstance(result, dict):
        result = {"summary": "当前证据尚不足以形成完整战略研判。", "deep_insights": [], "priority_alerts": [], "caveats": ["大模型未配置或暂时不可用。"]}

    raw_signals = result.get("deep_insights") or result.get("insights") or []
    signals = [item for item in raw_signals if isinstance(item, dict)][:3]
    if not signals:
        signals = _fallback_signals(payload, packet)
    evidence_by_id = {item.get("id"): item for item in packet.get("evidence", [])}
    clean_signals = []
    for item in signals:
        ids = [value for value in item.get("evidence_ids", []) if value in evidence_by_id]
        refs = [evidence_by_id[value] for value in ids]
        domains = sorted({ref.get("domain") for ref in refs if ref.get("domain")})
        groups = {ref.get("independence_group") for ref in refs if ref.get("kind") != "module" and ref.get("independence_group") not in {None, "derived"}}
        if len(groups) < 2:
            # Keep single-board observations visible but label them clearly.
            item = {**item, "title": f"单板块观察：{item.get('title') or '待核查信号'}", "confidence": "low"}
        clean_signals.append({
            "id": item.get("id") or f"signal-{len(clean_signals) + 1}",
            "title": str(item.get("title") or "待核查业务信号")[:180],
            "direction": item.get("direction") or item.get("polarity") or "watch",
            "priority": item.get("priority") if item.get("priority") in {"high", "medium", "low"} else "medium",
            "confidence": item.get("confidence") if item.get("confidence") in {"low", "medium", "high"} else "low",
            "observation": str(item.get("observation") or "")[:900],
            "inference": str(item.get("inference") or "")[:900],
            "business_impact": str(item.get("business_impact") or item.get("why_it_matters") or "")[:900],
            "alternative": str(item.get("alternative") or "")[:900],
            "next_signal": str(item.get("next_signal") or "")[:900],
            "time_horizon": str(item.get("time_horizon") or "")[:180],
            "domains": domains,
            "evidence_ids": [ref.get("id") for ref in refs],
            "evidence": refs,
        })

    headline = str(result.get("summary") or "当前证据尚不足以形成完整战略研判。")[:1200]
    return {
        "schema_version": "strategic-signals-v1",
        "brand": packet.get("brand") or brand_id,
        "window": {"scope": scope, "start": start, "end": end},
        "headline": {"posture": _posture(clean_signals), "summary": headline, "confidence": "medium" if clean_signals else "low"},
        "signals": clean_signals,
        "coverage": packet.get("coverage") or {},
        "source_coverage": packet.get("source_coverage") or {},
        "timeline": _timeline(packet.get("evidence") or []),
        "details": {
            "organization_estimate": {
                "status": "available",
                "href": f"/api/hiring/org-estimate?brand_id={brand_id}&start_date={start}&end_date={end}",
                "note": "人数、部门和地区区间估算在二级详情加载。",
            },
            "evidence_chain": [],
            "method": {"scope": scope, "max_history_days": _MAX_HISTORY_DAYS, "independent_source_groups": 2},
        },
        "caveats": list(dict.fromkeys((result.get("caveats") if isinstance(result.get("caveats"), list) else []) + packet.get("caveats", []))),
        "cached": False,
        "refresh": refresh,
    }
