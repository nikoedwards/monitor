from __future__ import annotations

import json
import mimetypes
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
DB_PATH = ROOT / "data" / "monitor.db"
PORT = int(os.environ.get("PORT", "8790"))


POSITIVE_TERMS = {
    "amazing", "love", "loved", "great", "excellent", "easy", "fast", "helpful",
    "reliable", "recommend", "smooth", "好用", "喜欢", "推荐", "稳定", "快", "满意"
}
NEGATIVE_TERMS = {
    "bad", "broken", "confusing", "crash", "delayed", "difficult", "expensive",
    "hate", "refund", "slow", "stopped", "terrible", "unstable", "差", "难用",
    "太贵", "退款", "慢", "崩溃", "投诉", "不稳定"
}
TOPIC_TERMS = {
    "price": {"price", "pricing", "expensive", "discount", "coupon", "太贵", "价格"},
    "quality": {"quality", "broken", "durable", "reliable", "材质", "质量"},
    "delivery": {"shipping", "delivery", "delayed", "arrived", "物流", "到货"},
    "feature": {"feature", "integration", "dashboard", "workflow", "功能", "接口"},
    "support": {"support", "service", "agent", "ticket", "客服", "售后"},
    "creator": {"creator", "influencer", "tiktok", "reels", "达人", "红人"},
    "retail": {"amazon", "shopify", "store", "checkout", "电商", "订单"},
    "pr": {"press", "media", "publication", "journalist", "媒体", "报道"}
}

AMAZON_MARKETS = {
    "amazon.com": "US",
    "amazon.co.uk": "UK",
    "amazon.ca": "CA",
    "amazon.de": "DE",
    "amazon.fr": "FR",
    "amazon.it": "IT",
    "amazon.es": "ES",
    "amazon.com.au": "AU",
    "amazon.co.jp": "JP"
}

SOCIAL_HOSTS = {
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "facebook": ("facebook.com", "fb.com"),
    "reddit": ("reddit.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "x": ("x.com", "twitter.com"),
    "linkedin": ("linkedin.com",),
    "pinterest": ("pinterest.com",)
}

GENERIC_SOCIAL_PATHS = {
    "instagram": {"", "about", "accounts", "explore", "p", "reel", "stories"},
    "tiktok": {"", "about", "discover", "embed", "tag", "music"},
    "facebook": {"", "share", "sharer", "dialog", "plugins"},
    "reddit": {"", "submit", "search"},
    "youtube": {"", "watch", "embed", "shorts", "playlist", "results"},
    "x": {"", "intent", "share", "search", "hashtag", "i"},
    "linkedin": {"", "sharearticle", "feed", "in"},
    "pinterest": {"", "pin", "search"}
}


class PageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self.anchors: list[str] = []
        self.json_ld: list[dict | list] = []
        self._script_type = ""
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = (attr.get("property") or attr.get("name") or "").lower()
            content = attr.get("content", "").strip()
            if key and content:
                self.meta[key] = content
        elif tag == "link":
            rel = attr.get("rel", "").lower()
            href = attr.get("href", "").strip()
            if rel and href:
                self.links[rel] = href
        elif tag == "a":
            href = attr.get("href", "").strip()
            if href:
                self.anchors.append(href)
        elif tag == "script":
            self._script_type = attr.get("type", "").lower()
            self._script_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag == "script" and "ld+json" in self._script_type:
            raw = "".join(self._script_parts).strip()
            if raw:
                try:
                    self.json_ld.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
            self._script_type = ""
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data.strip())
        elif "ld+json" in self._script_type:
            self._script_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(part for part in self.title_parts if part).strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def compact_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def normalize_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("URL is required")
    if not re.match(r"^https?://", url, re.I):
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError("Invalid URL")
    return url


def canonical_url(value: str | None) -> str:
    if not value:
        return ""
    try:
        url = normalize_url(value)
    except ValueError:
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parsed.path)
    return f"{parsed.scheme.lower()}://{host}{path}".lower()


