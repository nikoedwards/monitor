"""Background scheduler: periodically run due collectors and web snapshots."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import SCHEDULER_ENABLED, SCHEDULER_SECONDS, WEB_SCHEDULER_SECONDS
from .connectors.base import run_collector
from .connectors.registry import REGISTRY
from .db import db
from .util import today

_started = False
logger = logging.getLogger(__name__)


def _cadence_delta(cadence: str) -> timedelta:
    value = (cadence or "daily").lower()
    if value == "hourly":
        return timedelta(hours=1)
    if value == "weekly":
        return timedelta(days=7)
    return timedelta(days=1)


def _collector_is_due(conn, source_id: str, brand_id: str, cadence: str, now: datetime) -> bool:
    row = conn.execute(
        "SELECT last_collect_at FROM source_brand_runs WHERE source_id = ? AND brand_id = ?",
        (source_id, brand_id),
    ).fetchone()
    if not row or not row["last_collect_at"]:
        return True
    try:
        last = datetime.fromisoformat(row["last_collect_at"].replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return now >= last.astimezone(timezone.utc) + _cadence_delta(cadence)


def _run_due_collections() -> None:
    with db() as conn:
        brands = [dict(r) for r in conn.execute("SELECT * FROM brands").fetchall()]
    if not brands:
        return
    for spec in REGISTRY:
        if spec.collect is None or spec.status != "ready":
            continue
        for brand in brands:
            try:
                with db() as conn:
                    if not _collector_is_due(conn, spec.id, brand["id"], spec.cadence, datetime.now(timezone.utc)):
                        continue
                    run_collector(conn, spec, brand)
            except Exception:
                # run_collector already records errors per source; keep loop alive.
                logger.exception("Collector failed for %s / %s", spec.id, brand.get("id"))
                continue


def _run_due_sales() -> None:
    from .connectors.sales.runner import run_sales_collection

    with db() as conn:
        brands = [dict(r) for r in conn.execute("SELECT * FROM brands").fetchall()]
    for brand in brands:
        # Only run when the brand has an active automated sales link due today.
        with db() as conn:
            due = conn.execute(
                """
                SELECT 1 FROM links
                WHERE brand_id = ? AND dimension = 'sales' AND status = 'active'
                      AND url IS NOT NULL AND url != ''
                      AND channel IN ('amazon', 'dtc', 'other_ecom')
                      AND (last_collect_at IS NULL OR substr(last_collect_at, 1, 10) < ?)
                LIMIT 1
                """,
                (brand["id"], today()),
            ).fetchone()
        if not due:
            continue
        try:
            with db() as conn:
                run_sales_collection(conn, brand)
        except Exception:
            logger.exception("Sales collection failed for %s", brand.get("id"))
            continue


def _run_due_hiring() -> None:
    from .connectors.hiring.runner import run_hiring_collection, run_linkedin_people_collection

    with db() as conn:
        brands = [dict(r) for r in conn.execute("SELECT * FROM brands").fetchall()]
    for brand in brands:
        with db() as conn:
            due_links = conn.execute(
                """
                SELECT platform FROM links
                WHERE brand_id = ? AND dimension = 'hiring' AND status = 'active'
                      AND url IS NOT NULL AND url != ''
                      AND (last_collect_at IS NULL OR substr(last_collect_at, 1, 10) < ?)
                """,
                (brand["id"], today()),
            ).fetchall()
            due_profile = conn.execute(
                """
                SELECT 1 FROM linkedin_profiles
                WHERE brand_id = ? AND monitor = 1 AND status = 'active'
                  AND (last_seen IS NULL OR substr(last_seen, 1, 10) < ?)
                LIMIT 1
                """,
                (brand["id"], today()),
            ).fetchone()
        if not due_links and not due_profile:
            continue
        platforms = {row["platform"] for row in due_links}
        if platforms - {"linkedin_people"}:
            try:
                with db() as conn:
                    run_hiring_collection(conn, brand)
            except Exception:
                logger.exception("Hiring collection failed for %s", brand.get("id"))
        if "linkedin_people" in platforms or due_profile:
            try:
                with db() as conn:
                    run_linkedin_people_collection(conn, brand)
            except Exception:
                logger.exception("LinkedIn people collection failed for %s", brand.get("id"))


def _run_due_market_share_snapshots() -> None:
    """Materialize today's market-share inputs after scheduled App collection."""
    from .domains.market_share import sync_market_share_snapshots

    try:
        with db() as conn:
            sync_market_share_snapshots(conn)
    except Exception:
        logger.exception("Market-share snapshot refresh failed")


def _run_due_web_snapshots() -> None:
    from datetime import datetime, timezone

    from .domains.web import capture_monitor, check_monitor, monitor_is_due

    with db() as conn:
        monitors = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM web_monitors WHERE status = 'active'",
            ).fetchall()
        ]
    now = datetime.now(timezone.utc)
    for monitor in monitors:
        try:
            with db() as conn:
                if monitor_is_due(monitor, "snapshot", now):
                    capture_monitor(conn, monitor)
                elif monitor_is_due(monitor, "check", now):
                    check_monitor(conn, monitor)
        except Exception:
            logger.exception("Web monitor scheduler failed for %s", monitor.get("id"))
            continue


def _collection_loop() -> None:
    while True:
        try:
            _run_due_collections()
            _run_due_sales()
            _run_due_hiring()
            _run_due_market_share_snapshots()
        except Exception:
            logger.exception("Collection scheduler cycle failed")
        time.sleep(max(60, SCHEDULER_SECONDS))


def _web_snapshot_loop() -> None:
    """Poll webpage work independently so slow collectors cannot delay retries."""
    while True:
        try:
            _run_due_web_snapshots()
        except Exception:
            logger.exception("Web snapshot scheduler cycle failed")
        time.sleep(max(30, WEB_SCHEDULER_SECONDS))


def start_scheduler() -> None:
    global _started
    if _started or not SCHEDULER_ENABLED:
        return
    _started = True
    for target, name in (
        (_collection_loop, "monitor-collections"),
        (_web_snapshot_loop, "monitor-web-snapshots"),
    ):
        threading.Thread(target=target, name=name, daemon=True).start()
