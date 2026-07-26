"""Hiring provider registry: pick the right adapter per platform.

Boss 直聘 and LinkedIn both require a logged-in session cookie (configured in
settings, env fallback via CREDENTIALS). Providers are best-effort and degrade
to a ``blocked`` status when a page cannot be read.
"""
from __future__ import annotations

import sqlite3

from ...config import CREDENTIALS
from .base import (
    ActivityRef,
    HiringProvider,
    JobRef,
    JobSnapshot,
    PeopleProvider,
    ProfileRef,
    render,
)
from .boss import BossProvider
from .linkedin import LinkedInJobsProvider, LinkedInPeopleProvider

__all__ = [
    "ActivityRef",
    "HiringProvider",
    "JobRef",
    "JobSnapshot",
    "PeopleProvider",
    "ProfileRef",
    "render",
    "BossProvider",
    "LinkedInJobsProvider",
    "LinkedInPeopleProvider",
    "pick_provider",
    "pick_people_provider",
    "hiring_cookie",
    "PLATFORMS",
]

# Platforms that have an automated hiring provider.
PLATFORMS = ("boss", "linkedin")


def hiring_cookie(conn: sqlite3.Connection, key: str) -> str:
    """Settings-table cookie override wins over the env-provided credential."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row and (row["value"] or "").strip():
        return row["value"].strip()
    return (CREDENTIALS.get(key) or "").strip()


def pick_provider(platform: str, conn: sqlite3.Connection) -> HiringProvider | None:
    if platform == "boss":
        return BossProvider(cookie=hiring_cookie(conn, "boss_cookie"))
    if platform == "linkedin":
        return LinkedInJobsProvider(cookie=hiring_cookie(conn, "linkedin_cookie"))
    return None


def pick_people_provider(platform: str, conn: sqlite3.Connection) -> PeopleProvider | None:
    if platform == "linkedin":
        return LinkedInPeopleProvider(cookie=hiring_cookie(conn, "linkedin_cookie"))
    return None
