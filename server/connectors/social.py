"""Official social-account monitoring driven by configured ``links`` rows.

The connector intentionally treats official-account monitoring separately from
creator keyword listening.  Records produced here always use ``channel=social``
and keep the originating link id so each configured account can expose its own
collection status.
"""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from ..fetchers import FetchError, fetch_bytes, fetch_page
from ..util import clean_text, today, utc_now
from .creators import creator_credential
from .creators.base import CreatorPost
from .creators.thirdparty import ThirdPartyCreatorProvider

SOURCE_ID = "social_accounts"
SUPPORTED_THIRD_PARTY = {"instagram", "tiktok", "x"}
UNSUPPORTED_PLATFORM_MESSAGE = {
    "facebook": "Facebook 公开主页采集尚未接入；Graph API 通常只允许已授权主页。",
    "linkedin": "LinkedIn 公司动态采集尚未接入；官方接口权限受限，不建议直接爬取竞品页面。",
    "pinterest": "Pinterest 账号内容采集尚未接入。",
}

_ATOM = "http://www.w3.org/2005/Atom"
_YT = "http://www.youtube.com/xml/schemas/2015"
_MEDIA = "http://search.yahoo.com/mrss/"


def _touch_link(conn: sqlite3.Connection, link_id: str, status: str, error: str = "") -> None:
    now = utc_now()
    conn.execute(
        "UPDATE links SET last_collect_at = ?, last_status = ?, last_error = ?, updated_at = ? WHERE id = ?",
        (now, status, (error or "")[:500], now, link_id),
    )


def _error_status(exc: Exception) -> tuple[str, str]:
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    if any(token in lowered for token in ("401", "unauthorized", "credential", "token", "api key")):
        return "needs_credential", message
    if any(token in lowered for token in ("403", "429", "forbidden", "blocked", "rate limit")):
        return "blocked", message
    if any(token in lowered for token in ("timeout", "timed out", "connection", "resolve", "ssl", "network")):
        return "network", message
    return "error", message


