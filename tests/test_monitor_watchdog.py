"""Run the actual shell watchdog against a fake Docker CLI."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/aliyun/monitor-watchdog.sh"


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("flock"), "Linux watchdog")
class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls"
        docker = self.bin / "docker"
        docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >>"$TEST_CALLS"
if [ "$1" = inspect ]; then
    printf '%s\\n' "$TEST_INSPECT"
    exit "${TEST_INSPECT_RC:-0}"
fi
exit "${TEST_RECOVERY_RC:-0}"
""")
        logger = self.bin / "logger"
        logger.write_text("#!/bin/sh\nexit 0\n")
        docker.chmod(0o755)
        logger.chmod(0o755)
        self.env = {
            **os.environ, "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "TEST_CALLS": str(self.calls), "TEST_INSPECT": "running healthy",
            "MONITOR_WATCHDOG_STATE_DIR": str(self.root),
            "MONITOR_WATCHDOG_PROJECT_DIR": str(self.root),
            "MONITOR_WATCHDOG_LOCK": str(self.root / "lock"),
        }

    def run_check(self):
        return subprocess.run(["bash", str(SCRIPT)], env=self.env, capture_output=True, text=True, timeout=5)

    def commands(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_inspect_timeout_never_runs_recovery(self):
        self.env.update(TEST_INSPECT="", TEST_INSPECT_RC="124")
        self.assertEqual(self.run_check().returncode, 0)
        self.assertEqual(self.run_check().returncode, 0)
        self.assertTrue(all(x.startswith("inspect ") for x in self.commands()))

    def test_healthy_check_uses_one_inspect(self):
        (self.root / "monitor-watchdog.failures").write_text("1\n")
        self.assertEqual(self.run_check().returncode, 0)
        self.assertEqual(len(self.commands()), 1)
        self.assertFalse((self.root / "monitor-watchdog.failures").exists())

    def test_unhealthy_container_is_restarted_after_two_checks(self):
        self.env["TEST_INSPECT"] = "running unhealthy"
        self.run_check()
        self.assertFalse(any(x.startswith("restart") for x in self.commands()))
        self.run_check()
        self.assertEqual(sum(x.startswith("restart --time 10") for x in self.commands()), 1)
        self.assertFalse(any(x.startswith("compose") for x in self.commands()))

    def test_failed_recovery_is_rate_limited(self):
        self.env.update(TEST_INSPECT="running unhealthy", TEST_RECOVERY_RC="124")
        self.run_check()
        self.assertEqual(self.run_check().returncode, 124)
        self.run_check()
        self.assertEqual(sum(x.startswith("restart") for x in self.commands()), 1)

    def test_exited_container_uses_start(self):
        self.env["TEST_INSPECT"] = "exited unhealthy"
        self.run_check()
        self.run_check()
        self.assertTrue(any(x == "start monitor-monitor-1" for x in self.commands()))

    def test_missing_container_uses_compose(self):
        self.env.update(TEST_INSPECT="Error: No such object: monitor-monitor-1", TEST_INSPECT_RC="1")
        self.run_check()
        self.run_check()
        self.assertTrue(any(x.startswith("compose ") and "up -d --no-build --no-deps" in x for x in self.commands()))


if __name__ == "__main__":
    unittest.main()
