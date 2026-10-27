"""Exercise deployment failure paths without Docker or a production database."""
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'aliyun_deploy', Path(__file__).resolve().parents[1] /
    'deploy/aliyun/autodeploy/deploy_monitor.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
REVISION = 'a' * 40


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.database = self.data / 'monitor.db'
        with sqlite3.connect(self.database) as conn:
            conn.execute('CREATE TABLE records (id INTEGER PRIMARY KEY)')
            conn.execute('INSERT INTO records VALUES (1)')
        self.override = self.root / 'release.yml'
        self.old_override = b'services:\n  monitor:\n    image: old-release\n'
        self.override.write_bytes(self.old_override)
        self.config = {'data_dir':str(self.data),'state_dir':str(self.root/'releases'),
                       'release_override':str(self.override),'compose_files':['base.yml'],
                       'container':'monitor','public_url':'http://localhost'}
        self.deployer = module.Deployer(self.config, REVISION)
        for method in ['load_image', 'preflight', 'cleanup', 'run', 'start']:
            replacement = patch.object(self.deployer, method)
            setattr(self, method, replacement.start())
            self.addCleanup(replacement.stop)
        self.image_patch = patch.object(self.deployer, 'current_image', return_value='old-release')
        self.image_patch.start()
        self.addCleanup(self.image_patch.stop)

    def ids(self):
        with sqlite3.connect(self.database) as conn:
            return [r[0] for r in conn.execute('SELECT id FROM records ORDER BY id')]

    def add_row(self, number):
        with sqlite3.connect(self.database) as conn:
            conn.execute('INSERT INTO records VALUES (?)', (number,))

    def test_failed_preflight_leaves_live_service_and_data_untouched(self):
        self.preflight.side_effect = RuntimeError('bad candidate')
        with patch.object(self.deployer, 'stop') as stop:
            with self.assertRaisesRegex(RuntimeError, 'bad candidate'):
                self.deployer.deploy(io.BytesIO())
        stop.assert_not_called()
        self.assertEqual(self.ids(), [1])
        self.assertEqual(self.override.read_bytes(), self.old_override)

    def test_failed_rollout_restores_backup_captured_after_old_collector_stops(self):
        # A row arrives after preflight; it must survive rollback.
        self.preflight.side_effect = lambda _: self.add_row(2)
        def wait(url, container):
            if self.start.call_count == 1:
                self.add_row(99)  # Represents a failed startup database migration.
                raise RuntimeError('new release unhealthy')
        with patch.object(self.deployer, 'stop'), patch.object(self.deployer, 'wait_ready', side_effect=wait):
            with self.assertRaisesRegex(RuntimeError, 'new release unhealthy'):
                self.deployer.deploy(io.BytesIO())
        self.assertEqual(self.ids(), [1, 2])
        self.assertEqual(self.override.read_bytes(), self.old_override)
        self.assertEqual(self.start.call_count, 2)
        self.assertTrue(list((self.root/'releases').glob('release-*/rejected-runtime/monitor.db')))

    def test_backup_failure_restarts_previous_release_without_restoring_partial_backup(self):
        with patch.object(self.deployer, 'stop'), patch.object(self.deployer, 'wait_ready'), \
                patch.object(module, 'backup_database', side_effect=RuntimeError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'disk full'):
                self.deployer.deploy(io.BytesIO())
        self.assertEqual(self.ids(), [1])
        self.assertEqual(self.override.read_bytes(), self.old_override)
        self.start.assert_called_once()

    def test_success_records_revision_and_keeps_persistent_database(self):
        with patch.object(self.deployer, 'stop'), patch.object(self.deployer, 'wait_ready'):
            result = self.deployer.deploy(io.BytesIO())
        self.assertEqual(result['revision'], REVISION)
        self.assertIn('monitor-deploy:' + REVISION, self.override.read_text())
        self.assertEqual(self.ids(), [1])
        self.assertTrue(Path(result['databaseBackup']).is_file())
        state = json.loads((self.root/'releases/current.json').read_text())
        self.assertEqual(state['previousImage'], 'old-release')

    def test_invalid_revision_is_rejected_before_any_command(self):
        for value in ['main', '../data', 'a'*39, 'a'*40+';true', 'A'*40]:
            with self.assertRaises(ValueError):
                module.Deployer(self.config, value)


if __name__ == '__main__':
    unittest.main()
