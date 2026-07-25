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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree as ET

from ..fetchers import FetchError, fetch_bytes, fetch_json_post, fetch_page
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
_YT_WEB_METRICS_LIMIT = 12


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


def _yt_text(value) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if not isinstance(value, dict):
        return ""
    direct = value.get("content") or value.get("simpleText")
    if direct:
        return clean_text(str(direct))
    return clean_text(
        "".join(str(run.get("text") or "") for run in (value.get("runs") or []) if isinstance(run, dict))
    )


def _yt_initial_data(html: str) -> dict:
    """Extract YouTube's embedded ``ytInitialData`` without a brittle regex."""
    marker = "ytInitialData ="
    marker_at = html.find(marker)
    if marker_at < 0:
        raise FetchError("YouTube 频道页缺少结构化视频数据。")
    start = html.find("{", marker_at + len(marker))
    if start < 0:
        raise FetchError("YouTube 频道页结构化数据格式异常。")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(html)):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(html[start:index + 1])
                except json.JSONDecodeError as exc:
                    raise FetchError("YouTube 频道页结构化数据无法解析。") from exc
                if isinstance(value, dict):
                    return value
                break
    raise FetchError("YouTube 频道页结构化数据不完整。")


def _yt_renderers(value, renderer_name: str):
    if isinstance(value, dict):
        renderer = value.get(renderer_name)
        if isinstance(renderer, dict):
            yield renderer
        for child in value.values():
            yield from _yt_renderers(child, renderer_name)
    elif isinstance(value, list):
        for child in value:
            yield from _yt_renderers(child, renderer_name)


def _compact_count(label: str) -> int | None:
    normalized = clean_text(label).lower().replace(",", "")
    if not normalized or any(token in normalized for token in ("no views", "无观看", "沒有觀看")):
        return 0 if normalized else None
    match = re.search(r"([\d.]+)\s*([kmb]|万|萬|亿|億)?", normalized)
    if not match:
        return None
    multiplier = {
        "k": 1_000,
        "m": 1_000_000,
        "b": 1_000_000_000,
        "万": 10_000,
        "萬": 10_000,
        "亿": 100_000_000,
        "億": 100_000_000,
    }.get(match.group(2) or "", 1)
    try:
        return int(float(match.group(1)) * multiplier)
    except ValueError:
        return None


def _relative_timestamp(label: str) -> str:
    normalized = clean_text(label).lower()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if not normalized:
        return now.isoformat()
    if normalized in {"today", "just now", "今天", "刚刚", "剛剛"}:
        return now.isoformat()
    if normalized in {"yesterday", "昨天"}:
        return (now - timedelta(days=1)).isoformat()

    match = re.search(
        r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago",
        normalized,
    ) or re.search(r"(\d+)\s*(秒钟?|分鐘?|分钟?|小时|小時|天|周|週|个月|個月|月|年)前", normalized)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("second") or unit.startswith("秒"):
            delta = timedelta(seconds=amount)
        elif unit.startswith("minute") or unit.startswith(("分钟", "分鐘")):
            delta = timedelta(minutes=amount)
        elif unit.startswith("hour") or unit.startswith(("小时", "小時")):
            delta = timedelta(hours=amount)
        elif unit.startswith("day") or unit == "天":
            delta = timedelta(days=amount)
        elif unit.startswith("week") or unit in {"周", "週"}:
            delta = timedelta(weeks=amount)
        elif unit.startswith("month") or unit in {"个月", "個月", "月"}:
            delta = timedelta(days=amount * 30)
        else:
            delta = timedelta(days=amount * 365)
        return (now - delta).isoformat()
    return now.isoformat()


