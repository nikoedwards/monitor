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
from .estimates import estimate_amazon_sales

_ASIN_RE = re.compile(r'data-asin="([A-Z0-9]{10})"')
_DP_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")
_RATING_RE = re.compile(r"([0-5](?:\.[0-9])?)\s+out of\s+5", re.I)
_REVIEWS_RE = re.compile(r"([0-9][0-9,]*)\s+(?:global ratings|ratings|reviews)", re.I)
_BSR_RE = re.compile(r"(?:Best Sellers Rank|Best Seller Rank)[\s\S]{0,500}?#\s*([0-9][0-9,]*)", re.I)
_BSR_MARKER_RE = re.compile(
    r"(?:best\s+sellers?\s+rank|sales\s*rank|salesrank|"
    r"best[-_ ]seller[-_ ]rank|classement\s+des\s+meilleures\s+ventes|"
    r"rang\s+des\s+ventes|売れ筋ランキング|ランキング)",
    re.I,
)
_BSR_LEVEL_RE = re.compile(
    r"#\s*([0-9][0-9,]*)\s+in\s+([^#(\n\r]+?)(?=\s*\(|\s+#|\s*$)",
    re.I,
)
_BSR_TOKEN_RE = re.compile(r"#\s*([0-9][0-9.,\u00a0\u202f ]*)")
_BSR_CONTAINER_RE = re.compile(
    r"<[^>]+(?:id|class)=[\"'][^\"']*(?:salesrank|sales.rank|"
    r"detailbullets|best[-_ ]seller|best[-_ ]rank)[^\"']*[\"'][^>]*>",
    re.I,
)
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


def _is_amazon_interstitial(page: dict | None) -> bool:
    """Identify Amazon's short continue-shopping/robot gate response.

    Amazon sometimes returns a tiny ``Amazon.com / Continue shopping`` page to
    one request fingerprint while returning the real product page to another.
    Treat that response as retryable instead of persisting an empty snapshot.
    A short page with product markers is kept as valid, since some mobile
    layouts intentionally contain very little visible text.
    """
    if not isinstance(page, dict):
        return True
    text = clean_text(str(page.get("text") or ""))
    html_text = _strip_html(str(page.get("html") or ""))
    if not text and not html_text:
        return True
    content = max((text, html_text), key=len)
    lower = f"{text} {html_text}".lower()
    gate_markers = (
        "continue shopping",
        "click the button below",
        "robot check",
        "enter the characters you see",
        "automated access",
    )
    if not any(marker in lower for marker in gate_markers):
        return False
    # Continue-shopping interstitials are tiny; a long page containing that
    # phrase may be a real product page with a normal footer. Robot/automated
    # access gates remain retryable at any length.
    robot_gate = any(marker in lower for marker in gate_markers[2:])
    if not robot_gate and len(content) > 1800:
        return False
    product_markers = (
        "best sellers rank",
        "about this item",
        "add to cart",
        "buying options",
        "product information",
    )
    return not any(marker in lower for marker in product_markers)


def _fetch_amazon_page(url: str) -> dict:
    """Fetch an Amazon page, retrying a gated default response once.

    The ordinary request fingerprint is preferred: on some Amazon edges the
    Chrome-like headers and cookie below trigger a 153-byte interstitial while
    the default request returns the complete product page.  The browser-like
    request remains a fallback for storefronts that require it.
    """
    first_error: FetchError | None = None
    try:
        page = fetch_page(url)
    except FetchError as exc:
        first_error = exc
        page = None
    if page is not None and not _is_amazon_interstitial(page):
        return page

    try:
        retry = fetch_page(url, headers=_BROWSER_HEADERS)
    except FetchError:
        if page is not None:
            return page
        assert first_error is not None
        raise first_error
    if page is None:
        return retry
    if not _is_amazon_interstitial(retry):
        return retry
    # If both fingerprints are gated, keep the richer response so downstream
    # parsing has the best chance of recovering a title or diagnostic marker.
    page_size = len(str(page.get("html") or "")) + len(str(page.get("text") or ""))
    retry_size = len(str(retry.get("html") or "")) + len(str(retry.get("text") or ""))
    return retry if retry_size > page_size else page

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


