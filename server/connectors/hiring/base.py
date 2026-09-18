"""Hiring provider adapter contract + a cookie-aware page renderer.

A ``HiringProvider`` knows how to (1) expand a configured hiring source link
(company page / keyword search) into individual job postings, and (2) fetch a
snapshot of one posting (JD text + open/closed status). A ``PeopleProvider``
does the LinkedIn employee roster + activity feed.

All scraping is best-effort: Boss 直聘 and LinkedIn are heavily anti-bot, so a
provider that cannot read a page returns a ``blocked``/``error`` snapshot rather
than raising. The runner keeps going and records the status per row.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from ...fetchers import FetchError, fetch_page
from ...util import extract_visible_text

# Lazily-probed Playwright availability (mirrors server/snapshot.py).
_PLAYWRIGHT_AVAILABLE: bool | None = None


@dataclass
class RenderResult:
    html: str = ""
    text: str = ""
    title: str = ""
    final_url: str = ""
    anchors: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    status: str = "ok"          # ok | blocked | error
    method: str = ""            # playwright | http
    error: str = ""


def _cookies_for(cookie_header: str, url: str) -> list[dict]:
    """Convert a raw `name=value; name2=value2` cookie header into Playwright cookies."""
    host = (urlparse(url).hostname or "").lower()
    # Set on the registrable-ish domain so subdomains share the session.
    parts = host.split(".")
    domain = "." + ".".join(parts[-2:]) if len(parts) >= 2 else host
    cookies: list[dict] = []
    for chunk in (cookie_header or "").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        cookies.append({"name": name, "value": value, "domain": domain, "path": "/"})
    return cookies


def _render_playwright(url: str, cookie_header: str, wait_ms: int) -> RenderResult | None:
    global _PLAYWRIGHT_AVAILABLE
    if _PLAYWRIGHT_AVAILABLE is False:
        return None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        _PLAYWRIGHT_AVAILABLE = False
        return None

    result = RenderResult(method="playwright")
    browser = None
    context = None
    page = None
    try:
        with sync_playwright() as pw:
            # Anti-bot pages can leave navigation waiting indefinitely. Keep
            # each browser attempt bounded and always close every resource in
            # the finally block below so repeated scheduled runs cannot leak
            # Chromium processes/threads until the container is exhausted.
            browser = pw.chromium.launch(headless=True, timeout=20000)
            context = browser.new_context(
                viewport={"width": 1440, "height": 1600},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            cookies = _cookies_for(cookie_header, url)
            if cookies:
                try:
                    context.add_cookies(cookies)
                except Exception:
                    pass
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            result.html = page.content()
            result.final_url = page.url
            result.title = page.title()
            try:
                result.anchors = page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.getAttribute('href'))"
                ) or []
            except Exception:
                result.anchors = []
            try:
                result.meta = page.eval_on_selector_all(
                    "meta[name], meta[property]",
                    "els => Object.fromEntries(els.map(e => [e.getAttribute('property') || e.getAttribute('name'), e.getAttribute('content') || '']).filter(([k]) => k))",
                ) or {}
            except Exception:
                result.meta = {}
        _PLAYWRIGHT_AVAILABLE = True
        result.text = extract_visible_text(result.html)
        return result
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        result.status = "error"
        result.error = str(exc)[:300]
        return result
    finally:
        for resource in (page, context, browser):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                pass


def _render_http(url: str) -> RenderResult:
    result = RenderResult(method="http")
    try:
        page = fetch_page(url)
    except FetchError as exc:
        result.status = "error"
        result.error = str(exc)[:300]
        return result
    result.html = page.get("html") or ""
    result.text = page.get("text") or ""
    result.title = page.get("title") or ""
    result.final_url = page.get("final_url") or url
    result.anchors = page.get("anchors") or []
    result.meta = page.get("meta") or {}
    return result


def render(url: str, cookie_header: str = "", wait_ms: int = 2500) -> RenderResult:
    """Render a page with cookies via Playwright, falling back to a plain HTTP GET.

    Never raises: a failed render returns a RenderResult with status error/blocked.
    """
    pw = _render_playwright(url, cookie_header, wait_ms)
    if pw is not None and pw.status == "ok" and (pw.html or pw.text):
        return pw
    http = _render_http(url)
    # Prefer the Playwright error message if HTTP also failed and PW actually ran.
    if http.status != "ok" and pw is not None and pw.error:
        http.error = pw.error or http.error
    if http.status == "ok" and not (http.text or http.html):
        http.status = "blocked"
        http.error = http.error or "页面为空，可能被反爬拦截或需要登录 Cookie。"
    return http


# --------------------------------------------------------------------- jobs
@dataclass
class JobRef:
    """A single job posting discovered from a hiring source link."""

    url: str
    external_id: str = ""
    title: str = ""
    department: str = ""
    city: str = ""
    jd_text: str = ""
    posted_at: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class JobSnapshot:
    """One capture of a posting: JD content + open/closed status."""

    title: str = ""
    department: str = ""
    city: str = ""
    jd_text: str = ""
    posted_at: str = ""
    refreshed_at: str = ""
    applicant_signal: str = ""
    is_open: Optional[bool] = None
    status: str = "ok"          # ok | partial | blocked | error
    error: str = ""
    raw: dict = field(default_factory=dict)

    def fingerprint_fields(self) -> dict:
        """Subset of fields used to detect a JD *content* change."""
        return {
            "title": self.title or "",
            "department": self.department or "",
            "city": self.city or "",
            "jd_len": len(self.jd_text or ""),
            "is_open": self.is_open,
        }


class HiringProvider:
    """Base hiring provider. Subclasses override `expand` and `fetch`."""

    name = "base"

    def expand(self, conn: sqlite3.Connection, link: dict) -> list[JobRef]:
        raise NotImplementedError

    def fetch(self, conn: sqlite3.Connection, posting: dict) -> JobSnapshot:
        raise NotImplementedError


# ------------------------------------------------------------------- people
@dataclass
class ProfileRef:
    profile_url: str
    external_id: str = ""
    name: str = ""
    headline: str = ""
    title: str = ""
    avatar_url: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class ActivityRef:
    text: str = ""
    url: str = ""
    external_id: str = ""
    activity_type: str = "post"
    posted_at: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class ProfileSnapshot:
    name: str = ""
    headline: str = ""
    title: str = ""
    is_active: Optional[bool] = None
    status: str = "ok"          # ok | partial | blocked | error
    error: str = ""
    raw: dict = field(default_factory=dict)

    def fingerprint_fields(self) -> dict:
        return {
            "name": self.name or "",
            "headline": self.headline or "",
            "title": self.title or "",
        }


class PeopleProvider:
    """Base LinkedIn people provider (roster + activity feed)."""

    name = "base_people"

    def expand_profiles(self, conn: sqlite3.Connection, link: dict) -> list[ProfileRef]:
        raise NotImplementedError

    def fetch_profile(self, conn: sqlite3.Connection, profile: dict) -> ProfileSnapshot:
        raise NotImplementedError

    def fetch_activities(self, conn: sqlite3.Connection, profile: dict) -> list[ActivityRef]:
        raise NotImplementedError
