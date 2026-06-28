"""YouTube creator provider (official Data API v3).

Free-tier feasible: ``search.list`` discovers brand-relevant videos, then
``videos.list`` enriches view/like/comment counts and ``channels.list`` adds the
creator's subscriber count. Requires a ``youtube_api_key`` credential.
"""
from __future__ import annotations

import sqlite3
from urllib.parse import quote_plus

from ...fetchers import FetchError, fetch_json
from ...util import clean_text, utc_now
from .base import CreatorPost, CreatorProvider

_API = "https://www.googleapis.com/youtube/v3"


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class YouTubeProvider(CreatorProvider):
    name = "youtube_api"
    platform = "youtube"

    def __init__(self, api_key: str, max_results: int = 25) -> None:
        self.api_key = api_key
        self.max_results = max(1, min(max_results, 50))

    def collect(self, conn: sqlite3.Connection, brand: dict, queries: list[str]) -> list[CreatorPost]:
        if not self.api_key:
            return []
        posts: dict[str, CreatorPost] = {}
        for query in queries:
            for post in self._search(query):
                posts.setdefault(post.external_id, post)
        if posts:
            self._enrich_videos(posts)
            self._enrich_channels(posts)
        return list(posts.values())

    # ------------------------------------------------------------------ search
    def _search(self, query: str) -> list[CreatorPost]:
        url = (
            f"{_API}/search?part=snippet&type=video&maxResults={self.max_results}"
            f"&q={quote_plus(query)}&key={quote_plus(self.api_key)}"
        )
        try:
            data = fetch_json(url, timeout=16)
        except FetchError:
            return []
        out: list[CreatorPost] = []
        for item in (data.get("items", []) if isinstance(data, dict) else []):
            snippet = item.get("snippet", {}) or {}
            video_id = (item.get("id", {}) or {}).get("videoId")
            if not video_id:
                continue
            channel_id = snippet.get("channelId") or ""
            out.append(CreatorPost(
                platform="youtube",
                external_id=video_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                title=clean_text(snippet.get("title")),
                body=clean_text(snippet.get("description")) or clean_text(snippet.get("title")),
                author=clean_text(snippet.get("channelTitle")),
                author_handle=channel_id,
                author_url=f"https://www.youtube.com/channel/{channel_id}" if channel_id else "",
                avatar_url=((snippet.get("thumbnails", {}) or {}).get("default", {}) or {}).get("url", ""),
                occurred_at=snippet.get("publishedAt") or utc_now(),
                raw={"query": query, "channel_id": channel_id},
            ))
        return out

    # ----------------------------------------------------------- video metrics
    def _enrich_videos(self, posts: dict[str, CreatorPost]) -> None:
        ids = list(posts.keys())
        for chunk_start in range(0, len(ids), 50):
            chunk = ids[chunk_start:chunk_start + 50]
            url = (
                f"{_API}/videos?part=statistics&id={quote_plus(','.join(chunk))}"
                f"&key={quote_plus(self.api_key)}"
            )
            try:
                data = fetch_json(url, timeout=16)
            except FetchError:
                continue
            for item in (data.get("items", []) if isinstance(data, dict) else []):
                post = posts.get(item.get("id"))
                if not post:
                    continue
                stats = item.get("statistics", {}) or {}
                post.views = _to_int(stats.get("viewCount"))
                post.likes = _to_int(stats.get("likeCount"))
                post.comments = _to_int(stats.get("commentCount"))

    # --------------------------------------------------------- channel metrics
    def _enrich_channels(self, posts: dict[str, CreatorPost]) -> None:
        channel_ids = {p.author_handle for p in posts.values() if p.author_handle}
        followers: dict[str, int | None] = {}
        ids = list(channel_ids)
        for chunk_start in range(0, len(ids), 50):
            chunk = ids[chunk_start:chunk_start + 50]
            url = (
                f"{_API}/channels?part=statistics&id={quote_plus(','.join(chunk))}"
                f"&key={quote_plus(self.api_key)}"
            )
            try:
                data = fetch_json(url, timeout=16)
            except FetchError:
                continue
            for item in (data.get("items", []) if isinstance(data, dict) else []):
                stats = item.get("statistics", {}) or {}
                followers[item.get("id")] = _to_int(stats.get("subscriberCount"))
        for post in posts.values():
            if post.author_handle in followers:
                post.follower_count = followers[post.author_handle]
