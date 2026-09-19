"""Sales collection runner: expand storefronts -> listings -> daily snapshots.

Provider-agnostic. For each active sales link it expands the storefront into the
``sales_listings`` registry, then captures a daily ``sales_metrics`` row for every
monitored listing, detecting content changes against the previous fingerprint.
"""
from __future__ import annotations

import json
import sqlite3

from ...util import canonical_url, clean_text, new_id, today, utc_now
from . import pick_provider
from .base import ListingRef, ListingSnapshot, canonicalize_metric_changes, metric_rank_value

# Channels that have an automated provider (others are manual-entry only).
AUTOMATED_CHANNELS = ("amazon", "dtc", "other_ecom")


def _listing_key(ref_asin: str, ref_url: str) -> str:
    return (ref_asin or "").upper() or canonical_url(ref_url)


def _upsert_listing(conn: sqlite3.Connection, link: dict, ref: ListingRef) -> str:
    now = utc_now()
    try:
        link_config = json.loads(link.get("config_json") or "{}")
    except (TypeError, ValueError):
        link_config = {}
    asin = (ref.asin or "").upper()
    canon = canonical_url(ref.url)
    existing = conn.execute(
        """
        SELECT * FROM sales_listings
        WHERE link_id = ? AND ((asin != '' AND asin = ?) OR canonical_url = ?)
        LIMIT 1
        """,
        (link["id"], asin, canon),
    ).fetchone()
    if existing:
        try:
            existing_config = json.loads(existing["config_json"] or "{}")
        except (TypeError, ValueError):
            existing_config = {}
        merged_config = {**link_config, **existing_config}
        if "fingerprint" in existing_config:
            merged_config["fingerprint"] = existing_config["fingerprint"]
        conn.execute(
            """
            UPDATE sales_listings
            SET url = ?, canonical_url = ?, asin = COALESCE(NULLIF(?, ''), asin),
                marketplace = COALESCE(NULLIF(?, ''), marketplace),
                title = COALESCE(NULLIF(?, ''), title),
                image_url = COALESCE(NULLIF(?, ''), image_url),
                config_json = ?, last_seen = ?, updated_at = ?
            WHERE id = ?
            """,
            (ref.url, canon, asin, ref.marketplace, clean_text(ref.title),
             ref.image_url, json.dumps(merged_config, ensure_ascii=False), now, now, existing["id"]),
        )
        return existing["id"]

    listing_id = new_id()
    conn.execute(
        """
        INSERT INTO sales_listings (id, brand_id, product_id, link_id, channel, platform,
            asin, url, canonical_url, marketplace, title, sku, image_url, status, monitor,
            first_seen, last_seen, config_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, '{}', ?, ?)
        """,
        (
            listing_id, link["brand_id"], link.get("product_id"), link["id"], link["channel"],
            link.get("platform"), asin, ref.url, canon, ref.marketplace, clean_text(ref.title),
            ref.sku, ref.image_url, now, now, now, now,
        ),
    )
    if link_config:
        conn.execute(
            "UPDATE sales_listings SET config_json = ? WHERE id = ?",
            (json.dumps(link_config, ensure_ascii=False), listing_id),
        )
    return listing_id


def _diff_fingerprint(old_fp: dict, new_fp: dict) -> list[dict]:
    changes: list[dict] = []
    # Bridge fingerprints written before explicit category ranks existed. The
    # old `rank`/`bsr` value is the same broad category signal as the new
    # `category_rank`, so an upgrade should still produce a meaningful event.
    old_category = old_fp.get("category_rank")
    old_has_category_signal = "category_rank" in old_fp or "rank" in old_fp or "bsr" in old_fp
    if old_category is None and ("rank" in old_fp or "bsr" in old_fp):
        old_category = metric_rank_value(old_fp)
    new_category = new_fp.get("category_rank")
    if new_category is None and ("rank" in new_fp or "bsr" in new_fp):
        new_category = metric_rank_value(new_fp)
    if "category_rank" in new_fp and (new_category is not None or "category_rank" in old_fp):
        if old_has_category_signal and old_category != new_category and not (old_category is None and new_category is None):
            changes.append({"field": "category_rank", "from": old_category, "to": new_category})
    for key, new_val in new_fp.items():
        # Fingerprints written before a field was introduced should not emit a
        # false positive on the first capture after an upgrade.
        if key == "rank":
            if "rank" not in old_fp and "bsr" not in old_fp:
                continue
            old_val = metric_rank_value(old_fp)
        elif key in {"category_rank", "subcategory_rank"}:
            # Explicit rank fields are handled below with legacy bridging and
            # should not be treated as first-seen noise here.
            continue
        else:
            if key not in old_fp:
                continue
            old_val = old_fp.get(key)
        if old_val == new_val:
            continue
        # Skip "first time we ever saw a value" noise (None -> value on a brand-new field).
        if old_val in (None, "") and new_val in (None, ""):
            continue
        changes.append({"field": key, "from": old_val, "to": new_val})
    if "subcategory_rank" in new_fp and "subcategory_rank" in old_fp:
        old_subcategory = old_fp.get("subcategory_rank")
        new_subcategory = new_fp.get("subcategory_rank")
        if old_subcategory != new_subcategory and not (old_subcategory is None and new_subcategory is None):
            changes.append({"field": "subcategory_rank", "from": old_subcategory, "to": new_subcategory})
    return canonicalize_metric_changes(changes)


