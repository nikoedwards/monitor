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

from ..app_identity import app_id, app_record_allowed, configured_app_ids
from ..relevance import google_news_record_is_relevant, reddit_search_record_is_relevant
from ..util import new_id, today, utc_now
from .common import get_conn, resolve_window

router = APIRouter(prefix="/api/market-share", tags=["market-share"])


MODEL_PRESETS = {
    "balanced": {
        "label": "App 评分样本模型",
        "description": "使用所选品牌的应用商店累计评分计算样本内份额；有可比较的真实下载量时再计入下载信号。",
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
APP_PLATFORMS = {"app_store_reviews": "app_store", "google_play_reviews": "google_play"}


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
    *,
    include_engagement: bool = False,
) -> dict:
    country_filter = " AND UPPER(COALESCE(region, '')) = ?" if country else ""
    params: list[str] = [brand["id"], start, end]
    if country:
        params.append(country)
    app_filter = "" if include_engagement else (
        " AND (LOWER(channel) = 'app' OR INSTR(LOWER(source_id), 'app_store') > 0 "
        "OR LOWER(platform) IN ('app_store', 'google_play'))"
    )
    rows = conn.execute(
        "SELECT id, source_id, link_id, data_type, dimension, channel, platform, title, body, url, region, occurred_at, "
        "metrics_json, raw_json FROM records WHERE brand_id = ? "
        "AND substr(occurred_at, 1, 10) >= ? AND substr(occurred_at, 1, 10) <= ?"
        + country_filter
        + app_filter
        + " ORDER BY occurred_at",
        params,
    ).fetchall()

    app_reviews = 0
    engagement = 0.0
    explicit_downloads_by_app: dict[str, float] = defaultdict(float)
    app_ratings_by_app: dict[str, float] = defaultdict(float)
    app_evidence_by_app: set[str] = set()
    app_data_updated_at = ""
    allowed_apps = configured_app_ids(conn, brand["id"])
    excluded_app_records = 0
    latest_app_metrics: dict[str, tuple[str, int, dict]] = {}

    for row in rows:
        metrics = _json_object(row["metrics_json"])
        raw = _json_object(row["raw_json"])
        if not app_record_allowed({**dict(row), "raw": raw}, allowed_apps):
            excluded_app_records += 1
            continue
        if not _record_is_relevant(row, brand["name"], raw):
            continue

        dimension = (row["dimension"] or "").lower()
        channel = (row["channel"] or "").lower()
        platform = (row["platform"] or "").lower()
        source_id = (row["source_id"] or "").lower()
        data_type = (row["data_type"] or "").lower()
        is_app = channel == "app" or "app_store" in source_id or platform in {"app_store", "google_play"}

        if include_engagement:
            comment_value = _first_metric(metrics, COMMENT_KEYS)
            if data_type in {"community_reply", "comment", "reply"} and not comment_value:
                comment_value = 1
            engagement += _number(metrics.get("engagement")) or (
                _number(metrics.get("likes")) + comment_value
                + _number(metrics.get("shares")) + _number(metrics.get("vote_count"))
            )

        if is_app:
            # Play exposes a global cumulative rating count even on localized
            # storefronts. Do not present it as ratings from a single country.
            if country and platform == "google_play" and data_type == "app_metric":
                continue
            app_data_updated_at = max(app_data_updated_at, str(row["occurred_at"] or ""))
            is_review_record = data_type != "app_metric" and (
                dimension == "voc" or "review" in source_id or "review" in data_type
            )
            app_reviews += 1 if is_review_record else 0
            identity = app_id(platform, row["url"], raw)
            scope = "global" if platform == "google_play" else str(row["region"] or "")
            app_key = f"{platform}:{identity}:{scope}" if identity else str(row["link_id"] or source_id or platform or "app")
            app_evidence_by_app.add(str(row["link_id"] or row["id"]))
            if data_type == "app_metric":
                # Use the latest observation, including legitimate decreases;
                # configured/automatic links and localized Play pages share a key.
                order = (str(row["occurred_at"] or ""), int(row["region"] == "US"))
                previous = latest_app_metrics.get(app_key)
                if previous is None or order >= previous[:2]:
                    latest_app_metrics[app_key] = (*order, metrics)
                continue
            rating_count = _number(metrics.get("rating_count"))
            explicit_downloads_by_app[app_key] = max(
                explicit_downloads_by_app[app_key], _first_metric(metrics, DOWNLOAD_KEYS)
            )
            app_ratings_by_app[app_key] = max(
                app_ratings_by_app[app_key], rating_count
            )

    for app_key, (_, _, metrics) in latest_app_metrics.items():
        app_evidence_by_app.add(app_key)
        rating_count = _number(metrics.get("rating_count"))
        app_ratings_by_app[app_key] = rating_count
        explicit_downloads_by_app[app_key] = _first_metric(metrics, DOWNLOAD_KEYS)

    known_app_ratings = sum(app_ratings_by_app.values())
    return {
        "app_reviews": round(max(float(app_reviews), known_app_ratings)),
        "app_rating_count_observed": bool(known_app_ratings),
        "app_downloads": round(sum(explicit_downloads_by_app.values())),
        "app_evidence_count": len(app_evidence_by_app),
        "app_data_updated_at": app_data_updated_at or None,
        "excluded_app_records": excluded_app_records,
        **({"engagement": round(engagement)} if include_engagement else {}),
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
    sales_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(sales_metrics)").fetchall()
    }
    currency_column = "sm.currency" if "currency" in sales_columns else "NULL"
    created_at_column = "sm.created_at" if "created_at" in sales_columns else "NULL"
    rows = conn.execute(
        "SELECT sm.id, sm.link_id, sm.snapshot_date, sm.revenue_est, sm.units_est, "
        f"{currency_column} AS currency, {created_at_column} AS created_at "
        "FROM sales_metrics sm LEFT JOIN links l ON l.id = sm.link_id "
        "WHERE sm.brand_id = ? AND sm.snapshot_date >= ? AND sm.snapshot_date <= ?"
        + country_filter
        + " ORDER BY sm.snapshot_date DESC, created_at DESC, sm.id DESC",
        params,
    ).fetchall()

    # Sales rows are point-in-time snapshots. Use only the latest snapshot per
    # listing in the selected window so daily collection does not multiply a
    # listing's cumulative estimate.
    latest_by_listing: dict[str, sqlite3.Row] = {}
    for row in rows:
        key = str(row["link_id"] or row["id"])
        latest_by_listing.setdefault(key, row)
    selected_rows = list(latest_by_listing.values())

    revenue = 0.0
    units = 0.0
    currencies: set[str] = set()
    missing_currency_revenue = False
    for row in selected_rows:
        revenue += _number(row["revenue_est"])
        units += _number(row["units_est"])
        if _number(row["revenue_est"]):
            currency = str(row["currency"] or "").strip().upper()
            if currency:
                currencies.add(currency)
            else:
                missing_currency_revenue = True

    sales_currency = next(iter(currencies)) if len(currencies) == 1 and not missing_currency_revenue else None
    currency_mixed = len(currencies) > 1 or missing_currency_revenue
    return {
        # Revenue without a single known currency is not comparable and must
        # not leak into the market-share signal as if it were normalized.
        "sales_revenue": round(revenue, 2) if sales_currency else 0.0,
        "sales_units": round(units),
        "sales_data_points": len(selected_rows),
        "sales_currency": sales_currency,
        "sales_currency_mixed": currency_mixed,
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
    countries = {
        str(row["country"]).strip().upper()
        for row in record_rows
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
    # The existing snapshot download column also holds measurements from earlier releases.
    # Keep the basis marker so the trend can skip historical estimates.
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
                   app_download_basis, app_reviews, source_updated_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, brand_id, country) DO UPDATE SET
                  app_downloads_est = excluded.app_downloads_est,
                  app_download_basis = excluded.app_download_basis,
                  app_reviews = excluded.app_reviews,
                  source_updated_at = excluded.source_updated_at,
                  updated_at = excluded.updated_at
                WHERE market_share_snapshots.app_downloads_est != excluded.app_downloads_est
                   OR market_share_snapshots.app_download_basis != excluded.app_download_basis
                   OR market_share_snapshots.app_reviews != excluded.app_reviews
                   OR COALESCE(market_share_snapshots.source_updated_at, '') != COALESCE(excluded.source_updated_at, '')
                """,
                (
                    new_id(), day, brand["id"], country,
                    signals["app_downloads"], "observed" if signals["app_downloads"] else "unavailable",
                    signals["app_reviews"], signals["app_data_updated_at"], now, now,
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
        snapshot_dates = conn.execute(
            "SELECT DISTINCT snapshot_date FROM market_share_snapshots"
            + (f" WHERE brand_id IN ({','.join('?' for _ in brand_ids)})" if brand_ids else ""),
            brand_ids or [],
        ).fetchall()
        dates.update(str(row["snapshot_date"]) for row in snapshot_dates)

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


def _snapshot_downloads(snapshot: dict) -> float:
    return _number(snapshot.get("app_downloads_est")) if snapshot.get("app_download_basis") == "observed" else 0


def _trend_shares(brand_ids: list[str], state: dict[str, dict], model: str) -> dict[str, float]:
    weights = MODEL_PRESETS[model]["weights"]
    comparable_floor = max(2, math.ceil(len(brand_ids) * 0.5))
    signal_values = {
        "app": {brand_id: _snapshot_downloads(state.get(brand_id, {})) for brand_id in brand_ids},
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


def _observed_number(metrics: dict, *keys: str) -> int | float | None:
    for key in keys:
        value = metrics.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(number) and number >= 0:
            return int(number) if number.is_integer() else number
    return None


def _sum_observations(target: dict, values: dict) -> None:
    for key, value in values.items():
        if value is not None:
            target[key] = target.get(key, 0) + value


def _app_observation(row: sqlite3.Row, platform: str, region: str, raw: dict, metrics: dict) -> dict:
    scope = "global" if platform == "google_play" else region
    identity = app_id(platform, row["url"], raw) or row["link_id"] or row["url"] or row["id"]
    return {
        "key": f"{platform}:{identity}:{scope}",
        "app_id": identity,
        "name": raw.get("track_name") or raw.get("app_name") or row["title"] or identity,
        "platform": platform,
        "scope": scope,
        "date": row["occurred_at"][:10],
        "observed_at": row["occurred_at"],
        "url": row["url"],
        "rating_count": (
            None if metrics.get("rating_count_observed") is False
            else _observed_number(metrics, "rating_count")
        ),
        "downloads": max(
            (value for key in DOWNLOAD_KEYS if (value := _observed_number(metrics, key)) is not None),
            default=None,
        ),
    }


def _brand_observations(
    conn: sqlite3.Connection, brand: sqlite3.Row, start: str, end: str, country: str, include_history: bool,
) -> tuple[dict, set[str]]:
    brand_id = brand["id"]
    countries: set[str] = set()
    totals: dict = {}
    daily: dict[str, dict] = defaultdict(dict)
    apps: dict[str, dict] = {}
    app_days: dict[str, list[dict]] = defaultdict(list)
    app_days_seen: set[tuple[str, str]] = set()
    allowed_apps = configured_app_ids(conn, brand_id)
    records = conn.execute(
        "SELECT id, source_id, link_id, platform, title, url, region, occurred_at, metrics_json, raw_json "
        "FROM records WHERE brand_id = ? AND occurred_at >= ? AND occurred_at < ? AND data_type = 'app_metric' "
        "ORDER BY occurred_at DESC, (region = 'US') DESC, rowid DESC",
        (brand_id, start, end + "\uffff"),
    )
    for row in records:
        platform = (row["platform"] or "").lower() or APP_PLATFORMS.get(row["source_id"], "")
        raw = _json_object(row["raw_json"])
        if platform not in APP_PLATFORMS.values() or not app_record_allowed(
            {**dict(row), "platform": platform, "raw": raw}, allowed_apps,
        ):
            continue
        region = str(row["region"] or raw.get("storefront") or "").strip().upper()
        if platform == "app_store" and len(region) == 2 and region.isascii() and region.isalpha():
            countries.add(region)
        if country != "all" and (platform == "google_play" or country != region):
            continue
        observation = _app_observation(row, platform, region, raw, _json_object(row["metrics_json"]))
        key = observation["key"]
        day = observation["date"]
        values = {f"{platform}_ratings": observation["rating_count"], "app_downloads": observation["downloads"]}
        if key not in apps:
            apps[key] = observation
            _sum_observations(totals, values)
        if include_history and (key, day) not in app_days_seen:
            app_days_seen.add((key, day))
            app_days[key].append(observation)
            _sum_observations(daily[day], values)

    result = {"brand_id": brand_id, "name": brand["name"], "metrics": totals}
    if include_history:
        result["daily"] = [{"date": day, "metrics": daily[day]} for day in sorted(daily, reverse=True) if daily[day]]
        result["apps"] = [
            {**app, "daily": app_days[app["key"]]}
            for app in sorted(apps.values(), key=lambda item: (str(item["name"]).casefold(), item["platform"], item["scope"]))
        ]
    return result, countries


@router.get("/observations")
def market_observations(
    brand_ids: str,
    country: str = "all",
    start_date: str | None = None,
    end_date: str | None = None,
    include_history: bool = False,
    conn: sqlite3.Connection = Depends(get_conn),
):
    ids = list(dict.fromkeys(part.strip() for part in brand_ids.split(",") if part.strip()))
    if not ids or len(ids) > 12 or (include_history and len(ids) != 1):
        raise HTTPException(status_code=400, detail="品牌汇总支持 1–12 个品牌，按日明细一次查看 1 个品牌。")
    country_code = country.strip().upper() or "ALL"
    if country_code != "ALL" and (len(country_code) != 2 or not country_code.isascii() or not country_code.isalpha()):
        raise HTTPException(status_code=400, detail="请选择两位国家代码。")
    placeholders = ",".join("?" for _ in ids)
    brands = {row["id"]: row for row in conn.execute(
        f"SELECT id, name FROM brands WHERE id IN ({placeholders})", ids,
    )}
    if missing := [brand_id for brand_id in ids if brand_id not in brands]:
        raise HTTPException(status_code=404, detail=f"品牌不存在：{', '.join(missing)}")

    start, end = resolve_window(start_date=start_date, end_date=end_date)
    countries: set[str] = set()
    results = []
    for brand_id in ids:
        result, scopes = _brand_observations(conn, brands[brand_id], start, end, "all" if country_code == "ALL" else country_code, include_history)
        countries.update(scopes)
        results.append(result)
    return {"countries": sorted(countries), "brands": results}


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
        "SELECT brand_id, snapshot_date, app_downloads_est, app_download_basis, app_reviews, source_updated_at, updated_at "
        f"FROM market_share_snapshots WHERE brand_id IN ({placeholders}) "
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
            if _snapshot_downloads(state.get(brand_id, {})) or _number(state.get(brand_id, {}).get("app_reviews"))
        )
        if evidence_brands >= comparable_floor:
            points.append({
                "date": day,
                "shares": _trend_shares(ids, state, model),
                "public_metrics": {
                    brand_id: {
                        "rating_count": round(_number(state.get(brand_id, {}).get("app_reviews"))),
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
    base_weights = preset["weights"]
    rows: list[dict] = []
    for brand_id in ids:
        brand = by_id[brand_id]
        record_data = _record_signals(conn, brand, start, end, country_code, include_engagement=base_weights["engagement"] > 0)
        sales_data = _sales_signals(conn, brand_id, start, end, country_code) if base_weights["sales"] else {}
        conversation_signal = record_data["app_reviews"]
        signals = {
            "sales": 0,
            "app": record_data["app_downloads"],
            "conversation": conversation_signal,
            "engagement": record_data.get("engagement", 0),
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

    comparable_floor = max(2, math.ceil(len(rows) * 0.5))

    # Do not mix currencies with unit counts. Choose one sales basis that is
    # comparable across enough brands; otherwise the sales signal is excluded.
    revenue_currencies = {
        row["raw"].get("sales_currency")
        for row in rows
        if row["raw"].get("sales_revenue", 0) > 0 and row["raw"].get("sales_currency")
    }
    revenue_currency = next(iter(revenue_currencies)) if len(revenue_currencies) == 1 else None
    revenue_coverage = sum(
        1
        for row in rows
        if row["raw"].get("sales_revenue", 0) > 0
        and not row["raw"].get("sales_currency_mixed")
        and row["raw"].get("sales_currency") == revenue_currency
    )
    unit_coverage = sum(1 for row in rows if row["raw"].get("sales_units", 0) > 0)
    if revenue_coverage >= comparable_floor:
        sales_basis = "revenue"
        for row in rows:
            if row["raw"].get("sales_currency") == revenue_currency and not row["raw"].get("sales_currency_mixed"):
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
        if row["raw"]["excluded_app_records"]:
            gaps.append(f"已排除 {row['raw']['excluded_app_records']} 条非当前配置应用的记录")
        if base_weights["sales"] > 0 and not row["signals"]["sales"]:
            gaps.append("缺少可比较的销量或销售额")
        if not row["signals"]["app"]:
            gaps.append("缺少实采 App 下载量")
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
    # Rating counts are cumulative public totals, not independent evidence
    # samples. Count distinct observed apps for confidence instead of letting a
    # popular app's lifetime rating total inflate the score.
    evidence_total = sum(row["raw"].get("app_evidence_count", 0) for row in rows)
    sample_strength = min(1.0, math.log1p(evidence_total) / math.log1p(max(500, len(rows) * 250)))
    base_confidence = 100 * (0.55 * active_base_weight + 0.25 * fairness + 0.20 * sample_strength)
    # A ratings-only brand has less corroborating evidence than one with a measured download count.
    ratings_only_ratio = sum(1 for row in rows if row["signals"]["conversation"] and not row["signals"]["app"]) / len(rows)
    confidence_score = round(base_confidence * (1.0 - 0.30 * ratings_only_ratio))
    confidence_label = "高" if confidence_score >= 75 else "中" if confidence_score >= 50 else "低"

    warnings = [
        "该结果是所选品牌与已采集数据源内的相对份额，不等同于官方全行业市占。",
        "统计的是所选时段内最近采集的累计评分，不是该时段新增评分、下载量或销量。",
    ]
    if country_code:
        warnings.append("Google Play 累计评分为全球口径，不计入单个国家的评分样本。")
    else:
        warnings.append("Google Play 全球累计评分按应用包名去重，不重复累加各国家页面。")
    if country_code:
        warnings.append(f"当前仅统计国家标签为 {country_code} 的记录；未标注国家的数据不会混入该国家。")
    categories = {str(row["category"]).strip() for row in rows if row.get("category")}
    if len(categories) > 1:
        warnings.append("所选品牌分属不同品类，横向市占的业务含义可能有限。")
    elif any(not row.get("category") for row in rows):
        warnings.append("部分品牌未设置品类，请确认它们属于同一竞争市场。")
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
            "ratings_only_ratio": round(ratings_only_ratio, 2),
        },
        "brands": rows,
        "warnings": warnings,
    }
