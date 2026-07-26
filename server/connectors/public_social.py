"""Best-effort public account collectors for Instagram and TikTok.

These adapters only read data exposed to logged-out visitors.  They do not use
account credentials, solve CAPTCHAs, or attempt to access private content.
Platform web contracts change frequently, so normalization is deliberately
defensive and failures are surfaced on the configured account link.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from ..fetchers import FetchError, fetch_text
from .creators.base import CreatorPost

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_INSTAGRAM_APP_ID = "936619743392459"
_MAX_POSTS = 12


def _number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _count(value: Any) -> int | None:
    if isinstance(value, dict):
        return _number(value.get("count"))
    return _number(value)


def _timestamp(value: Any) -> str | None:
    seconds = _number(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=0).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _first_thumbnail(item: dict) -> str:
    direct = str(item.get("thumbnail") or "").strip()
    if direct:
        return direct
    for thumbnail in item.get("thumbnails") or []:
        if isinstance(thumbnail, dict) and thumbnail.get("url"):
            return str(thumbnail["url"])
    return ""


def _instagram_caption(node: dict) -> str:
    edges = ((node.get("edge_media_to_caption") or {}).get("edges") or [])
    for edge in edges:
        text = (((edge or {}).get("node") or {}).get("text") or "")
        if str(text).strip():
            return str(text).strip()
    return str(node.get("accessibility_caption") or "").strip()


def _instagram_feed_caption(item: dict) -> str:
    caption = item.get("caption")
    if isinstance(caption, dict):
        return str(caption.get("text") or "").strip()
    return str(caption or "").strip()


def _instagram_feed_thumbnail(item: dict) -> str:
    versions = item.get("image_versions2")
    candidates = versions.get("candidates") if isinstance(versions, dict) else []
    for candidate in candidates or []:
        if isinstance(candidate, dict) and candidate.get("url"):
            return str(candidate["url"])
    return str(item.get("display_uri") or "").strip()


def _instagram_followers_from_og(description: str) -> int | None:
    match = re.search(r"([\d,.]+)\s*([KMB]?)\s+Followers\b", description or "", flags=re.I)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(value * multiplier[match.group(2).upper()])


def instagram_posts_from_user(user: dict, account_url: str, *, limit: int = _MAX_POSTS) -> list[CreatorPost]:
    """Normalize Instagram's logged-out web profile response."""
    if not isinstance(user, dict) or not user.get("username"):
        raise FetchError("Instagram 公开主页未返回账号数据。")
    if user.get("is_private"):
        raise FetchError("Instagram 账号为私密账号；自采仅支持公开账号。")

    username = str(user.get("username") or "").strip().lower()
    display_name = str(user.get("full_name") or username).strip()
    follower_count = _count(user.get("edge_followed_by"))
    profile_pic = str(user.get("profile_pic_url") or "")
    timeline = user.get("edge_owner_to_timeline_media") or {}
    edges = timeline.get("edges") if isinstance(timeline, dict) else []
    posts: list[CreatorPost] = []

    for edge in (edges or [])[: max(1, limit)]:
        node = (edge or {}).get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        shortcode = str(node.get("shortcode") or "").strip()
        external_id = str(node.get("id") or shortcode).strip()
        if not shortcode or not external_id:
            continue
        caption = _instagram_caption(node)
        is_video = bool(node.get("is_video"))
        product_type = str(node.get("product_type") or "")
        path = "reel" if product_type == "clips" else "p"
        thumbnail = str(node.get("thumbnail_src") or node.get("display_url") or "")
        likes = _count(node.get("edge_liked_by"))
        if likes is None:
            likes = _count(node.get("edge_media_preview_like"))
        comments = _count(node.get("edge_media_to_comment"))
        views = _number(node.get("video_play_count")) if is_video else None
        if views is None and is_video:
            views = _number(node.get("video_view_count"))
        if views == 0 and ((likes or 0) > 0 or (comments or 0) > 0):
            # Instagram sometimes returns a literal zero when the logged-out
            # response withholds plays; storing it as real data is misleading.
            views = None
        posts.append(CreatorPost(
            platform="instagram",
            external_id=external_id,
            url=f"https://www.instagram.com/{path}/{shortcode}/",
            title=(caption or f"Instagram post by @{username}")[:200],
            body=caption or f"Instagram post by @{username}",
            author=display_name,
            author_handle=username,
            author_url=account_url,
            avatar_url=thumbnail or profile_pic,
            occurred_at=_timestamp(node.get("taken_at_timestamp")),
            views=views,
            likes=likes,
            comments=comments,
            follower_count=follower_count,
            raw={
                "collection_method": "instagram_public_web",
                "shortcode": shortcode,
                "post_type": product_type or ("video" if is_video else "image"),
                "is_video": is_video,
                "is_verified": bool(user.get("is_verified")),
                "profile_pic_url": profile_pic,
            },
        ))
    return posts


