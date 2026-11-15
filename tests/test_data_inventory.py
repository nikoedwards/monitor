"""Migration must detect lost files/rows and include committed WAL data."""
import importlib.util
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    'data_inventory', Path(__file__).resolve().parents[1] / 'deploy' / 'data_inventory.py'
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.root.mkdir()
        conn = sqlite3.connect(self.root / 'monitor.db')
        conn.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, body TEXT, secret BLOB)')
        conn.execute('INSERT INTO records VALUES (1, ?, ?)', ('history', b'private-value'))
        conn.commit()
        conn.close()
        (self.root / 'snapshots').mkdir()
        (self.root / 'snapshots' / 'old.html').write_text('<html>old</html>')
        (self.root / 'browser_profiles').mkdir()
        (self.root / 'browser_profiles' / '.hidden').write_bytes(b'profile-state')

    def test_full_copy_including_hidden_files(self):
        manifest = audit.inventory(self.root)
        dest = self.root.parent / 'dest'
        shutil.copytree(self.root, dest)
        self.assertEqual([], audit.compare(manifest, audit.inventory(dest)))
        self.assertIn('browser_profiles/.hidden', manifest['files'])
        self.assertNotIn('private-value', str(manifest))

    def test_missing_archive_is_detected(self):
        manifest = audit.inventory(self.root)
        (self.root / 'snapshots' / 'old.html').unlink()
        self.assertIn('files: missing snapshots/old.html', audit.compare(manifest, audit.inventory(self.root)))

    def test_same_size_corruption_is_detected(self):
        manifest = audit.inventory(self.root)
        (self.root / 'snapshots' / 'old.html').write_text('<html>new</html>')
        self.assertIn('files: changed snapshots/old.html', audit.compare(manifest, audit.inventory(self.root)))

    def test_same_row_count_changed_content_is_detected(self):
        manifest = audit.inventory(self.root)
        conn = sqlite3.connect(self.root / 'monitor.db')
        conn.execute("UPDATE records SET body='changed' WHERE id=1")
        conn.commit()
        conn.close()
        self.assertIn('databases: changed monitor.db', audit.compare(manifest, audit.inventory(self.root)))

    def test_wal_commits_are_audited_without_modifying_source(self):
        conn = sqlite3.connect(self.root / 'monitor.db')
        self.addCleanup(conn.close)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA wal_autocheckpoint=0')
        conn.execute("INSERT INTO records VALUES (2,'committed-in-wal',NULL)")
        conn.commit()
        before = {p.name: p.read_bytes() for p in self.root.glob('monitor.db*')}
        manifest = audit.inventory(self.root)
        self.assertEqual(2, manifest['databases']['monitor.db']['tables']['records']['rows'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.glob('monitor.db*')})
        dest = self.root.parent / 'dest'
        shutil.copytree(self.root, dest)
        (dest / 'monitor.db-wal').unlink()
        (dest / 'monitor.db-shm').unlink(missing_ok=True)
        differences = audit.compare(manifest, audit.inventory(dest))
        self.assertIn('databases: changed monitor.db', differences)

    def test_wrong_empty_root_fails_closed(self):
        empty = self.root.parent / 'empty'
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, 'monitor.db is missing'):
            audit.inventory(empty)

    def test_corrupt_database_fails_closed(self):
        (self.root / 'monitor.db').write_bytes(b'not-a-database')
        with self.assertRaisesRegex(ValueError, 'not a valid SQLite'):
            audit.inventory(self.root)

    def test_sqlite_metadata_and_duplicate_rows_are_preserved(self):
        conn = sqlite3.connect(self.root / 'monitor.db')
        conn.execute('CREATE TABLE duplicates (value TEXT)')
        conn.executemany('INSERT INTO duplicates VALUES (?)', [('same',), ('same',)])
        conn.execute('PRAGMA user_version=7')
        conn.commit()
        conn.close()
        manifest = audit.inventory(self.root)
        self.assertEqual(7, manifest['databases']['monitor.db']['user_version'])
        self.assertEqual(2, manifest['databases']['monitor.db']['tables']['duplicates']['rows'])


if __name__ == '__main__':
    unittest.main()
