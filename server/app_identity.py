"""App identities come from configured store links, never from title similarity."""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import parse_qs, urlparse


def app_id(platform: str, url: str = "", raw: dict | None = None) -> str:
    raw = raw if isinstance(raw, dict) else {}
    parsed = urlparse(url or "")
    if platform == "app_store":
        match = re.search(r"/id(\d+)(?:/|$)", parsed.path)
        return match.group(1) if match else str(raw.get("track_id") or "")
    if platform == "google_play":
        return str((parse_qs(parsed.query).get("id") or [raw.get("package_name") or ""])[0])
    return ""


def configured_app_ids(conn: sqlite3.Connection, brand_id: str) -> dict[str, set[str]] | None:
    """None preserves legacy brands; any configured store link locks both stores.

    Paused links still establish the identity boundary, but are not included.
    This also prevents an iOS-only brand from acquiring an unrelated Android app.
    """
    try:
        rows = conn.execute(
            "SELECT platform, url, status FROM links WHERE brand_id = ? "
            "AND platform IN ('app_store', 'google_play')", (brand_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Some small read-only/import databases do not contain link metadata.
        return None
    if not rows:
        return None
    allowed: dict[str, set[str]] = {"app_store": set(), "google_play": set()}
    for row in rows:
        identity = app_id(row["platform"], row["url"])
        if row["status"] == "active" and identity:
            allowed[row["platform"]].add(identity)
    return allowed


def app_record_allowed(record: dict, allowed: dict[str, set[str]] | None) -> bool:
    if allowed is None:
        return True
    platform = str(record.get("platform") or "").lower()
    if platform not in allowed:
        platform = {"app_store_reviews": "app_store", "google_play_reviews": "google_play"}.get(
            record.get("source_id"), platform,
        )
    if platform not in allowed:
        return True
    return app_id(platform, record.get("url") or "", record.get("raw")) in allowed[platform]
