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

rendered_services="$("${compose[@]}" config --services)"
if grep -Eq '^(relay-api|relay-outbox|relay-worker|relay-transfer-worker|relay-provider-sync|relay-provider-monitor|relay-callback-worker)$' <<<"$rendered_services"; then
  printf 'Legacy Python Relay service leaked into the pilot topology\n' >&2
  exit 1
fi

"${compose[@]}" build relay-new-api relay-download-edge
"${compose[@]}" up -d --force-recreate \
  relay-new-api \
  relay-download-edge \
  platform-download-gateway-registration-worker

for _ in {1..60}; do
  health="$(sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-video-relay-new-api-1 2>/dev/null || true)"
  if [[ "$health" == "healthy" ]]; then
    curl --fail --silent --show-error http://127.0.0.1:8300/health/ready
    printf '\nRELAY_REBUILD_OK\n'
    exit 0
  fi
  sleep 2
done

"${compose[@]}" logs --tail=100 relay-new-api relay-download-edge
printf 'Relay did not become healthy in time\n' >&2
exit 1