def instagram_posts_from_feed(
    feed: dict,
    account_url: str,
    *,
    handle: str = "",
    follower_count: int | None = None,
    limit: int = _MAX_POSTS,
) -> list[CreatorPost]:
    """Normalize Instagram's public REST timeline fallback response."""
    if not isinstance(feed, dict):
        raise FetchError("Instagram 公开时间线未返回有效数据。")

    items = feed.get("items") or []
    user = feed.get("user") if isinstance(feed.get("user"), dict) else {}
    if not user and items and isinstance(items[0], dict):
        first_item_user = items[0].get("user")
        user = first_item_user if isinstance(first_item_user, dict) else {}
    if user.get("is_private"):
        raise FetchError("Instagram 账号为私密账号；自采仅支持公开账号。")

    username = str(user.get("username") or handle).strip().lstrip("@").lower()
    if not username:
        raise FetchError("Instagram 公开时间线未返回账号资料。")
    display_name = str(user.get("full_name") or username).strip()
    profile_pic = str(user.get("profile_pic_url") or "")
    if follower_count is None:
        follower_count = _number(user.get("follower_count"))
    posts: list[CreatorPost] = []

    for item in items[: max(1, limit)]:
        if not isinstance(item, dict):
            continue
        shortcode = str(item.get("code") or "").strip()
        external_id = str(item.get("pk") or item.get("id") or shortcode).strip()
        if not shortcode or not external_id:
            continue
        caption = _instagram_feed_caption(item)
        media_type = _number(item.get("media_type"))
        product_type = str(item.get("product_type") or "")
        is_video = media_type == 2 or product_type == "clips"
        path = "reel" if product_type == "clips" else "p"
        likes = _number(item.get("like_count"))
        comments = _number(item.get("comment_count"))
        shares = _number(item.get("reshare_count"))
        if shares is None:
            shares = _number(item.get("share_count"))
        views = None
        if is_video:
            for key in ("play_count", "view_count", "video_view_count"):
                views = _number(item.get(key))
                if views is not None:
                    break
        if views == 0 and ((likes or 0) > 0 or (comments or 0) > 0):
            views = None
        posts.append(CreatorPost(
            platform="instagram",
            external_id=external_id,
            url=f"https://www.instagram.com/{path}/{shortcode}/",
            title=(caption or f"Instagram post by @{username}")[:200],
            body=caption or f"Instagram post by @{username}",
            author=display_name,
            author_handle=username,
            author_url=account_url,
            avatar_url=_instagram_feed_thumbnail(item) or profile_pic,
            occurred_at=_timestamp(item.get("taken_at")),
            views=views,
            likes=likes,
            comments=comments,
            shares=shares,
            follower_count=follower_count,
            raw={
                "collection_method": "instagram_public_timeline",
                "shortcode": shortcode,
                "post_type": product_type or str(media_type or "unknown"),
                "is_video": is_video,
                "is_verified": bool(user.get("is_verified")),
                "profile_pic_url": profile_pic,
            },
        ))
    return posts


