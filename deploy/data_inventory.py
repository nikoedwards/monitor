"""Inventory a quiesced data directory; verify a restored copy before app startup.

Only Python's standard library is required. Never import the application: its
startup runs migrations and collectors. Reports contain hashes/counts, not rows
or settings values. Keep reports and backups outside the public repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import struct
import tempfile


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def row_digest(row):
    digest = hashlib.sha256()
    for value in row:
        if value is None:
            tag, raw = b'n', b''
        elif isinstance(value, bytes):
            tag, raw = b'b', value
        elif isinstance(value, str):
            tag, raw = b's', value.encode('utf-8')
        elif isinstance(value, int):
            tag, raw = b'i', str(value).encode('ascii')
        elif isinstance(value, float):
            tag, raw = b'f', struct.pack('!d', value)
        else:
            raise TypeError(type(value).__name__)
        digest.update(tag + struct.pack('!Q', len(raw)) + raw)
    return digest.digest()


def database_inventory(source):
    # Reading WAL can create shared-memory files. Do this only on a disposable
    # copy, retaining the original DB and all sidecars byte-for-byte.
    with tempfile.TemporaryDirectory(prefix='monitor-db-audit-') as temp:
        copied = Path(temp) / source.name
        shutil.copyfile(source, copied)
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = Path(str(source) + suffix)
            if sidecar.exists():
                shutil.copyfile(sidecar, Path(str(copied) + suffix))
        conn = sqlite3.connect(copied)
        try:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            integrity = [row[0] for row in conn.execute('PRAGMA integrity_check')]
            if integrity != ['ok']:
                raise ValueError('SQLite integrity check failed: ' + source.name)
            schema = conn.execute(
                'SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name'
            ).fetchall()
            tables = {}
            for (table,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ):
                hashes = sorted(row_digest(row) for row in conn.execute(
                    'SELECT * FROM ' + quoted(table)
                ))
                tables[table] = {
                    'rows': len(hashes),
                    'sha256': hashlib.sha256(b''.join(hashes)).hexdigest(),
                }
            return {
                'integrity': 'ok',
                'schema_sha256': hashlib.sha256(b''.join(map(row_digest, schema))).hexdigest(),
                'user_version': conn.execute('PRAGMA user_version').fetchone()[0],
                'tables': tables,
                # Existing violations are recorded, not silently repaired.
                'foreign_key_violations': sorted(row_digest(row).hex() for row in
                    conn.execute('PRAGMA foreign_key_check')),
            }
        finally:
            conn.close()


def entries(root):
    result = {}
    def visit(directory):
        with os.scandir(directory) as scan:
            children = sorted(scan, key=lambda item: item.name)
        for item in children:
            p = Path(item.path)
            relative = p.relative_to(root).as_posix()
            info = p.lstat()
            record = {'mode': stat.S_IMODE(info.st_mode), 'mtime_ns': info.st_mtime_ns}
            if stat.S_ISLNK(info.st_mode):
                record.update(type='symlink', target=os.readlink(p))
            elif stat.S_ISDIR(info.st_mode):
                record.update(type='directory')
            elif stat.S_ISREG(info.st_mode):
                record.update(type='file', bytes=info.st_size)
            else:
                raise ValueError('Unsupported live/special file; stop its owner first: ' + relative)
            result[relative] = record
            if record['type'] == 'directory':
                visit(p)
    visit(root)
    return result


def inventory(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Data root must be a directory')
    before = entries(root)
    # This is a Monitor migration, not an empty-directory backup.
    if before.get('monitor.db', {}).get('type') != 'file':
        raise ValueError('monitor.db is missing; refusing the wrong or empty data directory')
    files, databases = {}, {}
    for name, meta in before.items():
        record = {key: value for key, value in meta.items() if key != 'mtime_ns'}
        if meta['type'] == 'file':
            p = root / name
            digest = hashlib.sha256()
            with p.open('rb') as stream:
                header = stream.read(16)
                digest.update(header)
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            record['sha256'] = digest.hexdigest()
            if header == b'SQLite format 3\x00':
                databases[name] = database_inventory(p)
        files[name] = record
    if entries(root) != before:
        raise ValueError('Data changed during inventory; stop all writers and retry')
    if 'monitor.db' not in databases:
        raise ValueError('monitor.db is not a valid SQLite database')
    return {
        'format': 1, 'files': files, 'databases': databases,
        'total_bytes': sum(x.get('bytes', 0) for x in files.values()),
        'file_count': sum(x['type'] == 'file' for x in files.values()),
    }


def compare(expected, actual):
    if expected.get('format') != 1 or actual.get('format') != 1:
        raise ValueError('Unsupported inventory format')
    differences = []
    for section in ('files', 'databases'):
        for name in sorted(expected[section].keys() | actual[section].keys()):
            if name not in actual[section]:
                differences.append(section + ': missing ' + name)
            elif name not in expected[section]:
                differences.append(section + ': extra ' + name)
            elif expected[section][name] != actual[section][name]:
                differences.append(section + ': changed ' + name)
    return differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('create', 'verify'))
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--writers-stopped', action='store_true', required=True,
                        help='Confirm application, collectors and external workers are stopped')
    args = parser.parse_args()
    if args.manifest.resolve().is_relative_to(args.data.resolve()):
        parser.error('Keep the manifest outside the data directory')
    result = inventory(args.data)
    if args.command == 'create':
        # Never overwrite the source manifest with a destination inventory.
        with args.manifest.open('x', encoding='utf-8') as output:
            json.dump(result, output, ensure_ascii=False, sort_keys=True, indent=2)
    else:
        expected = json.loads(args.manifest.read_text(encoding='utf-8'))
        differences = compare(expected, result)
        if differences:
            print(json.dumps({'ok': False, 'differences': differences}, ensure_ascii=False))
            return 1
    print(json.dumps({'ok': True, 'files': result['file_count'],
                      'bytes': result['total_bytes'], 'databases': len(result['databases'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