def host_key(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = urlparse(normalize_url(value))
    except ValueError:
        return ""
    host = parsed.netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_amazon_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().replace("www.", "")
    return any(host == domain or host.endswith(f".{domain}") for domain in AMAZON_MARKETS)


def detect_social_platform(url: str) -> str:
    host = host_key(url)
    for platform, hosts in SOCIAL_HOSTS.items():
        if any(host == item or host.endswith(f".{item}") for item in hosts):
            return platform
    return ""


def clean_external_link(base_url: str, href: str) -> str:
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return ""
    return canonical_url(urljoin(base_url, href))


def is_likely_account_link(platform: str, url: str) -> bool:
    parsed = urlparse(url)
    first_segment = parsed.path.strip("/").split("/")[0].lower()
    if first_segment in GENERIC_SOCIAL_PATHS.get(platform, set()):
        return False
    return bool(first_segment)


def extract_json_links(items: list[dict]) -> list[str]:
    links: list[str] = []
    for item in items:
        for key in ("sameAs", "url"):
            value = item.get(key)
            if isinstance(value, str):
                links.append(value)
            elif isinstance(value, list):
                links.extend(entry for entry in value if isinstance(entry, str))
    return links


def discover_platform_links(base_url: str, metadata: PageMetadataParser, json_items: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    social_links: dict[str, str] = {}
    ecommerce_links: dict[str, str] = {}
    candidates = [*metadata.anchors, *extract_json_links(json_items)]

    for href in candidates:
        url = clean_external_link(base_url, href)
        if not url:
            continue
        if is_amazon_url(url):
            ecommerce_links.setdefault("amazon", url)
            continue
        platform = detect_social_platform(url)
        if platform and is_likely_account_link(platform, url):
            social_links.setdefault(platform, url)

    return social_links, ecommerce_links


def amazon_market(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    for domain, market in AMAZON_MARKETS.items():
        if host == domain or host.endswith(f".{domain}"):
            return market
    return ""


def extract_asin(url: str) -> str:
    match = re.search(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})(?:[/?]|$)", url, re.I)
    return match.group(1).upper() if match else ""


def amazon_url_kind(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if extract_asin(url):
        return "product"
    if path.startswith("/s") or path.startswith("/search") or "k=" in parsed.query:
        return "search"
    if "/stores/" in path or "/brand/" in path:
        return "store"
    return "amazon"


def amazon_search_keyword(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    for key in ("k", "field-keywords", "keywords"):
        value = params.get(key, [""])[0]
        if value:
            return clean_text(unquote_plus(value)).title()
    return ""


def amazon_title_candidate(url: str) -> str:
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return ""
    stop_words = {"dp", "gp", "product", "stores", "brand", "s"}
    for segment in segments:
        if segment.lower() in stop_words or re.fullmatch(r"[A-Z0-9]{10}", segment, re.I):
            continue
        cleaned = clean_text(unquote_plus(segment.replace("-", " ")))
        if cleaned and len(cleaned) > 2:
            return cleaned.title()
    return ""


def amazon_brand_from_html(html: str) -> str:
    patterns = [
        r'id=["\']bylineInfo["\'][^>]*>\s*(?:Visit the|Brand:|by)?\s*([^<|]+)',
        r'id=["\']productTitle["\'][^>]*>\s*([^<]+)',
        r'Brand(?:\s*Name)?\s*</span>\s*</td>\s*<td[^>]*>\s*<span[^>]*>\s*([^<]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I | re.S)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip()
            value = re.sub(r"^(Visit the|Brand:|by)\s+", "", value, flags=re.I)
            value = re.sub(r"\s+Store$", "", value, flags=re.I)
            if value and not value.lower().startswith("amazon"):
                return value[:120]
    return ""


def brand_from_title(title: str, host: str, source_kind: str) -> str:
    title = clean_text(title)
    if source_kind == "amazon":
        cleaned = re.sub(r"^Amazon\.[^:]+:\s*", "", title, flags=re.I)
        cleaned = re.split(r"\s[|-]\s|:", cleaned)[0].strip()
        if cleaned and not cleaned.lower().startswith("amazon"):
            return cleaned[:80]
        return ""
    return title_to_brand(title, host)


def flatten_json_ld(items: list[dict | list]) -> list[dict]:
    flattened: list[dict] = []
    for item in items:
        if isinstance(item, list):
            flattened.extend(entry for entry in item if isinstance(entry, dict))
        elif isinstance(item, dict):
            graph = item.get("@graph")
            if isinstance(graph, list):
                flattened.extend(entry for entry in graph if isinstance(entry, dict))
            flattened.append(item)
    return flattened


def first_json_value(items: list[dict], *keys: str) -> str:
    for item in items:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return clean_text(value)
            if isinstance(value, dict):
                nested = value.get("name") or value.get("url")
                if isinstance(nested, str) and nested.strip():
                    return clean_text(nested)
    return ""


def title_to_brand(title: str, host: str) -> str:
    cleaned = re.split(r"\s[|-]\s|:", title)[0].strip()
    if cleaned and len(cleaned) <= 80:
        return cleaned
    host = host.replace("www.", "")
    return host.split(".")[0].replace("-", " ").title()


def fetch_metadata(url: str) -> tuple[PageMetadataParser, str, str]:
    request = Request(
        url,
        headers={
            "User-Agent": "MonitorIntelligenceHub/0.1 (+local brand onboarding)",
            "Accept": "text/html,application/xhtml+xml"
        }
    )
    with urlopen(request, timeout=12) as response:
        final_url = response.geturl()
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(1_500_000)
    html = raw.decode(charset, errors="replace")
    parser = PageMetadataParser()
    parser.feed(html)
    return parser, final_url, html[:5000]


def analyze_brand_url(input_url: str) -> dict:
    url = normalize_url(input_url)
    source_kind = "amazon" if is_amazon_url(url) else "website"
    amazon_kind = amazon_url_kind(url) if source_kind == "amazon" else ""
    evidence = ["Parsed URL"]
    metadata = PageMetadataParser()
    final_url = url
    html_sample = ""
    fetch_error = ""

    try:
        metadata, final_url, html_sample = fetch_metadata(url)
        evidence.append("Fetched public page metadata")
    except Exception as exc:
        fetch_error = str(exc)
        evidence.append("Could not fetch page; using URL-derived fields")

    parsed = urlparse(final_url)
    host = parsed.netloc.lower().replace("www.", "")
    json_items = flatten_json_ld(metadata.json_ld)
    meta = metadata.meta
    social_links, ecommerce_links = discover_platform_links(final_url, metadata, json_items)

    title = clean_text(meta.get("og:title") or metadata.title)
    if source_kind == "amazon":
        search_keyword = amazon_search_keyword(final_url)
        product_brand = ""
        if amazon_kind != "search":
            product_brand = (
                first_json_value(json_items, "brand")
                or amazon_brand_from_html(html_sample)
                or brand_from_title(title, host, source_kind)
            )
        name = product_brand or search_keyword or amazon_title_candidate(final_url) or "待确认品牌"
    else:
        search_keyword = ""
        product_brand = ""
        name = (
            first_json_value(json_items, "brand", "name")
            or clean_text(meta.get("og:site_name"))
            or clean_text(meta.get("application-name"))
            or title_to_brand(title, host)
        )
    description = clean_text(
        first_json_value(json_items, "description")
        or meta.get("og:description")
        or meta.get("description")
    )
    logo_url = clean_text(
        first_json_value(json_items, "logo", "image")
        or meta.get("og:image")
        or metadata.links.get("icon")
    )
    if logo_url:
        logo_url = urljoin(final_url, logo_url)

    asin = extract_asin(final_url)
    marketplace = amazon_market(final_url)
    category = f"marketplace_{amazon_kind}" if source_kind == "amazon" else "brand_site"
    official_website = "" if source_kind == "amazon" else final_url
    amazon_url = final_url if source_kind == "amazon" else ecommerce_links.get("amazon", "")
    if amazon_url and not asin:
        asin = extract_asin(amazon_url)
    if amazon_url and not marketplace:
        marketplace = amazon_market(amazon_url)
    if source_kind == "amazon":
        evidence.append(f"Detected Amazon {amazon_kind or 'marketplace'} link")
    if source_kind == "amazon" and search_keyword and not product_brand:
        evidence.append(f"Used Amazon search keyword '{search_keyword}' as brand candidate")
    if source_kind == "amazon" and name == "待确认品牌":
        evidence.append("Product brand could not be confirmed from public page data")
    if asin:
        evidence.append(f"Detected ASIN {asin}")
    if social_links:
        evidence.append(f"Detected {len(social_links)} social platform link(s)")
    if ecommerce_links and source_kind != "amazon":
        evidence.append("Detected ecommerce marketplace link")

    keywords = []
    for value in [name, search_keyword, product_brand, title, asin]:
        value = clean_text(value)
        if (
            value
            and value != "待确认品牌"
            and not value.lower().startswith("amazon")
            and value.lower() not in {item.lower() for item in keywords}
        ):
            keywords.append(value)

    confidence = 0.3 if source_kind == "amazon" else 0.35
    if name and name != "待确认品牌":
        confidence += 0.2
    if description:
        confidence += 0.15
    if logo_url:
        confidence += 0.1
    if asin or official_website:
        confidence += 0.15

    return {
        "source_url": url,
        "final_url": final_url,
        "source_kind": source_kind,
        "amazon_url_kind": amazon_kind,
        "name": name,
        "official_website": official_website,
        "amazon_url": amazon_url,
        "social_links": social_links,
        "ecommerce_links": ecommerce_links,
        "marketplace": marketplace,
        "asin": asin,
        "category": category,
        "description": description,
        "logo_url": logo_url,
        "monitoring_keywords": keywords[:8],
        "confidence": min(confidence, 0.95),
        "evidence": evidence,
        "raw": {
            "title": title,
            "host": host,
            "amazon_url_kind": amazon_kind,
            "amazon_search_keyword": search_keyword,
            "amazon_product_brand": product_brand,
            "meta": meta,
            "json_ld_count": len(json_items),
            "anchor_count": len(metadata.anchors),
            "social_platforms": sorted(social_links.keys()),
            "ecommerce_platforms": sorted(ecommerce_links.keys()),
            "html_sample_chars": len(html_sample),
            "fetch_error": fetch_error
        }
    }


def analyze_text(text: str) -> tuple[str, float, str, list[str]]:
    lowered = text.lower()
    positive = sum(1 for term in POSITIVE_TERMS if term in lowered or term in text)
    negative = sum(1 for term in NEGATIVE_TERMS if term in lowered or term in text)
    score = max(-1.0, min(1.0, (positive - negative) / 3))

    if score > 0.2:
        sentiment = "positive"
    elif score < -0.2:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    if any(term in lowered or term in text for term in ["refund", "退款", "broken", "crash", "投诉"]):
        intent = "complaint"
    elif any(term in lowered or term in text for term in ["wish", "need", "希望", "能不能", "feature"]):
        intent = "request"
    elif any(term in lowered or term in text for term in ["buy", "switch", "competitor", "换成", "购买"]):
        intent = "purchase_signal"
    elif sentiment == "positive":
        intent = "praise"
    else:
        intent = "observation"

    topics = [
        topic for topic, terms in TOPIC_TERMS.items()
        if any(term in lowered or term in text for term in terms)
    ]
    return sentiment, score, intent, topics[:4]


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              category TEXT NOT NULL,
              vendor TEXT NOT NULL,
              sync_mode TEXT NOT NULL,
              status TEXT NOT NULL,
              notes TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS records (
              id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              external_id TEXT,
              data_type TEXT NOT NULL,
              platform TEXT,
              title TEXT,
              author TEXT,
              body TEXT NOT NULL,
              url TEXT,
              brand TEXT,
              competitor TEXT,
              product TEXT,
              region TEXT,
              language TEXT,
              occurred_at TEXT NOT NULL,
              sentiment TEXT NOT NULL,
              sentiment_score REAL NOT NULL,
              intent TEXT NOT NULL,
              topics_json TEXT NOT NULL,
              raw_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY (source_id) REFERENCES sources(id)
            );

            CREATE TABLE IF NOT EXISTS brands (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              source_url TEXT NOT NULL,
              source_kind TEXT NOT NULL,
              official_website TEXT,
              amazon_url TEXT,
              marketplace TEXT,
              asin TEXT,
              category TEXT,
              description TEXT,
              logo_url TEXT,
              social_links_json TEXT NOT NULL DEFAULT '{}',
              ecommerce_links_json TEXT NOT NULL DEFAULT '{}',
              monitoring_keywords_json TEXT NOT NULL,
              raw_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_id);
            CREATE INDEX IF NOT EXISTS idx_records_type ON records(data_type);
            CREATE INDEX IF NOT EXISTS idx_records_sentiment ON records(sentiment);
            CREATE INDEX IF NOT EXISTS idx_records_occurred ON records(occurred_at);
            CREATE INDEX IF NOT EXISTS idx_brands_name ON brands(name);
            """
        )
        ensure_column(conn, "brands", "social_links_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "brands", "ecommerce_links_json", "TEXT NOT NULL DEFAULT '{}'")

        source_count = conn.execute("SELECT COUNT(*) AS count FROM sources").fetchone()["count"]
        if source_count:
            return

        now = utc_now()
        sources = [
            ("manual_csv", "Manual CSV", "manual", "Internal", "upload", "ready", "CSV and one-off imports"),
            ("meltwater", "Meltwater", "pr", "Meltwater", "api_export", "planned", "Earned media and PR mentions"),
            ("social_blade", "Social Blade", "social", "Social Blade", "business_api", "planned", "Creator and channel statistics"),
            ("nox", "NoxInfluencer", "creator", "Nox", "api_or_export", "planned", "Influencer discovery and profile reports"),
            ("xingtu", "巨量星图", "creator", "ByteDance", "api_or_export", "planned", "China creator campaign data"),
            ("amazon_reviews", "Amazon Reviews", "commerce", "Amazon", "export", "planned", "Marketplace reviews and ratings"),
            ("shopify_support", "Shopify Support", "commerce", "Shopify", "webhook", "planned", "Tickets, orders, and customer service"),
            ("reddit_search", "Reddit Search", "community", "Reddit", "api", "planned", "Community discussions and feedback")
        ]
        conn.executemany(
            """
            INSERT INTO sources (id, name, category, vendor, sync_mode, status, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(*source, now) for source in sources]
        )

        seed_rows = [
            {
                "source_id": "manual_csv",
                "data_type": "user_voice",
                "platform": "TikTok",
                "title": "Creator comment",
                "author": "@northstar",
                "body": "The creator made the routine feel authentic, but the price still feels expensive compared with Brand A.",
                "brand": "Our Brand",
                "competitor": "Brand A",
                "product": "Skincare kit",
                "region": "US",
                "language": "en",
                "occurred_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            },
            {
                "source_id": "amazon_reviews",
                "data_type": "ecommerce_review",
                "platform": "Amazon",
                "title": "Charging issue",
                "author": "Verified buyer",
                "body": "The device stopped charging after two weeks. I asked support for a refund.",
                "brand": "Our Brand",
                "product": "Smart bottle",
                "region": "US",
                "language": "en",
                "occurred_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            },
            {
                "source_id": "reddit_search",
                "data_type": "community_post",
                "platform": "Reddit",
                "title": "Switching brands",
                "author": "u/marketwatcher",
                "body": "I switched from Brand B because their dashboard is confusing. I need clearer alerts and weekly summaries.",
                "brand": "Our Brand",
                "competitor": "Brand B",
                "region": "US",
                "language": "en",
                "occurred_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            },
            {
                "source_id": "shopify_support",
                "data_type": "support_ticket",
                "platform": "Shopify",
                "title": "Shipping delay",
                "author": "customer-1024",
                "body": "物流太慢了，希望能看到更准确的 delivery ETA。",
                "brand": "Our Brand",
                "product": "Starter bundle",
                "region": "CA",
                "language": "zh",
                "occurred_at": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
            },
            {
                "source_id": "meltwater",
                "data_type": "media_mention",
                "platform": "News",
                "title": "Launch coverage",
                "author": "Retail Daily",
                "body": "The publication highlighted the launch and called the influencer campaign a smooth category entry.",
                "brand": "Our Brand",
                "region": "UK",
                "language": "en",
                "occurred_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
            },
            {
                "source_id": "social_blade",
                "data_type": "creator_signal",
                "platform": "YouTube",
                "title": "Creator spike",
                "author": "@review-lab",
                "body": "Creator views increased fast after a competitor review video. Comments ask for a direct comparison.",
                "brand": "Our Brand",
                "competitor": "Brand C",
                "region": "US",
                "language": "en",
                "occurred_at": (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
            }
        ]
        for row in seed_rows:
            insert_record(conn, row)


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def insert_record(conn: sqlite3.Connection, payload: dict) -> dict:
    body = str(payload.get("body") or payload.get("text") or payload.get("content") or payload.get("comment") or payload.get("review") or "").strip()
    if not body:
        raise ValueError("Record body is required")

    sentiment, score, intent, topics = analyze_text(body)
    record = {
        "id": str(uuid.uuid4()),
        "source_id": payload.get("source_id") or "manual_csv",
        "external_id": payload.get("external_id"),
        "data_type": payload.get("data_type") or payload.get("type") or "user_voice",
        "platform": payload.get("platform"),
        "title": payload.get("title") or payload.get("headline"),
        "author": payload.get("author") or payload.get("username"),
        "body": body,
        "url": payload.get("url"),
        "brand": payload.get("brand"),
        "competitor": payload.get("competitor"),
        "product": payload.get("product"),
        "region": payload.get("region") or payload.get("market"),
        "language": payload.get("language") or "en",
        "occurred_at": payload.get("occurred_at") or payload.get("published_at") or payload.get("date") or utc_now(),
        "sentiment": sentiment,
        "sentiment_score": score,
        "intent": intent,
        "topics_json": json.dumps(topics, ensure_ascii=False),
        "raw_json": json.dumps(payload, ensure_ascii=False),
        "created_at": utc_now()
    }
    conn.execute(
        """
        INSERT INTO records (
          id, source_id, external_id, data_type, platform, title, author, body, url,
          brand, competitor, product, region, language, occurred_at, sentiment,
          sentiment_score, intent, topics_json, raw_json, created_at
        )
        VALUES (
          :id, :source_id, :external_id, :data_type, :platform, :title, :author, :body, :url,
          :brand, :competitor, :product, :region, :language, :occurred_at, :sentiment,
          :sentiment_score, :intent, :topics_json, :raw_json, :created_at
        )
        """,
        record
    )
    return record


def upsert_brand(conn: sqlite3.Connection, payload: dict) -> dict:
    name = clean_text(payload.get("name"))
    source_url = clean_text(payload.get("source_url") or payload.get("final_url"))
    if not name:
        raise ValueError("Brand name is required")
    if not source_url:
        raise ValueError("Source URL is required")

    brand_id = payload.get("id") or str(uuid.uuid4())
    now = utc_now()
    existing = conn.execute("SELECT created_at FROM brands WHERE id = ?", (brand_id,)).fetchone()
    keywords = payload.get("monitoring_keywords") or []
    if isinstance(keywords, str):
        keywords = [item.strip() for item in keywords.split(",") if item.strip()]
    social_links = payload.get("social_links") or {}
    ecommerce_links = payload.get("ecommerce_links") or {}
    if not isinstance(social_links, dict):
        social_links = {}
    if not isinstance(ecommerce_links, dict):
        ecommerce_links = {}
    if payload.get("amazon_url") and not ecommerce_links.get("amazon"):
        ecommerce_links["amazon"] = payload.get("amazon_url")

    brand = {
        "id": brand_id,
        "name": name,
        "source_url": source_url,
        "source_kind": payload.get("source_kind") or "website",
        "official_website": payload.get("official_website"),
        "amazon_url": payload.get("amazon_url"),
        "marketplace": payload.get("marketplace"),
        "asin": payload.get("asin"),
        "category": payload.get("category"),
        "description": payload.get("description"),
        "logo_url": payload.get("logo_url"),
        "social_links_json": json.dumps(social_links, ensure_ascii=False),
        "ecommerce_links_json": json.dumps(ecommerce_links, ensure_ascii=False),
        "monitoring_keywords_json": json.dumps(keywords, ensure_ascii=False),
        "raw_json": json.dumps(payload.get("raw") or payload, ensure_ascii=False),
        "created_at": existing["created_at"] if existing else now,
        "updated_at": now
    }
    conn.execute(
        """
        INSERT INTO brands (
          id, name, source_url, source_kind, official_website, amazon_url, marketplace,
          asin, category, description, logo_url, monitoring_keywords_json, raw_json,
          social_links_json, ecommerce_links_json, created_at, updated_at
        )
        VALUES (
          :id, :name, :source_url, :source_kind, :official_website, :amazon_url, :marketplace,
          :asin, :category, :description, :logo_url, :monitoring_keywords_json, :raw_json,
          :social_links_json, :ecommerce_links_json, :created_at, :updated_at
        )
        ON CONFLICT(id) DO UPDATE SET
          name = excluded.name,
          source_url = excluded.source_url,
          source_kind = excluded.source_kind,
          official_website = excluded.official_website,
          amazon_url = excluded.amazon_url,
          marketplace = excluded.marketplace,
          asin = excluded.asin,
          category = excluded.category,
          description = excluded.description,
          logo_url = excluded.logo_url,
          social_links_json = excluded.social_links_json,
          ecommerce_links_json = excluded.ecommerce_links_json,
          monitoring_keywords_json = excluded.monitoring_keywords_json,
          raw_json = excluded.raw_json,
          updated_at = excluded.updated_at
        """,
        brand
    )
    return brand


def row_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    if "topics_json" in item:
        item["topics"] = json.loads(item.pop("topics_json") or "[]")
    if "monitoring_keywords_json" in item:
        item["monitoring_keywords"] = json.loads(item.pop("monitoring_keywords_json") or "[]")
    if "social_links_json" in item:
        item["social_links"] = json.loads(item.pop("social_links_json") or "{}")
    if "ecommerce_links_json" in item:
        item["ecommerce_links"] = json.loads(item.pop("ecommerce_links_json") or "{}")
    return item


def list_brands(conn: sqlite3.Connection) -> list[dict]:
    return [row_to_dict(row) for row in conn.execute("SELECT * FROM brands ORDER BY updated_at DESC")]


def brand_identity(brand: dict) -> dict[str, set[str]]:
    social_links = brand.get("social_links") or {}
    ecommerce_links = brand.get("ecommerce_links") or {}
    identities = {
        "name": {compact_key(brand.get("name"))} if compact_key(brand.get("name")) else set(),
        "host": {
            value for value in [
                host_key(brand.get("official_website")),
                host_key(brand.get("source_url"))
            ] if value
        },
        "asin": {clean_text(brand.get("asin")).upper()} if clean_text(brand.get("asin")) else set(),
        "amazon": {
            value for value in [
                canonical_url(brand.get("amazon_url")),
                canonical_url(ecommerce_links.get("amazon") if isinstance(ecommerce_links, dict) else "")
            ] if value
        },
        "social": {
            canonical_url(url)
            for url in (social_links.values() if isinstance(social_links, dict) else [])
            if canonical_url(url)
        }
    }
    return identities


def similar_name_match(draft_names: set[str], existing_names: set[str]) -> bool:
    for draft_name in draft_names:
        for existing_name in existing_names:
            if not draft_name or not existing_name:
                continue
            if draft_name == existing_name:
                return True
            if len(draft_name) >= 4 and draft_name in existing_name:
                return True
            if len(existing_name) >= 4 and existing_name in draft_name:
                return True
    return False


def duplicate_candidates(conn: sqlite3.Connection, draft: dict, exclude_id: str = "") -> list[dict]:
    draft_identity = brand_identity(draft)
    candidates = []
    for brand in list_brands(conn):
        if exclude_id and brand.get("id") == exclude_id:
            continue
        existing_identity = brand_identity(brand)
        reasons = []
        if draft_identity["asin"] & existing_identity["asin"]:
            reasons.append("same_asin")
        if draft_identity["amazon"] & existing_identity["amazon"]:
            reasons.append("same_amazon_url")
        if draft_identity["host"] & existing_identity["host"]:
            reasons.append("same_domain")
        if draft_identity["social"] & existing_identity["social"]:
            reasons.append("same_social_link")
        if similar_name_match(draft_identity["name"], existing_identity["name"]):
            reasons.append("similar_brand_name")
        if reasons:
            candidates.append({
                "id": brand["id"],
                "name": brand["name"],
                "source_kind": brand["source_kind"],
                "source_url": brand["source_url"],
                "reasons": reasons
            })
    return candidates[:5]


def overview_payload() -> dict:
    with get_db() as conn:
        totals = conn.execute("SELECT COUNT(*) AS total FROM records").fetchone()["total"]
        sources = conn.execute("SELECT COUNT(*) AS total FROM sources").fetchone()["total"]
        brands = conn.execute("SELECT COUNT(*) AS total FROM brands").fetchone()["total"]
        by_sentiment = {
            row["sentiment"]: row["count"]
            for row in conn.execute("SELECT sentiment, COUNT(*) AS count FROM records GROUP BY sentiment")
        }
        by_type = [dict(row) for row in conn.execute("SELECT data_type, COUNT(*) AS count FROM records GROUP BY data_type ORDER BY count DESC")]
        by_source = [dict(row) for row in conn.execute(
            """
            SELECT sources.name, records.source_id, COUNT(*) AS count
            FROM records JOIN sources ON sources.id = records.source_id
            GROUP BY records.source_id
            ORDER BY count DESC
            """
        )]
        recent = [row_to_dict(row) for row in conn.execute(
            """
            SELECT records.*, sources.name AS source_name
            FROM records JOIN sources ON sources.id = records.source_id
            ORDER BY occurred_at DESC
            LIMIT 6
            """
        )]
        start = datetime.now(timezone.utc) - timedelta(days=13)
        trend = []
        for day in range(14):
            date = (start + timedelta(days=day)).date().isoformat()
            count = conn.execute("SELECT COUNT(*) AS count FROM records WHERE substr(occurred_at, 1, 10) = ?", (date,)).fetchone()["count"]
            trend.append({"date": date, "count": count})

        topics: dict[str, int] = {}
        for row in conn.execute("SELECT topics_json FROM records"):
            for topic in json.loads(row["topics_json"] or "[]"):
                topics[topic] = topics.get(topic, 0) + 1

    return {
        "total_records": totals,
        "total_sources": sources,
        "total_brands": brands,
        "by_sentiment": by_sentiment,
        "by_type": by_type,
        "by_source": by_source,
        "trend": trend,
        "top_topics": sorted(
            [{"topic": key, "count": value} for key, value in topics.items()],
            key=lambda item: item["count"],
            reverse=True
        )[:8],
        "recent": recent
    }


def list_records(params: dict[str, list[str]]) -> list[dict]:
    clauses = []
    values = []
    for field in ("source_id", "data_type", "sentiment", "intent"):
        value = params.get(field, [""])[0]
        if value:
            clauses.append(f"records.{field} = ?")
            values.append(value)
    query = params.get("q", [""])[0].strip()
    if query:
        clauses.append("(records.body LIKE ? OR records.title LIKE ? OR records.brand LIKE ? OR records.competitor LIKE ?)")
        like = f"%{query}%"
        values.extend([like, like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT records.*, sources.name AS source_name
        FROM records JOIN sources ON sources.id = records.source_id
        {where}
        ORDER BY occurred_at DESC
        LIMIT 120
    """
    with get_db() as conn:
        return [row_to_dict(row) for row in conn.execute(sql, values)]


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"ok": True, "db": str(DB_PATH)})
        elif parsed.path == "/api/overview":
            self._json(overview_payload())
        elif parsed.path == "/api/brands":
            with get_db() as conn:
                self._json(list_brands(conn))
        elif parsed.path == "/api/sources":
            with get_db() as conn:
                self._json([dict(row) for row in conn.execute("SELECT * FROM sources ORDER BY category, name")])
        elif parsed.path == "/api/records":
            self._json(list_records(parse_qs(parsed.query)))
        else:
            self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/records":
                with get_db() as conn:
                    record = insert_record(conn, payload)
                self._json(row_to_dict(record), status=201)
            elif parsed.path == "/api/brands/analyze":
                draft = analyze_brand_url(payload.get("url", ""))
                with get_db() as conn:
                    draft["duplicate_candidates"] = duplicate_candidates(conn, draft)
                self._json(draft)
            elif parsed.path == "/api/brands":
                with get_db() as conn:
                    brand = upsert_brand(conn, payload)
                self._json(row_to_dict(brand), status=201)
            elif parsed.path == "/api/import":
                rows = payload.get("rows", [])
                if not isinstance(rows, list):
                    raise ValueError("rows must be a list")
                source_id = payload.get("source_id") or "manual_csv"
                created = []
                with get_db() as conn:
                    for row in rows:
                        if isinstance(row, dict):
                            row.setdefault("source_id", source_id)
                            created.append(insert_record(conn, row))
                self._json({"created": len(created)}, status=201)
            else:
                self._json({"error": "Not found"}, status=404)
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not parsed.path.startswith("/api/brands/"):
                self._json({"error": "Not found"}, status=404)
                return
            brand_id = parsed.path.rsplit("/", 1)[-1]
            payload = self._read_json()
            payload["id"] = brand_id
            with get_db() as conn:
                brand = upsert_brand(conn, payload)
            self._json(row_to_dict(brand))
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not parsed.path.startswith("/api/brands/"):
                self._json({"error": "Not found"}, status=404)
                return
            brand_id = parsed.path.rsplit("/", 1)[-1]
            with get_db() as conn:
                cursor = conn.execute("DELETE FROM brands WHERE id = ?", (brand_id,))
            self._json({"deleted": cursor.rowcount})
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def _json(self, payload, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _serve_static(self, request_path: str) -> None:
        if request_path == "/":
            file_path = DIST / "index.html"
        else:
            file_path = (DIST / request_path.lstrip("/")).resolve()
            if not str(file_path).startswith(str(DIST.resolve())) or not file_path.exists():
                file_path = DIST / "index.html"

        if not file_path.exists():
            self._json({"error": "Run npm run build before starting the server"}, status=503)
            return

        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        return


if __name__ == "__main__":
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Monitor server running at http://127.0.0.1:{PORT}")
    server.serve_forever()