def _merge_changes(existing: list[dict], current: list[dict]) -> list[dict]:
    """Merge repeated captures from the same day into one day-level event.

    A same-day refresh replaces the metric row. Preserve the first observed
    value as ``from`` while updating ``to`` to the newest value, so a manual
    refresh cannot erase the day's rank/rating/review log.
    """
    merged: dict[str, dict] = {}
    for change in canonicalize_metric_changes([*(existing or []), *(current or [])]):
        field = str(change.get("field") or "other")
        if field not in merged:
            merged[field] = {"field": field, "from": change.get("from"), "to": change.get("to")}
        else:
            merged[field]["to"] = change.get("to")
    return [item for item in merged.values() if item.get("from") != item.get("to")]


def _record_snapshot(conn: sqlite3.Connection, listing: dict, snap: ListingSnapshot) -> dict:
    """Insert today's metric row (replacing any existing one) and detect changes."""
    now = utc_now()
    day = today()
    config = json.loads(listing.get("config_json") or "{}")
    old_fp = config.get("fingerprint") or {}
    new_fp = snap.fingerprint_fields()

    changes: list[dict] = []
    change_score = 0.0
    if old_fp:
        changes = _diff_fingerprint(old_fp, new_fp)
        change_score = round(len(changes) / max(1, len(new_fp)), 4)

    # A blocked retry must not erase a useful snapshot captured earlier today.
    existing_today = conn.execute(
        "SELECT * FROM sales_metrics WHERE link_id = ? AND snapshot_date = ? LIMIT 1",
        (listing["id"], day),
    ).fetchone()
    existing_changes: list[dict] = []
    if existing_today:
        try:
            existing_changes = json.loads(existing_today["changes_json"] or "[]")
        except (TypeError, ValueError):
            existing_changes = []
    existing_columns = set(existing_today.keys()) if existing_today else set()
    has_existing_data = existing_today and any(
        existing_today[key] is not None
        for key in ("price", "rating", "review_count", "rank", "bsr", "category_rank", "subcategory_rank", "units_est", "revenue_est")
        if key in existing_columns
    )
    if snap.status in ("blocked", "error") and has_existing_data:
        conn.execute(
            """
            UPDATE sales_listings
            SET last_seen = ?, last_status = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, snap.status, snap.error, now, listing["id"]),
        )
        return {"changed": False, "status": snap.status, "preserved": True}

    # Dedupe: one metric row per listing per day. Keep a same-day event's
    # original baseline when a refresh captures a second value.
    changes = _merge_changes(existing_changes, changes)
    change_score = round(len(changes) / max(1, len(new_fp)), 4)
    conn.execute(
        "DELETE FROM sales_metrics WHERE link_id = ? AND snapshot_date = ?",
        (listing["id"], day),
    )
    raw = dict(snap.raw or {})
    if snap.estimate_method:
        raw.setdefault("estimate_method", snap.estimate_method)
        raw.setdefault("estimate_confidence", snap.estimate_confidence)
        raw.setdefault("estimate_period_days", snap.estimate_period_days)
        raw.setdefault("estimate_basis", snap.estimate_basis or {})
    metric_values = {
        "id": new_id(),
        "link_id": listing["id"],
        "brand_id": listing["brand_id"],
        "product_id": listing.get("product_id"),
        "snapshot_date": day,
        "channel": listing["channel"],
        "platform": listing.get("platform"),
        "price": snap.price,
        "currency": snap.currency,
        "review_count": snap.review_count,
        "rating": snap.rating,
        "rank": snap.rank,
        "category_rank": snap.category_rank,
        "subcategory_rank": snap.subcategory_rank,
        "category_name": snap.category_name or None,
        "subcategory_name": snap.subcategory_name or None,
        "units_est": snap.units_est,
        "revenue_est": snap.revenue_est,
        "in_stock": None if snap.in_stock is None else int(snap.in_stock),
        "asin": snap.sku or listing.get("asin"),
        "bsr": snap.bsr,
        "title": clean_text(snap.title) or listing.get("title"),
        "image_url": snap.image_url,
        "estimate_method": snap.estimate_method or None,
        "estimate_confidence": snap.estimate_confidence or None,
        "estimate_period_days": snap.estimate_period_days,
        "change_score": change_score,
        "changes_json": json.dumps(changes, ensure_ascii=False),
        "source": "sellersprite" if raw.get("provider") == "sellersprite" else "scrape",
        "raw_json": json.dumps(raw, ensure_ascii=False),
        "created_at": now,
    }
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sales_metrics)")}
    selected = [key for key in metric_values if key in columns]
    placeholders = ", ".join("?" for _ in selected)
    conn.execute(
        f"INSERT INTO sales_metrics ({', '.join(selected)}) VALUES ({placeholders})",
        [metric_values[key] for key in selected],
    )

    config["fingerprint"] = new_fp
    last_change_at = now if changes else listing.get("last_change_at")
    conn.execute(
        """
        UPDATE sales_listings
        SET title = COALESCE(NULLIF(?, ''), title),
            image_url = COALESCE(NULLIF(?, ''), image_url),
            sku = COALESCE(NULLIF(?, ''), sku),
            last_seen = ?, last_status = ?, last_error = ?, last_change_at = ?,
            config_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            clean_text(snap.title), snap.image_url, snap.sku, now, snap.status, snap.error,
            last_change_at, json.dumps(config, ensure_ascii=False), now, listing["id"],
        ),
    )
    return {"changed": bool(changes), "status": snap.status}


