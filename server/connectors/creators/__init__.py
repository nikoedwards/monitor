"""Creator provider registry: pick the right adapter per platform.

YouTube keyword discovery uses the official Data API when a key is configured.
Instagram/TikTok/X global keyword discovery has no reliable free provider; their
known official account URLs are collected separately by ``social_accounts``.
"""
from __future__ import annotations

import sqlite3

from ...config import CREDENTIALS
from .base import PLATFORM_LABELS, PLATFORMS, CreatorPost, CreatorProvider, detect_collaboration
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
    return None
