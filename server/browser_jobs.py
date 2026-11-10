"""Bound browser collectors without letting them starve the API or the host."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .config import ROOT
from .fetchers import FetchError

logger = logging.getLogger(__name__)
_SLOT = threading.Lock()
_COOLDOWN: dict[str, float] = {}


def _setting(name: str, default: int) -> int:
    return max(1, int(os.environ.get(name, str(default))))


def available_memory_mb() -> int | None:
    """Consider both host availability and reclaimable container headroom."""
    values = []
    try:
        info = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        values.append(int(info['MemAvailable'].split()[0]) // 1024)
    except (OSError, ValueError, KeyError):
        pass
    # Docker cgroup v2 normally exposes its own cgroup as the mount root.
    try:
        root = Path('/sys/fs/cgroup')
        limit = (root / 'memory.max').read_text().strip()
        if limit != 'max':
            current = int((root / 'memory.current').read_text())
            stats = dict(line.split() for line in (root / 'memory.stat').read_text().splitlines())
            reclaimable = int(stats.get('inactive_file', 0))
            values.append(max(0, int(limit) - current + reclaimable) // 1048576)
    except (OSError, ValueError):
        pass
    return min(values) if values else None


def process_group_rss_mb(pid: int) -> int:
    """Count worker, Playwright driver and Chromium, including reparented children."""
    total = 0
    for entry in Path('/proc').glob('[0-9]*/stat'):
        try:
            # comm may contain spaces or parentheses; fields after its last ')'
            # start at field 3. Field 5 is pgrp and field 24 is RSS in pages.
            fields = entry.read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pid:
                total += int(fields[21]) * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError, IndexError):
            continue
    return total // 1048576


def kill_process_tree(process: subprocess.Popen) -> None:
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=5)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def run_browser_job(kind: str, payload: dict) -> dict:
    """One job, a hard wall-clock deadline, resource guard and failure backoff."""
    if kind not in {'meta_ads', 'instagram'}:
        raise ValueError('Unknown browser collector')
    if not _SLOT.acquire(blocking=False):
        raise FetchError('Browser collector busy; retry later')
    started = time.monotonic()
    process = None
    peak = 0
    cooling_down = _COOLDOWN.get(kind, 0) > started
    try:
        if cooling_down:
            raise FetchError('Browser collector cooling down after a resource failure; retry later')
        available = available_memory_mb()
        minimum = _setting('MONITOR_COLLECTOR_MIN_AVAILABLE_MB', 384)
        if available is not None and available < minimum:
            raise FetchError(f'Browser collector deferred: available memory {available} MiB < {minimum} MiB')
        timeout = _setting('MONITOR_COLLECTOR_TIMEOUT_SECONDS', 90)
        budget = _setting('MONITOR_COLLECTOR_MAX_RSS_MB', 320)
        reserve = _setting('MONITOR_COLLECTOR_RESERVE_MB', 160)
        request = json.dumps({'kind': kind, 'payload': payload, 'timeout': timeout,
                              'constrained': available is not None and available < 1024})
        logger.warning('Browser collector start kind=%s available_mb=%s timeout=%s', kind, available, timeout)
        process = subprocess.Popen([sys.executable, '-m', 'server.collector_worker'], cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=os.name != 'nt')
        first = True
        deadline = time.monotonic() + timeout
        while True:
            reason = ''
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = f'Browser collector exceeded {timeout}s total deadline'
            else:
                available = available_memory_mb()
                if os.name == 'posix':
                    peak = max(peak, process_group_rss_mb(process.pid))
                if available is not None and available < reserve:
                    reason = f'Browser collector stopped: memory reserve below {reserve} MiB'
                elif peak > budget:
                    reason = f'Browser collector stopped: process group RSS {peak} MiB > {budget} MiB'
            if reason:
                raise FetchError(reason)
            try:
                stdout, stderr = process.communicate(request.encode() if first else None,
                                                      timeout=min(0.5, remaining))
                break
            except subprocess.TimeoutExpired:
                first = False
        if process.returncode:
            raise FetchError(f'Browser collector worker exited with code {process.returncode}')
        try:
            result = json.loads(stdout)
        except (ValueError, TypeError) as exc:
            raise FetchError('Browser collector returned invalid output') from exc
        if not isinstance(result, dict):
            raise FetchError('Browser collector returned invalid output')
        if result.get('error'):
            raise FetchError(str(result['error'])[:400])
        return result
    except FetchError:
        if not cooling_down:
            _COOLDOWN[kind] = time.monotonic() + _setting('MONITOR_COLLECTOR_COOLDOWN_SECONDS', 1800)
        raise
    finally:
        if process is not None:
            # Also run on success: children can outlive an exited worker.
            kill_process_tree(process)
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                logger.error('Browser collector worker did not reap promptly kind=%s pid=%s', kind, process.pid)
            logger.warning('Browser collector finish kind=%s pid=%s elapsed=%.1fs peak_group_rss_mb=%s exit=%s',
                           kind, process.pid, time.monotonic() - started, peak, process.returncode)
        _SLOT.release()


def chromium_args(constrained: bool) -> list[str]:
    args = ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
            '--disable-background-networking', '--disable-extensions', '--disable-sync',
            '--no-first-run', '--renderer-process-limit=1', '--autoplay-policy=user-gesture-required']
    if constrained:
        args.append('--js-flags=--max-old-space-size=96')
    return args


def restrict_resources(context) -> None:
    """Keep DOM, scripts, CSS and API responses; do not download ad media."""
    requests = 0

    def route_request(route):
        nonlocal requests
        requests += 1
        request = route.request
        if request.resource_type not in {'document', 'script', 'stylesheet', 'xhr', 'fetch'} or requests > 256:
            route.abort()
        else:
            route.continue_()

    context.route('**/*', route_request)