def _extract_rank_levels(text: str, html: str = "") -> dict:
    """Extract broad and nested Amazon BSR levels from page variants.

    The detail-bullets block is not stable: depending on locale, device and
    bot treatment Amazon may return visible text, an HTML container, or only a
    fragment with ``#SalesRank``/``salesrank`` identifiers.  We first use the
    strongly-labelled English form, then fall back to the first two ``#N``
    tokens in a labelled rank window.  The latter deliberately does not rely
    on the word ``in`` so localized category labels and line-wrapped HTML are
    still captured.
    """

    def _rank_number(value: str | None) -> int | None:
        if not value:
            return None
        # Rank values are integers. Amazon locales use comma, dot, NBSP or
        # narrow-NBSP as thousands separators; stripping all separators keeps
        # the parser locale-neutral while rejecting non-numeric fragments.
        normalized = re.sub(r"[\s,\.\u00a0\u202f]", "", value)
        return _to_int(normalized)

    def _name_after_token(window: str, end: int, next_start: int | None) -> str:
        tail = window[end: next_start if next_start is not None else end + 240]
        # Generic fallback can operate on raw HTML containers; remove tags
        # before looking for the category label so markup does not become part
        # of the returned name.
        tail = re.sub(r"<[^>]+>", " ", tail)
        tail = html_lib.unescape(tail)
        # Category labels end before the explanatory ``See Top 100`` link,
        # parenthetical text, another rank, or a neighboring page label.
        # Normalize line-wrapped labels first (for example ``in\nDigital``).
        tail = re.sub(r"\s+", " ", tail)
        tail = re.split(
            r"\(|\)|#|See\s+Top\s+100|Top\s+100|\bASIN\b|\bCustomer\s+Reviews?\b",
            tail,
            maxsplit=1,
            flags=re.I,
        )[0]
        # BSR category names are often wrapped after the connector (for
        # example ``#12 in\nDigital Voice Recorders``). Keep those line breaks
        # while trimming the metadata that follows the BSR list.
        tail = re.split(
            r"\b(?:ASIN|Customer\s+Reviews?|Product\s+information|"
            r"Item\s+model\s+number|Date\s+First\s+Available|"
            r"Best\s+Sellers?\s+Rank)\b",
            tail,
            maxsplit=1,
            flags=re.I,
        )[0]
        tail = re.sub(
            r"^\s*(?:in|im|en|dans|sur|unter|カテゴリー?|カテゴリ|在|中的?)\s*[:：-]?\s*",
            "",
            tail,
            flags=re.I,
        )
        # When no connector is present, remove punctuation left by a label
        # such as ``Best Sellers Rank:`` and keep the category words.
        return clean_text(tail.strip(" :：\u00a0\u202f-–—>"))

    def _result(levels: list[tuple[int, str]]) -> dict:
        return {
            "category_rank": levels[0][0],
            "subcategory_rank": levels[1][0] if len(levels) > 1 else None,
            "category_name": levels[0][1],
            "subcategory_name": levels[1][1] if len(levels) > 1 else "",
        }

    def _windows(source: str, *, html_source: bool = False) -> list[str]:
        if not source:
            return []
        windows: list[str] = []
        # Prefer every labelled block instead of only the first marker: the
        # page header may contain a navigation ``Best Sellers`` link before the
        # actual product-detail block.
        markers = list(_BSR_MARKER_RE.finditer(source))
        for marker in markers:
            windows.append(source[marker.start(): marker.start() + 2000])
        if html_source:
            for container in _BSR_CONTAINER_RE.finditer(source):
                windows.append(source[container.start(): container.start() + 4000])
        if not windows:
            # Do not interpret arbitrary ``#123`` fragments elsewhere on a
            # page as a BSR. A generic token pass is safe only inside a
            # labelled rank block or a known rank container.
            return []
        # Preserve order while avoiding duplicate windows from text/html.
        return list(dict.fromkeys(windows))

    sources = []
    visible = text or ""
    stripped_html = _strip_html(html or "")
    for value, is_html in ((visible, False), (stripped_html, True)):
        if value and value not in {item[0] for item in sources}:
            sources.append((value, is_html))
    # A raw HTML pass is useful for ``id=SalesRank`` containers whose text is
    # absent from the visible-text parser due to script/template wrappers.
    if html:
        sources.append((html, True))

    for source, is_html in sources:
        for window in _windows(source, html_source=is_html):
            # Generic/localized fallback: use the first two rank tokens in a
            # labelled window. This also handles ``# 1,234`` and ``#1.234``.
            tokens = list(_BSR_TOKEN_RE.finditer(window))
            generic: list[tuple[int, str]] = []
            for index, token in enumerate(tokens[:3]):
                rank = _rank_number(token.group(1))
                if rank is None:
                    continue
                next_start = tokens[index + 1].start() if index + 1 < len(tokens) else None
                name = _name_after_token(window, token.end(), next_start)
                generic.append((rank, name))
                if len(generic) == 2:
                    break
            if generic:
                return _result(generic)

            # Keep the older strict English parser as a final pass for odd
            # markup where the rank token is present but the generic window
            # was not labelled cleanly.
            matches = list(_BSR_LEVEL_RE.finditer(window))
            levels: list[tuple[int, str]] = []
            for match in matches:
                rank = _rank_number(match.group(1))
                if rank is not None:
                    levels.append((rank, clean_text(match.group(2))))
            if levels:
                return _result(levels)

    # Last-resort compatibility path for legacy captures that only expose one
    # broad BSR value without a rank label or nested category.
    for source in (visible, stripped_html):
        fallback = _BSR_RE.search(source)
        if fallback:
            value = _rank_number(fallback.group(1))
            if value is not None:
                return {"category_rank": value, "subcategory_rank": None, "category_name": "", "subcategory_name": ""}
    return {}


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
                fetched = _fetch_amazon_page(page_url)
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
            page = _fetch_amazon_page(url)
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
        ranks = _extract_rank_levels(text, html)
        if ranks:
            snap.category_rank = ranks.get("category_rank")
            snap.subcategory_rank = ranks.get("subcategory_rank")
            snap.category_name = ranks.get("category_name") or ""
            snap.subcategory_name = ranks.get("subcategory_name") or ""
            # Keep legacy fields populated for old clients and market-share
            # consumers.  The broad category rank is the canonical fallback.
            snap.bsr = snap.category_rank
            snap.rank = snap.category_rank
        snap.price = _extract_price(html)
        if "currently unavailable" in low or "out of stock" in low:
            snap.in_stock = False
        elif "in stock" in low or snap.price is not None:
            snap.in_stock = True

        # A public Amazon page has no authoritative order count.  Use a
        # monotonic BSR estimate as a clearly-labelled fallback; SellerSprite
        # replaces this in the paid provider when configured.
        estimate = estimate_amazon_sales(
            rank=snap.category_rank if snap.category_rank is not None else snap.bsr,
            price=snap.price,
            marketplace=listing.get("marketplace") or "US",
        )
        if estimate:
            snap.units_est = estimate.get("units_est")
            snap.revenue_est = estimate.get("revenue_est")
            snap.estimate_method = estimate.get("estimate_method") or ""
            snap.estimate_confidence = estimate.get("estimate_confidence") or ""
            snap.estimate_period_days = estimate.get("estimate_period_days")
            snap.estimate_basis = estimate.get("estimate_basis") or {}
        parsed_any = any(v is not None for v in (snap.price, snap.rating, snap.review_count, snap.bsr, snap.category_rank))
        if not title and not parsed_any:
            snap.status = "blocked"
            snap.error = "No listing fields parsed (likely blocked or JS-rendered)."
        elif not parsed_any:
            snap.status = "partial"
        snap.raw = {"final_url": page.get("final_url"), "provider": self.name}
        if ranks:
            # Keep the parser output in raw_json as a migration/debug seam for
            # installations whose database predates dedicated rank columns.
            snap.raw["rank_levels"] = ranks
        return snap
