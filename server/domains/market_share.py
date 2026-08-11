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
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

from ..relevance import google_news_record_is_relevant, reddit_search_record_is_relevant
from ..util import new_id, today, utc_now
from .common import get_conn, resolve_window

router = APIRouter(prefix="/api/market-share", tags=["market-share"])


MODEL_PRESETS = {
    "balanced": {
        "label": "App 下载与评分模型",
        "description": "使用所选国家的 App 下载估算与 App Store 公开评分数计算相对份额。",
        "weights": {"sales": 0.0, "app": 0.65, "conversation": 0.35, "engagement": 0.0},
    },
    "commerce": {
        "label": "商业结果优先",
        "description": "保留下载量和公开评分数为主要依据，同时提高销售结果的校准权重。",
        "weights": {"sales": 0.30, "app": 0.40, "conversation": 0.25, "engagement": 0.05},
    },
    "attention": {
        "label": "产品热度优先",
        "description": "提高评论和互动权重，适合新品、软件产品与增长趋势观察。",
        "weights": {"sales": 0.05, "app": 0.45, "conversation": 0.40, "engagement": 0.10},
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


def _record_signals(
    conn: sqlite3.Connection,
    brand: sqlite3.Row,
    start: str,
    end: str,
    country: str,
) -> dict:
    country_filter = " AND UPPER(COALESCE(region, '')) = ?" if country else ""
    params: list[str] = [brand["id"], start, end]
    if country:
        params.append(country)
    rows = conn.execute(
        "SELECT source_id, link_id, data_type, dimension, channel, platform, title, body, url, region, occurred_at, "
        "metrics_json, raw_json FROM records WHERE brand_id = ? "
        "AND substr(occurred_at, 1, 10) >= ? AND substr(occurred_at, 1, 10) <= ?"
        + country_filter
        + " ORDER BY occurred_at",
        params,
    ).fetchall()

    mentions = 0
    voc_records = 0
    app_reviews = 0
    comments = 0.0
    engagement = 0.0
    views = 0.0
    explicit_downloads_by_app: dict[str, float] = defaultdict(float)
    app_ratings_by_app: dict[str, float] = defaultdict(float)
    app_average_rating_by_app: dict[str, tuple[float, float]] = {}
    app_data_updated_at = ""

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
            app_data_updated_at = max(app_data_updated_at, str(row["occurred_at"] or ""))
            is_review_record = data_type != "app_metric" and (
                dimension == "voc" or "review" in source_id or "review" in data_type
            )
            app_reviews += 1 if is_review_record else 0
            app_key = str(row["link_id"] or source_id or platform or "app")
            rating = _number(metrics.get("rating"))
            rating_count = _number(metrics.get("rating_count"))
            if data_type == "app_metric" and rating and rating_count >= app_average_rating_by_app.get(app_key, (0, 0))[1]:
                app_average_rating_by_app[app_key] = (rating, rating_count)
            explicit_downloads_by_app[app_key] = max(
                explicit_downloads_by_app[app_key], _first_metric(metrics, DOWNLOAD_KEYS)
            )
            app_ratings_by_app[app_key] = max(
                app_ratings_by_app[app_key], rating_count
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

    rating_weight = sum(max(1.0, count) for _, count in app_average_rating_by_app.values())
    app_rating = (
        sum(rating * max(1.0, count) for rating, count in app_average_rating_by_app.values()) / rating_weight
        if rating_weight else None
    )

    return {
        "mentions": mentions,
        "voc_records": voc_records,
        "app_reviews": round(app_review_base),
        "app_rating": round(app_rating, 2) if app_rating is not None else None,
        "app_store_apps": len(app_ratings_by_app),
        "app_rating_count_observed": bool(known_app_ratings),
        "comments": round(comments),
        "engagement": round(engagement),
        "views": round(views),
        "app_downloads_est": round(app_downloads_est),
        "app_downloads_low": round(app_downloads_low),
        "app_downloads_high": round(app_downloads_high),
        "app_download_basis": app_download_basis,
        "app_data_updated_at": app_data_updated_at or None,
    }


def _sales_signals(
    conn: sqlite3.Connection,
    brand_id: str,
    start: str,
    end: str,
    country: str,
) -> dict:
    country_filter = " AND UPPER(COALESCE(l.region, '')) = ?" if country else ""
    params: list[str] = [brand_id, start, end]
    if country:
        params.append(country)
    rows = conn.execute(
        "SELECT sm.id, sm.link_id, sm.snapshot_date, sm.revenue_est, sm.units_est, sm.review_count "
        "FROM sales_metrics sm LEFT JOIN links l ON l.id = sm.link_id "
        "WHERE sm.brand_id = ? AND sm.snapshot_date >= ? AND sm.snapshot_date <= ?"
        + country_filter
        + " ORDER BY sm.snapshot_date, sm.id",
        params,
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


def _available_countries(
    conn: sqlite3.Connection,
    brand_ids: list[str],
    start: str,
    end: str,
) -> list[str]:
    placeholders = ",".join("?" for _ in brand_ids)
    record_rows = conn.execute(
        f"SELECT DISTINCT UPPER(region) AS country FROM records "
        f"WHERE brand_id IN ({placeholders}) AND region IS NOT NULL AND TRIM(region) != '' "
        "AND substr(occurred_at, 1, 10) >= ? AND substr(occurred_at, 1, 10) <= ? "
        "AND (LOWER(COALESCE(channel, '')) = 'app' "
        "OR LOWER(COALESCE(platform, '')) IN ('app_store', 'google_play'))",
        [*brand_ids, start, end],
    ).fetchall()
    link_rows = conn.execute(
        f"SELECT DISTINCT UPPER(region) AS country FROM links "
        f"WHERE brand_id IN ({placeholders}) AND region IS NOT NULL AND TRIM(region) != '' "
        "AND (LOWER(COALESCE(channel, '')) = 'app' "
        "OR LOWER(COALESCE(platform, '')) IN ('app_store', 'google_play'))",
        brand_ids,
    ).fetchall()
    countries = {
        str(row["country"]).strip().upper()
        for row in [*record_rows, *link_rows]
        if len(str(row["country"] or "").strip()) == 2
    }
    return sorted(countries)


def _signal_share(rows: list[dict], key: str) -> dict[str, float]:
    total = sum(max(0.0, float(row["signals"].get(key) or 0)) for row in rows)
    if total <= 0:
        return {row["brand_id"]: 0.0 for row in rows}
    return {row["brand_id"]: max(0.0, float(row["signals"].get(key) or 0)) / total for row in rows}


def _snapshot_date(value: str | None) -> str:
    candidate = value or today()
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError as exc:
        raise ValueError(f"无效快照日期：{candidate}") from exc


def capture_market_share_snapshots(
    conn: sqlite3.Connection,
    *,
    snapshot_date: str | None = None,
    brand_ids: list[str] | None = None,
) -> int:
    """Persist one daily App-signal snapshot per brand and country.

    Percentages are deliberately not stored: market share is relative to the
    selected comparison cohort, which can change. The trend endpoint computes
    percentages from these immutable daily inputs for the current cohort.
    """
    day = _snapshot_date(snapshot_date)
    params: list[str] = []
    where = ""
    if brand_ids:
        placeholders = ",".join("?" for _ in brand_ids)
        where = f" WHERE id IN ({placeholders})"
        params.extend(brand_ids)
    brands = conn.execute(
        "SELECT id, name, category, is_primary, is_competitor FROM brands" + where,
        params,
    ).fetchall()
    if not brands:
        return 0

    ids = [row["id"] for row in brands]
    countries = ["all", *_available_countries(conn, ids, "1970-01-01", day)]
    now = utc_now()
    changed = 0
    for country in countries:
        country_code = "" if country == "all" else country
        for brand in brands:
            signals = _record_signals(conn, brand, "1970-01-01", day, country_code)
            before = conn.total_changes
            conn.execute(
                """
                INSERT INTO market_share_snapshots
                  (id, snapshot_date, brand_id, country, app_downloads_est,
                   app_downloads_low, app_downloads_high, app_download_basis,
                   app_reviews, app_rating, app_store_apps, source_updated_at,
                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, brand_id, country) DO UPDATE SET
                  app_downloads_est = excluded.app_downloads_est,
                  app_downloads_low = excluded.app_downloads_low,
                  app_downloads_high = excluded.app_downloads_high,
                  app_download_basis = excluded.app_download_basis,
                  app_reviews = excluded.app_reviews,
                  app_rating = excluded.app_rating,
                  app_store_apps = excluded.app_store_apps,
                  source_updated_at = excluded.source_updated_at,
                  updated_at = excluded.updated_at
                WHERE market_share_snapshots.app_downloads_est != excluded.app_downloads_est
                   OR market_share_snapshots.app_downloads_low != excluded.app_downloads_low
                   OR market_share_snapshots.app_downloads_high != excluded.app_downloads_high
                   OR market_share_snapshots.app_download_basis != excluded.app_download_basis
                   OR market_share_snapshots.app_reviews != excluded.app_reviews
                   OR COALESCE(market_share_snapshots.app_rating, 0) != COALESCE(excluded.app_rating, 0)
                   OR market_share_snapshots.app_store_apps != excluded.app_store_apps
                   OR COALESCE(market_share_snapshots.source_updated_at, '') != COALESCE(excluded.source_updated_at, '')
                """,
                (
                    new_id(), day, brand["id"], country,
                    signals["app_downloads_est"], signals["app_downloads_low"],
                    signals["app_downloads_high"], signals["app_download_basis"],
                    signals["app_reviews"], signals["app_rating"], signals["app_store_apps"],
                    signals["app_data_updated_at"], now, now,
                ),
            )
            changed += conn.total_changes - before
    return changed


def sync_market_share_snapshots(
    conn: sqlite3.Connection,
    *,
    brand_ids: list[str] | None = None,
    include_history: bool = False,
) -> dict:
    """Backfill historical App metric dates when needed, then refresh today."""
    params: list[str] = []
    brand_filter = ""
    if brand_ids:
        placeholders = ",".join("?" for _ in brand_ids)
        brand_filter = f" AND brand_id IN ({placeholders})"
        params.extend(brand_ids)
    existing = conn.execute(
        "SELECT COUNT(*) AS c FROM market_share_snapshots"
        + (f" WHERE brand_id IN ({','.join('?' for _ in brand_ids)})" if brand_ids else ""),
        brand_ids or [],
    ).fetchone()["c"]

    dates = {today()}
    if include_history or existing == 0:
        rows = conn.execute(
            "SELECT DISTINCT substr(occurred_at, 1, 10) AS d FROM records "
            "WHERE data_type = 'app_metric' AND occurred_at IS NOT NULL "
            "AND length(substr(occurred_at, 1, 10)) = 10"
            + brand_filter,
            params,
        ).fetchall()
        dates.update(str(row["d"]) for row in rows if row["d"])

    changes = 0
    captured_dates: list[str] = []
    for day in sorted(dates):
        try:
            normalized = _snapshot_date(day)
        except ValueError:
            continue
        changes += capture_market_share_snapshots(
            conn,
            snapshot_date=normalized,
            brand_ids=brand_ids,
        )
        captured_dates.append(normalized)
    return {"captured_dates": captured_dates, "changes": changes}


def _trend_shares(brand_ids: list[str], state: dict[str, dict], model: str) -> dict[str, float]:
    weights = MODEL_PRESETS[model]["weights"]
    comparable_floor = max(2, math.ceil(len(brand_ids) * 0.5))
    signal_values = {
        "app": {brand_id: _number(state.get(brand_id, {}).get("app_downloads_est")) for brand_id in brand_ids},
        "conversation": {brand_id: _number(state.get(brand_id, {}).get("app_reviews")) for brand_id in brand_ids},
    }
    active = [
        key for key in ("app", "conversation")
        if weights[key] > 0 and sum(1 for value in signal_values[key].values() if value > 0) >= comparable_floor
    ]
    active_total = sum(weights[key] for key in active)
    shares = {brand_id: 0.0 for brand_id in brand_ids}
    if not active_total:
        return shares
    for key in active:
        total = sum(signal_values[key].values())
        if total <= 0:
            continue
        normalized_weight = weights[key] / active_total
        for brand_id in brand_ids:
            shares[brand_id] += normalized_weight * signal_values[key][brand_id] / total
    return {brand_id: round(value * 100, 2) for brand_id, value in shares.items()}


@router.post("/snapshots/refresh")
def refresh_market_share_snapshots(
    brand_ids: str = "",
    conn: sqlite3.Connection = Depends(get_conn),
):
    ids = list(dict.fromkeys(part.strip() for part in brand_ids.split(",") if part.strip()))
    if len(ids) > 12:
        raise HTTPException(status_code=400, detail="单次最多刷新 12 个品牌。")
    if ids:
        placeholders = ",".join("?" for _ in ids)
        found = {row["id"] for row in conn.execute(
            f"SELECT id FROM brands WHERE id IN ({placeholders})", ids,
        ).fetchall()}
        missing = [brand_id for brand_id in ids if brand_id not in found]
        if missing:
            raise HTTPException(status_code=404, detail=f"品牌不存在：{', '.join(missing)}")
    return sync_market_share_snapshots(conn, brand_ids=ids or None, include_history=True)


@router.get("/trend")
def market_share_trend(
    brand_ids: str,
    model: str = "balanced",
    country: str = "all",
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    ids = list(dict.fromkeys(part.strip() for part in brand_ids.split(",") if part.strip()))
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="至少选择 2 个品牌才能查看相对市占趋势。")
    if len(ids) > 12:
        raise HTTPException(status_code=400, detail="单次最多分析 12 个品牌。")
    if model not in MODEL_PRESETS:
        raise HTTPException(status_code=400, detail="未知估算模型。")
    country_code = "all" if not country or country.strip().lower() == "all" else country.strip().upper()
    if country_code != "all" and (len(country_code) != 2 or not country_code.isalpha()):
        raise HTTPException(status_code=400, detail="国家代码必须是两位 ISO 代码，例如 US、CN、GB。")

    placeholders = ",".join("?" for _ in ids)
    brands = conn.execute(
        f"SELECT id, name, category, is_primary, is_competitor FROM brands WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {row["id"]: row for row in brands}
    missing = [brand_id for brand_id in ids if brand_id not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"品牌不存在：{', '.join(missing)}")

    start, end = resolve_window(days, start_date, end_date)
    snapshot_rows = conn.execute(
        f"SELECT * FROM market_share_snapshots WHERE brand_id IN ({placeholders}) "
        "AND country = ? AND snapshot_date <= ? ORDER BY snapshot_date, brand_id",
        [*ids, country_code, end],
    ).fetchall()
    updates_by_date: dict[str, list[dict]] = defaultdict(list)
    state: dict[str, dict] = {}
    last_snapshot_at = ""
    for row in snapshot_rows:
        item = dict(row)
        last_snapshot_at = max(last_snapshot_at, str(item.get("updated_at") or ""))
        if item["snapshot_date"] < start:
            state[item["brand_id"]] = item
        else:
            updates_by_date[item["snapshot_date"]].append(item)

    points: list[dict] = []
    current = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    comparable_floor = max(2, math.ceil(len(ids) * 0.5))
    while current <= end_day:
        day = current.isoformat()
        fresh = updates_by_date.get(day, [])
        for item in fresh:
            state[item["brand_id"]] = item
        evidence_brands = sum(
            1 for brand_id in ids
            if _number(state.get(brand_id, {}).get("app_downloads_est"))
            or _number(state.get(brand_id, {}).get("app_reviews"))
        )
        if evidence_brands >= comparable_floor:
            points.append({
                "date": day,
                "shares": _trend_shares(ids, state, model),
                "public_metrics": {
                    brand_id: {
                        "rating_count": round(_number(state.get(brand_id, {}).get("app_reviews"))),
                        "average_rating": state.get(brand_id, {}).get("app_rating"),
                        "app_count": round(_number(state.get(brand_id, {}).get("app_store_apps"))),
                    }
                    for brand_id in ids
                },
                "fresh_brand_count": len({item["brand_id"] for item in fresh}),
                "is_carried_forward": not fresh,
                "data_as_of": max(
                    (str(state.get(brand_id, {}).get("source_updated_at") or "") for brand_id in ids),
                    default="",
                ) or None,
            })
        current += timedelta(days=1)

    summaries = []
    if points:
        first_shares = points[0]["shares"]
        latest_shares = points[-1]["shares"]
        first_metrics = points[0]["public_metrics"]
        latest_metrics = points[-1]["public_metrics"]
        for brand_id in ids:
            start_share = float(first_shares.get(brand_id) or 0)
            latest_share = float(latest_shares.get(brand_id) or 0)
            summaries.append({
                "brand_id": brand_id,
                "name": by_id[brand_id]["name"],
                "start_share": round(start_share, 2),
                "latest_share": round(latest_share, 2),
                "change_pp": round(latest_share - start_share, 2),
                "start_rating_count": first_metrics[brand_id]["rating_count"],
                "latest_rating_count": latest_metrics[brand_id]["rating_count"],
                "rating_count_change": latest_metrics[brand_id]["rating_count"] - first_metrics[brand_id]["rating_count"],
                "latest_average_rating": latest_metrics[brand_id]["average_rating"],
            })

    return {
        "range": {"start": start, "end": end},
        "country": country_code,
        "model": model,
        "cadence": "daily",
        "latest_date": points[-1]["date"] if points else None,
        "last_snapshot_at": last_snapshot_at or None,
        "brands": [
            {
                "brand_id": brand_id,
                "name": by_id[brand_id]["name"],
                "category": by_id[brand_id]["category"],
                "is_primary": bool(by_id[brand_id]["is_primary"]),
                "is_competitor": bool(by_id[brand_id]["is_competitor"]),
            }
            for brand_id in ids
        ],
        "points": points,
        "summary": summaries,
    }


@router.get("")
def market_share(
    brand_ids: str,
    model: str = "balanced",
    country: str = "all",
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
    country_code = "" if not country or country.strip().lower() == "all" else country.strip().upper()
    if country_code and (len(country_code) != 2 or not country_code.isalpha()):
        raise HTTPException(status_code=400, detail="国家代码必须是两位 ISO 代码，例如 US、CN、GB。")

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
    countries = _available_countries(conn, ids, start, end)
    rows: list[dict] = []
    for brand_id in ids:
        brand = by_id[brand_id]
        record_data = _record_signals(conn, brand, start, end, country_code)
        sales_data = _sales_signals(conn, brand_id, start, end, country_code)
        conversation_signal = record_data["app_reviews"]
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

    signal_coverage = {
        key: sum(1 for row in rows if row["signals"][key] > 0) for key in base_weights
    }
    active_keys = [
        key for key in base_weights
        if base_weights[key] > 0 and signal_coverage[key] >= comparable_floor
    ]
    active_base_weight = sum(base_weights[key] for key in active_keys)
    active_weights = {
        key: (base_weights[key] / active_base_weight if active_base_weight and key in active_keys else 0.0)
        for key in base_weights
    }
    shares_by_signal = {key: _signal_share(rows, key) for key in base_weights}

    for row in rows:
        gaps = row["gaps"]
        if base_weights["sales"] > 0 and not row["signals"]["sales"]:
            gaps.append("缺少可比较的销量或销售额")
        if row["raw"]["app_download_basis"] == "unavailable":
            gaps.append("缺少 App 下载量或应用商店评论")
        elif row["raw"]["app_download_basis"] == "review_proxy":
            gaps.append("App 下载量由公开评分数区间推算")
        if not row["signals"]["conversation"]:
            gaps.append("缺少 App Store 公开评分数")
        if base_weights["engagement"] > 0 and not row["signals"]["engagement"]:
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
        row["raw"]["app_reviews"]
        for row in rows
    )
    sample_strength = min(1.0, math.log1p(evidence_total) / math.log1p(max(500, len(rows) * 250)))
    base_confidence = 100 * (0.55 * active_base_weight + 0.25 * fairness + 0.20 * sample_strength)
    proxy_ratio = (
        sum(1 for row in rows if row["raw"]["app_download_basis"] == "review_proxy") / len(rows)
        if rows else 0.0
    )
    # Review-derived downloads and review counts are correlated rather than
    # independent evidence.  Discount confidence until observed install data
    # is available instead of showing a misleading near-100% score.
    confidence_score = round(base_confidence * (1.0 - 0.30 * proxy_ratio))
    confidence_label = "高" if confidence_score >= 75 else "中" if confidence_score >= 50 else "低"

    warnings = ["该结果是所选品牌与已采集数据源内的相对份额，不等同于官方全行业市占。"]
    if country_code:
        warnings.append(f"当前仅统计国家标签为 {country_code} 的记录；未标注国家的数据不会混入该国家。")
    categories = {str(row["category"]).strip() for row in rows if row.get("category")}
    if len(categories) > 1:
        warnings.append("所选品牌分属不同品类，横向市占的业务含义可能有限。")
    elif any(not row.get("category") for row in rows):
        warnings.append("部分品牌未设置品类，请确认它们属于同一竞争市场。")
    if any(row["raw"]["app_download_basis"] == "review_proxy" for row in rows):
        warnings.append("App Store 评分数与平均评分为对应国家商店的公开准确值；下载量仍按 0.5%-2% 的评分转化率估算，中位值按 1% 计算。")
    if active_base_weight < 0.999:
        warnings.append("数据覆盖不足、无法横向比较的指标未计入综合值，其权重已自动分配给可用指标。")
    for key, label in (("sales", "销售结果"), ("app", "App 下载"), ("conversation", "App Store 评分数"), ("engagement", "互动")):
        if base_weights[key] > 0 and 0 < signal_coverage[key] < comparable_floor:
            warnings.append(f"{label}仅覆盖 {signal_coverage[key]}/{len(rows)} 个品牌，本次未纳入综合份额。")

    return {
        "range": {"start": start, "end": end},
        "country": country_code or "all",
        "countries": countries,
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
            "download_proxy_ratio": round(proxy_ratio, 2),
        },
        "brands": rows,
        "warnings": warnings,
    }
