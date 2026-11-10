"""Disposable browser collector; stdout is a single JSON response."""
from __future__ import annotations
import json
import os
import sys
from .worker_support import _attach_windows_job, _prefer_worker_oom_kill


def main() -> int:
    _attach_windows_job()
    _prefer_worker_oom_kill()
    if os.name == 'posix':
        os.nice(10)
    request = json.load(sys.stdin)
    payload = request['payload']
    try:
        if request['kind'] == 'meta_ads':
            from .connectors.collectors import _collect_meta_public_ads_in_process
            result = _collect_meta_public_ads_in_process(
                payload, constrained=request['constrained'], timeout=max(1, request['timeout'] - 8))
        elif request['kind'] == 'instagram':
            from .connectors.public_social import _instagram_browser_result
            result = _instagram_browser_result(payload['handle'], payload['limit'],
                                               constrained=request['constrained'])
        else:
            raise ValueError('Unknown browser collector')
    except Exception as exc:
        result = {'error': str(exc)[:400]}
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
