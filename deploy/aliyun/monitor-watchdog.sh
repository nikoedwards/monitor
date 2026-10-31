#!/usr/bin/env bash
set -Eeuo pipefail

project_dir=/opt/monitor
compose=(docker compose -f "$project_dir/docker-compose.yml")
if [[ -f "$project_dir/deploy/aliyun/docker-compose.monitor-ljei.yml" ]]; then
  compose+=( -f "$project_dir/deploy/aliyun/docker-compose.monitor-ljei.yml" )
fi
container=monitor-monitor-1
state_file=/run/monitor-watchdog.failures
deploy_lock=/run/lock/monitor-source-deploy.lock

exec 9>"$deploy_lock"
if ! flock -n 9; then
    exit 0
fi

state="$(docker inspect "$container" --format '{{.State.Status}}' 2>/dev/null || true)"
health="$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || true)"

if [[ "$state" == running && "$health" == healthy ]]; then
    rm -f "$state_file"
    exit 0
fi

failures=0
if [[ -f "$state_file" ]]; then
    read -r failures <"$state_file" || failures=0
fi
if ! [[ "$failures" =~ ^[0-9]+$ ]]; then
    failures=0
fi
failures=$((failures + 1))
printf '%s\n' "$failures" >"$state_file"

if [[ "$state" == starting || "$failures" -lt 2 ]]; then
    exit 0
fi

logger -t monitor-watchdog "recovering Monitor: state=$state health=$health failures=$failures"
"${compose[@]}" up -d --no-build --no-deps monitor
rm -f "$state_file"
