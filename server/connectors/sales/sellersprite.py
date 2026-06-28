"""SellerSprite (\u5356\u5bb6\u7cbe\u7075) OpenAPI provider (paid seam).

Lights up only when a ``sellersprite_secret_key`` is configured. SellerSprite has no
"list a seller's ASINs" endpoint in the public API, so storefront expansion still uses
the Amazon scraper to discover ASINs; per-listing metrics then come from the reliable
``ASIN Sales Estimator`` endpoint.

Docs: GET https://api.sellersprite.com/v1/sales/prediction/asin?marketplace=US&asin=...
Header: ``secret-key: <key>``
"""
from __future__ import annotations

import sqlite3

from ...fetchers import FetchError, fetch_json
from ...util import clean_text
from .amazon import ScrapeAmazonProvider
from .base import ListingRef, ListingSnapshot, SalesProvider

_API_BASE = "https://api.sellersprite.com"


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class SellerSpriteProvider(SalesProvider):
    name = "sellersprite"

    def __init__(self, secret_key: str) -> None:
        self.secret_key = secret_key
        self._scraper = ScrapeAmazonProvider()

    def expand(self, conn: sqlite3.Connection, link: dict) -> list[ListingRef]:
        # ASIN discovery is not exposed by the API -> reuse the scraper.
        return self._scraper.expand(conn, link)

    def fetch(self, conn: sqlite3.Connection, listing: dict) -> ListingSnapshot:
        asin = (listing.get("asin") or "").strip()
        marketplace = (listing.get("marketplace") or "US").strip() or "US"
        snap = ListingSnapshot()
        if not asin:
            # No ASIN -> fall back to scraping this listing.
            return self._scraper.fetch(conn, listing)

        url = f"{_API_BASE}/v1/sales/prediction/asin?marketplace={marketplace}&asin={asin}"
        try:
            payload = fetch_json(url, headers={"secret-key": self.secret_key}, timeout=20)
        except FetchError as exc:
            snap.status = "error"
            snap.error = f"SellerSprite API error: {str(exc)[:240]}"
            return snap

        data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            snap.status = "error"
            snap.error = "Unexpected SellerSprite response shape."
            return snap

        detail = data.get("asinDetail") or {}
        snap.title = clean_text(detail.get("title"))
        snap.sku = asin
        snap.image_url = detail.get("imageUrl") or ""
        snap.rating = _to_float(detail.get("rating"))
        snap.review_count = _to_int(detail.get("ratings"))

        daily = data.get("dailyItemList") or []
        latest = daily[-1] if isinstance(daily, list) and daily else {}
        if latest:
            snap.bsr = _to_int(latest.get("bsr"))
            snap.rank = snap.bsr
            snap.units_est = _to_int(latest.get("sales"))
            snap.revenue_est = _to_float(latest.get("amount"))
            snap.price = _to_float(latest.get("price"))
            snap.in_stock = True
        snap.currency = "USD"
        snap.status = "ok" if (snap.units_est is not None or snap.bsr is not None) else "partial"
        snap.raw = {"provider": self.name, "marketplace": marketplace}
        return snap
