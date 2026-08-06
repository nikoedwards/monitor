"""Boss 直聘 (zhipin.com) hiring provider — best-effort, cookie-driven scraping.

Boss 直聘 requires a logged-in session and enforces captchas / behavioural risk
control, so this provider renders pages with a user-supplied cookie (configured
in settings). When a page cannot be read it returns a ``blocked`` snapshot
instead of raising, so the runner keeps going and surfaces the status.
"""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import urljoin

from ...util import canonical_url, clean_text, normalize_url
from .base import HiringProvider, JobRef, JobSnapshot, render

_JOB_DETAIL_RE = re.compile(r"/job_detail/[^\"'<>\s]+", re.I)
_JOB_ID_RE = re.compile(r"/job_detail/([^/.?\"'<>\s]+)", re.I)
# Text markers that indicate a posting is no longer accepting applicants.
_CLOSED_MARKERS = ("职位已下线", "停止招聘", "该职位已", "职位不存在", "已结束", "已关闭")
_LOGIN_MARKERS = (
    "boss直聘注册登录",
    "boss直聘在线注册登录",
    "登录boss直聘",
    "请先登录",
    "安全验证",
    "访问验证",
    "完成验证",
    "验证码",
)
_LOGIN_PATH_MARKERS = (
    "/web/user/",
    "/login",
    "/register",
    "/security-check",
    "/safe-check",
)


def _boss_cookie(conn: sqlite3.Connection) -> str:
    from . import hiring_cookie

    return hiring_cookie(conn, "boss_cookie")


class BossProvider(HiringProvider):
    name = "boss_scrape"

    def __init__(self, cookie: str = "", max_jobs: int = 40) -> None:
        self.cookie = cookie or ""
        self.max_jobs = max(1, max_jobs)

    # ------------------------------------------------------------------ expand
    def expand(self, conn: sqlite3.Connection, link: dict) -> list[JobRef]:
        url = link.get("url") or ""
        try:
            start = normalize_url(url)
        except ValueError:
            return []
        page = render(start, self.cookie)
        blocked_reason = _blocked_reason(page)
        if blocked_reason:
            raise RuntimeError(blocked_reason)
        if page.status != "ok" or not (page.html or page.anchors):
            # A configured direct job URL is still useful as a registry entry
            # when the detail page is temporarily blocked. Listing/company URLs
            # must not become fake postings themselves.
            job_id = _job_id(start)
            if job_id:
                return [JobRef(url=start, external_id=job_id)]
            raise RuntimeError(page.error or "无法读取 Boss 招聘源，请检查登录 Cookie、验证码或源 URL。")

        seen: set[str] = set()
        found: list[JobRef] = []
        candidates = list(page.anchors or [])
        # Also scan raw HTML for job_detail links Playwright may have missed.
        candidates += _JOB_DETAIL_RE.findall(page.html or "")
        for href in candidates:
            if not href or "/job_detail/" not in href:
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
            job_id = _job_id(start)
            return [JobRef(url=start, external_id=job_id)] if job_id else []
        return found

    # ------------------------------------------------------------------- fetch
    def fetch(self, conn: sqlite3.Connection, posting: dict) -> JobSnapshot:
        url = posting.get("url") or ""
        snap = JobSnapshot()
        if not _job_id(url):
            snap.status = "blocked"
            snap.error = "该记录不是 Boss 职位详情页；请删除它并配置公司招聘页或 /job_detail/ 职位链接。"
            snap.raw = {"provider": self.name, "invalid_posting_url": True}
            return snap
        page = render(url, self.cookie)
        blocked_reason = _blocked_reason(page)
        if blocked_reason:
            snap.status = "blocked"
            snap.error = blocked_reason
            snap.raw = {"final_url": page.final_url, "method": page.method, "provider": self.name}
            return snap
        if page.status != "ok":
            snap.status = "blocked" if not page.html else "partial"
            snap.error = page.error or "无法读取 Boss 职位页（可能需要登录 Cookie 或被风控拦截）。"
            return snap

        text = page.text or ""
        title = clean_text(page.meta.get("og:title") or page.title)
        # Boss titles often look like "岗位名-城市-公司名".
        parts = [p.strip() for p in re.split(r"[-|｜·]", title) if p.strip()]
        snap.title = parts[0] if parts else title
        if len(parts) >= 2:
            snap.city = parts[1]

        snap.jd_text = _extract_jd(text)
        snap.is_open = not any(marker in text for marker in _CLOSED_MARKERS)
        if not snap.jd_text and not snap.title:
            snap.status = "blocked"
            snap.error = "页面无有效内容，可能被反爬拦截。"
        elif not snap.jd_text:
            snap.status = "partial"
        snap.raw = {"final_url": page.final_url, "method": page.method, "provider": self.name}
        return snap


def _job_id(url: str) -> str:
    match = _JOB_ID_RE.search(url or "")
    return match.group(1) if match else ""


def _blocked_reason(page) -> str:
    title_and_text = f"{page.title or ''}\n{page.text or ''}".lower()
    final_url = (page.final_url or "").lower()
    if any(marker in title_and_text for marker in _LOGIN_MARKERS) or any(
        marker in final_url for marker in _LOGIN_PATH_MARKERS
    ):
        return "Boss 页面跳转到了登录/注册或安全验证页，请更新登录 Cookie 后重试。"
    return ""


def _extract_jd(text: str) -> str:
    """Heuristically slice the job-description body out of the page text."""
    if not text:
        return ""
    lines = [clean_text(l) for l in text.splitlines() if clean_text(l)]
    joined = "\n".join(lines)
    # Prefer the block after a "职位描述"/"岗位职责" heading when present.
    for anchor in ("职位描述", "岗位职责", "工作职责", "岗位描述"):
        idx = joined.find(anchor)
        if idx != -1:
            return joined[idx : idx + 4000]
    return joined[:4000]
