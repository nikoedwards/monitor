"""Concrete collector functions for real data sources."""
from __future__ import annotations

import json
import html as html_lib
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote_plus, unquote, urlencode, urlparse, urlunparse

from ..config import CREDENTIALS, USER_AGENT
from ..fetchers import FetchError, fetch_bytes, fetch_form_json, fetch_json, fetch_page, parse_rss
from ..nlp import classify_media_property, detect_pr_themes
from ..records import cleanup_google_ad_mismatches
from ..relevance import google_news_match_evidence, query_match_evidence, reddit_post_id, search_query_parts
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
    now = utc_now()
    # Keep failed community links eligible for the next scheduler pass. A
    # blocked Reddit request should cool down and retry, rather than being
    # treated as successfully collected for the rest of the day.
    collect_at = None if status in {"blocked", "network", "error", "needs_credential"} else now
    conn.execute(
        "UPDATE links SET last_collect_at = ?, last_status = ?, last_error = ?, updated_at = ? WHERE id = ?",
        (collect_at, status, (error or "")[:500], now, link_id),
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
GOOGLE_NEWS_LOOKBACK_DAYS = 7
GOOGLE_NEWS_RESULT_LIMIT = 100


def _google_news_url(
    query: str,
    region: str = "US",
    language: str = "en-US",
    lookback_days: int = GOOGLE_NEWS_LOOKBACK_DAYS,
) -> str:
    lang_code = language.split("-", 1)[0] or "en"
    search_query = query.strip()
    if lookback_days > 0 and not re.search(r"(?:^|\s)when:\S+", search_query, flags=re.IGNORECASE):
        search_query = f"{search_query} when:{lookback_days}d"
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(search_query)}&hl={quote_plus(language)}&gl={quote_plus(region)}"
        f"&ceid={quote_plus(f'{region}:{lang_code}')}"
    )


def _is_recent_news_item(published_at: str | None, lookback_days: int, *, now: datetime | None = None) -> bool:
    if not published_at:
        return False
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return published.astimezone(timezone.utc) >= reference - timedelta(days=max(lookback_days, 1))


def collect_google_news(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    payloads: list[dict] = []
    seen_items: set[str] = set()
    queries = brand_queries(brand)
    for query in queries:
        try:
            raw = fetch_bytes(_google_news_url(query), accept="application/rss+xml,application/xml", timeout=18)
        except FetchError:
            continue
        for item in parse_rss(raw, limit=GOOGLE_NEWS_RESULT_LIMIT):
            url = item.get("url")
            if not url:
                continue
            item_key = clean_text(item.get("guid")) or url
            if item_key in seen_items or not _is_recent_news_item(item.get("published_at"), GOOGLE_NEWS_LOOKBACK_DAYS):
                continue
            title = item.get("title") or "Untitled media mention"
            body = item.get("description") or title
            relevance = google_news_match_evidence(query, brand.get("name") or "", title, body)
            if relevance is None:
                continue
            seen_items.add(item_key)
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
                    "traffic_lower": pub.get("traffic_lower", 0),
                    "traffic_upper": pub.get("traffic_upper", 0),
                    "popularity_rank": pub.get("popularity_rank"),
                    "traffic_source": pub.get("source"),
                    "traffic_confidence": pub.get("traffic_confidence"),
                    "traffic_as_of": pub.get("traffic_as_of"),
                    "media_tier": pub["tier"],
                    "authority": pub["authority"],
                    "country": pub["country"],
                    "language": pub["language"],
                    "ave": ave,
                    "publication_domain": pub_domain,
                    "publication_url": source_url,
                    "publication_icon": pub["icon_url"],
                },
                "raw": {
                    "query": query,
                    "publication": publication,
                    "source_url": source_url,
                    "collection_method": "google_news_rss_keyword_search",
                    "body_relevance_validated": True,
                    **relevance,
                },
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
    return reddit_post_id(url)


def _reddit_search_term(query: str) -> str:
    """Ask Reddit for an exact phrase when a brand name has several parts."""
    return f'"{query}"' if len(search_query_parts(query)) > 1 else query


def _reddit_brand_query(brand: dict) -> str:
    """Reddit-wide discovery uses only the primary brand name, not product keywords."""
    return clean_text(brand.get("name"))


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
        post_id = _reddit_post_id(link)
        if not post_id:
            continue
        title = clean_text(item.get("title")) or "Reddit post"
        body = html_fragment_to_text(item.get("selftext")) or title
        relevance = query_match_evidence(query, title, body) if query else None
        if query and relevance is None:
            continue
        sub = item.get("subreddit") or subreddit
        collection_mode = "site_search" if query else "official_hub"
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
            "raw": {
                "query": query,
                "subreddit": sub,
                "scope": scope,
                "collection_mode": collection_mode,
                **(relevance or {}),
            },
        })
    return payloads


def _reddit_rss_payloads(brand: dict, query: str) -> list[dict]:
    """Last-resort anonymous RSS fallback when the JSON endpoints are blocked."""
    url = f"{REDDIT_WEB}/search.rss?q={quote_plus(_reddit_search_term(query))}&sort=new&limit=25"
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
        post_id = _reddit_post_id(link)
        if not link or not post_id:
            continue
        title = item.get("title") or "Reddit post"
        body = item.get("description") or title
        relevance = query_match_evidence(query, title, body)
        if relevance is None:
            continue
        payloads.append({
            "source_id": "reddit_search",
            "brand_id": brand.get("id"),
            "external_id": f"{brand.get('id')}:reddit:{post_id or item.get('guid') or link}",
            "data_type": "community_post",
            "dimension": "marketing",
            "channel": "community",
            "platform": "reddit",
            "title": title,
            "body": body,
            "url": link,
            "occurred_at": item.get("published_at"),
            "metrics": {"scope": "site"},
            "raw": {
                "query": query,
                "scope": "site",
                "collection_mode": "site_search",
                **relevance,
            },
        })
    return payloads


