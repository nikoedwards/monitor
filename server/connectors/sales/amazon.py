"""Best-effort Amazon scraping provider.

Expansion turns a seller storefront URL (``...?me=<seller-id>``) into the list of
its ASINs; fetch parses a ``/dp/<asin>`` page for title / price / rating / reviews /
BSR / availability. Amazon aggressively blocks plain HTTP clients, so every field is
best-effort and a listing may come back ``partial`` / ``blocked`` rather than failing.
"""
from __future__ import annotations

import html as html_lib
import re
import sqlite3
from urllib.parse import parse_qs, urlparse

from ...fetchers import FetchError, fetch_page
from ...util import amazon_market, clean_text, extract_asin
from .base import ListingRef, ListingSnapshot, SalesProvider

_ASIN_RE = re.compile(r'data-asin="([A-Z0-9]{10})"')
_DP_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")
_RATING_RE = re.compile(r"([0-5](?:\.[0-9])?)\s+out of\s+5", re.I)
_REVIEWS_RE = re.compile(r"([0-9][0-9,]*)\s+(?:global ratings|ratings|reviews)", re.I)
_BSR_RE = re.compile(r"(?:Best Sellers Rank|Best Seller Rank)[\s\S]{0,500}?#\s*([0-9][0-9,]*)", re.I)
_PRODUCT_TITLE_RE = re.compile(r'id=["\']productTitle["\'][^>]*>([\s\S]*?)</', re.I)
_ACR_RATING_RE = re.compile(r'id=["\']acrPopover["\'][^>]*(?:title|aria-label)=["\']([^"\']+)', re.I)
_ACR_REVIEWS_RE = re.compile(r'id=["\']acrCustomerReviewText["\'][^>]*>([\s\S]*?)</', re.I)
_ACR_REVIEWS_LABEL_RE = re.compile(r'id=["\']acrCustomerReviewText["\'][^>]*aria-label=["\']([^"\']+)', re.I)
_PRICE_BLOCK_RE = re.compile(
    r'id=["\'](?:corePrice[^"\']*|apex_desktop|price_inside_buybox|newBuyBoxPrice)["\'][\s\S]{0,2500}',
    re.I,
)
_PRICE_VALUE_RE = re.compile(r'(?:a-offscreen[^>]*>|priceToPay[^>]*>[\s\S]{0,300}?)(?:US)?\$\s*([0-9][0-9,]*\.?[0-9]{0,2})', re.I)
_JSON_PRICE_RE = re.compile(r'"price"\s*:\s*"?([0-9][0-9,]*\.?[0-9]{0,2})"?', re.I)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Cookie": "lc-main=en_US; i18n-prefs=USD",
}

# Main-image extraction from a /dp/ page (several layouts / fallbacks).
_IMG_DYNAMIC_RE = re.compile(r'id="landingImage"[^>]*\bdata-a-dynamic-image="([^"]+)"')
_IMG_HIRES_RE = re.compile(r'"hiRes"\s*:\s*"(https://[^"\\]+?\.jpg)"')
_IMG_LARGE_RE = re.compile(r'"large"\s*:\s*"(https://[^"\\]+?\.jpg)"')
_IMG_URL_IN_JSON_RE = re.compile(r'"(https://[^"\\]+?\.jpg)"')
# Product thumbnail inside a search/storefront result tile.
_GRID_MEDIA_RE = re.compile(r'https://m\.media-amazon\.com/images/I/[A-Za-z0-9._+-]+\.jpg')


def _extract_main_image(html: str, meta: dict) -> str:
    """Best main image from a /dp/ page HTML, falling back to og:image."""
    if html:
        dyn = _IMG_DYNAMIC_RE.search(html)
        if dyn:
            blob = dyn.group(1).replace("&quot;", '"')
            urls = _IMG_URL_IN_JSON_RE.findall(blob)
            if urls:
                return urls[0]
        for rgx in (_IMG_HIRES_RE, _IMG_LARGE_RE):
            found = rgx.search(html)
            if found:
                return found.group(1)
    return meta.get("og:image") or ""


def _grid_images(html: str) -> dict[str, str]:
    """Map ASIN -> real product thumbnail from a search/storefront results page."""
    images: dict[str, str] = {}
    if not html:
        return images
    for m in re.finditer(r'data-asin="([A-Z0-9]{10})"', html):
        asin = m.group(1).upper()
        if asin in images or not asin.startswith("B"):
            continue
        window = html[m.end(): m.end() + 3000]
        found = _GRID_MEDIA_RE.search(window)
        if found:
            images[asin] = found.group(0)
    return images

_MARKET_DOMAIN = {
    "US": "amazon.com", "UK": "amazon.co.uk", "CA": "amazon.ca", "DE": "amazon.de",
    "FR": "amazon.fr", "IT": "amazon.it", "ES": "amazon.es", "AU": "amazon.com.au",
    "JP": "amazon.co.jp",
}


