# Monitor continuous deployment

Every push to `main` runs `.github/workflows/deploy-monitor.yml`. The workflow
validates the frontend, compiles the backend, and runs the deployment rollback
tests before it contacts production.

GitHub adds the validated frontend `dist/` to a `git archive` for the exact
commit and sends it to `/opt/monitor/.deploy-src/<commit>`. The server builds the
Python runtime image locally with `Dockerfile.release`, so it reuses the current
runtime dependency layers without repeating `npm ci`, and large images do not
have to pass through the Actions SSH connection.

`deploy_source.py` owns the production transaction:

1. Validate the tracked Compose file and build the candidate image while the old
   service is still running.
2. Record the old container image, stop Monitor, and create a consistent SQLite
   backup with the SQLite backup API.
3. Install the release's tracked `docker-compose.yml`, move the stable image tag,
   and recreate only the `monitor` service.
4. Check the local health API, brands API, frontend entry point and assets, then
   check the public endpoint.
5. On startup, health, SSH `HUP`, `INT`, or `TERM` failure, stop the candidate,
   restore the database and its WAL/SHM state, restore the previous Compose file
   and image, and verify the old release locally.

The server keeps `/opt/monitor/deploy/aliyun/docker-compose.monitor-ljei.yml` as
a host-only override. It contains production-specific settings and is not copied
from Git. Runtime data remains in `/opt/monitor/data`. Deployment reports and the
five newest pre-release database backups are kept in
`/opt/monitor/.deploy-state`.

GitHub repository secrets:

- `ALIYUN_MONITOR_HOST`: the SSH hostname or IPv4 address.
- `ALIYUN_MONITOR_SSH_KEY`: the production deployment private key.
- `ALIYUN_MONITOR_KNOWN_HOSTS`: the verified OpenSSH known-hosts entry.

The workflow connects as `root` because the deployment must stop Docker, take a
host-level database backup, update the stable Compose file, and restore all three
on failure. Source upload uses a staging directory and only replaces a commit's
release directory after the archive extracts successfully. Successful releases
remove temporary source trees and SHA tags while retaining the stable and
rollback image tags.