def _active_links(conn: sqlite3.Connection, brand_id: str, *, force: bool) -> list[dict]:
    due_clause = "" if force else "AND (last_collect_at IS NULL OR substr(last_collect_at, 1, 10) < ?)"
    params: tuple = (brand_id,) if force else (brand_id, today())
    rows = conn.execute(
        f"""
        SELECT * FROM links
        WHERE brand_id = ? AND dimension = 'marketing' AND channel = 'social'
              AND status = 'active' AND url IS NOT NULL AND url != ''
              {due_clause}
        ORDER BY created_at
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _account_handle(url: str) -> str:
    try:
        segments = [part for part in urlparse(url).path.split("/") if part]
    except ValueError:
        return ""
    if not segments:
        return ""
    if segments[0].lower() in {"channel", "user", "c", "company", "pages"} and len(segments) > 1:
        return segments[1].lstrip("@").lower()
    return segments[0].lstrip("@").lower()


def _youtube_channel_id(account_url: str) -> tuple[str, dict]:
    direct = re.search(r"/channel/(UC[\w-]{20,})", account_url, re.I)
    if direct:
        return direct.group(1), {}

    page = fetch_page(account_url, timeout=20)
    candidates = [
        (page.get("meta") or {}).get("og:url", ""),
        (page.get("meta") or {}).get("al:web:url", ""),
        page.get("final_url", ""),
    ]
    for candidate in candidates:
        match = re.search(r"/channel/(UC[\w-]{20,})", candidate or "", re.I)
        if match:
            return match.group(1), page

    html = page.get("html") or ""
    for pattern in (r'"externalId":"(UC[\w-]{20,})"', r'channel_id=(UC[\w-]{20,})'):
        match = re.search(pattern, html)
        if match:
            return match.group(1), page
    raise FetchError("无法从 YouTube 账号页解析 channel id，请确认链接指向公开频道主页。")


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _youtube_posts(account_url: str, cached_channel_id: str = "") -> tuple[list[CreatorPost], str]:
    if re.fullmatch(r"UC[\w-]{20,}", cached_channel_id or "", re.I):
        channel_id, page = cached_channel_id, {}
    else:
        channel_id, page = _youtube_channel_id(account_url)
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    raw = fetch_bytes(feed_url, accept="application/atom+xml,application/xml", timeout=20)
    root = ET.fromstring(raw)
    ns = {"a": _ATOM, "yt": _YT, "m": _MEDIA}
    posts: list[CreatorPost] = []
    for entry in root.findall("a:entry", ns):
        video_id = clean_text(entry.findtext("yt:videoId", default="", namespaces=ns))
        if not video_id:
            continue
        link_node = entry.find("a:link", ns)
        media_group = entry.find("m:group", ns)
        thumbnail = media_group.find("m:thumbnail", ns) if media_group is not None else None
        community = media_group.find("m:community", ns) if media_group is not None else None
        statistics = community.find("m:statistics", ns) if community is not None else None
        rating = community.find("m:starRating", ns) if community is not None else None
        title = clean_text(entry.findtext("a:title", default="", namespaces=ns))
        description = clean_text(
            media_group.findtext("m:description", default="", namespaces=ns)
            if media_group is not None else ""
        )
        author = clean_text(entry.findtext("a:author/a:name", default="", namespaces=ns))
        author_url = clean_text(entry.findtext("a:author/a:uri", default="", namespaces=ns))
        posts.append(CreatorPost(
            platform="youtube",
            external_id=video_id,
            url=(link_node.get("href") if link_node is not None else "") or f"https://www.youtube.com/watch?v={video_id}",
            title=title,
            body=description or title,
            author=author or clean_text(page.get("title")),
            author_handle=channel_id,
            author_url=author_url or account_url,
            avatar_url=(thumbnail.get("url") if thumbnail is not None else "") or "",
            occurred_at=entry.findtext("a:published", default="", namespaces=ns) or utc_now(),
            views=_int(statistics.get("views") if statistics is not None else None),
            raw={
                "account_url": account_url,
                "channel_id": channel_id,
                "collection_method": "youtube_atom_feed",
                "average_rating": rating.get("average") if rating is not None else None,
                "rating_count": _int(rating.get("count") if rating is not None else None),
            },
        ))
    return posts, channel_id


def _link_config(link: dict) -> dict:
    try:
        value = json.loads(link.get("config_json") or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _cache_youtube_channel_id(conn: sqlite3.Connection, link: dict, channel_id: str) -> None:
    config = _link_config(link)
    if config.get("youtube_channel_id") == channel_id:
        return
    config["youtube_channel_id"] = channel_id
    conn.execute(
        "UPDATE links SET config_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config, ensure_ascii=False), utc_now(), link["id"]),
    )


def _third_party_posts(conn: sqlite3.Connection, platform: str, account_url: str) -> list[CreatorPost]:
    token = creator_credential(conn, "ensembledata_token")
    if not token:
        raise PermissionError("缺少 ensembledata_token；Instagram / TikTok / X 官方账号内容需第三方数据源。")
    handle = _account_handle(account_url)
    if not handle:
        raise ValueError("无法从社媒链接解析账号 handle。")
    provider = ThirdPartyCreatorProvider(platform, token)
    posts = provider.collect(conn, {}, [handle]) or []
    return [post for post in posts if (post.author_handle or "").lstrip("@").lower() == handle]


def _payload(brand: dict, link: dict, post: CreatorPost) -> dict:
    return {
        "source_id": SOURCE_ID,
        "brand_id": brand.get("id"),
        "link_id": link.get("id"),
        "external_id": f"{brand.get('id')}:{link.get('id')}:{post.platform}:{post.external_id}",
        "data_type": "social_post",
        "dimension": "marketing",
        "channel": "social",
        "platform": post.platform,
        "title": post.title or post.author,
        "author": post.author or post.author_handle,
        "body": post.body or post.title or post.author or "(no caption)",
        "url": post.url,
        "occurred_at": post.occurred_at,
        "metrics": {
            "views": post.views,
            "likes": post.likes,
            "comments": post.comments,
            "shares": post.shares,
            "engagement": post.engagement(),
            "engagement_rate": post.engagement_rate(),
            "follower_count": post.follower_count,
            "author_handle": post.author_handle,
            "author_url": post.author_url or link.get("url"),
            "thumbnail_url": post.avatar_url,
            "rating_count": (post.raw or {}).get("rating_count"),
        },
        "raw": {**(post.raw or {}), "configured_account_url": link.get("url")},
    }


def collect_social_accounts(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    """Collect new posts for all due official social-account links of one brand."""
    payloads: list[dict] = []
    force = bool(brand.get("_force_collect"))
    for link in _active_links(conn, brand.get("id") or "", force=force):
        platform = (link.get("platform") or "").strip().lower()
        try:
            if platform == "youtube":
                config = _link_config(link)
                posts, channel_id = _youtube_posts(link["url"], config.get("youtube_channel_id") or "")
                _cache_youtube_channel_id(conn, link, channel_id)
            elif platform in SUPPORTED_THIRD_PARTY:
                posts = _third_party_posts(conn, platform, link["url"])
            else:
                message = UNSUPPORTED_PLATFORM_MESSAGE.get(
                    platform,
                    f"{platform or '该平台'} 的官方账号采集适配器尚未接入。",
                )
                _touch_link(conn, link["id"], "unsupported", message)
                continue

            payloads.extend(_payload(brand, link, post) for post in posts)
            if posts:
                _touch_link(conn, link["id"], "ok")
            else:
                _touch_link(conn, link["id"], "empty", "本次未发现该官方账号的公开内容或新内容。")
        except PermissionError as exc:
            _touch_link(conn, link["id"], "needs_credential", str(exc))
        except Exception as exc:
            status, message = _error_status(exc)
            _touch_link(conn, link["id"], status, message)
    return payloads
