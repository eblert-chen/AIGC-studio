#!/usr/bin/env bash
set -Eeuo pipefail

backup_root=/opt/ai-video/shared/backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="$backup_root/$stamp"
sudo install -d -o root -g root -m 0700 "$backup_dir"

sudo sh -c "docker exec ai-video-postgres-1 pg_dump -U ai_video -Fc ai_video_platform > '$backup_dir/platform.dump'"
sudo sh -c "docker exec ai-video-relay-new-api-postgres-1 pg_dump -U new_api -Fc new_api > '$backup_dir/relay-new-api.dump'"
sudo docker exec ai-video-relay-new-api-redis-1 sh -ec \
  'redis-cli --no-auth-warning -a "$NEW_API_REDIS_PASSWORD" SAVE >/dev/null'
sudo docker cp ai-video-relay-new-api-redis-1:/data/dump.rdb "$backup_dir/relay-new-api-redis.rdb" >/dev/null
sudo sh -c "chown root:root '$backup_dir'/* && chmod 0600 '$backup_dir'/*"
sudo sh -c "cd '$backup_dir' && sha256sum platform.dump relay-new-api.dump relay-new-api-redis.rdb > SHA256SUMS && chmod 0600 SHA256SUMS"

sudo docker restart \
  ai-video-postgres-1 \
  ai-video-relay-new-api-postgres-1 \
  ai-video-relay-new-api-redis-1 >/dev/null
for _ in {1..60}; do
  postgres_health="$(sudo docker inspect --format '{{.State.Health.Status}}' ai-video-postgres-1 2>/dev/null || true)"
  relay_postgres_health="$(sudo docker inspect --format '{{.State.Health.Status}}' ai-video-relay-new-api-postgres-1 2>/dev/null || true)"
  relay_redis_health="$(sudo docker inspect --format '{{.State.Health.Status}}' ai-video-relay-new-api-redis-1 2>/dev/null || true)"
  if [[ "$postgres_health" == healthy && "$relay_postgres_health" == healthy && "$relay_redis_health" == healthy ]]; then
    break
  fi
  sleep 2
done
[[ "${postgres_health:-}" == healthy && "${relay_postgres_health:-}" == healthy && "${relay_redis_health:-}" == healthy ]]

app_containers=(
  ai-video-platform-api-1
  ai-video-platform-dispatcher-1
  ai-video-platform-relay-sync-1
  ai-video-platform-timeout-worker-1
  ai-video-platform-download-gateway-registration-worker-1
  ai-video-relay-new-api-1
  ai-video-relay-download-edge-1
  ai-video-api-gateway-1
)
sudo docker restart "${app_containers[@]}" >/dev/null

for _ in {1..90}; do
  platform_health="$(sudo docker inspect --format '{{.State.Health.Status}}' ai-video-platform-api-1 2>/dev/null || true)"
  relay_health="$(sudo docker inspect --format '{{.State.Health.Status}}' ai-video-relay-new-api-1 2>/dev/null || true)"
  edge_health="$(sudo docker inspect --format '{{.State.Health.Status}}' ai-video-relay-download-edge-1 2>/dev/null || true)"
  if [[ "$platform_health" == healthy && "$relay_health" == healthy && "$edge_health" == healthy ]]; then
    break
  fi
  sleep 2
done
[[ "${platform_health:-}" == healthy && "${relay_health:-}" == healthy && "${edge_health:-}" == healthy ]]

curl --fail --silent --show-error http://127.0.0.1:8200/health/ready
printf '\n'
curl --fail --silent --show-error http://127.0.0.1:8300/health/ready
printf '\n'
curl --fail --silent --show-error http://127.0.0.1:8400/health/ready
printf '\n'
curl --fail --silent --show-error http://127.0.0.1:8180/health/ready
printf '\n'

sudo docker exec ai-video-platform-api-1 alembic current
sudo docker exec ai-video-postgres-1 psql -U ai_video -d ai_video_platform -Atc \
  "select status || ':' || actual_cost_cents from generation_tasks where status='SUCCEEDED' order by created_at desc limit 1" \
  | grep -qx 'SUCCEEDED:10'

for container in "${app_containers[@]}"; do
  state="$(sudo docker inspect --format '{{.State.Status}}' "$container")"
  [[ "$state" == running ]] || {
    printf '%s is %s\n' "$container" "$state" >&2
    exit 1
  }
done

# Application ports must remain loopback-only during the closed pilot.
if sudo ss -lntH | awk '{print $4}' | grep -E '^(0\.0\.0\.0|\[::\]):(8180|8200|8300|8400)$'; then
  printf 'An application port is publicly exposed\n' >&2
  exit 1
fi

printf 'BACKUP_DIR=%s\n' "$backup_dir"
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
free -h
printf 'INTERNAL_PILOT_RESTART_RECOVERY_PASS\n'
