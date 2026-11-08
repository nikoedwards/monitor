"""Persist social covers before signed CDN URLs expire."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .config import DATA_DIR
from .fetchers import FetchError, fetch_bytes

CACHE_DIR = DATA_DIR / "social-covers"
MAX_IMAGE_BYTES = 5_000_000


def image_type(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in {b"avif", b"avis"}:
        return "image/avif"
    return ""


def cover_key(post_url: str) -> str:
    return hashlib.sha256(post_url.encode("utf-8")).hexdigest()


def cached_cover(post_url: str) -> Path:
    return CACHE_DIR / cover_key(post_url)


def save_cover(post_url: str, content: bytes) -> str:
    if not image_type(content) or len(content) > MAX_IMAGE_BYTES:
        raise FetchError("Invalid social cover image")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=CACHE_DIR, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        os.replace(temporary, cached_cover(post_url))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return f"/api/media/cache/{cover_key(post_url)}"


def cache_social_cover(post_url: str, image_url: str) -> str:
    target = cached_cover(post_url)
    if target.is_file():
        return f"/api/media/cache/{cover_key(post_url)}"
    content = fetch_bytes(
        image_url, timeout=8, max_bytes=MAX_IMAGE_BYTES + 1,
        headers={"Referer": "https://www.instagram.com/", "Sec-Fetch-Dest": "image"},
    )
    return save_cover(post_url, content)
