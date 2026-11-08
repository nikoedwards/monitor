"""Same-origin media proxy for third-party social thumbnails."""
from __future__ import annotations

from html import unescape
import re
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from .fetchers import FetchError, fetch_bytes
from .media_cache import CACHE_DIR, cached_cover, image_type, save_cover

router = APIRouter(prefix="/api/media", tags=["media"])


def _media_type(url: str, content: bytes) -> str:
    # Never trust a .jpg suffix when the upstream actually returned HTML.
    detected = image_type(content)
    if detected:
        return detected
    if content.lstrip().startswith((b"<!DOCTYPE", b"<!doctype", b"<html")):
        return "application/octet-stream"
    if content.lstrip().startswith(b"<svg"):
        return "image/svg+xml"
    return "application/octet-stream"


def _og_image(page_url: str, content: bytes) -> str:
    if b"<" not in content[:512] and b"<meta" not in content[:200_000].lower():
        return ""
    text = content[:1_000_000].decode("utf-8", errors="ignore")
    patterns = (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = unescape(match.group(1)).strip()
            if candidate.startswith(("https://", "http://")):
                return candidate
    return ""


@router.get("/cache/{key}")
def social_cover(key: str):
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise HTTPException(status_code=404, detail="Cover not found")
    path = CACHE_DIR / key
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Cover not found")
    with path.open("rb") as source:
        media_type = image_type(source.read(16))
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


def _instagram_embed_cover(post_url: str) -> bytes:
    parsed = urlparse(post_url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.instagram.com", "instagram.com"}:
        raise FetchError("Invalid Instagram post URL")
    match = re.fullmatch(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)/?", parsed.path)
    if not match:
        raise FetchError("Invalid Instagram post URL")
    page_url = f"https://www.instagram.com/p/{match.group(1)}/embed/captioned/"
    page = fetch_bytes(page_url, timeout=12)
    image_url = _og_image(page_url, page)
    if not image_url:
        # Public post embeds expose the cover as an ordinary image element.
        from html.parser import HTMLParser

        class EmbedImageParser(HTMLParser):
            image_url = ""

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == "img" and "EmbeddedMediaImage" in (attributes.get("class") or ""):
                    self.image_url = attributes.get("src") or ""

        parser = EmbedImageParser()
        parser.feed(page.decode("utf-8", errors="replace"))
        image_url = parser.image_url
    if not image_url.startswith("https://"):
        raise FetchError("Public embed did not expose a cover")
    content = fetch_bytes(image_url, timeout=12, max_bytes=5_000_001)
    if not image_type(content) or len(content) > 5_000_000:
        raise FetchError("Invalid cover response")
    return content


@router.get("/thumbnail")
def thumbnail(
    url: str = Query(..., min_length=8, max_length=4096),
    post_url: str = Query("", max_length=2048),
):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid thumbnail URL")
    if post_url and cached_cover(post_url).is_file():
        return social_cover(cached_cover(post_url).name)
    try:
        content = fetch_bytes(
            url,
            accept="image/avif,image/webp,image/apng,image/svg+xml,image/*,text/html;q=0.8,*/*;q=0.5",
            timeout=12,
            max_bytes=5_000_001,
            headers={
                "Referer": "https://www.instagram.com/",
                "Sec-Fetch-Dest": "image",
            },
        )
    except FetchError as exc:
        if not post_url:
            raise HTTPException(status_code=502, detail="Thumbnail fetch failed") from exc
        try:
            content = _instagram_embed_cover(post_url)
        except FetchError as fallback:
            raise HTTPException(status_code=502, detail="Thumbnail fetch failed") from fallback
    if not content or len(content) > 5_000_000:
        raise HTTPException(status_code=502, detail="Thumbnail response was empty")
    if _media_type(url, content) == "application/octet-stream":
        image_url = _og_image(url, content)
        if image_url:
            try:
                content = fetch_bytes(
                    image_url,
                    accept="image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    timeout=12,
                    max_bytes=5_000_001,
                    headers={"Referer": url, "Sec-Fetch-Dest": "image"},
                )
            except FetchError as exc:
                raise HTTPException(status_code=502, detail="Page thumbnail fetch failed") from exc
        else:
            raise HTTPException(status_code=502, detail="No image found at source URL")
    if _media_type(url, content) == "application/octet-stream" or len(content) > 5_000_000:
        raise HTTPException(status_code=502, detail="Invalid thumbnail response")
    if post_url and image_type(content):
        save_cover(post_url, content)
    return Response(
        content=content,
        media_type=_media_type(url, content),
        headers={"Cache-Control": "public, max-age=900, stale-while-revalidate=3600"},
    )