def instagram_posts_from_responses(
    result: dict,
    account_url: str,
    handle: str,
    *,
    limit: int = _MAX_POSTS,
) -> list[CreatorPost]:
    """Prefer the profile response and fall back to the public timeline."""
    profile_status = _number((result or {}).get("profile_status")) or 0
    user = (((result or {}).get("profile_data") or {}).get("data") or {}).get("user")
    if profile_status == 200 and isinstance(user, dict) and user.get("username"):
        return instagram_posts_from_user(user, account_url, limit=limit)

    feed_status = _number((result or {}).get("feed_status")) or 0
    feed = (result or {}).get("feed_data")
    if feed_status == 200 and isinstance(feed, dict):
        follower_count = _instagram_followers_from_og(str((result or {}).get("og_description") or ""))
        return instagram_posts_from_feed(
            feed,
            account_url,
            handle=handle,
            follower_count=follower_count,
            limit=limit,
        )

    raise FetchError(
        "Instagram 公开接口采集失败："
        f"profile HTTP {profile_status or 'unknown'}，"
        f"timeline HTTP {feed_status or 'unknown'}。"
    )


def collect_instagram_public(account_url: str, handle: str, *, limit: int = _MAX_POSTS) -> list[CreatorPost]:
    """Load one public Instagram profile through the logged-out web client."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError("Instagram 自采需要 Playwright/Chromium；当前运行环境未安装。") from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            context = None
            try:
                context = browser.new_context(user_agent=_BROWSER_USER_AGENT, locale="en-US")
                page = context.new_page()
                page.goto(
                    f"https://www.instagram.com/{handle}/",
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
                page.wait_for_timeout(800)
                result = page.evaluate(
                    """
                    async ({handle, appId, limit}) => {
                      const headers = {
                        "X-IG-App-ID": appId,
                        "X-Requested-With": "XMLHttpRequest"
                      };
                      const parseJson = async (response) => {
                        try { return await response.json(); } catch (_) { return null; }
                      };
                      const profileResponse = await fetch(
                        `/api/v1/users/web_profile_info/?username=${encodeURIComponent(handle)}`,
                        {
                          credentials: "same-origin",
                          headers
                        }
                      );
                      const profileData = await parseJson(profileResponse);
                      const profileUser = profileData?.data?.user;
                      let feedResponse = null;
                      let feedData = null;
                      if (profileResponse.status !== 200 || !profileUser?.username) {
                        feedResponse = await fetch(
                          `/api/v1/feed/user/${encodeURIComponent(handle)}/username/?count=${limit}`,
                          {credentials: "same-origin", headers}
                        );
                        feedData = await parseJson(feedResponse);
                        if (feedResponse.status !== 200) {
                          const html = document.documentElement?.innerHTML || "";
                          const profileId = html.match(/"profile_id":"?(\\d+)"?/)?.[1];
                          if (profileId) {
                            feedResponse = await fetch(
                              `/api/v1/feed/user/${profileId}/?count=${limit}`,
                              {credentials: "same-origin", headers}
                            );
                            feedData = await parseJson(feedResponse);
                          }
                        }
                      }
                      return {
                        profile_status: profileResponse.status,
                        profile_data: profileData,
                        feed_status: feedResponse?.status || 0,
                        feed_data: feedData,
                        og_description: document.querySelector('meta[property="og:description"]')?.content || ""
                      };
                    }
                    """,
                    {"handle": handle, "appId": _INSTAGRAM_APP_ID, "limit": max(1, limit)},
                )
            finally:
                try:
                    if context is not None:
                        context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass
    except PlaywrightTimeoutError as exc:
        raise FetchError("Instagram 公开主页请求 timed out。") from exc
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"Instagram 公开主页采集失败：{exc}") from exc

    return instagram_posts_from_responses(result, account_url, handle, limit=limit)


def _json_script(html: str, script_id: str) -> dict:
    pattern = rf'<script[^>]*\bid=["\']{re.escape(script_id)}["\'][^>]*>(.*?)</script>'
    match = re.search(pattern, html or "", flags=re.I | re.S)
    if not match:
        raise FetchError(f"页面缺少 {script_id} 结构化数据。")
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise FetchError(f"{script_id} 结构化数据无法解析。") from exc
    if not isinstance(value, dict):
        raise FetchError(f"{script_id} 结构化数据格式异常。")
    return value


def _tiktok_profile(account_url: str) -> dict:
    html = fetch_text(
        account_url,
        timeout=30,
        headers={
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    hydration = _json_script(html, "__UNIVERSAL_DATA_FOR_REHYDRATION__")
    scope = hydration.get("__DEFAULT_SCOPE__") or {}
    detail = scope.get("webapp.user-detail") if isinstance(scope, dict) else None
    user_info = (detail or {}).get("userInfo") if isinstance(detail, dict) else None
    if not isinstance(user_info, dict):
        raise FetchError("TikTok 公开主页未返回账号资料。")
    return user_info


def _tiktok_playlist(account_url: str, limit: int) -> list[dict]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise FetchError("TikTok 自采需要 yt-dlp；当前运行环境未安装。") from exc

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": max(1, limit),
        "socket_timeout": 35,
        "retries": 2,
        "extractor_retries": 2,
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            result = downloader.extract_info(account_url, download=False)
    except Exception as exc:
        raise FetchError(f"TikTok 公开账号采集失败：{exc}") from exc
    entries = (result or {}).get("entries") if isinstance(result, dict) else None
    return [item for item in (entries or []) if isinstance(item, dict)][: max(1, limit)]


def tiktok_posts_from_data(
    profile: dict,
    entries: list[dict],
    account_url: str,
    *,
    profile_error: str = "",
) -> list[CreatorPost]:
    """Normalize TikTok profile hydration and yt-dlp playlist entries."""
    user = profile.get("user") if isinstance(profile, dict) else {}
    stats = (profile.get("statsV2") or profile.get("stats")) if isinstance(profile, dict) else {}
    user = user if isinstance(user, dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    if user.get("privateAccount") or user.get("secret"):
        raise FetchError("TikTok 账号为私密账号；自采仅支持公开账号。")

    profile_handle = str(user.get("uniqueId") or "").strip().lower()
    profile_name = str(user.get("nickname") or profile_handle).strip()
    follower_count = _number(stats.get("followerCount"))
    profile_pic = str(user.get("avatarMedium") or user.get("avatarThumb") or "")
    posts: list[CreatorPost] = []

    for item in entries:
        external_id = str(item.get("id") or "").strip()
        if not external_id:
            continue
        handle = str(item.get("uploader") or profile_handle).strip().lstrip("@").lower()
        author = str(item.get("channel") or profile_name or handle).strip()
        description = str(item.get("description") or item.get("title") or "").strip()
        url = str(item.get("webpage_url") or item.get("url") or "").strip()
        if not url.startswith("http"):
            url = f"https://www.tiktok.com/@{handle}/video/{external_id}"
        posts.append(CreatorPost(
            platform="tiktok",
            external_id=external_id,
            url=url,
            title=(description or f"TikTok video by @{handle}")[:200],
            body=description or f"TikTok video by @{handle}",
            author=author,
            author_handle=handle,
            author_url=account_url,
            avatar_url=_first_thumbnail(item) or profile_pic,
            occurred_at=_timestamp(item.get("timestamp")),
            views=_number(item.get("view_count")),
            likes=_number(item.get("like_count")),
            comments=_number(item.get("comment_count")),
            shares=_number(item.get("repost_count")),
            follower_count=follower_count,
            raw={
                "collection_method": "tiktok_public_web_ytdlp",
                "duration": _number(item.get("duration")),
                "save_count": _number(item.get("save_count")),
                "is_verified": bool(user.get("verified")),
                "profile_pic_url": profile_pic,
                "profile_error": profile_error,
            },
        ))
    return posts


def collect_tiktok_public(account_url: str, handle: str, *, limit: int = _MAX_POSTS) -> list[CreatorPost]:
    """Load one public TikTok account without paid data providers."""
    profile: dict = {}
    profile_error = ""
    try:
        profile = _tiktok_profile(f"https://www.tiktok.com/@{handle}")
    except Exception as exc:
        # yt-dlp can still return post metrics even when profile hydration is
        # temporarily unavailable, so retain the partial result.
        profile_error = str(exc)[:300]

    entries = _tiktok_playlist(f"https://www.tiktok.com/@{handle}", limit)
    posts = tiktok_posts_from_data(profile, entries, account_url, profile_error=profile_error)
    stats = (profile.get("statsV2") or profile.get("stats")) if isinstance(profile, dict) else {}
    expected = _number((stats or {}).get("videoCount")) if isinstance(stats, dict) else None
    if not posts and expected:
        raise FetchError("TikTok 账号存在公开视频，但本次未解析到内容，可能被限流或页面结构已变化。")
    return posts
