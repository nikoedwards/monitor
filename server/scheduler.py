"""Background scheduler: periodically run due collectors and web snapshots."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import (
    SCHEDULER_ENABLED,
    SCHEDULER_SECONDS,
    SCHEDULER_STARTUP_DELAY_SECONDS,
    WEB_SCHEDULER_SECONDS,
)
from .connectors.base import run_collector
from .connectors.registry import REGISTRY
from .db import db
from .snapshot import SnapshotCaptureBusy
from .util import today

_started = False
logger = logging.getLogger(__name__)
# All scheduler-owned work shares one slot. This covers browser-backed
# collectors as well as web snapshots on a 1 GiB host.
SCHEDULER_WORK_SLOT = threading.BoundedSemaphore(1)


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
                    if not SCHEDULER_WORK_SLOT.acquire(blocking=False):
                        continue
                    try:
                        run_collector(conn, spec, brand)
                    finally:
                        SCHEDULER_WORK_SLOT.release()
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
            if not SCHEDULER_WORK_SLOT.acquire(blocking=False):
                continue
            with db() as conn:
                try:
                    run_sales_collection(conn, brand)
                finally:
                    SCHEDULER_WORK_SLOT.release()
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
                      AND lower(COALESCE(platform, '')) != 'boss'
                      AND lower(COALESCE(url, '')) NOT LIKE '%zhipin.com%'
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
                if not SCHEDULER_WORK_SLOT.acquire(blocking=False):
                    continue
                with db() as conn:
                    try:
                        run_hiring_collection(conn, brand)
                    finally:
                        SCHEDULER_WORK_SLOT.release()
            except Exception:
                logger.exception("Hiring collection failed for %s", brand.get("id"))
        if "linkedin_people" in platforms or due_profile:
            try:
                if not SCHEDULER_WORK_SLOT.acquire(blocking=False):
                    continue
                with db() as conn:
                    try:
                        run_linkedin_people_collection(conn, brand)
                    finally:
                        SCHEDULER_WORK_SLOT.release()
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
                    if not SCHEDULER_WORK_SLOT.acquire(blocking=False):
                        continue
                    try:
                        capture_monitor(conn, monitor)
                    finally:
                        SCHEDULER_WORK_SLOT.release()
                    # Drain at most one overdue browser job per cycle. The
                    # next poll gives the API and host time to recover.
                    break
                elif monitor_is_due(monitor, "check", now):
                    check_monitor(conn, monitor)
        except SnapshotCaptureBusy:
            logger.info("Skipping web snapshot while another capture is active for %s", monitor.get("id"))
            continue
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
    def delayed_target(target, name):
        if SCHEDULER_STARTUP_DELAY_SECONDS > 0:
            logger.warning(
                "Scheduler %s held for %ss after startup",
                name,
                SCHEDULER_STARTUP_DELAY_SECONDS,
            )
            time.sleep(SCHEDULER_STARTUP_DELAY_SECONDS)
        target()

    for target, name in (
        (_collection_loop, "monitor-collections"),
        (_web_snapshot_loop, "monitor-web-snapshots"),
    ):
        threading.Thread(
            target=delayed_target, args=(target, name), name=name, daemon=True,
        ).start()
