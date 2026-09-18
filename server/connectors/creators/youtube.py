"""YouTube creator provider (official Data API v3).

Free-tier feasible: ``search.list`` discovers brand-relevant videos, then
``videos.list`` enriches view/like/comment counts and ``channels.list`` adds the
creator's subscriber count. Requires a ``youtube_api_key`` credential.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote_plus

from ...fetchers import FetchError, fetch_json
from ...util import clean_text
from .base import CreatorPost, CreatorProvider, collection_since, recent_creator_posts

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
        published_after = collection_since(brand).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        for query in queries:
            for post in self._search(query, published_after=published_after):
                existing = posts.get(post.external_id)
                if existing is None:
                    post.raw["matched_queries"] = [query]
                    posts[post.external_id] = post
                else:
                    matched = existing.raw.setdefault("matched_queries", [])
                    if query not in matched:
                        matched.append(query)
        if posts:
            self._enrich_videos(posts)
            self._enrich_channels(posts)
        return recent_creator_posts(list(posts.values()), brand)

    # ------------------------------------------------------------------ search
    def _search(self, query: str, *, published_after: str | None = None) -> list[CreatorPost]:
        url = (
            f"{_API}/search?part=snippet&type=video&maxResults={self.max_results}"
            f"&q={quote_plus(query)}&key={quote_plus(self.api_key)}"
        )
        if published_after:
            url += f"&publishedAfter={quote_plus(published_after)}"
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
                # YouTube exposes the video preview in ``snippet.thumbnails``.
                # Prefer the largest available rendition so the content card
                # does not fall back to a blank cover.
                avatar_url=((snippet.get("thumbnails", {}) or {}).get("default", {}) or {}).get("url", ""),
                thumbnail_url=next(
                    (
                        ((snippet.get("thumbnails", {}) or {}).get(name, {}) or {}).get("url", "")
                        for name in ("maxres", "standard", "high", "medium", "default")
                        if ((snippet.get("thumbnails", {}) or {}).get(name, {}) or {}).get("url")
                    ),
                    "",
                ),
                occurred_at=snippet.get("publishedAt"),
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


class YouTubePublicProvider(CreatorProvider):
    """Public YouTube search fallback using yt-dlp (no API key required)."""

    name = "youtube_public_ytdlp"
    platform = "youtube"

    def __init__(self, max_results: int = 6) -> None:
        self.max_results = max(1, min(max_results, 12))

    def collect(self, conn: sqlite3.Connection, brand: dict, queries: list[str]) -> list[CreatorPost]:
        del conn
        try:
            import yt_dlp
        except ImportError as exc:
            raise FetchError("YouTube 公开搜索需要 yt-dlp；当前运行环境未安装。") from exc
        posts: dict[str, CreatorPost] = {}
        failures: list[str] = []
        successful_queries = 0
        options = {
            "quiet": True,
            "no_warnings": True,
            # Full metadata is needed for likes/comments. Nothing is downloaded;
            # yt-dlp only opens the public watch page for each bounded result.
            "extract_flat": False,
            "skip_download": True,
            "playlistend": self.max_results,
            "socket_timeout": 30,
            "retries": 1,
            "extractor_retries": 1,
            "dateafter": collection_since(brand).strftime("%Y%m%d"),
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                after = collection_since(brand).date().isoformat()
                for query in [str(q).strip() for q in queries if str(q).strip()][:8]:
                    try:
                        result = downloader.extract_info(
                            f"ytsearch{self.max_results}:{query} after:{after}", download=False
                        )
                        successful_queries += 1
                    except Exception as exc:
                        failures.append(str(exc)[:160])
                        continue
                    for item in (result or {}).get("entries") or []:
                        post = self._normalize(item, query)
                        if post and post.external_id not in posts:
                            posts[post.external_id] = post
        except Exception as exc:
            raise FetchError(f"YouTube 公开搜索失败：{str(exc)[:240]}") from exc
        if successful_queries == 0 and failures:
            raise FetchError(f"YouTube 公开搜索失败：{failures[0]}")
        return recent_creator_posts(list(posts.values()), brand)

    @staticmethod
    def _normalize(item: dict, query: str) -> CreatorPost | None:
        if not isinstance(item, dict):
            return None
        external_id = str(item.get("id") or "").strip()
        occurred_at = item.get("timestamp")
        if occurred_at:
            try:
                occurred_at = datetime.fromtimestamp(float(occurred_at), timezone.utc).replace(microsecond=0).isoformat()
            except (TypeError, ValueError, OSError, OverflowError):
                occurred_at = None
        if not occurred_at:
            upload_date = str(item.get("upload_date") or "").strip()
            if len(upload_date) == 8 and upload_date.isdigit():
                try:
                    occurred_at = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    occurred_at = None
        if not external_id or not occurred_at:
            return None
        channel_id = str(item.get("channel_id") or "").strip()
        handle = str(item.get("uploader_id") or item.get("channel") or "").strip()
        author_url = str(item.get("channel_url") or item.get("uploader_url") or "").strip()
        title = clean_text(item.get("title"))
        description = clean_text(item.get("description"))
        return CreatorPost(
            platform="youtube",
            external_id=external_id,
            url=str(item.get("webpage_url") or item.get("url") or f"https://www.youtube.com/watch?v={external_id}"),
            title=title,
            body=description or title,
            author=clean_text(item.get("channel") or item.get("uploader")),
            author_handle=channel_id or handle,
            author_url=author_url,
            thumbnail_url=str(item.get("thumbnail") or ""),
            occurred_at=occurred_at,
            views=_to_int(item.get("view_count")),
            likes=_to_int(item.get("like_count")),
            comments=_to_int(item.get("comment_count")),
            raw={"query": query, "collection_method": "youtube_public_ytdlp"},
        )
