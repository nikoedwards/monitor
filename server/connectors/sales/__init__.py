"""Sales provider registry: pick the right adapter per channel.

Amazon prefers SellerSprite when a key is configured (reliable sales/rank), otherwise
falls back to best-effort scraping. DTC and other e-commerce use generic scraping.
Offline / unknown channels have no automated provider (manual entry only).
"""
from __future__ import annotations

import sqlite3

from ...config import CREDENTIALS
from .amazon import ScrapeAmazonProvider
from .base import ListingRef, ListingSnapshot, SalesProvider
from .dtc import ScrapeDtcProvider
from .sellersprite import SellerSpriteProvider

__all__ = [
    "ListingRef",
    "ListingSnapshot",
    "SalesProvider",
    "ScrapeAmazonProvider",
    "ScrapeDtcProvider",
    "SellerSpriteProvider",
    "pick_provider",
    "sellersprite_key",
]


def sellersprite_key(conn: sqlite3.Connection) -> str:
    """Settings-table override wins over the env-provided credential."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", ("sellersprite_secret_key",)).fetchone()
    if row and (row["value"] or "").strip():
        return row["value"].strip()
    return (CREDENTIALS.get("sellersprite_secret_key") or "").strip()


def pick_provider(channel: str, conn: sqlite3.Connection) -> SalesProvider | None:
    if channel == "amazon":
        key = sellersprite_key(conn)
        if key:
            return SellerSpriteProvider(key)
        return ScrapeAmazonProvider()
    if channel in ("dtc", "other_ecom"):
        return ScrapeDtcProvider()
    return None
