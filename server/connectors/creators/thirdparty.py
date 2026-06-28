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
        return CreatorPost(
            platform=self.platform,
            external_id=external_id,
            url=str(_first(item, "url", "permalink", "share_url") or ""),
            title=clean_text(str(_first(item, "title", "caption", "desc", "text") or ""))[:200],
            body=clean_text(str(_first(item, "caption", "desc", "text", "title") or "")),
            author=author,
            author_handle=clean_text(str(_first(item, "username", "screen_name", "unique_id") or "")).lower(),
            occurred_at=_first(item, "created_at", "create_time", "taken_at") or utc_now(),
            views=_to_int(_first(item, "play_count", "view_count", "views")),
            likes=_to_int(_first(item, "like_count", "digg_count", "likes", "favorite_count")),
            comments=_to_int(_first(item, "comment_count", "comments", "reply_count")),
            shares=_to_int(_first(item, "share_count", "reshare_count", "retweet_count")),
            follower_count=_to_int(_first(item, "follower_count", "followers")),
            raw={"query": query, "provider": self.name},
        )
