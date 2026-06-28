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
from .base import ListingRef, ListingSnapshot

# Channels that have an automated provider (others are manual-entry only).
AUTOMATED_CHANNELS = ("amazon", "dtc", "other_ecom")


def _listing_key(ref_asin: str, ref_url: str) -> str:
    return (ref_asin or "").upper() or canonical_url(ref_url)


def _upsert_listing(conn: sqlite3.Connection, link: dict, ref: ListingRef) -> str:
    now = utc_now()
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
        conn.execute(
            """
            UPDATE sales_listings
            SET url = ?, canonical_url = ?, asin = COALESCE(NULLIF(?, ''), asin),
                marketplace = COALESCE(NULLIF(?, ''), marketplace),
                title = COALESCE(NULLIF(?, ''), title),
                image_url = COALESCE(NULLIF(?, ''), image_url),
                last_seen = ?, updated_at = ?
            WHERE id = ?
            """,
            (ref.url, canon, asin, ref.marketplace, clean_text(ref.title),
             ref.image_url, now, now, existing["id"]),
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
    return listing_id


def _diff_fingerprint(old_fp: dict, new_fp: dict) -> list[dict]:
    changes: list[dict] = []
    for key, new_val in new_fp.items():
        old_val = old_fp.get(key)
        if old_val == new_val:
            continue
        # Skip "first time we ever saw a value" noise (None -> value on a brand-new field).
        if old_val in (None, "") and new_val in (None, ""):
            continue
        changes.append({"field": key, "from": old_val, "to": new_val})
    return changes


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

    # Dedupe: one metric row per listing per day.
    conn.execute(
        "DELETE FROM sales_metrics WHERE link_id = ? AND snapshot_date = ?",
        (listing["id"], day),
    )
    conn.execute(
        """
        INSERT INTO sales_metrics (id, link_id, brand_id, product_id, snapshot_date, channel,
            platform, price, currency, review_count, rating, rank, units_est, revenue_est,
            in_stock, asin, bsr, title, image_url, change_score, changes_json, source,
            raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(), listing["id"], listing["brand_id"], listing.get("product_id"), day,
            listing["channel"], listing.get("platform"), snap.price, snap.currency,
            snap.review_count, snap.rating, snap.rank, snap.units_est, snap.revenue_est,
            None if snap.in_stock is None else int(snap.in_stock), snap.sku or listing.get("asin"),
            snap.bsr, clean_text(snap.title) or listing.get("title"), snap.image_url,
            change_score, json.dumps(changes, ensure_ascii=False),
            "sellersprite" if snap.raw.get("provider") == "sellersprite" else "scrape",
            json.dumps(snap.raw, ensure_ascii=False), now,
        ),
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
