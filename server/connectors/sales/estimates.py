"""Transparent, low-confidence sales estimates for scrape-only channels.

Marketplace APIs such as SellerSprite can provide observed model estimates.  A
plain product page cannot provide orders directly, so the fallback functions in
this module deliberately label their basis and confidence in ``raw_json``.
They are useful for trends and prioritisation, but are never presented as
actual seller-reported units.
"""
from __future__ import annotations

import math
import json
from datetime import date
from typing import Mapping


AMAZON_BSR_CONFIDENCE = "low"
DTC_REVIEW_CONFIDENCE = "low"
DTC_REVIEW_RATE = 0.02  # one review per ~50 orders; conservative proxy
DTC_DEFAULT_AGE_DAYS = 180.0


def _number(value) -> float | None:
    """Parse the loose numeric values found in legacy raw snapshots."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def recover_rank_levels(raw: Mapping | None) -> dict:
    """Recover rank fields saved by an older provider in ``raw_json``.

    Older captures did not have dedicated rank columns.  A few provider
    versions nevertheless persisted a rank object or the original BSR block;
    use those values during migration before asking the next scrape to fill
    them again.
    """
    if not isinstance(raw, Mapping):
        return {}
    candidates = [
        raw.get("rank_levels"),
        raw.get("rank_parser"),
        raw.get("sales_rank"),
        raw.get("rank"),
    ]
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            category = candidate.get("category_rank") or candidate.get("main_category_rank")
            subcategory = candidate.get("subcategory_rank") or candidate.get("sub_category_rank")
            result = {}
            category_value = _positive_int(category)
            subcategory_value = _positive_int(subcategory)
            if category_value is not None:
                result["category_rank"] = category_value
            if subcategory_value is not None:
                result["subcategory_rank"] = subcategory_value
            if result:
                return result
        if isinstance(candidate, (list, tuple)):
            values = [value for item in candidate if (value := _positive_int(item)) is not None]
            if values:
                return {
                    "category_rank": values[0],
                    "subcategory_rank": values[1] if len(values) > 1 else None,
                }
    for key in ("category_rank", "main_category_rank", "bsr", "rank"):
        value = _positive_int(raw.get(key))
        if value is not None:
            result = {"category_rank": value}
            for sub_key in ("subcategory_rank", "sub_category_rank"):
                sub_value = _positive_int(raw.get(sub_key))
                if sub_value is not None:
                    result["subcategory_rank"] = sub_value
                    break
            return result
    return {}


def _positive_int(value) -> int | None:
    try:
        number = float(str(value).replace(",", "").replace("\u00a0", "").strip())
        if not math.isfinite(number):
            return None
        number = int(number)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def _nonnegative_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


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
    price_value = _nonnegative_float(price)
    result = {
        "units_est": units,
        "revenue_est": round(units * price_value, 2) if price_value is not None else None,
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
    parsed_rate = _nonnegative_float(review_rate)
    rate = parsed_rate if parsed_rate and parsed_rate > 0 else DTC_REVIEW_RATE
    parsed_age = _nonnegative_float(assumed_age_days)
    age_days = max(1.0, parsed_age or DTC_DEFAULT_AGE_DAYS)
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
        "revenue_est": (
            round(units * price_value, 2)
            if (price_value := _nonnegative_float(price)) is not None
            else None
        ),
        "estimate_method": method,
        "estimate_confidence": DTC_REVIEW_CONFIDENCE,
        "estimate_period_days": period_days,
        "estimate_basis": basis,
    }


def backfill_sales_metrics(conn) -> int:
    """Fill estimate/rank columns for snapshots written before this feature.

    This is intentionally idempotent and only fills missing values.  It lets
    an upgraded installation show estimates immediately instead of waiting for
    the next daily scheduler run.  Provider observations already present in a
    row always win over the fallback models.
    """
    try:
        table_info = conn.execute("PRAGMA table_info(sales_metrics)").fetchall()
        # The application uses sqlite3.Row, but tests and one-off migration
        # scripts often use the default tuple row factory.
        columns = {
            row["name"] if isinstance(row, Mapping) else row[1]
            for row in table_info
        }
    except Exception:
        return 0
    required = {"id", "channel", "price", "review_count", "units_est", "revenue_est", "snapshot_date"}
    if not required.issubset(columns):
        return 0
    select_columns = [
        "id", "link_id", "channel", "price", "review_count", "snapshot_date",
        "units_est", "revenue_est", "rank", "bsr", "raw_json",
        "category_rank", "subcategory_rank", "estimate_method",
        "estimate_confidence", "estimate_period_days",
    ]
    select_columns = [column for column in select_columns if column in columns]
    order = (
        "COALESCE(link_id, id), snapshot_date, created_at"
        if "created_at" in columns and "link_id" in columns
        else "COALESCE(link_id, id), snapshot_date"
        if "link_id" in columns
        else "id, snapshot_date"
    )
    rows = conn.execute(
        f"SELECT {', '.join(select_columns)} FROM sales_metrics ORDER BY {order}"
    ).fetchall()
    previous_by_link: dict[str, dict] = {}
    changed = 0
    for row in rows:
        if isinstance(row, Mapping):
            item = dict(row)
        else:
            item = dict(zip(select_columns, row))
        raw = {}
        if item.get("raw_json"):
            try:
                parsed = json.loads(item["raw_json"])
                if isinstance(parsed, dict):
                    raw = parsed
            except (TypeError, ValueError):
                raw = {}
        rank_fields = recover_rank_levels(raw)
        updates: dict[str, object] = {}
        # Before explicit category columns existed, the broad BSR was stored
        # in ``bsr`` or ``rank``. Promote that legacy signal so summaries and
        # the new category field agree immediately after migration.
        legacy_category = (
            rank_fields.get("category_rank")
            or _positive_int(item.get("bsr"))
            or _positive_int(item.get("rank"))
        )
        if "category_rank" in columns and item.get("category_rank") is None and legacy_category is not None:
            updates["category_rank"] = legacy_category
        if "subcategory_rank" in columns and item.get("subcategory_rank") is None and rank_fields.get("subcategory_rank") is not None:
            updates["subcategory_rank"] = rank_fields["subcategory_rank"]

        # Do not let unrelated unlinked/manual rows share a review baseline.
        # Linked rows retain velocity estimates across snapshots; an unlinked
        # row falls back to the conservative review-stock model.
        row_key = item.get("link_id") or item.get("id")
        previous = previous_by_link.get(row_key)
        estimate = {}
        if item.get("channel") == "amazon":
            rank = item.get("category_rank") or legacy_category
            estimate = estimate_amazon_sales(rank=rank, price=item.get("price"), marketplace=raw.get("marketplace") or "US")
        elif item.get("channel") in {"dtc", "other_ecom"}:
            estimate = estimate_dtc_sales(
                price=item.get("price"),
                review_count=item.get("review_count"),
                previous_review_count=previous.get("review_count") if previous else None,
                previous_date=previous.get("snapshot_date") if previous else None,
                current_date=item.get("snapshot_date"),
            )
        # If a legacy/manual row already has units, derive its missing revenue
        # from the same units and price. Do not replace an observed units value
        # with a BSR/review-model value merely because revenue was omitted.
        derived_revenue = None
        if item.get("units_est") is not None and item.get("revenue_est") is None and item.get("price") is not None:
            try:
                price = float(item["price"])
                units = float(item["units_est"])
                if price >= 0 and units >= 0:
                    derived_revenue = round(units * price, 2)
            except (TypeError, ValueError):
                derived_revenue = None
        fill_units = bool(estimate and item.get("units_est") is None and estimate.get("units_est") is not None)
        fill_revenue = bool(estimate and item.get("revenue_est") is None and estimate.get("revenue_est") is not None and item.get("units_est") is None)
        if derived_revenue is not None:
            updates["revenue_est"] = derived_revenue
        if estimate:
            if fill_units:
                updates["units_est"] = estimate["units_est"]
            if fill_revenue:
                updates["revenue_est"] = estimate["revenue_est"]
            # Only label a legacy row as model-estimated when this pass filled
            # an estimate field. Existing observed/manual units must not be
            # silently reclassified as a BSR/review estimate.
            if fill_units or fill_revenue:
                for key in ("estimate_method", "estimate_confidence", "estimate_period_days"):
                    if key in columns and item.get(key) is None and estimate.get(key) is not None:
                        updates[key] = estimate[key]
                if "raw_json" in columns:
                    raw_with_estimate = dict(raw)
                    raw_with_estimate.setdefault("estimate_method", estimate.get("estimate_method"))
                    raw_with_estimate.setdefault("estimate_confidence", estimate.get("estimate_confidence"))
                    raw_with_estimate.setdefault("estimate_period_days", estimate.get("estimate_period_days"))
                    raw_with_estimate.setdefault("estimate_basis", estimate.get("estimate_basis") or {})
                    encoded_raw = json.dumps(raw_with_estimate, ensure_ascii=False)
                    if encoded_raw != (item.get("raw_json") or ""):
                        updates["raw_json"] = encoded_raw
        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(
                f"UPDATE sales_metrics SET {assignments} WHERE id = ?",
                [*updates.values(), item["id"]],
            )
            changed += 1
        previous_by_link[row_key] = {
            "review_count": item.get("review_count"),
            "snapshot_date": item.get("snapshot_date"),
        }
    return changed

