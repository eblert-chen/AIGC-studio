#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/ai-video/current
set -a
# shellcheck disable=SC1091
source /opt/ai-video/shared/secrets/pilot.env
# shellcheck disable=SC1091
source /opt/ai-video/shared/secrets/obs.env
set +a

compose=(
  sudo --preserve-env docker compose
  -f docker-compose.yml
  -f deploy/compose.internal-pilot.yml
)

"${compose[@]}" build \
  platform-api \
  platform-dispatcher \
  platform-relay-sync \
  platform-timeout-worker
"${compose[@]}" up -d --no-deps --force-recreate \
  platform-api \
  platform-dispatcher \
  platform-relay-sync \
  platform-timeout-worker \
  api-gateway

for _ in {1..60}; do
  health="$(sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-video-platform-api-1 2>/dev/null || true)"
  if [[ "$health" == "healthy" ]]; then
    curl --fail --silent --show-error http://127.0.0.1:8200/health/ready
    printf '\nPLATFORM_REBUILD_OK\n'
    exit 0
  fi
  sleep 2
done

sudo docker compose -f docker-compose.yml -f deploy/compose.internal-pilot.yml logs --tail=100 platform-api
printf 'Platform did not become healthy in time\n' >&2
exit 1
