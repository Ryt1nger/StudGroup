#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY=/opt/studgroup
COMPOSE_FILE=deploy/server/compose.yml
STATE_DIR=/var/lib/studgroup
LOCK_FILE=/run/studgroup-deploy.lock

requested=${SSH_ORIGINAL_COMMAND:-${*:-}}
if [[ ! $requested =~ ^deploy[[:space:]]+([0-9a-f]{40})$ ]]; then
  echo "usage: deploy <merged-main-sha>" >&2
  exit 64
fi
target=${BASH_REMATCH[1]}

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "another deploy is running" >&2; exit 75; }

cd "$REPOSITORY"
git fetch --quiet origin main
remote_main=$(git rev-parse origin/main)
if [[ $target != "$remote_main" ]]; then
  echo "refusing non-current main SHA" >&2
  exit 65
fi

previous=$(git rev-parse HEAD)
mkdir -p "$STATE_DIR"

deploy_sha() {
  local sha=$1 tag=${1:0:12} require_worker=${2:-0}
  # Explicit checks are required: bash disables errexit inside an if-tested function.
  git checkout --quiet --detach "$sha" || return 1
  STUDGROUP_IMAGE_TAG=$tag docker compose -f "$COMPOSE_FILE" build api caddy ranepa-worker || return 1
  STUDGROUP_IMAGE_TAG=$tag docker compose -f "$COMPOSE_FILE" up -d redis api caddy ranepa-worker || return 1

  local state
  for _ in $(seq 1 36); do
    state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' server-api-1 2>/dev/null || true)
    [[ $state == healthy ]] && break
    sleep 5
  done
  [[ ${state:-} == healthy ]] || return 1
  docker exec server-api-1 python -c \
    "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=15); raise SystemExit(0 if r.status == 200 else 1)" || return 1
  curl --fail --silent --show-error --max-time 20 \
    https://api.109-237-97-85.sslip.io/health >/dev/null || return 1
  curl --fail --silent --show-error --max-time 20 \
    https://app.109-237-97-85.sslip.io/ >/dev/null || return 1

  if [[ $require_worker == 1 ]]; then
    local worker_id
    worker_id=$(STUDGROUP_IMAGE_TAG=$tag docker compose -f "$COMPOSE_FILE" ps -q ranepa-worker) || return 1
    [[ -n $worker_id ]] || return 1
    for _ in $(seq 1 36); do
      state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$worker_id" 2>/dev/null || true)
      [[ $state == healthy ]] && break
      sleep 5
    done
    if [[ ${state:-} != healthy ]]; then
      echo "SDO-worker did not pass startup health check" >&2
      return 1
    fi
  fi
  return 0
}

if deploy_sha "$target" 1; then
  printf '%s\n' "$target" >"$STATE_DIR/deployed-sha"
  echo "deployed $target"
  exit 0
fi

echo "deploy failed; rolling back to $previous" >&2
if deploy_sha "$previous"; then
  printf '%s\n' "$previous" >"$STATE_DIR/deployed-sha"
  echo "rolled back to $previous" >&2
else
  echo "rollback failed" >&2
fi
exit 1
