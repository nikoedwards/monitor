"""Print safe failure classifications only; do not export log bodies or snapshot data."""
import re
import sys
from collections import Counter
counts = Counter()
frames = []
for line in sys.stdin:
    if 'Background analysis failed' in line:
        counts['analysis failure'] += 1
    if match := re.search(r'/app/server/(\w+(?:/\w+)*\.py)", line (\d+), in (\w+)', line):
        frames.append(match.groups())
    if match := re.search(r'outcome=(http_\d{3}|received|\w*Error) request_bytes=(\d+) headers_ms=([\d.]+|None) elapsed_ms=([\d.]+)', line):
        print('model call', *match.groups())
    for marker in ('invalid_request_error','image','timed out','Expecting delimiter','Expecting value','JSONDecodeError','KeyError','LlmError','HTTPException','IntegrityError','database is locked','Unauthorized','too large','tokens','Unsupported','429','502','DecompressionBomb','MemoryError'):
        if marker in line and (line.startswith(('server.', 'sqlite3.', 'fastapi.', 'PIL.', 'json.', 'KeyError:', 'TimeoutError:', 'MemoryError:')) or 'outcome=' in line):
            counts[marker] += 1
print('error types:', sorted(counts.items()))
print('frames:', frames[-45:])
