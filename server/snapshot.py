"""Web page snapshot engine: screenshot capture + visible-text change analysis.

Capture strategy (first that works wins):
  1. Playwright Chromium (full-page) if installed.
  2. Installed Edge/Chrome via headless --screenshot.
  3. SVG text fallback so a snapshot always exists.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from difflib import SequenceMatcher
from html import escape as html_escape
from pathlib import Path

from .config import ROOT, SNAPSHOT_DIR
from .util import clean_text

_PLAYWRIGHT_AVAILABLE: bool | None = None
logger = logging.getLogger(__name__)


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _playwright_capture(url: str, png_path: Path) -> dict | None:
    global _PLAYWRIGHT_AVAILABLE
    if _PLAYWRIGHT_AVAILABLE is False:
        return None
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception:
        _PLAYWRIGHT_AVAILABLE = False
        return None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1200})
            navigation_error = ""
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except PlaywrightTimeoutError as exc:
                # Dynamic sites may never finish loading, but their rendered DOM
                # is still usable. Keep the page and capture what is visible.
                navigation_error = str(exc)[:300]
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(1500)
            page.screenshot(path=str(png_path), full_page=True)
            browser.close()
        _PLAYWRIGHT_AVAILABLE = True
        if png_path.exists() and png_path.stat().st_size > 0:
            result = {"method": "playwright"}
            if navigation_error:
                result["navigation_warning"] = navigation_error
            return result
    except Exception as exc:
        return {"error": str(exc)[:300]}
    return None


def _browser_candidates() -> list[str]:
    candidates: list[str] = []
    env_browser = os.environ.get("SNAPSHOT_BROWSER", "").strip()
    if env_browser:
        candidates.append(env_browser)
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        candidates += [
            str(Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            str(Path(pf86) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            str(Path(local) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            str(Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ]
    for name in ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    seen, available = set(), []
    for c in candidates:
        if c and c not in seen and Path(c).exists():
            seen.add(c)
            available.append(c)
    return available


def _subprocess_capture(url: str, png_path: Path) -> dict | None:
    errors = []
    for browser in _browser_candidates():
        for flag in ("--headless=new", "--headless"):
            command = [
                browser, flag, "--disable-gpu", "--hide-scrollbars",
                "--disable-dev-shm-usage", "--no-sandbox",
                "--no-first-run", "--no-default-browser-check",
                "--window-size=1440,1200", f"--screenshot={png_path}", url,
            ]
            try:
                result = subprocess.run(
                    command, cwd=ROOT, capture_output=True, text=True, timeout=45
                )
                if result.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0:
                    return {"method": "headless_browser", "browser": browser}
                errors.append((result.stderr or result.stdout or f"exit {result.returncode}").strip()[:200])
            except Exception as exc:
                errors.append(str(exc)[:200])
    if errors:
        return {"error": "; ".join(e for e in errors if e)[:500]}
    return None


def _write_svg(filename: str, url: str, title: str, text: str, error: str = "") -> str:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / filename
    lines = [clean_text(line) for line in (text or "").splitlines() if clean_text(line)][:18]
    if not lines:
        lines = ["No readable page text was captured."]
    if error:
        lines.insert(0, f"Capture note: {error[:180]}")
    rows, y = [], 214
    for line in lines:
        rows.append(f'<text x="72" y="{y}" class="body">{html_escape(line[:150])}</text>')
        y += 42
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="1200" viewBox="0 0 1440 1200">
  <style>
    .bg {{ fill: #0a0a0a; }}
    .panel {{ fill: #111111; stroke: #2a2a2a; stroke-width: 2; }}
    .muted {{ fill: #888888; font: 24px ui-sans-serif, system-ui, sans-serif; }}
    .title {{ fill: #fafafa; font: 700 48px ui-sans-serif, system-ui, sans-serif; }}
    .body {{ fill: #e5e5e5; font: 26px ui-sans-serif, system-ui, sans-serif; }}
    .url {{ fill: #3b82f6; font: 22px ui-monospace, monospace; }}
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


def capture_image(url: str, title: str, text: str, base_name: str) -> tuple[str, dict]:
    """Return (filename, meta). Always produces a file."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    png_filename = f"{base_name}.png"
    png_path = SNAPSHOT_DIR / png_filename

    pw_result = _playwright_capture(url, png_path)
    if pw_result and "error" not in pw_result:
        return png_filename, pw_result

    sub_result = _subprocess_capture(url, png_path)
    if sub_result and "error" not in sub_result:
        return png_filename, sub_result

    error = (pw_result or {}).get("error", "") or (sub_result or {}).get("error", "") or "No headless browser available"
    logger.warning("Falling back to SVG snapshot for %s: %s", url, error)
    svg_filename = f"{base_name}.svg"
    _write_svg(svg_filename, url, title, text, error)
    return svg_filename, {"method": "svg_fallback", "error": error}


def _meaningful_lines(text: str) -> list[str]:
    result, seen = [], set()
    for line in (text or "").splitlines():
        normalized = clean_text(line)
        if 18 <= len(normalized) <= 260 and normalized.lower() not in seen:
            seen.add(normalized.lower())
            result.append(normalized)
    return result


def analyze_change(current: dict, previous: dict | None) -> tuple[float, str, list[dict]]:
    current_text = current.get("text", "")
    current_title = current.get("title", "")
    if not previous:
        return 0.0, "Baseline snapshot saved. Future captures compare against this version.", []

    previous_text = previous.get("text_excerpt") or ""
    previous_title = previous.get("title") or ""
    changes: list[dict] = []
    if previous_title and current_title and previous_title != current_title:
        changes.append({"type": "title", "from": previous_title, "to": current_title})

    old_lines = _meaningful_lines(previous_text)
    new_lines = _meaningful_lines(current_text)
    old_set = {l.lower() for l in old_lines}
    new_set = {l.lower() for l in new_lines}
    added = [l for l in new_lines if l.lower() not in old_set][:6]
    removed = [l for l in old_lines if l.lower() not in new_set][:6]
    changes.extend({"type": "added", "text": l} for l in added)
    changes.extend({"type": "removed", "text": l} for l in removed)

    if previous.get("text_hash") and previous["text_hash"] == current.get("text_hash"):
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
        summary = f"Minor page updates: {len(added)} additions, {len(removed)} removals."
    else:
        summary = f"Notable page changes: {len(added)} additions, {len(removed)} removals."
    if any(c["type"] == "title" for c in changes):
        summary += " Page title changed."
    return round(score, 4), summary, changes
