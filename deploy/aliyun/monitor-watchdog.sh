#!/usr/bin/env bash
set -Eeuo pipefail

project_dir=${MONITOR_WATCHDOG_PROJECT_DIR:-/opt/monitor}
state_dir=${MONITOR_WATCHDOG_STATE_DIR:-/run}
compose=(docker compose -f "$project_dir/docker-compose.yml")
if [[ -f "$project_dir/deploy/aliyun/docker-compose.monitor-ljei.yml" ]]; then
    compose+=(-f "$project_dir/deploy/aliyun/docker-compose.monitor-ljei.yml")
fi
container=monitor-monitor-1
state_file="$state_dir/monitor-watchdog.failures"
attempt_file="$state_dir/monitor-watchdog.last-attempt"
deploy_lock="${MONITOR_WATCHDOG_LOCK:-/run/lock/monitor-source-deploy.lock}"

exec 9>"$deploy_lock"
if ! flock -n 9; then exit 0; fi

# One inspect gives a coherent state/health pair. An API timeout does not
# establish that the container is stopped and must never trigger Compose up.
if inspection="$(timeout -k 2 10 docker inspect "$container" --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>&1)"; then
    read -r state health <<< "$inspection"
else
    rc=$?
    if [[ "$rc" == 1 && ( "$inspection" == *'No such object:'* || "$inspection" == *'No such container:'* ) ]]; then
        state=missing
        health=none
    else
        logger -t monitor-watchdog "Docker state unknown (inspect exit=$rc); deferring recovery"
        exit 0
    fi
fi

if [[ "$state" == running && "$health" == healthy ]]; then
    rm -f "$state_file"
    exit 0
fi
if [[ "$health" == starting || "$state" == restarting ]]; then exit 0; fi
case "$state" in
    running|exited|created|dead|missing) ;;
    *) logger -t monitor-watchdog "Unrecognized Docker state; deferring recovery"; exit 0 ;;
esac

failures=0
if [[ -f "$state_file" ]]; then read -r failures <"$state_file" || failures=0; fi
[[ "$failures" =~ ^[0-9]+$ ]] || failures=0
failures=$((failures + 1))
printf '%s\n' "$failures" >"$state_file"
if [[ "$failures" -lt 2 ]]; then exit 0; fi

now=$(date +%s)
last_attempt=0
if [[ -f "$attempt_file" ]]; then read -r last_attempt <"$attempt_file" || last_attempt=0; fi
[[ "$last_attempt" =~ ^[0-9]+$ ]] || last_attempt=0
if (( now - last_attempt < 600 )); then exit 0; fi
printf '%s\n' "$now" >"$attempt_file"
logger -t monitor-watchdog "recovering Monitor: state=$state health=$health failures=$failures"
if [[ "$state" == running ]]; then
    timeout -k 5 45 docker restart --time 10 "$container"
elif [[ "$state" == missing ]]; then
    timeout -k 5 45 "${compose[@]}" up -d --no-build --no-deps monitor
else
    timeout -k 5 45 docker start "$container"
fi
rm -f "$state_file"