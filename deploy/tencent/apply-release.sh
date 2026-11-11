#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Invoked on the provisioned VPS, never on Railway or the user's workstation.
revision="${1:?Expected a full Git commit SHA}"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid revision'; exit 2; }
base=/opt/monitor
release="$base/releases/$revision"
shared="$base/shared"
[[ -d "$release" && ! -L "$release" ]] || { echo 'Release directory missing'; exit 2; }
[[ -s "$shared/data/monitor.db" && -s "$shared/source-manifest.json" && -f "$shared/data-verified" ]] || {
    echo 'Import and verify the complete Railway data before deployment'; exit 2;
}
[[ -f "$shared/app.env" && -f "$shared/compose.env" ]] || {
    echo 'Server environment files are missing'; exit 2;
}
mkdir -p "$base/backups"
exec 9>"$base/deploy.lock"
flock -n 9 || { echo 'Another deployment is running'; exit 3; }
cd "$release"
sha256sum -c monitor-image.tar.gz.sha256
gzip -dc monitor-image.tar.gz | docker load
export MONITOR_IMAGE="monitor:$revision"
[[ "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$MONITOR_IMAGE")" == "$revision" ]] || {
    echo 'Image revision mismatch'; exit 2;
}
compose=(docker compose --project-name monitor-vps --env-file "$shared/compose.env" -f "$release/compose.yml")
"${compose[@]}" config --quiet

was_running=0
if [[ -n "$("${compose[@]}" ps --status running -q monitor)" ]]; then
    was_running=1
    "${compose[@]}" stop -t 90 monitor
fi
new_start_attempted=0
cleanup() {
    local result=$?
    trap - EXIT
    if [[ "$result" != 0 && "$was_running" == 1 && "$new_start_attempted" == 0 ]]; then
        # Resume the stopped container only when no new application startup
        # (and therefore no new schema migration) has been attempted.
        "${compose[@]}" start monitor || true
    fi
    exit "$result"
}
trap cleanup EXIT

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$base/backups/before-$stamp-$revision.tar.gz"
# Includes the entire data tree, WAL/SHM, profiles, and hidden files. Both the
# archive and inventory remain on the VPS; neither is uploaded to GitHub.
python3 "$release/data_inventory.py" create --data "$shared/data" --manifest "$archive.manifest.json" --writers-stopped
tar -czf "$archive.partial" -C "$shared" data
mv "$archive.partial" "$archive"
sha256sum "$archive" > "$archive.sha256"

new_start_attempted=1
"${compose[@]}" up -d --no-build --wait --wait-timeout 240 monitor
curl --fail --silent --show-error --max-time 15 http://127.0.0.1:8790/api/health
ln -sfn "$release" "$base/current"
printf '%s\n' "$revision" > "$shared/current-revision"
echo 'Application healthy. Existing proxy and shared data retained.'
# On a failure after startup, keep the backup and report failure. Do not
# silently overwrite newly accepted user writes or roll schemas backwards.
