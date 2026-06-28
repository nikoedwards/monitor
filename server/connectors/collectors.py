"""Concrete collector functions for real data sources."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlencode

from ..config import CREDENTIALS, USER_AGENT
from ..fetchers import FetchError, fetch_bytes, fetch_json, fetch_page, parse_rss
from ..nlp import classify_media_property, detect_pr_themes
from ..util import (
    clean_text,
    host_key,
    html_fragment_to_text,
    parse_rss_datetime,
    root_url,
    today,
    utc_now,
)
from .publications import enrich_publication, estimate_ave


def brand_queries(brand: dict) -> list[str]:
    queries: list[str] = []
    try:
        keywords = json.loads(brand.get("monitoring_keywords_json") or "[]")
    except (TypeError, ValueError):
        keywords = []
    queries.extend(k for k in keywords if isinstance(k, str) and k.strip())
    if brand.get("name"):
        queries.append(brand["name"])
    seen, unique = set(), []
    for q in queries:
        key = q.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(q.strip())
    # Each query is an independent search bucket; brand-name/variant queries
    # should all run to maximize recall. Duplicate articles across variants are
    # deduped downstream by (source_id, external_id), so extra queries only add
    # coverage. Cap kept generous to avoid unbounded fetch fan-out.
    return unique[:10]


def community_links(conn: sqlite3.Connection, brand_id: str, *, platform: str | None = None) -> list[dict]:
    """Active community links configured for a brand (links table, channel=community)."""
    rows = conn.execute(
        "SELECT * FROM links WHERE brand_id = ? AND channel = 'community' "
        "AND status = 'active' AND url IS NOT NULL AND url != ''",
        (brand_id,),
    ).fetchall()
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        if platform and (item.get("platform") or "").lower() != platform:
            continue
        result.append(item)
    return result


def _touch_link(conn: sqlite3.Connection, link_id, *, status: str, error: str = "") -> None:
    """Record per-link collection status so the brand config UI shows crawl activity.

    `status` is one of: ok | empty | blocked | needs_credential | network | error.
    The brand-config status chip maps these to a human reason + remediation hint.
    """
    if not link_id:
        return
    conn.execute(
        "UPDATE links SET last_collect_at = ?, last_status = ?, last_error = ?, updated_at = ? WHERE id = ?",
        (utc_now(), status, (error or "")[:500], utc_now(), link_id),
    )


def _classify_error(exc: Exception, *, default: str = "error") -> tuple[str, str]:
    """Map a fetch/parse exception to a (status, message) the UI can explain."""
    msg = str(exc) or exc.__class__.__name__
    low = msg.lower()
    if any(k in low for k in ("403", "429", "forbidden", "blocked", "too many request", "rate limit")):
        return "blocked", msg
    if any(k in low for k in ("401", "unauthorized", "credential", "token", "api key", "forbidden access")):
        return "needs_credential", msg
    if any(k in low for k in ("timed out", "timeout", "connection", "resolve", "ssl", "refused", "unreachable", "network", "eof occurred")):
        return "network", msg
    return default, msg


# ---------------------------------------------------------------- Google News
def _google_news_url(query: str, region: str = "US", language: str = "en-US") -> str:
    lang_code = language.split("-", 1)[0] or "en"
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl={quote_plus(language)}&gl={quote_plus(region)}"
        f"&ceid={quote_plus(f'{region}:{lang_code}')}"
    )


def collect_google_news(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    payloads: list[dict] = []
    for query in brand_queries(brand):
        try:
            raw = fetch_bytes(_google_news_url(query), accept="application/rss+xml,application/xml", timeout=18)
        except FetchError:
            continue
        for item in parse_rss(raw, limit=25):
            url = item.get("url")
            if not url:
                continue
            title = item.get("title") or "Untitled media mention"
            body = item.get("description") or title
            publication = item.get("source_name") or host_key(url) or "Unknown publication"
            source_url = item.get("source_url") or ""
            pub_domain = host_key(source_url)
            coverage_type, confidence, _ = classify_media_property(title, body, publication, url)
            pub = enrich_publication(conn, publication, pub_domain)
            reach = pub["est_monthly_traffic"]
            ave = estimate_ave(reach, coverage_type)
            payloads.append({
                "source_id": "google_news",
                "brand_id": brand.get("id"),
                "external_id": f"{brand.get('id')}:{item.get('guid') or url}",
                "data_type": "media_mention",
                "dimension": "marketing",
                "channel": "media",
                "platform": pub["name"] or publication,
                "title": title,
                "author": pub["name"] or publication,
                "body": body,
                "url": url,
                "occurred_at": item.get("published_at"),
                "topics": detect_pr_themes(f"{title} {body}"),
                "metrics": {
                    "coverage_type": coverage_type,
                    "confidence": confidence,
                    "estimated_reach": reach,
                    "monthly_traffic": reach,
                    "media_tier": pub["tier"],
                    "authority": pub["authority"],
                    "country": pub["country"],
                    "language": pub["language"],
                    "ave": ave,
                    "publication_domain": pub_domain,
                    "publication_url": source_url,
                    "publication_icon": pub["icon_url"],
                },
                "raw": {"query": query, "publication": publication, "source_url": source_url},
            })
    return payloads


# ---------------------------------------------------------------- Reddit
REDDIT_WEB = "https://www.reddit.com"
REDDIT_OAUTH = "https://oauth.reddit.com"


def _reddit_user_agent() -> str:
    return CREDENTIALS.get("reddit_user_agent") or USER_AGENT


def _reddit_get(path: str, params: dict) -> dict | list:
    """Reddit JSON request with descriptive UA, optional OAuth, and backoff retries.

    Reddit throttles anonymous `*.rss` aggressively; the JSON endpoints are more
    reliable when given a unique User-Agent. A bearer token (script app) bumps
    rate limits considerably and is used automatically when configured.
    """
    token = CREDENTIALS.get("reddit_bearer_token")
    base = REDDIT_OAUTH if token else REDDIT_WEB
    headers = {"User-Agent": _reddit_user_agent()}
    if token:
        headers["Authorization"] = f"bearer {token}"
    url = f"{base}{path}?{urlencode(params)}"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return fetch_json(url, timeout=16, headers=headers)
        except FetchError as exc:
            last_err = exc
            time.sleep(1.0 * (attempt + 1))
    raise last_err or FetchError("Reddit request failed")


def _reddit_time(created_utc) -> str | None:
    try:
        return (
            datetime.fromtimestamp(float(created_utc), tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _reddit_post_id(url: str) -> str:
    match = re.search(r"/comments/([a-z0-9]+)", url or "", re.I)
    return match.group(1) if match else ""


def _reddit_listing_payloads(
    data, brand: dict, query: str, subreddit: str | None = None, scope: str = "site",
) -> list[dict]:
    payloads: list[dict] = []
    if not isinstance(data, dict):
        return payloads
    children = ((data.get("data") or {}).get("children")) or []
    for child in children:
        item = (child or {}).get("data") or {}
        if not item:
            continue
        permalink = item.get("permalink")
        link = f"{REDDIT_WEB}{permalink}" if permalink else item.get("url")
        if not link:
            continue
        post_id = item.get("id") or _reddit_post_id(link)
        title = clean_text(item.get("title")) or "Reddit post"
        body = html_fragment_to_text(item.get("selftext")) or title
        sub = item.get("subreddit") or subreddit
        payloads.append({
            "source_id": "reddit_search",
            "brand_id": brand.get("id"),
            "external_id": f"{brand.get('id')}:reddit:{post_id or link}",
            "data_type": "community_post",
            "dimension": "marketing",
            "channel": "community",
            "platform": "reddit",
            "title": title,
            "author": clean_text(item.get("author")),
            "body": body,
            "url": link,
            "occurred_at": _reddit_time(item.get("created_utc")),
            "metrics": {
                "score": item.get("score"),
                "num_comments": item.get("num_comments"),
                "subreddit": sub,
                "scope": scope,
            },
            "raw": {"query": query, "subreddit": sub, "scope": scope},
        })
    return payloads


def _reddit_rss_payloads(brand: dict, query: str) -> list[dict]:
    """Last-resort anonymous RSS fallback when the JSON endpoints are blocked."""
    url = f"{REDDIT_WEB}/search.rss?q={quote_plus(query)}&sort=new&limit=25"
    try:
        raw = fetch_bytes(url, accept="application/rss+xml,application/atom+xml", timeout=16)
        items = parse_rss(raw, limit=25)
    except FetchError:
        return []
    except Exception:
        return []
    payloads: list[dict] = []
    for item in items:
        link = item.get("url")
        if not link:
            continue
        post_id = _reddit_post_id(link)
        title = item.get("title") or "Reddit post"
        payloads.append({
            "source_id": "reddit_search",
            "brand_id": brand.get("id"),
            "external_id": f"{brand.get('id')}:reddit:{post_id or item.get('guid') or link}",
            "data_type": "community_post",
            "dimension": "marketing",
            "channel": "community",
            "platform": "reddit",
            "title": title,
            "body": item.get("description") or title,
            "url": link,
            "occurred_at": item.get("published_at"),
            "metrics": {"scope": "site"},
            "raw": {"query": query, "scope": "site"},
        })
    return payloads


def _reddit_subreddit_rss_payloads(brand: dict, subreddit: str, scope: str) -> list[dict]:
    """Anonymous per-subreddit RSS fallback used when the JSON endpoints are 403/blocked."""
    url = f"{REDDIT_WEB}/r/{subreddit}/new.rss?limit=25"
    try:
        raw = fetch_bytes(url, accept="application/rss+xml,application/atom+xml", timeout=16)
        items = parse_rss(raw, limit=25)
    except Exception:
        return []
    payloads: list[dict] = []
    for item in items:
        link = item.get("url")
        if not link:
            continue
        post_id = _reddit_post_id(link)
        payloads.append({
            "source_id": "reddit_search",
            "brand_id": brand.get("id"),
            "external_id": f"{brand.get('id')}:reddit:{post_id or item.get('guid') or link}",
            "data_type": "community_post",
            "dimension": "marketing",
            "channel": "community",
            "platform": "reddit",
            "title": item.get("title") or "Reddit post",
            "body": item.get("description") or item.get("title") or "",
            "url": link,
            "occurred_at": item.get("published_at"),
            "metrics": {"subreddit": subreddit, "scope": scope},
            "raw": {"subreddit": subreddit, "scope": scope, "via": "rss"},
        })
    return payloads


def _extract_subreddit(value: str) -> str:
    """Pull a subreddit name from a configured link (`/r/anker`, `r/anker`, or `anker`)."""
    text = (value or "").strip()
    match = re.search(r"/r/([A-Za-z0-9_]+)", text)
    if match:
        return match.group(1)
    low = text.lstrip("/")
    if low.lower().startswith("r/"):
        return low[2:].split("/")[0]
    if text and "/" not in text and "." not in text and " " not in text:
        return text
    return ""


def collect_reddit(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    payloads: list[dict] = []
    queries = brand_queries(brand)
    # 1) Keyword search across all of Reddit.
    for query in queries:
        try:
            data = _reddit_get("/search.json", {"q": query, "sort": "new", "limit": 25, "type": "link"})
            payloads.extend(_reddit_listing_payloads(data, brand, query))
        except (FetchError, ValueError):
            payloads.extend(_reddit_rss_payloads(brand, query))
    # 2) Brand-configured subreddits (links table, platform=reddit). One brand can
    #    track several subreddits; each configured link is crawled independently.
    for link in community_links(conn, brand.get("id"), platform="reddit"):
        link_id = link.get("id")
        subreddit = _extract_subreddit(link.get("url"))
        if not subreddit:
            _touch_link(conn, link_id, status="error", error="无法解析 subreddit 名称，请填写如 r/anker 或完整链接")
            continue
        scope = f"subreddit:{subreddit}"
        got = 0
        last_exc: Exception | None = None
        try:
            data = _reddit_get(f"/r/{subreddit}/new.json", {"limit": 25})
            items = _reddit_listing_payloads(data, brand, "", subreddit=subreddit, scope=scope)
            payloads.extend(items)
            got += len(items)
        except (FetchError, ValueError) as exc:
            last_exc = exc
        for query in queries:
            try:
                data = _reddit_get(
                    f"/r/{subreddit}/search.json",
                    {"q": query, "restrict_sr": 1, "sort": "new", "limit": 25},
                )
                items = _reddit_listing_payloads(data, brand, query, subreddit=subreddit, scope=scope)
                payloads.extend(items)
                got += len(items)
            except (FetchError, ValueError) as exc:
                last_exc = exc
                continue
        # JSON endpoints blocked/failed → recover via anonymous RSS before reporting an error.
        if got == 0 and last_exc is not None:
            rss = _reddit_subreddit_rss_payloads(brand, subreddit, scope)
            if rss:
                payloads.extend(rss)
                _touch_link(conn, link_id, status="ok", error="")
            else:
                status, msg = _classify_error(last_exc, default="blocked")
                _touch_link(conn, link_id, status=status, error=msg)
        else:
            _touch_link(conn, link_id, status="ok" if got else "empty",
                        error="" if got else "该 subreddit 暂无匹配新帖子")
    return payloads


# ---------------------------------------------------------------- App Store
def collect_app_store(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM links WHERE brand_id = ? AND platform = 'app_store'",
        (brand.get("id"),),
    ).fetchall()
    payloads: list[dict] = []
    for row in rows:
        match = re.search(r"id(\d+)", row["url"] or "")
        if not match:
            continue
        app_id = match.group(1)
        country = (row["region"] or "us").lower()[:2]
        feed = (
            f"https://itunes.apple.com/{country}/rss/customerreviews/"
            f"id={app_id}/sortBy=mostRecent/json"
        )
        try:
            data = fetch_json(feed, timeout=16)
        except FetchError:
            continue
        entries = (data.get("feed", {}) or {}).get("entry", []) if isinstance(data, dict) else []
        for entry in entries:
            if not isinstance(entry, dict) or "im:rating" not in entry:
                continue
            body = clean_text((entry.get("content", {}) or {}).get("label"))
            title = clean_text((entry.get("title", {}) or {}).get("label"))
            review_id = (entry.get("id", {}) or {}).get("label") or f"{app_id}:{title}"
            rating = (entry.get("im:rating", {}) or {}).get("label")
            payloads.append({
                "source_id": "app_store_reviews",
                "brand_id": brand.get("id"),
                "product_id": row["product_id"],
                "link_id": row["id"],
                "external_id": review_id,
                "data_type": "user_voice",
                "dimension": "voc",
                "channel": "app",
                "platform": "app_store",
                "title": title,
                "author": clean_text((entry.get("author", {}) or {}).get("name", {}).get("label")),
                "body": body or title,
                "url": row["url"],
                "region": country.upper(),
                "metrics": {"rating": rating},
                "raw": {"rating": rating},
            })
    return payloads


# ---------------------------------------------------------------- Meta Ad Library
def collect_meta_ads(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    token = CREDENTIALS.get("facebook_access_token")
    if not token:
        return []
    payloads: list[dict] = []
    fields = "id,ad_creative_bodies,ad_snapshot_url,page_name,ad_delivery_start_time,publisher_platforms"
    for query in brand_queries(brand):
        url = (
            "https://graph.facebook.com/v19.0/ads_archive?"
            f"search_terms={quote_plus(query)}&ad_reached_countries=%5B%22US%22%5D"
            f"&ad_active_status=ALL&fields={fields}&limit=25&access_token={quote_plus(token)}"
        )
        try:
            data = fetch_json(url, timeout=20)
        except FetchError:
            continue
        for ad in (data.get("data", []) if isinstance(data, dict) else []):
            bodies = ad.get("ad_creative_bodies") or []
            body = clean_text(" ".join(bodies)) or "(no creative text)"
            payloads.append({
                "source_id": "meta_ads",
                "brand_id": brand.get("id"),
                "external_id": f"{brand.get('id')}:{ad.get('id')}",
                "data_type": "ad",
                "dimension": "marketing",
                "channel": "ads",
                "platform": "meta",
                "title": ad.get("page_name") or query,
                "author": ad.get("page_name"),
                "body": body,
                "url": ad.get("ad_snapshot_url"),
                "occurred_at": parse_rss_datetime(ad.get("ad_delivery_start_time")),
                "metrics": {"publisher_platforms": ad.get("publisher_platforms")},
                "raw": ad,
            })
    return payloads


# YouTube creator collection now lives in connectors/creators/youtube.py
# (enriched with view/like/comment + subscriber counts and collaboration detection).


# ---------------------------------------------------------------- Self-hosted community sites
# Reddit / Discord / Facebook groups are handled by their own (native) connectors;
# everything else configured under the community channel is treated as a website.
COMMUNITY_NATIVE_PLATFORMS = {
    "reddit", "discord", "discord_community", "facebook_group", "facebook_groups",
}


def _parse_iso(value) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError):
        return None


def _discourse_replies(root: str, topic_id, topic_title: str, topic_url: str, topic_ext: str, brand: dict, link_id, host: str) -> list[dict]:
    """Top-level replies of a Discourse topic (post_stream minus the original post)."""
    try:
        detail = fetch_json(f"{root}/t/{topic_id}.json", timeout=16)
    except (FetchError, ValueError):
        return []
    posts = (((detail or {}).get("post_stream") or {}).get("posts")) or []
    out: list[dict] = []
    for post in posts[1:11]:  # skip the original post; cap replies to bound volume
        pid = post.get("id")
        body = html_fragment_to_text(post.get("cooked")) or ""
        if not body:
            continue
        out.append({
            "source_id": "community_site",
            "brand_id": brand.get("id"),
            "link_id": link_id,
            "external_id": f"{topic_ext}:p{pid}",
            "data_type": "community_reply",
            "dimension": "marketing",
            "channel": "community",
            "platform": "discourse",
            "title": f"回复：{topic_title}",
            "author": clean_text(post.get("username") or post.get("name")),
            "body": body,
            "url": topic_url,
            "occurred_at": _parse_iso(post.get("created_at")),
            "metrics": {"parent_external_id": topic_ext, "reply_to_post_number": post.get("reply_to_post_number")},
            "raw": {"post_id": pid, "topic_id": topic_id, "site": root},
        })
    return out


def _discourse_payloads(url: str, brand: dict, link_id) -> list[dict]:
    """Discourse forums expose a clean JSON API; `/latest.json` lists recent topics,
    and `/t/{id}.json` yields the topic's posts (we keep the replies as community_reply)."""
    root = root_url(url).rstrip("/")
    try:
        data = fetch_json(f"{root}/latest.json", timeout=16)
    except (FetchError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    topics = ((data.get("topic_list") or {}).get("topics")) or []
    host = host_key(root)
    payloads: list[dict] = []
    for index, topic in enumerate(topics[:25]):
        topic_id = topic.get("id")
        slug = topic.get("slug")
        topic_url = f"{root}/t/{slug}/{topic_id}" if slug and topic_id else root
        title = clean_text(topic.get("title")) or "Forum topic"
        topic_ext = f"{brand.get('id')}:discourse:{host}:{topic_id}"
        payloads.append({
            "source_id": "community_site",
            "brand_id": brand.get("id"),
            "link_id": link_id,
            "external_id": topic_ext,
            "data_type": "community_post",
            "dimension": "marketing",
            "channel": "community",
            "platform": "discourse",
            "title": title,
            "body": clean_text(topic.get("excerpt")) or title,
            "url": topic_url,
            "occurred_at": _parse_iso(topic.get("last_posted_at") or topic.get("created_at")),
            "metrics": {
                "posts_count": topic.get("posts_count"),
                "reply_count": topic.get("reply_count"),
                "views": topic.get("views"),
            },
            "raw": {"topic_id": topic_id, "site": root},
        })
        # Pull replies for the most recent handful of topics to keep request volume bounded.
        if index < 8 and topic_id and (topic.get("posts_count") or 0) > 1:
            payloads.extend(_discourse_replies(root, topic_id, title, topic_url, topic_ext, brand, link_id, host))
    return payloads


def _candidate_feeds(url: str) -> list[str]:
    root = root_url(url).rstrip("/")
    base = url.rstrip("/")
    candidates = [
        f"{root}/latest.rss",
        f"{base}/feed",
        f"{base}.rss",
        f"{base}/rss",
        f"{root}/feed",
        f"{root}/rss",
        f"{root}/index.xml",
    ]
    seen, result = set(), []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def _rss_payloads(url: str, brand: dict, link_id) -> list[dict]:
    """Most forums / community platforms expose RSS; probe common feed locations."""
    host = host_key(url)
    for feed_url in _candidate_feeds(url):
        try:
            raw = fetch_bytes(feed_url, accept="application/rss+xml,application/atom+xml", timeout=14)
            items = parse_rss(raw, limit=25)
        except FetchError:
            continue
        except Exception:
            continue
        if not items:
            continue
        payloads: list[dict] = []
        for item in items:
            link = item.get("url")
            if not link:
                continue
            payloads.append({
                "source_id": "community_site",
                "brand_id": brand.get("id"),
                "link_id": link_id,
                "external_id": f"{brand.get('id')}:community:{host}:{item.get('guid') or link}",
                "data_type": "community_post",
                "dimension": "marketing",
                "channel": "community",
                "platform": "forum",
                "title": item.get("title") or "Community post",
                "body": item.get("description") or item.get("title") or "",
                "url": link,
                "occurred_at": item.get("published_at"),
                "raw": {"feed": feed_url},
            })
        if payloads:
            return payloads
    return []


def _next_data_apollo(html: str) -> dict | None:
    """Extract a Next.js `__NEXT_DATA__` Apollo normalized cache from page HTML."""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except (ValueError, TypeError):
        return None
    apollo = ((data.get("props") or {}).get("apolloState")) or {}
    if isinstance(apollo, dict) and isinstance(apollo.get("data"), dict):
        apollo = apollo["data"]
    return apollo if isinstance(apollo, dict) else None


def _frill_payloads(url: str, brand: dict, link_id) -> list[dict]:
    """Frill feedback boards (e.g. feedback.plaud.ai): ideas are server-rendered into the
    Apollo cache on the roadmap page; we collect them as posts (+ any SSR'd comments as replies)."""
    root = root_url(url).rstrip("/")
    try:
        base = fetch_page(root + "/", timeout=22)
    except (FetchError, ValueError):
        return []
    base_html = base.get("html") or ""
    base_apollo = _next_data_apollo(base_html)
    # Only treat as Frill when we see its signature, otherwise let other adapters handle it.
    is_frill = "frill" in base_html.lower() or (
        base_apollo is not None and any(isinstance(v, dict) and v.get("__typename") == "Board" for v in base_apollo.values())
    )
    if not is_frill:
        return []
    host = host_key(root)

    ideas: dict[str, dict] = {}
    refs: dict[str, dict] = {}
    comments: list[dict] = []

    def ingest(apollo: dict | None) -> None:
        if not apollo:
            return
        for key, val in apollo.items():
            if not isinstance(val, dict):
                continue
            tn = val.get("__typename")
            if tn == "Idea" and val.get("idx"):
                ideas.setdefault(val["idx"], val)
            elif tn == "Comment":
                comments.append(val)
            else:
                refs[key] = val

    ingest(base_apollo)
    # The landing page carries board metadata only; the roadmap SSRs the actual ideas.
    try:
        roadmap = fetch_page(root + "/roadmap", timeout=25)
        ingest(_next_data_apollo(roadmap.get("html") or ""))
    except (FetchError, ValueError):
        pass

    if not ideas:
        return []

    def resolve_name(val: dict) -> str | None:
        ref = (val.get("author") or {}).get("__ref")
        target = refs.get(ref) if ref else None
        if isinstance(target, dict):
            return clean_text(target.get("name") or target.get("full_name") or target.get("username")) or None
        return None

    payloads: list[dict] = []
    idea_ext_by_idx: dict[str, str] = {}
    for idea in ideas.values():
        slug = idea.get("slug")
        idea_url = f"{root}/roadmap/{slug}" if slug else root
        title = clean_text(idea.get("name")) or "Feature idea"
        body = clean_text(idea.get("excerpt")) or title
        ext = f"{brand.get('id')}:frill:{host}:{idea.get('idx')}"
        idea_ext_by_idx[idea.get("idx")] = ext
        payloads.append({
            "source_id": "community_site",
            "brand_id": brand.get("id"),
            "link_id": link_id,
            "external_id": ext,
            "data_type": "community_post",
            "dimension": "marketing",
            "channel": "community",
            "platform": "frill",
            "title": title,
            "author": resolve_name(idea),
            "body": body,
            "url": idea_url,
            "occurred_at": _parse_iso(idea.get("created_at")),
            "metrics": {
                "vote_count": idea.get("vote_count"),
                "comment_count": idea.get("comment_count"),
                "follower_count": idea.get("follower_count"),
            },
            "raw": {"idx": idea.get("idx"), "number": idea.get("number"), "site": root},
        })
    # If a board ever SSRs comments, attach them as replies linked to their parent idea.
    for comment in comments:
        idea_ref = (comment.get("idea") or {}).get("__ref") or ""
        parent_idx = idea_ref.split(".")[0] if idea_ref else ""
        parent_ext = idea_ext_by_idx.get(parent_idx)
        body = clean_text(comment.get("body") or comment.get("excerpt") or "")
        if not parent_ext or not body:
            continue
        payloads.append({
            "source_id": "community_site",
            "brand_id": brand.get("id"),
            "link_id": link_id,
            "external_id": f"{parent_ext}:c{comment.get('idx') or comment.get('id')}",
            "data_type": "community_reply",
            "dimension": "marketing",
            "channel": "community",
            "platform": "frill",
            "title": "回复",
            "author": resolve_name(comment),
            "body": body,
            "url": root,
            "occurred_at": _parse_iso(comment.get("created_at")),
            "metrics": {"parent_external_id": parent_ext},
            "raw": {"site": root},
        })
    return payloads


MIN_SNAPSHOT_CHARS = 140


def _generic_site_payload(url: str, brand: dict, link_id) -> list[dict]:
    """Last-resort daily snapshot of a community page's visible text, with a quality gate
    so we don't store navigation/boilerplate junk and an honest label when content is thin."""
    try:
        page = fetch_page(url)
    except (FetchError, ValueError):
        return []
    excerpt = clean_text(page.get("text") or "")[:600]
    title = clean_text(page.get("title") or "")
    final_url = page.get("final_url") or url
    host = host_key(final_url)
    # Quality gate: skip pages that yielded no meaningful content (likely client-rendered or
    # nav-only). Returning nothing lets the caller honestly report "未解析到内容".
    if len(excerpt) < MIN_SNAPSHOT_CHARS and (not title or title == host):
        return []
    dynamic = len(excerpt) < MIN_SNAPSHOT_CHARS
    note = "页面快照（未解析到结构化社群内容，可能为动态渲染站点）" if dynamic else "页面快照（站点无公开数据接口）"
    return [{
        "source_id": "community_site",
        "brand_id": brand.get("id"),
        "link_id": link_id,
        "external_id": f"{brand.get('id')}:community:{host}:{today()}",
        "data_type": "community_post",
        "dimension": "marketing",
        "channel": "community",
        "platform": host or "site",
        "title": title or host,
        "body": excerpt or title or final_url,
        "url": final_url,
        "occurred_at": utc_now(),
        "metrics": {"snapshot": True, "dynamic": dynamic},
        "raw": {"mode": "page_snapshot", "note": note, "dynamic": dynamic},
    }]


def collect_community_sites(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    payloads: list[dict] = []
    for link in community_links(conn, brand.get("id")):
        platform = (link.get("platform") or "").lower()
        if platform in COMMUNITY_NATIVE_PLATFORMS:
            continue
        url = link.get("url")
        if not url:
            continue
        link_id = link.get("id")
        try:
            items = (
                _discourse_payloads(url, brand, link_id)
                or _frill_payloads(url, brand, link_id)
                or _rss_payloads(url, brand, link_id)
                or _generic_site_payload(url, brand, link_id)
            )
            payloads.extend(items)
            _touch_link(conn, link_id, status="ok" if items else "empty",
                        error="" if items else "未解析到可采集内容（站点可能为纯前端渲染或无公开数据接口）")
        except Exception as exc:
            status, msg = _classify_error(exc)
            _touch_link(conn, link_id, status=status, error=msg)
    return payloads