def _reddit_subreddit_rss_payloads(brand: dict, subreddit: str, scope: str) -> list[dict]:
    """Anonymous per-subreddit RSS fallback used when the JSON endpoints are 403/blocked."""
    url = f"{REDDIT_WEB}/r/{subreddit}/new.rss?limit=100"
    try:
        raw = fetch_bytes(url, accept="application/rss+xml,application/atom+xml", timeout=16)
        items = parse_rss(raw, limit=100)
    except Exception:
        return []
    payloads: list[dict] = []
    for item in items:
        link = item.get("url")
        post_id = _reddit_post_id(link)
        if not link or not post_id:
            continue
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
            "raw": {
                "subreddit": subreddit,
                "scope": scope,
                "via": "rss",
                "collection_mode": "official_hub",
            },
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
    query = _reddit_brand_query(brand)
    # 1) Search all of Reddit using only the primary brand name. Product names
    #    and other monitoring keywords intentionally do not widen this search.
    if query:
        try:
            data = _reddit_get(
                "/search.json",
                {"q": _reddit_search_term(query), "sort": "new", "limit": 25, "type": "link"},
            )
            payloads.extend(_reddit_listing_payloads(data, brand, query))
        except (FetchError, ValueError):
            payloads.extend(_reddit_rss_payloads(brand, query))
    # 2) Brand-configured official hubs. Every post returned by the newest feed
    #    is accepted in feed order without keyword matching; deduplication happens downstream.
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
            data = _reddit_get(f"/r/{subreddit}/new.json", {"limit": 100})
            items = _reddit_listing_payloads(data, brand, "", subreddit=subreddit, scope=scope)
            payloads.extend(items)
            got += len(items)
        except (FetchError, ValueError) as exc:
            last_exc = exc
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
                        error="" if got else "该 subreddit 暂无新帖子")
    return payloads


# ---------------------------------------------------------------- App Store
_DEFAULT_APP_STORE_COUNTRIES = (
    "US", "CN", "GB", "JP", "KR", "DE", "FR", "CA", "AU", "SG", "HK",
    "TW", "IN", "BR", "MX", "IT", "ES", "NL", "ID", "TH", "VN",
)
_APP_STORE_DISCOVERY_COUNTRIES = ("US", "CN", "GB")
_APP_STORE_MARKET_WORKERS = 6


def _app_store_identity(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_text(str(value or "")).casefold())


def _app_store_count(value) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _select_official_apps(brand: dict, results: list[dict]) -> list[dict]:
    """Keep the most likely official seller portfolio for a brand search.

    Apple search can return competitors and third-party companion apps.  We
    first score sellers whose app names visibly contain the configured brand,
    then keep results from the strongest seller only.  This admits portfolios
    such as Anker/soundcore/eufy while rejecting unrelated search results.
    """
    brand_key = _app_store_identity(brand.get("name"))
    if len(brand_key) < 3:
        return []

    seller_scores: dict[str, int] = {}
    for item in results:
        if not isinstance(item, dict) or brand_key not in _app_store_identity(item.get("trackName")):
            continue
        seller = clean_text(item.get("sellerName"))
        seller_key = _app_store_identity(seller)
        if not seller_key:
            continue
        seller_scores[seller_key] = seller_scores.get(seller_key, 0) + max(
            1, _app_store_count(item.get("userRatingCount"))
        )
    if not seller_scores:
        return []

    trusted_seller = max(seller_scores, key=lambda key: (seller_scores[key], key))
    selected = [
        item for item in results
        if isinstance(item, dict)
        and _app_store_identity(item.get("sellerName")) == trusted_seller
        and item.get("trackId")
    ]
    selected.sort(
        key=lambda item: (-_app_store_count(item.get("userRatingCount")), clean_text(item.get("trackName")).casefold())
    )
    return selected[:12]


def _lookup_app_store_apps(app_ids: list[str], country: str) -> list[dict]:
    if not app_ids:
        return []
    lookup = (
        "https://itunes.apple.com/lookup?"
        f"id={quote_plus(','.join(app_ids))}&entity=software&country={country.lower()}"
    )
    data = fetch_json(lookup, timeout=16)
    return [item for item in (data.get("results", []) if isinstance(data, dict) else []) if isinstance(item, dict)]


def _search_official_app_store_apps(brand: dict, country: str) -> list[dict]:
    brand_name = clean_text(brand.get("name"))
    if not brand_name:
        return []
    search = (
        "https://itunes.apple.com/search?"
        f"term={quote_plus(brand_name)}&entity=software&country={country.lower()}&limit=25"
    )
    data = fetch_json(search, timeout=16)
    results = data.get("results", []) if isinstance(data, dict) else []
    return _select_official_apps(brand, results)


def _discover_app_store_portfolio(brand: dict) -> list[dict]:
    """Discover official app ids once, then localize them across storefronts."""
    for country in _APP_STORE_DISCOVERY_COUNTRIES:
        try:
            apps = _search_official_app_store_apps(brand, country)
        except FetchError:
            continue
        if apps:
            return apps
    return []


def _collect_app_store_markets(collector) -> list[tuple[str, list]]:
    """Fetch storefronts concurrently while preserving the configured order."""
    countries = list(_DEFAULT_APP_STORE_COUNTRIES)
    workers = min(_APP_STORE_MARKET_WORKERS, len(countries))

    def collect(country: str) -> tuple[str, list]:
        for attempt in range(2):
            try:
                return country, collector(country)
            except (FetchError, ValueError):
                if attempt == 0:
                    time.sleep(0.25)
        return country, []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(collect, countries))


