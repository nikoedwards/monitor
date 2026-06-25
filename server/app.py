from __future__ import annotations

import json
import mimetypes
import os
import re
import hashlib
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import Counter
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape as html_escape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote_plus, urlencode, unquote_plus, urljoin, urlparse
from urllib.request import Request, build_opener, ProxyHandler, urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
DB_PATH = ROOT / "data" / "monitor.db"
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
PORT = int(os.environ.get("PORT", "8790"))
MONITOR_SCHEDULER_SECONDS = int(os.environ.get("MONITOR_SCHEDULER_SECONDS", "3600"))
DEFAULT_CRAWL_LIMIT = int(os.environ.get("MONITOR_CRAWL_LIMIT", "20"))

CAPTURE_JOBS: dict[str, dict] = {}
CAPTURE_JOBS_LOCK = threading.Lock()


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
    "experience": {"app", "ios", "android", "ux", "experience", "crash", "卡顿", "闪退", "体验", "不好用"},
    "ads": {"ad", "ads", "campaign", "creative", "广告", "投放", "素材"},
    "email": {"email", "mail", "inbox", "邮件", "来信"},
    "creator": {"creator", "influencer", "tiktok", "reels", "达人", "红人"},
    "retail": {"amazon", "shopify", "store", "checkout", "电商", "订单"},
    "pr": {"press", "media", "publication", "journalist", "媒体", "报道"}
}