def run_sales_collection(conn: sqlite3.Connection, brand: dict, link_id: str | None = None) -> dict:
    """Expand configured links into listings and capture monitored listings.

    If ``link_id`` is given, only that link (and its listings) is processed.
    """
    summary = {"links": 0, "listings": 0, "captured": 0, "changed": 0, "errors": 0}

    link_clause = "AND id = ?" if link_id else ""
    link_params: tuple = (brand["id"], link_id) if link_id else (brand["id"],)
    links = conn.execute(
        f"""
        SELECT * FROM links
        WHERE brand_id = ? AND dimension = 'sales' AND status = 'active'
              AND url IS NOT NULL AND url != '' {link_clause}
        """,
        link_params,
    ).fetchall()

    for row in links:
        link = dict(row)
        provider = pick_provider(link["channel"], conn)
        if provider is None:
            continue
        summary["links"] += 1
        try:
            refs = provider.expand(conn, link)
        except Exception as exc:  # noqa: BLE001 - surface as link error, keep going
            conn.execute(
                "UPDATE links SET last_status = ?, last_error = ?, last_collect_at = ?, updated_at = ? WHERE id = ?",
                ("error", str(exc)[:300], utc_now(), utc_now(), link["id"]),
            )
            summary["errors"] += 1
            continue
        for ref in refs:
            _upsert_listing(conn, link, ref)
            summary["listings"] += 1
        conn.execute(
            "UPDATE links SET last_status = ?, last_error = '', last_collect_at = ?, updated_at = ? WHERE id = ?",
            ("ok", utc_now(), utc_now(), link["id"]),
        )

    # Capture monitored listings.
    listing_clause = "AND link_id = ?" if link_id else ""
    listing_params: tuple = (brand["id"], link_id) if link_id else (brand["id"],)
    listings = conn.execute(
        f"""
        SELECT * FROM sales_listings
        WHERE brand_id = ? AND monitor = 1 AND status = 'active' {listing_clause}
        """,
        listing_params,
    ).fetchall()

    for row in listings:
        listing = dict(row)
        provider = pick_provider(listing["channel"], conn)
        if provider is None:
            continue
        try:
            snap = provider.fetch(conn, listing)
        except Exception as exc:  # noqa: BLE001
            conn.execute(
                "UPDATE sales_listings SET last_status = 'error', last_error = ?, last_seen = ?, updated_at = ? WHERE id = ?",
                (str(exc)[:300], utc_now(), utc_now(), listing["id"]),
            )
            summary["errors"] += 1
            continue
        result = _record_snapshot(conn, listing, snap)
        summary["captured"] += 1
        if result["changed"]:
            summary["changed"] += 1
        if snap.status in ("error", "blocked"):
            summary["errors"] += 1

    return summary
