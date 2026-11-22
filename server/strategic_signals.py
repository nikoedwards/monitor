"""Cross-board strategic signal normalization for the hiring surface."""
from __future__ import annotations

import sqlite3
import threading
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from . import ai
from .overview_evidence import _diverse, build_overview_evidence
from .domains.common import resolve_window
from .domains.insights import _overview_payload


_MAX_HISTORY_DAYS = 3650
try:
    STRATEGIC_SIGNALS_LLM_TIMEOUT_SECONDS = max(5.0, float(os.environ.get("MONITOR_STRATEGIC_SIGNALS_TIMEOUT_SECONDS", "180")))
except (TypeError, ValueError):
    STRATEGIC_SIGNALS_LLM_TIMEOUT_SECONDS = 180.0
_STRATEGIC_SIGNALS_VERSION = "strategic-signals-v3"
_CACHE_LOCK = threading.Lock()
_IN_FLIGHT: dict[str, threading.Lock] = {}


def _data_revision(conn: sqlite3.Connection, brand_id: str) -> str:
    """Return a cheap revision token so same-day imports invalidate the cache."""
    sources = (
        ("records", "occurred_at", "created_at"),
        ("job_postings", "first_seen", "updated_at"),
        ("sales_metrics", "snapshot_date", "created_at"),
        ("web_snapshots", "snapshot_date", "created_at"),
        ("ad_entities", "first_seen_at", "updated_at"),
    )
    parts = []
    for table, date_column, updated_column in sources:
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS count, MAX(substr(COALESCE({updated_column}, {date_column}), 1, 19)) AS latest "
                f"FROM {table} WHERE brand_id = ?",
                (brand_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            continue
        parts.append(f"{table}:{row['count']}:{row['latest'] or ''}")
    return ";".join(parts)


def _cache_key(brand_id: str, scope: str, start: str, end: str, revision: str) -> str:
    return "strategic-signals:" + "|".join((_STRATEGIC_SIGNALS_VERSION, brand_id, scope, start, end, revision))


def _lock_for(key: str) -> threading.Lock:
    with _CACHE_LOCK:
        return _IN_FLIGHT.setdefault(key, threading.Lock())


def _release_lock(key: str, lock: threading.Lock) -> None:
    with _CACHE_LOCK:
        if _IN_FLIGHT.get(key) is lock:
            _IN_FLIGHT.pop(key, None)


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
    directions = {str(item.get("direction") or item.get("polarity") or "").lower() for item in signals}
    if "pivot" in directions or "mixed" in directions or ("expansion" in directions and "pressure" in directions) or ("positive" in directions and "negative" in directions):
        return "pivot"
    if "pressure" in directions or "negative" in directions:
        return "pressure"
    if "expansion" in directions or "positive" in directions:
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


def _quick_strategic_response(conn: sqlite3.Connection, brand_id: str, start: str, end: str, scope: str) -> dict:
    """Return a bounded cold-start response without building the full packet."""
    brand = conn.execute("SELECT name FROM brands WHERE id = ?", (brand_id,)).fetchone()
    counts: dict[str, int] = {}
    queries = {
        "hiring": ("job_postings", "first_seen"),
        "voice": ("records", "occurred_at"),
        "sales": ("sales_metrics", "snapshot_date"),
        "web": ("web_snapshots", "snapshot_date"),
        "marketing": ("ad_entities", "first_seen_at"),
    }
    for domain, (table, column) in queries.items():
        try:
            counts[domain] = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE brand_id = ? AND substr({column}, 1, 10) BETWEEN ? AND ?",
                (brand_id, start, end),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            counts[domain] = 0
    ready = [domain for domain, count in counts.items() if count]
    return {
        "schema_version": "strategic-signals-v1",
        "brand": brand["name"] if brand else brand_id,
        "window": {"scope": scope, "start": start, "end": end},
        "headline": {
            "posture": "watch",
            "summary": "已收集各板块数据，等待生成战略研判。",
            "confidence": "low",
        },
        "signals": [],
        "coverage": {domain: {"status": "ready" if count else "empty", "sample_count": count} for domain, count in counts.items()},
        "source_coverage": {},
        "timeline": [],
        "details": {
            "organization_estimate": {"status": "available", "href": f"/api/hiring/org-estimate?brand_id={brand_id}&scope=all_history", "note": "人数区间和组织分布可在组织推演台查看。"},
            "evidence_chain": [],
            "method": {"scope": scope, "fast_path": True, "ready_domains": ready},
        },
        "caveats": ["证据整理期间先展示数据覆盖。"],
        "cached": False,
        "refresh": False,
    }


def _model_packet(packet: dict) -> dict:
    """Bound model input while retaining full citations for the final response."""
    evidence = sorted(packet.get("evidence", []), key=lambda item: item.get("at") or "", reverse=True)
    evidence = _diverse(evidence, 36, lambda item: (item.get("domain"), item.get("kind")))
    model_evidence = []
    for item in evidence:
        metrics = item.get("metrics") or {}
        model_evidence.append({
            **{key: item[key] for key in ("id", "domain", "kind", "at", "title", "platform", "region", "product", "independence_group") if key in item},
            "detail": (item.get("detail") or "")[:340],
            "metrics": {key: value[:100] if isinstance(value, str) else value for key, value in metrics.items()
                        if isinstance(value, (str, int, float))},
        })
    return {
        **{key: packet[key] for key in ("brand", "range", "recent_window", "kpis", "coverage", "previous_modules", "caveats") if key in packet},
        "evidence": model_evidence,
        "source_coverage": {
            source: {key: coverage[key] for key in ("loaded", "possibly_truncated", "max_records_at_identical_timestamp")
                     if key in coverage}
            for source, coverage in packet.get("source_coverage", {}).items()
        },
    }


def build_strategic_signals(
    conn: sqlite3.Connection,
    brand_id: str,
    *,
    scope: str = "all_history",
    start_date: str | None = None,
    end_date: str | None = None,
    refresh: bool = False,
    revision: str | None = None,
) -> dict:
    if scope == "all_history" and not start_date and not end_date:
        start, end = history_window(conn, brand_id)
    else:
        start, end = resolve_window(365 if scope == "all_history" else 30, start_date, end_date)
        scope = "range"

    cache_key = _cache_key(brand_id, scope, start, end, revision if revision is not None else _data_revision(conn, brand_id))
    if not refresh:
        cached = ai.get_cached_summary(conn, cache_key)
        if isinstance(cached, dict):
            return {**cached, "cached": True, "refresh": False}
        scope_prefix = f"strategic-signals:{_STRATEGIC_SIGNALS_VERSION}|{brand_id}|{scope}|"
        if scope == "range":
            scope_prefix += f"{start}|{end}|"
        previous = ai.get_latest_saved_summary(conn, scope_prefix)
        if previous is not None:
            return {**previous, "refresh": False}
        return _quick_strategic_response(conn, brand_id, start, end, scope)

    # React can issue the initial query and a refetch close together. Serialize
    # only identical keys so one slow model request populates the shared cache
    # and the followers return immediately instead of multiplying the timeout.
    lock = _lock_for(cache_key)
    try:
        with lock:
            if not refresh:
                cached = ai.get_cached_summary(conn, cache_key)
                if isinstance(cached, dict):
                    return {**cached, "cached": True, "refresh": False}

            # The market-share module is a derived, cross-brand calculation and
            # can dominate a cold request on large datasets. Strategic signals
            # already mark it as non-independent evidence; keep this endpoint
            # bounded by requesting the current brand only.
            with ai.measure_preparation("hiring_strategy_evidence"):
                payload = _overview_payload(conn, brand_id, start, end, brand_id)
                packet = build_overview_evidence(conn, brand_id, payload)
            result = None
            model_succeeded = False
            model_error = "尚未配置大模型，请先在设置中配置。"
            if ai.is_configured(conn):
                try:
                    result = ai.summarize_overview(conn, _model_packet(packet), timeout=STRATEGIC_SIGNALS_LLM_TIMEOUT_SECONDS)
                    model_succeeded = isinstance(result, dict) and bool(result.get("summary"))
                    if not model_succeeded:
                        result = None
                        model_error = "大模型未返回有效的研判结果。"
                except (ai.LlmError, TimeoutError, OSError):
                    model_error = "模型暂时未返回结果，可点击刷新重试。"
                    result = None
            if not isinstance(result, dict):
                result = {"summary": "语义研判暂未生成，以下展示已采集证据与板块观察，可点击刷新重试。", "deep_insights": [], "priority_alerts": [], "caveats": [model_error]}

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
            response = {
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
                        "href": f"/api/hiring/org-estimate?brand_id={brand_id}&scope=all_history",
                        "note": "人数区间和组织分布可在组织推演台查看。",
                    },
                    "evidence_chain": [],
                    "method": {"scope": scope, "max_history_days": _MAX_HISTORY_DAYS, "independent_source_groups": 2},
                },
                "caveats": list(dict.fromkeys((result.get("caveats") if isinstance(result.get("caveats"), list) else []) + packet.get("caveats", []))),
                "cached": False,
                "analysis_status": "complete" if model_succeeded else "fallback",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "refresh": refresh,
            }
            if model_succeeded:
                # Cache the complete evidence-resolved response, so later requests skip
                # both the cross-board scans and the model call. The cache helper owns
                # the 24-hour TTL and is safe to use with the existing SQLite schema.
                ai.put_cached_summary(conn, cache_key, response)
    finally:
        _release_lock(cache_key, lock)
    return response
