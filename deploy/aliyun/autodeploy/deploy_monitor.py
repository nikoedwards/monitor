#!/usr/bin/env python3
"""Receive one release image; preflight it and replace only the Monitor service."""
import contextlib
import datetime
import fcntl
import gzip
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


def backup_database(source, destination):
    pending = destination.with_suffix('.pending')
    with contextlib.closing(sqlite3.connect(f'file:{source}?mode=ro', uri=True)) as src:
        with contextlib.closing(sqlite3.connect(pending)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Database backup failed integrity check')
    pending.chmod(0o600)
    pending.replace(destination)


def restore_database(backup, live):
    # Caller has stopped Monitor. Preserve the rejected database and sidecars.
    rejected = backup.parent / 'rejected-runtime'
    rejected.mkdir()
    for suffix in ('', '-wal', '-shm'):
        path = Path(str(live) + suffix)
        if path.exists():
            path.rename(rejected / path.name)
    shutil.copy2(backup, live)


class Deployer:
    def __init__(self, config, revision):
        if not re.fullmatch('[0-9a-f]{40}', revision):
            raise ValueError('Expected a full lowercase Git commit SHA')
        self.config = config
        self.revision = revision
        self.image = 'monitor-deploy:' + revision
        self.data = Path(config['data_dir'])
        self.root = Path(config['state_dir'])
        self.override = Path(config['release_override'])
        self.base_compose = ['docker', 'compose']
        for path in config['compose_files']:
            self.base_compose += ['-f', path]
        self.candidate = 'monitor-deploy-check'

    def run(self, args, **kwargs):
        return subprocess.check_output(args, text=True, **kwargs).strip()

    def compose(self, *args):
        cmd = self.base_compose[:]
        if self.override.exists():
            cmd += ['-f', str(self.override)]
        return self.run(cmd + list(args))

    def current_image(self):
        return self.run(['docker', 'inspect', self.config['container'],
                         '--format', '{{.Config.Image}}'])

    def write_override(self, image):
        # Image names come from Docker inspect or a validated SHA, never shell text.
        text = 'services:\n  monitor:\n    image: ' + json.dumps(image) + '\n'
        pending = self.override.with_suffix('.pending')
        pending.write_text(text)
        pending.replace(self.override)

    def load_image(self, stream):
        if shutil.disk_usage(self.data).free < 3 * 1024**3:
            raise RuntimeError('Need at least 3 GiB free before receiving a release')
        process = subprocess.Popen(['docker', 'load', '--quiet'], stdin=subprocess.PIPE)
        try:
            with gzip.GzipFile(fileobj=stream, mode='rb') as archive:
                shutil.copyfileobj(archive, process.stdin, length=1024*1024)
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError('Docker could not load the release image')
        except BaseException:
            process.stdin.close()
            process.wait()
            raise
        labels = json.loads(self.run(['docker', 'image', 'inspect', self.image,
                                      '--format', '{{json .Config.Labels}}']))
        if (labels.get('org.opencontainers.image.revision') != self.revision or
                labels.get('org.opencontainers.image.source') !=
                'https://github.com/nikoedwards/monitor'):
            raise RuntimeError('Release image identity did not match the request')

    def wait_ready(self, base_url, container, timeout=300):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.run(['docker', 'inspect', container, '--format', '{{.State.Status}}'])
            if state not in ('running', 'created', 'restarting'):
                raise RuntimeError('Release container exited before becoming healthy')
            try:
                with urllib.request.urlopen(base_url + '/api/health', timeout=5) as response:
                    if json.load(response).get('ok') is not True:
                        raise ValueError('Unexpected health response')
                with urllib.request.urlopen(base_url + '/api/brands', timeout=10) as response:
                    json.load(response)
                with urllib.request.urlopen(base_url + '/', timeout=10) as response:
                    html = response.read().decode()
                assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
                if not assets:
                    raise ValueError('Frontend entry point has no assets')
                for asset in assets:
                    with urllib.request.urlopen(base_url + asset, timeout=10) as response:
                        if response.status != 200:
                            raise ValueError('Frontend asset unavailable')
                return
            except (OSError, ValueError):
                time.sleep(3)
        raise RuntimeError('Release health check timed out')

    def preflight(self, release_dir):
        check_data = release_dir / 'preflight'
        check_data.mkdir()
        backup_database(self.data / 'monitor.db', check_data / 'monitor.db')
        subprocess.run(['docker', 'rm', '-f', self.candidate],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # The candidate may migrate its database copy; production snapshots are read-only.
        self.run(['docker', 'run', '-d', '--name', self.candidate, '--init',
                  '--env-file', self.config['env_file'],
                  '-e', 'HOST=0.0.0.0', '-e', 'PORT=8790', '-e', 'MONITOR_SCHEDULER=0',
                  '-p', '127.0.0.1:8791:8790', '--shm-size', '1gb',
                  '--mount', f'type=bind,src={check_data},dst=/app/data',
                  '--mount', f'type=bind,src={self.data / "snapshots"},dst=/app/data/snapshots,readonly',
                  self.image])
        try:
            self.wait_ready('http://127.0.0.1:8791', self.candidate)
        finally:
            self.run(['docker', 'rm', '-f', self.candidate])

    def stop(self):
        self.compose('stop', '-t', '60', 'monitor')

    def start(self):
        self.compose('up', '-d', '--no-build', '--no-deps', 'monitor')

    def cleanup(self, release_dir):
        # Only this deployer's containers, backup directories and image tags.
        tags = self.run(['docker', 'image', 'ls', '--filter', 'reference=monitor-deploy:*',
                         '--format', '{{.Repository}}:{{.Tag}}']).splitlines()
        for tag in tags:
            if re.fullmatch('monitor-deploy:[0-9a-f]{40}', tag) and tag != self.image:
                self.run(['docker', 'image', 'rm', tag])
        for old in sorted(self.root.glob('release-*'), reverse=True)[5:]:
            if old.is_dir() and old.parent == self.root and old != release_dir:
                shutil.rmtree(old)

    def deploy(self, stream):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.load_image(stream)
        previous = self.current_image()
        if previous == self.image:
            self.wait_ready(self.config['public_url'], self.config['container'])
            return {'revision': self.revision, 'alreadyDeployed': True}
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        release_dir = self.root / f'release-{stamp}-{self.revision[:12]}'
        release_dir.mkdir(mode=0o700)
        self.preflight(release_dir)
        # Keep the actual preceding image even when old SHA tags are cleaned up.
        self.run(['docker', 'image', 'tag', previous, 'monitor-deploy:rollback'])
        backup = release_dir / 'before-deploy.db'
        previous_override = self.override.read_bytes() if self.override.exists() else None
        stopped = False
        try:
            stopped = True
            self.stop()
            # Capture again AFTER stopping the old collector: preflight's older
            # database copy is never used to restore production.
            backup_database(self.data / 'monitor.db', backup)
            self.write_override(self.image)
            self.start()
            self.wait_ready(self.config['public_url'], self.config['container'])
        except BaseException:
            if stopped:
                self.stop()
                if backup.exists():
                    restore_database(backup, self.data / 'monitor.db')
                if previous_override is None:
                    self.override.unlink(missing_ok=True)
                else:
                    self.override.write_bytes(previous_override)
                self.start()
                self.wait_ready(self.config['public_url'], self.config['container'])
            raise
        report = {'revision': self.revision, 'image': self.image, 'previousImage': previous,
                  'deployedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'databaseBackup': str(backup), 'publicURL': self.config['public_url']}
        (release_dir / 'deployment.json').write_text(json.dumps(report, indent=2))
        (self.root / 'current.json').write_text(json.dumps(report, indent=2))
        try:
            self.cleanup(release_dir)
        except Exception:
            # A housekeeping failure must not turn a healthy rollout into a rollback.
            print('Release is healthy; housekeeping needs attention.', file=sys.stderr)
        return report


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit('This entry point requires root and one commit SHA')
    os.umask(0o077)
    config = json.loads(Path('/etc/monitor-deploy.json').read_text())
    deployer = Deployer(config, sys.argv[1])
    def interrupted(signum, frame):
        raise RuntimeError('Deployment connection interrupted')
    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    with open('/run/lock/monitor-deploy.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(deployer.deploy(sys.stdin.buffer)), flush=True)


if __name__ == '__main__':
    main()