def _youtube_web_config(html: str) -> dict | None:
    def match(pattern: str) -> str:
        found = re.search(pattern, html)
        return found.group(1) if found else ""

    api_key = match(r'"INNERTUBE_API_KEY":"([^"]+)"')
    client_version = match(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"')
    visitor_data = match(r'"VISITOR_DATA":"([^"]+)"')
    if not api_key or not client_version:
        return None
    client = {
        "clientName": "WEB",
        "clientVersion": client_version,
        "hl": "en",
        "gl": "US",
    }
    if visitor_data:
        client["visitorData"] = visitor_data
    return {"api_key": api_key, "client_version": client_version, "context": {"client": client}}


def _youtube_next(config: dict, payload: dict) -> dict:
    url = (
        "https://www.youtube.com/youtubei/v1/next?prettyPrint=false"
        f"&key={quote_plus(config['api_key'])}"
    )
    data = fetch_json_post(
        url,
        {"context": config["context"], **payload},
        timeout=16,
        headers={
            "Origin": "https://www.youtube.com",
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": config["client_version"],
        },
    )
    if not isinstance(data, dict):
        raise FetchError("YouTube 视频指标接口返回格式异常。")
    return data


def _button_metric(value, icon_name: str) -> int | None:
    if isinstance(value, dict):
        if value.get("iconName") == icon_name:
            count = _compact_count(_yt_text(value.get("title")))
            if count is not None:
                return count
        for child in value.values():
            count = _button_metric(child, icon_name)
            if count is not None:
                return count
    elif isinstance(value, list):
        for child in value:
            count = _button_metric(child, icon_name)
            if count is not None:
                return count
    return None


def _comment_continuation(data: dict) -> str:
    sections = [
        section
        for section in _yt_renderers(data, "itemSectionRenderer")
        if section.get("targetId") == "comments-section"
    ]
    for section in sections:
        for item in section.get("contents", []):
            token = (
                ((item.get("continuationItemRenderer") or {}).get("continuationEndpoint") or {})
                .get("continuationCommand", {})
                .get("token", "")
            )
            if token:
                return token
    return ""


def _comment_count(data: dict) -> int | None:
    for header in _yt_renderers(data, "commentsHeaderRenderer"):
        count = _compact_count(_yt_text(header.get("countText")))
        if count is not None:
            return count
    for header in _yt_renderers(data, "commentsEntryPointHeaderRenderer"):
        count = _compact_count(_yt_text(header.get("commentCount")))
        if count is not None:
            return count
    return None


def _youtube_web_video_metrics(config: dict, video_id: str) -> dict:
    initial = _youtube_next(config, {"videoId": video_id})
    views = None
    for renderer in _yt_renderers(initial, "videoViewCountRenderer"):
        views = _compact_count(_yt_text(renderer.get("viewCount") or renderer.get("shortViewCount")))
        if views is not None:
            break
    likes = _button_metric(initial, "LIKE")
    comments = _comment_count(initial)
    if comments is None:
        continuation = _comment_continuation(initial)
        if continuation:
            comments = _comment_count(_youtube_next(config, {"continuation": continuation}))
    return {"views": views, "likes": likes, "comments": comments}


def _enrich_youtube_web_metrics(posts: list[CreatorPost], html: str) -> None:
    config = _youtube_web_config(html)
    targets = posts[:_YT_WEB_METRICS_LIMIT]
    if not config or not targets:
        return

    def collect(post: CreatorPost) -> dict:
        try:
            return _youtube_web_video_metrics(config, post.external_id)
        except Exception as exc:
            return {"error": str(exc)[:300]}

    workers = min(4, len(targets))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(collect, targets))
    collected_at = utc_now()
    for post, metrics in zip(targets, results):
        if metrics.get("views") is not None:
            post.views = metrics["views"]
        if metrics.get("likes") is not None:
            post.likes = metrics["likes"]
        if metrics.get("comments") is not None:
            post.comments = metrics["comments"]
        post.raw["metrics_collection_method"] = "youtube_web_next"
        post.raw["metrics_collected_at"] = collected_at
        if metrics.get("error"):
            post.raw["metrics_error"] = metrics["error"]


