#!/usr/bin/env bash
set -Eeuo pipefail

# Run from the repository root, for example:
#   ./deploy/backup.sh
# The archive contains the SQLite database, WAL files and all snapshots.
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

backup_dir="${BACKUP_DIR:-$repo_dir/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$backup_dir"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$backup_dir/monitor-data-$stamp.tar.gz"

cleanup() {
    # The service may have been stopped before tar started. Always bring it
    # back when this script exits, including after an interrupted backup.
    if [[ "${service_was_running:-0}" == "1" ]]; then
        docker compose up -d monitor >/dev/null
    fi
}
trap cleanup EXIT

service_was_running=0
if docker compose ps --status running --services 2>/dev/null | grep -qx monitor; then
    service_was_running=1
    docker compose stop monitor >/dev/null
fi

tar -czf "$archive" data
find "$backup_dir" -type f -name 'monitor-data-*.tar.gz' -mtime "+$retention_days" -delete

echo "Created $archive"