def _app_store_metric_payload(
    brand: dict,
    app: dict,
    country: str,
    *,
    link_id: str | None = None,
    product_id: str | None = None,
    discovery: str,
) -> dict | None:
    app_id = clean_text(str(app.get("trackId") or ""))
    rating_count = _app_store_count(app.get("userRatingCount"))
    if not app_id or rating_count <= 0:
        return None
    track_name = clean_text(app.get("trackName")) or brand.get("name") or "App"
    rating = app.get("averageUserRating") or app.get("averageUserRatingForCurrentVersion")
    app_link_id = link_id or f"auto-app-store:{country}:{app_id}"
    return {
        "source_id": "app_store_reviews",
        "brand_id": brand.get("id"),
        "product_id": product_id,
        "link_id": app_link_id,
        "external_id": f"{brand.get('id')}:app-store:{country}:{app_id}:metrics:{today()}",
        "data_type": "app_metric",
        "dimension": "platform",
        "channel": "app",
        "platform": "app_store",
        "title": f"{track_name} App Store 指标",
        "body": f"{country} App Store 累计评分 {rating_count}",
        "url": clean_text(app.get("trackViewUrl")),
        "region": country,
        "metrics": {
            "rating": rating,
            "rating_count": rating_count,
            "rating_count_observed": True,
        },
        "raw": {
            "track_id": app_id,
            "track_name": track_name,
            "seller_name": clean_text(app.get("sellerName")),
            "rating_count": rating_count,
            "average_user_rating": rating,
            "storefront": country,
            "version": clean_text(app.get("version")),
            "current_version_release_date": app.get("currentVersionReleaseDate"),
            "public_data": True,
            "discovery": discovery,
        },
    }


def _configured_market_metric_payloads(
    brand: dict,
    rows: list[sqlite3.Row],
) -> tuple[list[dict], set[str]]:
    targets: dict[str, sqlite3.Row] = {}
    for row in rows:
        match = re.search(r"id(\d+)", row["url"] or "")
        if match:
            targets.setdefault(match.group(1), row)
    if not targets:
        return [], set()

    app_ids = list(targets)
    fallback_row = next(iter(targets.values()))

    def collect_market(country: str) -> list[tuple[dict, str]]:
        apps = _lookup_app_store_apps(app_ids, country)
        discovery = "configured_link_market_lookup"
        if not apps:
            apps = _search_official_app_store_apps(brand, country)
            discovery = "configured_link_storefront_search"
        market_payloads: list[tuple[dict, str]] = []
        for app in apps:
            app_id = clean_text(str(app.get("trackId") or ""))
            row = targets.get(app_id) or fallback_row
            metric = _app_store_metric_payload(
                brand,
                app,
                country,
                link_id=f"{row['id']}:{country}:{app_id}",
                product_id=row["product_id"],
                discovery=discovery,
            )
            if metric:
                market_payloads.append((metric, row["id"]))
        return market_payloads

    payloads: list[dict] = []
    successful_links: set[str] = set()
    for _country, market_payloads in _collect_app_store_markets(collect_market):
        for metric, link_id in market_payloads:
            payloads.append(metric)
            successful_links.add(link_id)
    return payloads, successful_links


def _configured_review_payloads(
    conn: sqlite3.Connection,
    brand: dict,
    row: sqlite3.Row,
    *,
    has_metric: bool,
) -> list[dict]:
    match = re.search(r"id(\d+)", row["url"] or "")
    if not match:
        _touch_link(conn, row["id"], status="empty", error="App Store 链接中未找到应用 ID")
        return []
    app_id = match.group(1)
    country = (row["region"] or "US").upper()[:2]
    payloads: list[dict] = []
    errors: list[str] = []

    feed = (
        f"https://itunes.apple.com/{country.lower()}/rss/customerreviews/"
        f"id={app_id}/sortBy=mostRecent/json"
    )
    try:
        data = fetch_json(feed, timeout=16)
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
                "region": country,
                "metrics": {"rating": rating},
                "raw": {"rating": rating},
            })
    except FetchError as exc:
        errors.append(str(exc))

    _touch_link(
        conn,
        row["id"],
        status="ok" if payloads or has_metric else "network" if errors else "empty",
        error="; ".join(errors) if not payloads and not has_metric else "",
    )
    return payloads


def _discovered_app_payloads(brand: dict) -> list[dict]:
    portfolio = _discover_app_store_portfolio(brand)
    app_ids = [clean_text(str(app.get("trackId") or "")) for app in portfolio if app.get("trackId")]
    if not app_ids:
        return []
    payloads: list[dict] = []
    def collect_market(country: str) -> list[dict]:
        try:
            apps = _lookup_app_store_apps(app_ids, country)
        except (FetchError, ValueError):
            apps = []
        discovery = "portfolio_market_lookup"
        if not apps:
            apps = _search_official_app_store_apps(brand, country)
            discovery = "storefront_brand_search"
        return [
            metric
            for app in apps
            if (metric := _app_store_metric_payload(brand, app, country, discovery=discovery))
        ]

    for _country, market_payloads in _collect_app_store_markets(collect_market):
        payloads.extend(market_payloads)
    return payloads


def collect_app_store(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM links WHERE brand_id = ? AND platform = 'app_store' AND status = 'active'",
        (brand.get("id"),),
    ).fetchall()
    if not rows:
        return _discovered_app_payloads(brand)
    payloads, successful_links = _configured_market_metric_payloads(brand, rows)
    for row in rows:
        payloads.extend(_configured_review_payloads(
            conn,
            brand,
            row,
            has_metric=row["id"] in successful_links,
        ))
    return payloads


