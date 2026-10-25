# Monitor continuous deployment

Every push to `main` runs `.github/workflows/deploy-monitor.yml`. Manual reruns
are available through GitHub Actions (`workflow_dispatch`). Standard hosted
runners are free for this public repository. Images are streamed directly to
the server; no container registry or image artifact storage is required.

GitHub builds the normal project Dockerfile (including frontend type checking),
checks backend syntax and the voice/source regression tests, then
uploads the compressed Docker image over SSH. The server first checks the image
identity and starts an isolated candidate with a database copy, read-only access
to snapshots, and collection disabled. A failed candidate leaves production alone.

After preflight, only Monitor stops. The server takes a fresh SQLite backup,
changes its image override, and starts the new release. The host data directory
and collector settings stay in place. If its HTTP/frontend checks fail, the old
image and that fresh database backup are restored. The previous image and the
five most recent deployment directories are retained. Kicksonar is not deployed
by this workflow.

GitHub repository secrets:

- `ALIYUN_MONITOR_HOST`: the SSH hostname or IP.
- `ALIYUN_MONITOR_SSH_KEY`: a dedicated Ed25519 deployment private key.
- `ALIYUN_MONITOR_KNOWN_HOSTS`: verified OpenSSH known_hosts entry for that host.

The dedicated `monitor-deploy` SSH user has a forced command and no Docker group
membership. Its key accepts only `deploy <40-character commit SHA>`; forwarding,
PTYs, and arbitrary shell commands are disabled. Root owns the deploy script,
SSH gateway, sudo rule, and `/etc/monitor-deploy.json`. Runtime environment
credentials stay on the server and are not copied into the repository or image.

Server configuration schema (paths are examples):

```json
{
  "data_dir": "/opt/aliyun/data/monitor",
  "state_dir": "/opt/aliyun/monitor-deploy",
  "release_override": "/opt/aliyun/deploy/aliyun/docker-compose.monitor-release.yml",
  "compose_files": [
    "/opt/aliyun/deploy/aliyun/docker-compose.yml",
    "/opt/aliyun/deploy/aliyun/docker-compose.monitor-ip.yml"
  ],
  "container": "aliyun-production-monitor-1",
  "env_file": "/opt/aliyun/env/monitor.env",
  "public_url": "http://YOUR_SERVER_IP"
}
```

Root installs `deploy_monitor.py` as `/usr/local/sbin/deploy-monitor` and
`ssh-gateway.sh` as `/usr/local/sbin/monitor-ssh-gateway`. The SSH authorized key
uses `restrict,command="/usr/local/sbin/monitor-ssh-gateway"`; sudo allows only
`/usr/local/sbin/deploy-monitor`. The root script validates the argument again.

Deployment status is in `/opt/aliyun/monitor-deploy/current.json`; root-only
release folders contain the pre-deploy database backup and deployment metadata.
Include the generated `docker-compose.monitor-release.yml` after both existing
Compose files when manually restarting Monitor, so it uses the current release.
After changing domains, update `public_url` in the root-owned configuration and
the final workflow HTTP check to the new HTTPS address.

Host-side gateway/script/config changes are installed separately through the
administrator SSH session; repository code cannot replace the SSH gateway.

The deployment gate also tests failed preflight, failed startup with database
restoration, backup failure, and successful rollout bookkeeping. It does not run
the full legacy suite: the initial audit found existing creator/sales failures
and tests requiring development-only HTTP-client/browser-worker dependencies.
Those unrelated failures are not silently treated as passing checks.
