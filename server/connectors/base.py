"""Connector specification and the shared collection runner."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import has_credential
from ..records import insert_record_if_new
from ..util import utc_now

# A collector takes a db connection + a brand dict and returns record payloads.
CollectFn = Callable[[sqlite3.Connection, dict], list[dict]]


@dataclass
class ConnectorSpec:
    id: str
    name: str
    category: str            # sales | media | social | ads | creators | community | voc | web | manual
    dimension: str           # sales | marketing | voc | web | platform
    tier: int                # 1 real now, 2 needs-credential, 3 paid/seam
    vendor: str = ""
    sync_mode: str = "scheduled"   # scheduled | manual | realtime
    cadence: str = "daily"
    credential_key: str = ""
    notes: str = ""
    collect: Optional[CollectFn] = field(default=None, repr=False)

    @property
    def needs_credentials(self) -> bool:
        return bool(self.credential_key)

    @property
    def status(self) -> str:
        if self.needs_credentials and not has_credential(self.credential_key):
            return "needs_credential"
        if self.tier >= 3 and self.collect is None:
            return "planned"
        if self.collect is None and self.category != "web":
            return "planned"
        return "ready"


def run_collector(conn: sqlite3.Connection, spec: ConnectorSpec, brand: dict) -> dict:
    """Execute a connector for a brand, persist new records, update source stats."""
    result = {"source_id": spec.id, "created": 0, "status": "ok", "error": ""}
    if spec.collect is None:
        result["status"] = "skipped"
        result["error"] = "Connector has no automated collector"
        _update_source_stats(conn, spec.id, status="skipped", error=result["error"], added=0)
        _update_brand_run(conn, spec.id, brand.get("id"), status="skipped", error=result["error"], added=0)
        return result
    if spec.needs_credentials and not has_credential(spec.credential_key):
        result["status"] = "needs_credential"
        result["error"] = f"Missing credential: {spec.credential_key}"
        _update_source_stats(conn, spec.id, status="needs_credential", error=result["error"], added=0)
        _update_brand_run(conn, spec.id, brand.get("id"), status="needs_credential", error=result["error"], added=0)
        return result
    try:
        payloads = spec.collect(conn, brand) or []
        created = 0
        for payload in payloads:
            payload.setdefault("source_id", spec.id)
            if insert_record_if_new(conn, payload) is not None:
                created += 1
        result["created"] = created
        _update_source_stats(conn, spec.id, status="ok", error="", added=created)
        _update_brand_run(conn, spec.id, brand.get("id"), status="ok", error="", added=created)
    except Exception as exc:  # surface, do not silently swallow
        result["status"] = "error"
        result["error"] = str(exc)[:500]
        _update_source_stats(conn, spec.id, status="error", error=result["error"], added=0)
        _update_brand_run(conn, spec.id, brand.get("id"), status="error", error=result["error"], added=0)
    return result


def _update_source_stats(conn: sqlite3.Connection, source_id: str, *, status: str, error: str, added: int) -> None:
    conn.execute(
        """
        UPDATE sources
        SET last_collect_at = ?, last_status = ?, last_error = ?,
            item_count = item_count + ?
        WHERE id = ?
        """,
        (utc_now(), status, error, added, source_id),
    )


def _update_brand_run(conn: sqlite3.Connection, source_id: str, brand_id: str | None, *, status: str, error: str, added: int) -> None:
    if not brand_id:
        return
    conn.execute(
        """
        INSERT INTO source_brand_runs (source_id, brand_id, last_collect_at, last_status, last_error, item_count)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, brand_id) DO UPDATE SET
            last_collect_at = excluded.last_collect_at,
            last_status = excluded.last_status,
            last_error = excluded.last_error,
            item_count = source_brand_runs.item_count + excluded.item_count
        """,
        (source_id, brand_id, utc_now(), status, error, added),
    )