def _youtube_page_posts(channel_id: str, account_url: str, feed_error: Exception) -> list[CreatorPost]:
    page = fetch_page(f"https://www.youtube.com/channel/{channel_id}/videos", timeout=25)
    data = _yt_initial_data(page.get("html") or "")
    author = clean_text(page.get("title") or "")
    author = re.sub(r"\s*[-–—]\s*YouTube\s*$", "", author, flags=re.I)
    for channel in _yt_renderers(data, "channelMetadataRenderer"):
        author = _yt_text(channel.get("title")) or author
        if author:
            break

    posts: list[CreatorPost] = []
    seen: set[str] = set()

    # Current desktop channel pages use lockupViewModel (July 2026).
    for lockup in _yt_renderers(data, "lockupViewModel"):
        video_id = clean_text(lockup.get("contentId"))
        if lockup.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO" or not video_id or video_id in seen:
            continue
        metadata = ((lockup.get("metadata") or {}).get("lockupMetadataViewModel") or {})
        title = _yt_text(metadata.get("title"))
        labels: list[str] = []
        rows = ((metadata.get("metadata") or {}).get("contentMetadataViewModel") or {}).get("metadataRows", [])
        for row in rows:
            for part in (row.get("metadataParts", []) if isinstance(row, dict) else []):
                label = _yt_text((part or {}).get("text"))
                if label:
                    labels.append(label)
        view_label = next(
            (
                label
                for label in labels
                if "view" in label.lower()
                or any(token in label for token in ("观看", "觀看", "收看", "播放"))
            ),
            "",
        )
        published_label = next((label for label in labels if label != view_label), "")
        sources = (((lockup.get("contentImage") or {}).get("thumbnailViewModel") or {}).get("image") or {}).get(
            "sources", []
        )
        thumbnail = next(
            (item.get("url") for item in reversed(sources) if isinstance(item, dict) and item.get("url")),
            "",
        )
        seen.add(video_id)
        posts.append(CreatorPost(
            platform="youtube",
            external_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            title=title,
            body=title,
            author=author,
            author_handle=channel_id,
            author_url=account_url,
            avatar_url=thumbnail,
            occurred_at=_relative_timestamp(published_label),
            views=_compact_count(view_label),
            raw={
                "account_url": account_url,
                "channel_id": channel_id,
                "collection_method": "youtube_channel_page",
                "feed_error": str(feed_error)[:300],
                "published_label": published_label,
                "view_count_label": view_label,
            },
        ))

    # Keep compatibility with YouTube's older videoRenderer response shape.
    for renderer in _yt_renderers(data, "videoRenderer"):
        video_id = clean_text(renderer.get("videoId"))
        if not video_id or video_id in seen:
            continue
        title = _yt_text(renderer.get("title"))
        thumbnails = (renderer.get("thumbnail") or {}).get("thumbnails", [])
        thumbnail = next(
            (item.get("url") for item in reversed(thumbnails) if isinstance(item, dict) and item.get("url")),
            "",
        )
        view_label = _yt_text(renderer.get("viewCountText") or renderer.get("shortViewCountText"))
        published_label = _yt_text(renderer.get("publishedTimeText"))
        seen.add(video_id)
        posts.append(CreatorPost(
            platform="youtube",
            external_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            title=title,
            body=_yt_text(renderer.get("descriptionSnippet")) or title,
            author=author,
            author_handle=channel_id,
            author_url=account_url,
            avatar_url=thumbnail,
            occurred_at=_relative_timestamp(published_label),
            views=_compact_count(view_label),
            raw={
                "account_url": account_url,
                "channel_id": channel_id,
                "collection_method": "youtube_channel_page",
                "feed_error": str(feed_error)[:300],
                "published_label": published_label,
                "view_count_label": view_label,
            },
        ))

    if not posts:
        raise FetchError(f"YouTube Atom feed 失败，频道页也未解析到视频：{feed_error}")
    _enrich_youtube_web_metrics(posts, page.get("html") or "")
    return posts


def _youtube_posts(account_url: str, cached_channel_id: str = "") -> tuple[list[CreatorPost], str]:
    if re.fullmatch(r"UC[\w-]{20,}", cached_channel_id or "", re.I):
        channel_id, page = cached_channel_id, {}
    else:
        channel_id, page = _youtube_channel_id(account_url)
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        raw = fetch_bytes(feed_url, accept="application/atom+xml,application/xml", timeout=20)
        root = ET.fromstring(raw)
    except (FetchError, ET.ParseError) as exc:
        return _youtube_page_posts(channel_id, account_url, exc), channel_id
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
    if not posts:
        return _youtube_page_posts(channel_id, account_url, FetchError("Atom feed 未返回视频")), channel_id
    try:
        metrics_page = fetch_page(f"https://www.youtube.com/channel/{channel_id}/videos", timeout=25)
        _enrich_youtube_web_metrics(posts, metrics_page.get("html") or "")
    except Exception as exc:
        for post in posts[:_YT_WEB_METRICS_LIMIT]:
            post.raw["metrics_error"] = str(exc)[:300]
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
    has_engagement = any(value is not None for value in (post.likes, post.comments, post.shares))
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
            "engagement": post.engagement() if has_engagement else None,
            "engagement_rate": post.engagement_rate() if has_engagement else None,
            "follower_count": post.follower_count,
            "author_handle": post.author_handle,
            "author_url": post.author_url or link.get("url"),
            "thumbnail_url": post.avatar_url,
            "rating_count": (post.raw or {}).get("rating_count"),
        },
        "raw": {**(post.raw or {}), "configured_account_url": link.get("url")},
    }


def _json_object(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _refresh_existing_social_records(conn: sqlite3.Connection, payloads: list[dict]) -> list[dict]:
    new_payloads: list[dict] = []
    for payload in payloads:
        existing = conn.execute(
            "SELECT id, metrics_json, raw_json FROM records WHERE source_id = ? AND external_id = ?",
            (payload.get("source_id"), payload.get("external_id")),
        ).fetchone()
        if not existing:
            new_payloads.append(payload)
            continue

        metrics = _json_object(existing["metrics_json"])
        metrics.update({key: value for key, value in (payload.get("metrics") or {}).items() if value is not None})
        raw = {**_json_object(existing["raw_json"]), **(payload.get("raw") or {})}
        conn.execute(
            """
            UPDATE records
            SET title = ?, author = ?, body = ?, url = ?, metrics_json = ?, raw_json = ?
            WHERE id = ?
            """,
            (
                payload.get("title") or "",
                payload.get("author") or "",
                payload.get("body") or "",
                payload.get("url") or "",
                json.dumps(metrics, ensure_ascii=False),
                json.dumps(raw, ensure_ascii=False),
                existing["id"],
            ),
        )
    return new_payloads


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
    return _refresh_existing_social_records(conn, payloads)
