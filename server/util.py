"""Shared text / URL / HTML helpers used across connectors and domains."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

AMAZON_MARKETS = {
    "amazon.com": "US",
    "amazon.co.uk": "UK",
    "amazon.ca": "CA",
    "amazon.de": "DE",
    "amazon.fr": "FR",
    "amazon.it": "IT",
    "amazon.es": "ES",
    "amazon.com.au": "AU",
    "amazon.co.jp": "JP",
}

SOCIAL_HOSTS = {
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "facebook": ("facebook.com", "fb.com"),
    "reddit": ("reddit.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "x": ("x.com", "twitter.com"),
    "linkedin": ("linkedin.com",),
    "pinterest": ("pinterest.com",),
}

GENERIC_SOCIAL_PATHS = {
    "instagram": {"", "about", "accounts", "explore", "p", "reel", "stories"},
    "tiktok": {"", "about", "discover", "embed", "tag", "music"},
    "facebook": {"", "share", "sharer", "dialog", "plugins"},
    "reddit": {"", "submit", "search"},
    "youtube": {"", "watch", "embed", "shorts", "playlist", "results"},
    "x": {"", "intent", "share", "search", "hashtag", "i"},
    "linkedin": {"", "company", "in", "feed", "shareArticle"},
    "pinterest": {"", "pin", "search"},
}


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def compact_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def normalize_url(value: str) -> str:
    url = (value or "").strip()
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


def root_url(value: str) -> str:
    parsed = urlparse(normalize_url(value))
    return f"{parsed.scheme}://{parsed.netloc}/"


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


def is_amazon_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().replace("www.", "")
    return any(host == domain or host.endswith(f".{domain}") for domain in AMAZON_MARKETS)


def amazon_market(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    for domain, market in AMAZON_MARKETS.items():
        if host == domain or host.endswith(f".{domain}"):
            return market
    return ""


def extract_asin(url: str) -> str:
    match = re.search(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})(?:[/?]|$)", url, re.I)
    return match.group(1).upper() if match else ""


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


def extract_visible_text(html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(html)
    return parser.text


def html_fragment_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = VisibleTextParser()
    parser.feed(value)
    return parser.text or clean_text(re.sub(r"<[^>]+>", " ", value))
