"""Records, VoC analysis + action workflow, and marketing summaries."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..nlp import VOC_ACTION_STATUSES, priority_for_records, team_for_records
from ..records import insert_record, record_to_dict, upsert_ad_observation
from ..schemas import ImportIn, RecordIn, VocActionIn, VocActionUpdate
from ..util import new_id, utc_now
from .common import build_trend, get_conn, query_records, resolve_window

router = APIRouter(prefix="/api", tags=["content"])


def _ad_entity_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    for key in ("publisher_platforms_json", "link_urls_json", "raw_json"):
        target = key.removesuffix("_json")
        try:
            item[target] = json.loads(item.pop(key) or ("[]" if key != "raw_json" else "{}"))
        except (TypeError, ValueError):
            item[target] = [] if key != "raw_json" else {}
    item["duration_days"] = _ad_duration_days(item.get("started_at"), item.get("stopped_at"), item.get("last_seen_at"))
    # Keep the database names available, but expose a small card-friendly
    # shape so the dashboard does not need to know source-specific columns.
    item["source"] = item.get("source_id")
    item["source_ad_id"] = item.get("ad_external_id")
    item["advertiser_name"] = item.get("page_name")
    item["body"] = item.get("creative_body")
    item["snapshot_url"] = item.get("snapshot_url")
    item["delivery_start"] = item.get("started_at")
    item["delivery_stop"] = item.get("stopped_at")
    item["first_seen"] = item.get("first_seen_at")
    item["last_seen"] = item.get("last_seen_at")
    item["status"] = "active" if item.get("active_status") == "active" else "stopped"
    item["platforms"] = item.get("publisher_platforms") or []
    item["creative_url"] = item.get("snapshot_url")
    metrics = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    item["spend"] = metrics.get("spend")
    item["impressions"] = metrics.get("impressions")
    item["reach"] = metrics.get("reach")
    item["landing_url"] = (item.get("link_urls") or [None])[0]
    duration = item.get("duration_days")
    item["persistence_score"] = round(min(100.0, max(0.0, (float(duration or 0) / 30.0) * 100.0)), 1)
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    item["evidence_level"] = "B" if raw.get("impressions") or raw.get("spend") else "C"
    return item


def _ad_duration_days(started_at: str | None, stopped_at: str | None, end_at: str | None) -> float | None:
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(stopped_at or end_at).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return round(max(0.0, (end - start).total_seconds() / 86400), 1)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------- records
@router.get("/records")
def list_records(
    brand_id: str | None = None,
    product_id: str | None = None,
    dimension: str | None = None,
    channel: str | None = None,
    data_type: str | None = None,
    source_id: str | None = None,
    sentiment: str | None = None,
    intent: str | None = None,
    platform: str | None = None,
    region: str | None = None,
    days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    q: str | None = None,
    limit: int = 200,
    conn: sqlite3.Connection = Depends(get_conn),
):
    filters = {k: v for k, v in locals().items() if k not in {"conn", "limit"}}
    return {"records": query_records(conn, filters, limit=limit)}


@router.post("/records", status_code=201)
def create_record(payload: RecordIn, conn: sqlite3.Connection = Depends(get_conn)):
    data = payload.model_dump()
    try:
        upsert_ad_observation(conn, data)
        record = insert_record(conn, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return record_to_dict(conn.execute("SELECT * FROM records WHERE id = ?", (record["id"],)).fetchone())


@router.post("/import", status_code=201)
def import_records(payload: ImportIn, conn: sqlite3.Connection = Depends(get_conn)):
    created = 0
    for row in payload.rows:
        if not isinstance(row, dict):
            continue
        row.setdefault("source_id", payload.source_id)
        if payload.brand_id:
            row.setdefault("brand_id", payload.brand_id)
        row.setdefault("dimension", "voc")
        try:
            upsert_ad_observation(conn, row)
            insert_record(conn, row)
            created += 1
        except ValueError:
            continue
    return {"created": created}


# --------------------------------------------------------------- VoC summary
def _topic_breakdown(records: list[dict]) -> list[dict]:
    topic_records: dict[str, list[dict]] = {}
    for record in records:
        for topic in record.get("topics") or []:
            topic_records.setdefault(topic, []).append(record)
    out = []
    for topic, items in topic_records.items():
        negative = sum(1 for r in items if r.get("sentiment") == "negative")
        out.append({
            "topic": topic,
            "total": len(items),
            "negative": negative,
            "negative_rate": round(negative / len(items), 4) if items else 0.0,
            "suggested_team": team_for_records(items, topic),
        })
    return sorted(out, key=lambda item: item["total"], reverse=True)[:10]


def _channel_breakdown(records: list[dict]) -> list[dict]:
    counts = Counter(r.get("source_id") or "unknown" for r in records)
    negatives = Counter(r.get("source_id") or "unknown" for r in records if r.get("sentiment") == "negative")
    out = []
    for source_id, total in counts.items():
        negative = negatives.get(source_id, 0)
        out.append({
            "source_id": source_id,
            "total": total,
            "negative": negative,
            "negative_rate": round(negative / total, 4) if total else 0.0,
        })
    return sorted(out, key=lambda item: item["total"], reverse=True)


def _build_alerts(records: list[dict]) -> list[dict]:
    alerts = []
    for topic in _topic_breakdown(records):
        if topic["negative"] >= 2:
            alerts.append({
                "id": f"topic_{topic['topic']}",
                "type": "topic",
                "label": f"主题「{topic['topic']}」负向声量 {topic['negative']} 条",
                "severity": "high" if topic["negative_rate"] >= 0.4 else "medium",
                "suggested_team": topic["suggested_team"],
            })
    products = Counter(r.get("product_id") for r in records if r.get("sentiment") == "negative" and r.get("product_id"))
    for product_id, count in products.items():
        if count >= 2:
            alerts.append({
                "id": f"product_{product_id}",
                "type": "product",
                "label": f"产品负向反馈 {count} 条",
                "severity": "high",
                "product_id": product_id,
            })
    return alerts[:12]


@router.get("/voc/summary")
def voc_summary(
    brand_id: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    q: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = resolve_window(days, start_date, end_date)
    records = query_records(conn, {"brand_id": brand_id, "dimension": "voc", "start_date": start, "end_date": end, "q": q}, limit=1000)
    total = len(records)
    negative = sum(1 for r in records if r.get("sentiment") == "negative")
    positive = sum(1 for r in records if r.get("sentiment") == "positive")
    actions = conn.execute(
        "SELECT status FROM voc_actions WHERE (? IS NULL OR brand_id = ?)",
        (brand_id, brand_id),
    ).fetchall()
    closed = sum(1 for a in actions if a["status"] in {"resolved", "closed"})
    return {
        "totals": {
            "total": total,
            "negative": negative,
            "positive": positive,
            "neutral": total - negative - positive,
            "negative_rate": round(negative / total, 4) if total else 0.0,
        },
        "trend": build_trend(records, start, end),
        "channels": _channel_breakdown(records),
        "topics": _topic_breakdown(records),
        "alerts": _build_alerts(records),
        "actions": {
            "total": len(actions),
            "open": len(actions) - closed,
            "closed": closed,
            "closure_rate": round(closed / len(actions), 4) if actions else 0.0,
        },
    }


# --------------------------------------------------------------- VoC actions
def action_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item.pop("raw_json", None)
    return item


@router.get("/voc/actions")
def list_actions(brand_id: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute(
        "SELECT * FROM voc_actions WHERE (? IS NULL OR brand_id = ?) ORDER BY created_at DESC",
        (brand_id, brand_id),
    ).fetchall()
    return {"actions": [action_to_dict(r) for r in rows]}


@router.post("/voc/actions", status_code=201)
def create_action(payload: VocActionIn, conn: sqlite3.Connection = Depends(get_conn)):
    now = utc_now()
    action_id = new_id()
    owner = payload.owner_team
    if not owner and payload.record_id:
        record = conn.execute("SELECT * FROM records WHERE id = ?", (payload.record_id,)).fetchone()
        if record:
            owner = team_for_records([record_to_dict(record)])
    conn.execute(
        """
        INSERT INTO voc_actions (id, record_id, brand_id, title, description, owner_team,
            priority, status, product, topic, due_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action_id, payload.record_id, payload.brand_id, payload.title, payload.description,
            owner or "marketing_team", payload.priority, payload.status, payload.product,
            payload.topic, payload.due_at, now, now,
        ),
    )
    return action_to_dict(conn.execute("SELECT * FROM voc_actions WHERE id = ?", (action_id,)).fetchone())


