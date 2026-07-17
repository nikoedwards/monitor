"""Background scheduler: periodically run due collectors and web snapshots."""
from __future__ import annotations

import threading
import time

from .config import SCHEDULER_ENABLED, SCHEDULER_SECONDS
from .connectors.base import run_collector
from .connectors.registry import REGISTRY
from .db import db
from .util import today

_started = False


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
                    run_collector(conn, spec, brand)
            except Exception:
                # run_collector already records errors per source; keep loop alive.
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
            continue


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
            continue


def _loop() -> None:
    while True:
        try:
            _run_due_collections()
            _run_due_sales()
            _run_due_web_snapshots()
        except Exception:
            pass
        time.sleep(max(60, SCHEDULER_SECONDS))


def start_scheduler() -> None:
    global _started
    if _started or not SCHEDULER_ENABLED:
        return
    _started = True
    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