# ---------------------------------------------------------------- Google Ads Transparency Center
_GOOGLE_ADS_RPC = "https://adstransparency.google.com/anji/_/rpc"
_GOOGLE_ADS_REGION_CODE = 2840  # United States
_GOOGLE_ADS_REGION = "US"
_GOOGLE_ADS_PAGE_SIZE = 40
_GOOGLE_ADS_MAX_PAGES = 5
_GOOGLE_ADS_MAX_ADVERTISERS = 4
_GOOGLE_ADS_MAX_PREVIEWS = 12
_GOOGLE_ADS_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _google_timestamp(value) -> str | None:
    """Decode Google's `{seconds, nanos}` timestamp shape to UTC ISO text."""
    if isinstance(value, dict):
        # The public RPC uses protobuf-style numeric keys (``1``/``2``),
        # while fixtures and other Google surfaces may expose the named
        # ``seconds``/``nanos`` shape.  Accept both representations.
        if "1" in value or "2" in value:
            seconds = value.get("1")
            nanos = value.get("2")
        else:
            seconds = value.get("seconds")
            nanos = value.get("nanos")
        try:
            value = float(seconds) + float(nanos or 0) / 1_000_000_000
        except (TypeError, ValueError):
            value = seconds
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _google_timestamp_seconds(value) -> float | None:
    if isinstance(value, dict):
        if "1" in value or "2" in value:
            seconds = value.get("1")
            nanos = value.get("2")
        else:
            seconds = value.get("seconds")
            nanos = value.get("nanos")
        try:
            value = float(seconds) + float(nanos or 0) / 1_000_000_000
        except (TypeError, ValueError):
            value = seconds
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _google_js_unescape(value: str) -> str:
    """Unescape the small JS string fragments used by preview/content.js."""
    if not value:
        return ""

    def replace_hex(match: re.Match) -> str:
        try:
            return chr(int(match.group(1), 16))
        except (TypeError, ValueError):
            return match.group(0)

    value = re.sub(r"\\x([0-9a-fA-F]{2})", replace_hex, value)
    value = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )
    return (
        value.replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\/", "/")
        .replace("\\&", "&")
        .replace("\\=", "=")
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace("\\\\", "\\")
    )


def _google_preview_fields(preview_url: str, cache: dict[str, dict]) -> dict:
    """Read text/links/media from Google's public creative preview script."""
    if not preview_url:
        return {}
    if preview_url in cache:
        return cache[preview_url]
    fields: dict[str, str] = {}
    try:
        raw = fetch_bytes(
            preview_url,
            accept="application/javascript,text/javascript,*/*",
            timeout=22,
            max_bytes=3_000_000,
            headers={"User-Agent": _GOOGLE_ADS_USER_AGENT, "Referer": "https://adstransparency.google.com/"},
        )
        text = raw.decode("utf-8", errors="replace")
    except (FetchError, ValueError, OSError):
        cache[preview_url] = fields
        return fields

    # The response contains a large generic ad renderer before the actual
    # ``google_template_data.adData`` object. Decode first, then use the last
    # occurrence so a helper function's `description` string cannot be
    # mistaken for the creative copy.
    decoded_text = _google_js_unescape(text)
    ad_data_matches = list(re.finditer(r"adData[\"']?\s*:\s*\[\s*\{", decoded_text))
    if ad_data_matches:
        text = decoded_text[ad_data_matches[-1].start():]
    else:
        text = decoded_text

    def first(patterns: tuple[str, ...], haystack: str = text) -> str:
        for pattern in patterns:
            match = re.search(pattern, haystack, flags=re.I | re.S)
            if match:
                value = clean_text(_google_js_unescape(match.group(1)))
                if any(marker in value for marker in ("+c.", "catch(", "function(", "this.")):
                    continue
                if value:
                    return value
        return ""

    for key, patterns in {
        "headline": (r"(?<![A-Za-z0-9_])['\"]?headline['\"]?\s*:\s*'((?:\\.|[^'])*)'", r"(?<![A-Za-z0-9_])['\"]?headline['\"]?\s*:\s*\"((?:\\.|[^\"])*)\""),
        "long_headline": (r"(?<![A-Za-z0-9_])['\"]?longHeadline['\"]?\s*:\s*'((?:\\.|[^'])*)'", r"(?<![A-Za-z0-9_])['\"]?longHeadline['\"]?\s*:\s*\"((?:\\.|[^\"])*)\""),
        "description": (r"(?<![A-Za-z0-9_])['\"]?description['\"]?\s*:\s*'((?:\\.|[^'])*)'", r"(?<![A-Za-z0-9_])['\"]?description['\"]?\s*:\s*\"((?:\\.|[^\"])*)\""),
        "destination_url": (r"(?<![A-Za-z0-9_])['\"]?destination_url['\"]?\s*:\s*'((?:\\.|[^'])*)'", r"(?<![A-Za-z0-9_])['\"]?destination_url['\"]?\s*:\s*\"((?:\\.|[^\"])*)\""),
        "final_url": (r"(?<![A-Za-z0-9_])['\"]?final_url['\"]?\s*:\s*'((?:\\.|[^'])*)'", r"(?<![A-Za-z0-9_])['\"]?final_url['\"]?\s*:\s*\"((?:\\.|[^\"])*)\""),
        "visible_url": (r"(?<![A-Za-z0-9_])['\"]?visible_url['\"]?\s*:\s*'((?:\\.|[^'])*)'", r"(?<![A-Za-z0-9_])['\"]?visible_url['\"]?\s*:\s*\"((?:\\.|[^\"])*)\""),
        "thumbnail_url": (r"(?<![A-Za-z0-9_])['\"]?thumbnail['\"]?\s*:\s*'((?:\\.|[^'])*)'", r"(?<![A-Za-z0-9_])['\"]?thumbnail['\"]?\s*:\s*\"((?:\\.|[^\"])*)\""),
        "high_res_thumbnail_url": (r"(?<![A-Za-z0-9_])['\"]?highResThumbnail['\"]?\s*:\s*'((?:\\.|[^'])*)'", r"(?<![A-Za-z0-9_])['\"]?highResThumbnail['\"]?\s*:\s*\"((?:\\.|[^\"])*)\""),
        "video_url": (r"(?<![A-Za-z0-9_])['\"]?video['\"]?\s*:\s*'((?:\\.|[^'])*)'", r"(?<![A-Za-z0-9_])['\"]?video['\"]?\s*:\s*\"((?:\\.|[^\"])*)\""),
    }.items():
        value = first(patterns, decoded_text if key in {"destination_url", "final_url", "visible_url"} else text)
        if value:
            fields[key] = unquote(value) if key.endswith("url") else value
    cache[preview_url] = fields
    return fields


