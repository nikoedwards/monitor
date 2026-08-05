"""Explainable market-share estimates across configured brands.

The endpoint intentionally uses a deterministic proxy model instead of an LLM:
each available signal is converted into a within-cohort share, then blended with
documented weights.  Missing signals are reweighted and surfaced as data gaps.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException

from ..relevance import google_news_record_is_relevant, reddit_search_record_is_relevant
from .common import get_conn, resolve_window

router = APIRouter(prefix="/api/market-share", tags=["market-share"])


MODEL_PRESETS = {
    "balanced": {
        "label": "综合代理模型",
        "description": "兼顾商业结果、App 使用规模与公开讨论热度，适合日常竞品监测。",
        "weights": {"sales": 0.45, "app": 0.25, "conversation": 0.20, "engagement": 0.10},
    },
    "commerce": {
        "label": "商业结果优先",
        "description": "提高销售额/销量权重，适合电商或硬件品牌。",
        "weights": {"sales": 0.65, "app": 0.15, "conversation": 0.15, "engagement": 0.05},
    },
    "attention": {
        "label": "产品热度优先",
        "description": "提高 App、评论讨论与互动权重，适合新品和软件产品。",
        "weights": {"sales": 0.25, "app": 0.30, "conversation": 0.30, "engagement": 0.15},
    },
}

DOWNLOAD_KEYS = ("downloads", "download_count", "installs", "install_count")
COMMENT_KEYS = ("comments", "comment_count", "num_comments")


def _number(value) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _first_metric(metrics: dict, keys: tuple[str, ...]) -> float:
    return max((_number(metrics.get(key)) for key in keys), default=0.0)


def _json_object(value) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _record_is_relevant(row: sqlite3.Row, brand_name: str, raw: dict) -> bool:
    source_id = row["source_id"] or ""
    if source_id not in {"google_news", "reddit_search"}:
        return True
    record = {
        "source_id": source_id,
        "raw": raw,
        "url": row["url"],
        "title": row["title"],
        "body": row["body"],
    }
    if source_id == "google_news":
        return google_news_record_is_relevant(record, brand_name)
    return reddit_search_record_is_relevant(record, brand_name)


def _record_signals(conn: sqlite3.Connection, brand: sqlite3.Row, start: str, end: str) -> dict:
    rows = conn.execute(
        "SELECT source_id, link_id, data_type, dimension, channel, platform, title, body, url, "
        "metrics_json, raw_json FROM records WHERE brand_id = ? "
        "AND substr(occurred_at, 1, 10) >= ? AND substr(occurred_at, 1, 10) <= ?",
        (brand["id"], start, end),
    ).fetchall()

    mentions = 0
    voc_records = 0
    app_reviews = 0
    app_rating_sum = 0.0
    app_rating_count = 0
    comments = 0.0
    engagement = 0.0
    views = 0.0
    explicit_downloads_by_app: dict[str, float] = defaultdict(float)
    app_ratings_by_app: dict[str, float] = defaultdict(float)

    for row in rows:
        metrics = _json_object(row["metrics_json"])
        raw = _json_object(row["raw_json"])
        if not _record_is_relevant(row, brand["name"], raw):
            continue

        dimension = (row["dimension"] or "").lower()
        channel = (row["channel"] or "").lower()
        platform = (row["platform"] or "").lower()
        source_id = (row["source_id"] or "").lower()
        data_type = (row["data_type"] or "").lower()
        is_app = channel == "app" or "app_store" in source_id or platform in {"app_store", "google_play"}

        if dimension == "marketing" and data_type != "community_metric":
            mentions += 1
        if dimension == "voc":
            voc_records += 1

        comment_value = _first_metric(metrics, COMMENT_KEYS)
        if data_type in {"community_reply", "comment", "reply"} and not comment_value:
            comment_value = 1
        comments += comment_value

        record_engagement = _number(metrics.get("engagement"))
        if not record_engagement:
            record_engagement = (
                _number(metrics.get("likes"))
                + comment_value
                + _number(metrics.get("shares"))
                + _number(metrics.get("vote_count"))
            )
        engagement += record_engagement
        views += _number(metrics.get("views"))

        if is_app:
            app_reviews += 1 if dimension == "voc" or "review" in source_id or "review" in data_type else 0
            rating = _number(metrics.get("rating"))
            if rating:
                app_rating_sum += rating
                app_rating_count += 1
            app_key = str(row["link_id"] or source_id or platform or "app")
            explicit_downloads_by_app[app_key] = max(
                explicit_downloads_by_app[app_key], _first_metric(metrics, DOWNLOAD_KEYS)
            )
            app_ratings_by_app[app_key] = max(
                app_ratings_by_app[app_key], _number(metrics.get("rating_count"))
            )

    explicit_downloads = sum(explicit_downloads_by_app.values())
    known_app_ratings = sum(app_ratings_by_app.values())
    app_review_base = max(float(app_reviews), known_app_ratings)
    if explicit_downloads:
        app_downloads_est = explicit_downloads
        app_downloads_low = explicit_downloads
        app_downloads_high = explicit_downloads
        app_download_basis = "observed"
    elif app_review_base:
        # Configurable app intelligence providers can populate installs directly.
        # Until then, use a deliberately wide 0.5%-2% review-to-install interval.
        app_downloads_est = app_review_base * 100
        app_downloads_low = app_review_base * 50
        app_downloads_high = app_review_base * 200
        app_download_basis = "review_proxy"
    else:
        app_downloads_est = app_downloads_low = app_downloads_high = 0.0
        app_download_basis = "unavailable"

    return {
        "mentions": mentions,
        "voc_records": voc_records,
        "app_reviews": app_reviews,
        "app_rating": round(app_rating_sum / app_rating_count, 2) if app_rating_count else None,
        "comments": round(comments),
        "engagement": round(engagement),
        "views": round(views),
        "app_downloads_est": round(app_downloads_est),
        "app_downloads_low": round(app_downloads_low),
        "app_downloads_high": round(app_downloads_high),
        "app_download_basis": app_download_basis,
    }


def _sales_signals(conn: sqlite3.Connection, brand_id: str, start: str, end: str) -> dict:
    rows = conn.execute(
        "SELECT id, link_id, snapshot_date, revenue_est, units_est, review_count "
        "FROM sales_metrics WHERE brand_id = ? AND snapshot_date >= ? AND snapshot_date <= ? "
        "ORDER BY snapshot_date, id",
        (brand_id, start, end),
    ).fetchall()
    latest_reviews: dict[str, float] = {}
    revenue = 0.0
    units = 0.0
    for row in rows:
        revenue += _number(row["revenue_est"])
        units += _number(row["units_est"])
        key = str(row["link_id"] or row["id"])
        if _number(row["review_count"]):
            latest_reviews[key] = _number(row["review_count"])
    return {
        "sales_revenue": round(revenue, 2),
        "sales_units": round(units),
        "product_reviews": round(sum(latest_reviews.values())),
        "sales_data_points": len(rows),
    }


def _signal_share(rows: list[dict], key: str) -> dict[str, float]:
    total = sum(max(0.0, float(row["signals"].get(key) or 0)) for row in rows)
    if total <= 0:
        return {row["brand_id"]: 0.0 for row in rows}
    return {row["brand_id"]: max(0.0, float(row["signals"].get(key) or 0)) / total for row in rows}


@router.get("")
def market_share(
    brand_ids: str,
    model: str = "balanced",
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    ids = list(dict.fromkeys(part.strip() for part in brand_ids.split(",") if part.strip()))
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="至少选择 2 个品牌才能估算相对市占。")
    if len(ids) > 12:
        raise HTTPException(status_code=400, detail="单次最多分析 12 个品牌。")
    preset = MODEL_PRESETS.get(model)
    if not preset:
        raise HTTPException(status_code=400, detail="未知估算模型。")

    placeholders = ",".join("?" for _ in ids)
    found = conn.execute(
        f"SELECT id, name, category, is_primary, is_competitor FROM brands WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {row["id"]: row for row in found}
    missing = [brand_id for brand_id in ids if brand_id not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"品牌不存在：{', '.join(missing)}")

    start, end = resolve_window(days, start_date, end_date)
    rows: list[dict] = []
    for brand_id in ids:
        brand = by_id[brand_id]
        record_data = _record_signals(conn, brand, start, end)
        sales_data = _sales_signals(conn, brand_id, start, end)
        conversation_signal = (
            record_data["mentions"]
            + record_data["voc_records"]
            + record_data["comments"]
        )
        signals = {
            "sales": 0,
            "app": record_data["app_downloads_est"],
            "conversation": conversation_signal,
            "engagement": record_data["engagement"],
        }
        rows.append({
            "brand_id": brand_id,
            "name": brand["name"],
            "category": brand["category"],
            "is_primary": bool(brand["is_primary"]),
            "is_competitor": bool(brand["is_competitor"]),
            "signals": signals,
            "raw": {**record_data, **sales_data},
            "gaps": [],
        })

    base_weights = preset["weights"]
    comparable_floor = max(2, math.ceil(len(rows) * 0.5))

    # Do not mix currencies with unit counts. Choose one sales basis that is
    # comparable across enough brands; otherwise the sales signal is excluded.
    revenue_coverage = sum(1 for row in rows if row["raw"]["sales_revenue"] > 0)
    unit_coverage = sum(1 for row in rows if row["raw"]["sales_units"] > 0)
    if revenue_coverage >= comparable_floor:
        sales_basis = "revenue"
        for row in rows:
            row["signals"]["sales"] = row["raw"]["sales_revenue"]
    elif unit_coverage >= comparable_floor:
        sales_basis = "units"
        for row in rows:
            row["signals"]["sales"] = row["raw"]["sales_units"]
    else:
        sales_basis = "unavailable"

    # Cumulative product review totals are useful only when available for a
    # comparable portion of the cohort; otherwise they would reward data
    # coverage rather than brand demand.
    product_review_coverage = sum(1 for row in rows if row["raw"]["product_reviews"] > 0)
    if product_review_coverage >= comparable_floor:
        for row in rows:
            row["signals"]["conversation"] += row["raw"]["product_reviews"]

    signal_coverage = {
        key: sum(1 for row in rows if row["signals"][key] > 0) for key in base_weights
    }
    active_keys = [key for key in base_weights if signal_coverage[key] >= comparable_floor]
    active_base_weight = sum(base_weights[key] for key in active_keys)
    active_weights = {
        key: (base_weights[key] / active_base_weight if active_base_weight and key in active_keys else 0.0)
        for key in base_weights
    }
    shares_by_signal = {key: _signal_share(rows, key) for key in base_weights}

    for row in rows:
        gaps = row["gaps"]
        if not row["signals"]["sales"]:
            gaps.append("缺少可比较的销量或销售额")
        if row["raw"]["app_download_basis"] == "unavailable":
            gaps.append("缺少 App 下载量或应用商店评论")
        elif row["raw"]["app_download_basis"] == "review_proxy":
            gaps.append("App 下载量由评论量区间推算")
        if not row["signals"]["conversation"]:
            gaps.append("缺少评论、讨论或媒体提及")
        if not row["signals"]["engagement"]:
            gaps.append("缺少点赞、评论、分享等互动指标")
        brand_id = row["brand_id"]
        score = sum(active_weights[key] * shares_by_signal[key][brand_id] for key in active_keys)
        row["share"] = round(score * 100, 2)
        row["signal_shares"] = {
            key: round(shares_by_signal[key][brand_id] * 100, 2) for key in base_weights
        }
        row["coverage"] = round(
            100 * sum(base_weights[key] for key in base_weights if row["signals"][key] > 0),
            0,
        )

    rows.sort(key=lambda row: (-row["share"], row["name"].lower()))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index

    fairness = 0.0
    if active_keys:
        fairness = sum(
            active_weights[key] * (sum(1 for row in rows if row["signals"][key] > 0) / len(rows))
            for key in active_keys
        )
    evidence_total = sum(
        row["raw"]["mentions"]
        + row["raw"]["voc_records"]
        + row["raw"]["comments"]
        + row["raw"]["sales_data_points"]
        for row in rows
    )
    sample_strength = min(1.0, math.log1p(evidence_total) / math.log1p(max(500, len(rows) * 250)))
    confidence_score = round(100 * (0.55 * active_base_weight + 0.25 * fairness + 0.20 * sample_strength))
    confidence_label = "高" if confidence_score >= 75 else "中" if confidence_score >= 50 else "低"

    warnings = ["该结果是所选品牌与已采集数据源内的相对份额，不等同于官方全行业市占。"]
    categories = {str(row["category"]).strip() for row in rows if row.get("category")}
    if len(categories) > 1:
        warnings.append("所选品牌分属不同品类，横向市占的业务含义可能有限。")
    elif any(not row.get("category") for row in rows):
        warnings.append("部分品牌未设置品类，请确认它们属于同一竞争市场。")
    if any(row["raw"]["app_download_basis"] == "review_proxy" for row in rows):
        warnings.append("App 下载估算按 0.5%-2% 的评论转化率给出宽区间，中位值按 1% 计算。")
    if active_base_weight < 0.999:
        warnings.append("数据覆盖不足、无法横向比较的指标未计入综合值，其权重已自动分配给可用指标。")
    for key, label in (("sales", "销售结果"), ("app", "App 下载"), ("conversation", "评论/声量"), ("engagement", "互动")):
        if 0 < signal_coverage[key] < comparable_floor:
            warnings.append(f"{label}仅覆盖 {signal_coverage[key]}/{len(rows)} 个品牌，本次未纳入综合份额。")
    if product_review_coverage and product_review_coverage < comparable_floor:
        warnings.append("商品累计评论量覆盖品牌不足，仅展示原始值，未纳入评论/声量信号。")

    return {
        "range": {"start": start, "end": end},
        "model": {
            "key": model,
            "label": preset["label"],
            "description": preset["description"],
            "sales_basis": sales_basis,
            "base_weights": base_weights,
            "active_weights": {key: round(value, 4) for key, value in active_weights.items()},
        },
        "confidence": {
            "score": confidence_score,
            "label": confidence_label,
            "available_weight": round(active_base_weight, 2),
            "coverage_fairness": round(fairness, 2),
            "evidence_total": evidence_total,
        },
        "brands": rows,
        "warnings": warnings,
    }
