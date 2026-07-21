"""Web page snapshot engine: visual capture, offline archive, and change analysis.

Capture strategy (first that works wins):
  1. Playwright Chromium: full-page PNG plus a self-contained HTML archive.
  2. Installed Edge/Chrome: PNG plus a limited HTML fallback.
  3. SVG text fallback so a snapshot always has a visual artifact.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import mimetypes
import os
import re
import shutil
import subprocess
from difflib import SequenceMatcher
from html import escape as html_escape
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from .config import ROOT, SNAPSHOT_DIR
from .fetchers import _is_blocked_host
from .util import clean_text

_PLAYWRIGHT_AVAILABLE: bool | None = None
logger = logging.getLogger(__name__)

ARCHIVE_TOTAL_LIMIT = 30_000_000
ARCHIVE_RESOURCE_LIMIT = 6_000_000
ARCHIVE_RESOURCE_COUNT_LIMIT = 160
ARCHIVE_REPLAY_VERSION = 5
ARCHIVE_IMAGE_OPTIMIZE_THRESHOLD = 96_000
ARCHIVE_IMAGE_MAX_DIMENSION = 1600
ARCHIVE_IMAGE_WEBP_QUALITY = 78
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
_ARCHIVE_GUARD_MARKER = f'data-monitor-archive-guard="{ARCHIVE_REPLAY_VERSION}"'
_ARCHIVE_GUARD_TAG_RE = re.compile(
    r'<script\b[^>]*data-monitor-archive-guard="\d+"[^>]*>.*?</script>',
    re.I | re.S,
)
_ARCHIVE_SCRIPT_TAG_RE = re.compile(r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.I | re.S)
_ARCHIVE_DATA_IMAGE_RE = re.compile(
    r"data:(?P<mime>image/(?:png|jpe?g|webp|avif));base64,(?P<payload>[A-Za-z0-9+/=]+)",
    re.I,
)
_ARCHIVE_REPLAY_GUARD = r"""
(() => {
  if (window.__monitorArchiveReplayGuardV5) return;
  window.__monitorArchiveReplayGuardV5 = true;

  const blocked = () => Promise.reject(new Error('Archived page: network access disabled'));
  try { window.fetch = blocked; } catch (_) {}
  try { XMLHttpRequest.prototype.open = function () { throw new Error('Archived page: network access disabled'); }; } catch (_) {}

  const cookieSelector = [
    '#onetrust-banner-sdk',
    '#onetrust-consent-sdk',
    '#shopify-pc__banner',
    '[id*="shopify-pc__banner" i]',
    '[class*="shopify-pc__banner" i]',
    '[id*="cookie" i]',
    '[class*="cookie" i]',
    '[data-testid*="cookie" i]',
    '[aria-label*="cookie" i]',
    '[id*="consent-banner" i]',
    '[class*="consent-banner" i]',
    '[data-testid*="consent-banner" i]',
    '[aria-label*="consent banner" i]'
  ].join(',');
  const dialogSelector = [
    '[role="dialog"]',
    '[role="alertdialog"]',
    '[aria-modal="true"]',
    'dialog',
    '[data-testid="POPUP"]',
    '[data-testid*="popup"]',
    '[data-testid*="modal"]',
    '[class*="popup"]',
    '[class*="modal"]',
    '[id*="popup"]',
    '[id*="modal"]',
    cookieSelector
  ].join(',');
  const semanticDialogSelector = '[role="dialog"],[role="alertdialog"],[aria-modal="true"],dialog';
  const controlSelector = 'button,[role="button"],a,input[type="button"],input[type="reset"],input[type="submit"]';
  const dismissedDialogKeys = new Set();
  const dismissedDialogSelectors = new Set();
  const dismissalStyle = document.createElement('style');
  dismissalStyle.setAttribute('data-monitor-archive-dismissals', '');
  (document.head || document.documentElement).appendChild(dismissalStyle);
  const scrollUnlockStyle = document.createElement('style');
  scrollUnlockStyle.setAttribute('data-monitor-archive-scroll-unlock', '');
  scrollUnlockStyle.textContent = `
    html[data-monitor-archive-scroll-unlocked] {
      overflow-y: auto !important;
      position: static !important;
      top: auto !important;
      height: auto !important;
      max-height: none !important;
      width: auto !important;
      padding-right: 0 !important;
    }
    body[data-monitor-archive-scroll-unlocked] {
      overflow: visible !important;
      overflow-y: visible !important;
      position: static !important;
      top: auto !important;
      right: auto !important;
      bottom: auto !important;
      left: auto !important;
      height: auto !important;
      max-height: none !important;
      width: auto !important;
      padding-right: 0 !important;
      margin-right: 0 !important;
      touch-action: auto !important;
      overscroll-behavior: auto !important;
    }
  `;
  (document.head || document.documentElement).appendChild(scrollUnlockStyle);

  const controlLabel = (control) => [
    control.getAttribute('aria-label'),
    control.getAttribute('title'),
    control.getAttribute('data-testid'),
    control.id,
    typeof control.className === 'string' ? control.className : '',
    control.value,
    control.textContent
  ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();

  const isCloseControl = (control) => {
    const label = controlLabel(control);
    return /(?:^|[\s_-])(close|dismiss)(?:$|[\s_-])|关闭|關閉|取消|klaviyo-close|modal-close|popup-close/i.test(label)
      || /^[x×✕✖]$/i.test(label);
  };

  const isCookieDismissControl = (control) => {
    const label = controlLabel(control);
    return /\b(?:accept|reject|decline|agree|allow|continue|save|confirm|okay|ok|close|dismiss)\b|\bgot\s+it\b|接受|同意|允许|允許|拒绝|拒絕|不同意|仅必要|僅必要|只允许必要|只允許必要|保存(?:设置|設置|偏好)?|继续|繼續|知道了|我知道了|关闭|關閉/i.test(label);
  };

  const overlayFor = (dialog) => {
    let node = dialog;
    for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
      try {
        if (getComputedStyle(node).position === 'fixed') return node;
      } catch (_) {}
    }
    return dialog;
  };

  const dialogKey = (dialog) => {
    for (const attribute of ['aria-label', 'data-testid', 'id']) {
      const value = String(dialog.getAttribute(attribute) || '').trim();
      if (value) return `${attribute}:${value}`;
    }
    const text = String(dialog.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 160);
    return text ? `text:${text}` : '';
  };

  const escapedAttribute = (value) => String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const dialogCssSelector = (dialog) => {
    const ariaLabel = String(dialog.getAttribute('aria-label') || '').trim();
    if (ariaLabel) {
      const value = escapedAttribute(ariaLabel);
      return `[aria-label="${value}"]`;
    }
    const testId = String(dialog.getAttribute('data-testid') || '').trim();
    if (testId) return `[data-testid="${escapedAttribute(testId)}"]`;
    const id = String(dialog.id || '').trim();
    if (id) return `[id="${escapedAttribute(id)}"]`;
    return '';
  };

  const refreshDismissalStyle = () => {
    dismissalStyle.textContent = dismissedDialogSelectors.size
      ? `${Array.from(dismissedDialogSelectors).join(',')}{display:none!important;visibility:hidden!important;pointer-events:none!important;}`
      : '';
  };

  const hideOverlay = (overlay) => {
    if (!overlay || !overlay.isConnected) return;
    if (overlay.style.getPropertyValue('display') !== 'none' || overlay.style.getPropertyPriority('display') !== 'important') {
      overlay.style.setProperty('display', 'none', 'important');
    }
    if (overlay.style.getPropertyValue('visibility') !== 'hidden' || overlay.style.getPropertyPriority('visibility') !== 'important') {
      overlay.style.setProperty('visibility', 'hidden', 'important');
    }
    if (overlay.style.getPropertyValue('pointer-events') !== 'none' || overlay.style.getPropertyPriority('pointer-events') !== 'important') {
      overlay.style.setProperty('pointer-events', 'none', 'important');
    }
    if (overlay.getAttribute('aria-hidden') !== 'true') overlay.setAttribute('aria-hidden', 'true');
    if (overlay.getAttribute('data-monitor-archive-dismissed') !== 'true') overlay.setAttribute('data-monitor-archive-dismissed', 'true');
  };

  const restoreScroll = () => {
    const commonOverrides = {
      position: 'static',
      top: 'auto',
      right: 'auto',
      bottom: 'auto',
      left: 'auto',
      height: 'auto',
      'max-height': 'none',
      width: 'auto',
      'padding-right': '0px',
      'touch-action': 'auto',
      'overscroll-behavior': 'auto'
    };
    for (const element of [document.documentElement, document.body]) {
      if (!element) continue;
      if (element.getAttribute('data-monitor-archive-scroll-unlocked') !== 'true') {
        element.setAttribute('data-monitor-archive-scroll-unlocked', 'true');
      }
      const overrides = element === document.documentElement
        ? { ...commonOverrides, 'overflow-x': 'hidden', 'overflow-y': 'auto' }
        : { ...commonOverrides, 'overflow-x': 'visible', 'overflow-y': 'visible' };
      for (const property of [
        'overflow-x', 'overflow-y', 'position', 'top', 'right', 'bottom', 'left',
        'height', 'max-height', 'width', 'max-width', 'padding-right', 'margin-right',
        'touch-action', 'overscroll-behavior'
      ]) {
        const safeValue = overrides[property];
        if (safeValue !== undefined) {
          if (element.style.getPropertyValue(property) !== safeValue || element.style.getPropertyPriority(property) !== 'important') {
            element.style.setProperty(property, safeValue, 'important');
          }
        } else if (element.style.getPropertyValue(property)) {
          element.style.removeProperty(property);
        }
      }
      element.removeAttribute('data-scroll-locked');
      element.removeAttribute('data-kl-scroll-locking-modal');
      element.removeAttribute('data-lenis-prevent');
    }
  };

  const enforceDismissedDialogs = () => {
    restoreScroll();
    if (dismissedDialogKeys.size) {
      for (const dialog of document.querySelectorAll(dialogSelector)) {
        if (dismissedDialogKeys.has(dialogKey(dialog))) hideOverlay(overlayFor(dialog));
      }
    }
  };

  const dismiss = (dialog, key = dialogKey(dialog)) => {
    if (key) dismissedDialogKeys.add(key);
    const cssSelector = dialog ? dialogCssSelector(dialog) : '';
    if (cssSelector) {
      dismissedDialogSelectors.add(cssSelector);
      refreshDismissalStyle();
    }
    if (!dialog || !dialog.isConnected) {
      enforceDismissedDialogs();
      return;
    }
    const overlay = overlayFor(dialog);
    try {
      if (overlay.tagName === 'DIALOG' && typeof overlay.close === 'function') overlay.close();
    } catch (_) {}
    hideOverlay(overlay);
    restoreScroll();
  };

  document.addEventListener('submit', (event) => event.preventDefault(), true);
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const link = target.closest('a[data-archive-href]');
    if (link) event.preventDefault();

    const control = target.closest(controlSelector);
    if (!control) return;
    const closeControl = isCloseControl(control);
    const candidateCookie = control.closest(cookieSelector);
    const cookie = candidateCookie === document.body || candidateCookie === document.documentElement
      ? null
      : candidateCookie;
    const dialog = cookie && (closeControl || isCookieDismissControl(control))
      ? cookie
      : closeControl
        ? control.closest(semanticDialogSelector) || control.closest(dialogSelector)
        : null;
    if (!dialog) return;
    const key = dialogKey(dialog);
    setTimeout(() => {
      dismiss(dialog, key);
    }, 0);
  }, true);
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const dialogs = Array.from(document.querySelectorAll(dialogSelector)).filter((dialog) => {
      try { return getComputedStyle(dialog).display !== 'none'; } catch (_) { return true; }
    });
    const dialog = dialogs[dialogs.length - 1];
    if (dialog) setTimeout(() => dismiss(dialog), 0);
  }, true);
  new MutationObserver(enforceDismissedDialogs).observe(document.documentElement, {
    subtree: true,
    childList: true
  });
  const observeScrollRoot = (element) => {
    if (!element || element.hasAttribute('data-monitor-archive-scroll-observed')) return;
    element.setAttribute('data-monitor-archive-scroll-observed', 'true');
    new MutationObserver(restoreScroll).observe(element, {
      attributes: true,
      attributeFilter: ['style', 'data-scroll-locked', 'data-kl-scroll-locking-modal', 'data-lenis-prevent']
    });
  };
  const initializeScrollRestore = () => {
    restoreScroll();
    observeScrollRoot(document.documentElement);
    observeScrollRoot(document.body);
  };
  initializeScrollRestore();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeScrollRestore, { once: true });
  }
  window.addEventListener('load', initializeScrollRestore, { once: true });
  for (const delay of [0, 100, 500, 1500]) setTimeout(initializeScrollRestore, delay);
})();
""".strip()


def _archive_guard_tag() -> str:
    return f'<script {_ARCHIVE_GUARD_MARKER}>{_ARCHIVE_REPLAY_GUARD}</script>'


def _optimize_archive_image(mime: str, body: bytes) -> tuple[str, bytes]:
    """Shrink raster assets for replay; the lossless PNG screenshot remains the visual source of truth."""
    if len(body) < ARCHIVE_IMAGE_OPTIMIZE_THRESHOLD:
        return mime, body
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(body)) as source:
            if bool(getattr(source, "is_animated", False)):
                return mime, body
            image = ImageOps.exif_transpose(source)
            image.thumbnail(
                (ARCHIVE_IMAGE_MAX_DIMENSION, ARCHIVE_IMAGE_MAX_DIMENSION),
                Image.Resampling.LANCZOS,
            )
            has_alpha = "A" in image.getbands()
            image = image.convert("RGBA" if has_alpha else "RGB")
            output = io.BytesIO()
            image.save(
                output,
                format="WEBP",
                quality=ARCHIVE_IMAGE_WEBP_QUALITY,
                method=4,
            )
            optimized = output.getvalue()
            if len(optimized) >= len(body) * 0.92:
                return mime, body
            return "image/webp", optimized
    except Exception:
        return mime, body


def _optimize_archive_data_images(content: str) -> str:
    """Re-encode data-URI images in archives already stored on the persistent volume."""
    cache: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        value = match.group(0)
        cached = cache.get(value)
        if cached is not None:
            return cached
        try:
            body = base64.b64decode(match.group("payload"), validate=True)
        except Exception:
            cache[value] = value
            return value
        mime, optimized = _optimize_archive_image(match.group("mime").lower(), body)
        replacement = _data_uri(mime, optimized)
        cache[value] = replacement
        return replacement

    return _ARCHIVE_DATA_IMAGE_RE.sub(replace, content)


def _move_archive_scripts_to_body(content: str) -> str:
    """Let archived markup and styles paint before original site scripts initialize."""
    scripts: list[str] = []

    def collect(match: re.Match) -> str:
        attrs = match.group("attrs")
        if "data-monitor-archive-guard" in attrs.lower():
            return match.group(0)
        if re.search(r"\bsrc\s*=", attrs, re.I) and not re.search(r"\b(?:async|defer)\b", attrs, re.I):
            attrs = f"{attrs} defer"
        scripts.append(f"<script{attrs}>{match.group('body')}</script>")
        return ""

    updated = _ARCHIVE_SCRIPT_TAG_RE.sub(collect, content)
    if not scripts:
        return updated
    closing_body = re.search(r"</body\s*>", updated, re.I)
    block = "".join(scripts)
    if closing_body:
        return updated[:closing_body.start()] + block + updated[closing_body.start():]
    return updated + block


def upgrade_snapshot_archives(root: Path = SNAPSHOT_DIR) -> int:
    """Upgrade stored archives once: fast raster assets, non-blocking scripts, current guard."""
    root.mkdir(parents=True, exist_ok=True)
    marker = _ARCHIVE_GUARD_MARKER.encode("utf-8")
    tag = _archive_guard_tag()
    upgraded = 0
    for path in root.glob("*.html"):
        temporary = path.with_name(f".{path.name}.archive-upgrade.tmp")
        try:
            with path.open("rb") as handle:
                if marker in handle.read(262_144):
                    continue
            content = path.read_text(encoding="utf-8")
            content = _ARCHIVE_GUARD_TAG_RE.sub("", content)
            content = _optimize_archive_data_images(content)
            content = _move_archive_scripts_to_body(content)
            head = re.search(r"<head(?:\s[^>]*)?>", content, re.I)
            if head:
                content = content[:head.end()] + tag + content[head.end():]
            else:
                content = tag + content
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, path)
            upgraded += 1
        except Exception:
            temporary.unlink(missing_ok=True)
            logger.exception("Failed to upgrade archived snapshot %s", path)
    return upgraded


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _prepare_page_for_full_capture(page) -> dict:
    """Scroll through the document so lazy media is loaded before capture."""
    media = page.evaluate(
        """
        async () => {
          const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
          const copyAttr = (element, source, target) => {
            const value = element.getAttribute(source);
            const current = element.getAttribute(target);
            if (value && (!current || current.startsWith('data:image/'))) element.setAttribute(target, value);
          };
          const isNearViewport = (element) => {
            const rect = element.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && rect.bottom >= -window.innerHeight * 0.25 && rect.top <= window.innerHeight * 1.25;
          };
          const promoteVisibleMedia = () => {
            for (const image of Array.from(document.querySelectorAll('img')).filter(isNearViewport)) {
              image.loading = 'eager';
              copyAttr(image, 'data-src', 'src');
              copyAttr(image, 'data-lazy-src', 'src');
              copyAttr(image, 'data-original', 'src');
              copyAttr(image, 'data-srcset', 'srcset');
              copyAttr(image, 'data-lazy-srcset', 'srcset');
            }
            for (const source of Array.from(document.querySelectorAll('picture source, video source')).filter((source) => isNearViewport(source.parentElement || source))) {
              copyAttr(source, 'data-src', 'src');
              copyAttr(source, 'data-srcset', 'srcset');
            }
            for (const frame of Array.from(document.querySelectorAll('iframe')).filter(isNearViewport)) {
              frame.loading = 'eager';
              copyAttr(frame, 'data-src', 'src');
            }
            for (const video of Array.from(document.querySelectorAll('video')).filter(isNearViewport)) {
              video.preload = 'auto';
              video.muted = true;
              copyAttr(video, 'data-src', 'src');
              copyAttr(video, 'data-poster', 'poster');
              try { video.load(); } catch (_) {}
              try {
                const play = video.play();
                if (play) play.catch(() => {});
              } catch (_) {}
            }
            for (const element of Array.from(document.querySelectorAll('[data-bg], [data-background], [data-background-image]')).filter(isNearViewport)) {
              const value = element.getAttribute('data-bg') || element.getAttribute('data-background') || element.getAttribute('data-background-image');
              if (value && !element.style.backgroundImage) {
                element.style.backgroundImage = value.startsWith('url(') ? value : `url("${value.replaceAll('"', '\\"')}")`;
              }
            }
          };
          const waitForVisibleImages = async () => {
            const pending = Array.from(document.images).filter((image) => isNearViewport(image) && !image.complete);
            if (!pending.length) {
              await sleep(350);
              return;
            }
            const settled = Promise.all(pending.map((image) => new Promise((resolve) => {
              image.addEventListener('load', resolve, { once: true });
              image.addEventListener('error', resolve, { once: true });
            })));
            await Promise.race([settled, sleep(1800)]);
            await sleep(250);
          };

          promoteVisibleMedia();
          await waitForVisibleImages();
          let stableBottomPasses = 0;
          let previousHeight = 0;
          const deadline = Date.now() + 45000;
          for (let step = 0; step < 120 && Date.now() < deadline; step += 1) {
            const root = document.documentElement;
            const height = Math.max(root.scrollHeight, document.body?.scrollHeight || 0);
            const distance = Math.max(window.innerHeight * 0.7, 560);
            const nextY = Math.min(window.scrollY + distance, Math.max(0, height - window.innerHeight));
            window.scrollTo(0, nextY);
            promoteVisibleMedia();
            await waitForVisibleImages();

            const newHeight = Math.max(root.scrollHeight, document.body?.scrollHeight || 0);
            const atBottom = window.scrollY + window.innerHeight >= newHeight - 4;
            if (atBottom) {
              await sleep(1000);
              promoteVisibleMedia();
              await waitForVisibleImages();
              const settledHeight = Math.max(root.scrollHeight, document.body?.scrollHeight || 0);
              stableBottomPasses = settledHeight <= Math.max(previousHeight, newHeight) + 4 ? stableBottomPasses + 1 : 0;
              previousHeight = settledHeight;
              if (stableBottomPasses >= 2) break;
            } else {
              stableBottomPasses = 0;
              previousHeight = newHeight;
            }
          }

          for (const video of document.querySelectorAll('video')) {
            try { video.pause(); } catch (_) {}
          }

          window.scrollTo(0, 0);
          await sleep(700);
          const renderedImages = Array.from(document.images).filter((image) => {
            const rect = image.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          });
          return {
            images: document.images.length,
            loaded_images: Array.from(document.images).filter((image) => image.complete && image.naturalWidth > 0).length,
            rendered_images: renderedImages.length,
            loaded_rendered_images: renderedImages.filter((image) => image.complete && image.naturalWidth > 0).length,
            videos: document.querySelectorAll('video').length,
            iframes: document.querySelectorAll('iframe').length,
            height: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0),
          };
        }
        """
    )
    return media


def _resource_url_allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and not _is_blocked_host(parsed.hostname or "")


def _mime_type(url: str, headers: dict | None = None) -> str:
    value = ((headers or {}).get("content-type") or "").split(";", 1)[0].strip().lower()
    if value:
        return value
    return mimetypes.guess_type(urlparse(url).path)[0] or "application/octet-stream"


def _data_uri(mime: str, body: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(body).decode('ascii')}"


def _capture_loaded_resource(cache: dict[str, tuple[str, bytes]], response) -> None:
    """Keep already-loaded page assets so archive creation rarely refetches them."""
    try:
        if response.status < 200 or response.status >= 400:
            return
        if response.request.resource_type not in {"stylesheet", "script", "image", "font"}:
            return
        url = urldefrag(response.url)[0]
        if not _resource_url_allowed(url) or url in cache:
            return
        length = int(response.headers.get("content-length") or 0)
        if length > ARCHIVE_RESOURCE_LIMIT:
            return
        body = response.body()
        if not body or len(body) > ARCHIVE_RESOURCE_LIMIT:
            return
        cache[url] = (_mime_type(url, response.headers), body)
    except Exception:
        return


def _archive_manifest(page) -> list[dict]:
    return page.evaluate(
        r"""
        () => {
          const result = [];
          const add = (url, kind) => {
            if (!url || !/^https?:/i.test(url)) return;
            if (!result.some((item) => item.url === url)) result.push({ url, kind });
          };
          for (const image of document.images) add(image.currentSrc || image.src, 'image');
          for (const input of document.querySelectorAll('input[type="image"]')) add(input.src, 'image');
          for (const link of document.querySelectorAll('link[rel~="stylesheet"]')) add(link.href, 'stylesheet');
          for (const script of document.querySelectorAll('script[src]')) add(script.src, 'script');
          for (const video of document.querySelectorAll('video[poster]')) add(video.poster, 'image');
          for (const image of document.querySelectorAll('svg image')) add(image.href?.baseVal || image.getAttribute('href'), 'image');
          const cssUrls = (value, base) => {
            for (const match of String(value || '').matchAll(/url\(\s*['"]?([^'"\)]+)['"]?\s*\)/gi)) {
              try { add(new URL(match[1], base).href, 'style-asset'); } catch (_) {}
            }
          };
          for (const style of document.querySelectorAll('style')) cssUrls(style.textContent, document.baseURI);
          for (const element of document.querySelectorAll('[style]')) cssUrls(element.getAttribute('style'), document.baseURI);
          return result.slice(0, 160);
        }
        """
    )


def _build_archive_resource_map(context, manifest: list[dict], loaded: dict[str, tuple[str, bytes]]) -> tuple[dict[str, str], dict]:
    resources: dict[str, str] = {}
    failures: list[str] = []
    loading: set[str] = set()
    total_bytes = 0

    def load(url: str, kind: str = "", depth: int = 0) -> str:
        nonlocal total_bytes
        clean_url = urldefrag(url)[0]
        if not clean_url or clean_url in resources:
            return resources.get(clean_url, "")
        if clean_url in loading or depth > 3 or not _resource_url_allowed(clean_url):
            return ""
        if len(resources) >= ARCHIVE_RESOURCE_COUNT_LIMIT or total_bytes >= ARCHIVE_TOTAL_LIMIT:
            return ""
        loading.add(clean_url)
        mime = ""
        body = b""
        try:
            if clean_url in loaded:
                mime, body = loaded[clean_url]
            else:
                response = context.request.get(clean_url, timeout=12000, fail_on_status_code=False)
                try:
                    if 200 <= response.status < 400:
                        length = int(response.headers.get("content-length") or 0)
                        if length <= ARCHIVE_RESOURCE_LIMIT:
                            body = response.body()
                            mime = _mime_type(clean_url, response.headers)
                finally:
                    response.dispose()
            if not body or len(body) > ARCHIVE_RESOURCE_LIMIT:
                failures.append(clean_url)
                return ""
            if mime == "text/css" or kind == "stylesheet":
                css = body.decode("utf-8", errors="replace")

                def replace_css(match: re.Match) -> str:
                    raw = match.group(2).strip()
                    if not raw or raw.startswith(("data:", "blob:", "#")):
                        return match.group(0)
                    nested = load(urljoin(clean_url, raw), "style-asset", depth + 1)
                    return f'url("{nested}")' if nested else "url(\"\")"

                body = _CSS_URL_RE.sub(replace_css, css).encode("utf-8")
                mime = "text/css"
            elif mime.startswith("image/"):
                mime, body = _optimize_archive_image(mime, body)
            if total_bytes + len(body) > ARCHIVE_TOTAL_LIMIT:
                failures.append(clean_url)
                return ""
            total_bytes += len(body)
            value = _data_uri(mime or _mime_type(clean_url), body)
            resources[clean_url] = value
            resources[url] = value
            return value
        except Exception:
            failures.append(clean_url)
            return ""
        finally:
            loading.discard(clean_url)

    for item in manifest[:ARCHIVE_RESOURCE_COUNT_LIMIT]:
        load(str(item.get("url") or ""), str(item.get("kind") or ""))
    return resources, {
        "resource_count": len({urldefrag(key)[0] for key in resources}),
        "resource_bytes": total_bytes,
        "resource_failures": len(set(failures)),
        "self_contained": True,
    }


def _serialize_archive(page, resources: dict[str, str], original_url: str) -> str:
    return page.evaluate(
        r"""
        ({ resources, originalUrl, guardScript, guardVersion }) => {
          const clone = document.documentElement.cloneNode(true);
          const head = clone.querySelector('head') || clone.insertBefore(document.createElement('head'), clone.firstChild);
          for (const base of clone.querySelectorAll('base')) base.remove();
          for (const refresh of clone.querySelectorAll('meta[http-equiv="refresh" i]')) refresh.remove();

          const csp = document.createElement('meta');
          csp.setAttribute('http-equiv', 'Content-Security-Policy');
          csp.setAttribute('content', "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline' data:; font-src data:; script-src 'unsafe-inline' data: blob:; media-src data: blob:; connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'");
          head.prepend(csp);

          const absolute = (value, base = document.baseURI) => {
            try { return new URL(value, base).href; } catch (_) { return ''; }
          };
          const mapped = (value, base = document.baseURI) => resources[absolute(value, base)] || resources[value] || '';
          const rewriteCss = (css, base = document.baseURI) => String(css || '').replace(/url\(\s*(['"]?)(.*?)\1\s*\)/gi, (_all, _quote, raw) => {
            if (!raw || /^(data:|blob:|#)/i.test(raw)) return `url("${raw}")`;
            return `url("${mapped(raw, base)}")`;
          });

          const liveImages = Array.from(document.querySelectorAll('img'));
          const clonedImages = Array.from(clone.querySelectorAll('img'));
          clonedImages.forEach((copy, index) => {
            const live = liveImages[index];
            const source = live?.currentSrc || live?.src || copy.getAttribute('src') || '';
            copy.setAttribute('src', mapped(source));
            copy.removeAttribute('srcset');
            copy.setAttribute('loading', 'eager');
          });
          const liveInputs = Array.from(document.querySelectorAll('input[type="image"]'));
          Array.from(clone.querySelectorAll('input[type="image"]')).forEach((copy, index) => copy.setAttribute('src', mapped(liveInputs[index]?.src || copy.getAttribute('src') || '')));
          const liveLinks = Array.from(document.querySelectorAll('link[rel~="stylesheet"]'));
          Array.from(clone.querySelectorAll('link[rel~="stylesheet"]')).forEach((copy, index) => {
            const value = mapped(liveLinks[index]?.href || copy.getAttribute('href') || '');
            if (value) copy.setAttribute('href', value); else copy.remove();
            copy.removeAttribute('integrity');
            copy.removeAttribute('crossorigin');
          });
          const liveScripts = Array.from(document.querySelectorAll('script[src]'));
          Array.from(clone.querySelectorAll('script[src]')).forEach((copy, index) => {
            const value = mapped(liveScripts[index]?.src || copy.getAttribute('src') || '');
            if (value) copy.setAttribute('src', value); else copy.remove();
            copy.removeAttribute('integrity');
            copy.removeAttribute('crossorigin');
            copy.removeAttribute('nonce');
          });
          Array.from(clone.querySelectorAll('script:not([src])')).forEach((script) => script.removeAttribute('nonce'));
          const replayBody = clone.querySelector('body') || clone.appendChild(document.createElement('body'));
          for (const script of Array.from(clone.querySelectorAll('script'))) {
            if (script.src && !script.hasAttribute('async') && !script.hasAttribute('defer')) script.setAttribute('defer', '');
            replayBody.appendChild(script);
          }
          Array.from(clone.querySelectorAll('style')).forEach((style) => { style.textContent = rewriteCss(style.textContent); });
          Array.from(clone.querySelectorAll('[style]')).forEach((element) => element.setAttribute('style', rewriteCss(element.getAttribute('style'))));

          const liveVideos = Array.from(document.querySelectorAll('video'));
          Array.from(clone.querySelectorAll('video')).forEach((copy, index) => {
            const live = liveVideos[index];
            copy.setAttribute('poster', mapped(live?.poster || copy.getAttribute('poster') || ''));
            copy.removeAttribute('src');
            copy.removeAttribute('autoplay');
            copy.setAttribute('controls', '');
            for (const source of copy.querySelectorAll('source')) source.remove();
          });
          for (const frame of Array.from(clone.querySelectorAll('iframe'))) {
            const placeholder = document.createElement('div');
            placeholder.textContent = '嵌入内容未离线保存';
            placeholder.setAttribute('style', 'display:flex;align-items:center;justify-content:center;min-height:120px;padding:16px;background:#f3f4f6;color:#6b7280;border:1px dashed #d1d5db;font:14px system-ui,sans-serif;');
            frame.replaceWith(placeholder);
          }
          for (const object of clone.querySelectorAll('object, embed')) object.remove();
          for (const source of clone.querySelectorAll('picture source')) source.remove();

          for (const anchor of clone.querySelectorAll('a[href]')) {
            const href = absolute(anchor.getAttribute('href') || '', originalUrl);
            if (href.includes('#') && href.split('#')[0] === originalUrl.split('#')[0]) {
              anchor.setAttribute('href', `#${href.split('#').slice(1).join('#')}`);
            } else {
              anchor.setAttribute('data-archive-href', href);
              anchor.setAttribute('href', '#');
            }
          }
          for (const form of clone.querySelectorAll('form')) form.setAttribute('action', '');

          const guard = document.createElement('script');
          guard.setAttribute('data-monitor-archive-guard', String(guardVersion));
          guard.textContent = guardScript;
          head.insertBefore(guard, csp.nextSibling);
          clone.setAttribute('data-monitor-archive-url', originalUrl);
          clone.setAttribute('data-monitor-archive-created-at', new Date().toISOString());
          return '<!doctype html>\n' + clone.outerHTML;
        }
        """,
        {
            "resources": resources,
            "originalUrl": original_url,
            "guardScript": _ARCHIVE_REPLAY_GUARD,
            "guardVersion": ARCHIVE_REPLAY_VERSION,
        },
    )


def _write_fallback_archive(filename: str, url: str, title: str, html: str, text: str, error: str = "") -> str:
    """Write a limited offline HTML artifact when Playwright archiving is unavailable."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / filename
    excerpt = "\n".join(clean_text(line) for line in (text or "").splitlines() if clean_text(line))[:30000]
    original = (html or "")[:3_000_000]
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'">
<title>{html_escape(title or url)}</title><style>body{{font:15px/1.6 system-ui,sans-serif;max-width:1000px;margin:32px auto;padding:0 24px;color:#111}}.note{{padding:12px;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px}}pre{{white-space:pre-wrap}}details{{margin-top:24px}}</style></head><body>
<h1>{html_escape(title or url)}</h1><p>{html_escape(url)}</p><div class="note">该快照使用受限归档回退生成，部分样式和交互不可用。{html_escape(error)}</div>
<pre>{html_escape(excerpt)}</pre><details><summary>原始 HTML 源码</summary><pre>{html_escape(original)}</pre></details></body></html>"""
    path.write_text(page, encoding="utf-8")
    return filename


def _playwright_capture(url: str, png_path: Path, html_path: Path) -> dict | None:
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
            browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
            context = browser.new_context(viewport={"width": 1440, "height": 1200})
            page = context.new_page()
            loaded: dict[str, tuple[str, bytes]] = {}
            page.on("response", lambda response: _capture_loaded_resource(loaded, response))
            navigation_error = ""
            archive_error = ""
            archive_meta: dict = {"self_contained": False}
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except PlaywrightTimeoutError as exc:
                navigation_error = str(exc)[:300]
            try:
                page.wait_for_function(
                    "() => document.body && document.body.innerText.length > 100 && document.documentElement.scrollHeight > window.innerHeight",
                    timeout=15000,
                )
            except PlaywrightTimeoutError:
                page.wait_for_timeout(2000)
            media = _prepare_page_for_full_capture(page)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(1000)
            page.screenshot(path=str(png_path), full_page=True, animations="disabled", caret="hide")
            try:
                manifest = _archive_manifest(page)
                resources, archive_meta = _build_archive_resource_map(context, manifest, loaded)
                html_path.write_text(_serialize_archive(page, resources, page.url or url), encoding="utf-8")
                archive_meta["archive_size"] = html_path.stat().st_size
            except Exception as exc:
                archive_error = str(exc)[:300]
            browser.close()
        _PLAYWRIGHT_AVAILABLE = True
        if png_path.exists() and png_path.stat().st_size > 0:
            result = {"method": "playwright", "media": media, "archive": archive_meta}
            if navigation_error:
                result["navigation_warning"] = navigation_error
            if archive_error:
                result["archive_error"] = archive_error
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
    for candidate in candidates:
        if candidate and candidate not in seen and Path(candidate).exists():
            seen.add(candidate)
            available.append(candidate)
    return available


def _subprocess_capture(url: str, png_path: Path) -> dict | None:
    errors = []
    for browser in _browser_candidates():
        for flag in ("--headless=new", "--headless"):
            command = [
                browser, flag, "--disable-gpu", "--hide-scrollbars", "--disable-dev-shm-usage", "--no-sandbox",
                "--no-first-run", "--no-default-browser-check", "--window-size=1440,1200", f"--screenshot={png_path}", url,
            ]
            try:
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=45)
                if result.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0:
                    return {"method": "headless_browser", "browser": browser}
                errors.append((result.stderr or result.stdout or f"exit {result.returncode}").strip()[:200])
            except Exception as exc:
                errors.append(str(exc)[:200])
    if errors:
        return {"error": "; ".join(error for error in errors if error)[:500]}
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
  <style>.bg {{ fill: #0a0a0a; }}.panel {{ fill: #111; stroke: #2a2a2a; stroke-width: 2; }}.muted {{ fill: #888; font: 24px ui-sans-serif,system-ui,sans-serif; }}.title {{ fill: #fafafa; font: 700 48px ui-sans-serif,system-ui,sans-serif; }}.body {{ fill: #e5e5e5; font: 26px ui-sans-serif,system-ui,sans-serif; }}.url {{ fill: #3b82f6; font: 22px ui-monospace,monospace; }}</style>
  <rect class="bg" width="1440" height="1200"/><rect class="panel" x="48" y="48" width="1344" height="1104" rx="16"/>
  <text class="muted" x="72" y="104">Generated visual snapshot fallback</text><text class="title" x="72" y="166">{html_escape((title or "Untitled page")[:80])}</text><text class="url" x="72" y="204">{html_escape(url[:120])}</text>{''.join(rows)}</svg>"""
    path.write_text(svg, encoding="utf-8")
    return filename


def capture_artifacts(url: str, title: str, text: str, html: str, base_name: str) -> tuple[str, str, dict]:
    """Return ``(screenshot filename, archive filename, metadata)``."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    png_filename = f"{base_name}.png"
    html_filename = f"{base_name}.html"
    png_path = SNAPSHOT_DIR / png_filename
    html_path = SNAPSHOT_DIR / html_filename

    pw_result = _playwright_capture(url, png_path, html_path)
    if pw_result and "error" not in pw_result:
        if not html_path.exists() or html_path.stat().st_size == 0:
            _write_fallback_archive(html_filename, url, title, html, text, pw_result.get("archive_error", ""))
            pw_result.setdefault("archive", {}).update({"self_contained": False, "archive_size": html_path.stat().st_size})
        return png_filename, html_filename, pw_result

    sub_result = _subprocess_capture(url, png_path)
    if sub_result and "error" not in sub_result:
        _write_fallback_archive(html_filename, url, title, html, text, "Playwright archive unavailable")
        sub_result["archive"] = {"self_contained": False, "archive_size": html_path.stat().st_size}
        return png_filename, html_filename, sub_result

    error = (pw_result or {}).get("error", "") or (sub_result or {}).get("error", "") or "No headless browser available"
    logger.warning("Falling back to SVG snapshot for %s: %s", url, error)
    svg_filename = f"{base_name}.svg"
    _write_svg(svg_filename, url, title, text, error)
    _write_fallback_archive(html_filename, url, title, html, text, error)
    return svg_filename, html_filename, {
        "method": "svg_fallback",
        "error": error,
        "archive": {"self_contained": False, "archive_size": html_path.stat().st_size},
    }


def capture_image(url: str, title: str, text: str, base_name: str) -> tuple[str, dict]:
    """Backward-compatible screenshot-only wrapper."""
    screenshot, _archive, meta = capture_artifacts(url, title, text, "", base_name)
    return screenshot, meta


def compare_visuals(current_filename: str, previous_filename: str | None) -> dict:
    """Compare two screenshots with a noise-tolerant, deterministic visual diff."""
    if not previous_filename:
        return {"available": False, "score": 0.0, "ratio": 0.0, "regions": []}
    try:
        from PIL import Image, ImageChops, ImageFilter

        current_path = Path(current_filename)
        previous_path = Path(previous_filename)
        if not current_path.is_absolute():
            current_path = SNAPSHOT_DIR / current_path
        if not previous_path.is_absolute():
            previous_path = SNAPSHOT_DIR / previous_path
        with Image.open(current_path) as current_raw, Image.open(previous_path) as previous_raw:
            current = current_raw.convert("RGB")
            previous = previous_raw.convert("RGB")
            source_size = {"width": current.width, "height": current.height}
            target_width = 560

            def normalized(image):
                height = max(1, min(10000, round(image.height * target_width / max(image.width, 1))))
                return image.resize((target_width, height))

            current_small = normalized(current).filter(ImageFilter.GaussianBlur(radius=0.8))
            previous_small = normalized(previous).filter(ImageFilter.GaussianBlur(radius=0.8))
            canvas_height = max(current_small.height, previous_small.height)
            current_canvas = Image.new("RGB", (target_width, canvas_height), "white")
            previous_canvas = Image.new("RGB", (target_width, canvas_height), "white")
            current_canvas.paste(current_small, (0, 0))
            previous_canvas.paste(previous_small, (0, 0))
            gray = ImageChops.difference(current_canvas, previous_canvas).convert("L")
            mask = gray.point(lambda value: 255 if value >= 24 else 0)
            histogram = mask.histogram()
            changed_pixels = histogram[255]
            total_pixels = target_width * canvas_height
            ratio = changed_pixels / max(total_pixels, 1)
            height_delta = abs(current_small.height - previous_small.height) / max(canvas_height, 1)
            score = min(1.0, max(ratio, height_delta * 0.35))
            if score < 0.004:
                score = 0.0

            regions = []
            tile = 140
            for top in range(0, canvas_height, tile):
                for left in range(0, target_width, tile):
                    right, bottom = min(target_width, left + tile), min(canvas_height, top + tile)
                    region_histogram = mask.crop((left, top, right, bottom)).histogram()
                    region_ratio = region_histogram[255] / max((right - left) * (bottom - top), 1)
                    if region_ratio >= 0.035:
                        regions.append({
                            "x": round(left / target_width, 4),
                            "y": round(top / canvas_height, 4),
                            "width": round((right - left) / target_width, 4),
                            "height": round((bottom - top) / canvas_height, 4),
                            "change_ratio": round(region_ratio, 4),
                        })
            regions.sort(key=lambda item: item["change_ratio"], reverse=True)
            return {
                "available": True,
                "score": round(score, 4),
                "ratio": round(ratio, 4),
                "regions": regions[:8],
                "source_size": source_size,
                "previous_size": {"width": previous.width, "height": previous.height},
            }
    except Exception as exc:
        return {"available": False, "score": 0.0, "ratio": 0.0, "regions": [], "error": str(exc)[:200]}


def visual_comparison_image(previous_filename: str, current_filename: str, regions: list[dict] | None = None) -> dict | None:
    """Create a compact before/after JPEG for a multimodal model."""
    try:
        from PIL import Image, ImageDraw

        def open_image(filename: str):
            path = Path(filename)
            if not path.is_absolute():
                path = SNAPSHOT_DIR / path
            return Image.open(path).convert("RGB")

        previous = open_image(previous_filename)
        current = open_image(current_filename)
        region = (regions or [{}])[0] if regions else {}

        def crop(image):
            if region:
                x = float(region.get("x") or 0)
                y = float(region.get("y") or 0)
                width = float(region.get("width") or 1)
                height = float(region.get("height") or 1)
                padding_x, padding_y = width * 0.35, height * 0.35
                left = max(0.0, x - padding_x)
                top = max(0.0, y - padding_y)
                right = min(1.0, x + width + padding_x)
                bottom = min(1.0, y + height + padding_y)
                image = image.crop((round(left * image.width), round(top * image.height), round(right * image.width), round(bottom * image.height)))
            image.thumbnail((700, 900))
            return image

        previous = crop(previous)
        current = crop(current)
        panel_width = max(previous.width, current.width)
        panel_height = max(previous.height, current.height)
        sheet = Image.new("RGB", (panel_width * 2 + 24, panel_height + 48), "white")
        sheet.paste(previous, ((panel_width - previous.width) // 2, 42))
        sheet.paste(current, (panel_width + 24 + (panel_width - current.width) // 2, 42))
        draw = ImageDraw.Draw(sheet)
        draw.text((8, 12), "BEFORE", fill="black")
        draw.text((panel_width + 32, 12), "AFTER", fill="black")
        buffer = io.BytesIO()
        sheet.save(buffer, format="JPEG", quality=82, optimize=True)
        return {"media_type": "image/jpeg", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}
    except Exception:
        return None


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
        return 0.0, "已保存基线快照，后续快照将与其比较。", []

    previous_text = previous.get("text_excerpt") or ""
    previous_title = previous.get("title") or ""
    changes: list[dict] = []
    if previous_title and current_title and previous_title != current_title:
        changes.append({"type": "title", "from": previous_title, "to": current_title})

    old_lines = _meaningful_lines(previous_text)
    new_lines = _meaningful_lines(current_text)
    old_set = {line.lower() for line in old_lines}
    new_set = {line.lower() for line in new_lines}
    added = [line for line in new_lines if line.lower() not in old_set][:6]
    removed = [line for line in old_lines if line.lower() not in new_set][:6]
    changes.extend({"type": "added", "text": line} for line in added)
    changes.extend({"type": "removed", "text": line} for line in removed)

    if previous.get("text_hash") and previous["text_hash"] == current.get("text_hash"):
        return 0.0, "与上一张快照相比，可见文本没有变化。", changes

    old_sample = previous_text[:20_000]
    new_sample = current_text[:20_000]
    if not old_sample and not new_sample:
        score = 0.0
    elif not old_sample or not new_sample:
        score = 1.0
    else:
        score = max(0.0, min(1.0, 1 - SequenceMatcher(None, old_sample, new_sample).ratio()))

    if score < 0.02 and not changes:
        summary = "未检测到有意义的可见文本变化。"
    elif score < 0.15:
        summary = f"页面有轻微更新：新增 {len(added)} 项，移除 {len(removed)} 项。"
    else:
        summary = f"页面有明显更新：新增 {len(added)} 项，移除 {len(removed)} 项。"
    if any(change["type"] == "title" for change in changes):
        summary += " 页面标题已变化。"
    return round(score, 4), summary, changes
