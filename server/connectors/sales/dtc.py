"""Generic DTC / e-commerce scraping provider.

Expansion crawls a shop / collection page for same-host product links; fetch reads
schema.org ``Product`` / ``Offer`` JSON-LD (price, availability, rating, reviews, sku)
with og-meta fallbacks. Works well for Shopify-style stores and any site exposing
product structured data.
"""
from __future__ import annotations

import re
import sqlite3

from ...fetchers import FetchError, fetch_page
from ...util import canonical_url, clean_external_link, clean_text, host_key, is_html_like_url, normalize_url, today
from .base import ListingRef, ListingSnapshot, SalesProvider
from .estimates import estimate_dtc_sales

_PRODUCT_PATH_RE = re.compile(r"/(products?|product|item|p|shop|dp)/", re.I)


def _iter_nodes(node) -> list[dict]:
    """Flatten JSON-LD (handles lists and @graph) into a list of dict nodes."""
    out: list[dict] = []
    if isinstance(node, list):
        for item in node:
            out.extend(_iter_nodes(item))
    elif isinstance(node, dict):
        if "@graph" in node and isinstance(node["@graph"], list):
            for item in node["@graph"]:
                out.extend(_iter_nodes(item))
        out.append(node)
    return out


def _is_type(node: dict, wanted: str) -> bool:
    t = node.get("@type")
    if isinstance(t, list):
        return any(str(x).lower() == wanted for x in t)
    return str(t).lower() == wanted


def _to_float(value) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _first_offer(node: dict) -> dict:
    offers = node.get("offers")
    if isinstance(offers, list) and offers:
        return offers[0] if isinstance(offers[0], dict) else {}
    if isinstance(offers, dict):
        return offers
    return {}


def _extract_review_signals(html: str, text: str, meta: dict) -> tuple[float | None, int | None, str]:
    """Recover rating/review count when JSON-LD is absent or incomplete.

    Shopify review apps and rendered storefront themes commonly expose these
    values as data attributes, accessible labels, or plain text rather than in
    ``aggregateRating``.  Keep the patterns tied to a review/rating label so
    prices, years, and unrelated counters are not mistaken for reviews.
    """
    source = html or ""
    visible = text or ""
    metadata = {str(key).lower(): value for key, value in (meta or {}).items()}

    rating: float | None = None
    review_count: int | None = None

    def number(value) -> float | None:
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    def integer(value) -> int | None:
        value = str(value or "").replace("\u00a0", " ").strip()
        match = re.search(r"\d[\d,\.\s]*", value)
        if not match:
            return None
        try:
            return int(float(match.group(0).replace(",", "").replace(" ", "")))
        except (TypeError, ValueError):
            return None

    # Meta and common review-app attributes are the least ambiguous signals.
    for key in (
        "product:rating", "product:rating_value", "rating", "ratingvalue",
        "aggregate_rating", "aggregate-rating", "data-rating",
    ):
        candidate = number(metadata.get(key))
        if candidate is not None and 0 <= candidate <= 5:
            rating = candidate
            break
    for key in (
        "product:review_count", "product:reviewcount", "review_count",
        "reviewcount", "rating_count", "ratingcount", "reviews_count",
        "reviews-count", "data-review-count", "data-reviews-count",
    ):
        candidate = integer(metadata.get(key))
        if candidate is not None and candidate >= 0:
            review_count = candidate
            break

    rating_patterns = (
        r"(?:ratingvalue|rating_value|rating|data-rating)\s*[\"'=:> ]+([0-5](?:[\.,][0-9]+)?)",
        r"([0-5](?:[\.,][0-9]+)?)\s*(?:out\s+of\s+5|/\s*5|stars?)",
    )
    for pattern in rating_patterns:
        match = re.search(pattern, source, re.I) or re.search(pattern, visible, re.I)
        if match:
            candidate = number(match.group(1).replace(",", "."))
            if candidate is not None and 0 <= candidate <= 5:
                rating = rating if rating is not None else candidate
                break

    count_patterns = (
        r"(?:review[_\- ]?count|reviews[_\- ]?count|rating[_\- ]?count|data-(?:review|reviews)[-_]count)\s*[\"'=:> ]+(\d[\d,\.\s]*)",
        r"(\d[\d,\.\s]*)\s*(?:customer\s+)?(?:reviews?|ratings?|reviews?\s+and\s+ratings?|条评论|条评价|评论|评价)\b",
        r"(?:reviews?|ratings?|评论|评价)[^\d]{0,24}(\d[\d,\.\s]*)",
    )
    for pattern in count_patterns:
        match = re.search(pattern, source, re.I) or re.search(pattern, visible, re.I)
        if match:
            candidate = integer(match.group(1))
            if candidate is not None and candidate >= 0:
                review_count = review_count if review_count is not None else candidate
                break

    return rating, review_count, "html_fallback" if rating is not None or review_count is not None else ""


