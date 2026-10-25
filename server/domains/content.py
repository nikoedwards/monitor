"""Records, VoC analysis + action workflow, and marketing summaries."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import time

from fastapi import APIRouter, Depends, HTTPException

from .. import ai
from ..connectors.publications import enrich_publication, publication_domain_hint, publication_needs_network
from ..nlp import VOC_ACTION_STATUSES, team_for_records
from ..records import insert_record, record_to_dict, upsert_ad_observation
from ..schemas import ImportIn, RecordIn, VocActionIn, VocActionUpdate
from ..util import compact_key, new_id, utc_now
from .common import build_trend, community_post_views, get_conn, query_records, resolve_window

router = APIRouter(prefix="/api", tags=["content"])

_LEGACY_FIXED_PUBLICATION_TRAFFIC = {25_000, 80_000, 220_000, 500_000, 1_500_000, 5_000_000}
_MARKETING_OVERVIEW_CACHE: dict[tuple, tuple[float, dict]] = {}
_MARKETING_SUMMARY_TTL_SECONDS = 600

VOC_SOURCE_CATALOG = (
    {"key": "sales_reviews", "label": "销售渠道评论", "description": "Amazon、独立站等销售平台的商品评论"},
    {"key": "marketing_videos", "label": "营销视频", "description": "品牌官方账号发布的 YouTube、TikTok 等视频内容"},
    {"key": "social_posts_comments", "label": "社交帖子与评论", "description": "社交媒体、社区和论坛中的帖子、回复与评论"},
    {"key": "creator_comments", "label": "红人视频与评论", "description": "红人达人发布的视频、帖子及其评论反馈"},
    {"key": "app_reviews", "label": "应用商店评论", "description": "App Store 等应用市场的用户评价"},
    {"key": "manual_feedback", "label": "手动反馈", "description": "人工录入或 CSV 导入的客服、访谈与线下反馈"},
)
VOC_SOURCE_KEYS = {item["key"] for item in VOC_SOURCE_CATALOG}
_VIDEO_PLATFORMS = {"youtube", "tiktok", "douyin", "bilibili", "vimeo", "kuaishou"}
_SALES_REVIEW_CHANNELS = {"amazon", "dtc", "other_ecom", "ecommerce", "sales", "retail"}

_P0_RULES = (
    ("safety", "设备或人身安全风险", "product_team", ("起火", "爆炸", "触电", "人身安全", "caught fire", "exploded", "electric shock", "burn hazard")),
    ("privacy_security", "隐私或账号安全风险", "experience_team", ("隐私泄露", "信息泄露", "账号被盗", "数据泄露", "privacy breach", "data breach", "account hacked")),
    ("data_loss", "用户数据不可逆丢失", "product_team", ("数据丢失", "录音丢失", "文件全部丢失", "data loss", "lost all recordings", "lost all my data")),
    ("billing", "未授权或重复扣款", "support_team", ("未授权扣款", "重复扣款", "恶意扣费", "unauthorized charge", "charged twice", "double charged")),
    ("outage", "大范围服务不可用", "experience_team", ("全局故障", "服务全面中断", "所有人都无法使用", "global outage", "service down for everyone", "system-wide outage")),
)


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
    publication_domain: str | None = None,
    publication_name: str | None = None,
    region: str | None = None,
    days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    q: str | None = None,
    limit: int = 200,
    conn: sqlite3.Connection = Depends(get_conn),
):
    filters = {k: v for k, v in locals().items() if k not in {"conn", "limit"}}
    if product_id and channel == "creators":
        filters["product_id"] = None
        filters["matched_product_id"] = product_id
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
def _voice_source_key(record: dict) -> str | None:
    dimension = (record.get("dimension") or "").lower()
    channel = (record.get("channel") or "").lower()
    source_id = (record.get("source_id") or "").lower()
    platform = (record.get("platform") or "").lower()
    data_type = (record.get("data_type") or "").lower()

    if dimension == "sales" and ("review" in source_id or "review" in data_type or channel in _SALES_REVIEW_CHANNELS):
        return "sales_reviews"

    if dimension == "voc":
        if source_id in {"manual", "manual_csv"}:
            return "manual_feedback"
        if channel == "app" or "app_store" in source_id or platform in {"app_store", "google_play"}:
            return "app_reviews"
        if channel in _SALES_REVIEW_CHANNELS or source_id in {"amazon_reviews", "shopify_reviews", "sales_reviews"}:
            return "sales_reviews"
        return "manual_feedback"
    if dimension != "marketing":
        return None
    if channel == "creators":
        return "creator_comments"
    if channel == "community":
        return "social_posts_comments"
    if channel == "social":
        metrics = record.get("metrics") or {}
        content_type = str(metrics.get("content_type") or metrics.get("media_type") or "").lower()
        if platform in _VIDEO_PLATFORMS or content_type in {"video", "short_video", "reel"}:
            return "marketing_videos"
        return "social_posts_comments"
    return None


def _parse_voc_sources(value: str | None) -> set[str]:
    if not value:
        return set(VOC_SOURCE_KEYS)
    selected = {part.strip() for part in value.split(",") if part.strip()} & VOC_SOURCE_KEYS
    return selected or set(VOC_SOURCE_KEYS)


def _load_voice_records(conn: sqlite3.Connection, brand_id: str | None, start: str, end: str, q: str | None = None) -> list[dict]:
    base = {"brand_id": brand_id, "start_date": start, "end_date": end, "q": q}
    candidates: list[dict] = []
    for filters in (
        {**base, "dimension": "voc"},
        {**base, "dimension": "marketing", "channel": "social"},
        {**base, "dimension": "marketing", "channel": "community"},
        {**base, "dimension": "marketing", "channel": "creators"},
    ):
        candidates.extend(query_records(conn, filters, limit=1000))

    records: list[dict] = []
    seen: set[str] = set()
    for record in candidates:
        source_key = _voice_source_key(record)
        record_id = str(record.get("id") or "")
        if not source_key or (record_id and record_id in seen):
            continue
        if record_id:
            seen.add(record_id)
        records.append(_enrich_voice_record({**record, "voice_source": source_key}))
    return sorted(records, key=lambda item: item.get("occurred_at") or "", reverse=True)


def _source_catalog(records: list[dict]) -> list[dict]:
    catalog: list[dict] = []
    for item in VOC_SOURCE_CATALOG:
        source_records = [record for record in records if record.get("voice_source") == item["key"]]
        counts = Counter(record.get("sentiment") or "neutral" for record in source_records)
        topic_counts = Counter(topic for record in source_records for topic in (record.get("topics") or []))
        negative = counts.get("negative", 0)
        total = len(source_records)
        representative_record = next(
            (record for record in source_records if record.get("sentiment") == "negative"),
            source_records[0] if source_records else {},
        )
        summary = (
            representative_record.get("problem_summary") or _problem_summary(representative_record)
            if source_records
            else "当前时间范围内暂无反馈。"
        )
        catalog.append({
            **item,
            "count": total,
            "positive": counts.get("positive", 0),
            "neutral": counts.get("neutral", 0),
            "negative": negative,
            "negative_rate": round(negative / total, 4) if total else 0.0,
            "summary": summary,
            "problem_summary": summary,
            "representative": _representative_feedback([representative_record] if representative_record else source_records),
            "top_topics": [{"topic": topic, "count": count} for topic, count in topic_counts.most_common(3)],
            "hot_risk_count": sum(1 for record in source_records if record.get("hot_risk")),
        })
    return catalog


def _enrich_voice_record(record: dict) -> dict:
    """Add a readable summary and a transparent high-velocity risk signal."""
    metrics = record.get("metrics") or {}
    body = (record.get("body") or record.get("title") or "").strip()
    sentiment = record.get("sentiment") or "neutral"
    problem_summary = _problem_summary(record)
    highlight_terms = _highlight_terms(record)

    def metric_number(*keys: str) -> float:
        for key in keys:
            try:
                value = float(metrics.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                return value
        return 0

    engagement = metric_number("engagement")
    comments = metric_number("comments", "replies")
    likes = metric_number("likes", "upvotes")
    interactions = max(engagement, comments + likes)
    occurred_at = record.get("occurred_at")
    age_hours = 9999.0
    if occurred_at:
        try:
            parsed = datetime.fromisoformat(str(occurred_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_hours = max(0.25, (datetime.now(timezone.utc) - parsed).total_seconds() / 3600)
        except (TypeError, ValueError):
            pass
    velocity = interactions / age_hours if interactions else 0.0
    hot_risk = sentiment == "negative" and ((age_hours <= 24 and interactions >= 20) or (age_hours <= 72 and interactions >= 50) or velocity >= 8)
    return {
        **record,
        "problem_summary": problem_summary,
        "feedback_summary": problem_summary,
        "highlight_terms": highlight_terms,
        "interaction_count": int(interactions),
        "interaction_velocity": round(velocity, 2),
        "age_hours": round(age_hours, 1) if age_hours < 9999 else None,
        "hot_risk": hot_risk,
    }


_TOPIC_PROBLEM_LABELS = {
    "support": "客服与售后支持",
    "experience": "产品使用体验",
    "feature": "功能与系统集成",
    "quality": "产品质量与稳定性",
    "price": "价格与订阅成本",
    "delivery": "物流与交付",
    "retail": "购买渠道与订单",
    "ads": "广告与内容表达",
    "creator": "红人内容",
    "pr": "品牌口碑与媒体报道",
}


def _problem_summary(record: dict) -> str:
    """Turn a raw feedback item into a specific, readable Chinese one-liner."""
    title = str(record.get("title") or "").strip()
    body = str(record.get("body") or "").strip()
    text = f"{title} {body}".strip()
    lowered = text.lower()
    topics = record.get("topics") or []
    intent = record.get("intent") or ""
    sentiment = record.get("sentiment") or "neutral"

    if ("start recording" in lowered or "stop recording" in lowered) and ("shortcut" in lowered or "siri" in lowered or "app intents" in lowered):
        return "用户希望通过 Apple 快捷指令和 Siri 开始、停止录音，并查看录音状态。"
    if ("magnetic case" in lowered or "magnetic" in lowered) and ("iphone duo" in lowered or "back panel" in lowered) and ("doesn't work" in lowered or "does not work" in lowered or "too big" in lowered or "doesn't fit" in lowered):
        return "磁吸保护壳与 iPhone Duo 尺寸不兼容，保护壳过大并影响手机折叠。"
    if any(term in lowered for term in ("doesn't fit", "does not fit", "won't fit", "cannot fit")):
        return "产品尺寸或适配性存在问题，无法与目标设备正常配合使用。"
    if any(term in lowered for term in ("crash", "crashes", "crashed", "keeps crashing", "闪退", "崩溃")):
        return "应用或游戏频繁崩溃，用户无法正常完成使用。"
    if any(term in lowered for term in ("refund", "退款", "charged twice", "double charged", "unauthorized charge")):
        return "用户认为收费存在问题，提出退款或对未授权扣款进行投诉。"
    if any(term in lowered for term in ("repair", "rma", "replacement", "no response", "status update", "delays")) and ("support" in lowered or "service" in lowered or "died" in lowered):
        return "产品故障后的维修与客服响应周期过长，用户长时间得不到明确进展。"
    if "battery" in lowered and any(term in lowered for term in ("drain", "draining", "dies quickly", "续航", "耗电")):
        return "设备耗电过快，续航表现没有达到用户预期。"
    if "ct clamp" in lowered and any(term in lowered for term in ("not reading", "incorrect", "wrong", "self test")):
        return "智能电表无法正确读取 CT 夹数据，导致电池放电功率异常。"

    topic = next((str(item) for item in topics if item in _TOPIC_PROBLEM_LABELS), "")
    topic_label = _TOPIC_PROBLEM_LABELS.get(topic, "产品使用")
    if intent == "request":
        if topic == "feature" or any(term in lowered for term in ("feature", "integration", "workflow", "shortcut", "siri")):
            return "用户希望补充功能或系统集成能力，以减少操作步骤并提升使用效率。"
        return f"用户希望改善{topic_label}，并提出了具体功能或服务需求。"
    if sentiment == "positive":
        return f"用户对{topic_label}整体评价积极，认可当前产品或服务表现。"
    if sentiment == "negative":
        if topic == "support":
            return "用户对客服或售后支持不满，认为问题处理不及时或没有得到解决。"
        return f"用户对{topic_label}不满，认为当前表现影响了正常使用。"
    if title and not title.lower().startswith(("unknown", "untitled")):
        return f"用户提到：{title[:72]}{'…' if len(title) > 72 else ''}。"
    return f"用户围绕{topic_label}分享了使用反馈，暂未形成明确的正负向结论。"


def _highlight_terms(record: dict) -> list[str]:
    text = f"{record.get('title') or ''} {record.get('body') or ''}"
    lowered = text.lower()
    terms: list[str] = []
    explanation = record.get("sentiment_explanation") or {}
    for key in ("positive_terms", "negative_terms", "negation_terms"):
        values = explanation.get(key) if isinstance(explanation, dict) else []
        if isinstance(values, list):
            terms.extend(str(value).strip() for value in values if str(value).strip())
    patterns = (
        r"(?:doesn't|does not|isn't|is not|can't|cannot|won't|will not)\s+(?:work|fit|respond|start|stop)(?:\s+(?:on|with|for|the|it|this|that|device|phone|app)){0,4}",
        r"too\s+(?:big|small|slow|expensive|late|difficult)",
        r"(?:keeps?\s+)?crash(?:es|ed|ing)?",
        r"hangs?\s+past\s+the\s+(?:bottom|back|edge)\s+(?:edge|panel|of\s+the\s+phone)",
        r"no\s+(?:response|meaningful status update)",
    )
    for pattern in patterns:
        terms.extend(match.group(0).strip() for match in re.finditer(pattern, text, flags=re.IGNORECASE))
    unique: list[str] = []
    seen: set[str] = set()
    for term in sorted(terms, key=len, reverse=True):
        key = term.lower()
        if len(term) >= 3 and key in lowered and key not in seen:
            unique.append(term)
            seen.add(key)
    return unique[:8]


def _representative_feedback(records: list[dict]) -> str:
    record = next((item for item in records if item.get("sentiment") == "negative"), records[0] if records else {})
    text = (record.get("body") or record.get("title") or "").strip()
    return text if len(text) <= 160 else f"{text[:157]}..."


def _p0_match(record: dict) -> tuple[str, str, str] | None:
    if record.get("sentiment") != "negative":
        return None
    text = f"{record.get('title') or ''} {record.get('body') or ''}".lower()
    for key, label, team, terms in _P0_RULES:
        for term in terms:
            lowered = term.lower()
            matched = bool(re.search(rf"\b{re.escape(lowered)}\b", text)) if lowered.isascii() else lowered in text
            if matched:
                return key, label, team
    return None


def _p0_summary(records: list[dict]) -> dict:
    grouped: dict[str, dict] = {}
    for record in records:
        matched = _p0_match(record)
        if not matched:
            continue
        key, label, team = matched
        grouped.setdefault(key, {"key": key, "label": label, "owner_team": team, "records": []})["records"].append(record)
    issues = []
    for group in grouped.values():
        items = group.pop("records")
        first = items[0]
        issues.append({
            **group,
            "count": len(items),
            "representative": _representative_feedback(items),
            "record_id": first.get("id"),
            "url": first.get("url"),
            "latest_at": first.get("occurred_at"),
            "source_keys": list(dict.fromkeys(item.get("voice_source") for item in items if item.get("voice_source"))),
        })
    issues.sort(key=lambda item: (item["count"], item.get("latest_at") or ""), reverse=True)
    return {"total": sum(item["count"] for item in issues), "issue_count": len(issues), "issues": issues}


def _issue_priority(records: list[dict]) -> str:
    if any(_p0_match(record) for record in records):
        return "urgent"
    negative = sum(1 for record in records if record.get("sentiment") == "negative")
    complaints = sum(1 for record in records if record.get("intent") == "complaint")
    rate = negative / len(records) if records else 0.0
    if negative >= 5 or complaints >= 5 or (negative >= 3 and rate >= 0.5):
        return "high"
    if negative >= 2 or complaints >= 2:
        return "medium"
    return "low"


def _topic_breakdown(records: list[dict]) -> list[dict]:
    issue_groups: dict[str, dict] = {}
    for record in records:
        topics = [str(topic) for topic in (record.get("topics") or []) if str(topic).strip()]
        if not topics:
            continue
        problem_summary = record.get("problem_summary") or _problem_summary(record)
        group_key = " ".join(str(problem_summary).split()).casefold()
        group = issue_groups.setdefault(
            group_key,
            {"records": {}, "topics": set(), "teams": set(), "problem_summary": problem_summary},
        )
        record_key = str(record.get("id") or f"record_{id(record)}")
        group["records"][record_key] = record
        group["topics"].update(topics)
        for topic in topics:
            group["teams"].add(team_for_records([record], topic))

    out = []
    team_order = {"marketing_team": 0, "support_team": 1, "product_team": 2, "experience_team": 3}
    for group in issue_groups.values():
        items = list(group["records"].values())
        negative = sum(1 for r in items if r.get("sentiment") == "negative")
        if not negative:
            continue
        topics = sorted(group["topics"])
        suggested_teams = sorted(group["teams"], key=lambda team: (team_order.get(team, 99), team))
        source_counts = Counter(item.get("voice_source") for item in items if item.get("voice_source"))
        representative_record = next((item for item in items if item.get("sentiment") == "negative"), items[0])
        out.append({
            "topic": topics[0],
            "topics": topics,
            "total": len(items),
            "negative": negative,
            "negative_rate": round(negative / len(items), 4) if items else 0.0,
            "suggested_team": suggested_teams[0] if suggested_teams else team_for_records(items, topics[0]),
            "suggested_teams": suggested_teams,
            "priority": _issue_priority(items),
            "representative": _representative_feedback(items),
            "problem_summary": group["problem_summary"],
            "source_keys": [key for key, _ in source_counts.most_common(3)],
            "latest_at": max((item.get("occurred_at") or "" for item in items), default=""),
        })
    return sorted(out, key=lambda item: (item["negative"], item["negative_rate"], item["total"]), reverse=True)[:10]


def _channel_breakdown(records: list[dict]) -> list[dict]:
    counts = Counter(r.get("voice_source") or "unknown" for r in records)
    negatives = Counter(r.get("voice_source") or "unknown" for r in records if r.get("sentiment") == "negative")
    out = []
    for source_key, total in counts.items():
        negative = negatives.get(source_key, 0)
        out.append({
            "source_key": source_key,
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
                "priority": topic["priority"],
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
    sources: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = resolve_window(days, start_date, end_date)
    all_records = _load_voice_records(conn, brand_id, start, end, q)
    selected_sources = _parse_voc_sources(sources)
    records = [record for record in all_records if record.get("voice_source") in selected_sources]
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
        "source_catalog": _source_catalog(all_records),
        "selected_sources": sorted(selected_sources),
        "topics": _topic_breakdown(records),
        "alerts": _build_alerts(records),
        "p0": _p0_summary(records),
        "actions": {
            "total": len(actions),
            "open": len(actions) - closed,
            "closed": closed,
            "closure_rate": round(closed / len(actions), 4) if actions else 0.0,
        },
    }


@router.get("/voc/records")
def voc_records(
    brand_id: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    q: str | None = None,
    sources: str | None = None,
    sentiment: str | None = None,
    limit: int = 40,
    conn: sqlite3.Connection = Depends(get_conn),
):
    start, end = resolve_window(days, start_date, end_date)
    selected_sources = _parse_voc_sources(sources)
    records = [
        record
        for record in _load_voice_records(conn, brand_id, start, end, q)
        if record.get("voice_source") in selected_sources and (not sentiment or record.get("sentiment") == sentiment)
    ]
    return {"records": records[:max(1, min(limit, 200))]}


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


def _ad_duration_days(started_at: str | None, stopped_at: str | None, end_at: str | None) -> float | None:
    if not started_at or not (stopped_at or end_at):
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(stopped_at or end_at).replace("Z", "+00:00"))
        if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None: end = end.replace(tzinfo=timezone.utc)
        return round(max(0.0, (end - start).total_seconds() / 86400), 1)
    except (TypeError, ValueError):
        return None


def _ad_entity_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    for key in ("publisher_platforms_json", "link_urls_json", "raw_json"):
        target = key.removesuffix("_json")
        try: item[target] = json.loads(item.pop(key) or ("{}" if key == "raw_json" else "[]"))
        except (TypeError, ValueError): item[target] = {} if key == "raw_json" else []
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    snapshot = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
    media_items: list[dict] = []
    for key in ("cards", "images", "videos", "extra_images", "extra_videos"):
        values = snapshot.get(key) or []
        if isinstance(values, list):
            media_items.extend(value for value in values if isinstance(value, dict))
    thumbnail_url = raw.get("thumbnail_url")
    video_url = raw.get("video_url")
    for media in media_items:
        thumbnail_url = thumbnail_url or media.get("resized_image_url") or media.get("original_image_url") or media.get("video_preview_image_url")
        video_url = video_url or media.get("video_hd_url") or media.get("video_sd_url")
    link_urls = item.get("link_urls")
    landing_url = link_urls[0] if isinstance(link_urls, list) and link_urls else None
    if not landing_url:
        # Google Transparency payloads keep the destination in the raw
        # snapshot as well.  This fallback also makes older entities visible
        # before their next lifecycle refresh repopulates link_urls_json.
        landing_url = raw.get("landing_url") or raw.get("ad_landing_url")
        if not landing_url:
            preview_fields = raw.get("preview_fields")
            if isinstance(preview_fields, dict):
                landing_url = (
                    preview_fields.get("destination_url")
                    or preview_fields.get("final_url")
                    or preview_fields.get("visible_url")
                )
    item.update({
        "source": item.get("source_id"), "source_ad_id": item.get("ad_external_id"),
        "advertiser_name": item.get("page_name"), "body": item.get("creative_body"),
        "delivery_start": item.get("started_at"), "delivery_stop": item.get("stopped_at"),
        "first_seen": item.get("first_seen_at"), "last_seen": item.get("last_seen_at"),
        "status": "active" if item.get("active_status") == "active" else "stopped",
        "platforms": item.get("publisher_platforms") or [],
        "creative_url": thumbnail_url or item.get("snapshot_url"),
        "thumbnail_url": thumbnail_url,
        "media_url": thumbnail_url or video_url,
        "video_url": video_url,
        "landing_url": landing_url,
        "spend": raw.get("spend"), "impressions": raw.get("impressions"), "reach": raw.get("reach"),
    })
    item["duration_days"] = _ad_duration_days(item.get("started_at"), item.get("stopped_at"), item.get("last_seen_at"))
    item["persistence_score"] = round(min(100.0, max(0.0, float(item.get("duration_days") or 0) / 30.0 * 100.0)), 1)
    item["evidence_level"] = "B" if item.get("spend") or item.get("impressions") or item.get("reach") else "C"
    return item


@router.get("/marketing/ads")
def marketing_ads(brand_id: str | None = None, days: int = 30, start_date: str | None = None, end_date: str | None = None, platform: str | None = None, source: str | None = None, status: str | None = None, q: str | None = None, limit: int = 100, conn: sqlite3.Connection = Depends(get_conn)):
    start, end = resolve_window(days, start_date, end_date)
    where = ["brand_id = ?", "date(first_seen_at) <= date(?)", "date(COALESCE(stopped_at, last_seen_at)) >= date(?)"]
    params: list[object] = [brand_id, end, start]
    if platform: where += ["platform = ?"]; params.append(platform)
    if status == "new": where.append("date(first_seen_at) BETWEEN date(?) AND date(?)"); params += [start, end]
    elif status in {"active", "inactive", "stopped"}: where.append("active_status = ?"); params.append("inactive" if status == "stopped" else status)
    if source: where.append("source_id = ?"); params.append(source)
    if q: where.append("(page_name LIKE ? OR creative_body LIKE ?)"); params += [f"%{q}%", f"%{q}%"]
    rows = conn.execute(f"SELECT * FROM ad_entities WHERE {' AND '.join(where)} ORDER BY last_seen_at DESC LIMIT ?", (*params, max(1, min(limit, 500)))).fetchall()
    return {"ads": [_ad_entity_dict(row) for row in rows], "start_date": start, "end_date": end}


@router.get("/marketing/ads/summary")
@router.get("/marketing/ads-summary")
def marketing_ads_summary(brand_id: str | None = None, days: int = 30, start_date: str | None = None, end_date: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    start, end = resolve_window(days, start_date, end_date)
    rows = conn.execute("SELECT * FROM ad_entities WHERE brand_id = ? AND date(first_seen_at) <= date(?) AND date(COALESCE(stopped_at, last_seen_at)) >= date(?) ORDER BY last_seen_at DESC", (brand_id, end, start)).fetchall()
    ads = [_ad_entity_dict(row) for row in rows]
    active = [a for a in ads if a.get("active_status") == "active"]
    durations = [a["duration_days"] for a in ads if a.get("duration_days") is not None]
    daily = [dict(row) for row in conn.execute("SELECT observed_date AS date, COUNT(*) AS total, SUM(CASE WHEN active_status = 'active' THEN 1 ELSE 0 END) AS active FROM ad_snapshots WHERE brand_id = ? AND date(observed_date) BETWEEN date(?) AND date(?) GROUP BY observed_date ORDER BY observed_date", (brand_id, start, end)).fetchall()]
    new_by_day = Counter(str(a.get("first_seen_at") or "")[:10] for a in ads)
    stopped_by_day = Counter(str(a.get("stopped_at") or "")[:10] for a in ads if a.get("stopped_at"))
    for point in daily: point["new"] = new_by_day.get(point.get("date"), 0); point["stopped"] = stopped_by_day.get(point.get("date"), 0)
    events = conn.execute("SELECT event_type, event_at, COUNT(*) AS total FROM ad_events WHERE brand_id = ? AND date(event_at) BETWEEN date(?) AND date(?) GROUP BY event_type, event_at ORDER BY event_at DESC LIMIT 8", (brand_id, start, end)).fetchall()
    return {"total": len(ads), "active": len(active), "active_ads": len(active), "inactive": len(ads)-len(active), "stopped_ads": len(ads)-len(active), "new_ads": sum(1 for a in ads if str(a.get("first_seen_at") or "")[:10] >= start), "avg_duration_days": round(sum(durations)/len(durations), 1) if durations else None, "avg_lifetime_days": round(sum(durations)/len(durations), 1) if durations else None, "persistence_score": round(sum(a.get("persistence_score", 0) for a in ads)/len(ads), 1) if ads else 0, "evidence_level": "B" if any(a.get("evidence_level") == "B" for a in ads) else "C", "by_source": [{"source": k, "source_id": k, "total": v} for k,v in Counter(a.get("source") or "unknown" for a in ads).most_common()], "by_platform": [{"platform": k, "total": v} for k,v in Counter(a.get("platform") or "unknown" for a in ads).most_common()], "by_page": [{"page_name": k, "total": v} for k,v in Counter(a.get("page_name") or "unknown" for a in ads).most_common(12)], "by_status": [{"status": "active", "total": len(active)}, {"status": "stopped", "total": len(ads)-len(active)}], "alerts": [{"id": f"{r['event_type']}-{r['event_at']}", "type": r["event_type"], "title": f"{r['event_type']} {r['total']} 条", "detail": "来自广告库的生命周期或素材变化事件", "detected_at": r["event_at"]} for r in events], "trend": daily, "start_date": start, "end_date": end}


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
    community_views = [views for r in records if (views := community_post_views(r)) is not None]
    social_totals = {"views": 0, "likes": 0, "comments": 0, "engagement": 0}
    for record in records:
        metrics = record.get("metrics") or {}
        for key in social_totals:
            try:
                social_totals[key] += int(metrics.get(key) or 0)
            except (TypeError, ValueError):
                pass

    publication_names: dict[str, str] = {}
    publication_name_keys: dict[str, str] = {}
    publication_name_counts: Counter = Counter()
    for record in records:
        metrics = record.get("metrics") or {}
        domain = str(metrics.get("publication_domain") or "").strip().lower()
        if record.get("channel") != "media":
            continue
        name = record.get("platform") or domain or "Unknown publication"
        name_key = compact_key(name)
        publication_name_keys.setdefault(name_key, name)
        publication_name_counts[name_key] += 1
        if domain:
            publication_names.setdefault(domain, name)

    domain_hints: dict[str, str] = {}
    for row in conn.execute("SELECT domain, name FROM publications").fetchall():
        key = compact_key(row["name"])
        if key in publication_name_keys:
            domain_hints.setdefault(key, row["domain"])
    for key, name in publication_name_keys.items():
        hinted_domain = domain_hints.get(key) or publication_domain_hint(name)
        if hinted_domain:
            publication_names.setdefault(hinted_domain, name)

    current_publications: dict[str, dict] = {}
    network_refresh_budget = 10
    ranked_publication_names = sorted(
        publication_names.items(),
        key=lambda item: publication_name_counts[compact_key(item[1])],
        reverse=True,
    )
    for domain, name in ranked_publication_names:
        needs_network = publication_needs_network(conn, domain)
        allow_network = not needs_network or network_refresh_budget > 0
        if needs_network and allow_network:
            network_refresh_budget -= 1
        current_publications[domain] = enrich_publication(conn, name, domain, allow_network=allow_network)
    current_publications_by_name: dict[str, dict] = {}
    for publication in current_publications.values():
        current_publications_by_name.setdefault(compact_key(publication.get("name")), publication)
        current_publications_by_name.setdefault(compact_key(publication.get("domain")), publication)

    by_tier: Counter = Counter()
    by_country: Counter = Counter()
    sov: Counter = Counter()
    publications: dict[str, dict] = {}
    total_reach = 0
    total_ave = 0
    for r in records:
        m = r.get("metrics") or {}
        domain = str(m.get("publication_domain") or "").strip().lower()
        record_name = r.get("platform") or domain or "Unknown publication"
        current_publication = None
        if r.get("channel") == "media":
            current_publication = current_publications.get(domain) or current_publications_by_name.get(compact_key(record_name))
            if current_publication and not domain:
                domain = current_publication.get("domain") or ""
        media_tier = (current_publication or {}).get("tier") or m.get("media_tier")
        country = (current_publication or {}).get("country") or m.get("country")
        if media_tier:
            by_tier[media_tier] += 1
        if country:
            by_country[country] += 1
        if r.get("channel") != "media":
            try:
                total_reach += int(m.get("monthly_traffic") or m.get("estimated_reach") or 0)
            except (TypeError, ValueError):
                pass
        try:
            total_ave += int(m.get("ave") or 0)
        except (TypeError, ValueError):
            pass
        if r.get("channel") == "media":
            name = (current_publication or {}).get("name") or record_name
            key = domain or name
            publication = publications.setdefault(key, {
                "name": name,
                "domain": domain,
                "total": 0,
                "monthly_traffic": int((current_publication or {}).get("est_monthly_traffic") or 0),
                "traffic_lower": int((current_publication or {}).get("traffic_lower") or m.get("traffic_lower") or 0),
                "traffic_upper": int((current_publication or {}).get("traffic_upper") or m.get("traffic_upper") or 0),
                "popularity_rank": (current_publication or {}).get("popularity_rank") or m.get("popularity_rank"),
                "traffic_source": (current_publication or {}).get("source") or m.get("traffic_source") or "historical",
                "traffic_confidence": (current_publication or {}).get("traffic_confidence") or m.get("traffic_confidence") or "low",
                "traffic_as_of": (current_publication or {}).get("traffic_as_of") or m.get("traffic_as_of") or "",
                "authority": int((current_publication or {}).get("authority") or 0),
                "tier": media_tier or "unknown",
                "country": country or "",
            })
            publication["total"] += 1
            if current_publication is None:
                for field in ("monthly_traffic", "estimated_reach"):
                    try:
                        candidate = int(m.get(field) or 0)
                        if candidate not in _LEGACY_FIXED_PUBLICATION_TRAFFIC:
                            publication["monthly_traffic"] = max(publication["monthly_traffic"], candidate)
                    except (TypeError, ValueError):
                        pass
                try:
                    publication["authority"] = max(publication["authority"], int(m.get("authority") or 0))
                except (TypeError, ValueError):
                    pass
                if publication["monthly_traffic"] <= 0:
                    publication["traffic_source"] = "unavailable"
                    publication["traffic_confidence"] = "low"
        sov[r.get("platform") or "unknown"] += 1
    sov_total = sum(sov.values()) or 1

    total_reach += sum(int(item.get("monthly_traffic") or 0) for item in publications.values())

    return {
        "total": len(records),
        "posts": posts,
        "replies": replies,
        "total_community_views": sum(community_views) if community_views else None,
        "community_view_records": len(community_views),
        "total_views": social_totals["views"],
        "total_likes": social_totals["likes"],
        "total_comments": social_totals["comments"],
        "total_engagement": social_totals["engagement"],
        "engagement_rate": round(social_totals["engagement"] / social_totals["views"], 6)
        if social_totals["views"]
        else None,
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


# -------------------------------------------------------- marketing overview
def _marketing_number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _marketing_delta(current: object, previous: object) -> dict:
    if current is None:
        return {"value": None, "previous": previous, "delta": None, "delta_pct": None}
    current_value = _marketing_number(current)
    previous_value = _marketing_number(previous)
    delta = current_value - previous_value
    return {
        "value": current,
        "previous": previous,
        "delta": round(delta, 2),
        "delta_pct": None if previous_value == 0 else round(delta / abs(previous_value) * 100, 1),
    }


def _marketing_status(conn: sqlite3.Connection, brand_id: str, has_data: bool) -> str:
    if has_data:
        return "ready"
    try:
        configured = conn.execute(
            "SELECT COUNT(*) AS c FROM links WHERE brand_id = ? AND dimension = 'marketing'",
            (brand_id,),
        ).fetchone()["c"]
    except sqlite3.OperationalError:
        configured = 0
    return "configured_no_data" if configured else "empty"


def _marketing_channel_snapshot(conn: sqlite3.Connection, brand_id: str, channel: str, start: str, end: str) -> dict:
    records = query_records(
        conn,
        {"brand_id": brand_id, "dimension": "marketing", "channel": channel, "start_date": start, "end_date": end},
        limit=5000,
    )
    totals = {"views": 0, "likes": 0, "comments": 0, "engagement": 0}
    for record in records:
        metrics = record.get("metrics") or {}
        for key in totals:
            totals[key] += int(_marketing_number(metrics.get(key)))
    posts = sum(1 for record in records if record.get("data_type") != "community_reply")
    replies = sum(1 for record in records if record.get("data_type") == "community_reply")
    last_data_at = max((record.get("occurred_at") or "" for record in records), default=None)
    base = {
        "status": _marketing_status(conn, brand_id, bool(records)),
        "total": len(records),
        "posts": posts,
        "replies": replies,
        "total_views": totals["views"],
        "total_likes": totals["likes"],
        "total_comments": totals["comments"],
        "total_engagement": totals["engagement"],
        "trend": build_trend(records, start, end),
        "last_data_at": last_data_at,
    }
    if channel == "media":
        reach = 0
        publications: set[str] = set()
        ave = 0
        tiers = Counter()
        for record in records:
            metrics = record.get("metrics") or {}
            domain = str(metrics.get("publication_domain") or record.get("platform") or "unknown").strip().lower()
            if domain not in publications:
                publications.add(domain)
                reach += int(_marketing_number(metrics.get("monthly_traffic") or metrics.get("estimated_reach")))
            ave += int(_marketing_number(metrics.get("ave")))
            if metrics.get("media_tier"):
                tiers[str(metrics["media_tier"])] += 1
        base.update({"mentions": len(records), "total_reach": reach, "total_ave": ave, "publication_count": len(publications), "by_tier": [{"tier": key, "total": value} for key, value in tiers.most_common()]})
    elif channel == "creators":
        handles = set()
        collab_posts = 0
        sponsored_posts = 0
        for record in records:
            metrics = record.get("metrics") or {}
            handle = metrics.get("author_handle") or record.get("author")
            if handle:
                handles.add(str(handle).lower())
            collab_posts += 1 if metrics.get("is_collab") else 0
            sponsored_posts += 1 if metrics.get("is_sponsored") else 0
        base.update({"creator_count": len(handles), "collab_posts": collab_posts, "sponsored_posts": sponsored_posts})
    elif channel == "social":
        base.update({"content_count": len(records), "engagement_rate": round(totals["engagement"] / totals["views"], 6) if totals["views"] else None})
    elif channel == "community":
        subchannels = set()
        for record in records:
            metrics = record.get("metrics") or {}
            sub = metrics.get("subreddit") or metrics.get("scope") or record.get("platform")
            if sub:
                subchannels.add(str(sub))
        base.update({"subchannel_count": len(subchannels), "data_source_count": len({record.get("source_id") for record in records if record.get("source_id")})})
    return base


def _marketing_ads_snapshot(conn: sqlite3.Connection, brand_id: str, start: str, end: str) -> dict:
    try:
        rows = conn.execute(
            "SELECT * FROM ad_entities WHERE brand_id = ? AND date(first_seen_at) <= date(?) AND date(COALESCE(stopped_at, last_seen_at)) >= date(?) ORDER BY last_seen_at DESC",
            (brand_id, end, start),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    ads = [dict(row) for row in rows]
    active = [row for row in ads if row.get("active_status") == "active"]
    new_ads = sum(1 for row in ads if start <= (row.get("first_seen_at") or "")[:10] <= end)
    stopped_ads = sum(1 for row in ads if start <= (row.get("stopped_at") or "")[:10] <= end)
    changed_ads = sum(1 for row in ads if (row.get("first_seen_at") or "")[:10] < start and start <= (row.get("updated_at") or "")[:10] <= end)
    try:
        daily_rows = conn.execute(
            "SELECT observed_date AS date, COUNT(*) AS total, SUM(CASE WHEN active_status = 'active' THEN 1 ELSE 0 END) AS active FROM ad_snapshots WHERE brand_id = ? AND date(observed_date) BETWEEN date(?) AND date(?) GROUP BY observed_date ORDER BY observed_date",
            (brand_id, start, end),
        ).fetchall()
        trend = [dict(row) for row in daily_rows]
    except sqlite3.OperationalError:
        trend = []
    for point in trend:
        point["new"] = sum(1 for row in ads if (row.get("first_seen_at") or "")[:10] == point.get("date"))
        point["stopped"] = sum(1 for row in ads if (row.get("stopped_at") or "")[:10] == point.get("date"))
    return {
        "status": _marketing_status(conn, brand_id, bool(ads)),
        "total_ads": len(ads), "active_ads": len(active), "new_ads": new_ads,
        "stopped_ads": stopped_ads, "changed_ads": changed_ads, "trend": trend,
        "last_data_at": max((row.get("last_seen_at") or "" for row in ads), default=None),
    }


def _marketing_previous_window(start: str, end: str) -> tuple[str, str]:
    start_day, end_day = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    span = (end_day - start_day).days + 1
    previous_end = start_day - timedelta(days=1)
    return (previous_end - timedelta(days=span - 1)).isoformat(), previous_end.isoformat()


def _marketing_overview_payload(conn: sqlite3.Connection, brand_id: str, start: str, end: str) -> dict:
    previous_start, previous_end = _marketing_previous_window(start, end)
    channels = {key: _marketing_channel_snapshot(conn, brand_id, key, start, end) for key in ("media", "creators", "social", "community")}
    channels["ads"] = _marketing_ads_snapshot(conn, brand_id, start, end)
    previous_channels = {key: _marketing_channel_snapshot(conn, brand_id, key, previous_start, previous_end) for key in ("media", "creators", "social", "community")}
    previous_channels["ads"] = _marketing_ads_snapshot(conn, brand_id, previous_start, previous_end)
    content_total = sum(channels[key].get("total", 0) for key in ("media", "creators", "social", "community"))
    previous_content_total = sum(previous_channels[key].get("total", 0) for key in ("media", "creators", "social", "community"))
    total_views = sum(channels[key].get("total_views", 0) for key in ("media", "creators", "social", "community"))
    total_engagement = sum(channels[key].get("total_engagement", 0) for key in ("media", "creators", "social", "community"))
    content_ready = content_total > 0
    kpis = {
        "content_total": _marketing_delta(content_total if content_ready else None, previous_content_total if previous_content_total else None),
        "total_views": _marketing_delta(total_views if content_ready else None, sum(previous_channels[key].get("total_views", 0) for key in previous_channels if key != "ads") or None),
        "total_engagement": _marketing_delta(total_engagement if content_ready else None, sum(previous_channels[key].get("total_engagement", 0) for key in previous_channels if key != "ads") or None),
        "media_reach": _marketing_delta(channels["media"].get("total_reach") if channels["media"].get("status") == "ready" else None, previous_channels["media"].get("total_reach") if previous_channels["media"].get("status") == "ready" else None),
        "ads_total": _marketing_delta(channels["ads"].get("total_ads") if channels["ads"].get("status") == "ready" else None, previous_channels["ads"].get("total_ads") if previous_channels["ads"].get("status") == "ready" else None),
        "ads_active": _marketing_delta(channels["ads"].get("active_ads") if channels["ads"].get("status") == "ready" else None, previous_channels["ads"].get("active_ads") if previous_channels["ads"].get("status") == "ready" else None),
        "creator_count": _marketing_delta(channels["creators"].get("creator_count") if channels["creators"].get("status") == "ready" else None, previous_channels["creators"].get("creator_count") if previous_channels["creators"].get("status") == "ready" else None),
        "community_replies": _marketing_delta(channels["community"].get("replies") if channels["community"].get("status") == "ready" else None, previous_channels["community"].get("replies") if previous_channels["community"].get("status") == "ready" else None),
    }
    signals = []
    if channels["ads"].get("changed_ads") or channels["ads"].get("new_ads") or channels["ads"].get("stopped_ads"):
        signals.append({"domain": "ads", "severity": "medium", "title": "广告素材有生命周期变化", "detail": f"新增 {channels['ads'].get('new_ads', 0)} 条、停止 {channels['ads'].get('stopped_ads', 0)} 条、更新 {channels['ads'].get('changed_ads', 0)} 条。", "href": f"/brand/{brand_id}/marketing"})
    if channels["media"].get("total_reach", 0) and channels["media"].get("total", 0):
        signals.append({"domain": "media", "severity": "low", "title": "媒体声量已覆盖", "detail": f"本期收录 {channels['media']['total']} 篇报道，估算触达 {channels['media']['total_reach']:,}。", "href": f"/brand/{brand_id}/marketing"})
    for key, label in (("creators", "红人达人"), ("social", "社交媒体"), ("community", "社群")):
        previous_total = previous_channels[key].get("total", 0)
        current_total = channels[key].get("total", 0)
        if current_total and previous_total and current_total < previous_total * 0.7:
            signals.append({"domain": key, "severity": "medium", "title": f"{label}声量下降", "detail": f"本期 {current_total:,} 条，较上一周期减少 {previous_total - current_total:,} 条。", "href": f"/brand/{brand_id}/marketing"})
    severity_order = {"high": 0, "medium": 1, "low": 2}
    signals.sort(key=lambda item: severity_order.get(item["severity"], 9))
    yesterday_day = date.fromisoformat(end[:10]) - timedelta(days=1)
    yesterday = {"date": yesterday_day.isoformat(), "channels": {key: _marketing_channel_snapshot(conn, brand_id, key, yesterday_day.isoformat(), yesterday_day.isoformat()) if key != "ads" else _marketing_ads_snapshot(conn, brand_id, yesterday_day.isoformat(), yesterday_day.isoformat()) for key in channels}}
    yesterday["has_data"] = any((value.get("total", 0) or value.get("total_ads", 0)) for value in yesterday["channels"].values())
    coverage = {key: {"status": value.get("status", "empty"), "last_data_at": value.get("last_data_at")} for key, value in channels.items()}
    return {"range": {"start": start, "end": end, "previous_start": previous_start, "previous_end": previous_end}, "kpis": kpis, "modules": channels, "coverage": coverage, "yesterday": yesterday, "signals": signals[:12]}


def _marketing_fallback_summary(payload: dict) -> dict:
    modules = payload.get("modules") or {}
    total = sum((modules.get(key) or {}).get("total", 0) for key in ("media", "creators", "social", "community"))
    ads = modules.get("ads") or {}
    summary = f"本期营销共收录 {total:,} 条内容，广告库有 {ads.get('total_ads', 0):,} 条素材。"
    active = [item for item in (payload.get("signals") or []) if item.get("severity") in {"high", "medium"}]
    highlights = [{"domain": item.get("domain"), "severity": item.get("severity"), "title": item.get("title"), "detail": item.get("detail")} for item in active[:6]]
    focus = [{"domain": item.get("domain"), "action": f"查看{item.get('title', '营销信号')}", "reason": item.get("detail", "")} for item in active[:4]]
    yesterday = payload.get("yesterday") or {}
    yesterday_total = sum((value or {}).get("total", 0) + (value or {}).get("total_ads", 0) for value in (yesterday.get("channels") or {}).values())
    return {"mode": "fallback", "summary": summary, "yesterday_summary": f"{yesterday.get('date', '')}：营销相关新增或变化 {yesterday_total:,} 条。" if yesterday.get("has_data") else f"{yesterday.get('date', '')}：暂无营销数据。", "highlights": highlights, "focus": focus, "caveats": ["当前为确定性系统速览；配置大模型后可生成更完整的营销总结。"], "range": payload.get("range")}


@router.get("/marketing/overview")
def marketing_overview(brand_id: str, days: int = 30, start_date: str | None = None, end_date: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    start, end = resolve_window(days, start_date, end_date)
    return _marketing_overview_payload(conn, brand_id, start, end)


@router.get("/marketing/overview/summary")
def marketing_overview_summary(brand_id: str, days: int = 30, start_date: str | None = None, end_date: str | None = None, refresh: bool = False, conn: sqlite3.Connection = Depends(get_conn)):
    start, end = resolve_window(days, start_date, end_date)
    payload = _marketing_overview_payload(conn, brand_id, start, end)
    cache_key = (brand_id, start, end)
    now = time.monotonic()
    cached = _MARKETING_OVERVIEW_CACHE.get(cache_key)
    if not refresh and cached and now - cached[0] < _MARKETING_SUMMARY_TTL_SECONDS:
        return {**cached[1], "cached": True}
    if not ai.is_configured(conn):
        result = _marketing_fallback_summary(payload)
    else:
        try:
            from .. import ai
            evidence = {"range": payload["range"], "coverage": payload["coverage"], "yesterday": payload["yesterday"], "signals": payload["signals"], "modules": {key: {field: value for field, value in module.items() if field != "trend"} for key, module in payload["modules"].items()}}
            result = {"mode": "ai", **ai.summarize_marketing_overview(conn, evidence)}
        except ai.LlmError:
            result = _marketing_fallback_summary(payload)
            result["caveats"] = ["大模型暂时不可用，当前展示确定性系统速览。"]
    result.update({"range": payload["range"], "cached": False})
    _MARKETING_OVERVIEW_CACHE[cache_key] = (now, result)
    return result
