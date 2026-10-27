#!/usr/bin/env python3
"""Build and roll out one Monitor source release on the production host."""

import argparse
import contextlib
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request

try:
    import fcntl
except ImportError:  # pragma: no cover - tests can import this module on Windows.
    fcntl = None


class DeploymentInterrupted(RuntimeError):
    """Raised when the SSH session or Actions job interrupts a rollout."""


class ReleaseFailed(RuntimeError):
    """Raised when a candidate cannot become healthy without intervention."""


MIN_RESTART_HEADROOM = 3 * 1024**3
IMAGE_BUILD_TIMEOUT = 35 * 60
SQLITE_HEADER = b"SQLite format 3\x00"
SQLITE_HEADER_BYTES = 100


def backup_database(source, destination):
    pending = destination.with_suffix(".pending")
    pending.unlink(missing_ok=True)
    source_uri = source.resolve().as_uri() + "?mode=ro"
    with contextlib.closing(sqlite3.connect(source_uri, uri=True)) as src:
        with contextlib.closing(sqlite3.connect(pending)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Database backup failed integrity check")
    pending.chmod(0o600)
    pending.replace(destination)


def restore_database(backup, live):
    rejected = backup.parent / "rejected-runtime"
    rejected.mkdir()
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(live) + suffix)
        if path.exists():
            path.replace(rejected / path.name)
    shutil.copy2(backup, live)


def atomic_write(path, contents, mode=0o644):
    pending = path.with_name(path.name + ".pending")
    pending.write_bytes(contents)
    pending.chmod(mode)
    pending.replace(path)