def parse_storefront(url: str) -> dict:
    """Pull seller id + marketplace out of a storefront/search URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    seller = (qs.get("me") or qs.get("seller") or [""])[0]
    marketplace = amazon_market(url) or "US"
    return {"seller_id": seller, "marketplace": marketplace, "domain": parsed.netloc or _MARKET_DOMAIN.get(marketplace, "amazon.com")}


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.replace(",", ""))
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _strip_html(value: str | None) -> str:
    if not value:
        return ""
    return clean_text(html_lib.unescape(re.sub(r"<[^>]+>", " ", value)))


def _extract_price(html: str) -> float | None:
    """Read a product offer price, never an arbitrary dollar amount on the page.

    Amazon pages contain coupons, gift-card promotions and accessory prices.  The
    old page-wide ``$...`` regex commonly turned those into a fake USD 10 price.
    """
    for block in _PRICE_BLOCK_RE.findall(html):
        match = _PRICE_VALUE_RE.search(block)
        if match:
            return _to_float(match.group(1))
    for script in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>', html, re.I):
        match = _JSON_PRICE_RE.search(script)
        if match:
            return _to_float(match.group(1))
    return None


class ScrapeAmazonProvider(SalesProvider):
    name = "amazon_scrape"

    def __init__(self, max_pages: int = 2) -> None:
        self.max_pages = max(1, max_pages)

    # ------------------------------------------------------------------ expand
    def expand(self, conn: sqlite3.Connection, link: dict) -> list[ListingRef]:
        url = link.get("url") or ""
        single = extract_asin(url)
        info = parse_storefront(url)
        domain = info["domain"] or "www.amazon.com"
        if not domain.startswith("www."):
            domain = f"www.{domain}"
        marketplace = info["marketplace"]

        # A single product link configured directly -> one listing.
        # (Image is filled later by fetch() from the /dp/ page.)
        if single and not info["seller_id"]:
            return [ListingRef(url=f"https://{domain}/dp/{single}", asin=single, marketplace=marketplace)]

        seen: set[str] = set()
        refs: list[ListingRef] = []
        for page in range(1, self.max_pages + 1):
            page_url = url if page == 1 else self._with_page(url, page)
            try:
                fetched = fetch_page(page_url, headers=_BROWSER_HEADERS)
            except FetchError:
                break
            html = fetched.get("html") or ""
            images = _grid_images(html)
            asins = _ASIN_RE.findall(html) + _DP_RE.findall(html)
            new_on_page = 0
            for asin in asins:
                asin = asin.upper()
                if asin in seen or not asin.startswith("B"):
                    continue
                seen.add(asin)
                new_on_page += 1
                refs.append(ListingRef(url=f"https://{domain}/dp/{asin}", asin=asin, marketplace=marketplace, image_url=images.get(asin, "")))
            if new_on_page == 0:
                break
        # Fall back to the storefront itself as a single ref if nothing parsed.
        if not refs and single:
            refs.append(ListingRef(url=f"https://{domain}/dp/{single}", asin=single, marketplace=marketplace))
        return refs

    @staticmethod
    def _with_page(url: str, page: int) -> str:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}page={page}"

    # ------------------------------------------------------------------- fetch
    def fetch(self, conn: sqlite3.Connection, listing: dict) -> ListingSnapshot:
        url = listing.get("url") or ""
        snap = ListingSnapshot(currency="USD")
        try:
            page = fetch_page(url, headers=_BROWSER_HEADERS)
        except FetchError as exc:
            snap.status = "error"
            snap.error = str(exc)[:300]
            return snap

        text = page.get("text") or ""
        html = page.get("html") or ""
        meta = page.get("meta") or {}
        product_title = _PRODUCT_TITLE_RE.search(html)
        title = _strip_html(product_title.group(1)) if product_title else clean_text(meta.get("og:title") or page.get("title"))
        if title.lower() in {"amazon.com", "amazon.co.uk", "amazon.ca"}:
            title = ""
        snap.title = title
        snap.sku = listing.get("asin") or extract_asin(url)
        # Real main image from the page; when blocked/empty the runner keeps the
        # image already captured from the storefront grid (COALESCE on save).
        snap.image_url = _extract_main_image(html, meta)

        low = text.lower()
        if "robot check" in low or "enter the characters you see" in low or "to discuss automated access" in low:
            snap.status = "blocked"
            snap.error = "Amazon anti-bot page returned (scrape blocked)."
            return snap

        rating_attr = _ACR_RATING_RE.search(html)
        rating = _RATING_RE.search(rating_attr.group(1) if rating_attr else text)
        if rating:
            snap.rating = _to_float(rating.group(1))
        reviews_label = _ACR_REVIEWS_LABEL_RE.search(html)
        reviews_node = _ACR_REVIEWS_RE.search(html)
        reviews_source = reviews_label.group(1) if reviews_label else (_strip_html(reviews_node.group(1)) if reviews_node else text)
        reviews = _REVIEWS_RE.search(reviews_source)
        if reviews:
            snap.review_count = _to_int(reviews.group(1))
        bsr = _BSR_RE.search(text)
        if bsr:
            snap.bsr = _to_int(bsr.group(1))
            snap.rank = snap.bsr
        snap.price = _extract_price(html)
        if "currently unavailable" in low or "out of stock" in low:
            snap.in_stock = False
        elif "in stock" in low or snap.price is not None:
            snap.in_stock = True

        parsed_any = any(v is not None for v in (snap.price, snap.rating, snap.review_count, snap.bsr))
        if not title and not parsed_any:
            snap.status = "blocked"
            snap.error = "No listing fields parsed (likely blocked or JS-rendered)."
        elif not parsed_any:
            snap.status = "partial"
        snap.raw = {"final_url": page.get("final_url"), "provider": self.name}
        return snap
