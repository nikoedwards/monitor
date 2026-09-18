"""Same-origin media proxy for third-party social thumbnails."""
from __future__ import annotations

from html import unescape
import re
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from .fetchers import FetchError, fetch_bytes

router = APIRouter(prefix="/api/media", tags=["media"])


def _media_type(url: str, content: bytes) -> str:
    lower = url.lower().split("?", 1)[0]
    if content.startswith(b"\xff\xd8\xff") or lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n") or lower.endswith(".png"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")) or lower.endswith(".gif"):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in {b"avif", b"avis"}:
        return "image/avif"
    if content.lstrip().startswith(b"<svg") or lower.endswith(".svg"):
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


@router.get("/thumbnail")
def thumbnail(url: str = Query(..., min_length=8, max_length=4096)):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid thumbnail URL")
    try:
        content = fetch_bytes(
            url,
            accept="image/avif,image/webp,image/apng,image/svg+xml,image/*,text/html;q=0.8,*/*;q=0.5",
            timeout=12,
            max_bytes=5_000_000,
            headers={
                "Referer": "https://www.instagram.com/",
                "Sec-Fetch-Dest": "image",
            },
        )
    except FetchError as exc:
        raise HTTPException(status_code=502, detail="Thumbnail fetch failed") from exc
    if not content:
        raise HTTPException(status_code=502, detail="Thumbnail response was empty")
    if _media_type(url, content) == "application/octet-stream":
        image_url = _og_image(url, content)
        if image_url:
            try:
                content = fetch_bytes(
                    image_url,
                    accept="image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    timeout=12,
                    max_bytes=5_000_000,
                    headers={"Referer": url, "Sec-Fetch-Dest": "image"},
                )
            except FetchError as exc:
                raise HTTPException(status_code=502, detail="Page thumbnail fetch failed") from exc
        else:
            raise HTTPException(status_code=502, detail="No image found at source URL")
    return Response(
        content=content,
        media_type=_media_type(url, content),
        headers={"Cache-Control": "public, max-age=900, stale-while-revalidate=3600"},
    )
