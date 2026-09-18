"""Third-party creator provider seam for Instagram / TikTok / X.

These platforms expose no free, commercial keyword-search API, so real data
requires a paid aggregator (e.g. Ensemble Data, which offers unit-priced
``Search Keyword`` endpoints across IG/TikTok/X). This provider is the seam:
it only activates when an ``ensembledata_token`` is configured and otherwise
returns nothing, leaving the connector in a ``needs_credential`` state.

The endpoint paths/field names below follow Ensemble Data's documented shape but
MUST be verified against the live API before relying on them in production; all
parsing is defensive so a schema mismatch degrades to an empty result rather than
crashing the collection run.
"""
from __future__ import annotations

import sqlite3
from urllib.parse import quote_plus

from ...fetchers import FetchError, fetch_json
from ...util import clean_text, utc_now
from .base import CreatorPost, CreatorProvider

_BASE = "https://ensembledata.com/apis"

# platform -> (keyword-search endpoint, result list key)
_ENDPOINTS = {
    "instagram": ("/instagram/keyword/search", "data"),
    "tiktok": ("/tt/keyword/search", "data"),
    "x": ("/twitter/keyword/search", "data"),
}


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first(d: dict, *keys):
    for key in keys:
        if isinstance(d, dict) and d.get(key) not in (None, ""):
            return d[key]
    return None


def _url(value) -> str:
    """Return a usable image URL from the provider's loose image shapes."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("url", "src", "href"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""


def _thumbnail(item: dict, _depth: int = 0) -> str:
    """Extract post media from Instagram/TikTok/X response variants.

    Aggregator responses are not consistent across platforms or API versions:
    some return a flat ``display_url``/``thumbnail_url``, while Instagram may
    nest candidates under ``image_versions2`` or ``carousel_media``.  Keep the
    extraction defensive so a schema change only removes a preview instead of
    dropping the whole post.
    """
    direct = _first(
        item,
        "thumbnail_url", "thumbnailUrl", "thumbnail_src", "thumbnailSrc",
        "cover_url", "coverUrl", "display_url", "displayUrl", "display_uri",
        "displayUri", "image_url", "imageUrl", "photo_url", "photoUrl",
        "media_url", "mediaUrl", "thumbnail", "image", "cover", "poster",
    )
    found = _url(direct)
    if found:
        return found

    for container in (
        item.get("image_versions2"),
        item.get("images"),
        item.get("thumbnails"),
        item.get("thumbnail_resources"),
        item.get("thumbnailResources"),
        item.get("display_resources"),
        item.get("displayResources"),
        item.get("video_versions"),
    ):
        if isinstance(container, dict):
            candidates = container.get("candidates") or container.get("items") or []
            if isinstance(candidates, list):
                for candidate in candidates:
                    found = _url(candidate)
                    if found:
                        return found
            found = _url(container)
            if found:
                return found
        elif isinstance(container, list):
            for candidate in container:
                found = _url(candidate)
                if found:
                    return found

    carousel = item.get("carousel_media") or item.get("carouselMedia") or []
    if isinstance(carousel, list):
        for media in carousel:
            if not isinstance(media, dict):
                continue
            found = _thumbnail(media, _depth + 1)
            if found:
                return found

    # Some aggregator versions wrap the actual post under ``media``, ``node``,
    # ``post`` or ``data``. Keep this bounded so malformed payloads cannot
    # recurse forever while still covering the common Instagram shapes.
    if _depth < 3:
        for key in ("media", "post", "node", "item", "data", "result"):
            nested = item.get(key)
            if isinstance(nested, dict):
                found = _thumbnail(nested, _depth + 1)
                if found:
                    return found
            elif isinstance(nested, list):
                for child in nested:
                    if isinstance(child, dict):
                        found = _thumbnail(child, _depth + 1)
                        if found:
                            return found
    return ""


class ThirdPartyCreatorProvider(CreatorProvider):
    """Aggregator-backed provider for platforms without a free official API."""

    name = "ensembledata"

    def __init__(self, platform: str, token: str, max_results: int = 25) -> None:
        self.platform = platform
        self.token = token
        self.max_results = max(1, min(max_results, 50))

    def collect(self, conn: sqlite3.Connection, brand: dict, queries: list[str]) -> list[CreatorPost]:
        endpoint = _ENDPOINTS.get(self.platform)
        if not self.token or not endpoint:
            return []
        path, list_key = endpoint
        posts: dict[str, CreatorPost] = {}
        for query in queries:
            url = (
                f"{_BASE}{path}?token={quote_plus(self.token)}"
                f"&keyword={quote_plus(query)}&period=180"
            )
            try:
                data = fetch_json(url, timeout=20)
            except FetchError:
                continue
            items = (data.get(list_key) if isinstance(data, dict) else None) or []
            for item in items[: self.max_results]:
                post = self._normalize(item, query)
                if post:
                    posts.setdefault(post.external_id, post)
        return list(posts.values())

    def _normalize(self, item: dict, query: str) -> CreatorPost | None:
        if not isinstance(item, dict):
            return None
        external_id = str(_first(item, "id", "aweme_id", "pk", "tweet_id", "shortcode") or "").strip()
        if not external_id:
            return None
        author = clean_text(str(_first(item, "username", "nickname", "author_name", "screen_name") or ""))
        thumbnail_url = _thumbnail(item)
        avatar_url = _url(_first(item, "avatar_url", "avatarUrl", "profile_pic_url", "profilePicUrl", "author_avatar"))
        return CreatorPost(
            platform=self.platform,
            external_id=external_id,
            url=str(_first(item, "url", "permalink", "share_url") or ""),
            title=clean_text(str(_first(item, "title", "caption", "desc", "text") or ""))[:200],
            body=clean_text(str(_first(item, "caption", "desc", "text", "title") or "")),
            author=author,
            author_handle=clean_text(str(_first(item, "username", "screen_name", "unique_id") or "")).lower(),
            avatar_url=avatar_url,
            thumbnail_url=thumbnail_url,
            occurred_at=_first(item, "created_at", "create_time", "taken_at") or utc_now(),
            views=_to_int(_first(item, "play_count", "view_count", "views")),
            likes=_to_int(_first(item, "like_count", "digg_count", "likes", "favorite_count")),
            comments=_to_int(_first(item, "comment_count", "comments", "reply_count")),
            shares=_to_int(_first(item, "share_count", "reshare_count", "retweet_count")),
            follower_count=_to_int(_first(item, "follower_count", "followers")),
            # Keep the normalized provider payload so a later API shape change
            # can still recover a cover URL at read time without losing data.
            raw={"query": query, "provider": self.name, **item},
        )
