"""Transparent, low-confidence sales estimates for scrape-only channels.

Marketplace APIs such as SellerSprite can provide observed model estimates.  A
plain product page cannot provide orders directly, so the fallback functions in
this module deliberately label their basis and confidence in ``raw_json``.
They are useful for trends and prioritisation, but are never presented as
actual seller-reported units.
"""
from __future__ import annotations

import math
from datetime import date


AMAZON_BSR_CONFIDENCE = "low"
DTC_REVIEW_CONFIDENCE = "low"
DTC_REVIEW_RATE = 0.02  # one review per ~50 orders; conservative proxy
DTC_DEFAULT_AGE_DAYS = 180.0


def _positive_int(value) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def estimate_amazon_sales(
    *,
    rank: int | None,
    price: float | None = None,
    marketplace: str = "US",
) -> dict:
    """Estimate daily Amazon units from a BSR-like rank.

    The ``1000 / sqrt(rank)`` curve is intentionally broad and marked low
    confidence.  It gives a stable monotonic series when SellerSprite/Keepa is
    unavailable while preserving the measured rank and price as the evidence.
    """
    rank_value = _positive_int(rank)
    if rank_value is None:
        return {}
    units = max(1, min(1_000_000, int(round(1000 / math.sqrt(rank_value)))))
    result = {
        "units_est": units,
        "revenue_est": round(units * float(price), 2) if price is not None and float(price) >= 0 else None,
        "estimate_method": "amazon_bsr_curve",
        "estimate_confidence": AMAZON_BSR_CONFIDENCE,
        "estimate_period_days": 1.0,
        "estimate_basis": {"rank": rank_value, "marketplace": marketplace or "US"},
    }
    return result


def _days_between(previous_date: str | None, current_date: str | None) -> float:
    if previous_date and current_date:
        try:
            days = (date.fromisoformat(current_date[:10]) - date.fromisoformat(previous_date[:10])).days
            if days > 0:
                return float(days)
        except ValueError:
            pass
    return 1.0


def estimate_dtc_sales(
    *,
    price: float | None,
    review_count: int | None,
    previous_review_count: int | None = None,
    previous_date: str | None = None,
    current_date: str | None = None,
    review_rate: float = DTC_REVIEW_RATE,
    assumed_age_days: float = DTC_DEFAULT_AGE_DAYS,
) -> dict:
    """Estimate DTC daily units from review velocity or review stock.

    When two snapshots have a positive review delta, that delta is preferred.
    On the first snapshot, total reviews are spread over a conservative assumed
    product age so a useful baseline appears without pretending the page knows
    its order count.  Empty review signals remain unavailable.
    """
    reviews = _positive_int(review_count)
    rate = float(review_rate) if review_rate and review_rate > 0 else DTC_REVIEW_RATE
    age_days = max(1.0, float(assumed_age_days or DTC_DEFAULT_AGE_DAYS))
    units: int | None = None
    period_days = 1.0
    method = ""
    basis: dict = {"review_rate": rate}
    if reviews is not None and previous_review_count is not None:
        previous = max(0, int(previous_review_count))
        delta = reviews - previous
        period_days = _days_between(previous_date, current_date)
        if delta > 0:
            units = max(1, int(round(delta / rate / period_days)))
            method = "dtc_review_velocity"
            basis.update({"review_delta": delta, "period_days": period_days})
    if units is None and reviews is not None:
        units = max(1, int(round(reviews / rate / age_days)))
        method = "dtc_review_stock"
        period_days = 1.0
        basis.update({"review_count": reviews, "assumed_age_days": age_days})
    if units is None:
        return {}
    return {
        "units_est": units,
        "revenue_est": round(units * float(price), 2) if price is not None and float(price) >= 0 else None,
        "estimate_method": method,
        "estimate_confidence": DTC_REVIEW_CONFIDENCE,
        "estimate_period_days": period_days,
        "estimate_basis": basis,
    }

