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
