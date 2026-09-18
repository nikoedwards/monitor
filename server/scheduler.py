"""Background scheduler: periodically run due collectors and web snapshots."""
from __future__ import annotations

import logging
import threading
import time
import logging

from .config import SCHEDULER_ENABLED, SCHEDULER_SECONDS, WEB_SCHEDULER_SECONDS
from .connectors.base import run_collector
from .connectors.registry import REGISTRY
from .db import db
from .util import today

_started = False
_start_lock = threading.Lock()
logger = logging.getLogger(__name__)


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
                logger.exception("Scheduled collection failed: source=%s brand=%s", spec.id, brand.get("id"))
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
            logger.exception("Scheduled sales collection failed: brand=%s", brand.get("id"))
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
            logger.exception("Scheduled web snapshot failed: monitor=%s", monitor.get("id"))
            continue


def _collection_loop() -> None:
    while True:
        started = time.monotonic()
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
            # Keep the daemon alive, but leave an actionable traceback in the
            # Railway logs instead of making a failed scheduler look healthy.
            logger.exception("Scheduler cycle failed")
        finally:
            logger.info("Scheduler cycle finished in %.1fs", time.monotonic() - started)
        time.sleep(max(60, SCHEDULER_SECONDS))


def start_scheduler() -> None:
    global _started
    if not SCHEDULER_ENABLED:
        logger.warning("Scheduler disabled by MONITOR_SCHEDULER=0")
        return
    with _start_lock:
        if _started:
            return
        _started = True
        try:
            thread = threading.Thread(target=_loop, name="monitor-scheduler", daemon=True)
            thread.start()
        except RuntimeError:
            # A thread creation failure should be visible; do not silently mark
            # the service as healthy while all scheduled collection is stopped.
            _started = False
            logger.exception("Unable to start scheduler thread")
