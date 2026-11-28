import json
import os
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from server.db import db
from server.domains.web import _period_stats, _query_snapshots

start, end = '2026-10-30', '2026-11-28'
candidates = []
with db() as conn:
    for brand, in conn.execute("select distinct brand_id from web_monitors where brand_id is not null"):
        rows = _query_snapshots(conn, start, end, brand)
        scopes = [(None, rows)] + [(m, [r for r in rows if r['monitor_id'] == m]) for m in sorted({r['monitor_id'] for r in rows})]
        for monitor, records in scopes:
            stats = _period_stats(records, start, end)
            page = next((p for p in stats['page_activity'] if p['page']=='/'), {})
            if not page or stats['changed_days'] < 1:
                continue
            distance = (abs(stats['changed_days'] - 10) + abs(page['changed']-10),
                        abs(round(stats['average_severity']*100)-49), abs(page['captures']-16), monitor is not None)
            candidates.append((distance, brand, monitor, stats['changed_days'], page['captures']))
if not candidates:
    raise SystemExit('No matching snapshots')
distance, brand, monitor, changes, captures = min(candidates, key=lambda item:item[0])
print('matching screenshot', distance, 'changed days', changes, 'captures', captures, flush=True)
if distance[0] > 2 or distance[1] > 6 or distance[2] > 3:
    raise SystemExit('No close match')
params = dict(brand_id=brand, start_date=start, end_date=end, background='true')
if monitor:
    params['monitor_id'] = monitor
def request(**kw):
    url = 'http://127.0.0.1:8790/api/web/analyze?' + urlencode({**params, **kw})
    req = Request(url, method='POST')
    if code := os.getenv('MONITOR_ACCESS_CODE'):
        req.add_unredirected_header('Authorization', 'Bearer '+code)
    with urlopen(req,timeout=25) as res:
        return json.load(res)
t=time.monotonic()
before = request(cached_only='true').get('analysis_id')
result=request(refresh='true')
while result.get('generation',{}).get('status')=='running' and time.monotonic()-t<430:
    time.sleep(3)
    result=request(cached_only='true')
status=result.get('generation',{}).get('status')
print('analysis result',status,'seconds',round(time.monotonic()-t),'summary_chars',len(result.get('summary','')), 'has_model',bool(result.get('model') and result.get('model')!='deterministic'),flush=True)
if not result.get('summary') or status in ('running','failed') or result.get('model') in (None,'deterministic'):
    raise SystemExit(1)
assert result['analysis_id'] and result['analysis_id'] != before
assert request(cached_only='true').get('analysis_id') == result['analysis_id']
with db() as conn:
    saved = conn.execute("SELECT result_json FROM web_snapshot_analyses WHERE id=?", (result['analysis_id'],)).fetchone()
    assert saved and json.loads(saved[0])['summary'] == result['summary']
print('new durable model report', 'scope', 'monitor' if monitor else 'all pages',
      'highlights', len(result.get('highlights',[])), 'events', len(result.get('major_events',[])))
print('cached report confirmed')
