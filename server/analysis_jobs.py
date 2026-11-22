"""Bounded background analysis jobs, polled independently of HTTP requests.

Workers own their SQLite connection; request connections never escape their
lifetime. Successful analyses are persisted by the existing analysis helpers.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable

from fastapi import HTTPException

from .db import ResilientConnection

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analysis")
_LOCK = threading.Lock()
_JOBS: dict[tuple[str, str], dict] = {}
_TTL = 24 * 60 * 60
logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("uvicorn.error.monitor.analysis_jobs")


def analysis_job(conn: sqlite3.Connection, key: str, generate: Callable[[sqlite3.Connection], dict] | None = None,
                 *, start: bool = True, refresh: bool = False, revision: str | None = None) -> dict:
    """Follow a stable request key, using revisions only to invalidate finished results."""
    path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not path:
        raise HTTPException(status_code=400, detail="后台分析需要文件数据库。")
    job_key = (path, key)
    with _LOCK:
        now = time.monotonic()
        for old_key, job in list(_JOBS.items()):
            if job["status"] != "running" and now - job["updated"] > _TTL:
                del _JOBS[old_key]
        job = _JOBS.get(job_key)
        if job and job["status"] != "running" and revision is not None and job["revision"] != revision:
            job = None
        if start and generate is not None and (job is None or (refresh and job["status"] != "running")):
            if sum(item["status"] == "running" for item in _JOBS.values()) >= 16:
                raise HTTPException(status_code=503, detail="分析任务较多，请稍后重试。")
            job = {"status": "running", "updated": now, "revision": revision}
            _JOBS[job_key] = job
            _EXECUTOR.submit(_generate, path, key.partition(":")[0], job, generate)
        if job is None:
            return {}
        return {**job.get("result", {}), "generation": {
            "status": job["status"], **({"error": job["error"]} if job.get("error") else {}),
        }}


def _generate(path: str, kind: str, job: dict, generate: Callable[[sqlite3.Connection], dict]) -> None:
    started = time.monotonic()
    queue_ms = (started - job["updated"]) * 1000
    try:
        conn = sqlite3.connect(path, timeout=15, isolation_level=None, factory=ResilientConnection)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            result = generate(conn)
        finally:
            conn.close()
        with _LOCK:
            job.update(status="completed", result=result, updated=time.monotonic())
    except Exception:
        logger.exception("Background analysis failed")
        with _LOCK:
            job.update(status="failed", error="分析生成失败，请重试；已有结果仍可查看。", updated=time.monotonic())
    finally:
        timing_logger.info("AI background task kind=%s status=%s queue_ms=%.1f run_ms=%.1f",
                           kind, job["status"], queue_ms, (time.monotonic() - started) * 1000)
