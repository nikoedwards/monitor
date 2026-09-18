"""Creator-monitoring provider contract + collaboration detection.

A ``CreatorProvider`` knows how to search one platform (YouTube / Instagram /
TikTok / X) for brand-relevant creator content and return normalized
``CreatorPost`` rows. The runner stays provider-agnostic: it picks a provider per
platform (official API vs third-party vs none), runs collaboration detection, and
persists posts as unified ``records`` + a materialized ``creators`` roster.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from ...util import clean_text

# Platforms covered by the influencer/creator section, in display order.
PLATFORMS = ("youtube", "instagram", "tiktok", "x")

PLATFORM_LABELS = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "x": "X",
}

# Explicit paid-partnership / ad-disclosure markers (hard signal of sponsorship).
_SPONSOR_TAGS = (
    "#ad", "#ads", "#sponsored", "#sponsor", "#paidpartnership", "#partner",
    "#collab", "#collaboration", "#广告", "#合作", "#推广", "#赞助",
    "paid partnership", "sponsored by", "in partnership with",
)


@dataclass
class CreatorPost:
    """One normalized piece of creator content discovered for a brand."""

    platform: str
    external_id: str
    url: str = ""
    title: str = ""
    body: str = ""
    author: str = ""              # creator display name / channel title
    author_handle: str = ""       # @handle / channel id (stable key)
    author_url: str = ""
    avatar_url: str = ""
    # Preview image for the post/video itself.  This is intentionally separate
    # from ``avatar_url`` (which identifies the creator) because third-party
    # providers commonly return both values.
    thumbnail_url: str = ""
    occurred_at: Optional[str] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    follower_count: Optional[int] = None
    raw: dict = field(default_factory=dict)

    def engagement(self) -> int:
        return (self.likes or 0) + (self.comments or 0) + (self.shares or 0)

    def engagement_rate(self) -> Optional[float]:
        base = self.follower_count or self.views
        if not base:
            return None
        return round(self.engagement() / base, 6)


class CreatorProvider:
    """Base provider. Subclasses override ``collect``."""

    name = "base"
    platform = ""

    def collect(self, conn: sqlite3.Connection, brand: dict, queries: list[str]) -> list[CreatorPost]:
        raise NotImplementedError


def collection_since(brand: dict, *, now: datetime | None = None) -> datetime:
    """Return the lower bound for the current scheduled creator collection.

    The scheduler injects ``_collection_since`` into the brand context.  Keeping
    a cadence fallback here also makes providers safe when called directly in a
    test or from an explicit manual sync.
    """
    current = now or datetime.now(timezone.utc)
    raw = brand.get("_collection_since") if isinstance(brand, dict) else None
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    cadence = str((brand or {}).get("_collection_cadence") or "daily").lower()
    delta = {"hourly": timedelta(hours=1), "weekly": timedelta(days=7)}.get(
        cadence, timedelta(days=1)
    )
    return current.astimezone(timezone.utc) - delta


def occurred_in_collection_window(post: CreatorPost, brand: dict, *, now: datetime | None = None) -> bool:
    """Require a parseable publication time inside the current cadence window."""
    if not post.occurred_at:
        return False
    try:
        occurred = datetime.fromisoformat(str(post.occurred_at).replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        published = occurred.astimezone(timezone.utc)
        return collection_since(brand, now=current) <= published <= current + timedelta(minutes=5)
    except (TypeError, ValueError):
        return False


def recent_creator_posts(posts: list[CreatorPost], brand: dict) -> list[CreatorPost]:
    """Drop historical or undated search results before persistence."""
    return [post for post in posts if occurred_in_collection_window(post, brand)]


# ----------------------------------------------------------- collaboration NLP
def _handle_from_url(url: str) -> str:
    """Pull the account handle out of a social profile URL (best-effort)."""
    try:
        path = urlparse(url).path
    except ValueError:
        return ""
    segment = (path or "").strip("/").split("/")[0]
    return segment.lstrip("@").lower()


def brand_signals(brand: dict) -> dict:
    """Collect the tokens that mark a piece of content as a brand collaboration."""
    handles: set[str] = set()
    social = brand.get("social_links")
    if isinstance(social, str):
        try:
            social = json.loads(social or "{}")
        except (TypeError, ValueError):
            social = {}
    for url in (social or {}).values():
        handle = _handle_from_url(url) if isinstance(url, str) else ""
        if handle:
            handles.add(handle)

    names: set[str] = set()
    if brand.get("name"):
        names.add(clean_text(brand["name"]).lower())
    keywords = brand.get("monitoring_keywords")
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords or "[]")
        except (TypeError, ValueError):
            keywords = []
    for kw in keywords or []:
        if isinstance(kw, str) and kw.strip():
            names.add(kw.strip().lower())
    return {"handles": handles, "names": {n for n in names if len(n) >= 2}}


def detect_collaboration(text: str, brand: dict, signals: dict | None = None) -> dict:
    """Classify whether creator content is a brand collaboration.

    Returns ``{is_collab, is_sponsored, collab_type, mentions}`` where
    ``collab_type`` is one of ``tag`` (@official handle), ``mention`` (brand name
    in caption), ``hashtag`` (ad/sponsor disclosure only) or ``none``.

    NOTE: this is a text-only signal. Soft placements (product shown in a video
    with no caption mention) are NOT detectable here and require the multimodal
    detection layer (see runner TODO) before they can be flagged.
    """
    sig = signals or brand_signals(brand)
    lowered = (text or "").lower()
    mentions: list[str] = []

    tagged = False
    for handle in sig["handles"]:
        if handle and (f"@{handle}" in lowered or f"/{handle}" in lowered):
            tagged = True
            mentions.append(f"@{handle}")

    named = False
    for name in sig["names"]:
        if name and name in lowered:
            named = True
            mentions.append(name)

    has_sponsor_tag = any(tag in lowered for tag in _SPONSOR_TAGS)

    if tagged:
        collab_type = "tag"
    elif named:
        collab_type = "mention"
    elif has_sponsor_tag:
        collab_type = "hashtag"
    else:
        collab_type = "none"

    is_collab = collab_type != "none"
    # Sponsorship = explicit ad disclosure, or a brand tag/mention paired with one.
    is_sponsored = has_sponsor_tag and (collab_type in ("tag", "mention", "hashtag"))
    return {
        "is_collab": is_collab,
        "is_sponsored": is_sponsored,
        "collab_type": collab_type,
        "mentions": sorted(set(mentions)),
    }


_TAG_RE = re.compile(r"#[\w\u4e00-\u9fff]+")


def extract_hashtags(text: str) -> list[str]:
    return _TAG_RE.findall(text or "")