class ScrapeDtcProvider(SalesProvider):
    name = "dtc_scrape"

    def __init__(self, max_listings: int = 30) -> None:
        self.max_listings = max(1, max_listings)

    # ------------------------------------------------------------------ expand
    def expand(self, conn: sqlite3.Connection, link: dict) -> list[ListingRef]:
        url = link.get("url") or ""
        try:
            start = normalize_url(url)
        except ValueError:
            return []
        try:
            page = fetch_page(start)
        except FetchError:
            return [ListingRef(url=start)]

        root_host = host_key(start)
        found: list[str] = []
        seen: set[str] = set()
        for href in page.get("anchors", []):
            candidate = clean_external_link(page.get("final_url") or start, href)
            if (
                candidate
                and host_key(candidate) == root_host
                and _PRODUCT_PATH_RE.search(candidate)
                and is_html_like_url(candidate)
                and candidate not in seen
            ):
                seen.add(candidate)
                found.append(candidate)
            if len(found) >= self.max_listings:
                break

        # If the configured URL is itself a product page (or nothing discovered),
        # monitor it directly.
        if not found:
            return [ListingRef(url=start)]
        return [ListingRef(url=u) for u in found]

    # ------------------------------------------------------------------- fetch
    def fetch(self, conn: sqlite3.Connection, listing: dict) -> ListingSnapshot:
        url = listing.get("url") or ""
        snap = ListingSnapshot()
        try:
            page = fetch_page(url)
        except FetchError as exc:
            snap.status = "error"
            snap.error = str(exc)[:300]
            return snap

        meta = page.get("meta") or {}
        snap.title = clean_text(meta.get("og:title") or page.get("title"))
        snap.image_url = meta.get("og:image") or ""

        product = None
        for node in _iter_nodes(page.get("json_ld") or []):
            if _is_type(node, "product"):
                product = node
                break

        if product:
            snap.title = clean_text(product.get("name")) or snap.title
            snap.sku = clean_text(str(product.get("sku") or product.get("mpn") or ""))
            img = product.get("image")
            if isinstance(img, list) and img:
                img = img[0]
            if isinstance(img, str):
                snap.image_url = img or snap.image_url
            offer = _first_offer(product)
            if offer:
                snap.price = _to_float(offer.get("price") or offer.get("lowPrice"))
                snap.currency = clean_text(str(offer.get("priceCurrency") or "")) or "USD"
                avail = str(offer.get("availability") or "").lower()
                if avail:
                    snap.in_stock = "instock" in avail or "in_stock" in avail or avail.endswith("instock")
            rating = product.get("aggregateRating")
            if isinstance(rating, dict):
                snap.rating = _to_float(rating.get("ratingValue"))
                snap.review_count = _to_int(rating.get("reviewCount") or rating.get("ratingCount"))

        # og-meta price fallback.
        if snap.price is None:
            snap.price = _to_float(meta.get("product:price:amount") or meta.get("og:price:amount"))
            cur = meta.get("product:price:currency") or meta.get("og:price:currency")
            if cur:
                snap.currency = cur

        # A number of Shopify themes load reviews client-side and omit
        # aggregateRating from JSON-LD.  Recover the labelled values from the
        # rendered HTML/meta so the review-based estimate can still be formed.
        fallback_rating, fallback_reviews, fallback_source = _extract_review_signals(
            page.get("html") or "", page.get("text") or "", meta,
        )
        if snap.rating is None:
            snap.rating = fallback_rating
        if snap.review_count is None:
            snap.review_count = fallback_reviews

        # DTC pages generally expose no order count.  Use review stock/velocity
        # as a transparent low-confidence proxy so the daily history can still
        # show an estimated units/revenue trend.  If no reviews exist, leave
        # estimates null rather than fabricating a number from price alone.
        previous = None
        if conn is not None and listing.get("id"):
            previous = conn.execute(
                "SELECT snapshot_date, review_count FROM sales_metrics "
                "WHERE link_id = ? ORDER BY snapshot_date DESC, created_at DESC LIMIT 1",
                (listing["id"],),
            ).fetchone()
        estimate = estimate_dtc_sales(
            price=snap.price,
            review_count=snap.review_count,
            previous_review_count=previous["review_count"] if previous else None,
            previous_date=previous["snapshot_date"] if previous else None,
            current_date=today(),
        )
        if estimate:
            snap.units_est = estimate.get("units_est")
            snap.revenue_est = estimate.get("revenue_est")
            snap.estimate_method = estimate.get("estimate_method") or ""
            snap.estimate_confidence = estimate.get("estimate_confidence") or ""
            snap.estimate_period_days = estimate.get("estimate_period_days")
            snap.estimate_basis = estimate.get("estimate_basis") or {}

        if snap.price is None and snap.rating is None and not product:
            snap.status = "partial"
        snap.raw = {
            "final_url": page.get("final_url"),
            "provider": self.name,
            "had_jsonld": bool(product),
        }
        if fallback_source:
            snap.raw["review_signal_source"] = fallback_source
        if estimate:
            snap.raw.update({
                "estimate_method": snap.estimate_method,
                "estimate_confidence": snap.estimate_confidence,
                "estimate_period_days": snap.estimate_period_days,
                "estimate_basis": snap.estimate_basis,
            })
        snap.sku = snap.sku or canonical_url(url)[-40:]
        return snap
