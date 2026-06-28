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
from ...util import canonical_url, clean_external_link, clean_text, host_key, is_html_like_url, normalize_url
from .base import ListingRef, ListingSnapshot, SalesProvider

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

        if snap.price is None and snap.rating is None and not product:
            snap.status = "partial"
        snap.raw = {"final_url": page.get("final_url"), "provider": self.name, "had_jsonld": bool(product)}
        snap.sku = snap.sku or canonical_url(url)[-40:]
        return snap