def _google_html_image(html: str) -> str | None:
    if not html:
        return None
    match = re.search(r'<img[^>]+src=["\']([^"\']+)', html, flags=re.I)
    return clean_text(html_lib.unescape(match.group(1))) if match else None


def _google_advertiser_suggestions(query: str) -> list[dict]:
    payload = {
        "1": query,
        "2": 10,
        "3": 10,
        "4": [_GOOGLE_ADS_REGION_CODE],
        "5": {"1": 1},
    }
    response = fetch_form_json(
        f"{_GOOGLE_ADS_RPC}/SearchService/SearchSuggestions?authuser=",
        {"f.req": json.dumps(payload, separators=(",", ":"))},
        timeout=25,
        headers={
            "User-Agent": _GOOGLE_ADS_USER_AGENT,
            "Origin": "https://adstransparency.google.com",
            "Referer": "https://adstransparency.google.com/?region=US",
        },
    )
    if not isinstance(response, dict) or "1" not in response:
        raise ValueError("Google SearchSuggestions returned an invalid response")
    suggestions: list[dict] = []
    for item in (response.get("1", []) if isinstance(response, dict) else []):
        advertiser = item.get("1") if isinstance(item, dict) else None
        if not isinstance(advertiser, dict):
            continue
        advertiser_id = clean_text(advertiser.get("2"))
        name = clean_text(advertiser.get("1"))
        if advertiser_id and name:
            suggestions.append({
                "id": advertiser_id,
                "name": name,
                "country": clean_text(advertiser.get("3")) or _GOOGLE_ADS_REGION,
                "raw": advertiser,
            })
    return suggestions


def _google_name_matches_query(name: str, query: str, brand_name: str | None = None) -> bool:
    """Keep advertiser suggestions that plausibly represent the monitored brand."""
    candidates = [clean_text(query), clean_text(brand_name)]
    # ``\w`` keeps Unicode letters/digits, so Chinese or accented advertiser
    # names can still be matched without relying on an unsafe first-result
    # fallback from SearchSuggestions.
    normalized_name = re.sub(r"[_\W]+", " ", (name or "").casefold()).strip()
    if not normalized_name:
        return False
    for candidate in candidates:
        normalized = re.sub(r"[_\W]+", " ", (candidate or "").casefold()).strip()
        if not normalized:
            continue
        if normalized_name == normalized or normalized_name.startswith(f"{normalized} "):
            return True
        # A multi-word search often includes product terms while the public
        # advertiser name only contains the brand token.
        first_token = normalized.split(" ", 1)[0]
        if len(first_token) >= 4 and normalized_name.startswith(f"{first_token} "):
            return True
    return False


def _google_search_creatives(
    advertiser_ids: list[str],
    *,
    offset: int = 0,
    page_token: str | None = None,
) -> dict:
    payload = {
        "2": _GOOGLE_ADS_PAGE_SIZE,
        "3": {
            "8": [_GOOGLE_ADS_REGION_CODE],
            "12": {"1": "", "2": True},
            "13": {"1": advertiser_ids},
        },
        "7": {"1": 1, "2": offset, "3": _GOOGLE_ADS_REGION_CODE},
    }
    # SearchCreatives returns its continuation token as response field `2`;
    # subsequent requests consume it through request field `4`.
    if page_token:
        payload["4"] = page_token
    response = fetch_form_json(
        f"{_GOOGLE_ADS_RPC}/SearchService/SearchCreatives?authuser=",
        {"f.req": json.dumps(payload, separators=(",", ":"))},
        timeout=35,
        headers={
            "User-Agent": _GOOGLE_ADS_USER_AGENT,
            "Origin": "https://adstransparency.google.com",
            "Referer": "https://adstransparency.google.com/?region=US",
        },
    )
    return response if isinstance(response, dict) else {}


def _google_public_payload(
    item: dict,
    brand: dict,
    query: str,
    *,
    preview_cache: dict[str, dict] | None = None,
    load_preview: bool = False,
    now: datetime | None = None,
) -> dict | None:
    advertiser_id = clean_text(item.get("1"))
    creative_id = clean_text(item.get("2"))
    if not advertiser_id or not creative_id:
        return None
    preview = item.get("3") if isinstance(item.get("3"), dict) else {}
    preview_url = clean_text(((preview.get("1") or {}).get("4")))
    image_html = clean_text(((preview.get("3") or {}).get("2")))
    image_url = _google_html_image(image_html)
    fields = {}
    if preview_url and load_preview:
        fields = _google_preview_fields(
            preview_url,
            preview_cache if preview_cache is not None else {},
        )
    elif preview_url and preview_cache is not None:
        fields = preview_cache.get(preview_url) or {}
    thumbnail_url = image_url or fields.get("high_res_thumbnail_url") or fields.get("thumbnail_url")
    video_url = fields.get("video_url")
    body_parts: list[str] = []
    for key in ("headline", "long_headline", "description"):
        value = clean_text(fields.get(key))
        if value and value not in body_parts:
            body_parts.append(value)
    body = " · ".join(body_parts)
    landing_url = fields.get("destination_url") or fields.get("final_url") or fields.get("visible_url")
    start_at = _google_timestamp(item.get("6"))
    last_seen_at = _google_timestamp(item.get("7"))
    now_dt = now or datetime.now(timezone.utc)
    last_seen_seconds = _google_timestamp_seconds(item.get("7"))
    active = bool(last_seen_seconds is not None and last_seen_seconds >= now_dt.timestamp() - 3 * 86400)
    stopped_at = None if active else last_seen_at
    snapshot_url = f"https://adstransparency.google.com/advertiser/{quote_plus(advertiser_id)}/creative/{quote_plus(creative_id)}?region={_GOOGLE_ADS_REGION}"
    advertiser_name = clean_text(item.get("12")) or query
    raw = {
        "collection_method": "google_ads_transparency_public_rpc",
        "search_query": query,
        "advertiser_id": advertiser_id,
        "creative_id": creative_id,
        "preview_url": preview_url,
        "preview_fields": fields,
        "thumbnail_url": thumbnail_url,
        "video_url": video_url,
        "landing_url": landing_url,
        "page_name": advertiser_name,
        "google_item": item,
    }
    metrics = {
        "advertiser_id": advertiser_id,
        "creative_id": creative_id,
        "creative_type": item.get("4"),
        "thumbnail_url": thumbnail_url,
        "video_url": video_url,
        "ad_landing_url": landing_url,
        "ad_creative_link_urls": [landing_url] if landing_url else [],
        "google_last_seen_at": last_seen_at,
        "publisher_platforms": ["google"],
        "country": _GOOGLE_ADS_REGION,
    }
    return {
        "source_id": "google_ads",
        "brand_id": brand.get("id"),
        "external_id": f"{brand.get('id')}:{advertiser_id}:{creative_id}",
        "data_type": "ad",
        "dimension": "marketing",
        "channel": "ads",
        "platform": "google",
        "title": advertiser_name,
        "author": advertiser_name,
        "body": body or advertiser_name,
        "url": snapshot_url,
        "occurred_at": start_at or utc_now(),
        "started_at": start_at,
        "stopped_at": stopped_at,
        "active_status": "active" if active else "inactive",
        "metrics": metrics,
        "raw": raw,
    }


