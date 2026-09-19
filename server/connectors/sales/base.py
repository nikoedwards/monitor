"""Sales provider adapter contract.

A `SalesProvider` knows how to (1) expand a configured storefront/shop link into
individual product listings (the "Listing List"), and (2) fetch a daily snapshot
of metrics for one listing. Concrete providers (scrape / SellerSprite) implement
the two methods; the runner stays provider-agnostic.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Mapping, Optional


def metric_rank_value(values: Mapping[str, object]):
    """Return the canonical (large-category) rank signal.

    ``rank``/``bsr`` predate the separate category fields and remain the
    fallback so old snapshots continue to participate in deltas and summaries.
    """
    for key in ("category_rank", "bsr", "rank"):
        value = values.get(key)
        if value is not None:
            return value
    return None


def canonicalize_metric_changes(changes: list[dict] | None) -> list[dict]:
    """Collapse ``bsr``/``rank`` aliases into one authoritative rank change."""
    normalized: list[dict] = []
    indexes: dict[str, int] = {}
    priorities: dict[str, int] = {}
    for raw_change in changes or []:
        if not isinstance(raw_change, dict):
            continue
        source_field = str(raw_change.get("field") or "other")
        field = "rank" if source_field in {"rank", "bsr"} else source_field
        change = {**raw_change, "field": field}
        priority = 1 if source_field == "bsr" else 0
        if field not in indexes:
            indexes[field] = len(normalized)
            priorities[field] = priority
            normalized.append(change)
            continue
        existing = normalized[indexes[field]]
        if existing.get("from") == change.get("from") and existing.get("to") == change.get("to"):
            if field == "rank" and priority > priorities[field]:
                normalized[indexes[field]] = change
                priorities[field] = priority
            continue
        # Repeated captures in one day may contain multiple transitions for a
        # field. Keep the day's original baseline and newest observed value.
        existing["to"] = change.get("to")
        priorities[field] = max(priorities[field], priority)
    return normalized


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
    category_rank: Optional[int] = None
    subcategory_rank: Optional[int] = None
    category_name: str = ""
    subcategory_name: str = ""
    units_est: Optional[int] = None
    revenue_est: Optional[float] = None
    estimate_method: str = ""
    estimate_confidence: str = ""
    estimate_period_days: Optional[float] = None
    estimate_basis: dict = field(default_factory=dict)
    in_stock: Optional[bool] = None
    status: str = "ok"          # ok | partial | blocked | error
    error: str = ""
    raw: dict = field(default_factory=dict)

    def fingerprint_fields(self) -> dict:
        """Fields whose observed changes belong in the sales monitoring log."""
        fields = {
            "title": self.title or "",
            "sku": self.sku or "",
            "image_url": self.image_url or "",
            "in_stock": self.in_stock,
            "rating": self.rating,
            "review_count": self.review_count,
            "price": self.price,
            "currency": self.currency,
            "units_est": self.units_est,
            "revenue_est": self.revenue_est,
        }
        # New captures use explicit category levels. Keep the legacy rank alias
        # when no level was available, while always retaining the explicit keys
        # once a collector has started producing them so a later disappearance
        # is visible in the change log.
        fields["category_rank"] = self.category_rank
        fields["subcategory_rank"] = self.subcategory_rank
        if self.category_rank is None and self.subcategory_rank is None:
            fields["rank"] = self.bsr if self.bsr is not None else self.rank
        return fields


class SalesProvider:
    """Base provider. Subclasses override `expand` and `fetch`."""

    name = "base"

    def expand(self, conn: sqlite3.Connection, link: dict) -> list[ListingRef]:
        raise NotImplementedError

    def fetch(self, conn: sqlite3.Connection, listing: dict) -> ListingSnapshot:
        raise NotImplementedError
