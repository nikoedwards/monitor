"""Creator provider registry for API and logged-out public-web collection."""
from __future__ import annotations

import sqlite3

from ...config import CREDENTIALS
from .base import PLATFORM_LABELS, PLATFORMS, CreatorPost, CreatorProvider, detect_collaboration
from .public_search import PublicSearchCreatorProvider
from .youtube import YouTubeProvider, YouTubePublicProvider

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
        return YouTubeProvider(key) if key else YouTubePublicProvider()
    if platform in {"instagram", "tiktok"}:
        return PublicSearchCreatorProvider(platform)
    return None