@router.put("/voc/actions/{action_id}")
def update_action(action_id: str, payload: VocActionUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    existing = conn.execute("SELECT * FROM voc_actions WHERE id = ?", (action_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Action not found")
    data = payload.model_dump(exclude_none=True)
    if "status" in data and data["status"] not in VOC_ACTION_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    closed_at = existing["closed_at"]
    if data.get("status") in {"resolved", "closed"} and not closed_at:
        closed_at = utc_now()
    fields = {**dict(existing), **data, "closed_at": closed_at, "updated_at": utc_now()}
    conn.execute(
        """
        UPDATE voc_actions SET title = ?, description = ?, owner_team = ?, priority = ?,
            status = ?, due_at = ?, closed_at = ?, updated_at = ? WHERE id = ?
        """,
        (
            fields["title"], fields["description"], fields["owner_team"], fields["priority"],
            fields["status"], fields["due_at"], fields["closed_at"], fields["updated_at"], action_id,
        ),
    )
    return action_to_dict(conn.execute("SELECT * FROM voc_actions WHERE id = ?", (action_id,)).fetchone())


@router.delete("/voc/actions/{action_id}")
def delete_action(action_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("DELETE FROM voc_actions WHERE id = ?", (action_id,))
    return {"deleted": action_id}


# ----------------------------------------------------------- advertising monitor
@router.get("/marketing/ads")
def marketing_ads(
    brand_id: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    platform: str | None = None,
    source: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 100,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = resolve_window(days, start_date, end_date)
    where = ["brand_id = ?", "date(first_seen_at) <= date(?)", "date(COALESCE(stopped_at, last_seen_at)) >= date(?)"]
    params: list[object] = [brand_id, end, start]
    if platform:
        where.append("platform = ?")
        params.append(platform)
    if status == "new":
        where.append("date(first_seen_at) BETWEEN date(?) AND date(?)")
        params.extend([start, end])
    elif status in {"active", "inactive", "stopped"}:
        where.append("active_status = ?")
        params.append("inactive" if status == "stopped" else status)
    if source:
        where.append("source_id = ?")
        params.append(source)
    if q:
        where.append("(page_name LIKE ? OR creative_body LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    rows = conn.execute(
        f"SELECT * FROM ad_entities WHERE {' AND '.join(where)} ORDER BY last_seen_at DESC LIMIT ?",
        (*params, max(1, min(limit, 500))),
    ).fetchall()
    return {"ads": [_ad_entity_dict(row) for row in rows], "start_date": start, "end_date": end}


@router.get("/marketing/ads/summary")
@router.get("/marketing/ads-summary")
def marketing_ads_summary(
    brand_id: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = resolve_window(days, start_date, end_date)
    rows = conn.execute(
        """SELECT * FROM ad_entities
           WHERE brand_id = ? AND date(first_seen_at) <= date(?)
             AND date(COALESCE(stopped_at, last_seen_at)) >= date(?)
           ORDER BY last_seen_at DESC""",
        (brand_id, end, start),
    ).fetchall()
    ads = [_ad_entity_dict(row) for row in rows]
    active = [a for a in ads if a.get("active_status") == "active"]
    duration_values = [a["duration_days"] for a in ads if a.get("duration_days") is not None]
    events = conn.execute(
        """SELECT event_type, COUNT(*) AS total FROM ad_events
           WHERE brand_id = ? AND date(event_at) BETWEEN date(?) AND date(?)
           GROUP BY event_type ORDER BY total DESC""",
        (brand_id, start, end),
    ).fetchall()
    daily_rows = conn.execute(
        """SELECT observed_date AS date, COUNT(*) AS total,
                  SUM(CASE WHEN active_status = 'active' THEN 1 ELSE 0 END) AS active
           FROM ad_snapshots WHERE brand_id = ? AND date(observed_date) BETWEEN date(?) AND date(?)
           GROUP BY observed_date ORDER BY observed_date""",
        (brand_id, start, end),
    ).fetchall()
    daily = [dict(row) for row in daily_rows]
    new_by_day = Counter(str(a.get("first_seen_at") or "")[:10] for a in ads)
    stopped_by_day = Counter(str(a.get("stopped_at") or "")[:10] for a in ads if a.get("stopped_at"))
    for point in daily:
        day = point.get("date")
        point["new"] = new_by_day.get(day, 0)
        point["stopped"] = stopped_by_day.get(day, 0)
    by_platform = Counter(a.get("platform") or "unknown" for a in ads)
    by_page = Counter(a.get("page_name") or "unknown" for a in ads)
    return {
        "total": len(ads),
        "active": len(active),
        "active_ads": len(active),
        "inactive": len(ads) - len(active),
        "stopped_ads": len(ads) - len(active),
        "new_ads": sum(1 for a in ads if str(a.get("first_seen_at") or "")[:10] >= start),
        "avg_duration_days": round(sum(duration_values) / len(duration_values), 1) if duration_values else None,
        "avg_lifetime_days": round(sum(duration_values) / len(duration_values), 1) if duration_values else None,
        "persistence_score": round(sum(a.get("persistence_score", 0) for a in ads) / len(ads), 1) if ads else 0,
        "evidence_level": "B" if any((a.get("evidence_level") == "B") for a in ads) else "C",
        "events": [{"event_type": row["event_type"], "total": row["total"]} for row in events],
        "by_source": [{"source": k, "source_id": k, "total": v} for k, v in Counter(a.get("source") or "unknown" for a in ads).most_common()],
        "by_platform": [{"platform": k, "total": v} for k, v in by_platform.most_common()],
        "by_page": [{"page_name": k, "total": v} for k, v in by_page.most_common(12)],
        "by_status": [
            {"status": "active", "total": len(active)},
            {"status": "stopped", "total": len(ads) - len(active)},
        ],
        "alerts": [
            {
                "id": f"{row['event_type']}-{row['event_at']}",
                "type": row["event_type"],
                "title": f"{row['event_type']} {row['total']} 条",
                "detail": "来自广告库的生命周期或素材变化事件",
                "detected_at": row["event_at"],
            }
            for row in conn.execute(
                """SELECT event_type, event_at, COUNT(*) AS total FROM ad_events
                   WHERE brand_id = ? AND date(event_at) BETWEEN date(?) AND date(?)
                   GROUP BY event_type, event_at ORDER BY event_at DESC LIMIT 8""",
                (brand_id, start, end),
            ).fetchall()
        ],
        "trend": daily,
        "start_date": start,
        "end_date": end,
    }


# ----------------------------------------------------------- marketing summary
def _subchannel_label(record: dict) -> tuple[str, str]:
    """Group a community record under (platform, sub-channel) e.g. ('reddit','r/PLAUDAI')."""
    platform = record.get("platform") or "unknown"
    metrics = record.get("metrics") or {}
    sub = metrics.get("subreddit")
    if sub:
        return platform, f"r/{sub}"
    scope = metrics.get("scope")
    if isinstance(scope, str) and scope.startswith("subreddit:"):
        return platform, f"r/{scope.split(':', 1)[1]}"
    # self-hosted / others: fall back to the URL host as the sub-channel identity
    url = record.get("url") or ""
    host = ""
    if "://" in url:
        host = url.split("://", 1)[1].split("/", 1)[0].replace("www.", "")
    return platform, host or platform


def _by_subchannel(records: list[dict]) -> list[dict]:
    """Aggregate per platform with per-sub-channel breakdown + posts/replies split."""
    groups: dict[str, dict] = {}
    for r in records:
        platform, sub = _subchannel_label(r)
        is_reply = r.get("data_type") == "community_reply"
        grp = groups.setdefault(platform, {"platform": platform, "total": 0, "posts": 0, "replies": 0, "subchannels": {}})
        grp["total"] += 1
        grp["posts"] += 0 if is_reply else 1
        grp["replies"] += 1 if is_reply else 0
        node = grp["subchannels"].setdefault(sub, {"key": sub, "total": 0, "posts": 0, "replies": 0})
        node["total"] += 1
        node["posts"] += 0 if is_reply else 1
        node["replies"] += 1 if is_reply else 0
    out = []
    for grp in groups.values():
        grp["subchannels"] = sorted(grp["subchannels"].values(), key=lambda x: x["total"], reverse=True)
        out.append(grp)
    return sorted(out, key=lambda g: g["total"], reverse=True)


@router.get("/marketing/summary")
def marketing_summary(
    brand_id: str | None = None,
    channel: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = resolve_window(days, start_date, end_date)
    records = query_records(
        conn, {"brand_id": brand_id, "dimension": "marketing", "channel": channel, "start_date": start, "end_date": end}, limit=1000
    )
    by_channel = Counter(r.get("channel") or "unknown" for r in records)
    by_platform = Counter(r.get("platform") or "unknown" for r in records)
    by_source = Counter(r.get("source_id") or "unknown" for r in records)
    coverage = Counter((r.get("metrics") or {}).get("coverage_type") for r in records if (r.get("metrics") or {}).get("coverage_type"))
    posts = sum(1 for r in records if r.get("data_type") != "community_reply")
    replies = sum(1 for r in records if r.get("data_type") == "community_reply")

    by_tier: Counter = Counter()
    by_country: Counter = Counter()
    sov: Counter = Counter()
    publications: dict[str, dict] = {}
    total_reach = 0
    total_ave = 0
    for r in records:
        m = r.get("metrics") or {}
        if m.get("media_tier"):
            by_tier[m["media_tier"]] += 1
        if m.get("country"):
            by_country[m["country"]] += 1
        try:
            total_reach += int(m.get("monthly_traffic") or m.get("estimated_reach") or 0)
        except (TypeError, ValueError):
            pass
        try:
            total_ave += int(m.get("ave") or 0)
        except (TypeError, ValueError):
            pass
        if r.get("channel") == "media":
            domain = m.get("publication_domain") or ""
            name = r.get("platform") or domain or "Unknown publication"
            key = domain or name
            publication = publications.setdefault(key, {
                "name": name,
                "domain": domain,
                "total": 0,
                "monthly_traffic": 0,
                "authority": 0,
                "tier": m.get("media_tier") or "unknown",
                "country": m.get("country") or "",
            })
            publication["total"] += 1
            for field in ("monthly_traffic", "estimated_reach"):
                try:
                    publication["monthly_traffic"] = max(publication["monthly_traffic"], int(m.get(field) or 0))
                except (TypeError, ValueError):
                    pass
            try:
                publication["authority"] = max(publication["authority"], int(m.get("authority") or 0))
            except (TypeError, ValueError):
                pass
        sov[r.get("platform") or "unknown"] += 1
    sov_total = sum(sov.values()) or 1

    return {
        "total": len(records),
        "posts": posts,
        "replies": replies,
        "total_reach": total_reach,
        "total_ave": total_ave,
        "by_channel": [{"channel": k, "total": v} for k, v in by_channel.most_common()],
        "by_platform": [{"platform": k, "total": v} for k, v in by_platform.most_common(12)],
        "by_source": [{"source_id": k, "total": v} for k, v in by_source.most_common()],
        "by_publication": sorted(publications.values(), key=lambda item: (-item["total"], item["name"].lower())),
        "by_subchannel": _by_subchannel(records),
        "by_tier": [{"tier": k, "total": v} for k, v in by_tier.most_common()],
        "by_country": [{"country": k, "total": v} for k, v in by_country.most_common(12)],
        "share_of_voice": [
            {"platform": k, "total": v, "share": round(v / sov_total, 4)}
            for k, v in sov.most_common(8)
        ],
        "coverage_types": [{"type": k, "total": v} for k, v in coverage.most_common()],
        "trend": build_trend(records, start, end),
        "recent": records[:50],
    }