VOC_ACTION_STATUSES = {"open", "assigned", "in_progress", "resolved", "closed"}
VOC_ACTION_PRIORITIES = {"low", "medium", "high", "urgent"}
VOC_OWNER_TEAMS = {"product_team", "support_team", "experience_team", "marketing_team"}
VOC_OWNER_TEAM_LABELS = {
    "product_team": "产品团队",
    "support_team": "客服同学",
    "experience_team": "体验/APP 团队",
    "marketing_team": "品牌/市场团队"
}
VOC_TOPIC_LABELS = {
    "price": "价格",
    "quality": "质量",
    "delivery": "物流",
    "feature": "功能",
    "support": "客服",
    "experience": "体验",
    "ads": "广告",
    "email": "邮件",
    "creator": "红人",
    "retail": "电商",
    "pr": "PR",
    "other": "其他"
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

PAID_PR_TERMS = {
    "sponsored", "paid content", "partner content", "advertorial", "press release",
    "pr newswire", "business wire", "globenewswire", "newswire", "ein presswire",
    "美通社", "新闻稿", "通稿", "赞助", "推广"
}

PUBLICATION_REACH_HINTS = [
    ("reuters", 9_000_000, "tier_1"),
    ("associated press", 8_500_000, "tier_1"),
    ("ap news", 8_000_000, "tier_1"),
    ("bloomberg", 7_500_000, "tier_1"),
    ("forbes", 6_500_000, "tier_1"),
    ("the wall street journal", 6_000_000, "tier_1"),
    ("new york times", 6_000_000, "tier_1"),
    ("bbc", 6_000_000, "tier_1"),
    ("cnn", 5_500_000, "tier_1"),
    ("techcrunch", 2_500_000, "tier_2"),
    ("the verge", 2_200_000, "tier_2"),
    ("wired", 2_000_000, "tier_2"),
    ("fast company", 1_700_000, "tier_2"),
    ("adweek", 1_200_000, "tier_2"),
    ("business wire", 550_000, "wire"),
    ("pr newswire", 520_000, "wire"),
    ("globenewswire", 420_000, "wire")
]

PR_THEME_TERMS = {
    "product_launch": {"launch", "unveil", "introduce", "release", "debut", "发布", "推出", "新品"},
    "partnership": {"partner", "partnership", "collaboration", "alliance", "合作", "联名"},
    "funding": {"funding", "investment", "raised", "series a", "融资", "投资"},
    "retail_expansion": {"retail", "store", "amazon", "walmart", "target", "marketplace", "渠道", "上架"},
    "creator_campaign": {"creator", "influencer", "tiktok", "youtube", "ambassador", "红人", "达人"},
    "leadership": {"ceo", "executive", "appoint", "hire", "founder", "任命", "高管"},
    "sustainability": {"sustainable", "climate", "recycle", "carbon", "esg", "可持续", "环保"},
    "reputation_risk": {"recall", "lawsuit", "complaint", "investigation", "breach", "召回", "诉讼", "调查"}
}

SALES_PLATFORM_HOSTS = {
    "amazon": ("amazon.com", "amazon.co.uk", "amazon.ca", "amazon.de", "amazon.fr", "amazon.it", "amazon.es", "amazon.com.au", "amazon.co.jp"),
    "alibaba": ("alibaba.com",),
    "aliexpress": ("aliexpress.com",),
    "walmart": ("walmart.com",),
    "tiktok_shop": ("shop.tiktok.com", "tiktok.com"),
    "shopify": ("myshopify.com",),
    "temu": ("temu.com",),
    "ebay": ("ebay.com",),
    "target": ("target.com",),
    "shopee": ("shopee.com", "shopee.sg", "shopee.co.th", "shopee.com.my", "shopee.ph", "shopee.vn"),
    "lazada": ("lazada.com", "lazada.sg", "lazada.co.th", "lazada.com.my", "lazada.com.ph", "lazada.vn"),
    "shein": ("shein.com",)
}

SALES_PLATFORM_LABELS = {
    "amazon": "Amazon",
    "alibaba": "Alibaba 国际站",
    "aliexpress": "AliExpress",
    "walmart": "Walmart",
    "tiktok_shop": "TikTok Shop",
    "shopify": "Shopify",
    "temu": "Temu",
    "ebay": "eBay",
    "target": "Target",
    "shopee": "Shopee",
    "lazada": "Lazada",
    "shein": "SHEIN",
    "owned_site": "独立站",
    "other": "其他渠道"
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


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth += 1
        elif tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            text = clean_text(data)
            if text:
                self.parts.append(text)

    @property
    def text(self) -> str:
        lines = []
        for line in "\n".join(self.parts).splitlines():
            normalized = clean_text(line)
            if normalized:
                lines.append(normalized)
        return "\n".join(lines)


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


def open_request(request: Request, timeout: int = 12):
    hostname = (urlparse(request.full_url).hostname or "").lower()
    if hostname in {"127.0.0.1", "localhost", "::1"}:
        return build_opener(ProxyHandler({})).open(request, timeout=timeout)
    return urlopen(request, timeout=timeout)


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
    with open_request(request, timeout=12) as response:
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


def snapshot_asset_url(filename: str | None) -> str:
    return f"/snapshots/{filename}" if filename else ""


def root_url(value: str) -> str:
    parsed = urlparse(normalize_url(value))
    return f"{parsed.scheme}://{parsed.netloc}/"


def page_key(value: str | None) -> str:
    return canonical_url(value) if value else ""


def is_html_like_url(value: str) -> bool:
    path = urlparse(value).path.lower()
    if not path or path.endswith("/"):
        return True
    suffix = Path(path).suffix
    return suffix in {"", ".html", ".htm", ".php", ".asp", ".aspx"}


def resolved_icon_url(page_url: str, icon_href: str = "") -> str:
    if icon_href:
        return urljoin(page_url, icon_href)
    return urljoin(root_url(page_url), "/favicon.ico")


def delete_snapshot_files(rows: list[sqlite3.Row]) -> None:
    for row in rows:
        for key in ("screenshot_path", "html_path"):
            filename = row[key]
            if not filename:
                continue
            file_path = (SNAPSHOT_DIR / filename).resolve()
            try:
                file_path.relative_to(SNAPSHOT_DIR.resolve())
                file_path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass


def extract_visible_text(html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(html)
    return parser.text


def fetch_snapshot_page(input_url: str) -> dict:
    url = normalize_url(input_url)
    request = Request(
        url,
        headers={
            "User-Agent": "MonitorIntelligenceHub/0.1 (+daily snapshot monitor)",
            "Accept": "text/html,application/xhtml+xml"
        }
    )
    with open_request(request, timeout=18) as response:
        final_url = response.geturl()
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(3_000_000)
    html = raw.decode(charset, errors="replace")
    metadata = PageMetadataParser()
    metadata.feed(html)
    title = clean_text(metadata.meta.get("og:title") or metadata.title)
    icon_href = metadata.links.get("icon") or metadata.links.get("shortcut icon") or metadata.links.get("apple-touch-icon")
    return {
        "requested_url": url,
        "final_url": final_url,
        "title": title or host_key(final_url) or url,
        "html": html,
        "text": extract_visible_text(html),
        "meta": metadata.meta,
        "anchors": metadata.anchors,
        "icon_url": resolved_icon_url(final_url, icon_href),
        "anchor_count": len(metadata.anchors),
        "json_ld_count": len(metadata.json_ld)
    }


def new_capture_job(monitor_id: str) -> dict:
    job = {
        "id": str(uuid.uuid4()),
        "monitor_id": monitor_id,
        "status": "queued",
        "progress": 0,
        "phase": "queued",
        "message": "Waiting to start",
        "current_url": "",
        "total_pages": 0,
        "completed_pages": 0,
        "snapshot_ids": [],
        "error": "",
        "created_at": utc_now(),
        "started_at": "",
        "finished_at": ""
    }
    with CAPTURE_JOBS_LOCK:
        CAPTURE_JOBS[job["id"]] = job
    return dict(job)


def update_capture_job(job_id: str | None, **updates) -> None:
    if not job_id:
        return
    with CAPTURE_JOBS_LOCK:
        job = CAPTURE_JOBS.get(job_id)
        if job:
            job.update(updates)


def capture_job_payload(job_id: str) -> dict:
    with CAPTURE_JOBS_LOCK:
        job = CAPTURE_JOBS.get(job_id)
        if not job:
            raise ValueError("Capture job not found")
        return dict(job)


def same_domain_page_candidates(base_url: str, page: dict) -> list[str]:
    root_host = host_key(base_url)
    links = []
    for href in page.get("anchors") or []:
        candidate = clean_external_link(page.get("final_url") or base_url, href)
        if not candidate:
            continue
        if host_key(candidate) != root_host:
            continue
        if not is_html_like_url(candidate):
            continue
        links.append(candidate)
    return links


def discover_domain_pages(start_url: str, limit: int, job_id: str | None = None) -> list[str]:
    limit = max(1, min(limit or DEFAULT_CRAWL_LIMIT, 200))
    start = canonical_url(start_url) or normalize_url(start_url)
    queue = [start]
    seen = {start}
    discovered: list[str] = []

    while queue and len(discovered) < limit:
        current = queue.pop(0)
        discovered.append(current)
        update_capture_job(
            job_id,
            phase="discovering",
            message=f"Discovering pages {len(discovered)}/{limit}",
            current_url=current,
            total_pages=max(len(discovered), len(queue) + len(discovered)),
            progress=min(18, 4 + int((len(discovered) / limit) * 14))
        )
        try:
            page = fetch_snapshot_page(current)
        except Exception:
            continue
        for candidate in same_domain_page_candidates(start, page):
            if candidate not in seen and len(seen) < limit:
                seen.add(candidate)
                queue.append(candidate)

    return discovered[:limit]


def start_capture_job(monitor_id: str) -> dict:
    job = new_capture_job(monitor_id)
    thread = threading.Thread(target=run_capture_job, args=(job["id"], monitor_id), daemon=True)
    thread.start()
    return job


def headless_browser_candidates() -> list[str]:
    candidates: list[str] = []
    env_browser = os.environ.get("SNAPSHOT_BROWSER", "").strip()
    if env_browser:
        candidates.append(env_browser)
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        candidates.extend([
            str(Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            str(Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            str(Path(local_app_data) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            str(Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe")
        ])
    for name in ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    seen = set()
    available = []
    for candidate in candidates:
        if candidate and candidate not in seen and Path(candidate).exists():
            seen.add(candidate)
            available.append(candidate)
    return available


def write_svg_snapshot(filename: str, url: str, title: str, text: str, error: str = "") -> str:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / filename
    text_lines = [clean_text(line) for line in text.splitlines() if clean_text(line)][:18]
    if not text_lines:
        text_lines = ["No readable page text was captured."]
    if error:
        text_lines.insert(0, f"Capture note: {error[:180]}")

    rows = []
    y = 214
    for line in text_lines:
        rows.append(f'<text x="72" y="{y}" class="body">{html_escape(line[:150])}</text>')
        y += 42

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="1200" viewBox="0 0 1440 1200">
  <style>
    .bg {{ fill: #f6f7f3; }}
    .panel {{ fill: #ffffff; stroke: #dfe5dd; stroke-width: 2; }}
    .muted {{ fill: #68736d; font: 24px system-ui, sans-serif; }}
    .title {{ fill: #1f2723; font: 700 48px system-ui, sans-serif; }}
    .body {{ fill: #1f2723; font: 26px system-ui, sans-serif; }}
    .url {{ fill: #127f70; font: 22px system-ui, sans-serif; }}
  </style>
  <rect class="bg" width="1440" height="1200"/>
  <rect class="panel" x="48" y="48" width="1344" height="1104" rx="16"/>
  <text class="muted" x="72" y="104">Generated visual snapshot fallback</text>
  <text class="title" x="72" y="166">{html_escape((title or "Untitled page")[:80])}</text>
  <text class="url" x="72" y="204">{html_escape(url[:120])}</text>
  {''.join(rows)}
</svg>"""
    path.write_text(svg, encoding="utf-8")
    return filename


def capture_snapshot_image(url: str, title: str, text: str, base_name: str) -> tuple[str, dict]:
    errors = []
    png_filename = f"{base_name}.png"
    png_path = SNAPSHOT_DIR / png_filename
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    for browser in headless_browser_candidates():
        for headless_flag in ("--headless=new", "--headless"):
            command = [
                browser,
                headless_flag,
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1440,1200",
                f"--screenshot={png_path}",
                url
            ]
            try:
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=45
                )
                if result.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0:
                    return png_filename, {"method": "headless_browser", "browser": browser}
                detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
                errors.append(detail[:300])
            except Exception as exc:
                errors.append(str(exc)[:300])

    svg_filename = f"{base_name}.svg"
    error = "; ".join(error for error in errors if error)[:500]
    write_svg_snapshot(svg_filename, url, title, text, error)
    return svg_filename, {"method": "svg_fallback", "error": error or "No headless browser found"}


def meaningful_lines(text: str) -> list[str]:
    result = []
    seen = set()
    for line in text.splitlines():
        normalized = clean_text(line)
        if 18 <= len(normalized) <= 260 and normalized.lower() not in seen:
            seen.add(normalized.lower())
            result.append(normalized)
    return result


def analyze_snapshot_change(current: dict, previous: sqlite3.Row | None) -> tuple[float, str, list[dict]]:
    current_text = current.get("text", "")
    current_title = current.get("title", "")
    if not previous:
        return 0.0, "Baseline snapshot saved. Future captures will be compared against this version.", []

    previous_text = previous["text_excerpt"] or ""
    previous_title = previous["title"] or ""
    changes: list[dict] = []
    if previous_title and current_title and previous_title != current_title:
        changes.append({"type": "title", "from": previous_title, "to": current_title})

    old_lines = meaningful_lines(previous_text)
    new_lines = meaningful_lines(current_text)
    old_set = {line.lower() for line in old_lines}
    new_set = {line.lower() for line in new_lines}
    added = [line for line in new_lines if line.lower() not in old_set][:6]
    removed = [line for line in old_lines if line.lower() not in new_set][:6]
    changes.extend({"type": "added", "text": line} for line in added)
    changes.extend({"type": "removed", "text": line} for line in removed)

    if previous["text_hash"] and previous["text_hash"] == current.get("text_hash"):
        return 0.0, "No visible text changes from the previous snapshot.", changes

    old_sample = previous_text[:20_000]
    new_sample = current_text[:20_000]
    if not old_sample and not new_sample:
        score = 0.0
    elif not old_sample or not new_sample:
        score = 1.0
    else:
        score = max(0.0, min(1.0, 1 - SequenceMatcher(None, old_sample, new_sample).ratio()))

    if score < 0.02 and not changes:
        summary = "No meaningful visible changes detected."
    elif score < 0.15:
        summary = f"Minor page updates detected: {len(added)} additions and {len(removed)} removals."
    else:
        summary = f"Notable page changes detected: {len(added)} additions and {len(removed)} removals."
    if any(item["type"] == "title" for item in changes):
        summary += " Page title changed."
    return round(score, 4), summary, changes


def snapshot_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["changes"] = json.loads(item.pop("changes_json") or "[]")
    item["raw"] = json.loads(item.pop("raw_json") or "{}")
    item["screenshot_url"] = snapshot_asset_url(item.get("screenshot_path"))
    item["html_url"] = snapshot_asset_url(item.get("html_path"))
    parsed = urlparse(item.get("final_url") or item.get("url") or "")
    item["page_path"] = parsed.path or "/"
    return item


def monitor_to_dict(conn: sqlite3.Connection, row: sqlite3.Row, days: int = 7) -> dict:
    monitor = dict(row)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days - 1, 0))).date().isoformat()
    stats = conn.execute(
        """
        SELECT
          COUNT(*) AS snapshots,
          COUNT(DISTINCT COALESCE(page_key, url)) AS pages,
          SUM(CASE WHEN change_score >= 0.02 THEN 1 ELSE 0 END) AS changed,
          MAX(created_at) AS latest
        FROM web_snapshots
        WHERE monitor_id = ? AND snapshot_date >= ?
        """,
        (row["id"], cutoff)
    ).fetchone()
    latest = conn.execute(
        """
        SELECT * FROM web_snapshots
        WHERE monitor_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (row["id"],)
    ).fetchone()
    monitor["snapshots"] = stats["snapshots"] or 0
    monitor["page_count"] = stats["pages"] or 0
    monitor["changed_snapshots"] = stats["changed"] or 0
    monitor["latest_snapshot"] = snapshot_to_dict(latest) if latest else None
    return monitor


def list_web_monitors(params: dict[str, list[str]]) -> list[dict]:
    days = int(params.get("days", ["7"])[0] or "7")
    brand_id = first_param(params, "brand_id")
    clauses = []
    values: list[str] = []
    if brand_id:
        clauses.append("brand_id = ?")
        values.append(brand_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM web_monitors {where} ORDER BY updated_at DESC",
            values
        ).fetchall()
        return [monitor_to_dict(conn, row, days) for row in rows]


def list_web_snapshots(params: dict[str, list[str]]) -> list[dict]:
    clauses = []
    values = []
    monitor_id = params.get("monitor_id", [""])[0]
    if monitor_id:
        clauses.append("web_snapshots.monitor_id = ?")
        values.append(monitor_id)
    days = params.get("days", [""])[0]
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(int(days) - 1, 0))).date().isoformat()
        clauses.append("snapshot_date >= ?")
        values.append(cutoff)
    start = params.get("from", [""])[0]
    end = params.get("to", [""])[0]
    if start:
        clauses.append("snapshot_date >= ?")
        values.append(start)
    if end:
        clauses.append("snapshot_date <= ?")
        values.append(end)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT web_snapshots.*, web_monitors.name AS monitor_name
            FROM web_snapshots
            JOIN web_monitors ON web_monitors.id = web_snapshots.monitor_id
            {where}
            ORDER BY web_snapshots.created_at DESC
            LIMIT 200
            """,
            values
        ).fetchall()
        return [snapshot_to_dict(row) for row in rows]


def web_monitor_summary_payload(params: dict[str, list[str]]) -> dict:
    days = int(params.get("days", ["7"])[0] or "7")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days - 1, 0))).date().isoformat()
    monitor_id = params.get("monitor_id", [""])[0]
    clauses = ["snapshot_date >= ?"]
    values = [cutoff]
    if monitor_id:
        clauses.append("web_snapshots.monitor_id = ?")
        values.append(monitor_id)

    with get_db() as conn:
        monitors = conn.execute("SELECT COUNT(*) AS count FROM web_monitors WHERE status = 'active'").fetchone()["count"]
        rows = conn.execute(
            f"""
            SELECT web_snapshots.*, web_monitors.name AS monitor_name
            FROM web_snapshots
            JOIN web_monitors ON web_monitors.id = web_snapshots.monitor_id
            WHERE {' AND '.join(clauses)}
            ORDER BY web_snapshots.created_at DESC
            """,
            values
        ).fetchall()

    snapshots = [snapshot_to_dict(row) for row in rows]
    daily: dict[str, dict] = {}
    for item in snapshots:
        bucket = daily.setdefault(item["snapshot_date"], {"date": item["snapshot_date"], "snapshots": 0, "changed": 0, "summaries": []})
        bucket["snapshots"] += 1
        if item["change_score"] >= 0.02:
            bucket["changed"] += 1
        if item["summary"]:
            bucket["summaries"].append({
                "monitor": item.get("monitor_name"),
                "page": item.get("title") or item.get("page_path") or item.get("url"),
                "url": item.get("final_url") or item.get("url"),
                "summary": item["summary"],
                "score": item["change_score"],
                "snapshot_id": item["id"]
            })

    highlights = [
        {
            "monitor": item.get("monitor_name"),
            "page": item.get("title") or item.get("page_path") or item.get("url"),
            "url": item.get("final_url") or item.get("url"),
            "date": item["snapshot_date"],
            "summary": item["summary"],
            "score": item["change_score"],
            "snapshot_id": item["id"],
            "screenshot_url": item["screenshot_url"]
        }
        for item in snapshots
        if item["summary"]
    ][:8]

    return {
        "range_days": days,
        "generated_at": utc_now(),
        "active_monitors": monitors,
        "total_snapshots": len(snapshots),
        "changed_snapshots": sum(1 for item in snapshots if item["change_score"] >= 0.02),
        "daily": sorted(daily.values(), key=lambda item: item["date"], reverse=True),
        "highlights": highlights
    }


def normalized_monitor_scope(value: str | None) -> str:
    return "single_page" if value == "single_page" else "domain"


def normalized_crawl_limit(value) -> int:
    try:
        return max(1, min(int(value), 200))
    except (TypeError, ValueError):
        return DEFAULT_CRAWL_LIMIT


def create_web_monitor(payload: dict) -> dict:
    url = normalize_url(payload.get("url", ""))
    brand_id = clean_text(payload.get("brand_id"))
    name = clean_text(payload.get("name")) or host_key(url) or url
    with get_db() as conn:
        if brand_id:
            brand = conn.execute("SELECT name FROM brands WHERE id = ?", (brand_id,)).fetchone()
            if brand and not clean_text(payload.get("name")):
                name = f"{brand['name']} {name}"
    now = utc_now()
    scope = normalized_monitor_scope(payload.get("scope"))
    crawl_limit = normalized_crawl_limit(payload.get("crawl_limit") or DEFAULT_CRAWL_LIMIT)
    monitor = {
        "id": str(uuid.uuid4()),
        "brand_id": brand_id or None,
        "name": name,
        "url": url,
        "scope": scope,
        "crawl_limit": crawl_limit,
        "icon_url": clean_text(payload.get("icon_url")) or resolved_icon_url(url),
        "status": payload.get("status") or "active",
        "cadence": "daily",
        "last_snapshot_at": None,
        "last_status": "pending",
        "last_error": "",
        "created_at": now,
        "updated_at": now
    }
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO web_monitors (
              id, brand_id, name, url, scope, crawl_limit, icon_url, status, cadence, last_snapshot_at, last_status,
              last_error, created_at, updated_at
            )
            VALUES (
              :id, :brand_id, :name, :url, :scope, :crawl_limit, :icon_url, :status, :cadence, :last_snapshot_at, :last_status,
              :last_error, :created_at, :updated_at
            )
            """,
            monitor
        )
        created = monitor_to_dict(conn, conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor["id"],)).fetchone())
    if payload.get("capture_now", True):
        capture_monitor_pages(monitor["id"])
        with get_db() as conn:
            created = monitor_to_dict(conn, conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor["id"],)).fetchone())
    return created


def update_web_monitor(monitor_id: str, payload: dict) -> dict:
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone()
        if not existing:
            raise ValueError("Monitor not found")
        name = clean_text(payload.get("name")) or existing["name"]
        url = normalize_url(payload.get("url") or existing["url"])
        status = payload.get("status") or existing["status"]
        scope = normalized_monitor_scope(payload.get("scope") or existing["scope"])
        crawl_limit = normalized_crawl_limit(payload.get("crawl_limit") or existing["crawl_limit"])
        icon_url = clean_text(payload.get("icon_url")) or existing["icon_url"] or resolved_icon_url(url)
        brand_id = clean_text(payload.get("brand_id")) or existing["brand_id"]
        conn.execute(
            """
            UPDATE web_monitors
            SET brand_id = ?, name = ?, url = ?, scope = ?, crawl_limit = ?, icon_url = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (brand_id or None, name, url, scope, crawl_limit, icon_url, status, utc_now(), monitor_id)
        )
        return monitor_to_dict(conn, conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone())


def capture_web_snapshot(monitor_id: str, target_url: str | None = None) -> dict:
    with get_db() as conn:
        monitor = conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone()
        if not monitor:
            raise ValueError("Monitor not found")

    capture_url = normalize_url(target_url or monitor["url"])
    snapshot_id = str(uuid.uuid4())
    snapshot_date = datetime.now(timezone.utc).date().isoformat()
    base_name = f"{snapshot_date}_{monitor_id[:8]}_{snapshot_id[:8]}"
    page = {
        "requested_url": capture_url,
        "final_url": capture_url,
        "title": monitor["name"],
        "html": "",
        "text": "",
        "meta": {},
        "anchors": [],
        "icon_url": monitor["icon_url"] or resolved_icon_url(capture_url),
        "anchor_count": 0,
        "json_ld_count": 0
    }
    status = "ready"
    fetch_error = ""
    try:
        page = fetch_snapshot_page(capture_url)
    except Exception as exc:
        status = "error"
        fetch_error = str(exc)

    text = page.get("text", "")
    text_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest() if text else ""
    page["text_hash"] = text_hash
    key = page_key(page.get("final_url") or capture_url)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    html_filename = f"{base_name}.html"
    html_content = page.get("html") or f"<pre>{html_escape(fetch_error or 'No HTML captured')}</pre>"
    (SNAPSHOT_DIR / html_filename).write_text(html_content, encoding="utf-8", errors="replace")
    screenshot_filename, screenshot_meta = capture_snapshot_image(
        page.get("final_url") or capture_url,
        page.get("title") or monitor["name"],
        text,
        base_name
    )

    with get_db() as conn:
        previous = conn.execute(
            """
            SELECT * FROM web_snapshots
            WHERE monitor_id = ? AND COALESCE(page_key, '') = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (monitor_id, key)
        ).fetchone()
        change_score, summary, changes = analyze_snapshot_change(page, previous)
        if fetch_error:
            summary = f"Capture warning: {fetch_error[:180]}"
        raw = {
            "meta": page.get("meta", {}),
            "anchor_count": page.get("anchor_count", 0),
            "json_ld_count": page.get("json_ld_count", 0),
            "page_key": key,
            "screenshot": screenshot_meta,
            "fetch_error": fetch_error
        }
        record = {
            "id": snapshot_id,
            "monitor_id": monitor_id,
            "snapshot_date": snapshot_date,
            "url": capture_url,
            "page_key": key,
            "final_url": page.get("final_url") or capture_url,
            "title": page.get("title") or monitor["name"],
            "status": status,
            "screenshot_path": screenshot_filename,
            "html_path": html_filename,
            "text_hash": text_hash,
            "text_excerpt": text[:50_000],
            "content_length": len(text),
            "change_score": change_score,
            "summary": summary,
            "changes_json": json.dumps(changes, ensure_ascii=False),
            "raw_json": json.dumps(raw, ensure_ascii=False),
            "created_at": utc_now()
        }
        conn.execute(
            """
            INSERT INTO web_snapshots (
              id, monitor_id, snapshot_date, url, page_key, final_url, title, status,
              screenshot_path, html_path, text_hash, text_excerpt, content_length,
              change_score, summary, changes_json, raw_json, created_at
            )
            VALUES (
              :id, :monitor_id, :snapshot_date, :url, :page_key, :final_url, :title, :status,
              :screenshot_path, :html_path, :text_hash, :text_excerpt, :content_length,
              :change_score, :summary, :changes_json, :raw_json, :created_at
            )
            """,
            record
        )
        conn.execute(
            """
            UPDATE web_monitors
            SET last_snapshot_at = ?, last_status = ?, last_error = ?,
                icon_url = COALESCE(NULLIF(icon_url, ''), ?), updated_at = ?
            WHERE id = ?
            """,
            (record["created_at"], status, fetch_error, page.get("icon_url") or resolved_icon_url(capture_url), record["created_at"], monitor_id)
        )
        saved = conn.execute(
            """
            SELECT web_snapshots.*, web_monitors.name AS monitor_name
            FROM web_snapshots
            JOIN web_monitors ON web_monitors.id = web_snapshots.monitor_id
            WHERE web_snapshots.id = ?
            """,
            (snapshot_id,)
        ).fetchone()
        return snapshot_to_dict(saved)


def capture_monitor_pages(monitor_id: str, job_id: str | None = None) -> list[dict]:
    with get_db() as conn:
        monitor = conn.execute("SELECT * FROM web_monitors WHERE id = ?", (monitor_id,)).fetchone()
        if not monitor:
            raise ValueError("Monitor not found")

    update_capture_job(
        job_id,
        status="running",
        phase="discovering",
        started_at=utc_now(),
        progress=3,
        message="Preparing page discovery"
    )
    if monitor["scope"] == "single_page":
        pages = [normalize_url(monitor["url"])]
    else:
        pages = discover_domain_pages(monitor["url"], monitor["crawl_limit"], job_id)
    if not pages:
        pages = [normalize_url(monitor["url"])]

    total = len(pages)
    snapshots = []
    update_capture_job(
        job_id,
        phase="capturing",
        total_pages=total,
        completed_pages=0,
        progress=20,
        message=f"Found {total} page{'s' if total != 1 else ''}"
    )
    for index, url in enumerate(pages, start=1):
        update_capture_job(
            job_id,
            phase="capturing",
            current_url=url,
            completed_pages=index - 1,
            progress=20 + int(((index - 1) / total) * 75),
            message=f"Capturing page {index}/{total}"
        )
        snapshot = capture_web_snapshot(monitor_id, url)
        snapshots.append(snapshot)
        update_capture_job(
            job_id,
            completed_pages=index,
            snapshot_ids=[item["id"] for item in snapshots],
            progress=20 + int((index / total) * 75),
            message=f"Captured page {index}/{total}"
        )
    return snapshots


def run_capture_job(job_id: str, monitor_id: str) -> None:
    try:
        snapshots = capture_monitor_pages(monitor_id, job_id)
        update_capture_job(
            job_id,
            status="complete",
            phase="complete",
            progress=100,
            completed_pages=len(snapshots),
            total_pages=len(snapshots),
            message=f"Done. Captured {len(snapshots)} page{'s' if len(snapshots) != 1 else ''}.",
            finished_at=utc_now()
        )
    except Exception as exc:
        update_capture_job(
            job_id,
            status="error",
            phase="error",
            error=str(exc),
            message=str(exc),
            finished_at=utc_now()
        )
        with get_db() as conn:
            conn.execute(
                "UPDATE web_monitors SET last_status = 'error', last_error = ?, updated_at = ? WHERE id = ?",
                (str(exc), utc_now(), monitor_id)
            )


def run_due_web_snapshots() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM web_monitors
            WHERE status = 'active'
              AND NOT EXISTS (
                SELECT 1 FROM web_snapshots
                WHERE web_snapshots.monitor_id = web_monitors.id
                  AND web_snapshots.snapshot_date = ?
              )
            ORDER BY created_at ASC
            """,
            (today,)
        ).fetchall()
    count = 0
    for row in rows:
        try:
            count += len(capture_monitor_pages(row["id"]))
        except Exception as exc:
            with get_db() as conn:
                conn.execute(
                    "UPDATE web_monitors SET last_status = 'error', last_error = ?, updated_at = ? WHERE id = ?",
                    (str(exc), utc_now(), row["id"])
                )
    return count


def web_monitor_scheduler() -> None:
    while True:
        try:
            run_due_web_snapshots()
        except Exception:
            pass
        time.sleep(max(MONITOR_SCHEDULER_SECONDS, 60))


def start_web_monitor_scheduler() -> None:
    if os.environ.get("MONITOR_SCHEDULER", "1") == "0":
        return
    thread = threading.Thread(target=web_monitor_scheduler, daemon=True)
    thread.start()


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
              monitor_id TEXT,
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

            CREATE TABLE IF NOT EXISTS voc_actions (
              id TEXT PRIMARY KEY,
              record_id TEXT,
              source_id TEXT,
              title TEXT NOT NULL,
              description TEXT,
              owner_team TEXT NOT NULL,
              priority TEXT NOT NULL,
              status TEXT NOT NULL,
              product TEXT,
              topic TEXT,
              due_at TEXT,
              closed_at TEXT,
              raw_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (record_id) REFERENCES records(id),
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

            CREATE TABLE IF NOT EXISTS sales_channels (
              id TEXT PRIMARY KEY,
              brand_name TEXT NOT NULL,
              product_name TEXT NOT NULL,
              platform TEXT NOT NULL,
              store_name TEXT NOT NULL,
              store_type TEXT NOT NULL,
              channel_url TEXT,
              region TEXT NOT NULL,
              sales_units INTEGER NOT NULL,
              previous_sales_units INTEGER NOT NULL,
              review_count INTEGER NOT NULL,
              rating REAL NOT NULL,
              revenue REAL NOT NULL,
              currency TEXT NOT NULL DEFAULT 'USD',
              snapshot_date TEXT NOT NULL,
              status TEXT NOT NULL,
              raw_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sales_channel_brands (
              id TEXT PRIMARY KEY,
              brand_profile_id TEXT,
              name TEXT NOT NULL,
              source_url TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              notes TEXT,
              raw_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sales_channel_links (
              id TEXT PRIMARY KEY,
              sales_brand_id TEXT NOT NULL,
              platform TEXT NOT NULL,
              name TEXT NOT NULL,
              url TEXT NOT NULL,
              canonical_url TEXT NOT NULL,
              store_type TEXT NOT NULL DEFAULT '自营店',
              region TEXT NOT NULL DEFAULT 'Global',
              status TEXT NOT NULL DEFAULT 'active',
              cadence TEXT NOT NULL DEFAULT 'manual',
              discovery_source TEXT,
              confidence REAL NOT NULL DEFAULT 0,
              notes TEXT,
              last_checked_at TEXT,
              last_status TEXT NOT NULL DEFAULT 'pending',
              last_error TEXT,
              raw_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (sales_brand_id) REFERENCES sales_channel_brands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS web_monitors (
              id TEXT PRIMARY KEY,
              brand_id TEXT,
              name TEXT NOT NULL,
              url TEXT NOT NULL,
              scope TEXT NOT NULL DEFAULT 'domain',
              crawl_limit INTEGER NOT NULL DEFAULT 20,
              icon_url TEXT,
              status TEXT NOT NULL,
              cadence TEXT NOT NULL,
              last_snapshot_at TEXT,
              last_status TEXT NOT NULL DEFAULT 'pending',
              last_error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS media_monitors (
              id TEXT PRIMARY KEY,
              brand_id TEXT,
              brand_name TEXT NOT NULL,
              query TEXT NOT NULL,
              region TEXT NOT NULL DEFAULT 'US',
              language TEXT NOT NULL DEFAULT 'en-US',
              status TEXT NOT NULL,
              cadence TEXT NOT NULL,
              last_scan_at TEXT,
              last_status TEXT NOT NULL DEFAULT 'pending',
              last_error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS marketing_links (
              id TEXT PRIMARY KEY,
              brand_id TEXT,
              brand_name TEXT NOT NULL,
              monitor_type TEXT NOT NULL,
              platform TEXT NOT NULL,
              name TEXT NOT NULL,
              url TEXT NOT NULL,
              status TEXT NOT NULL,
              cadence TEXT NOT NULL,
              last_collect_at TEXT,
              last_status TEXT NOT NULL DEFAULT 'pending',
              last_error TEXT,
              metrics_json TEXT NOT NULL DEFAULT '{}',
              raw_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS community_brands (
              id TEXT PRIMARY KEY,
              brand_profile_id TEXT,
              name TEXT NOT NULL,
              description TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS community_sources (
              id TEXT PRIMARY KEY,
              brand_id TEXT NOT NULL,
              platform TEXT NOT NULL,
              name TEXT NOT NULL,
              url TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              cadence TEXT NOT NULL DEFAULT 'manual',
              notes TEXT,
              last_collect_at TEXT,
              last_status TEXT NOT NULL DEFAULT 'pending',
              last_error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (brand_id) REFERENCES community_brands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS web_snapshots (
              id TEXT PRIMARY KEY,
              monitor_id TEXT NOT NULL,
              snapshot_date TEXT NOT NULL,
              url TEXT NOT NULL,
              page_key TEXT,
              final_url TEXT,
              title TEXT,
              status TEXT NOT NULL,
              screenshot_path TEXT,
              html_path TEXT,
              text_hash TEXT,
              text_excerpt TEXT,
              content_length INTEGER NOT NULL DEFAULT 0,
              change_score REAL NOT NULL DEFAULT 0,
              summary TEXT,
              changes_json TEXT NOT NULL DEFAULT '[]',
              raw_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              FOREIGN KEY (monitor_id) REFERENCES web_monitors(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_id);
            CREATE INDEX IF NOT EXISTS idx_records_type ON records(data_type);
            CREATE INDEX IF NOT EXISTS idx_records_sentiment ON records(sentiment);
            CREATE INDEX IF NOT EXISTS idx_records_occurred ON records(occurred_at);
            CREATE INDEX IF NOT EXISTS idx_voc_actions_status ON voc_actions(status);
            CREATE INDEX IF NOT EXISTS idx_voc_actions_owner ON voc_actions(owner_team);
            CREATE INDEX IF NOT EXISTS idx_voc_actions_record ON voc_actions(record_id);
            CREATE INDEX IF NOT EXISTS idx_brands_name ON brands(name);
            CREATE INDEX IF NOT EXISTS idx_sales_channels_brand ON sales_channels(brand_name);
            CREATE INDEX IF NOT EXISTS idx_sales_channels_product ON sales_channels(product_name);
            CREATE INDEX IF NOT EXISTS idx_sales_channels_platform ON sales_channels(platform);
            CREATE INDEX IF NOT EXISTS idx_sales_channel_brands_name ON sales_channel_brands(name);
            CREATE INDEX IF NOT EXISTS idx_sales_channel_links_brand ON sales_channel_links(sales_brand_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_channel_links_unique ON sales_channel_links(sales_brand_id, canonical_url);
            CREATE INDEX IF NOT EXISTS idx_media_monitors_status ON media_monitors(status);
            CREATE INDEX IF NOT EXISTS idx_marketing_links_type ON marketing_links(monitor_type);
            CREATE INDEX IF NOT EXISTS idx_marketing_links_brand ON marketing_links(brand_name);
            CREATE INDEX IF NOT EXISTS idx_marketing_links_platform ON marketing_links(platform);
            CREATE INDEX IF NOT EXISTS idx_community_sources_brand ON community_sources(brand_id);
            CREATE INDEX IF NOT EXISTS idx_community_sources_platform ON community_sources(platform);
            CREATE INDEX IF NOT EXISTS idx_web_monitors_status ON web_monitors(status);
            CREATE INDEX IF NOT EXISTS idx_web_snapshots_monitor ON web_snapshots(monitor_id);
            CREATE INDEX IF NOT EXISTS idx_web_snapshots_date ON web_snapshots(snapshot_date);
            """
        )
        ensure_column(conn, "records", "monitor_id", "TEXT")
        ensure_column(conn, "brands", "social_links_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "brands", "ecommerce_links_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "web_monitors", "scope", "TEXT NOT NULL DEFAULT 'domain'")
        ensure_column(conn, "web_monitors", "crawl_limit", "INTEGER NOT NULL DEFAULT 20")
        ensure_column(conn, "web_monitors", "icon_url", "TEXT")
        ensure_column(conn, "web_monitors", "brand_id", "TEXT")
        ensure_column(conn, "community_brands", "brand_profile_id", "TEXT")
        ensure_column(conn, "web_snapshots", "page_key", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_web_monitors_brand ON web_monitors(brand_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_community_brands_profile ON community_brands(brand_profile_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_web_snapshots_page ON web_snapshots(monitor_id, page_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_monitor ON records(monitor_id)")

        record_count = conn.execute("SELECT COUNT(*) AS count FROM records").fetchone()["count"]
        cleanup_demo_sales_channels(conn)

        now = utc_now()
        sources = [
            ("manual_csv", "Manual CSV", "manual", "Internal", "upload", "ready", "CSV and one-off imports"),
            ("google_news", "Google News RSS", "pr", "Google News", "rss", "ready", "Public news search feed for media coverage discovery"),
            ("meltwater", "Meltwater", "pr", "Meltwater", "api_export", "planned", "Earned media and PR mentions"),
            ("tiktok_social", "TikTok Social", "social", "TikTok", "api_or_export", "planned", "Owned and earned social comments"),
            ("instagram_social", "Instagram Social", "social", "Meta", "api_or_export", "planned", "Instagram posts, comments, and mentions"),
            ("social_public_link", "Public Social Link", "social", "Public Web", "oembed_or_page", "ready", "Public social profile and post metadata"),
            ("creator_public_link", "Public Creator Link", "creator", "Public Web", "oembed_or_page", "ready", "Public creator profile, video, and post metadata"),
            ("meta_ads", "Meta Ads Comments", "ads", "Meta", "api_or_export", "planned", "Paid social ad comments and creative feedback"),
            ("customer_email", "Customer Email", "email", "Support inbox", "imap_or_export", "planned", "Customer emails and inbound feedback"),
            ("app_store_reviews", "App Store Reviews", "app", "Apple/Google", "api_or_export", "planned", "APP reviews and experience feedback"),
            ("social_blade", "Social Blade", "social", "Social Blade", "business_api", "planned", "Creator and channel statistics"),
            ("nox", "NoxInfluencer", "creator", "Nox", "api_or_export", "planned", "Influencer discovery and profile reports"),
            ("xingtu", "巨量星图", "creator", "ByteDance", "api_or_export", "planned", "China creator campaign data"),
            ("amazon_reviews", "Amazon Reviews", "commerce", "Amazon", "export", "planned", "Marketplace reviews and ratings"),
            ("shopify_support", "Shopify Support", "commerce", "Shopify", "webhook", "planned", "Tickets, orders, and customer service"),
            ("reddit_search", "Reddit Search", "community", "Reddit", "api", "planned", "Subreddit posts, comments, and keyword monitoring"),
            ("discord_community", "Discord Community", "community", "Discord", "bot_or_export", "planned", "Channel messages, threads, reactions, and community support"),
            ("facebook_groups", "Facebook Groups", "community", "Meta", "graph_api_or_export", "planned", "Group posts, comments, engagement, and admin announcements"),
            ("owned_community", "Owned Community", "community", "Website", "crawler_or_webhook", "planned", "Forum, member community, comments, and owned Q&A")
        ]
        conn.executemany(
            """
            INSERT INTO sources (id, name, category, vendor, sync_mode, status, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            [(*source, now) for source in sources]
        )

        if record_count:
            return

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
                "source_id": "discord_community",
                "data_type": "community_post",
                "platform": "Discord",
                "title": "Feature request thread",
                "author": "member-238",
                "body": "The weekly summary is helpful, but the team keeps asking for faster alerts when competitor pricing changes.",
                "brand": "Our Brand",
                "competitor": "Brand B",
                "product": "Dashboard",
                "region": "US",
                "language": "en",
                "occurred_at": (datetime.now(timezone.utc) - timedelta(days=2, hours=6)).isoformat()
            },
            {
                "source_id": "facebook_groups",
                "data_type": "community_post",
                "platform": "Facebook",
                "title": "Group feedback on bundle",
                "author": "group member",
                "body": "Several people love the starter bundle, but shipping delays made the launch feel less reliable.",
                "brand": "Our Brand",
                "product": "Starter bundle",
                "region": "US",
                "language": "en",
                "occurred_at": (datetime.now(timezone.utc) - timedelta(days=1, hours=8)).isoformat()
            },
            {
                "source_id": "owned_community",
                "data_type": "community_post",
                "platform": "Owned",
                "title": "Member Q&A",
                "author": "community member",
                "body": "Can you add a clearer workflow for comparing products before renewal? The current dashboard is useful but hard to scan.",
                "brand": "Our Brand",
                "product": "Dashboard",
                "region": "US",
                "language": "en",
                "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=18)).isoformat()
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


def cleanup_demo_sales_channels(conn: sqlite3.Connection) -> None:
    demo_ids = [
        "sales-our-smart-bottle-amazon-owned",
        "sales-our-smart-bottle-amazon-partner",
        "sales-our-smart-bottle-site-owned",
        "sales-our-starter-bundle-site-owned",
        "sales-our-starter-bundle-amazon-owned",
        "sales-our-starter-bundle-amazon-partner",
        "sales-our-starter-bundle-tiktok-partner",
        "sales-brand-a-skincare-amazon-owned",
        "sales-brand-a-skincare-site-owned",
        "sales-brand-a-skincare-marketplace-partner"
    ]
    placeholders = ",".join("?" for _ in demo_ids)
    conn.execute(f"DELETE FROM sales_channels WHERE id IN ({placeholders})", demo_ids)


def seed_sales_channels(conn: sqlite3.Connection) -> None:
    now = utc_now()
    snapshot_date = datetime.now(timezone.utc).date().isoformat()
    rows = [
        {
            "id": "sales-our-smart-bottle-amazon-owned",
            "brand_name": "Our Brand",
            "product_name": "Smart bottle",
            "platform": "Amazon",
            "store_name": "Amazon Brand Store",
            "store_type": "自营店",
            "channel_url": "https://www.amazon.com/dp/B0SMART001",
            "region": "US",
            "sales_units": 1240,
            "previous_sales_units": 1118,
            "review_count": 286,
            "rating": 4.4,
            "revenue": 86720,
            "currency": "USD",
            "snapshot_date": snapshot_date,
            "status": "增长",
            "raw_json": json.dumps({"monitor_scope": "amazon_owned_store"}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "sales-our-smart-bottle-amazon-partner",
            "brand_name": "Our Brand",
            "product_name": "Smart bottle",
            "platform": "Amazon",
            "store_name": "Outdoor Pro",
            "store_type": "渠道店",
            "channel_url": "https://www.amazon.com/s?k=smart+bottle",
            "region": "US",
            "sales_units": 520,
            "previous_sales_units": 566,
            "review_count": 119,
            "rating": 4.0,
            "revenue": 35360,
            "currency": "USD",
            "snapshot_date": snapshot_date,
            "status": "下滑",
            "raw_json": json.dumps({"monitor_scope": "amazon_partner_store"}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "sales-our-smart-bottle-site-owned",
            "brand_name": "Our Brand",
            "product_name": "Smart bottle",
            "platform": "独立站",
            "store_name": "品牌官网",
            "store_type": "自营店",
            "channel_url": "https://example.com/products/smart-bottle",
            "region": "US",
            "sales_units": 880,
            "previous_sales_units": 812,
            "review_count": 74,
            "rating": 4.7,
            "revenue": 70400,
            "currency": "USD",
            "snapshot_date": snapshot_date,
            "status": "增长",
            "raw_json": json.dumps({"monitor_scope": "owned_dtc_site"}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "sales-our-starter-bundle-site-owned",
            "brand_name": "Our Brand",
            "product_name": "Starter bundle",
            "platform": "独立站",
            "store_name": "品牌官网",
            "store_type": "自营店",
            "channel_url": "https://example.com/products/starter-bundle",
            "region": "US",
            "sales_units": 760,
            "previous_sales_units": 690,
            "review_count": 64,
            "rating": 4.6,
            "revenue": 65360,
            "currency": "USD",
            "snapshot_date": snapshot_date,
            "status": "增长",
            "raw_json": json.dumps({"monitor_scope": "owned_dtc_site"}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "sales-our-starter-bundle-amazon-owned",
            "brand_name": "Our Brand",
            "product_name": "Starter bundle",
            "platform": "Amazon",
            "store_name": "Amazon Brand Store",
            "store_type": "自营店",
            "channel_url": "https://www.amazon.com/s?k=starter+bundle",
            "region": "US",
            "sales_units": 690,
            "previous_sales_units": 612,
            "review_count": 142,
            "rating": 4.3,
            "revenue": 57960,
            "currency": "USD",
            "snapshot_date": snapshot_date,
            "status": "增长",
            "raw_json": json.dumps({"monitor_scope": "amazon_owned_store"}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "sales-our-starter-bundle-amazon-partner",
            "brand_name": "Our Brand",
            "product_name": "Starter bundle",
            "platform": "Amazon",
            "store_name": "Beauty Hub",
            "store_type": "渠道店",
            "channel_url": "https://www.amazon.com/s?k=starter+bundle",
            "region": "US",
            "sales_units": 430,
            "previous_sales_units": 452,
            "review_count": 88,
            "rating": 4.1,
            "revenue": 36120,
            "currency": "USD",
            "snapshot_date": snapshot_date,
            "status": "下滑",
            "raw_json": json.dumps({"monitor_scope": "amazon_partner_store"}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "sales-our-starter-bundle-tiktok-partner",
            "brand_name": "Our Brand",
            "product_name": "Starter bundle",
            "platform": "TikTok Shop",
            "store_name": "Creator Live Store",
            "store_type": "渠道店",
            "channel_url": "https://www.tiktok.com/shop",
            "region": "US",
            "sales_units": 390,
            "previous_sales_units": 340,
            "review_count": 51,
            "rating": 4.5,
            "revenue": 32760,
            "currency": "USD",
            "snapshot_date": snapshot_date,
            "status": "增长",
            "raw_json": json.dumps({"monitor_scope": "creator_channel_store"}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "sales-brand-a-skincare-amazon-owned",
            "brand_name": "Brand A",
            "product_name": "Skincare kit",
            "platform": "Amazon",
            "store_name": "Amazon Brand Store",
            "store_type": "自营店",
            "channel_url": "https://www.amazon.com/s?k=skincare+kit",
            "region": "US",
            "sales_units": 930,
            "previous_sales_units": 880,
            "review_count": 204,
            "rating": 4.2,
            "revenue": 74400,
            "currency": "USD",
            "snapshot_date": snapshot_date,
            "status": "增长",
            "raw_json": json.dumps({"monitor_scope": "amazon_owned_store"}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "sales-brand-a-skincare-site-owned",
            "brand_name": "Brand A",
            "product_name": "Skincare kit",
            "platform": "独立站",
            "store_name": "品牌官网",
            "store_type": "自营店",
            "channel_url": "https://example.com/brand-a/skincare-kit",
            "region": "US",
            "sales_units": 610,
            "previous_sales_units": 650,
            "review_count": 57,
            "rating": 4.0,
            "revenue": 51850,
            "currency": "USD",
            "snapshot_date": snapshot_date,
            "status": "下滑",
            "raw_json": json.dumps({"monitor_scope": "owned_dtc_site"}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        },
        {
            "id": "sales-brand-a-skincare-marketplace-partner",
            "brand_name": "Brand A",
            "product_name": "Skincare kit",
            "platform": "Walmart",
            "store_name": "Marketplace Partner",
            "store_type": "渠道店",
            "channel_url": "https://www.walmart.com/search?q=skincare+kit",
            "region": "US",
            "sales_units": 420,
            "previous_sales_units": 398,
            "review_count": 46,
            "rating": 4.3,
            "revenue": 33600,
            "currency": "USD",
            "snapshot_date": snapshot_date,
            "status": "稳定",
            "raw_json": json.dumps({"monitor_scope": "marketplace_partner_store"}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        }
    ]
    conn.executemany(
        """
        INSERT INTO sales_channels (
          id, brand_name, product_name, platform, store_name, store_type, channel_url,
          region, sales_units, previous_sales_units, review_count, rating, revenue,
          currency, snapshot_date, status, raw_json, created_at, updated_at
        )
        VALUES (
          :id, :brand_name, :product_name, :platform, :store_name, :store_type, :channel_url,
          :region, :sales_units, :previous_sales_units, :review_count, :rating, :revenue,
          :currency, :snapshot_date, :status, :raw_json, :created_at, :updated_at
        )
        ON CONFLICT(id) DO NOTHING
        """,
        rows
    )


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
        "monitor_id": payload.get("monitor_id"),
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
          id, source_id, external_id, monitor_id, data_type, platform, title, author, body, url,
          brand, competitor, product, region, language, occurred_at, sentiment,
          sentiment_score, intent, topics_json, raw_json, created_at
        )
        VALUES (
          :id, :source_id, :external_id, :monitor_id, :data_type, :platform, :title, :author, :body, :url,
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


def list_sales_channels(params: dict[str, list[str]]) -> list[dict]:
    columns = {
        "brand_name": "brand_name",
        "product_name": "product_name",
        "platform": "platform",
        "store_type": "store_type"
    }
    clauses = []
    values = []
    for param, column in columns.items():
        value = params.get(param, [""])[0].strip()
        if value:
            clauses.append(f"{column} = ?")
            values.append(value)
    query = params.get("q", [""])[0].strip()
    if query:
        clauses.append("(brand_name LIKE ? OR product_name LIKE ? OR platform LIKE ? OR store_name LIKE ?)")
        like = f"%{query}%"
        values.extend([like, like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT *
        FROM sales_channels
        {where}
        ORDER BY brand_name, product_name, platform, store_type, store_name
    """
    with get_db() as conn:
        return [row_to_dict(row) for row in conn.execute(sql, values)]


def sales_platform_label(platform: str) -> str:
    return SALES_PLATFORM_LABELS.get(platform, platform or "其他渠道")


def detect_sales_platform(url: str, fallback: str = "") -> str:
    try:
        host = host_key(url)
    except ValueError:
        host = ""
    raw_fallback = clean_text(fallback).lower()
    compact = compact_key(fallback)
    if "亚马逊" in raw_fallback:
        return "amazon"
    if "国际站" in raw_fallback or "阿里巴巴" in raw_fallback:
        return "alibaba"
    if "独立站" in raw_fallback or "官网" in raw_fallback:
        return "owned_site"
    platform_aliases = {
        "amazon": "amazon",
        "亚马逊": "amazon",
        "alibaba": "alibaba",
        "国际站": "alibaba",
        "alibabacom": "alibaba",
        "aliexpress": "aliexpress",
        "walmart": "walmart",
        "tiktokshop": "tiktok_shop",
        "shopify": "shopify",
        "temu": "temu",
        "ebay": "ebay",
        "target": "target",
        "shopee": "shopee",
        "lazada": "lazada",
        "shein": "shein",
        "独立站": "owned_site",
        "ownedsite": "owned_site",
        "dtc": "owned_site"
    }
    if compact in platform_aliases:
        return platform_aliases[compact]
    for platform, hosts in SALES_PLATFORM_HOSTS.items():
        if any(host == item or host.endswith(f".{item}") for item in hosts):
            if platform == "tiktok_shop" and "/shop" not in urlparse(url).path.lower() and "shop." not in host:
                continue
            return platform
    return "owned_site" if host else "other"


def sales_store_type(platform: str, value: str = "") -> str:
    cleaned = clean_text(value)
    if cleaned:
        return cleaned
    return "自营店" if platform in {"owned_site", "shopify", "amazon"} else "渠道店"


def sales_region_from_url(url: str, platform: str, value: str = "") -> str:
    cleaned = clean_text(value)
    if cleaned:
        return cleaned.upper()
    if platform == "amazon":
        return amazon_market(normalize_url(url)) or "US"
    host = host_key(url)
    suffix = host.rsplit(".", 1)[-1].upper() if "." in host else ""
    if suffix in {"US", "UK", "CA", "DE", "FR", "IT", "ES", "AU", "JP", "SG", "MY", "PH", "VN", "TH"}:
        return suffix
    return "Global"


def sales_link_name(platform: str, url: str, value: str = "", brand_name: str = "") -> str:
    cleaned = clean_text(value)
    if cleaned:
        return cleaned
    if platform == "owned_site":
        return "品牌独立站"
    if platform == "amazon" and extract_asin(url):
        return "Amazon 商品页"
    if brand_name:
        return f"{sales_platform_label(platform)} - {brand_name}"
    return sales_platform_label(platform)


def sales_brand_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["raw"] = json.loads(item.pop("raw_json") or "{}")
    item["links"] = []
    item["link_count"] = 0
    item["active_link_count"] = 0
    item["platforms"] = []
    return item


def sales_link_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["raw"] = json.loads(item.pop("raw_json") or "{}")
    item["platform_label"] = sales_platform_label(item.get("platform", ""))
    return item


def list_sales_channel_links(conn: sqlite3.Connection, sales_brand_id: str = "") -> list[dict]:
    clauses = []
    values = []
    if sales_brand_id:
        clauses.append("sales_brand_id = ?")
        values.append(sales_brand_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT *
        FROM sales_channel_links
        {where}
        ORDER BY updated_at DESC
        """,
        values
    ).fetchall()
    return [sales_link_to_dict(row) for row in rows]


def list_sales_channel_brands(params: dict[str, list[str]] | None = None) -> list[dict]:
    params = params or {}
    query = params.get("q", [""])[0].strip() if params else ""
    clauses = []
    values = []
    if query:
        clauses.append("(name LIKE ? OR notes LIKE ?)")
        like = f"%{query}%"
        values.extend([like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_db() as conn:
        brands = [sales_brand_to_dict(row) for row in conn.execute(
            f"SELECT * FROM sales_channel_brands {where} ORDER BY updated_at DESC",
            values
        )]
        for brand in brands:
            links = list_sales_channel_links(conn, brand["id"])
            brand["links"] = links
            brand["link_count"] = len(links)
            brand["active_link_count"] = sum(1 for link in links if link.get("status") == "active")
            brand["platforms"] = sorted({link.get("platform_label") or link.get("platform") for link in links})
        return brands


def infer_sales_brand_from_payload(payload: dict) -> tuple[str, str, str, dict]:
    brand_profile_id = clean_text(payload.get("brand_profile_id") or payload.get("brand_id"))
    name = clean_text(payload.get("name") or payload.get("brand_name"))
    source_url = clean_text(payload.get("source_url") or payload.get("seed_url") or payload.get("url"))
    raw: dict = {}
    with get_db() as conn:
        if brand_profile_id:
            brand = conn.execute("SELECT * FROM brands WHERE id = ?", (brand_profile_id,)).fetchone()
            if brand:
                brand_payload = row_to_dict(brand)
                name = name or brand_payload.get("name") or ""
                source_url = source_url or brand_payload.get("official_website") or brand_payload.get("amazon_url") or brand_payload.get("source_url") or ""
                raw["brand_profile"] = {
                    "id": brand_profile_id,
                    "name": brand_payload.get("name"),
                    "source_kind": brand_payload.get("source_kind")
                }
    if source_url and (not name or payload.get("auto_analyze")):
        try:
            draft = analyze_brand_url(source_url)
            name = name or clean_text(draft.get("name"))
            raw["analysis"] = draft
        except Exception as exc:
            raw["analysis_error"] = str(exc)
    if not name and source_url:
        name = title_to_brand("", host_key(source_url))
    return brand_profile_id, name, source_url, raw


def upsert_sales_channel_brand(payload: dict, brand_id: str = "") -> dict:
    brand_profile_id, name, source_url, inferred_raw = infer_sales_brand_from_payload(payload)
    if not name:
        raise ValueError("Sales channel brand name is required")
    now = utc_now()
    next_id = brand_id or payload.get("id") or str(uuid.uuid4())
    status = clean_text(payload.get("status") or "active")
    if status not in {"active", "paused"}:
        status = "active"
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    raw = {**inferred_raw, **raw}
    with get_db() as conn:
        existing = conn.execute("SELECT created_at FROM sales_channel_brands WHERE id = ?", (next_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO sales_channel_brands (
              id, brand_profile_id, name, source_url, status, notes, raw_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              brand_profile_id = excluded.brand_profile_id,
              name = excluded.name,
              source_url = excluded.source_url,
              status = excluded.status,
              notes = excluded.notes,
              raw_json = excluded.raw_json,
              updated_at = excluded.updated_at
            """,
            (
                next_id,
                brand_profile_id or None,
                name,
                source_url or "",
                status,
                clean_text(payload.get("notes")),
                json.dumps(raw or payload, ensure_ascii=False),
                existing["created_at"] if existing else now,
                now
            )
        )
        brand = sales_brand_to_dict(conn.execute("SELECT * FROM sales_channel_brands WHERE id = ?", (next_id,)).fetchone())
        brand["links"] = list_sales_channel_links(conn, next_id)
        brand["link_count"] = len(brand["links"])
        brand["active_link_count"] = sum(1 for link in brand["links"] if link.get("status") == "active")
        brand["platforms"] = sorted({link.get("platform_label") or link.get("platform") for link in brand["links"]})
        return brand


def sales_link_payload(payload: dict, link_id: str = "") -> dict:
    sales_brand_id = clean_text(payload.get("sales_brand_id") or payload.get("brand_id"))
    url = normalize_url(clean_text(payload.get("url")))
    platform = detect_sales_platform(url, clean_text(payload.get("platform")))
    now = utc_now()
    brand_name = clean_text(payload.get("brand_name"))
    if sales_brand_id and not brand_name:
        with get_db() as conn:
            brand = conn.execute("SELECT name FROM sales_channel_brands WHERE id = ?", (sales_brand_id,)).fetchone()
            if brand:
                brand_name = brand["name"]
    canonical = canonical_url(url) or url.lower()
    return {
        "id": link_id or payload.get("id") or str(uuid.uuid4()),
        "sales_brand_id": sales_brand_id,
        "platform": platform,
        "name": sales_link_name(platform, url, clean_text(payload.get("name")), brand_name),
        "url": url,
        "canonical_url": canonical,
        "store_type": sales_store_type(platform, clean_text(payload.get("store_type"))),
        "region": sales_region_from_url(url, platform, clean_text(payload.get("region"))),
        "status": clean_text(payload.get("status") or "active"),
        "cadence": clean_text(payload.get("cadence") or "manual"),
        "discovery_source": clean_text(payload.get("discovery_source") or "manual"),
        "confidence": float(payload.get("confidence") or 0),
        "notes": clean_text(payload.get("notes")),
        "last_checked_at": payload.get("last_checked_at"),
        "last_status": clean_text(payload.get("last_status") or "pending"),
        "last_error": clean_text(payload.get("last_error")),
        "raw_json": json.dumps(payload.get("raw") if isinstance(payload.get("raw"), dict) else payload, ensure_ascii=False),
        "created_at": now,
        "updated_at": now
    }


def upsert_sales_channel_link(payload: dict, link_id: str = "") -> dict:
    link = sales_link_payload(payload, link_id)
    if not link["sales_brand_id"]:
        raise ValueError("Sales brand is required")
    if link["status"] not in {"active", "paused"}:
        link["status"] = "active"
    with get_db() as conn:
        brand = conn.execute("SELECT id FROM sales_channel_brands WHERE id = ?", (link["sales_brand_id"],)).fetchone()
        if not brand:
            raise ValueError("Sales channel brand not found")
        existing = conn.execute("SELECT created_at FROM sales_channel_links WHERE id = ?", (link["id"],)).fetchone()
        if not existing:
            duplicate = conn.execute(
                "SELECT id, created_at FROM sales_channel_links WHERE sales_brand_id = ? AND canonical_url = ? LIMIT 1",
                (link["sales_brand_id"], link["canonical_url"])
            ).fetchone()
            if duplicate:
                link["id"] = duplicate["id"]
                existing = duplicate
        if existing:
            link["created_at"] = existing["created_at"]
        conn.execute(
            """
            INSERT INTO sales_channel_links (
              id, sales_brand_id, platform, name, url, canonical_url, store_type, region, status,
              cadence, discovery_source, confidence, notes, last_checked_at, last_status, last_error,
              raw_json, created_at, updated_at
            )
            VALUES (
              :id, :sales_brand_id, :platform, :name, :url, :canonical_url, :store_type, :region, :status,
              :cadence, :discovery_source, :confidence, :notes, :last_checked_at, :last_status, :last_error,
              :raw_json, :created_at, :updated_at
            )
            ON CONFLICT(id) DO UPDATE SET
              platform = excluded.platform,
              name = excluded.name,
              url = excluded.url,
              canonical_url = excluded.canonical_url,
              store_type = excluded.store_type,
              region = excluded.region,
              status = excluded.status,
              cadence = excluded.cadence,
              discovery_source = excluded.discovery_source,
              confidence = excluded.confidence,
              notes = excluded.notes,
              last_checked_at = excluded.last_checked_at,
              last_status = excluded.last_status,
              last_error = excluded.last_error,
              raw_json = excluded.raw_json,
              updated_at = excluded.updated_at
            """,
            link
        )
        return sales_link_to_dict(conn.execute("SELECT * FROM sales_channel_links WHERE id = ?", (link["id"],)).fetchone())


def delete_sales_channel_brand(brand_id: str) -> dict:
    with get_db() as conn:
        conn.execute("DELETE FROM sales_channel_links WHERE sales_brand_id = ?", (brand_id,))
        cursor = conn.execute("DELETE FROM sales_channel_brands WHERE id = ?", (brand_id,))
        return {"deleted": cursor.rowcount}


def delete_sales_channel_link(link_id: str) -> dict:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM sales_channel_links WHERE id = ?", (link_id,))
        return {"deleted": cursor.rowcount}


def unique_sales_candidates(candidates: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for candidate in candidates:
        url = canonical_url(candidate.get("url")) or clean_text(candidate.get("url")).lower()
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(candidate)
    return result


def sales_candidate_from_url(url: str, brand_name: str = "", source: str = "scan", confidence: float = 0.72, name: str = "") -> dict:
    normalized = normalize_url(url)
    platform = detect_sales_platform(normalized)
    return {
        "platform": platform,
        "platform_label": sales_platform_label(platform),
        "name": sales_link_name(platform, normalized, name, brand_name),
        "url": normalized,
        "canonical_url": canonical_url(normalized) or normalized.lower(),
        "store_type": sales_store_type(platform),
        "region": sales_region_from_url(normalized, platform),
        "discovery_source": source,
        "confidence": confidence
    }


def discover_sales_channel_candidates(payload: dict) -> dict:
    seed_url = normalize_url(clean_text(payload.get("url") or payload.get("seed_url")))
    brand_name = clean_text(payload.get("brand_name"))
    analysis: dict = {}
    candidates: list[dict] = []
    evidence = ["Parsed seed URL"]
    try:
        analysis = analyze_brand_url(seed_url)
        brand_name = brand_name or clean_text(analysis.get("name"))
        evidence.extend(analysis.get("evidence") or [])
    except Exception as exc:
        evidence.append(f"Brand analysis failed: {exc}")

    candidates.append(sales_candidate_from_url(seed_url, brand_name, "seed_url", 0.92))
    for url in [
        analysis.get("official_website"),
        analysis.get("amazon_url"),
        *((analysis.get("ecommerce_links") or {}).values() if isinstance(analysis.get("ecommerce_links"), dict) else [])
    ]:
        if url:
            candidates.append(sales_candidate_from_url(url, brand_name, "brand_metadata", 0.86))

    try:
        metadata, final_url, _html = fetch_metadata(seed_url)
        json_items = flatten_json_ld(metadata.json_ld)
        social_links, ecommerce_links = discover_platform_links(final_url, metadata, json_items)
        for url in ecommerce_links.values():
            candidates.append(sales_candidate_from_url(url, brand_name, "page_link", 0.82))
        for href in metadata.anchors[:250]:
            url = clean_external_link(final_url, href)
            if not url:
                continue
            platform = detect_sales_platform(url)
            if platform in {"amazon", "alibaba", "aliexpress", "walmart", "tiktok_shop", "shopify", "temu", "ebay", "target", "shopee", "lazada", "shein"}:
                candidates.append(sales_candidate_from_url(url, brand_name, "page_link", 0.78))
        if social_links:
            evidence.append(f"Detected {len(social_links)} social links; kept them out of sales channels")
    except Exception as exc:
        evidence.append(f"Link scan failed: {exc}")

    if not brand_name:
        brand_name = title_to_brand("", host_key(seed_url))
    return {
        "brand": {
            "name": brand_name,
            "source_url": seed_url,
            "analysis": analysis
        },
        "candidates": unique_sales_candidates(candidates),
        "evidence": evidence
    }


def create_sales_channel_brand_from_discovery(payload: dict) -> dict:
    discovery = discover_sales_channel_candidates(payload)
    brand = upsert_sales_channel_brand({
        "name": discovery["brand"]["name"],
        "source_url": discovery["brand"]["source_url"],
        "auto_analyze": False,
        "raw": {"discovery": discovery}
    })
    selected = payload.get("candidates")
    candidates = selected if isinstance(selected, list) and selected else discovery["candidates"]
    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate.get("url"):
            continue
        try:
            upsert_sales_channel_link({
                **candidate,
                "sales_brand_id": brand["id"],
                "discovery_source": candidate.get("discovery_source") or "discovery",
                "raw": candidate
            })
        except Exception:
            continue
    with get_db() as conn:
        row = conn.execute("SELECT * FROM sales_channel_brands WHERE id = ?", (brand["id"],)).fetchone()
        saved = sales_brand_to_dict(row)
        links = list_sales_channel_links(conn, brand["id"])
        saved["links"] = links
        saved["link_count"] = len(links)
        saved["active_link_count"] = sum(1 for link in links if link.get("status") == "active")
        saved["platforms"] = sorted({link.get("platform_label") or link.get("platform") for link in links})
        return saved


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


def first_param(params: dict[str, list[str]], key: str, default: str = "") -> str:
    return clean_text(str(params.get(key, [default])[0] if params.get(key) else default))


def parse_int_param(params: dict[str, list[str]], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(first_param(params, key, str(default)) or default)
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def parse_range_days(params: dict[str, list[str]], default: int = 30) -> int:
    return parse_int_param(params, "days", default, 1, 365)


def record_filter_clauses(
    params: dict[str, list[str]],
    default_days: int | None = None,
    include_days: bool = True
) -> tuple[list[str], list[str], int | None]:
    clauses = []
    values = []
    days: int | None = None

    if include_days:
        start_date = first_param(params, "start_date")
        end_date = first_param(params, "end_date")
        if start_date:
            clauses.append("substr(records.occurred_at, 1, 10) >= ?")
            values.append(start_date[:10])
        if end_date:
            clauses.append("substr(records.occurred_at, 1, 10) <= ?")
            values.append(end_date[:10])
        if not start_date and not end_date and (first_param(params, "days") or default_days is not None):
            days = parse_range_days(params, default_days or 30)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days - 1, 0))).date().isoformat()
            clauses.append("substr(records.occurred_at, 1, 10) >= ?")
            values.append(cutoff)

    field_map = {
        "source_id": "records.source_id",
        "data_type": "records.data_type",
        "sentiment": "records.sentiment",
        "intent": "records.intent",
        "product": "records.product",
        "brand": "records.brand",
        "region": "records.region",
        "channel": "sources.category"
    }
    for field, column in field_map.items():
        value = first_param(params, field)
        if value:
            clauses.append(f"{column} = ?")
            values.append(value)

    query = first_param(params, "q")
    if query:
        clauses.append(
            """
            (
              records.body LIKE ?
              OR records.title LIKE ?
              OR records.brand LIKE ?
              OR records.competitor LIKE ?
              OR records.product LIKE ?
            )
            """
        )
        like = f"%{query}%"
        values.extend([like, like, like, like, like])
    return clauses, values, days


def query_records(params: dict[str, list[str]], start_date: str = "", end_date: str = "", limit: int = 120) -> list[dict]:
    clauses, values, _ = record_filter_clauses(params, include_days=not bool(start_date or end_date))
    if start_date:
        clauses.append("substr(records.occurred_at, 1, 10) >= ?")
        values.append(start_date[:10])
    if end_date:
        clauses.append("substr(records.occurred_at, 1, 10) <= ?")
        values.append(end_date[:10])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT records.*, sources.name AS source_name, sources.category AS source_category
        FROM records JOIN sources ON sources.id = records.source_id
        {where}
        ORDER BY occurred_at DESC
        LIMIT ?
    """
    values.append(limit)
    with get_db() as conn:
        return [row_to_dict(row) for row in conn.execute(sql, values)]


def list_records(params: dict[str, list[str]]) -> list[dict]:
    limit = parse_int_param(params, "limit", 120, 1, 1000)
    return query_records(params, limit=limit)


COMMUNITY_PLATFORMS = {"reddit", "discord", "facebook", "owned"}

COMMUNITY_SOURCE_IDS = {
    "reddit": "reddit_search",
    "discord": "discord_community",
    "facebook": "facebook_groups",
    "owned": "owned_community"
}


def normalize_community_platform(value: str | None, url: str = "") -> str:
    platform = compact_key(value)
    if platform in {"facebookgroup", "facebookgroups", "meta"}:
        platform = "facebook"
    if platform in {"selfhosted", "website", "forum", "ownedcommunity"}:
        platform = "owned"
    if platform in COMMUNITY_PLATFORMS:
        return platform
    host = host_key(url)
    if "reddit.com" in host:
        return "reddit"
    if "discord.com" in host or "discord.gg" in host:
        return "discord"
    if "facebook.com" in host or "fb.com" in host:
        return "facebook"
    return "owned"


def community_platform_label(platform: str) -> str:
    return {
        "reddit": "Reddit",
        "discord": "Discord",
        "facebook": "Facebook Group",
        "owned": "Owned Community"
    }.get(platform, platform.title())


def community_source_id(platform: str) -> str:
    return COMMUNITY_SOURCE_IDS.get(platform, "owned_community")


def community_brand_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def community_stats_records(conn: sqlite3.Connection, brand_id: str = "", source_id: str = "") -> list[dict]:
    clauses = ["records.data_type = 'community_post'", "records.monitor_id IS NOT NULL"]
    values: list[str] = []
    if brand_id:
        clauses.append("community_sources.brand_id = ?")
        values.append(brand_id)
    if source_id:
        clauses.append("records.monitor_id = ?")
        values.append(source_id)
    where = f"WHERE {' AND '.join(clauses)}"
    rows = conn.execute(
        f"""
        SELECT records.sentiment, records.topics_json, records.occurred_at
        FROM records
        JOIN community_sources ON community_sources.id = records.monitor_id
        {where}
        ORDER BY records.occurred_at DESC
        """,
        values
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def community_source_to_dict(conn: sqlite3.Connection, row: sqlite3.Row, include_records: bool = False) -> dict:
    item = dict(row)
    stats_records = community_stats_records(conn, source_id=item["id"])
    display_records = []
    for record in conn.execute(
        """
        SELECT records.*, sources.name AS source_name, sources.category AS source_category
        FROM records JOIN sources ON sources.id = records.source_id
        WHERE records.monitor_id = ? AND records.data_type = 'community_post'
        ORDER BY records.occurred_at DESC
        LIMIT 200
        """,
        (item["id"],)
    ):
        scoped_record = row_to_dict(record)
        scoped_record["community_source_name"] = item["name"]
        scoped_record["community_platform"] = item["platform"]
        scoped_record["community_brand_id"] = item["brand_id"]
        display_records.append(scoped_record)
    item["platform_label"] = community_platform_label(item["platform"])
    item["record_count"] = len(stats_records)
    item["negative_count"] = len(records_negative(stats_records))
    item["negative_rate"] = negative_rate(stats_records)
    item["top_topics"] = compact_topics(stats_records, 6)
    item["latest_record_at"] = stats_records[0]["occurred_at"] if stats_records else ""
    if include_records:
        item["records"] = display_records
    return item


def list_community_sources(conn: sqlite3.Connection, brand_id: str = "", include_records: bool = False) -> list[dict]:
    clauses = []
    values = []
    if brand_id:
        clauses.append("brand_id = ?")
        values.append(brand_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT *
        FROM community_sources
        {where}
        ORDER BY updated_at DESC, created_at DESC
        """,
        values
    ).fetchall()
    return [community_source_to_dict(conn, row, include_records=include_records) for row in rows]


def list_community_brands(params: dict[str, list[str]] | None = None) -> list[dict]:
    params = params or {}
    brand_profile_id = first_param(params, "brand_profile_id")
    clauses = []
    values: list[str] = []
    if brand_profile_id:
        clauses.append("brand_profile_id = ?")
        values.append(brand_profile_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_db() as conn:
        brands = [
            community_brand_to_dict(row)
            for row in conn.execute(
                f"SELECT * FROM community_brands {where} ORDER BY updated_at DESC",
                values
            )
        ]
        for brand in brands:
            sources = list_community_sources(conn, brand["id"], include_records=False)
            records = community_stats_records(conn, brand_id=brand["id"])
            brand["sources"] = sources
            brand["source_count"] = len(sources)
            brand["record_count"] = len(records)
            brand["negative_count"] = len(records_negative(records))
            brand["negative_rate"] = negative_rate(records)
            brand["top_topics"] = compact_topics(records, 6)
    return brands


def upsert_community_brand(payload: dict, brand_id: str = "") -> dict:
    name = clean_text(payload.get("name"))
    if not name:
        raise ValueError("Community brand name is required")
    now = utc_now()
    next_id = brand_id or payload.get("id") or str(uuid.uuid4())
    brand_profile_id = clean_text(payload.get("brand_profile_id"))
    status = clean_text(payload.get("status") or "active")
    if status not in {"active", "paused"}:
        status = "active"
    with get_db() as conn:
        existing = conn.execute("SELECT created_at FROM community_brands WHERE id = ?", (next_id,)).fetchone()
        existing_full = conn.execute("SELECT * FROM community_brands WHERE id = ?", (next_id,)).fetchone()
        if not brand_profile_id and existing_full:
            brand_profile_id = existing_full["brand_profile_id"] or ""
        conn.execute(
            """
            INSERT INTO community_brands (id, brand_profile_id, name, description, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              brand_profile_id = excluded.brand_profile_id,
              name = excluded.name,
              description = excluded.description,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                next_id,
                brand_profile_id or None,
                name,
                clean_text(payload.get("description")),
                status,
                existing["created_at"] if existing else now,
                now
            )
        )
        row = conn.execute("SELECT * FROM community_brands WHERE id = ?", (next_id,)).fetchone()
        brand = community_brand_to_dict(row)
        brand["sources"] = list_community_sources(conn, next_id)
        return brand


def delete_community_brand(brand_id: str) -> dict:
    with get_db() as conn:
        source_rows = conn.execute("SELECT id FROM community_sources WHERE brand_id = ?", (brand_id,)).fetchall()
        for row in source_rows:
            conn.execute("DELETE FROM records WHERE monitor_id = ? AND data_type = 'community_post'", (row["id"],))
        conn.execute("DELETE FROM community_sources WHERE brand_id = ?", (brand_id,))
        cursor = conn.execute("DELETE FROM community_brands WHERE id = ?", (brand_id,))
        return {"deleted": cursor.rowcount}


def upsert_community_source(payload: dict, source_id: str = "") -> dict:
    brand_id = clean_text(payload.get("brand_id"))
    url = normalize_url(clean_text(payload.get("url")))
    platform = normalize_community_platform(payload.get("platform"), url)
    name = clean_text(payload.get("name")) or community_platform_label(platform)
    now = utc_now()
    next_id = source_id or payload.get("id") or str(uuid.uuid4())
    status = clean_text(payload.get("status") or "active")
    if status not in {"active", "paused"}:
        status = "active"
    with get_db() as conn:
        brand = conn.execute("SELECT * FROM community_brands WHERE id = ?", (brand_id,)).fetchone()
        if not brand:
            raise ValueError("Community brand not found")
        existing = conn.execute("SELECT created_at FROM community_sources WHERE id = ?", (next_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO community_sources (
              id, brand_id, platform, name, url, status, cadence, notes,
              last_collect_at, last_status, last_error, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              brand_id = excluded.brand_id,
              platform = excluded.platform,
              name = excluded.name,
              url = excluded.url,
              status = excluded.status,
              cadence = excluded.cadence,
              notes = excluded.notes,
              updated_at = excluded.updated_at
            """,
            (
                next_id,
                brand_id,
                platform,
                name,
                url,
                status,
                clean_text(payload.get("cadence")) or "manual",
                clean_text(payload.get("notes")),
                payload.get("last_collect_at"),
                payload.get("last_status") or "pending",
                payload.get("last_error"),
                existing["created_at"] if existing else now,
                now
            )
        )
        row = conn.execute("SELECT * FROM community_sources WHERE id = ?", (next_id,)).fetchone()
        return community_source_to_dict(conn, row, include_records=True)


def delete_community_source(source_id: str) -> dict:
    with get_db() as conn:
        conn.execute("DELETE FROM records WHERE monitor_id = ? AND data_type = 'community_post'", (source_id,))
        cursor = conn.execute("DELETE FROM community_sources WHERE id = ?", (source_id,))
        return {"deleted": cursor.rowcount}


def reddit_api_url(source_url: str, oauth: bool = False) -> str:
    url = normalize_url(source_url)
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
    base_url = "https://oauth.reddit.com" if oauth else "https://www.reddit.com"
    json_suffix = "" if oauth else ".json"
    if len(segments) >= 4 and segments[0].lower() == "r" and segments[2].lower() == "comments":
        path = "/" + "/".join(segments[:4])
        return f"{base_url}{path}{json_suffix}?limit=50"
    if len(segments) >= 2 and segments[0].lower() == "r":
        return f"{base_url}/r/{segments[1]}/new{json_suffix}?limit=25"
    params = parse_qs(parsed.query)
    query = params.get("q", [""])[0]
    if query:
        return f"{base_url}/search{json_suffix}?q={quote_plus(query)}&limit=25"
    raise ValueError("Reddit link must be a subreddit, post, or search URL")


def reddit_rss_url(source_url: str) -> str:
    url = normalize_url(source_url)
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
    if len(segments) >= 4 and segments[0].lower() == "r" and segments[2].lower() == "comments":
        path = "/" + "/".join(segments[:4])
        return f"https://www.reddit.com{path}/.rss?limit=20"
    if len(segments) >= 2 and segments[0].lower() == "r":
        return f"https://www.reddit.com/r/{segments[1]}/.rss?limit=10"
    params = parse_qs(parsed.query)
    query = params.get("q", [""])[0]
    if query:
        return f"https://www.reddit.com/search.rss?q={quote_plus(query)}&sort=new&limit=10"
    raise ValueError("Reddit link must be a subreddit, post, or search URL")


def parse_atom_time(value: str) -> str:
    value = clean_text(value)
    if not value:
        return utc_now()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        try:
            return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
        except Exception:
            return utc_now()


def flatten_reddit_children(payload) -> list[dict]:
    children: list[dict] = []
    if isinstance(payload, list):
        for entry in payload:
            children.extend(flatten_reddit_children(entry))
        return children
    if not isinstance(payload, dict):
        return children
    listing = payload.get("data", {})
    for child in listing.get("children", []) if isinstance(listing, dict) else []:
        if isinstance(child, dict) and isinstance(child.get("data"), dict):
            children.append({"kind": child.get("kind"), **child["data"]})
            replies = child["data"].get("replies")
            if isinstance(replies, dict):
                children.extend(flatten_reddit_children(replies))
    return children


def reddit_record_payload(item: dict, source: sqlite3.Row, brand: sqlite3.Row) -> dict | None:
    reddit_id = clean_text(item.get("id"))
    kind = item.get("kind")
    title = clean_text(item.get("title") or item.get("link_title"))
    body = clean_text(item.get("selftext") or item.get("body") or title)
    if not reddit_id or not body or body in {"[deleted]", "[removed]"}:
        return None
    permalink = item.get("permalink") or ""
    created = item.get("created_utc")
    occurred_at = datetime.fromtimestamp(created, timezone.utc).isoformat() if isinstance(created, (int, float)) else utc_now()
    return {
        "source_id": community_source_id("reddit"),
        "external_id": f"community:{source['id']}:reddit:{reddit_id}",
        "monitor_id": source["id"],
        "data_type": "community_post",
        "platform": "Reddit",
        "title": title or ("Reddit comment" if kind == "t1" else "Reddit post"),
        "author": f"u/{clean_text(item.get('author'))}" if item.get("author") else "",
        "body": body,
        "url": urljoin("https://www.reddit.com", permalink),
        "brand": brand["name"],
        "region": "US",
        "language": "en",
        "occurred_at": occurred_at,
        "raw": {
            "score": item.get("score"),
            "num_comments": item.get("num_comments"),
            "subreddit": item.get("subreddit"),
            "kind": kind
        }
    }


def reddit_rss_record_payload(entry: ET.Element, source: sqlite3.Row, brand: sqlite3.Row) -> dict | None:
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry_id = clean_text(entry.findtext("atom:id", default="", namespaces=ns))
    title = clean_text(entry.findtext("atom:title", default="", namespaces=ns))
    updated = clean_text(entry.findtext("atom:updated", default="", namespaces=ns))
    content = entry.findtext("atom:content", default="", namespaces=ns)
    body = clean_text(f"{title}\n{extract_visible_text(content)}")
    author_node = entry.find("atom:author/atom:name", namespaces=ns)
    author = clean_text(author_node.text if author_node is not None else "")
    href = ""
    for link in entry.findall("atom:link", namespaces=ns):
        if link.attrib.get("rel") in {"alternate", ""} or not href:
            href = clean_text(link.attrib.get("href"))
        if link.attrib.get("rel") == "alternate":
            break
    stable_id = entry_id or href or title
    if not stable_id or not body:
        return None
    digest = hashlib.sha1(stable_id.encode("utf-8")).hexdigest()
    return {
        "source_id": community_source_id("reddit"),
        "external_id": f"community:{source['id']}:reddit:rss:{digest}",
        "monitor_id": source["id"],
        "data_type": "community_post",
        "platform": "Reddit",
        "title": title or "Reddit post",
        "author": author,
        "body": body,
        "url": href,
        "brand": brand["name"],
        "region": "US",
        "language": "en",
        "occurred_at": parse_atom_time(updated),
        "raw": {
            "feed": "reddit_rss",
            "entry_id": entry_id
        }
    }


def fetch_reddit_rss_bytes(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": os.environ.get("REDDIT_USER_AGENT", "MonitorIntelligenceHub/0.1 community analysis"),
            "Accept": "application/atom+xml,application/rss+xml,text/xml"
        }
    )
    try:
        with open_request(request, timeout=16) as response:
            return response.read(2_500_000)
    except HTTPError as exc:
        if exc.code not in {403, 429}:
            raise
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        raise ValueError("Reddit RSS request was rate limited and curl is not available for fallback fetching.")
    result = subprocess.run(
        [
            curl,
            "-L",
            "-sS",
            "--max-time",
            "20",
            "-A",
            os.environ.get("REDDIT_USER_AGENT", "MonitorIntelligenceHub/0.1 community analysis"),
            "-H",
            "Accept: application/atom+xml,application/rss+xml,text/xml",
            url
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=25
    )
    if result.returncode == 0 and result.stdout.strip().startswith(b"<"):
        return result.stdout[:2_500_000]

    ps_detail = ""
    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if powershell:
        command = (
            "$ProgressPreference='SilentlyContinue';"
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            "$headers=@{'User-Agent'=$env:REDDIT_USER_AGENT_VALUE;'Accept'='application/atom+xml,application/rss+xml,text/xml'};"
            "$response=Invoke-WebRequest -Uri $env:REDDIT_RSS_URL -Headers $headers -UseBasicParsing -TimeoutSec 20;"
            "Write-Output $response.Content"
        )
        env = os.environ.copy()
        env["REDDIT_RSS_URL"] = url
        env["REDDIT_USER_AGENT_VALUE"] = os.environ.get("REDDIT_USER_AGENT", "MonitorIntelligenceHub/0.1 community analysis")
        ps_result = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=ROOT,
            env=env,
            capture_output=True,
            timeout=30
        )
        if ps_result.returncode == 0 and ps_result.stdout.strip().startswith(b"<"):
            return ps_result.stdout[:2_500_000]
        ps_detail = (ps_result.stderr or ps_result.stdout or b"").decode("utf-8", errors="replace")

    detail = (result.stderr or result.stdout or b"Reddit RSS fallback failed").decode("utf-8", errors="replace")
    combined = clean_text("; ".join(part for part in [detail, ps_detail] if part))
    raise ValueError(combined or "Reddit RSS fallback failed")


def collect_reddit_rss_source(conn: sqlite3.Connection, source: sqlite3.Row, brand: sqlite3.Row) -> tuple[int, int]:
    raw = fetch_reddit_rss_bytes(reddit_rss_url(source["url"]))
    root = ET.fromstring(raw)
    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    created = 0
    scanned = 0
    for entry in entries:
        record = reddit_rss_record_payload(entry, source, brand)
        if not record:
            continue
        scanned += 1
        if insert_record_if_new(conn, record):
            created += 1
    return created, scanned


def collect_reddit_source(conn: sqlite3.Connection, source: sqlite3.Row, brand: sqlite3.Row) -> tuple[int, int]:
    token = os.environ.get("REDDIT_BEARER_TOKEN", "").strip()
    if not token:
        return collect_reddit_rss_source(conn, source, brand)
    api_url = reddit_api_url(source["url"], oauth=bool(token))
    headers = {
        "User-Agent": os.environ.get("REDDIT_USER_AGENT", "MonitorIntelligenceHub/0.1 community analysis"),
        "Accept": "application/json"
    }
    if token:
        headers["Authorization"] = f"bearer {token}"
    request = Request(
        api_url,
        headers=headers
    )
    try:
        with open_request(request, timeout=16) as response:
            payload = json.loads(response.read(2_500_000).decode("utf-8", errors="replace"))
    except HTTPError as exc:
        if exc.code in {403, 429} and not token:
            return collect_reddit_rss_source(conn, source, brand)
        if exc.code == 403:
            raise ValueError("Reddit returned 403. Configure REDDIT_BEARER_TOKEN and REDDIT_USER_AGENT, or use an authorized Reddit API integration.")
        if exc.code == 429:
            raise ValueError("Reddit rate limit reached. Retry later or use an authorized Reddit API integration.")
        raise
    children = flatten_reddit_children(payload)
    created = 0
    scanned = 0
    for item in children:
        record = reddit_record_payload(item, source, brand)
        if not record:
            continue
        scanned += 1
        if insert_record_if_new(conn, record):
            created += 1
    return created, scanned


def discord_channel_parts(source_url: str) -> tuple[str, str]:
    parsed = urlparse(normalize_url(source_url))
    segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
    params = parse_qs(parsed.query)
    if "channel_id" in params:
        return params.get("guild_id", [""])[0], params["channel_id"][0]
    if len(segments) >= 3 and segments[0].lower() == "channels":
        return segments[1], segments[2]
    raise ValueError("Discord collection requires a channel URL like https://discord.com/channels/{server_id}/{channel_id} and DISCORD_BOT_TOKEN.")


def discord_invite_code(source_url: str) -> str:
    parsed = urlparse(normalize_url(source_url))
    host = parsed.netloc.lower().replace("www.", "")
    segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
    if host == "discord.gg" and segments:
        return segments[0]
    if host.endswith("discord.com") and segments:
        if segments[0].lower() in {"invite", "invites"} and len(segments) >= 2:
            return segments[1]
        if segments[0].lower() not in {"channels", "api"}:
            return segments[0]
    params = parse_qs(parsed.query)
    return clean_text(params.get("invite", [""])[0])


def discord_invite_record_payload(payload: dict, source: sqlite3.Row, brand: sqlite3.Row) -> dict | None:
    code = clean_text(payload.get("code"))
    guild = payload.get("guild") if isinstance(payload.get("guild"), dict) else {}
    channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
    guild_name = clean_text(guild.get("name") or payload.get("guild_name") or code)
    if not code or not guild_name:
        return None
    approximate_members = payload.get("approximate_member_count")
    approximate_presence = payload.get("approximate_presence_count")
    description = clean_text(guild.get("description"))
    body_parts = [
        f"Discord server: {guild_name}",
        f"Invite code: {code}",
    ]
    if approximate_members is not None:
        body_parts.append(f"Approximate members: {approximate_members}")
    if approximate_presence is not None:
        body_parts.append(f"Approximate online: {approximate_presence}")
    if channel.get("name"):
        body_parts.append(f"Landing channel: #{clean_text(channel.get('name'))}")
    if description:
        body_parts.append(description)
    return {
        "source_id": community_source_id("discord"),
        "external_id": f"community:{source['id']}:discord_invite:{code}:{clean_text(guild.get('id'))}",
        "monitor_id": source["id"],
        "data_type": "community_post",
        "platform": "Discord",
        "title": f"{guild_name} Discord community",
        "author": "Discord public invite",
        "body": "\n".join(body_parts),
        "url": f"https://discord.gg/{code}",
        "brand": brand["name"],
        "language": "en",
        "occurred_at": utc_now(),
        "raw": {
            "invite_code": code,
            "guild_id": guild.get("id"),
            "guild_name": guild_name,
            "channel_id": channel.get("id"),
            "channel_name": channel.get("name"),
            "approximate_member_count": approximate_members,
            "approximate_presence_count": approximate_presence
        }
    }


def collect_discord_invite_source(conn: sqlite3.Connection, source: sqlite3.Row, brand: sqlite3.Row) -> tuple[int, int]:
    code = discord_invite_code(source["url"])
    if not code:
        raise ValueError("Discord public collection requires an invite URL like https://discord.gg/{code}; channel messages require DISCORD_BOT_TOKEN.")
    api_url = f"https://discord.com/api/v10/invites/{quote_plus(code)}?with_counts=true&with_expiration=true"
    request = Request(
        api_url,
        headers={
            "User-Agent": "MonitorIntelligenceHub/0.1 community analysis",
            "Accept": "application/json"
        }
    )
    try:
        with open_request(request, timeout=20) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8", errors="replace"))
    except HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise ValueError("Discord invite is not publicly readable or has expired. Use a valid invite URL or configure DISCORD_BOT_TOKEN for channel messages.")
        if exc.code == 429:
            raise ValueError("Discord public invite API rate limit reached. Retry later.")
        raise
    record = discord_invite_record_payload(payload, source, brand)
    if not record:
        raise ValueError("Discord invite API returned no usable community data.")
    return (1 if insert_record_if_new(conn, record) else 0), 1


def discord_record_payload(message: dict, source: sqlite3.Row, brand: sqlite3.Row, guild_id: str, channel_id: str) -> dict | None:
    message_id = clean_text(message.get("id"))
    body = clean_text(message.get("content"))
    if not message_id or not body:
        return None
    author = message.get("author") if isinstance(message.get("author"), dict) else {}
    username = clean_text(author.get("global_name") or author.get("username"))
    timestamp = clean_text(message.get("timestamp")) or utc_now()
    return {
        "source_id": community_source_id("discord"),
        "external_id": f"community:{source['id']}:discord:{message_id}",
        "monitor_id": source["id"],
        "data_type": "community_post",
        "platform": "Discord",
        "title": body[:80],
        "author": username,
        "body": body,
        "url": f"https://discord.com/channels/{guild_id or '@me'}/{channel_id}/{message_id}",
        "brand": brand["name"],
        "language": "en",
        "occurred_at": timestamp,
        "raw": {
            "channel_id": channel_id,
            "guild_id": guild_id,
            "author_id": author.get("id"),
            "attachments": len(message.get("attachments") or [])
        }
    }


def collect_discord_source(conn: sqlite3.Connection, source: sqlite3.Row, brand: sqlite3.Row) -> tuple[int, int]:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        return collect_discord_invite_source(conn, source, brand)
    guild_id, channel_id = discord_channel_parts(source["url"])
    auth_value = token if token.lower().startswith("bot ") else f"Bot {token}"
    request = Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=50",
        headers={
            "Authorization": auth_value,
            "User-Agent": "MonitorIntelligenceHub/0.1 (+community analysis)",
            "Accept": "application/json"
        }
    )
    try:
        with open_request(request, timeout=16) as response:
            payload = json.loads(response.read(2_500_000).decode("utf-8", errors="replace"))
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("Discord API denied access. Check DISCORD_BOT_TOKEN, bot channel permissions, and the channel URL.")
        if exc.code == 429:
            raise ValueError("Discord rate limit reached. Retry later.")
        raise
    if not isinstance(payload, list):
        raise ValueError("Discord API returned an unexpected response.")
    created = 0
    scanned = 0
    for message in payload:
        record = discord_record_payload(message, source, brand, guild_id, channel_id)
        if not record:
            continue
        scanned += 1
        if insert_record_if_new(conn, record):
            created += 1
    return created, scanned


def facebook_group_id(source_url: str) -> str:
    parsed = urlparse(normalize_url(source_url))
    params = parse_qs(parsed.query)
    if "group_id" in params:
        return params["group_id"][0]
    segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
    for index, segment in enumerate(segments):
        if segment.lower() == "groups" and index + 1 < len(segments):
            return segments[index + 1]
    raise ValueError("Facebook Group collection requires a group URL like https://www.facebook.com/groups/{group_id} and FACEBOOK_ACCESS_TOKEN.")


def facebook_record_payload(post: dict, source: sqlite3.Row, brand: sqlite3.Row) -> dict | None:
    post_id = clean_text(post.get("id"))
    body = clean_text(post.get("message") or post.get("story"))
    if not post_id or not body:
        return None
    author = post.get("from") if isinstance(post.get("from"), dict) else {}
    return {
        "source_id": community_source_id("facebook"),
        "external_id": f"community:{source['id']}:facebook:{post_id}",
        "monitor_id": source["id"],
        "data_type": "community_post",
        "platform": "Facebook",
        "title": body[:80],
        "author": clean_text(author.get("name")),
        "body": body,
        "url": clean_text(post.get("permalink_url")),
        "brand": brand["name"],
        "language": "en",
        "occurred_at": clean_text(post.get("created_time")) or utc_now(),
        "raw": {
            "post_id": post_id,
            "author_id": author.get("id")
        }
    }


def collect_facebook_source(conn: sqlite3.Connection, source: sqlite3.Row, brand: sqlite3.Row) -> tuple[int, int]:
    token = os.environ.get("FACEBOOK_ACCESS_TOKEN", "").strip() or os.environ.get("META_ACCESS_TOKEN", "").strip()
    if not token:
        raise ValueError("Facebook Group collection requires FACEBOOK_ACCESS_TOKEN or META_ACCESS_TOKEN with group content permissions.")
    group_id = facebook_group_id(source["url"])
    params = urlencode({
        "fields": "id,message,story,permalink_url,created_time,from{name,id}",
        "limit": "50",
        "access_token": token
    })
    request = Request(
        f"https://graph.facebook.com/v19.0/{quote_plus(group_id)}/feed?{params}",
        headers={
            "User-Agent": "MonitorIntelligenceHub/0.1 (+community analysis)",
            "Accept": "application/json"
        }
    )
    try:
        with open_request(request, timeout=16) as response:
            payload = json.loads(response.read(2_500_000).decode("utf-8", errors="replace"))
    except HTTPError as exc:
        if exc.code in {400, 401, 403}:
            raise ValueError("Facebook Graph API denied access. Check group ID, token scopes, app review status, and group admin authorization.")
        if exc.code == 429:
            raise ValueError("Facebook Graph API rate limit reached. Retry later.")
        raise
    posts = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(posts, list):
        raise ValueError("Facebook Graph API returned an unexpected response.")
    created = 0
    scanned = 0
    for post in posts:
        record = facebook_record_payload(post, source, brand)
        if not record:
            continue
        scanned += 1
        if insert_record_if_new(conn, record):
            created += 1
    return created, scanned


def collect_owned_source(conn: sqlite3.Connection, source: sqlite3.Row, brand: sqlite3.Row) -> tuple[int, int]:
    page = fetch_snapshot_page(source["url"])
    text = clean_text(page.get("text", ""))[:3000]
    if not text:
        raise ValueError("No readable text found on the owned community page")
    text_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
    record = {
        "source_id": community_source_id("owned"),
        "external_id": f"community:{source['id']}:owned:{text_hash}",
        "monitor_id": source["id"],
        "data_type": "community_post",
        "platform": "Owned",
        "title": page.get("title") or source["name"],
        "author": host_key(page.get("final_url") or source["url"]),
        "body": text,
        "url": page.get("final_url") or source["url"],
        "brand": brand["name"],
        "language": "en",
        "occurred_at": utc_now(),
        "raw": {
            "content_length": len(text),
            "anchor_count": page.get("anchor_count", 0)
        }
    }
    return (1 if insert_record_if_new(conn, record) else 0), 1


def collect_community_source(source_id: str) -> dict:
    status = "ready"
    error = ""
    created = 0
    scanned = 0
    with get_db() as conn:
        source = conn.execute("SELECT * FROM community_sources WHERE id = ?", (source_id,)).fetchone()
        if not source:
            raise ValueError("Community source not found")
        brand = conn.execute("SELECT * FROM community_brands WHERE id = ?", (source["brand_id"],)).fetchone()
        if not brand:
            raise ValueError("Community brand not found")
        try:
            if source["platform"] == "reddit":
                created, scanned = collect_reddit_source(conn, source, brand)
            elif source["platform"] == "discord":
                created, scanned = collect_discord_source(conn, source, brand)
            elif source["platform"] == "facebook":
                created, scanned = collect_facebook_source(conn, source, brand)
            elif source["platform"] == "owned":
                created, scanned = collect_owned_source(conn, source, brand)
            else:
                raise ValueError(f"{community_platform_label(source['platform'])} collection requires official API/Bot authorization")
        except Exception as exc:
            status = "error"
            error = str(exc) or exc.__class__.__name__
        now = utc_now()
        conn.execute(
            """
            UPDATE community_sources
            SET last_collect_at = ?, last_status = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, status, error, now, source_id)
        )
        saved = community_source_to_dict(conn, conn.execute("SELECT * FROM community_sources WHERE id = ?", (source_id,)).fetchone(), include_records=True)
    if error:
        raise ValueError(error)
    return {"source": saved, "created": created, "scanned": scanned}


def query_community_records(params: dict[str, list[str]], limit: int | None = 120) -> list[dict]:
    clauses = ["records.data_type = 'community_post'", "records.monitor_id IS NOT NULL"]
    values: list[str] = []
    brand_id = first_param(params, "brand_id")
    source_id = first_param(params, "source_id")
    if brand_id:
        clauses.append("community_sources.brand_id = ?")
        values.append(brand_id)
    if source_id:
        clauses.append("records.monitor_id = ?")
        values.append(source_id)
    days = first_param(params, "days")
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(parse_int_param(params, "days", 30, 1, 365) - 1, 0))).date().isoformat()
        clauses.append("substr(records.occurred_at, 1, 10) >= ?")
        values.append(cutoff)
    where = f"WHERE {' AND '.join(clauses)}"
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT ?"
        values.append(limit)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
              records.*,
              sources.name AS source_name,
              sources.category AS source_category,
              community_sources.name AS community_source_name,
              community_sources.platform AS community_platform,
              community_sources.brand_id AS community_brand_id,
              community_brands.name AS community_brand_name
            FROM records
            JOIN sources ON sources.id = records.source_id
            JOIN community_sources ON community_sources.id = records.monitor_id
            JOIN community_brands ON community_brands.id = community_sources.brand_id
            {where}
            ORDER BY records.occurred_at DESC
            {limit_sql}
            """,
            values
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def list_community_records(params: dict[str, list[str]]) -> list[dict]:
    limit = parse_int_param(params, "limit", 120, 1, 1000)
    return query_community_records(params, limit=limit)


def community_summary_payload(params: dict[str, list[str]]) -> dict:
    brands = list_community_brands()
    records = query_community_records(params, limit=None)
    platform_counts: dict[str, dict] = {}
    for record in records:
        platform = record.get("community_platform") or record.get("platform") or "owned"
        bucket = platform_counts.setdefault(platform, {
            "platform": platform,
            "platform_label": community_platform_label(platform),
            "count": 0,
            "negative": 0
        })
        bucket["count"] += 1
        if record.get("sentiment") == "negative":
            bucket["negative"] += 1
    by_platform = []
    for item in platform_counts.values():
        item["negative_rate"] = round(item["negative"] / item["count"], 4) if item["count"] else 0
        by_platform.append(item)
    return {
        "generated_at": utc_now(),
        "brands": brands,
        "records": records[:12],
        "total_brands": len(brands),
        "total_sources": sum(len(brand.get("sources") or []) for brand in brands),
        "total_records": len(records),
        "negative_records": len(records_negative(records)),
        "negative_rate": negative_rate(records),
        "top_topics": compact_topics(records, 8),
        "by_platform": sorted(by_platform, key=lambda item: item["count"], reverse=True)
    }


MARKETING_LINK_TYPES = {"social", "creator", "ads"}


def normalize_marketing_type(value: str | None) -> str:
    next_type = compact_key(value or "social")
    return next_type if next_type in MARKETING_LINK_TYPES else "social"


def normalize_marketing_platform(value: str | None, url: str = "") -> str:
    platform = compact_key(value)
    aliases = {
        "twitter": "x",
        "xcom": "x",
        "youtubechannel": "youtube",
        "yt": "youtube",
        "instagramprofile": "instagram",
        "tiktokshop": "tiktok",
        "facebookpage": "facebook"
    }
    platform = aliases.get(platform, platform)
    if platform in {"youtube", "tiktok", "instagram", "x", "facebook", "linkedin", "pinterest", "website"}:
        return platform
    host = host_key(url)
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "tiktok.com" in host:
        return "tiktok"
    if "instagram.com" in host:
        return "instagram"
    if "x.com" in host or "twitter.com" in host:
        return "x"
    if "facebook.com" in host or "fb.com" in host:
        return "facebook"
    if "linkedin.com" in host:
        return "linkedin"
    if "pinterest.com" in host:
        return "pinterest"
    return "website"


def marketing_platform_label(platform: str) -> str:
    return {
        "youtube": "YouTube",
        "tiktok": "TikTok",
        "instagram": "Instagram",
        "x": "X / Twitter",
        "facebook": "Facebook",
        "linkedin": "LinkedIn",
        "pinterest": "Pinterest",
        "website": "Website"
    }.get(platform, platform.title())


def marketing_record_type(monitor_type: str) -> str:
    if monitor_type == "ads":
        return "ad_comment"
    return "creator_signal" if monitor_type == "creator" else "social_comment"


def marketing_source_id(monitor_type: str) -> str:
    if monitor_type == "ads":
        return "meta_ads"
    return "creator_public_link" if monitor_type == "creator" else "social_public_link"


def marketing_link_to_dict(conn: sqlite3.Connection, row: sqlite3.Row, days: int = 30) -> dict:
    item = dict(row)
    item["platform_label"] = marketing_platform_label(item["platform"])
    item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
    item["raw"] = json.loads(item.pop("raw_json") or "{}")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days - 1, 0))).date().isoformat()
    records = conn.execute(
        """
        SELECT records.*, sources.name AS source_name
        FROM records JOIN sources ON sources.id = records.source_id
        WHERE records.monitor_id = ?
          AND records.data_type = ?
          AND substr(records.occurred_at, 1, 10) >= ?
        ORDER BY records.occurred_at DESC
        """,
        (item["id"], marketing_record_type(item["monitor_type"]), cutoff)
    ).fetchall()
    parsed_records = [row_to_dict(record) for record in records]
    item["record_count"] = len(parsed_records)
    item["latest_record_at"] = parsed_records[0]["occurred_at"] if parsed_records else ""
    item["latest_record"] = parsed_records[0] if parsed_records else None
    return item


def list_marketing_links(params: dict[str, list[str]]) -> list[dict]:
    monitor_type = normalize_marketing_type(first_param(params, "monitor_type"))
    brand_name = first_param(params, "brand_name")
    brand_id = first_param(params, "brand_id")
    platform = first_param(params, "platform")
    days = parse_int_param(params, "days", 30, 1, 365)
    clauses = ["monitor_type = ?"]
    values: list[str] = [monitor_type]
    if brand_id:
        clauses.append("brand_id = ?")
        values.append(brand_id)
    if brand_name:
        clauses.append("brand_name = ?")
        values.append(brand_name)
    if platform:
        clauses.append("platform = ?")
        values.append(normalize_marketing_platform(platform))
    where = f"WHERE {' AND '.join(clauses)}"
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM marketing_links
            {where}
            ORDER BY brand_name, platform, updated_at DESC
            """,
            values
        ).fetchall()
        return [marketing_link_to_dict(conn, row, days) for row in rows]


def marketing_link_summary(params: dict[str, list[str]]) -> dict:
    links = list_marketing_links(params)
    total_records = sum(int(link.get("record_count") or 0) for link in links)
    active_links = sum(1 for link in links if link.get("status") == "active")
    ready_links = sum(1 for link in links if link.get("last_status") == "ready")
    error_links = sum(1 for link in links if link.get("last_status") == "error")
    by_brand: dict[str, dict] = {}
    by_platform: dict[str, dict] = {}
    for link in links:
        brand_bucket = by_brand.setdefault(link["brand_name"], {"brand_name": link["brand_name"], "links": 0, "records": 0})
        brand_bucket["links"] += 1
        brand_bucket["records"] += int(link.get("record_count") or 0)
        platform_bucket = by_platform.setdefault(link["platform"], {
            "platform": link["platform"],
            "platform_label": link["platform_label"],
            "links": 0,
            "records": 0
        })
        platform_bucket["links"] += 1
        platform_bucket["records"] += int(link.get("record_count") or 0)
    return {
        "generated_at": utc_now(),
        "monitor_type": normalize_marketing_type(first_param(params, "monitor_type")),
        "total_links": len(links),
        "active_links": active_links,
        "ready_links": ready_links,
        "error_links": error_links,
        "total_records": total_records,
        "by_brand": sorted(by_brand.values(), key=lambda item: (item["records"], item["links"]), reverse=True),
        "by_platform": sorted(by_platform.values(), key=lambda item: (item["records"], item["links"]), reverse=True),
        "recent": [link["latest_record"] for link in links if link.get("latest_record")][:8],
        "links": links
    }


def marketing_oembed_url(platform: str, url: str) -> str:
    encoded = quote_plus(url)
    if platform == "youtube":
        return f"https://www.youtube.com/oembed?url={encoded}&format=json"
    if platform == "tiktok":
        return f"https://www.tiktok.com/oembed?url={encoded}"
    if platform == "x":
        return f"https://publish.twitter.com/oembed?url={encoded}"
    return ""


def fetch_marketing_oembed(platform: str, url: str) -> dict:
    oembed_url = marketing_oembed_url(platform, url)
    if not oembed_url:
        return {}
    request = Request(
        oembed_url,
        headers={
            "User-Agent": "MonitorIntelligenceHub/0.1 (+public marketing link monitor)",
            "Accept": "application/json"
        }
    )
    with open_request(request, timeout=12) as response:
        return json.loads(response.read(1_000_000).decode("utf-8", errors="replace"))


def collect_marketing_public_data(link: sqlite3.Row) -> dict:
    url = normalize_url(link["url"])
    platform = link["platform"]
    oembed = {}
    oembed_error = ""
    try:
        oembed = fetch_marketing_oembed(platform, url)
    except Exception as exc:
        oembed_error = str(exc)

    page = {}
    page_error = ""
    try:
        snapshot_page = fetch_snapshot_page(url)
        page = {
            "final_url": snapshot_page.get("final_url"),
            "title": snapshot_page.get("title"),
            "text": clean_text(snapshot_page.get("text", ""))[:1600],
            "icon_url": snapshot_page.get("icon_url"),
            "anchor_count": snapshot_page.get("anchor_count", 0)
        }
    except Exception as exc:
        page_error = str(exc)

    title = clean_text(oembed.get("title") or page.get("title") or link["name"])
    author = clean_text(oembed.get("author_name") or host_key(page.get("final_url") or url) or marketing_platform_label(platform))
    provider = clean_text(oembed.get("provider_name") or marketing_platform_label(platform))
    description = clean_text(page.get("text") or title)
    if not description:
        description = f"{link['brand_name']} {marketing_platform_label(platform)} link collected."
    thumbnail_url = clean_text(oembed.get("thumbnail_url") or page.get("icon_url"))
    final_url = clean_text(page.get("final_url") or url)
    text_hash = hashlib.sha1(f"{title}|{author}|{description[:800]}".encode("utf-8")).hexdigest()
    return {
        "title": title,
        "author": author,
        "provider": provider,
        "description": description[:3000],
        "thumbnail_url": thumbnail_url,
        "final_url": final_url,
        "text_hash": text_hash,
        "fetched_at": utc_now(),
        "oembed": oembed,
        "oembed_error": oembed_error,
        "page": page,
        "page_error": page_error,
        "method": "oembed+page" if oembed and page else "oembed" if oembed else "page"
    }


def upsert_marketing_link(payload: dict, link_id: str = "") -> dict:
    monitor_type = normalize_marketing_type(payload.get("monitor_type"))
    brand_id = clean_text(payload.get("brand_id"))
    brand_name = clean_text(payload.get("brand_name"))
    with get_db() as conn:
        if brand_id:
            brand = conn.execute("SELECT * FROM brands WHERE id = ?", (brand_id,)).fetchone()
            if brand:
                brand_name = brand_name or row_to_dict(brand).get("name") or ""
    if not brand_name:
        raise ValueError("Brand name is required")
    url = normalize_url(clean_text(payload.get("url")))
    platform = normalize_marketing_platform(payload.get("platform"), url)
    name = clean_text(payload.get("name")) or marketing_platform_label(platform)
    status = clean_text(payload.get("status") or "active")
    if status not in {"active", "paused"}:
        status = "active"
    now = utc_now()
    next_id = link_id or payload.get("id") or str(uuid.uuid4())
    with get_db() as conn:
        existing = conn.execute("SELECT created_at, metrics_json, raw_json FROM marketing_links WHERE id = ?", (next_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO marketing_links (
              id, brand_id, brand_name, monitor_type, platform, name, url, status, cadence,
              last_collect_at, last_status, last_error, metrics_json, raw_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              brand_id = excluded.brand_id,
              brand_name = excluded.brand_name,
              monitor_type = excluded.monitor_type,
              platform = excluded.platform,
              name = excluded.name,
              url = excluded.url,
              status = excluded.status,
              cadence = excluded.cadence,
              updated_at = excluded.updated_at
            """,
            (
                next_id,
                brand_id or None,
                brand_name,
                monitor_type,
                platform,
                name,
                url,
                status,
                clean_text(payload.get("cadence")) or "daily",
                payload.get("last_collect_at") if existing is None else None,
                payload.get("last_status") or "pending",
                payload.get("last_error") or "",
                existing["metrics_json"] if existing else "{}",
                existing["raw_json"] if existing else json.dumps({"created_from": payload}, ensure_ascii=False),
                existing["created_at"] if existing else now,
                now
            )
        )
        row = conn.execute("SELECT * FROM marketing_links WHERE id = ?", (next_id,)).fetchone()
        saved = marketing_link_to_dict(conn, row)

    if payload.get("collect_now", not link_id):
        try:
            return collect_marketing_link(next_id)["link"]
        except Exception:
            with get_db() as conn:
                row = conn.execute("SELECT * FROM marketing_links WHERE id = ?", (next_id,)).fetchone()
                return marketing_link_to_dict(conn, row)
    return saved


def delete_marketing_link(link_id: str) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT monitor_type FROM marketing_links WHERE id = ?", (link_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM records WHERE monitor_id = ? AND data_type = ?", (link_id, marketing_record_type(row["monitor_type"])))
        cursor = conn.execute("DELETE FROM marketing_links WHERE id = ?", (link_id,))
        return {"deleted": cursor.rowcount}


def collect_marketing_link(link_id: str) -> dict:
    status = "ready"
    error = ""
    created = 0
    scanned = 0
    metrics: dict = {}
    with get_db() as conn:
        link = conn.execute("SELECT * FROM marketing_links WHERE id = ?", (link_id,)).fetchone()
        if not link:
            raise ValueError("Marketing link not found")
        try:
            metrics = collect_marketing_public_data(link)
            record_payload = {
                "source_id": marketing_source_id(link["monitor_type"]),
                "external_id": f"marketing:{link['id']}:{metrics['text_hash']}",
                "monitor_id": link["id"],
                "data_type": marketing_record_type(link["monitor_type"]),
                "platform": marketing_platform_label(link["platform"]),
                "title": metrics["title"],
                "author": metrics["author"],
                "body": metrics["description"],
                "url": metrics["final_url"],
                "brand": link["brand_name"],
                "language": "en",
                "occurred_at": metrics["fetched_at"],
                "raw": {
                    "monitor_type": link["monitor_type"],
                    "platform": link["platform"],
                    "provider": metrics["provider"],
                    "thumbnail_url": metrics["thumbnail_url"],
                    "method": metrics["method"]
                }
            }
            scanned = 1
            if insert_record_if_new(conn, record_payload):
                created = 1
        except Exception as exc:
            status = "error"
            error = str(exc)
        now = utc_now()
        conn.execute(
            """
            UPDATE marketing_links
            SET last_collect_at = ?, last_status = ?, last_error = ?, metrics_json = ?, raw_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                now,
                status,
                error,
                json.dumps(metrics, ensure_ascii=False),
                json.dumps({"last_collect": metrics, "error": error}, ensure_ascii=False),
                now,
                link_id
            )
        )
        saved = marketing_link_to_dict(conn, conn.execute("SELECT * FROM marketing_links WHERE id = ?", (link_id,)).fetchone())
    if error:
        raise ValueError(error)
    return {"link": saved, "created": created, "scanned": scanned}


def run_due_marketing_link_collections() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id FROM marketing_links
            WHERE status = 'active'
              AND (
                last_collect_at IS NULL
                OR substr(last_collect_at, 1, 10) < ?
              )
            ORDER BY created_at ASC
            """,
            (today,)
        ).fetchall()
    count = 0
    for row in rows:
        try:
            collect_marketing_link(row["id"])
            count += 1
        except Exception:
            pass
    return count


def records_negative(records: list[dict]) -> list[dict]:
    return [record for record in records if record.get("sentiment") == "negative"]


def negative_rate(records: list[dict]) -> float:
    return round(len(records_negative(records)) / len(records), 4) if records else 0.0


def team_for_record(record: dict, topic: str = "") -> str:
    topics = set(record.get("topics") or [])
    if topic:
        topics.add(topic)
    haystack = " ".join(
        str(record.get(key) or "")
        for key in ("body", "title", "product", "platform", "data_type")
    ).lower()
    if "experience" in topics or any(term in haystack for term in ("app", "ios", "android", "ux", "crash", "卡顿", "闪退", "体验")):
        return "experience_team"
    if record.get("intent") == "complaint" or topics & {"support", "delivery"} or any(term in haystack for term in ("refund", "ticket", "投诉", "退款", "售后")):
        return "support_team"
    if record.get("intent") == "request" or topics & {"quality", "feature"}:
        return "product_team"
    return "marketing_team"


def team_for_records(records: list[dict], topic: str = "") -> str:
    if not records:
        return "marketing_team"
    counts = Counter(team_for_record(record, topic) for record in records)
    return counts.most_common(1)[0][0]


def priority_for_records(records: list[dict]) -> str:
    negative = len(records_negative(records))
    rate = negative_rate(records)
    complaints = sum(1 for record in records if record.get("intent") == "complaint")
    if negative >= 5 or (negative >= 3 and rate >= 0.5):
        return "urgent"
    if negative >= 2 or complaints >= 2:
        return "high"
    if negative or complaints:
        return "medium"
    return "low"


def safe_action_status(value: str | None, default: str = "open") -> str:
    status = clean_text(value or default)
    return status if status in VOC_ACTION_STATUSES else default


def safe_action_priority(value: str | None, default: str = "medium") -> str:
    priority = clean_text(value or default)
    return priority if priority in VOC_ACTION_PRIORITIES else default


def safe_owner_team(value: str | None, default: str = "marketing_team") -> str:
    owner = clean_text(value or default)
    return owner if owner in VOC_OWNER_TEAMS else default


def topic_counts(records: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for record in records:
        topics = record.get("topics") or ["other"]
        for topic in topics:
            item = grouped.setdefault(topic, {"topic": topic, "count": 0, "negative": 0, "records": []})
            item["count"] += 1
            if record.get("sentiment") == "negative":
                item["negative"] += 1
            item["records"].append(record)
    result = []
    for item in grouped.values():
        records_for_topic = item.pop("records")
        item["negative_rate"] = negative_rate(records_for_topic)
        item["owner_team"] = team_for_records(records_for_topic, item["topic"])
        result.append(item)
    return sorted(result, key=lambda item: (item["count"], item["negative"]), reverse=True)


def compact_topics(records: list[dict], limit: int = 4) -> list[dict]:
    return topic_counts(records)[:limit]


def channel_breakdown(records: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for record in records:
        key = record.get("source_id") or "unknown"
        item = grouped.setdefault(key, {
            "source_id": key,
            "name": record.get("source_name") or key,
            "category": record.get("source_category") or "",
            "count": 0,
            "negative": 0,
            "records": []
        })
        item["count"] += 1
        if record.get("sentiment") == "negative":
            item["negative"] += 1
        item["records"].append(record)
    result = []
    for item in grouped.values():
        scoped = item.pop("records")
        item["negative_rate"] = negative_rate(scoped)
        item["top_topics"] = compact_topics(scoped, 3)
        result.append(item)
    return sorted(result, key=lambda item: item["count"], reverse=True)


def product_breakdown(records: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for record in records:
        raw_product = clean_text(record.get("product")) or ""
        label = raw_product or "未标注产品"
        item = grouped.setdefault(label, {
            "product": label,
            "value": raw_product,
            "count": 0,
            "negative": 0,
            "records": []
        })
        item["count"] += 1
        if record.get("sentiment") == "negative":
            item["negative"] += 1
        item["records"].append(record)
    result = []
    for item in grouped.values():
        scoped = item.pop("records")
        item["negative_rate"] = negative_rate(scoped)
        item["top_topics"] = compact_topics(scoped, 3)
        result.append(item)
    return sorted(result, key=lambda item: item["count"], reverse=True)


def trend_breakdown(records: list[dict], days: int) -> list[dict]:
    start = datetime.now(timezone.utc).date() - timedelta(days=max(days - 1, 0))
    counts = {
        (start + timedelta(days=offset)).isoformat(): {"date": (start + timedelta(days=offset)).isoformat(), "count": 0, "negative": 0}
        for offset in range(days)
    }
    for record in records:
        date = str(record.get("occurred_at") or "")[:10]
        if date in counts:
            counts[date]["count"] += 1
            if record.get("sentiment") == "negative":
                counts[date]["negative"] += 1
    return list(counts.values())


def alert_payload(
    alert_id: str,
    title: str,
    description: str,
    records: list[dict],
    previous_count: int = 0,
    topic: str = "",
    product: str = "",
    source_id: str = ""
) -> dict:
    priority = priority_for_records(records)
    count = len(records)
    change_rate = round((count - previous_count) / max(previous_count, 1), 4)
    return {
        "id": alert_id,
        "title": title,
        "description": description,
        "level": "high" if priority in {"urgent", "high"} else "medium",
        "priority": priority,
        "owner_team": team_for_records(records, topic),
        "count": count,
        "previous_count": previous_count,
        "change_rate": change_rate,
        "topic": topic,
        "product": product,
        "source_id": source_id,
        "record_ids": [record["id"] for record in records[:12]]
    }


def build_voc_alerts(records: list[dict], previous_records: list[dict]) -> list[dict]:
    alerts: list[dict] = []
    negative_records = records_negative(records)
    previous_negative_count = len(records_negative(previous_records))
    if len(negative_records) >= 2 and len(negative_records) >= previous_negative_count + 2:
        alerts.append(alert_payload(
            "negative_spike",
            "负向声量上升",
            "当前周期负向反馈较上一周期出现明显增加，需要优先判断是否为产品、客服或体验问题。",
            negative_records,
            previous_negative_count
        ))

    previous_by_topic = {
        item["topic"]: item["negative"]
        for item in topic_counts(previous_records)
    }
    for topic in topic_counts(records):
        if topic["topic"] == "other" or topic["negative"] < 2:
            continue
        scoped = [record for record in records if topic["topic"] in (record.get("topics") or []) and record.get("sentiment") == "negative"]
        topic_name = VOC_TOPIC_LABELS.get(topic["topic"], topic["topic"])
        alerts.append(alert_payload(
            f"topic_{topic['topic']}",
            f"{topic_name}相关问题集中",
            f"{topic['negative']} 条负向反馈集中在该主题，建议按责任团队拆解原因并跟进处理。",
            scoped,
            previous_by_topic.get(topic["topic"], 0),
            topic=topic["topic"]
        ))

    for product in product_breakdown(records):
        if not product["value"] or product["negative"] < 2:
            continue
        scoped = [
            record for record in records
            if clean_text(record.get("product")) == product["value"] and record.get("sentiment") == "negative"
        ]
        alerts.append(alert_payload(
            f"product_{compact_key(product['value'])}",
            f"{product['product']} 负向反馈集中",
            "该产品线在当前周期出现多条负向反馈，需要产品、客服或体验团队确认闭环方案。",
            scoped,
            0,
            product=product["value"]
        ))

    for channel in channel_breakdown(records):
        if channel["negative"] < 2 or channel["negative_rate"] < 0.35:
            continue
        scoped = [
            record for record in records
            if record.get("source_id") == channel["source_id"] and record.get("sentiment") == "negative"
        ]
        alerts.append(alert_payload(
            f"channel_{channel['source_id']}",
            f"{channel['name']} 负向比例偏高",
            "该渠道的负向反馈占比偏高，建议查看评论原文并确认是否需要对外回应。",
            scoped,
            0,
            source_id=channel["source_id"]
        ))

    deduped = {alert["id"]: alert for alert in alerts}
    return sorted(deduped.values(), key=lambda item: (item["level"] == "high", item["count"]), reverse=True)[:8]


def build_voc_conclusions(records: list[dict], previous_records: list[dict], days: int) -> list[dict]:
    if not records:
        return [{
            "title": "等待反馈入库",
            "detail": "当前筛选条件下还没有用户反馈，可先导入 CSV 或手动录入评论、邮件、工单和社群内容。",
            "tone": "neutral"
        }]

    total = len(records)
    negative = len(records_negative(records))
    previous_total = len(previous_records)
    top_topic = topic_counts(records)[0] if topic_counts(records) else None
    top_channel = channel_breakdown(records)[0] if channel_breakdown(records) else None
    top_product = product_breakdown(records)[0] if product_breakdown(records) else None
    conclusions = [{
        "title": f"近 {days} 天收集 {total} 条用户声音",
        "detail": f"负向 {negative} 条，占比 {round(negative_rate(records) * 100)}%；上一周期为 {previous_total} 条，总量变化 {total - previous_total:+d} 条。",
        "tone": "high" if negative_rate(records) >= 0.35 else "neutral"
    }]
    if top_topic:
        topic_name = VOC_TOPIC_LABELS.get(top_topic["topic"], top_topic["topic"])
        owner_name = VOC_OWNER_TEAM_LABELS.get(top_topic["owner_team"], top_topic["owner_team"])
        conclusions.append({
            "title": f"主要问题集中在{topic_name}",
            "detail": f"该主题出现 {top_topic['count']} 次，其中负向 {top_topic['negative']} 次，建议由{owner_name}牵头处理。",
            "tone": "high" if top_topic["negative"] >= 2 else "neutral"
        })
    if top_product and top_product["value"]:
        conclusions.append({
            "title": f"{top_product['product']} 是当前重点产品线",
            "detail": f"该产品线反馈 {top_product['count']} 条，负向 {top_product['negative']} 条，可作为产品维度复盘入口。",
            "tone": "medium" if top_product["negative"] else "neutral"
        })
    if top_channel:
        conclusions.append({
            "title": f"{top_channel['name']} 是主要反馈渠道",
            "detail": f"该渠道贡献 {top_channel['count']} 条反馈，负向占比 {round(top_channel['negative_rate'] * 100)}%。",
            "tone": "medium" if top_channel["negative_rate"] >= 0.35 else "neutral"
        })
    return conclusions


def voc_action_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["raw"] = json.loads(item.pop("raw_json") or "{}")
    record_body = item.pop("record_body", None)
    record_title = item.pop("record_title", None)
    record_sentiment = item.pop("record_sentiment", None)
    record_intent = item.pop("record_intent", None)
    record_occurred_at = item.pop("record_occurred_at", None)
    source_name = item.pop("source_name", None)
    if record_body is not None:
        item["record"] = {
            "title": record_title,
            "body": record_body,
            "sentiment": record_sentiment,
            "intent": record_intent,
            "occurred_at": record_occurred_at,
            "source_name": source_name
        }
    return item


def list_voc_actions(params: dict[str, list[str]]) -> list[dict]:
    clauses = []
    values = []
    status = first_param(params, "status")
    if status == "active":
        clauses.append("voc_actions.status NOT IN ('resolved', 'closed')")
    elif status:
        clauses.append("voc_actions.status = ?")
        values.append(status)
    for field in ("source_id", "product", "owner_team", "priority"):
        value = first_param(params, field)
        if value:
            clauses.append(f"voc_actions.{field} = ?")
            values.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = parse_int_param(params, "limit", 80, 1, 200)
    sql = f"""
        SELECT
          voc_actions.*,
          records.title AS record_title,
          records.body AS record_body,
          records.sentiment AS record_sentiment,
          records.intent AS record_intent,
          records.occurred_at AS record_occurred_at,
          sources.name AS source_name
        FROM voc_actions
        LEFT JOIN records ON records.id = voc_actions.record_id
        LEFT JOIN sources ON sources.id = COALESCE(voc_actions.source_id, records.source_id)
        {where}
        ORDER BY
          CASE voc_actions.status
            WHEN 'open' THEN 0
            WHEN 'assigned' THEN 1
            WHEN 'in_progress' THEN 2
            WHEN 'resolved' THEN 3
            ELSE 4
          END,
          voc_actions.updated_at DESC
        LIMIT ?
    """
    values.append(limit)
    with get_db() as conn:
        return [voc_action_to_dict(row) for row in conn.execute(sql, values)]


def get_voc_action(conn: sqlite3.Connection, action_id: str) -> dict:
    row = conn.execute(
        """
        SELECT
          voc_actions.*,
          records.title AS record_title,
          records.body AS record_body,
          records.sentiment AS record_sentiment,
          records.intent AS record_intent,
          records.occurred_at AS record_occurred_at,
          sources.name AS source_name
        FROM voc_actions
        LEFT JOIN records ON records.id = voc_actions.record_id
        LEFT JOIN sources ON sources.id = COALESCE(voc_actions.source_id, records.source_id)
        WHERE voc_actions.id = ?
        """,
        (action_id,)
    ).fetchone()
    if not row:
        raise ValueError("VoC action not found")
    return voc_action_to_dict(row)


def default_due_at(priority: str) -> str:
    days = {"urgent": 1, "high": 3, "medium": 7, "low": 14}.get(priority, 7)
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


def create_voc_action(payload: dict) -> dict:
    record_id = clean_text(payload.get("record_id"))
    record_ids = payload.get("record_ids")
    if not record_id and isinstance(record_ids, list) and record_ids:
        record_id = clean_text(record_ids[0])

    with get_db() as conn:
        record = None
        if record_id:
            row = conn.execute(
                """
                SELECT records.*, sources.name AS source_name, sources.category AS source_category
                FROM records JOIN sources ON sources.id = records.source_id
                WHERE records.id = ?
                """,
                (record_id,)
            ).fetchone()
            if row:
                record = row_to_dict(row)

        topic = clean_text(payload.get("topic")) or ((record.get("topics") or [""])[0] if record else "")
        source_id = clean_text(payload.get("source_id")) or (record.get("source_id") if record else "")
        product = clean_text(payload.get("product")) or (record.get("product") if record else "")
        owner_default = team_for_record(record, topic) if record else "marketing_team"
        priority_default = priority_for_records([record]) if record else "medium"
        owner_team = safe_owner_team(payload.get("owner_team"), owner_default)
        priority = safe_action_priority(payload.get("priority"), priority_default)
        status = safe_action_status(payload.get("status"), "open")
        title = clean_text(payload.get("title"))
        description = clean_text(payload.get("description"))
        if record and not title:
            title = clean_text(record.get("title")) or clean_text(record.get("body"))[:54]
        if not title:
            title = "跟进用户声音"
        if record and not description:
            description = clean_text(record.get("body"))

        now = utc_now()
        action = {
            "id": str(uuid.uuid4()),
            "record_id": record_id or None,
            "source_id": source_id or None,
            "title": title,
            "description": description,
            "owner_team": owner_team,
            "priority": priority,
            "status": status,
            "product": product or None,
            "topic": topic or None,
            "due_at": clean_text(payload.get("due_at")) or default_due_at(priority),
            "closed_at": now if status in {"resolved", "closed"} else None,
            "raw_json": json.dumps(payload, ensure_ascii=False),
            "created_at": now,
            "updated_at": now
        }
        conn.execute(
            """
            INSERT INTO voc_actions (
              id, record_id, source_id, title, description, owner_team, priority,
              status, product, topic, due_at, closed_at, raw_json, created_at, updated_at
            )
            VALUES (
              :id, :record_id, :source_id, :title, :description, :owner_team, :priority,
              :status, :product, :topic, :due_at, :closed_at, :raw_json, :created_at, :updated_at
            )
            """,
            action
        )
        return get_voc_action(conn, action["id"])


def update_voc_action(action_id: str, payload: dict) -> dict:
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM voc_actions WHERE id = ?", (action_id,)).fetchone()
        if not existing:
            raise ValueError("VoC action not found")
        status = safe_action_status(payload.get("status"), existing["status"])
        priority = safe_action_priority(payload.get("priority"), existing["priority"])
        owner_team = safe_owner_team(payload.get("owner_team"), existing["owner_team"])
        closed_at = existing["closed_at"]
        if status in {"resolved", "closed"} and not closed_at:
            closed_at = utc_now()
        elif status not in {"resolved", "closed"}:
            closed_at = None
        conn.execute(
            """
            UPDATE voc_actions
            SET title = ?, description = ?, owner_team = ?, priority = ?, status = ?,
                product = ?, topic = ?, due_at = ?, closed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                clean_text(payload.get("title")) or existing["title"],
                clean_text(payload.get("description")) or existing["description"],
                owner_team,
                priority,
                status,
                clean_text(payload.get("product")) or existing["product"],
                clean_text(payload.get("topic")) or existing["topic"],
                clean_text(payload.get("due_at")) or existing["due_at"],
                closed_at,
                utc_now(),
                action_id
            )
        )
        return get_voc_action(conn, action_id)


def delete_voc_action(action_id: str) -> dict:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM voc_actions WHERE id = ?", (action_id,))
        return {"deleted": cursor.rowcount}


def voc_summary_payload(params: dict[str, list[str]]) -> dict:
    days = parse_range_days(params, 30)
    today = datetime.now(timezone.utc).date()
    current_start = today - timedelta(days=max(days - 1, 0))
    previous_start = current_start - timedelta(days=days)
    previous_end = current_start - timedelta(days=1)

    records = query_records(params, current_start.isoformat(), today.isoformat(), limit=1000)
    previous_records = query_records(params, previous_start.isoformat(), previous_end.isoformat(), limit=1000)
    actions = list_voc_actions({**params, "limit": ["120"]})
    open_actions = [action for action in actions if action["status"] not in {"resolved", "closed"}]
    closed_actions = [action for action in actions if action["status"] in {"resolved", "closed"}]

    return {
        "range_days": days,
        "generated_at": utc_now(),
        "total_records": len(records),
        "negative_records": len(records_negative(records)),
        "negative_rate": negative_rate(records),
        "open_actions": len(open_actions),
        "closed_actions": len(closed_actions),
        "closure_rate": round(len(closed_actions) / len(actions), 4) if actions else 0.0,
        "trend": trend_breakdown(records, days),
        "channels": channel_breakdown(records),
        "products": product_breakdown(records),
        "topics": topic_counts(records)[:10],
        "conclusions": build_voc_conclusions(records, previous_records, days),
        "alerts": build_voc_alerts(records, previous_records),
        "actions": actions,
        "recent": records[:12]
    }


def parse_rss_datetime(value: str | None) -> str:
    if not value:
        return utc_now()
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return utc_now()


def html_fragment_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = VisibleTextParser()
    parser.feed(value)
    return parser.text or clean_text(re.sub(r"<[^>]+>", " ", value))


def normalize_media_region(value: str | None) -> str:
    region = clean_text(value or "US").upper()
    return region if re.fullmatch(r"[A-Z]{2}", region) else "US"


def normalize_media_language(value: str | None) -> str:
    language = clean_text(value or "en-US")
    return language if re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", language) else "en-US"


def google_news_search_url(query: str, region: str, language: str) -> str:
    language_code = language.split("-", 1)[0] or "en"
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl={quote_plus(language)}&gl={quote_plus(region)}&ceid={quote_plus(f'{region}:{language_code}')}"
    )


def fetch_google_news_mentions(query: str, region: str, language: str, limit: int = 25) -> list[dict]:
    url = google_news_search_url(query, region, language)
    request = Request(
        url,
        headers={
            "User-Agent": "MonitorIntelligenceHub/0.1 (+media monitoring)",
            "Accept": "application/rss+xml,application/xml,text/xml"
        }
    )
    with urlopen(request, timeout=18) as response:
        raw = response.read(1_500_000)
    root = ET.fromstring(raw)
    mentions = []
    for item in root.findall(".//item")[:max(1, min(limit, 100))]:
        source_node = item.find("source")
        link = clean_text(item.findtext("link"))
        title = clean_text(item.findtext("title"))
        description = html_fragment_to_text(item.findtext("description"))
        source_name = clean_text(source_node.text if source_node is not None else "")
        mentions.append({
            "title": title,
            "url": link,
            "source_name": source_name or host_key(link) or "Unknown publication",
            "published_at": parse_rss_datetime(item.findtext("pubDate")),
            "description": description,
            "guid": clean_text(item.findtext("guid")) or link,
            "rss_url": url
        })
    return mentions


def classify_media_property(title: str, body: str, publication: str, url: str) -> tuple[str, float, str]:
    haystack = " ".join([title, body, publication, url]).lower()
    matched = [term for term in PAID_PR_TERMS if term in haystack]
    if matched:
        return "paid_pr", 0.82, f"matched {matched[0]}"
    if publication.lower() in {"pr newswire", "business wire", "globenewswire"}:
        return "paid_pr", 0.86, "wire publication"
    return "earned", 0.64, "no paid-placement marker"


def estimate_publication_metrics(publication: str, url: str) -> tuple[int, str]:
    key = f"{publication} {host_key(url)}".lower()
    for needle, reach, tier in PUBLICATION_REACH_HINTS:
        if needle in key:
            return reach, tier
    if any(term in key for term in ("times", "post", "journal", "tribune", "news", "daily", "business")):
        return 220_000, "tier_3"
    if host_key(url):
        return 80_000, "tier_4"
    return 25_000, "unknown"


def detect_pr_themes(text: str) -> list[str]:
    lowered = text.lower()
    themes = [
        theme for theme, terms in PR_THEME_TERMS.items()
        if any(term in lowered or term in text for term in terms)
    ]
    return themes[:4] or ["general_coverage"]


def media_record_payload(monitor: sqlite3.Row, mention: dict) -> dict:
    title = mention.get("title") or "Untitled media mention"
    body = clean_text(mention.get("description")) or title
    publication = mention.get("source_name") or host_key(mention.get("url")) or "Unknown publication"
    coverage_type, confidence, reason = classify_media_property(title, body, publication, mention.get("url", ""))
    estimated_reach, media_tier = estimate_publication_metrics(publication, mention.get("url", ""))
    themes = detect_pr_themes(f"{title} {body}")
    external_basis = f"{monitor['id']}:{mention.get('guid') or mention.get('url') or title}"
    return {
        "source_id": "google_news",
        "external_id": f"media:{hashlib.sha1(external_basis.encode('utf-8')).hexdigest()}",
        "monitor_id": monitor["id"],
        "data_type": "media_mention",
        "platform": "News",
        "title": title,
        "author": publication,
        "body": body,
        "url": mention.get("url"),
        "brand": monitor["brand_name"],
        "region": monitor["region"],
        "language": monitor["language"],
        "occurred_at": mention.get("published_at") or utc_now(),
        "coverage_type": coverage_type,
        "coverage_confidence": confidence,
        "coverage_reason": reason,
        "estimated_reach": estimated_reach,
        "media_tier": media_tier,
        "publication": publication,
        "source_domain": host_key(mention.get("url")),
        "pr_themes": themes,
        "rss_url": mention.get("rss_url")
    }


def insert_record_if_new(conn: sqlite3.Connection, payload: dict) -> dict | None:
    external_id = payload.get("external_id")
    source_id = payload.get("source_id") or "manual_csv"
    if external_id:
        existing = conn.execute(
            "SELECT id FROM records WHERE source_id = ? AND external_id = ? LIMIT 1",
            (source_id, external_id)
        ).fetchone()
        if existing:
            return None
    return insert_record(conn, payload)


def media_mention_to_dict(row: sqlite3.Row) -> dict:
    item = row_to_dict(row)
    raw = json.loads(item.pop("raw_json", "{}") or "{}")
    item["raw"] = raw
    item["publication"] = raw.get("publication") or item.get("author") or item.get("source_name")
    item["coverage_type"] = raw.get("coverage_type") or "earned"
    item["coverage_confidence"] = raw.get("coverage_confidence") or 0
    item["coverage_reason"] = raw.get("coverage_reason") or ""
    item["estimated_reach"] = raw.get("estimated_reach") or 0
    item["media_tier"] = raw.get("media_tier") or "unknown"
    item["source_domain"] = raw.get("source_domain") or host_key(item.get("url"))
    item["pr_themes"] = raw.get("pr_themes") or detect_pr_themes(f"{item.get('title', '')} {item.get('body', '')}")
    return item


def list_media_mentions(params: dict[str, list[str]]) -> list[dict]:
    clauses = ["records.data_type = 'media_mention'", "records.monitor_id IS NOT NULL"]
    values: list[str] = []
    monitor_id = params.get("monitor_id", [""])[0]
    if monitor_id:
        clauses.append("records.monitor_id = ?")
        values.append(monitor_id)
    days = int(params.get("days", ["30"])[0] or "30")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days - 1, 0))).date().isoformat()
    clauses.append("substr(records.occurred_at, 1, 10) >= ?")
    values.append(cutoff)
    query = params.get("q", [""])[0].strip()
    if query:
        clauses.append("(records.title LIKE ? OR records.body LIKE ? OR records.author LIKE ? OR records.brand LIKE ?)")
        like = f"%{query}%"
        values.extend([like, like, like, like])
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT records.*, sources.name AS source_name
            FROM records JOIN sources ON sources.id = records.source_id
            WHERE {' AND '.join(clauses)}
            ORDER BY records.occurred_at DESC
            LIMIT 300
            """,
            values
        ).fetchall()
        return [media_mention_to_dict(row) for row in rows]


def media_monitor_to_dict(conn: sqlite3.Connection, row: sqlite3.Row, days: int = 30) -> dict:
    monitor = dict(row)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days - 1, 0))).date().isoformat()
    records = conn.execute(
        """
        SELECT records.*, sources.name AS source_name
        FROM records JOIN sources ON sources.id = records.source_id
        WHERE records.monitor_id = ?
          AND records.data_type = 'media_mention'
          AND substr(records.occurred_at, 1, 10) >= ?
        ORDER BY records.occurred_at DESC
        """,
        (row["id"], cutoff)
    ).fetchall()
    mentions = [media_mention_to_dict(record) for record in records]
    monitor["mentions"] = len(mentions)
    monitor["estimated_reach"] = sum(int(item.get("estimated_reach") or 0) for item in mentions)
    monitor["earned_mentions"] = sum(1 for item in mentions if item.get("coverage_type") == "earned")
    monitor["paid_mentions"] = sum(1 for item in mentions if item.get("coverage_type") != "earned")
    monitor["latest_mention"] = mentions[0] if mentions else None
    return monitor


def list_media_monitors(params: dict[str, list[str]]) -> list[dict]:
    days = int(params.get("days", ["30"])[0] or "30")
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM media_monitors ORDER BY updated_at DESC").fetchall()
        return [media_monitor_to_dict(conn, row, days) for row in rows]


def create_media_monitor(payload: dict) -> dict:
    brand_id = clean_text(payload.get("brand_id"))
    brand_name = clean_text(payload.get("brand_name"))
    query = clean_text(payload.get("query"))
    with get_db() as conn:
        if brand_id:
            brand = conn.execute("SELECT * FROM brands WHERE id = ?", (brand_id,)).fetchone()
            if brand:
                brand_payload = row_to_dict(brand)
                brand_name = brand_name or brand_payload.get("name") or ""
                keywords = brand_payload.get("monitoring_keywords") or []
                query = query or (f'"{keywords[0]}"' if keywords else f'"{brand_name}"')
    if not brand_name:
        brand_name = query.strip('"') if query else ""
    if not query and brand_name:
        query = f'"{brand_name}"'
    if not brand_name or not query:
        raise ValueError("Brand name or query is required")

    now = utc_now()
    monitor = {
        "id": str(uuid.uuid4()),
        "brand_id": brand_id or None,
        "brand_name": brand_name,
        "query": query,
        "region": normalize_media_region(payload.get("region")),
        "language": normalize_media_language(payload.get("language")),
        "status": payload.get("status") or "active",
        "cadence": "daily",
        "last_scan_at": None,
        "last_status": "pending",
        "last_error": "",
        "created_at": now,
        "updated_at": now
    }
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO media_monitors (
              id, brand_id, brand_name, query, region, language, status, cadence,
              last_scan_at, last_status, last_error, created_at, updated_at
            )
            VALUES (
              :id, :brand_id, :brand_name, :query, :region, :language, :status, :cadence,
              :last_scan_at, :last_status, :last_error, :created_at, :updated_at
            )
            """,
            monitor
        )
        created = media_monitor_to_dict(conn, conn.execute("SELECT * FROM media_monitors WHERE id = ?", (monitor["id"],)).fetchone())

    if payload.get("scan_now", True):
        try:
            scan_media_monitor(monitor["id"])
        except Exception:
            pass
        with get_db() as conn:
            created = media_monitor_to_dict(conn, conn.execute("SELECT * FROM media_monitors WHERE id = ?", (monitor["id"],)).fetchone())
    return created


def update_media_monitor(monitor_id: str, payload: dict) -> dict:
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM media_monitors WHERE id = ?", (monitor_id,)).fetchone()
        if not existing:
            raise ValueError("Media monitor not found")
        brand_name = clean_text(payload.get("brand_name")) or existing["brand_name"]
        query = clean_text(payload.get("query")) or existing["query"]
        status = payload.get("status") or existing["status"]
        region = normalize_media_region(payload.get("region") or existing["region"])
        language = normalize_media_language(payload.get("language") or existing["language"])
        conn.execute(
            """
            UPDATE media_monitors
            SET brand_name = ?, query = ?, region = ?, language = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (brand_name, query, region, language, status, utc_now(), monitor_id)
        )
        return media_monitor_to_dict(conn, conn.execute("SELECT * FROM media_monitors WHERE id = ?", (monitor_id,)).fetchone())


def scan_media_monitor(monitor_id: str) -> dict:
    with get_db() as conn:
        monitor = conn.execute("SELECT * FROM media_monitors WHERE id = ?", (monitor_id,)).fetchone()
        if not monitor:
            raise ValueError("Media monitor not found")

    created = 0
    scanned = 0
    status = "ready"
    error = ""
    try:
        mentions = fetch_google_news_mentions(monitor["query"], monitor["region"], monitor["language"])
        scanned = len(mentions)
        with get_db() as conn:
            for mention in mentions:
                payload = media_record_payload(monitor, mention)
                if insert_record_if_new(conn, payload):
                    created += 1
    except Exception as exc:
        status = "error"
        error = str(exc)

    now = utc_now()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE media_monitors
            SET last_scan_at = ?, last_status = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, status, error, now, monitor_id)
        )
        saved = media_monitor_to_dict(conn, conn.execute("SELECT * FROM media_monitors WHERE id = ?", (monitor_id,)).fetchone())
    if error:
        raise ValueError(error)
    return {"monitor": saved, "created": created, "scanned": scanned}


def media_summary_payload(params: dict[str, list[str]]) -> dict:
    days = int(params.get("days", ["30"])[0] or "30")
    monitor_id = params.get("monitor_id", [""])[0]
    mentions = list_media_mentions(params)
    daily: dict[str, dict] = {}
    for offset in range(days):
        date = (datetime.now(timezone.utc) - timedelta(days=offset)).date().isoformat()
        daily[date] = {"date": date, "mentions": 0, "estimated_reach": 0}

    publications: dict[str, dict] = {}
    themes: dict[str, int] = {}
    sentiment: dict[str, int] = {"positive": 0, "neutral": 0, "negative": 0}
    coverage_mix: dict[str, int] = {"earned": 0, "paid_pr": 0}
    for item in mentions:
        date = str(item.get("occurred_at", ""))[:10]
        if date:
            bucket = daily.setdefault(date, {"date": date, "mentions": 0, "estimated_reach": 0})
            bucket["mentions"] += 1
            bucket["estimated_reach"] += int(item.get("estimated_reach") or 0)
        publication = item.get("publication") or "Unknown publication"
        pub = publications.setdefault(publication, {"publication": publication, "mentions": 0, "estimated_reach": 0})
        pub["mentions"] += 1
        pub["estimated_reach"] += int(item.get("estimated_reach") or 0)
        for theme in item.get("pr_themes") or []:
            themes[theme] = themes.get(theme, 0) + 1
        sentiment[item.get("sentiment") or "neutral"] = sentiment.get(item.get("sentiment") or "neutral", 0) + 1
        coverage_type = item.get("coverage_type") or "earned"
        coverage_mix[coverage_type] = coverage_mix.get(coverage_type, 0) + 1

    with get_db() as conn:
        active_monitors = conn.execute("SELECT COUNT(*) AS count FROM media_monitors WHERE status = 'active'").fetchone()["count"]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days - 1, 0))).date().isoformat()
        sov_clauses = ["records.data_type = 'media_mention'", "substr(records.occurred_at, 1, 10) >= ?"]
        sov_values: list[str] = [cutoff]
        if monitor_id:
            sov_clauses.append("records.monitor_id = ?")
            sov_values.append(monitor_id)
        sov_rows = conn.execute(
            f"""
            SELECT media_monitors.brand_name, records.monitor_id, COUNT(*) AS mentions
            FROM records
            JOIN media_monitors ON media_monitors.id = records.monitor_id
            WHERE {' AND '.join(sov_clauses)}
            GROUP BY records.monitor_id
            ORDER BY mentions DESC
            """,
            sov_values
        ).fetchall()

    total_mentions = len(mentions)
    total_reach = sum(int(item.get("estimated_reach") or 0) for item in mentions)
    top_publications = sorted(publications.values(), key=lambda item: (item["mentions"], item["estimated_reach"]), reverse=True)[:8]
    pr_directions = sorted(
        [{"theme": theme, "count": count} for theme, count in themes.items()],
        key=lambda item: item["count"],
        reverse=True
    )[:8]
    share_total = sum(row["mentions"] for row in sov_rows) or 1
    share_of_voice = [
        {
            "brand_name": row["brand_name"],
            "monitor_id": row["monitor_id"],
            "mentions": row["mentions"],
            "share": round(row["mentions"] / share_total, 4)
        }
        for row in sov_rows
    ]

    return {
        "range_days": days,
        "generated_at": utc_now(),
        "active_monitors": active_monitors,
        "total_mentions": total_mentions,
        "estimated_reach": total_reach,
        "earned_mentions": coverage_mix.get("earned", 0),
        "paid_mentions": sum(count for key, count in coverage_mix.items() if key != "earned"),
        "coverage_mix": coverage_mix,
        "sentiment": sentiment,
        "daily": sorted(daily.values(), key=lambda item: item["date"]),
        "top_publications": top_publications,
        "pr_directions": pr_directions,
        "share_of_voice": share_of_voice,
        "recent": mentions[:8]
    }


def run_due_media_scans() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM media_monitors
            WHERE status = 'active'
              AND (
                last_scan_at IS NULL
                OR substr(last_scan_at, 1, 10) < ?
              )
            ORDER BY created_at ASC
            """,
            (today,)
        ).fetchall()
    count = 0
    for row in rows:
        try:
            scan_media_monitor(row["id"])
            count += 1
        except Exception as exc:
            with get_db() as conn:
                conn.execute(
                    "UPDATE media_monitors SET last_status = 'error', last_error = ?, updated_at = ? WHERE id = ?",
                    (str(exc), utc_now(), row["id"])
                )
    return count


def media_monitor_scheduler() -> None:
    while True:
        try:
            run_due_media_scans()
            run_due_marketing_link_collections()
        except Exception:
            pass
        time.sleep(max(MONITOR_SCHEDULER_SECONDS, 60))


def start_media_monitor_scheduler() -> None:
    if os.environ.get("MONITOR_SCHEDULER", "1") == "0":
        return
    thread = threading.Thread(target=media_monitor_scheduler, daemon=True)
    thread.start()


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
        elif parsed.path == "/api/sales-channel-brands":
            self._json(list_sales_channel_brands(parse_qs(parsed.query)))
        elif parsed.path == "/api/sales-channels":
            self._json(list_sales_channels(parse_qs(parsed.query)))
        elif parsed.path == "/api/sources":
            with get_db() as conn:
                self._json([dict(row) for row in conn.execute("SELECT * FROM sources ORDER BY category, name")])
        elif parsed.path == "/api/records":
            self._json(list_records(parse_qs(parsed.query)))
        elif parsed.path == "/api/voc-summary":
            self._json(voc_summary_payload(parse_qs(parsed.query)))
        elif parsed.path == "/api/voc-actions":
            self._json(list_voc_actions(parse_qs(parsed.query)))
        elif parsed.path == "/api/community-brands":
            self._json(list_community_brands(parse_qs(parsed.query)))
        elif parsed.path == "/api/community-sources":
            params = parse_qs(parsed.query)
            with get_db() as conn:
                self._json(list_community_sources(conn, first_param(params, "brand_id"), include_records=True))
        elif parsed.path == "/api/community-records":
            self._json(list_community_records(parse_qs(parsed.query)))
        elif parsed.path == "/api/community-summary":
            self._json(community_summary_payload(parse_qs(parsed.query)))
        elif parsed.path == "/api/media-monitors":
            self._json(list_media_monitors(parse_qs(parsed.query)))
        elif parsed.path == "/api/media-mentions":
            self._json(list_media_mentions(parse_qs(parsed.query)))
        elif parsed.path == "/api/media-summary":
            self._json(media_summary_payload(parse_qs(parsed.query)))
        elif parsed.path == "/api/marketing-links":
            self._json(list_marketing_links(parse_qs(parsed.query)))
        elif parsed.path == "/api/marketing-link-summary":
            self._json(marketing_link_summary(parse_qs(parsed.query)))
        elif parsed.path == "/api/web-monitors":
            self._json(list_web_monitors(parse_qs(parsed.query)))
        elif parsed.path == "/api/web-snapshots":
            self._json(list_web_snapshots(parse_qs(parsed.query)))
        elif parsed.path == "/api/web-monitor-summary":
            self._json(web_monitor_summary_payload(parse_qs(parsed.query)))
        elif parsed.path.startswith("/api/capture-jobs/"):
            try:
                self._json(capture_job_payload(parsed.path.rsplit("/", 1)[-1]))
            except Exception as exc:
                self._json({"error": str(exc)}, status=404)
        elif parsed.path.startswith("/snapshots/"):
            self._serve_snapshot_asset(parsed.path)
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
            elif parsed.path == "/api/voc-actions":
                self._json(create_voc_action(payload), status=201)
            elif parsed.path == "/api/community-brands":
                self._json(upsert_community_brand(payload), status=201)
            elif parsed.path == "/api/community-sources":
                self._json(upsert_community_source(payload), status=201)
            elif parsed.path.startswith("/api/community-sources/") and parsed.path.endswith("/collect"):
                source_id = parsed.path.split("/")[-2]
                self._json(collect_community_source(source_id), status=201)
            elif parsed.path == "/api/brands/analyze":
                draft = analyze_brand_url(payload.get("url", ""))
                with get_db() as conn:
                    draft["duplicate_candidates"] = duplicate_candidates(conn, draft)
                self._json(draft)
            elif parsed.path == "/api/brands":
                with get_db() as conn:
                    brand = upsert_brand(conn, payload)
                self._json(row_to_dict(brand), status=201)
            elif parsed.path == "/api/sales-channel-discovery":
                self._json(discover_sales_channel_candidates(payload))
            elif parsed.path == "/api/sales-channel-brands/discover":
                self._json(create_sales_channel_brand_from_discovery(payload), status=201)
            elif parsed.path == "/api/sales-channel-brands":
                self._json(upsert_sales_channel_brand(payload), status=201)
            elif parsed.path == "/api/sales-channel-links":
                self._json(upsert_sales_channel_link(payload), status=201)
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
            elif parsed.path == "/api/media-monitors":
                monitor = create_media_monitor(payload)
                self._json(monitor, status=201)
            elif parsed.path.startswith("/api/media-monitors/") and parsed.path.endswith("/scan"):
                monitor_id = parsed.path.split("/")[-2]
                self._json(scan_media_monitor(monitor_id), status=201)
            elif parsed.path == "/api/marketing-links":
                self._json(upsert_marketing_link(payload), status=201)
            elif parsed.path.startswith("/api/marketing-links/") and parsed.path.endswith("/collect"):
                link_id = parsed.path.split("/")[-2]
                self._json(collect_marketing_link(link_id), status=201)
            elif parsed.path == "/api/web-monitors":
                monitor = create_web_monitor(payload)
                self._json(monitor, status=201)
            elif parsed.path.startswith("/api/web-monitors/") and parsed.path.endswith("/capture-job"):
                monitor_id = parsed.path.split("/")[-2]
                self._json(start_capture_job(monitor_id), status=202)
            elif parsed.path.startswith("/api/web-monitors/") and parsed.path.endswith("/capture"):
                monitor_id = parsed.path.split("/")[-2]
                snapshots = capture_monitor_pages(monitor_id)
                self._json({"snapshots": snapshots}, status=201)
            else:
                self._json({"error": "Not found"}, status=404)
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/voc-actions/"):
                action_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json()
                self._json(update_voc_action(action_id, payload))
                return
            if parsed.path.startswith("/api/sales-channel-brands/"):
                brand_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json()
                self._json(upsert_sales_channel_brand(payload, brand_id=brand_id))
                return
            if parsed.path.startswith("/api/sales-channel-links/"):
                link_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json()
                self._json(upsert_sales_channel_link(payload, link_id=link_id))
                return
            if parsed.path.startswith("/api/community-brands/"):
                brand_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json()
                self._json(upsert_community_brand(payload, brand_id=brand_id))
                return
            if parsed.path.startswith("/api/community-sources/"):
                source_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json()
                self._json(upsert_community_source(payload, source_id=source_id))
                return
            if parsed.path.startswith("/api/media-monitors/"):
                monitor_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json()
                self._json(update_media_monitor(monitor_id, payload))
                return
            if parsed.path.startswith("/api/marketing-links/"):
                link_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json()
                self._json(upsert_marketing_link(payload, link_id=link_id))
                return
            if parsed.path.startswith("/api/web-monitors/"):
                monitor_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json()
                self._json(update_web_monitor(monitor_id, payload))
                return
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
            if parsed.path.startswith("/api/voc-actions/"):
                action_id = parsed.path.rsplit("/", 1)[-1]
                self._json(delete_voc_action(action_id))
                return
            if parsed.path.startswith("/api/sales-channel-brands/"):
                brand_id = parsed.path.rsplit("/", 1)[-1]
                self._json(delete_sales_channel_brand(brand_id))
                return
            if parsed.path.startswith("/api/sales-channel-links/"):
                link_id = parsed.path.rsplit("/", 1)[-1]
                self._json(delete_sales_channel_link(link_id))
                return
            if parsed.path.startswith("/api/community-brands/"):
                brand_id = parsed.path.rsplit("/", 1)[-1]
                self._json(delete_community_brand(brand_id))
                return
            if parsed.path.startswith("/api/community-sources/"):
                source_id = parsed.path.rsplit("/", 1)[-1]
                self._json(delete_community_source(source_id))
                return
            if parsed.path.startswith("/api/media-monitors/"):
                monitor_id = parsed.path.rsplit("/", 1)[-1]
                with get_db() as conn:
                    conn.execute("DELETE FROM records WHERE monitor_id = ? AND data_type = 'media_mention'", (monitor_id,))
                    cursor = conn.execute("DELETE FROM media_monitors WHERE id = ?", (monitor_id,))
                self._json({"deleted": cursor.rowcount})
                return
            if parsed.path.startswith("/api/marketing-links/"):
                link_id = parsed.path.rsplit("/", 1)[-1]
                self._json(delete_marketing_link(link_id))
                return
            if parsed.path.startswith("/api/web-monitors/"):
                monitor_id = parsed.path.rsplit("/", 1)[-1]
                with get_db() as conn:
                    snapshot_rows = conn.execute(
                        "SELECT screenshot_path, html_path FROM web_snapshots WHERE monitor_id = ?",
                        (monitor_id,)
                    ).fetchall()
                    delete_snapshot_files(snapshot_rows)
                    conn.execute("DELETE FROM web_snapshots WHERE monitor_id = ?", (monitor_id,))
                    cursor = conn.execute("DELETE FROM web_monitors WHERE id = ?", (monitor_id,))
                self._json({"deleted": cursor.rowcount})
                return
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

    def _serve_snapshot_asset(self, request_path: str) -> None:
        rel_path = unquote_plus(request_path.removeprefix("/snapshots/"))
        file_path = (SNAPSHOT_DIR / rel_path).resolve()
        try:
            file_path.relative_to(SNAPSHOT_DIR.resolve())
        except ValueError:
            self._json({"error": "Invalid snapshot path"}, status=403)
            return
        if not file_path.exists():
            self._json({"error": "Snapshot not found"}, status=404)
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
    start_web_monitor_scheduler()
    start_media_monitor_scheduler()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Monitor server running at http://127.0.0.1:{PORT}")
    server.serve_forever()
