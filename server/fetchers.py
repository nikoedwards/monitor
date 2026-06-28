"""HTTP fetch helpers with a basic SSRF guard, plus page/RSS parsing."""
from __future__ import annotations

import ipaddress
import json
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, ProxyHandler, urlopen
from xml.etree import ElementTree as ET

from .config import USER_AGENT
from .util import (
    PageMetadataParser,
    clean_text,
    extract_visible_text,
    host_key,
    html_fragment_to_text,
    normalize_url,
    parse_rss_datetime,
    resolved_icon_url,
)

ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class FetchError(RuntimeError):
    pass


def _is_blocked_host(hostname: str) -> bool:
    """Reject obvious internal/private targets to mitigate SSRF.

    Note: we intentionally do NOT pre-resolve DNS. Many local setups route
    traffic through a proxy with "fake-IP" DNS (e.g. 198.18.0.0/15), which
    would make DNS-based checks reject legitimate public hosts. For a local
    single-user tool, blocking literal private/loopback IPs plus localhost
    hostnames is a pragmatic guard that still works behind a proxy.
    """
    host = (hostname or "").lower().strip("[]")
    if not host:
        return True
    if host in {"localhost", "ip6-localhost"} or host.endswith(".local") or host.endswith(".internal"):
        return host not in ALLOWED_LOCAL_HOSTS
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A domain name: allow (request will fail naturally if unreachable).
        return False
    if str(ip) in ALLOWED_LOCAL_HOSTS:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast


def _open(request: Request, timeout: int):
    hostname = (urlparse(request.full_url).hostname or "").lower()
    if _is_blocked_host(hostname):
        raise FetchError(f"Blocked or unresolvable host: {hostname}")
    if hostname in ALLOWED_LOCAL_HOSTS:
        return build_opener(ProxyHandler({})).open(request, timeout=timeout)
    return urlopen(request, timeout=timeout)


def fetch_bytes(url: str, *, accept: str = "*/*", timeout: int = 16, max_bytes: int = 3_000_000) -> bytes:
    request = Request(normalize_url(url), headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with _open(request, timeout) as response:
            return response.read(max_bytes)
    except (URLError, OSError, ValueError) as exc:
        raise FetchError(str(exc)) from exc


def fetch_text(url: str, *, accept: str = "text/html,application/xhtml+xml", timeout: int = 16) -> str:
    request = Request(normalize_url(url), headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with _open(request, timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(3_000_000)
    except (URLError, OSError, ValueError) as exc:
        raise FetchError(str(exc)) from exc
    return raw.decode(charset, errors="replace")


def fetch_json(url: str, *, timeout: int = 16, headers: dict | None = None) -> dict | list:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    merged.update(headers or {})
    request = Request(normalize_url(url), headers=merged)
    try:
        with _open(request, timeout) as response:
            raw = response.read(3_000_000)
    except (URLError, OSError, ValueError) as exc:
        raise FetchError(str(exc)) from exc
    return json.loads(raw.decode("utf-8", errors="replace"))


def fetch_page(input_url: str, *, timeout: int = 18) -> dict:
    """Fetch a page and return parsed metadata + visible text."""
    url = normalize_url(input_url)
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    try:
        with _open(request, timeout) as response:
            final_url = response.geturl()
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(3_000_000)
    except (URLError, OSError, ValueError) as exc:
        raise FetchError(str(exc)) from exc
    html = raw.decode(charset, errors="replace")
    metadata = PageMetadataParser()
    metadata.feed(html)
    title = clean_text(metadata.meta.get("og:title") or metadata.title)
    icon_href = (
        metadata.links.get("icon")
        or metadata.links.get("shortcut icon")
        or metadata.links.get("apple-touch-icon")
    )
    return {
        "requested_url": url,
        "final_url": final_url,
        "title": title or host_key(final_url) or url,
        "html": html,
        "text": extract_visible_text(html),
        "meta": metadata.meta,
        "anchors": metadata.anchors,
        "json_ld": metadata.json_ld,
        "icon_url": resolved_icon_url(final_url, icon_href),
        "anchor_count": len(metadata.anchors),
        "json_ld_count": len(metadata.json_ld),
    }


def parse_rss(raw: bytes, *, limit: int = 50) -> list[dict]:
    """Parse an RSS/Atom feed into a list of normalized item dicts."""
    root = ET.fromstring(raw)
    items: list[dict] = []
    # RSS <item>
    for item in root.findall(".//item")[: max(1, limit)]:
        source_node = item.find("source")
        link = clean_text(item.findtext("link"))
        items.append(
            {
                "title": clean_text(item.findtext("title")),
                "url": link,
                "source_name": clean_text(source_node.text if source_node is not None else ""),
                "source_url": clean_text(source_node.get("url") if source_node is not None else ""),
                "published_at": parse_rss_datetime(item.findtext("pubDate")),
                "description": html_fragment_to_text(item.findtext("description")),
                "guid": clean_text(item.findtext("guid")) or link,
            }
        )
    if items:
        return items
    # Atom <entry>
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//a:entry", ns)[: max(1, limit)]:
        link_node = entry.find("a:link", ns)
        link = clean_text(link_node.get("href") if link_node is not None else "")
        items.append(
            {
                "title": clean_text(entry.findtext("a:title", default="", namespaces=ns)),
                "url": link,
                "source_name": "",
                "source_url": "",
                "published_at": parse_rss_datetime(
                    entry.findtext("a:updated", default="", namespaces=ns)
                    or entry.findtext("a:published", default="", namespaces=ns)
                ),
                "description": html_fragment_to_text(
                    entry.findtext("a:summary", default="", namespaces=ns)
                    or entry.findtext("a:content", default="", namespaces=ns)
                ),
                "guid": clean_text(entry.findtext("a:id", default="", namespaces=ns)) or link,
            }
        )
    return items