class SourceDeployer:
    def __init__(
        self,
        revision,
        source_dir,
        project_dir,
        data_dir,
        state_dir,
        server_override,
        stable_image,
        local_url,
        public_url=None,
    ):
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Expected a full lowercase Git commit SHA")
        self.revision = revision
        self.source_dir = Path(source_dir).resolve()
        self.project_dir = Path(project_dir).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.server_override = Path(server_override).resolve()
        self.source_base = self.source_dir / "docker-compose.yml"
        self.release_dockerfile = (
            self.source_dir / "deploy/aliyun/autodeploy/Dockerfile.release"
        )
        self.stable_base = self.project_dir / "docker-compose.yml"
        self.database = self.data_dir / "monitor.db"
        self.image = f"monitor-intelligence-hub:{revision}"
        self.stable_image = stable_image
        self.rollback_image = "monitor-intelligence-hub:rollback"
        self.local_url = local_url.rstrip("/")
        self.public_url = public_url.rstrip("/") if public_url else None
        self.rolling_back = False
        self.committed = False

    def run(self, args, *, timeout=900, env=None, stream_output=False):
        command_env = os.environ.copy()
        if env:
            command_env.update(env)
        result = subprocess.run(
            args,
            text=True,
            stdout=None if stream_output else subprocess.PIPE,
            stderr=None if stream_output else subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=command_env,
        )
        if result.returncode != 0:
            detail = ((result.stdout or "") + (result.stderr or "")).strip()
            if len(detail) > 8000:
                detail = detail[-8000:]
            raise RuntimeError(
                f"Command failed ({result.returncode}): {' '.join(args)}"
                + (f"\n{detail}" if detail else "")
            )
        return (result.stdout or "").strip()

    def compose(self, base_file, *args):
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(self.project_dir),
            "-f",
            str(base_file),
            "-f",
            str(self.server_override),
        ]
        return self.run(command + list(args))

    def validate_inputs(self):
        required = (
            self.source_base,
            self.release_dockerfile,
            self.source_dir / "dist/index.html",
            self.stable_base,
            self.server_override,
            self.database,
        )
        for path in required:
            if not path.is_file():
                raise RuntimeError(f"Required deployment file is missing: {path}")
        rendered = json.loads(
            self.compose(self.source_base, "config", "--format", "json")
        )
        monitor_image = rendered.get("services", {}).get("monitor", {}).get("image")
        if monitor_image != self.stable_image:
            raise RuntimeError(
                f"Monitor Compose image is {monitor_image!r}, expected {self.stable_image!r}"
            )

    def build_image(self):
        command = [
            "docker",
            "build",
            "--build-arg",
            "BUILDKIT_INLINE_CACHE=1",
            "--label",
            f"org.opencontainers.image.revision={self.revision}",
            "--label",
            "org.opencontainers.image.source=https://github.com/nikoedwards/monitor",
            "--tag",
            self.image,
            "--file",
            str(self.release_dockerfile),
        ]
        try:
            self.run(["docker", "image", "inspect", self.stable_image], timeout=30)
        except RuntimeError:
            pass
        else:
            # The stable image already contains Playwright, Chromium, and the
            # Debian runtime packages. Reusing it avoids a slow CDN download
            # on every release; the Dockerfile falls back to a full install if
            # the cached runtime is incomplete.
            command += [
                "--cache-from",
                self.stable_image,
                "--build-arg",
                f"BASE_IMAGE={self.stable_image}",
                "--build-arg",
                "REUSE_RUNTIME=1",
            ]
        command.append(str(self.source_dir))
        self.run(
            command,
            timeout=IMAGE_BUILD_TIMEOUT,
            env={"DOCKER_BUILDKIT": "1"},
            stream_output=True,
        )

    def image_id(self, reference):
        return self.run(
            ["docker", "image", "inspect", reference, "--format", "{{.Id}}"],
            timeout=30,
        )

    def container_id(self, base_file):
        return self.compose(base_file, "ps", "--all", "-q", "monitor")

    def current_image_id(self, base_file):
        container = self.container_id(base_file)
        if not container:
            raise RuntimeError("Monitor container is not present")
        return self.run(
            ["docker", "inspect", container, "--format", "{{.Image}}"], timeout=30
        )

    def validate_live_data_mount(self, base_file):
        container = self.container_id(base_file)
        if not container:
            raise RuntimeError("Monitor container is not present")
        mounts = json.loads(
            self.run(
                ["docker", "inspect", container, "--format", "{{json .Mounts}}"],
                timeout=30,
            )
        )
        data_mount = next(
            (mount for mount in mounts if mount.get("Destination") == "/app/data"),
            None,
        )
        if not data_mount or data_mount.get("Type") != "bind":
            raise RuntimeError("Monitor /app/data is not a host bind mount")
        mounted_source = Path(data_mount.get("Source", "")).resolve()
        if mounted_source != self.data_dir:
            raise RuntimeError(
                f"Monitor data mount is {mounted_source}, expected {self.data_dir}"
            )

    def tag(self, source, destination):
        self.run(["docker", "image", "tag", source, destination], timeout=60)

    def stop(self, base_file):
        self.compose(base_file, "stop", "-t", "60", "monitor")

    def force_remove(self, base_file):
        container = self.container_id(base_file)
        if container:
            self.run(["docker", "rm", "-f", container], timeout=90)

    def start(self, base_file):
        self.compose(
            base_file,
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            "--force-recreate",
            "monitor",
        )

    def install_base(self, contents):
        atomic_write(self.stable_base, contents)

    def deployment_storage_is_shared(self):
        return self.state_dir.stat().st_dev == self.data_dir.stat().st_dev

    def ensure_deployment_capacity(self):
        if not self.deployment_storage_is_shared():
            raise RuntimeError(
                "Monitor data and deployment state must be on the same filesystem "
                "for atomic rollback"
            )
        try:
            backup_bytes = self.database.stat().st_size
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Database disappeared before deployment: {self.database}"
            ) from exc
        for path in (
            Path(str(self.database) + "-wal"),
            Path(str(self.database) + "-shm"),
        ):
            try:
                backup_bytes += path.stat().st_size
            except FileNotFoundError:
                pass
        backup_free = shutil.disk_usage(self.state_dir).free
        required = backup_bytes + MIN_RESTART_HEADROOM
        if backup_free < required:
            raise RuntimeError(
                "Need at least "
                f"{required} bytes free before stopping Monitor "
                f"({backup_bytes} for the SQLite backup and "
                f"{MIN_RESTART_HEADROOM} reserved for restart); "
                f"only {backup_free} available"
            )

    def discard_candidate(self):
        try:
            self.run(["docker", "image", "rm", self.image], timeout=120)
        except RuntimeError as exc:
            print(f"Could not remove failed candidate tag: {exc}", file=sys.stderr)

    def _check_http(self, base_url):
        with urllib.request.urlopen(base_url + "/api/health", timeout=5) as response:
            if json.load(response).get("ok") is not True:
                raise ValueError("Unexpected health response")
        with urllib.request.urlopen(base_url + "/api/brands", timeout=10) as response:
            json.load(response)
        with urllib.request.urlopen(base_url + "/", timeout=10) as response:
            html = response.read().decode()
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
        if not assets:
            raise ValueError("Frontend entry point has no assets")
        for asset in assets:
            with urllib.request.urlopen(base_url + asset, timeout=10) as response:
                if response.status != 200:
                    raise ValueError(f"Frontend asset unavailable: {asset}")
        return tuple(sorted(set(assets)))

    def wait_ready(
        self, base_url, expected_image, timeout=240, expected_assets=None
    ):
        deadline = time.monotonic() + timeout
        last_error = "service did not answer"
        while time.monotonic() < deadline:
            try:
                container = self.container_id(self.stable_base)
                if not container:
                    raise RuntimeError("Monitor container is not present")
                state = self.run(
                    ["docker", "inspect", container, "--format", "{{.State.Status}}"],
                    timeout=30,
                )
                if state not in ("running", "restarting"):
                    try:
                        logs = self.run(
                            ["docker", "logs", "--tail", "100", container],
                            timeout=30,
                        )
                    except RuntimeError as log_error:
                        logs = str(log_error)
                    raise ReleaseFailed(
                        f"Monitor container entered state {state}: {logs[-4000:]}"
                    )
                running_image = self.run(
                    ["docker", "inspect", container, "--format", "{{.Image}}"],
                    timeout=30,
                )
                if running_image != expected_image:
                    raise ReleaseFailed(
                        f"Monitor is running {running_image}, expected {expected_image}"
                    )
                assets = self._check_http(base_url)
                if expected_assets is not None and assets != expected_assets:
                    raise ValueError(
                        f"Frontend assets are {assets}, expected {expected_assets}"
                    )
                return assets
            except (DeploymentInterrupted, ReleaseFailed):
                raise
            except (OSError, ValueError, RuntimeError) as exc:
                last_error = str(exc)
                time.sleep(3)
        raise RuntimeError(f"Release health check timed out: {last_error}")

    def _rollback(self, previous_base, previous_image, backup, backup_ready):
        errors = []
        stopped = False
        try:
            self.stop(self.stable_base)
            stopped = True
        except BaseException as exc:
            errors.append(f"compose stop failed: {exc}")
            try:
                self.force_remove(self.stable_base)
                stopped = True
            except BaseException as force_exc:
                errors.append(f"force remove failed: {force_exc}")

        database_ready = not backup_ready
        if stopped and backup_ready:
            try:
                restore_database(backup, self.database)
                database_ready = True
            except BaseException as exc:
                errors.append(f"database restore failed: {exc}")

        try:
            self.install_base(previous_base)
        except BaseException as exc:
            errors.append(f"Compose restore failed: {exc}")
        try:
            self.tag(previous_image, self.stable_image)
        except BaseException as exc:
            errors.append(f"image restore failed: {exc}")

        if stopped and database_ready:
            try:
                self.start(self.stable_base)
                self.wait_ready(self.local_url, previous_image)
            except BaseException as exc:
                errors.append(f"previous release restart failed: {exc}")
        elif not stopped:
            errors.append("candidate could not be stopped; database was not restored")
        else:
            errors.append("database was not restored; previous release was not started")

        if errors:
            raise RuntimeError("; ".join(errors))

    def cleanup(self, current_release):
        tags = self.run(
            [
                "docker",
                "image",
                "ls",
                "--filter",
                "reference=monitor-intelligence-hub:*",
                "--format",
                "{{.Repository}}:{{.Tag}}",
            ],
            timeout=60,
        ).splitlines()
        for tag in tags:
            if re.fullmatch(r"monitor-intelligence-hub:[0-9a-f]{40}", tag):
                try:
                    self.run(["docker", "image", "rm", tag], timeout=120)
                except RuntimeError:
                    pass
        try:
            self.run(
                [
                    "docker",
                    "image",
                    "prune",
                    "--force",
                    "--filter",
                    "until=168h",
                    "--filter",
                    "label=org.opencontainers.image.source=https://github.com/nikoedwards/monitor",
                ],
                timeout=120,
            )
        except RuntimeError:
            pass
        source_root = self.source_dir.parent
        for old_source in source_root.iterdir():
            if old_source == self.source_dir or not old_source.is_dir():
                continue
            if re.fullmatch(r"(?:\.incoming-)?[0-9a-f]{40}", old_source.name):
                shutil.rmtree(old_source)
        self.prune_releases(current_release)

    def release_has_valid_backup(self, release):
        backup = release / "before-deploy.db"
        try:
            if not backup.is_file():
                return False
            size = backup.stat().st_size
            if size < SQLITE_HEADER_BYTES:
                return False
            with backup.open("rb") as handle:
                header = handle.read(SQLITE_HEADER_BYTES)
            if not header.startswith(SQLITE_HEADER):
                return False
            encoded_page_size = int.from_bytes(header[16:18], "big")
            page_size = 65536 if encoded_page_size == 1 else encoded_page_size
            declared_pages = int.from_bytes(header[28:32], "big")
            actual_pages = size // page_size if page_size else 0
            header_size_is_current = header[24:28] == header[92:96]
            return (
                512 <= page_size <= 65536
                and (page_size & (page_size - 1)) == 0
                and size >= page_size
                and size % page_size == 0
                and (
                    declared_pages == 0
                    or not header_size_is_current
                    or declared_pages == actual_pages
                )
            )
        except OSError:
            return False

    def prune_releases(self, current_release=None):
        valid = []
        for release in self.state_dir.glob("release-*"):
            if not release.is_dir():
                continue
            if self.release_has_valid_backup(release):
                valid.append(release)
            elif release != current_release and release.parent == self.state_dir:
                shutil.rmtree(release)

        releases = sorted(
            valid,
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        keep = set(releases[:5])
        if current_release in releases and current_release not in keep:
            keep.discard(releases[4])
            keep.add(current_release)
        for old in releases:
            if old not in keep and old.parent == self.state_dir:
                shutil.rmtree(old)

    def deploy(self):
        self.validate_inputs()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.build_image()
        release_dir = None
        try:
            self.prune_releases()
            candidate_image = self.image_id(self.image)
            previous_image = self.current_image_id(self.stable_base)
            self.validate_live_data_mount(self.stable_base)
            previous_base = self.stable_base.read_bytes()
            self.tag(previous_image, self.rollback_image)

            stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ"
            )
            release_dir = self.state_dir / (
                f"release-{stamp}-{self.revision[:12]}-{os.getpid()}"
            )
            release_dir.mkdir(mode=0o700)
            self.prune_releases(release_dir)
            self.ensure_deployment_capacity()
        except BaseException:
            if release_dir is not None:
                shutil.rmtree(release_dir, ignore_errors=True)
            self.discard_candidate()
            raise
        backup = release_dir / "before-deploy.db"
        backup_ready = False
        stop_started = False

        try:
            stop_started = True
            self.stop(self.stable_base)
            backup_database(self.database, backup)
            backup_ready = True
            self.install_base(self.source_base.read_bytes())
            self.tag(self.image, self.stable_image)
            self.start(self.stable_base)
            self.validate_live_data_mount(self.stable_base)
            local_assets = self.wait_ready(self.local_url, candidate_image)
            if self.public_url and self.public_url != self.local_url:
                self.wait_ready(
                    self.public_url,
                    candidate_image,
                    expected_assets=local_assets,
                )
            self.committed = True
        except BaseException as deployment_error:
            if stop_started:
                self.rolling_back = True
                try:
                    self._rollback(
                        previous_base, previous_image, backup, backup_ready
                    )
                except BaseException as rollback_error:
                    raise RuntimeError(
                        f"Deployment failed: {deployment_error}; rollback failed: {rollback_error}"
                    ) from deployment_error
                finally:
                    self.rolling_back = False
                try:
                    self.prune_releases()
                except Exception as cleanup_error:
                    print(
                        f"Rollback completed; release cleanup needs attention: {cleanup_error}",
                        file=sys.stderr,
                    )
                self.discard_candidate()
            raise

        report = {
            "revision": self.revision,
            "image": self.image,
            "imageId": candidate_image,
            "previousImageId": previous_image,
            "deployedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "databaseBackup": str(backup),
            "localURL": self.local_url,
            "publicURL": self.public_url,
        }
        (release_dir / "deployment.json").write_text(json.dumps(report, indent=2))
        (self.state_dir / "current.json").write_text(json.dumps(report, indent=2))
        try:
            self.cleanup(release_dir)
        except Exception as exc:
            print(f"Release is healthy; cleanup needs attention: {exc}", file=sys.stderr)
        return report


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("revision")
    parser.add_argument("source_dir")
    parser.add_argument("--project-dir", default="/opt/monitor")
    parser.add_argument("--data-dir", default="/opt/monitor/data")
    parser.add_argument("--state-dir", default="/opt/monitor/.deploy-state")
    parser.add_argument(
        "--server-override",
        default="/opt/monitor/deploy/aliyun/docker-compose.monitor-ljei.yml",
    )
    parser.add_argument(
        "--stable-image", default="monitor-intelligence-hub:isolated-20261027"
    )
    parser.add_argument("--local-url", default="http://127.0.0.1:8790")
    parser.add_argument("--public-url")
    return parser.parse_args()


def main():
    if os.name != "posix" or os.geteuid() != 0 or fcntl is None:
        raise SystemExit("This entry point must run as root on Linux")
    os.umask(0o077)
    args = parse_args()
    deployer = SourceDeployer(
        args.revision,
        args.source_dir,
        args.project_dir,
        args.data_dir,
        args.state_dir,
        args.server_override,
        args.stable_image,
        args.local_url,
        args.public_url,
    )

    def interrupted(signum, _frame):
        if deployer.rolling_back or deployer.committed:
            return
        raise DeploymentInterrupted(f"Deployment interrupted by signal {signum}")

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)

    with open("/run/lock/monitor-source-deploy.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(deployer.deploy()), flush=True)


if __name__ == "__main__":
    main()
