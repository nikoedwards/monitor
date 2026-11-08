"""Build a bounded, source-addressable research packet for executive insights."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta
from urllib.parse import urlparse

from .domains.common import query_records

OVERVIEW_VERSION = "research-v3"
DOMAINS = {"sales": "销售", "market": "市场", "hiring": "招聘", "marketing": "营销", "voice": "用户之声", "web": "网页动态"}


def _json(value, default=None):
    try:
        return json.loads(value or "null") or default
    except (TypeError, ValueError):
        return default


def _compact(value, limit=12):
    if isinstance(value, dict):
        return {k: _compact(v, limit) for k, v in value.items() if k not in {"raw", "raw_json"}}
    if isinstance(value, list):
        return [_compact(v, limit) for v in value[:limit]]
    return value[:900] if isinstance(value, str) else value


def _statistics(module):
    nested = {"totals", "global_metrics", "current", "confidence", "ads", "by_channel", "by_department", "by_product", "topics"}
    return _compact({k: v for k, v in module.items() if isinstance(v, (int, float, str)) or k in nested})


def _url(value):
    value = str(value or "")
    try:
        return value if urlparse(value).scheme in {"http", "https"} and urlparse(value).netloc else ""
    except ValueError:
        return ""


def _diverse(rows, limit, key):
    """Round-robin across entities so high-volume channels cannot drown out others."""
    buckets = defaultdict(list)
    for row in rows:
        buckets[key(row)].append(row)
    selected = []
    while buckets and len(selected) < limit:
        for group in list(buckets):
            selected.append(buckets[group].pop(0))
            if not buckets[group]:
                del buckets[group]
            if len(selected) == limit:
                break
    return selected


def build_overview_evidence(conn, brand_id, payload):
    window = payload["range"]
    start, end, previous_start = window["start"], window["end"], window["previous_start"]
    span = min(7, (date.fromisoformat(end) - date.fromisoformat(start)).days + 1)
    recent_start = (date.fromisoformat(end) - timedelta(days=span - 1)).isoformat()
    baseline_end = (date.fromisoformat(recent_start) - timedelta(days=1)).isoformat()
    baseline_start = (date.fromisoformat(recent_start) - timedelta(days=span)).isoformat()
    catalog, coverage, caveats = [], {}, []
    products = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM products WHERE brand_id = ?", (brand_id,))}
    brand = conn.execute("SELECT name FROM brands WHERE id = ?", (brand_id,)).fetchone()

    def add(domain, kind, row, at, title, detail, metrics=None, url=None, group=None):
        item = {"id": f"{kind}:{row['id']}", "domain": domain, "kind": kind, "at": at,
                "title": str(title or DOMAINS[domain])[:180], "detail": str(detail or "")[:1200],
                "url": _url(url or row.get("url")), "platform": row.get("platform"), "region": row.get("region"),
                "product_id": row.get("product_id"), "product": products.get(row.get("product_id")),
                "independence_group": group or domain, "metrics": _compact(metrics or {})}
        catalog.append(item)

    # Module statistics supplement concrete records. Market share is derived,
    # and voice reuses marketing conversations: neither is independent proof.
    for domain, module in payload["modules"].items():
        if module.get("status") != "ready":
            continue
        fields = _statistics(module)
        add(domain, "module", {"id": domain}, end, f"{DOMAINS[domain]}周期统计",
            "已采集样本的板块汇总，详细指标见统计数据；不代表全市场。", fields,
            group="derived" if domain in {"market", "voice", "marketing"} else domain)

    source_groups = [
        ("voc", None), ("platform", "app"), ("marketing", "media"),
        ("marketing", "social"), ("marketing", "community"),
        ("marketing", "creators"), ("marketing", "ads"), ("hiring", None),
    ]
    for dimension, channel in source_groups:
        source = f"{dimension}/{channel or 'all'}"
        records = query_records(conn, {"brand_id": brand_id, "dimension": dimension, "channel": channel,
                                      "start_date": min(previous_start, baseline_start), "end_date": end}, limit=1000)
        # app_metric snapshots are not user comments and must never affect sentiment.
        discussions = [r for r in records if r.get("data_type") != "app_metric"]
        periods = {}
        for label, low, high in (("current", start, end), ("previous", previous_start, window["previous_end"]),
                                 ("recent", recent_start, end), ("recent_baseline", baseline_start, baseline_end)):
            rows = [r for r in discussions if low <= (r.get("occurred_at") or "")[:10] <= high]
            sentiments = Counter(r.get("sentiment") or "unknown" for r in rows)
            periods[label] = {"start": low, "end": high, "sample_count": len(rows), "sentiment": dict(sentiments)}
        observed_dates = sorted(r.get("occurred_at") for r in records if r.get("occurred_at"))
        timestamp_counts = Counter(observed_dates)
        coverage[source] = {"loaded": len(records), "limit": 1000, "possibly_truncated": len(records) >= 1000, "periods": periods,
                            "first_record_at": observed_dates[0] if observed_dates else None, "last_record_at": observed_dates[-1] if observed_dates else None,
                            "max_records_at_identical_timestamp": max(timestamp_counts.values(), default=0)}
        if max(timestamp_counts.values(), default=0) >= 3:
            caveats.append(f"{source} 存在至少 3 条时间戳完全相同的记录；可能涉及批量导入或日期回填，不能仅据时间集中推断集中发布、PR 放量或用户爆发。")
        if len(records) >= 1000:
            caveats.append(f"{source} 达到 1000 条读取上限；周期计数为已读取样本，不可直接视为总体趋势。")
        selected = _diverse(discussions, 10, lambda r: (r.get("platform"), r.get("product_id"), r.get("sentiment"),
                                                      "current" if (r.get("occurred_at") or "")[:10] >= start else "previous"))
        # Include high-engagement evidence of either polarity alongside recent records.
        def engagement(r):
            metrics = r.get("metrics") or {}
            return sum(float(metrics.get(k) or 0) for k in ("likes", "comments", "score", "views") if str(metrics.get(k) or 0).replace('.', '', 1).lstrip('-').isdigit())
        selected += sorted(discussions, key=engagement, reverse=True)[:2]
        seen = set()
        for row in selected:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            raw = row.get("raw") or {}
            metadata = {k: raw[k] for k in ("version", "appVersion", "reviewCreatedVersion", "release_notes", "releaseNotes", "current_version_release_date", "track_id", "app_id") if k in raw}
            domain = "voice" if dimension in {"voc", "platform"} or channel == "community" else dimension
            group = "conversation" if domain == "voice" else ("creators" if channel == "creators" else domain)
            metrics = row.get("metrics") or {}
            relevant_metrics = {k: metrics[k] for k in ("rating", "rating_count", "review_count", "views", "likes", "comments", "score", "engagement", "shares", "content_type") if k in metrics}
            relevant_metrics["collected_at"] = row.get("created_at")
            add(domain, "record", row, row.get("occurred_at"), row.get("title"), row.get("body"),
                {"sentiment": row.get("sentiment"), "topics": row.get("topics"), **relevant_metrics, **metadata}, group=group)
        # Preserve endpoints and extrema for the SAME app/storefront. An isolated
        # review's stars cannot substitute for a store's aggregate rating.
        app_groups = defaultdict(list)
        for row in records:
            if row.get("data_type") == "app_metric":
                raw = row.get("raw") or {}
                identity = row.get("link_id") or raw.get("track_id") or raw.get("app_id") or row.get("url")
                if identity:
                    app_groups[(row.get("platform"), identity, row.get("region"))].append(row)
        coverage[source]["app_series_total"] = len(app_groups)
        coverage[source]["app_series_sampled"] = min(12, len(app_groups))
        for rows in list(app_groups.values())[:12]:
            rows.sort(key=lambda r: r.get("occurred_at") or "")
            numeric = [r for r in rows if isinstance((r.get("metrics") or {}).get("rating"), (int, float))]
            candidates = [rows[0], rows[-1]]
            if numeric:
                candidates += [min(numeric, key=lambda r: r["metrics"]["rating"]), max(numeric, key=lambda r: r["metrics"]["rating"])]
            for row in {r["id"]: r for r in candidates}.values():
                raw = row.get("raw") or {}
                add("voice", "app_metric", row, row.get("occurred_at"), row.get("title"), row.get("body"),
                    {**(row.get("metrics") or {}), "link_id": row.get("link_id"), **{k: raw.get(k) for k in ("version", "current_version_release_date", "track_id", "app_id", "release_notes")}}, group="app_store")

    queries = {
        "sales": ("SELECT * FROM sales_metrics WHERE brand_id = ? AND snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date DESC, created_at DESC", "snapshot_date"),
        "web": ("SELECT * FROM web_snapshots WHERE brand_id = ? AND substr(snapshot_date,1,10) BETWEEN ? AND ? ORDER BY snapshot_date DESC", "snapshot_date"),
        "hiring": ("SELECT * FROM job_postings WHERE brand_id = ? AND substr(first_seen,1,10) <= ? AND (closed_at IS NULL OR closed_at = '' OR substr(closed_at,1,10) >= ?) ORDER BY first_seen DESC", "first_seen"),
        "ads": ("SELECT * FROM ad_entities WHERE brand_id = ? AND substr(first_seen_at,1,10) <= ? AND substr(COALESCE(stopped_at,last_seen_at),1,10) >= ? ORDER BY first_seen_at DESC", "first_seen_at"),
    }
    for source, (sql, time_field) in queries.items():
        domain = "marketing" if source == "ads" else source
        params = (brand_id, end, previous_start) if source in {"hiring", "ads"} else (brand_id, previous_start, end)
        try:
            rows = [dict(r) for r in conn.execute(sql + " LIMIT 3000", params)]
        except sqlite3.OperationalError:
            coverage[source] = {"unavailable": True}
            caveats.append(f"{DOMAINS[domain]}明细暂不可读取，相关判断缺少该来源支持。")
            continue
        coverage[source] = {"loaded": len(rows), "limit": 3000, "possibly_truncated": len(rows) >= 3000}
        if len(rows) >= 3000:
            caveats.append(f"{source} 明细达到 3000 条上限，仅对已读取样本分析。")
        if source == "sales":
            series = defaultdict(list)
            for row in rows:
                # Never merge different listings or unlinked manual measurements.
                series[(row.get("link_id") or row["id"], row.get("platform"), row.get("currency"))].append(row)
            coverage[source].update(series_total=len(series), series_sampled=min(12, len(series)))
            selected = []
            for values in list(series.values())[:12]:
                candidates = [values[0], values[-1]]
                numeric = [r for r in values if r.get("rating") is not None]
                if numeric:
                    candidates += [min(numeric, key=lambda r: r["rating"]), max(numeric, key=lambda r: r["rating"])]
                selected.extend({r["id"]: r for r in candidates}.values())
        else:
            selected = _diverse(rows, 14, lambda r: r.get("department") if source == "hiring" else r.get("monitor_id") if source == "web" else r.get("platform"))
        coverage[source]["details_sampled"] = len(selected)
        for row in selected:
            if source == "sales":
                metrics = {k: row.get(k) for k in ("link_id", "asin", "price", "currency", "rating", "review_count", "rank", "bsr", "in_stock", "units_est", "revenue_est", "estimate_method", "estimate_confidence")}
                metrics["changes"] = _json(row.get("changes_json"), [])
                add(domain, source, row, row.get(time_field), row.get("title"), "同一 Listing 的销售快照；销量/销售额为估算，评分为该 Listing 评分。", metrics)
            elif source == "hiring":
                add(domain, source, row, row.get(time_field), row.get("title"), row.get("jd_text"),
                    {k: row.get(k) for k in ("department", "city", "status", "first_seen", "posted_at", "closed_at")})
            elif source == "web":
                add(domain, source, row, row.get(time_field), row.get("title"), "\n".join(filter(None, [row.get("summary"), row.get("text_excerpt")])),
                    {"change_score": row.get("change_score"), "visual_change_score": row.get("visual_change_score"), "changes": _json(row.get("changes_json"), [])})
            else:
                add(domain, source, row, row.get(time_field), row.get("page_name") or "广告素材", row.get("creative_body"),
                    {k: row.get(k) for k in ("started_at", "first_seen_at", "last_seen_at", "stopped_at", "active_status")}, url=row.get("snapshot_url"))

    module_coverage = {k: dict(v) for k, v in payload["coverage"].items()}
    for item in catalog:
        if item["kind"] != "module" and start <= str(item.get("at") or "")[:10] <= end:
            info = module_coverage.setdefault(item["domain"], {})
            info["status"] = "ready"
            info["last_data_at"] = max(str(info.get("last_data_at") or ""), str(item.get("at") or ""))
    return {"brand": brand["name"] if brand else brand_id, "range": window, "kpis": payload["kpis"],
            "recent_window": {"start": recent_start, "end": end, "baseline_start": baseline_start, "baseline_end": baseline_end},
            "coverage": module_coverage, "source_coverage": coverage,
            "previous_modules": {k: _statistics(v) for k, v in (payload.get("previous_modules") or {}).items()},
            "trends": {k: {"points": _compact((v.get("trend") or [])[-32:], 32), "total_points": len(v.get("trend") or []), "truncated": len(v.get("trend") or []) > 32} for k, v in payload["modules"].items() if v.get("status") == "ready"},
            "evidence": catalog, "caveats": caveats + ["明细按渠道、产品、情绪和实体分层抽样，不等于阅读所有原文。首见时间是监控发现时间，不等于实际发布或上线时间。", "市场份额为复合估算，与销售、营销和用户数据可能同源，不能作为独立交叉证据。", "缺失、采集覆盖变化、重复转述及样本结构变化不能当作真实业务增长或因果关系。"]}


def validate_overview_insights(result, packet):
    """Only expose server-resolved citations; weak hypotheses stay out of insights."""
    catalog = {e["id"]: e for e in packet["evidence"]}
    rejected = 0
    # v3 names the two user-facing jobs explicitly. Keep accepting the v2
    # names so cached or mocked model responses remain readable during rollout.
    if not isinstance(result.get("deep_insights"), list):
        result["deep_insights"] = result.get("insights") if isinstance(result.get("insights"), list) else []
    if not isinstance(result.get("priority_alerts"), list):
        result["priority_alerts"] = result.get("recent_highlights") if isinstance(result.get("recent_highlights"), list) else []
    for field, limit in (("deep_insights", 3), ("priority_alerts", 4)):
        valid = []
        items = result.get(field)
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"].strip():
                continue
            ids = item.get("evidence_ids")
            refs = [catalog[i] for i in dict.fromkeys(i for i in ids if isinstance(i, str)) if i in catalog] if isinstance(ids, list) else []
            domains = {r["domain"] for r in refs}
            concrete = [r for r in refs if r["kind"] != "module"]
            independent = {r["independence_group"] for r in concrete} - {"derived"}
            if not refs or not concrete:
                rejected += 1
                continue
            if field == "deep_insights" and (len(domains) < 2 or len(independent) < 2):
                rejected += 1
                continue
            if field == "priority_alerts" and not any(packet["recent_window"]["start"] <= str(r.get("at") or "")[:10] <= packet["range"]["end"] for r in concrete):
                rejected += 1
                continue
            # A routine version bump plus an unchanged store rating is context,
            # not a standout event. Require a real rating move or release detail.
            if field == "priority_alerts" and all(r["kind"] == "app_metric" for r in concrete):
                series = defaultdict(list)
                release_detail = False
                for ref in concrete:
                    metrics = ref.get("metrics") or {}
                    release_detail |= bool(metrics.get("release_notes"))
                    identity = metrics.get("track_id") or metrics.get("app_id") or metrics.get("link_id") or ref.get("url")
                    rating = metrics.get("rating")
                    if identity and isinstance(rating, (int, float)) and 0 < rating <= 5:
                        series[(ref.get("platform"), identity, ref.get("region"))].append(rating)
                if not release_detail and not any(len(values) >= 2 and max(values) - min(values) >= 0.1 for values in series.values()):
                    rejected += 1
                    continue
            required = ("observation", "inference", "alternative", "next_signal") if field == "deep_insights" else ("observation",)
            if any(not isinstance(item.get(k), str) or not item[k].strip() for k in required):
                rejected += 1
                continue
            clean = {k: (item[k][:1500] if isinstance(item.get(k), str) else "") for k in ("title", "observation", "inference", "why_it_matters", "alternative", "next_signal", "time_horizon")}
            priority = item.get("priority") if item.get("priority") in {"high", "medium", "low"} else ("high" if item.get("polarity") == "negative" else "medium")
            clean.update(evidence_ids=[r["id"] for r in refs], evidence=refs, domains=sorted(domains), priority=priority,
                         confidence="medium" if item.get("confidence") == "medium" else "low",
                         polarity=item.get("polarity") if isinstance(item.get("polarity"), str) and item["polarity"] in {"positive", "negative", "mixed", "neutral"} else "neutral")
            valid.append(clean)
        result[field] = valid[:limit]
    # Keep the old keys as a compatibility surface for clients upgraded out
    # of order, while the UI and new prompts use the explicit names.
    result["deep_insights"] = result["deep_insights"]
    result["priority_alerts"] = result["priority_alerts"]
    result["insights"] = result["deep_insights"]
    result["recent_highlights"] = result["priority_alerts"]
    result["source_coverage"] = packet["source_coverage"]
    result["coverage"] = packet.get("coverage") or {}
    result["recent_window"] = packet["recent_window"]
    if rejected or not isinstance(result.get("summary"), str):
        cards = result["deep_insights"] or result["priority_alerts"]
        result["summary"] = "；".join(c["title"] for c in cards[:2]) if cards else "当前证据尚不足以形成可靠研判，需补充独立来源和可比历史样本。"
    caveats = result.get("caveats") if isinstance(result.get("caveats"), list) else []
    result["caveats"] = list(dict.fromkeys([str(c) for c in caveats] + packet["caveats"] + (["部分候选发现因缺少有效来源、近期证据或独立跨板块支持，未展示为洞察。"] if rejected else [])))
    return result
