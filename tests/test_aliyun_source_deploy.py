"""Exercise source-build deployment rollback paths without Docker."""

import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "aliyun_source_deploy",
    Path(__file__).resolve().parents[1]
    / "deploy/aliyun/autodeploy/deploy_source.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
REVISION = "b" * 40


class SourceDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.project = self.root / "project"
        self.data = self.project / "data"
        self.state = self.project / ".deploy-state"
        self.source.mkdir()
        self.data.mkdir(parents=True)
        self.override = self.project / "deploy/aliyun/server.yml"
        self.override.parent.mkdir(parents=True)
        self.override.write_text("services:\n  monitor:\n    image: stable\n")
        self.old_base = b"services:\n  monitor:\n    image: old\n"
        self.new_base = b"services:\n  monitor:\n    image: new\n"
        (self.project / "docker-compose.yml").write_bytes(self.old_base)
        (self.source / "docker-compose.yml").write_bytes(self.new_base)
        self.database = self.data / "monitor.db"
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            with connection:
                connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY)")
                connection.execute("INSERT INTO records VALUES (1)")

        self.deployer = module.SourceDeployer(
            REVISION,
            self.source,
            self.project,
            self.data,
            self.state,
            self.override,
            "monitor-intelligence-hub:stable",
            "http://127.0.0.1:8790",
        )
        patches = {
            "validate_inputs": patch.object(self.deployer, "validate_inputs"),
            "build_image": patch.object(self.deployer, "build_image"),
            "ensure_deployment_capacity": patch.object(
                self.deployer, "ensure_deployment_capacity"
            ),
            "image_id": patch.object(
                self.deployer, "image_id", return_value="sha256:new"
            ),
            "current_image_id": patch.object(
                self.deployer, "current_image_id", return_value="sha256:old"
            ),
            "validate_live_data_mount": patch.object(
                self.deployer, "validate_live_data_mount"
            ),
            "tag": patch.object(self.deployer, "tag"),
            "stop": patch.object(self.deployer, "stop"),
            "force_remove": patch.object(self.deployer, "force_remove"),
            "start": patch.object(self.deployer, "start"),
            "wait_ready": patch.object(self.deployer, "wait_ready"),
            "discard_candidate": patch.object(self.deployer, "discard_candidate"),
            "cleanup": patch.object(self.deployer, "cleanup"),
        }
        for name, patcher in patches.items():
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def ids(self):
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            return [row[0] for row in connection.execute("SELECT id FROM records")]

    def add_row(self, value):
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            with connection:
                connection.execute("INSERT INTO records VALUES (?)", (value,))

    def test_success_installs_compose_and_records_database_backup(self):
        result = self.deployer.deploy()

        self.assertEqual((self.project / "docker-compose.yml").read_bytes(), self.new_base)
        self.assertEqual(self.ids(), [1])
        self.assertTrue(Path(result["databaseBackup"]).is_file())
        self.assertEqual(self.start.call_count, 1)
        self.assertEqual(self.validate_live_data_mount.call_count, 2)
        self.wait_ready.assert_called_once_with(
            "http://127.0.0.1:8790", "sha256:new"
        )
        state = json.loads((self.state / "current.json").read_text())
        self.assertEqual(state["revision"], REVISION)

    def test_build_failure_never_stops_the_live_service(self):
        self.build_image.side_effect = RuntimeError("build failed")

        with self.assertRaisesRegex(RuntimeError, "build failed"):
            self.deployer.deploy()

        self.stop.assert_not_called()
        self.start.assert_not_called()
        self.discard_candidate.assert_not_called()
        self.assertEqual((self.project / "docker-compose.yml").read_bytes(), self.old_base)

    def test_low_disk_after_build_discards_candidate_without_stopping_service(self):
        self.ensure_deployment_capacity.side_effect = RuntimeError("not enough space")

        with self.assertRaisesRegex(RuntimeError, "not enough space"):
            self.deployer.deploy()

        self.build_image.assert_called_once_with()
        self.stop.assert_not_called()
        self.start.assert_not_called()
        self.discard_candidate.assert_called_once_with()
        self.assertEqual((self.project / "docker-compose.yml").read_bytes(), self.old_base)
        self.assertFalse(list(self.state.glob("release-*")))

    def test_pre_stop_validation_failure_discards_candidate(self):
        self.validate_live_data_mount.side_effect = RuntimeError("mount mismatch")

        with self.assertRaisesRegex(RuntimeError, "mount mismatch"):
            self.deployer.deploy()

        self.stop.assert_not_called()
        self.start.assert_not_called()
        self.discard_candidate.assert_called_once_with()
        self.assertFalse(list(self.state.glob("release-*")))

    def test_capacity_reserves_backup_size_plus_three_gib_for_restart(self):
        self.state.mkdir()
        wal = Path(str(self.database) + "-wal")
        shm = Path(str(self.database) + "-shm")
        wal.write_bytes(b"w" * 31)
        shm.write_bytes(b"s" * 17)
        database_bytes = sum(path.stat().st_size for path in (self.database, wal, shm))
        required = database_bytes + module.MIN_RESTART_HEADROOM

        with patch.object(
            module.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=required - 1),
        ) as disk_usage:
            with self.assertRaisesRegex(RuntimeError, "reserved for restart"):
                module.SourceDeployer.ensure_deployment_capacity(self.deployer)
        disk_usage.assert_called_once_with(self.deployer.state_dir)

        with patch.object(
            module.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=required),
        ):
            module.SourceDeployer.ensure_deployment_capacity(self.deployer)

    def test_split_filesystems_are_rejected_before_stopping_service(self):
        self.state.mkdir()
        with patch.object(
            self.deployer, "deployment_storage_is_shared", return_value=False
        ), patch.object(module.shutil, "disk_usage") as disk_usage:
            with self.assertRaisesRegex(RuntimeError, "same filesystem"):
                module.SourceDeployer.ensure_deployment_capacity(self.deployer)
        disk_usage.assert_not_called()

    def test_database_disappearing_after_build_aborts_before_stop(self):
        def check_capacity():
            module.SourceDeployer.ensure_deployment_capacity(self.deployer)

        self.ensure_deployment_capacity.side_effect = check_capacity
        self.database.unlink()

        with self.assertRaisesRegex(RuntimeError, "Database disappeared"):
            self.deployer.deploy()

        self.stop.assert_not_called()
        self.start.assert_not_called()
        self.discard_candidate.assert_called_once_with()
        self.assertFalse(list(self.state.glob("release-*")))

    def test_public_check_must_serve_the_same_assets_as_local_release(self):
        self.deployer.public_url = "http://production.example"
        local_assets = ("/assets/index-new.js", "/assets/index-new.css")
        self.wait_ready.side_effect = [local_assets, local_assets]

        self.deployer.deploy()

        self.assertEqual(self.wait_ready.call_count, 2)
        self.wait_ready.assert_any_call(
            "http://production.example",
            "sha256:new",
            expected_assets=local_assets,
        )

    def test_failed_health_restores_database_compose_and_image(self):
        def wait_for_release(_url, expected_image):
            if expected_image == "sha256:new":
                self.add_row(99)
                raise RuntimeError("new release unhealthy")

        self.wait_ready.side_effect = wait_for_release

        with self.assertRaisesRegex(RuntimeError, "new release unhealthy"):
            self.deployer.deploy()

        self.assertEqual(self.ids(), [1])
        self.assertEqual((self.project / "docker-compose.yml").read_bytes(), self.old_base)
        self.assertEqual(self.start.call_count, 2)
        self.tag.assert_any_call("sha256:old", "monitor-intelligence-hub:stable")
        self.discard_candidate.assert_called_once_with()
        rejected = list(self.state.glob("release-*/rejected-runtime/monitor.db"))
        self.assertTrue(rejected)

    def test_start_failure_enters_the_same_rollback_path(self):
        self.start.side_effect = [RuntimeError("compose up failed"), None]

        with self.assertRaisesRegex(RuntimeError, "compose up failed"):
            self.deployer.deploy()

        self.assertEqual(self.ids(), [1])
        self.assertEqual((self.project / "docker-compose.yml").read_bytes(), self.old_base)
        self.assertEqual(self.start.call_count, 2)

    def test_backup_failure_restarts_old_release_without_replacing_data(self):
        with patch.object(
            module, "backup_database", side_effect=RuntimeError("disk full")
        ):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                self.deployer.deploy()

        self.assertEqual(self.ids(), [1])
        self.assertEqual((self.project / "docker-compose.yml").read_bytes(), self.old_base)
        self.start.assert_called_once()
        self.assertFalse(list(self.state.glob("release-*")))

    def test_release_retention_counts_only_valid_database_backups(self):
        self.state.mkdir()
        valid = []
        for index in range(7):
            release = self.state / f"release-valid-{index}"
            release.mkdir()
            shutil.copy2(self.database, release / "before-deploy.db")
            os.utime(release, (1000 + index, 1000 + index))
            valid.append(release)

        missing = self.state / "release-missing"
        missing.mkdir()
        empty = self.state / "release-empty"
        empty.mkdir()
        (empty / "before-deploy.db").write_bytes(b"")
        corrupt = self.state / "release-corrupt"
        corrupt.mkdir()
        (corrupt / "before-deploy.db").write_bytes(b"not a SQLite database")
        truncated = self.state / "release-truncated"
        truncated.mkdir()
        header = bytearray(module.SQLITE_HEADER_BYTES)
        header[: len(module.SQLITE_HEADER)] = module.SQLITE_HEADER
        header[16:18] = (4096).to_bytes(2, "big")
        (truncated / "before-deploy.db").write_bytes(header)
        page_truncated = self.state / "release-page-truncated"
        page_truncated.mkdir()
        page_truncated_backup = page_truncated / "before-deploy.db"
        shutil.copy2(self.database, page_truncated_backup)
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        self.assertGreater(page_truncated_backup.stat().st_size, page_size)
        with page_truncated_backup.open("r+b") as handle:
            handle.truncate(page_truncated_backup.stat().st_size - page_size)

        self.deployer.prune_releases()

        self.assertFalse(missing.exists())
        self.assertFalse(empty.exists())
        self.assertFalse(corrupt.exists())
        self.assertFalse(truncated.exists())
        self.assertFalse(page_truncated.exists())
        self.assertEqual(
            {path.name for path in self.state.glob("release-*")},
            {path.name for path in valid[-5:]},
        )

    def test_interruption_after_start_rolls_back_before_propagating(self):
        self.wait_ready.side_effect = [
            module.DeploymentInterrupted("connection closed"),
            None,
        ]

        with self.assertRaisesRegex(module.DeploymentInterrupted, "connection closed"):
            self.deployer.deploy()

        self.assertEqual(self.ids(), [1])
        self.assertEqual((self.project / "docker-compose.yml").read_bytes(), self.old_base)
        self.assertEqual(self.wait_ready.call_count, 2)

    def test_invalid_revision_is_rejected_before_commands_run(self):
        for revision in ("main", "a" * 39, "A" * 40, "a" * 40 + ";true"):
            with self.assertRaises(ValueError):
                module.SourceDeployer(
                    revision,
                    self.source,
                    self.project,
                    self.data,
                    self.state,
                    self.override,
                    "stable",
                    "http://127.0.0.1:8790",
                )


if __name__ == "__main__":
    unittest.main()
