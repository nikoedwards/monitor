"""Sales provider adapter contract.

A `SalesProvider` knows how to (1) expand a configured storefront/shop link into
individual product listings (the "Listing List"), and (2) fetch a daily snapshot
of metrics for one listing. Concrete providers (scrape / SellerSprite) implement
the two methods; the runner stays provider-agnostic.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ListingRef:
    """A single product listing discovered from a storefront/shop link."""

    url: str
    asin: str = ""
    title: str = ""
    image_url: str = ""
    sku: str = ""
    marketplace: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class ListingSnapshot:
    """One daily capture of a listing's metrics. Missing fields stay None."""

    title: str = ""
    sku: str = ""
    image_url: str = ""
    price: Optional[float] = None
    currency: str = "USD"
    rating: Optional[float] = None
    review_count: Optional[int] = None
    rank: Optional[int] = None
    bsr: Optional[int] = None
    units_est: Optional[int] = None
    revenue_est: Optional[float] = None
    in_stock: Optional[bool] = None
    status: str = "ok"          # ok | partial | blocked | error
    error: str = ""
    raw: dict = field(default_factory=dict)

    def fingerprint_fields(self) -> dict:
        """Subset of fields used to detect a listing *content* change.

        Price is intentionally excluded (tracked as a trend metric, not a "change").
        """
        return {
            "title": self.title or "",
            "sku": self.sku or "",
            "image_url": self.image_url or "",
            "in_stock": self.in_stock,
        }


class SalesProvider:
    """Base provider. Subclasses override `expand` and `fetch`."""

    name = "base"

    def expand(self, conn: sqlite3.Connection, link: dict) -> list[ListingRef]:
        raise NotImplementedError

    def fetch(self, conn: sqlite3.Connection, listing: dict) -> ListingSnapshot:
        raise NotImplementedError
