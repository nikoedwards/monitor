"""Collect BOSS 直聘 jobs from a persistent, user-authenticated browser.

This is intentionally a small orchestration worker rather than a second hiring
provider.  It reads active ``dimension='hiring'`` links from the local Monitor
SQLite database, opens them with a dedicated Playwright profile, visits the
visible job details, and sends the capture payload to ``/api/hiring/browser-capture``.

The first run should be headed so the operator can log in manually.  Later runs
can use the same profile headlessly.  Login pages, CAPTCHA/security checks, and
empty/partial pages are reported to Monitor as ``page_status='blocked'`` or
``page_status='ok'`` with zero jobs; the worker never guesses that missing jobs
are closed and never attempts to bypass a challenge.

Example (first run)::

    python -m tools.boss_browser_worker --headed --wait-for-login 180

Example (scheduled run)::

    python -m tools.boss_browser_worker --headless --max-jobs 40

The browser profile is deliberately separate from a user's normal Chrome
profile.  This prevents profile-lock conflicts and makes the login session used
by the scheduled worker explicit and revocable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from server.config import ROOT
from server.db import connect


DEFAULT_BASE_URL = os.environ.get("MONITOR_BASE_URL", "http://127.0.0.1:8790")
DEFAULT_PROFILE_DIR = ROOT / "data" / "browser_profiles" / "boss"
DEFAULT_LOGIN_URL = "https://www.zhipin.com/"
BOSS_HOSTS = ("zhipin.com",)
LOGIN_MARKERS = (
    "boss直聘注册登录",
    "boss直聘在线注册登录",
    "登录boss直聘",
    "请先登录",
    "安全验证",
    "访问验证",
    "完成验证",
    "验证码",
)
LOGIN_PATH_MARKERS = ("/web/user/", "/login", "/register", "/security-check", "/safe-check")
CLOSED_MARKERS = ("职位已下线", "停止招聘", "职位不存在", "已结束", "已关闭")
JOB_DETAIL_PATH = "/job_detail/"


class _MonitorUnavailable(RuntimeError):
    """The configured Monitor endpoint could not be reached."""


@dataclass(frozen=True)
class HiringLink:
    id: str
    brand_id: str
    url: str
    label: str = ""
    platform: str = "boss"


@dataclass
class CaptureResult:
    source_url: str
    source_title: str
    page_status: str
    page_error: str
    jobs: list[dict[str, Any]]


def _clean(value: Any, limit: int = 20_000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _canonical_url(value: str, base: str = "") -> str:
    try:
        parsed = urlsplit(urljoin(base, value))
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _is_boss_url(value: str) -> bool:
    try:
        host = (urlsplit(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(host == root or host.endswith(f".{root}") for root in BOSS_HOSTS)


def _looks_like_blocked(url: str, title: str, body: str) -> bool:
    path = (urlsplit(url).path or "").lower()
    haystack = f"{title}\n{body}".lower()
    return any(marker.lower() in haystack for marker in LOGIN_MARKERS) or any(
        marker in path for marker in LOGIN_PATH_MARKERS
    )


def _is_open(body: str) -> bool:
    lower = body.lower()
    return not any(marker.lower() in lower for marker in CLOSED_MARKERS)


def _rows_to_links(rows: Iterable[dict[str, Any]]) -> list[HiringLink]:
    links: list[HiringLink] = []
    for row in rows:
        url = _canonical_url(row.get("url") or "")
        # Treat the host as the source of truth.  Older Monitor rows may have
        # been saved with a blank/legacy platform value even though the URL is
        # a BOSS page; dropping those rows would make the scheduled worker
        # silently skip a valid source.
        if not url or not _is_boss_url(url):
            continue
        links.append(
            HiringLink(
                id=str(row.get("id") or ""),
                brand_id=str(row.get("brand_id") or ""),
                url=url,
                label=str(row.get("label") or ""),
                platform="boss",
            )
        )
    return [link for link in links if link.id and link.brand_id]


def _read_links_from_api(base_url: str, brand_id: str | None, link_id: str | None) -> list[HiringLink]:
    query = urlencode({"dimension": "hiring"})
    endpoint = f"{base_url.rstrip('/')}/api/links?{query}"
    try:
        with urlopen(Request(endpoint, headers={"Accept": "application/json"}), timeout=20) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Monitor API HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise _MonitorUnavailable(f"无法连接 Monitor API {endpoint}: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError(f"无法从 Monitor API 读取招聘链接 {endpoint}: {exc}") from exc
    rows = data.get("links") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("Monitor API 返回的招聘链接数据格式不正确。")
    filtered = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("status") == "active"
        and (not brand_id or row.get("brand_id") == brand_id)
        and (not link_id or row.get("id") == link_id)
    ]
    return _rows_to_links(filtered)


def _read_links_from_db(brand_id: str | None, link_id: str | None) -> list[HiringLink]:
    """Read active BOSS hiring links from the local Monitor database."""
    conn = connect()
    try:
        clauses = [
            "dimension = 'hiring'",
            "status = 'active'",
            "url IS NOT NULL",
            "url != ''",
            "(platform = 'boss' OR lower(url) LIKE '%zhipin.com%')",
        ]
        params: list[str] = []
        if brand_id:
            clauses.append("brand_id = ?")
            params.append(brand_id)
        if link_id:
            clauses.append("id = ?")
            params.append(link_id)
        try:
            rows = conn.execute(
                f"SELECT id, brand_id, url, label, platform FROM links WHERE {' AND '.join(clauses)} ORDER BY created_at",
                params,
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                "Monitor 数据库尚未初始化，请先启动一次 `python -m server.app`。"
            ) from exc
        return _rows_to_links(dict(row) for row in rows)
    finally:
        conn.close()


def _read_links(base_url: str, brand_id: str | None, link_id: str | None) -> list[HiringLink]:
    try:
        return _read_links_from_api(base_url, brand_id, link_id)
    except _MonitorUnavailable:
        host = (urlsplit(base_url).hostname or "").lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise
        return _read_links_from_db(brand_id, link_id)


def _post_capture(base_url: str, capture: CaptureResult, brand_id: str) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/api/hiring/browser-capture"
    payload = {
        "brand_id": brand_id,
        "platform": "boss",
        "source_url": capture.source_url,
        "source_title": capture.source_title,
        "page_status": capture.page_status,
        "page_error": capture.page_error,
        "jobs": capture.jobs,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=45) as response:
            text = response.read().decode("utf-8", errors="replace")
            result = json.loads(text or "{}")
            if not isinstance(result, dict):
                return {"status": "ok", "raw": result}
            return result
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Monitor API HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        # The Codex cron may run while the local SPA/API is closed.  Keep the
        # scheduled worker self-contained by applying the same ingestion
        # routine directly to the local SQLite database when the target is a
        # local Monitor address.  Remote API failures remain hard failures so
        # a misconfigured deployment cannot silently write to another DB.
        host = (urlsplit(base_url).hostname or "").lower()
        if host in {"127.0.0.1", "localhost", "::1"}:
            try:
                from server.connectors.hiring.runner import ingest_browser_hiring_capture

                conn = connect()
                try:
                    brand = conn.execute("SELECT * FROM brands WHERE id = ? LIMIT 1", (brand_id,)).fetchone()
                    if not brand:
                        raise RuntimeError(f"本地 Monitor 中不存在品牌 {brand_id}。")
                    result = ingest_browser_hiring_capture(
                        conn,
                        dict(brand),
                        platform="boss",
                        source_url=capture.source_url,
                        source_title=capture.source_title,
                        page_status=capture.page_status,
                        page_error=capture.page_error,
                        jobs=capture.jobs,
                    )
                    conn.commit()
                    return {**result, "delivery": "direct_db"}
                finally:
                    conn.close()
            except Exception as direct_exc:
                raise RuntimeError(
                    f"无法连接 Monitor API {endpoint}，且本地数据库写入失败：{direct_exc}"
                ) from direct_exc
        detail = getattr(exc, "reason", None) or str(exc)
        raise RuntimeError(f"无法连接 Monitor API {endpoint}: {detail}") from exc


async def _first_text(page: Any, selectors: Iterable[str], root: Any = None) -> str:
    """Return the first non-empty inner text for a small selector list."""
    scope = root or page
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            value = _clean(await locator.inner_text(timeout=1500), 20_000)
            if value:
                return value
        except Exception:
            continue
    return ""


async def _extract_detail(page: Any, url: str, source_title: str = "") -> dict[str, Any]:
    await page.wait_for_timeout(1200)
    title = _clean(await page.title(), 500)
    body = _clean(await page.locator("body").inner_text(timeout=5_000), 50_000)
    final_url = page.url or url
    if _looks_like_blocked(final_url, title, body):
        return {"blocked": True, "title": title, "body": body, "url": final_url}

    job_title = await _first_text(page, ("h1", ".job-name", "[class*='job-name']"))
    city = await _first_text(
        page,
        (".location-address", ".job-address", "[class*='job-address']", "[class*='job-location']"),
    )
    department = await _first_text(page, ("[class*='job-category']", "[class*='department']"))
    jd = await _first_text(
        page,
        (".job-sec-text", ".job-detail", ".job-detail-section", "[class*='job-detail']"),
    )
    # An unknown layout must not turn navigation/footer text into a changed JD.
    # A missing description is preserved as partial by browser-capture.
    return {
        "blocked": False,
        "job": {
            "url": _canonical_url(final_url, url),
            "title": job_title,
            "city": city,
            "department": department,
            "jd_text": jd,
            "is_open": _is_open(body),
            "raw": {"capture_type": "detail", "source_title": source_title},
        },
    }


async def _extract_listing(page: Any, url: str) -> tuple[CaptureResult, list[str]]:
    await page.wait_for_timeout(1500)
    title = _clean(await page.title(), 500)
    body = _clean(await page.locator("body").inner_text(timeout=5_000), 50_000)
    final_url = page.url or url
    if _looks_like_blocked(final_url, title, body):
        return CaptureResult(url, title, "blocked", "当前 BOSS 页面要求登录或安全验证。", []), []

    detail_urls: list[str] = []
    seen: set[str] = set()
    anchors = page.locator(f"a[href*='{JOB_DETAIL_PATH}']")
    try:
        count = await anchors.count()
    except Exception:
        count = 0
    for index in range(count):
        try:
            href = await anchors.nth(index).get_attribute("href")
        except Exception:
            continue
        job_url = _canonical_url(href or "", final_url)
        if job_url and _is_boss_url(job_url) and job_url not in seen:
            seen.add(job_url)
            detail_urls.append(job_url)

    # A direct detail URL is a valid source link and should still be captured.
    if JOB_DETAIL_PATH in (urlsplit(final_url).path or "") and final_url not in seen:
        detail_urls.insert(0, _canonical_url(final_url))

    status = "ok" if detail_urls else "partial"
    error = "" if detail_urls else "当前页面没有发现可识别的 Boss 职位链接。"
    return CaptureResult(url, title, status, error, []), detail_urls


async def _collect_link(
    context: Any,
    link: HiringLink,
    max_jobs: int,
    detail_delay_ms: int,
) -> CaptureResult:
    page = await context.new_page()
    try:
        await page.goto(link.url, wait_until="domcontentloaded", timeout=30_000)
        listing, detail_urls = await _extract_listing(page, link.url)
        if listing.page_status == "blocked":
            return listing
        jobs: list[dict[str, Any]] = []
        detail_blocked = False
        for detail_url in detail_urls[:max_jobs]:
            detail_page = await context.new_page()
            try:
                await detail_page.goto(detail_url, wait_until="domcontentloaded", timeout=30_000)
                result = await _extract_detail(detail_page, detail_url, listing.source_title)
                if result.get("blocked"):
                    # Login expiry while traversing details should be reported
                    # as blocked, preserving jobs already captured this run.
                    if not jobs:
                        return CaptureResult(
                            listing.source_url,
                            listing.source_title,
                            "blocked",
                            "访问职位详情时遇到登录或安全验证。",
                            [],
                        )
                    detail_blocked = True
                    break
                job = result.get("job") or {}
                if job.get("url"):
                    jobs.append(job)
            except Exception:
                # Keep the successful details from this run. A later scheduled
                # run can retry transient detail navigation failures.
                continue
            finally:
                await detail_page.close()
            if detail_delay_ms:
                await page.wait_for_timeout(detail_delay_ms)
        listing.jobs = jobs
        if detail_blocked:
            listing.page_status = "blocked"
            listing.page_error = "访问职位详情时遇到登录或安全验证；已保留本次成功采集的职位。"
            return listing
        if not jobs and listing.page_status == "ok":
            listing.page_status = "partial"
            listing.page_error = "列表页可访问，但详情页没有成功采集职位。"
        return listing
    except Exception as exc:
        title = ""
        try:
            title = _clean(await page.title(), 500)
        except Exception:
            pass
        return CaptureResult(link.url, title, "blocked", f"BOSS 页面访问失败：{str(exc)[:300]}", [])
    finally:
        await page.close()


async def _run(args: argparse.Namespace) -> int:
    try:
        links = _read_links(args.base_url, args.brand_id, args.link_id)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not links and not args.wait_for_login:
        print("没有找到 active 的 BOSS hiring 链接。", file=sys.stderr)
        return 2

    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - depends on optional install
        print("缺少 Playwright，请先安装 `pip install playwright` 并运行 `python -m playwright install chromium`。", file=sys.stderr)
        print(f"详细错误：{exc}", file=sys.stderr)
        return 2

    profile_dir = Path(args.profile_dir).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    had_blocked = False
    async with async_playwright() as playwright:
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(profile_dir),
                headless=not args.headed,
                viewport={"width": 1440, "height": 1100},
                locale="zh-CN",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
        except Exception as exc:
            print(f"无法启动持久化浏览器 profile {profile_dir}: {exc}", file=sys.stderr)
            print("请关闭正在使用该 profile 的浏览器，或为 worker 指定独立的 --profile-dir。", file=sys.stderr)
            return 2

        try:
            if args.wait_for_login:
                login_page = await context.new_page()
                try:
                    login_target = links[0].url if links else DEFAULT_LOGIN_URL
                    await login_page.goto(login_target, wait_until="domcontentloaded", timeout=30_000)
                    print(f"请在打开的 BOSS 浏览器中人工登录。等待 {args.wait_for_login} 秒后开始采集…", file=sys.stderr)
                    await asyncio.sleep(args.wait_for_login)
                finally:
                    await login_page.close()
            if not links:
                print("BOSS 登录 profile 已保存；当前没有 active 招聘链接，本次不采集。", file=sys.stderr)
                return 0
            for link in links:
                capture = await _collect_link(context, link, args.max_jobs, args.detail_delay_ms)
                result = _post_capture(args.base_url, capture, link.brand_id)
                link_blocked = capture.page_status == "blocked"
                print(
                    json.dumps(
                        {
                            "link_id": link.id,
                            "brand_id": link.brand_id,
                            "source_url": capture.source_url,
                            "page_status": capture.page_status,
                            "jobs": len(capture.jobs),
                            "api": result,
                        },
                        ensure_ascii=False,
                    )
                )
                had_blocked = had_blocked or link_blocked
        finally:
            await context.close()
    return 1 if had_blocked else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从持久化登录浏览器采集 BOSS 直聘招聘信息")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Monitor API 地址")
    parser.add_argument("--brand-id", help="只采集指定品牌")
    parser.add_argument("--link-id", help="只采集指定 hiring 链接")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR), help="专用 Playwright 登录 profile 目录")
    parser.add_argument("--headed", action="store_true", help="显示浏览器；首次登录或人工验证时使用")
    parser.add_argument("--headless", action="store_true", help="显式使用无头模式（默认也是无头）")
    parser.add_argument("--wait-for-login", type=int, default=0, metavar="SECONDS", help="先打开 BOSS 页面等待人工登录，再开始采集；需 --headed")
    parser.add_argument("--max-jobs", type=int, default=40, help="每个源最多采集多少个职位详情")
    parser.add_argument("--detail-delay-ms", type=int, default=900, help="职位详情之间的等待毫秒数")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.headed and args.headless:
        print("--headed 与 --headless 不能同时使用。", file=sys.stderr)
        return 2
    if args.wait_for_login and not args.headed:
        print("--wait-for-login 需要与 --headed 一起使用。", file=sys.stderr)
        return 2
    args.max_jobs = max(1, min(args.max_jobs, 200))
    args.detail_delay_ms = max(0, min(args.detail_delay_ms, 30_000))
    args.wait_for_login = max(0, min(args.wait_for_login, 1800))
    started = time.monotonic()
    try:
        code = asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    print(f"BOSS worker finished in {time.monotonic() - started:.1f}s", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
