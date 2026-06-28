"""Creator provider registry: pick the right adapter per platform.

YouTube uses the official Data API when a key is configured. Instagram / TikTok /
X have no free commercial search API, so they light up only when a third-party
aggregator token (``ensembledata_token``) is configured. Settings-table values
win over env-provided credentials (mirrors the sales/SellerSprite pattern).
"""
from __future__ import annotations

import sqlite3

from ...config import CREDENTIALS
from .base import PLATFORM_LABELS, PLATFORMS, CreatorPost, CreatorProvider, detect_collaboration
from .thirdparty import ThirdPartyCreatorProvider
from .youtube import YouTubeProvider

__all__ = [
    "PLATFORMS",
    "PLATFORM_LABELS",
    "CreatorPost",
    "CreatorProvider",
    "detect_collaboration",
    "pick_provider",
    "creator_credential",
]

# Which credential each platform needs (also drives the connector `status`).
PLATFORM_CREDENTIAL = {
    "youtube": "youtube_api_key",
    "instagram": "ensembledata_token",
    "tiktok": "ensembledata_token",
    "x": "ensembledata_token",
}


def creator_credential(conn: sqlite3.Connection, key: str) -> str:
    """Settings-table override wins over the env-provided credential."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row and (row["value"] or "").strip():
        return row["value"].strip()
    return (CREDENTIALS.get(key) or "").strip()


def pick_provider(platform: str, conn: sqlite3.Connection) -> CreatorProvider | None:
    if platform == "youtube":
        key = creator_credential(conn, "youtube_api_key")
        return YouTubeProvider(key) if key else None
    if platform in ("instagram", "tiktok", "x"):
        token = creator_credential(conn, "ensembledata_token")
        return ThirdPartyCreatorProvider(platform, token) if token else None
    return None
