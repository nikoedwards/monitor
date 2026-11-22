"""Bounded production screenshot diagnostics; never changes monitor records."""
from __future__ import annotations

import collections
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid
from urllib.request import urlopen


def emit(event, **values):
    print(json.dumps(dict(event=event, **values), ensure_ascii=True), flush=True)


def resources():
    readings = {}
    for name in ('memory.current', 'memory.max', 'memory.events', 'memory.stat', 'pids.current', 'pids.max', 'pids.events', 'cpu.stat', 'cpu.pressure', 'memory.pressure', 'io.pressure'):
        path = Path('/sys/fs/cgroup') / name
        if path.exists():
            readings[name] = path.read_text().strip()
    processes = collections.Counter()
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            try:
                processes[(entry / 'comm').read_text().strip()] += 1
            except OSError:
                pass
    emit('resources', cgroup=readings, processes=dict(processes))


def capture(url, attempt):
    from server.fetchers import fetch_page
    from server.snapshot import capture_artifacts
    from server.config import SNAPSHOT_DIR
    from PIL import Image
    if os.environ.get('MONITOR_DIAGNOSTIC_LIVE_ROOT'):
        import server.snapshot as snapshot
        SNAPSHOT_DIR = Path(os.environ['MONITOR_DIAGNOSTIC_LIVE_ROOT']) / 'data/snapshots'
        snapshot.SNAPSHOT_DIR = SNAPSHOT_DIR

    start = time.monotonic()
    prefetch_error = ''
    try:
        page = fetch_page(url)
    except Exception as exc:
        page = {}
        prefetch_error = str(exc)[:500]
    emit('prefetch', url=url, elapsed=round(time.monotonic()-start, 2), error=prefetch_error,
         html_bytes=len(page.get('html', '')))
    basename = 'diagnostic-' + uuid.uuid4().hex[:12]
    png, archive, meta = capture_artifacts(
        url, page.get('title', ''), page.get('text', ''), page.get('html', ''),
        basename, prefetch_error=prefetch_error,
    )
    meta.pop('page', None)
    with Image.open(SNAPSHOT_DIR / png) as image:
        image.load()
        size = image.size
        extrema = image.convert('RGB').getextrema()
    emit('capture', url=url, attempt=attempt, elapsed=round(time.monotonic()-start, 2),
         png=png, archive=archive, size=size, extrema=extrema,
         sha256=hashlib.sha256((SNAPSHOT_DIR / png).read_bytes()).hexdigest(), meta=meta)


def main():
    from server.config import DB_PATH
    if os.environ.get('MONITOR_DIAGNOSTIC_LIVE_ROOT'):
        DB_PATH = Path(os.environ['MONITOR_DIAGNOSTIC_LIVE_ROOT']) / 'data/monitor.db'

    mode = sys.argv[1] if len(sys.argv) > 1 else 'resources'
    attempts = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    if mode not in {'resources', 'capture'} or attempts not in {1, 2, 3}:
        raise ValueError('Invalid diagnostic arguments')
    resources()
    with sqlite3.connect(DB_PATH.resolve().as_uri()+'?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        monitors = [dict(r) for r in conn.execute('SELECT url, last_status, last_error, snapshot_retry_count, last_snapshot_at FROM web_monitors')]
        errors = collections.Counter()
        for row in conn.execute('SELECT raw_json FROM web_snapshots ORDER BY created_at DESC LIMIT 200'):
            meta = json.loads(row[0] or '{}')
            if meta.get('error'):
                errors[meta['error'][:350]] += 1
    emit('history', monitors=monitors, errors=dict(errors))
    if mode == 'resources':
        return
    urls = list(dict.fromkeys(m['url'] for m in monitors))[:3]
    failures = 0
    for attempt in range(1, attempts+1):
        for url in urls:
            emit('starting_capture', url=url, attempt=attempt)
            health_errors = []
            peak_pids = peak_memory = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(capture, url, attempt)
                while not future.done():
                    peak_pids = max(peak_pids, int(Path('/sys/fs/cgroup/pids.current').read_text()))
                    peak_memory = max(peak_memory, int(Path('/sys/fs/cgroup/memory.current').read_text()))
                    try:
                        with urlopen('http://127.0.0.1:8790/api/health', timeout=3) as r:
                            assert json.load(r)['ok']
                    except Exception as exc:
                        health_errors.append(str(exc))
                    time.sleep(1)
                try:
                    future.result()
                except Exception as exc:
                    failures += 1
                    emit('capture_failed', url=url, attempt=attempt, error=str(exc))
            emit('api_health', url=url, errors=health_errors, peak_pids=peak_pids,
                 peak_memory_mib=round(peak_memory/1024**2, 2))
            resources()
    emit('summary', attempts=attempts*len(urls), failures=failures)
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
