"""LinkedIn hiring + people providers — best-effort, cookie-driven scraping.

LinkedIn is extremely aggressive about anti-scraping and scraping personal
profile activity violates its Terms of Service and raises legal/privacy risk.
These providers therefore require a user-supplied session cookie, degrade
gracefully to a ``blocked`` result, and never raise. Employee-activity
monitoring in particular should be treated as opportunistic, not guaranteed.
"""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import urljoin

from ...util import canonical_url, clean_text, normalize_url
from .base import (
    ActivityRef,
    HiringProvider,
    JobRef,
    JobSnapshot,
    PeopleProvider,
    ProfileRef,
    render,
)

_JOB_VIEW_RE = re.compile(r"/jobs/view/[0-9]+", re.I)
_JOB_ID_RE = re.compile(r"/jobs/view/([0-9]+)", re.I)
_PROFILE_RE = re.compile(r"/in/[^\"'<>\s?/]+", re.I)
_CLOSED_MARKERS = ("no longer accepting", "not accepting applications", "已关闭", "不再接受")


def _linkedin_cookie(conn: sqlite3.Connection) -> str:
    from . import hiring_cookie

    return hiring_cookie(conn, "linkedin_cookie")


class LinkedInJobsProvider(HiringProvider):
    name = "linkedin_jobs"

    def __init__(self, cookie: str = "", max_jobs: int = 40) -> None:
        self.cookie = cookie or ""
        self.max_jobs = max(1, max_jobs)

    def expand(self, conn: sqlite3.Connection, link: dict) -> list[JobRef]:
        url = link.get("url") or ""
        try:
            start = normalize_url(url)
        except ValueError:
            return []
        page = render(start, self.cookie)
        if page.status != "ok" or not (page.html or page.anchors):
            return [JobRef(url=start, external_id=_job_id(start))]

        seen: set[str] = set()
        found: list[JobRef] = []
        candidates = list(page.anchors or []) + _JOB_VIEW_RE.findall(page.html or "")
        for href in candidates:
            if not href or "/jobs/view/" not in href:
                continue
            absolute = urljoin(page.final_url or start, href.split("?")[0])
            canon = canonical_url(absolute)
            if not canon or canon in seen:
                continue
            seen.add(canon)
            found.append(JobRef(url=absolute, external_id=_job_id(absolute)))
            if len(found) >= self.max_jobs:
                break
        if not found:
            return [JobRef(url=start, external_id=_job_id(start))]
        return found

    def fetch(self, conn: sqlite3.Connection, posting: dict) -> JobSnapshot:
        url = posting.get("url") or ""
        snap = JobSnapshot()
        page = render(url, self.cookie)
        if page.status != "ok":
            snap.status = "blocked" if not page.html else "partial"
            snap.error = page.error or "无法读取 LinkedIn 职位页（需登录 Cookie 或被反爬拦截）。"
            return snap
        text = page.text or ""
        snap.title = clean_text(page.meta.get("og:title") or page.title)
        snap.jd_text = _extract_jd(text)
        lowered = text.lower()
        snap.is_open = not any(m in lowered for m in _CLOSED_MARKERS)
        if not snap.jd_text and not snap.title:
            snap.status = "blocked"
            snap.error = "页面无有效内容，可能被反爬拦截。"
        elif not snap.jd_text:
            snap.status = "partial"
        snap.raw = {"final_url": page.final_url, "method": page.method, "provider": self.name}
        return snap


class LinkedInPeopleProvider(PeopleProvider):
    name = "linkedin_people"

    def __init__(self, cookie: str = "", max_profiles: int = 40) -> None:
        self.cookie = cookie or ""
        self.max_profiles = max(1, max_profiles)

    def expand_profiles(self, conn: sqlite3.Connection, link: dict) -> list[ProfileRef]:
        url = link.get("url") or ""
        try:
            start = normalize_url(url)
        except ValueError:
            return []
        page = render(start, self.cookie)
        if page.status != "ok" or not (page.html or page.anchors):
            return []
        seen: set[str] = set()
        found: list[ProfileRef] = []
        candidates = list(page.anchors or []) + _PROFILE_RE.findall(page.html or "")
        for href in candidates:
            if not href or "/in/" not in href:
                continue
            absolute = urljoin(page.final_url or start, href.split("?")[0])
            canon = canonical_url(absolute)
            if not canon or "/in/" not in canon or canon in seen:
                continue
            seen.add(canon)
            found.append(ProfileRef(profile_url=absolute, external_id=_profile_slug(absolute)))
            if len(found) >= self.max_profiles:
                break
        return found

    def fetch_activities(self, conn: sqlite3.Connection, profile: dict) -> list[ActivityRef]:
        base = (profile.get("profile_url") or "").rstrip("/")
        if not base:
            return []
        activity_url = f"{base}/recent-activity/all/"
        page = render(activity_url, self.cookie)
        if page.status != "ok" or not page.text:
            return []
        activities: list[ActivityRef] = []
        for block in _activity_blocks(page.text):
            activities.append(ActivityRef(text=block, url=activity_url, activity_type="post"))
            if len(activities) >= 20:
                break
        return activities


def _job_id(url: str) -> str:
    match = _JOB_ID_RE.search(url or "")
    return match.group(1) if match else ""


def _profile_slug(url: str) -> str:
    match = re.search(r"/in/([^/]+)", url or "")
    return match.group(1) if match else ""


def _extract_jd(text: str) -> str:
    if not text:
        return ""
    lines = [clean_text(l) for l in text.splitlines() if clean_text(l)]
    joined = "\n".join(lines)
    for anchor in ("About the job", "Job description", "Responsibilities", "关于此职位", "职位描述"):
        idx = joined.find(anchor)
        if idx != -1:
            return joined[idx : idx + 4000]
    return joined[:4000]


def _activity_blocks(text: str) -> list[str]:
    """Very rough: keep medium-length text lines as candidate activity items."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        normalized = clean_text(line)
        key = normalized.lower()
        if 24 <= len(normalized) <= 400 and key not in seen:
            seen.add(key)
            out.append(normalized)
    return out