def _collect_google_public_ads_with_report(brand: dict) -> tuple[list[dict], dict]:
    """Collect Google creatives and report whether the crawl is complete.

    Payloads can still be useful when one public RPC call fails, but those
    partial results must never authorize deletion of historical entities.
    """
    payloads: list[dict] = []
    raw_items: list[tuple[dict, str]] = []
    seen_advertisers: set[str] = set()
    # Creative IDs are normally globally unique, but Google does not document
    # that as a contract. Include the advertiser ID so a reused/scoped ID from
    # another advertiser is not silently dropped.
    seen_creatives: set[tuple[str, str]] = set()
    preview_cache: dict[str, dict] = {}
    queries = brand_queries(brand)[:4]
    report = {
        "matched_advertiser_ids": [],
        "queries_completed": 0,
        "query_count": len(queries),
        "suggestion_failed": False,
        "creative_failed": False,
        "pagination_truncated": False,
        "advertiser_cap_reached": False,
        "creative_count": 0,
        "safe_to_cleanup": False,
    }
    for query in queries:
        try:
            suggestions = _google_advertiser_suggestions(query)
        except Exception:
            report["suggestion_failed"] = True
            continue
        report["queries_completed"] += 1
        if not isinstance(suggestions, list):
            report["suggestion_failed"] = True
            continue
        matching = [
            advertiser
            for advertiser in suggestions
            if isinstance(advertiser, dict)
            and _google_name_matches_query(advertiser.get("name", ""), query, brand.get("name"))
        ]
        # Do not attach an arbitrary suggestion when the advertiser name does
        # not match the monitored brand. Google sometimes returns a single
        # generic result for product-like queries; treating that as a match
        # pollutes the lifecycle table with unrelated advertisers.
        for advertiser in matching:
            advertiser_id = advertiser.get("id")
            if not advertiser_id or advertiser_id in seen_advertisers:
                continue
            if len(seen_advertisers) >= _GOOGLE_ADS_MAX_ADVERTISERS:
                report["advertiser_cap_reached"] = True
                break
            seen_advertisers.add(advertiser_id)
            report["matched_advertiser_ids"].append(advertiser_id)
            page_token: str | None = None
            for page_index in range(_GOOGLE_ADS_MAX_PAGES):
                try:
                    search_kwargs = {"offset": page_index * _GOOGLE_ADS_PAGE_SIZE}
                    if page_token:
                        search_kwargs["page_token"] = page_token
                    response = _google_search_creatives([advertiser_id], **search_kwargs)
                except Exception:
                    report["creative_failed"] = True
                    break
                if not isinstance(response, dict) or "1" not in response:
                    report["creative_failed"] = True
                    break
                page_items = response.get("1")
                if not isinstance(page_items, list):
                    report["creative_failed"] = True
                    break
                for item in page_items:
                    creative_id = clean_text(item.get("2")) if isinstance(item, dict) else ""
                    creative_key = (advertiser_id, creative_id)
                    if not creative_id or creative_key in seen_creatives:
                        continue
                    seen_creatives.add(creative_key)
                    raw_items.append((item, query))
                raw_next_token = response.get("2") if isinstance(response, dict) else None
                next_token = clean_text(str(raw_next_token)) if raw_next_token is not None else ""
                # The server token is authoritative; a short page can still
                # carry a continuation when filters are applied. The
                # repeated-token guard protects against a throttled endpoint
                # returning the same page forever (the hard page cap is a
                # second backstop).
                if not next_token or next_token == page_token:
                    if next_token == page_token and next_token:
                        report["pagination_truncated"] = True
                    break
                if page_index == _GOOGLE_ADS_MAX_PAGES - 1:
                    report["pagination_truncated"] = True
                    break
                page_token = next_token
    # Image creatives expose a stable archive URL directly. Fetch a bounded
    # number of preview scripts for richer copy/video thumbnails; fetching all
    # scripts makes a daily public crawl unnecessarily slow and noisy.
    priority_preview_urls: list[str] = []
    fallback_preview_urls: list[str] = []
    for item, _query in raw_items:
        preview = item.get("3") if isinstance(item.get("3"), dict) else {}
        preview_url = clean_text(((preview.get("1") or {}).get("4")))
        image_html = clean_text(((preview.get("3") or {}).get("2")))
        if not preview_url:
            continue
        target = priority_preview_urls if not _google_html_image(image_html) else fallback_preview_urls
        if preview_url not in priority_preview_urls and preview_url not in fallback_preview_urls:
            target.append(preview_url)
    preview_urls = (priority_preview_urls + fallback_preview_urls)[:_GOOGLE_ADS_MAX_PREVIEWS]
    if preview_urls:
        def load_preview(url: str) -> tuple[str, dict]:
            return url, _google_preview_fields(url, {})
        with ThreadPoolExecutor(max_workers=4) as executor:
            for url, fields in executor.map(load_preview, preview_urls):
                preview_cache[url] = fields
    for item, query in raw_items:
        payload = _google_public_payload(item, brand, query, preview_cache=preview_cache)
        if payload:
            payloads.append(payload)
    report["creative_count"] = len(payloads)
    report["safe_to_cleanup"] = bool(
        report["matched_advertiser_ids"]
        and report["creative_count"]
        and report["queries_completed"] == report["query_count"]
        and not report["suggestion_failed"]
        and not report["creative_failed"]
        and not report["pagination_truncated"]
        and not report["advertiser_cap_reached"]
    )
    return payloads, report


