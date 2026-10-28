"""Official X API v2 creator search provider.

X does not expose a stable logged-out search endpoint.  This provider uses the
official recent-search API with a bearer token and normalizes tweets into the
shared creator-post model.  The recent endpoint supports a rolling seven-day
window; the collector clamps older scheduling windows to that API limit.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from ...fetchers import FetchError, fetch_json
from ...util import clean_text
from .base import CreatorPost, CreatorProvider, collection_since, recent_creator_posts

_API = "https://api.x.com/2/tweets/search/recent"
_MAX_RESULTS = 100


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sum_int(*values) -> int | None:
    numbers = [_int(value) for value in values]
    present = [value for value in numbers if value is not None]
    return sum(present) if present else None


def _iso(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError):
        return clean_text(str(value))


class XApiCreatorProvider(CreatorProvider):
    """Search public X posts through the official API v2 recent endpoint."""

    name = "x_api_v2"
    platform = "x"

    def __init__(self, bearer_token: str, max_results: int = _MAX_RESULTS) -> None:
        self.bearer_token = bearer_token.strip()
        self.max_results = max(10, min(int(max_results or _MAX_RESULTS), _MAX_RESULTS))

    def collect(self, conn: sqlite3.Connection, brand: dict, queries: list[str]) -> list[CreatorPost]:
        del conn
        if not self.bearer_token:
            return []

        now = datetime.now(timezone.utc)
        # Recent search rejects start_time values older than seven days. Keep a
        # small safety margin for clock skew while preserving the scheduler's
        # exact daily/hourly window whenever possible.
        start = max(collection_since(brand), now - timedelta(days=6, hours=23, minutes=50))
        posts: dict[str, CreatorPost] = {}
        failures: list[str] = []
        successful_queries = 0
        for query in [str(item).strip() for item in queries if str(item).strip()][:8]:
            try:
                data = self._search(query, start_time=start, end_time=now)
                successful_queries += 1
            except FetchError as exc:
                failures.append(str(exc)[:240])
                continue
            includes = data.get("includes") if isinstance(data, dict) else {}
            users = {str(item.get("id")): item for item in (includes or {}).get("users", []) if isinstance(item, dict)}
            media = {str(item.get("media_key")): item for item in (includes or {}).get("media", []) if isinstance(item, dict)}
            for item in (data.get("data", []) if isinstance(data, dict) else []):
                post = self._normalize(item, users, media, query)
                if not post:
                    continue
                existing = posts.get(post.external_id)
                if existing is None:
                    posts[post.external_id] = post
                else:
                    matched = existing.raw.setdefault("matched_queries", [])
                    if query not in matched:
                        matched.append(query)
        if successful_queries == 0 and failures:
            raise FetchError(f"X API recent search 失败：{failures[0]}")
        return recent_creator_posts(list(posts.values()), brand)

    def _search(self, query: str, *, start_time: datetime, end_time: datetime) -> dict:
        # Keep the query human-entered and avoid retweet duplication.
        expression = query if "-is:retweet" in query else f"{query} -is:retweet"
        params = (
            f"query={quote_plus(expression[:512])}"
            f"&max_results={self.max_results}"
            f"&start_time={quote_plus(start_time.replace(microsecond=0).isoformat().replace('+00:00', 'Z'))}"
            f"&end_time={quote_plus(end_time.replace(microsecond=0).isoformat().replace('+00:00', 'Z'))}"
            "&tweet.fields=created_at,public_metrics,author_id,attachments,entities,lang"
            "&expansions=author_id,attachments.media_keys"
            "&user.fields=id,name,username,public_metrics,profile_image_url,verified"
            "&media.fields=media_key,type,url,preview_image_url,width,height"
        )
        try:
            result = fetch_json(
                f"{_API}?{params}",
                timeout=20,
                headers={"Authorization": f"Bearer {self.bearer_token}"},
            )
        except Exception as exc:
            if isinstance(exc, FetchError):
                raise
            raise FetchError(str(exc)) from exc
        if not isinstance(result, dict):
            raise FetchError("X API 返回格式异常。")
        return result

    @staticmethod
    def _normalize(item: dict, users: dict[str, dict], media: dict[str, dict], query: str) -> CreatorPost | None:
        if not isinstance(item, dict):
            return None
        external_id = clean_text(item.get("id"))
        if not external_id:
            return None
        author_id = clean_text(item.get("author_id"))
        author_data = users.get(author_id) or {}
        username = clean_text(author_data.get("username")).lstrip("@").lower()
        author = clean_text(author_data.get("name") or username or author_id)
        metrics = item.get("public_metrics") or {}
        attachments = item.get("attachments") or {}
        thumbnail = ""
        for key in attachments.get("media_keys", []) if isinstance(attachments, dict) else []:
            candidate = media.get(str(key)) or {}
            thumbnail = clean_text(candidate.get("preview_image_url") or candidate.get("url"))
            if thumbnail:
                break
        url = f"https://x.com/{username}/status/{external_id}" if username else f"https://x.com/i/web/status/{external_id}"
        return CreatorPost(
            platform="x",
            external_id=external_id,
            url=url,
            title=clean_text(item.get("text"))[:200],
            body=clean_text(item.get("text")),
            author=author,
            author_handle=username or author_id,
            author_url=f"https://x.com/{username}" if username else "",
            avatar_url=clean_text(author_data.get("profile_image_url")),
            thumbnail_url=thumbnail,
            occurred_at=_iso(item.get("created_at")),
            views=_int(metrics.get("impression_count")),
            likes=_int(metrics.get("like_count")),
            comments=_int(metrics.get("reply_count")),
            shares=_sum_int(metrics.get("retweet_count"), metrics.get("quote_count")),
            follower_count=_int((author_data.get("public_metrics") or {}).get("followers_count")),
            raw={
                "provider": "x_api_v2",
                "query": query,
                "matched_queries": [query],
                "tweet": item,
                "author": author_data,
            },
        )
