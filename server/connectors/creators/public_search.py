"""Logged-out Bing discovery for Instagram and TikTok creators.

Search engines are used only to discover public profile URLs.  Actual post
normalization is delegated to the existing Instagram/TikTok public adapters,
which do not use credentials or bypass private/login-gated content.
"""
from __future__ import annotations

import base64
import re
import sqlite3
from datetime import datetime
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from ...fetchers import FetchError, fetch_page
from ...util import clean_text
from ..public_social import collect_instagram_public, collect_tiktok_public
from .base import CreatorPost, CreatorProvider, collection_since, recent_creator_posts

_BING = "https://www.bing.com/search"
_MAX_QUERIES = 6
_MAX_CANDIDATES = 8


def _unwrap_bing_url(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.path == "/ck/a" or parsed.query:
        target = parse_qs(parsed.query).get("u", [""])[0]
        if target.startswith(("http://", "https://")):
            value = unquote(target)
        elif target.startswith("a1"):
            try:
                encoded = target[2:] + "=" * (-len(target[2:]) % 4)
                decoded = base64.urlsafe_b64decode(encoded).decode("utf-8")
                if decoded.startswith(("http://", "https://")):
                    value = decoded
            except (ValueError, UnicodeDecodeError):
                pass
    return value


def _profile_url(platform: str, value: str) -> tuple[str, str]:
    """Convert a result URL into a public profile URL and stable handle."""
    url = _unwrap_bing_url(value)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if platform == "instagram" and not (host == "instagram.com" or host.endswith(".instagram.com")):
        return "", ""
    if platform == "tiktok" and not (host == "tiktok.com" or host.endswith(".tiktok.com")):
        return "", ""
    parts = [part for part in parsed.path.split("/") if part]
    if platform == "instagram":
        if not parts or parts[0].lower() in {"p", "reel", "reels", "tv", "explore", "accounts"}:
            return "", ""
        handle = parts[0].lstrip("@").strip().lower()
        if not re.fullmatch(r"[a-z0-9._-]{2,64}", handle):
            return "", ""
        return f"https://www.instagram.com/{handle}/", handle
    # TikTok profile pages begin with @handle; video/search/tag pages are not
    # stable profile targets for the public profile adapter.
    if not parts or not parts[0].startswith("@"):
        return "", ""
    handle = parts[0].lstrip("@").strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{2,64}", handle):
        return "", ""
    return f"https://www.tiktok.com/@{handle}", handle


def _bing_freshness(cadence: str) -> str:
    # Bing's shortest supported window is a day. The provider applies the
    # precise hourly/daily/weekly cutoff to each platform post afterwards.
    return "Week" if (cadence or "daily").lower() == "weekly" else "Day"


def discover_profile_urls(
    platform: str,
    query: str,
    *,
    cadence: str = "daily",
    since: datetime | None = None,
    limit: int = _MAX_CANDIDATES,
) -> list[tuple[str, str]]:
    """Return distinct public account URLs from one Bing result page."""
    freshness = _bing_freshness(cadence)
    date_operator = f" after:{(since or datetime.now()).date().isoformat()}"
    search = f"site:{platform}.com {query}{date_operator}"
    url = f"{_BING}?q={quote_plus(search)}&freshness={freshness}"
    page = fetch_page(url, timeout=18)
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in page.get("anchors", []) or []:
        profile, handle = _profile_url(platform, str(anchor))
        if profile and profile not in seen:
            seen.add(profile)
            found.append((profile, handle))
            if len(found) >= max(1, limit):
                break
    return found


class PublicSearchCreatorProvider(CreatorProvider):
    """Free public-web keyword discovery for Instagram or TikTok."""

    name = "bing_public_search"

    def __init__(self, platform: str, max_results: int = 12) -> None:
        if platform not in {"instagram", "tiktok"}:
            raise ValueError(f"Unsupported public search platform: {platform}")
        self.platform = platform
        self.max_results = max(1, min(max_results, 24))

    def collect(self, conn: sqlite3.Connection, brand: dict, queries: list[str]) -> list[CreatorPost]:
        del conn
        posts: dict[str, CreatorPost] = {}
        candidates: list[tuple[str, str, str]] = []
        seen_profiles: set[str] = set()
        discovery_failures: list[str] = []
        discovery_succeeded = 0
        cadence = str(brand.get("_collection_cadence") or "daily")
        since = collection_since(brand)
        for query in [str(q).strip() for q in queries[:_MAX_QUERIES] if str(q).strip()]:
            try:
                discovered = discover_profile_urls(self.platform, query, cadence=cadence, since=since)
                discovery_succeeded += 1
            except (FetchError, OSError, ValueError) as exc:
                discovery_failures.append(str(exc)[:160])
                continue
            for profile, handle in discovered:
                if profile in seen_profiles:
                    continue
                seen_profiles.add(profile)
                candidates.append((profile, handle, query))
                if len(candidates) >= _MAX_CANDIDATES:
                    break
            if len(candidates) >= _MAX_CANDIDATES:
                break

        if discovery_succeeded == 0 and discovery_failures:
            raise FetchError(
                f"Bing {self.platform} 关键词发现失败：{discovery_failures[0]}"
            )

        profile_failures: list[str] = []
        profiles_succeeded = 0
        for profile, handle, query in candidates:
            try:
                if self.platform == "instagram":
                    profile_posts = collect_instagram_public(profile, handle, limit=self.max_results)
                else:
                    profile_posts = collect_tiktok_public(profile, handle, limit=self.max_results)
                profiles_succeeded += 1
            except (FetchError, OSError, ValueError) as exc:
                profile_failures.append(str(exc)[:160])
                continue
            for post in recent_creator_posts(profile_posts or [], brand):
                post.raw.setdefault("collection_method", "bing_public_search")
                post.raw["discovery_query"] = query
                posts.setdefault(f"{post.platform}:{post.external_id}", post)
        if candidates and profiles_succeeded == 0 and profile_failures:
            raise FetchError(
                f"{self.platform} 公开主页采集失败：{profile_failures[0]}"
            )
        return list(posts.values())