def _collect_google_public_ads(brand: dict) -> list[dict]:
    """Backward-compatible payload-only wrapper for existing callers/tests."""
    payloads, _report = _collect_google_public_ads_with_report(brand)
    return payloads


def collect_google_ads(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    """Collect public Google Ads Transparency creatives without credentials."""
    payloads, report = _collect_google_public_ads_with_report(brand)
    if report.get("safe_to_cleanup"):
        cleanup_google_ad_mismatches(conn, brand, report["matched_advertiser_ids"])
    return payloads


# ---------------------------------------------------------------- Meta Ad Library
_META_PUBLIC_URL = "https://www.facebook.com/ads/library/"
_META_PUBLIC_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _meta_timestamp(value) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _meta_public_ads_from_html(html: str) -> list[dict]:
    """Extract Meta's server-rendered Ad Library results from application JSON."""
    scripts = re.findall(
        r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
        html or "",
        flags=re.I | re.S,
    )
    ads: list[dict] = []
    seen: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, dict):
            ad_id = value.get("ad_archive_id")
            if ad_id and isinstance(value.get("snapshot"), dict) and str(ad_id) not in seen:
                seen.add(str(ad_id))
                ads.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for raw in scripts:
        try:
            walk(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return ads


def _meta_public_payload(ad: dict, brand: dict, query: str) -> dict | None:
    snapshot = ad.get("snapshot") or {}
    cards = snapshot.get("cards") or []
    if not isinstance(cards, list):
        cards = []
    body_data = snapshot.get("body") or {}
    body = clean_text(body_data.get("text") if isinstance(body_data, dict) else body_data)
    if not body and cards:
        body = clean_text(" ".join(str(card.get("body") or "") for card in cards if isinstance(card, dict)))
    title = clean_text(snapshot.get("title")) or clean_text(body)[:160]
    page_name = clean_text(ad.get("page_name") or snapshot.get("page_name"))
    ad_id = clean_text(ad.get("ad_archive_id"))
    if not ad_id:
        return None
    images: list[str] = []
    videos: list[str] = []
    links: list[str] = []
    media_items = list(cards)
    for key in ("images", "videos", "extra_images", "extra_videos"):
        value = snapshot.get(key) or []
        if isinstance(value, list):
            media_items.extend(item for item in value if isinstance(item, dict))
    for card in media_items:
        if not isinstance(card, dict):
            continue
        for key in ("resized_image_url", "original_image_url", "video_preview_image_url"):
            value = clean_text(card.get(key))
            if value and value not in images:
                images.append(value)
        for key in ("video_hd_url", "video_sd_url"):
            value = clean_text(card.get(key))
            if value and value not in videos:
                videos.append(value)
        value = clean_text(card.get("link_url"))
        if value and value not in links:
            links.append(value)
    for key in ("link_url", "caption"):
        value = clean_text(snapshot.get(key))
        if value.startswith("http") and value not in links:
            links.append(value)
    start_at = _meta_timestamp(ad.get("start_date"))
    stop_at = _meta_timestamp(ad.get("end_date"))
    is_active = bool(ad.get("is_active"))
    platforms = ad.get("publisher_platform") or snapshot.get("publisher_platform") or []
    raw = {
        **ad,
        "collection_method": "meta_ad_library_public_ssr",
        "search_query": query,
        "thumbnail_url": images[0] if images else None,
        "video_url": videos[0] if videos else None,
    }
    metrics = {
        "publisher_platforms": platforms,
        "ad_creative_link_urls": links,
        "thumbnail_url": images[0] if images else None,
        "video_url": videos[0] if videos else None,
        "impressions": ad.get("impressions_with_index"),
        "spend": ad.get("spend"),
        "reach": ad.get("reach_estimate"),
        "page_id": ad.get("page_id") or snapshot.get("page_id"),
        "page_like_count": snapshot.get("page_like_count"),
        "cta_type": snapshot.get("cta_type"),
    }
    return {
        "source_id": "meta_ads",
        "brand_id": brand.get("id"),
        "external_id": f"{brand.get('id')}:{ad_id}",
        "data_type": "ad",
        "dimension": "marketing",
        "channel": "ads",
        "platform": "meta",
        "title": page_name or title or query,
        "author": page_name,
        "body": body or title or "(no creative text)",
        "url": f"https://www.facebook.com/ads/library/?id={quote_plus(ad_id)}",
        "occurred_at": start_at or utc_now(),
        "started_at": start_at,
        "stopped_at": stop_at if not is_active else None,
        "active_status": "active" if is_active else "inactive",
        "metrics": metrics,
        "raw": raw,
    }


def _collect_meta_public_ads(brand: dict) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError("Meta 公开广告抓取需要 Playwright/Chromium") from exc

    payloads: list[dict] = []
    seen: set[str] = set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(locale="en-US", user_agent=_META_PUBLIC_USER_AGENT, viewport={"width": 1366, "height": 900})
        page = context.new_page()
        try:
            for query in brand_queries(brand)[:4]:
                params = urlencode({
                    "active_status": "all",
                    "ad_type": "all",
                    "country": "US",
                    "q": query,
                    "search_type": "keyword_unordered",
                })
                try:
                    page.goto(f"{_META_PUBLIC_URL}?{params}", wait_until="domcontentloaded", timeout=90_000)
                    page.wait_for_timeout(4_500)
                    html = page.content()
                except Exception:
                    continue
                for ad in _meta_public_ads_from_html(html):
                    ad_id = str(ad.get("ad_archive_id"))
                    if ad_id in seen:
                        continue
                    payload = _meta_public_payload(ad, brand, query)
                    if payload:
                        seen.add(ad_id)
                        payloads.append(payload)
        finally:
            try:
                context.close()
            finally:
                browser.close()
    return payloads


def collect_meta_ads(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    token = CREDENTIALS.get("facebook_access_token")
    if token:
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
    return _collect_meta_public_ads(brand)


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


def _frill_latest_url(final_url: str, apollo: dict | None, root: str) -> str | None:
    """Return the board's public Ideas URL with Frill's "Latest Ideas" sort selected."""
    parsed = urlparse(final_url or "")
    path = parsed.path.rstrip("/")
    if not re.fullmatch(r"/b/[^/]+/[^/]+", path):
        board = next(
            (
                value
                for value in (apollo or {}).values()
                if isinstance(value, dict)
                and value.get("__typename") == "Board"
                and value.get("idx")
                and value.get("slug")
            ),
            None,
        )
        if not board:
            return None
        board_id = str(board["idx"])
        if board_id.startswith("board_"):
            board_id = board_id.removeprefix("board_")
        path = f"/b/{board_id}/{board['slug']}"
        parsed = urlparse(root + path)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["sortBy"] = "created_at"
    return urlunparse(parsed._replace(path=path, query=urlencode(query), fragment=""))


def _frill_payloads(url: str, brand: dict, link_id) -> list[dict] | None:
    """Collect each item from a Frill board's public ``Latest Ideas`` listing.

    ``None`` means the site is not recognized as Frill. An empty list means it is a
    Frill site but the structured listing could not be parsed, which prevents the
    caller from silently replacing real ideas with a daily homepage snapshot.
    """
    root = root_url(url).rstrip("/")
    try:
        base = fetch_page(root + "/", timeout=22)
    except (FetchError, ValueError):
        return None
    base_html = base.get("html") or ""
    base_apollo = _next_data_apollo(base_html)
    # Only treat as Frill when we see its signature, otherwise let other adapters handle it.
    is_frill = "frill" in base_html.lower() or (
        base_apollo is not None and any(isinstance(v, dict) and v.get("__typename") == "Board" for v in base_apollo.values())
    )
    if not is_frill:
        return None
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
    latest_url = _frill_latest_url(base.get("final_url") or url, base_apollo, root)
    if not latest_url:
        return []

    latest_page = base if (base.get("final_url") or "") == latest_url else None
    try:
        if latest_page is None:
            latest_page = fetch_page(latest_url, timeout=25)
        ingest(_next_data_apollo(latest_page.get("html") or ""))
    except (FetchError, ValueError):
        return []

    if not ideas:
        return []

    latest_final_url = (latest_page or {}).get("final_url") or latest_url
    latest_parsed = urlparse(latest_final_url)
    board_url = urlunparse(latest_parsed._replace(query="", fragment="")).rstrip("/")

    def resolve_name(val: dict) -> str | None:
        ref = (val.get("author") or {}).get("__ref")
        target = refs.get(ref) if ref else None
        if isinstance(target, dict):
            return clean_text(target.get("name") or target.get("full_name") or target.get("username")) or None
        return None

    payloads: list[dict] = []
    idea_ext_by_idx: dict[str, str] = {}
    sorted_ideas = sorted(
        ideas.values(),
        key=lambda idea: str(idea.get("created_at") or ""),
        reverse=True,
    )
    for idea in sorted_ideas:
        slug = idea.get("slug")
        idea_url = f"{board_url}/{slug}" if slug else board_url
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
            "platform": host or "frill",
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
            "raw": {
                "idx": idea.get("idx"),
                "number": idea.get("number"),
                "site": root,
                "collection_method": "frill_latest_ideas",
                "sort_by": "created_at",
            },
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
            "platform": host or "frill",
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
            items = _discourse_payloads(url, brand, link_id)
            frill_recognized = False
            if not items:
                frill_items = _frill_payloads(url, brand, link_id)
                frill_recognized = frill_items is not None
                if frill_recognized:
                    items = frill_items
                    site_platform = host_key(url) or "frill"
                    conn.execute(
                        "UPDATE records SET platform = ? "
                        "WHERE source_id = 'community_site' AND brand_id = ? AND link_id = ? "
                        "AND platform = 'frill' AND external_id LIKE ?",
                        (
                            site_platform,
                            brand.get("id"),
                            link_id,
                            f"{brand.get('id')}:frill:{site_platform}:%",
                        ),
                    )
                else:
                    items = _rss_payloads(url, brand, link_id) or _generic_site_payload(url, brand, link_id)
            payloads.extend(items)
            empty_error = (
                "Frill 的 Latest Ideas 页面未解析到结构化条目"
                if frill_recognized
                else "未解析到可采集内容（站点可能为纯前端渲染或无公开数据接口）"
            )
            _touch_link(conn, link_id, status="ok" if items else "empty", error="" if items else empty_error)
        except Exception as exc:
            status, msg = _classify_error(exc)
            _touch_link(conn, link_id, status=status, error=msg)
    return payloads
