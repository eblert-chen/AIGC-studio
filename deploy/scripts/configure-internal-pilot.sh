#!/usr/bin/env sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: $0 OBS_ENV OUTPUT_ENV" >&2
  exit 64
fi

obs_env=$1
output_env=$2

test -f "$obs_env"
umask 077
cp "$obs_env" "$output_env"

append_random_hex() {
  key=$1
  value=$(openssl rand -hex 32)
  echo "${key}=${value}" >> "$output_env"
}

echo "" >> "$output_env"
append_random_hex POSTGRES_PASSWORD
append_random_hex PLATFORM_BOOTSTRAP_TOKEN
echo "RELAY_CLIENT_ID=customer-platform" >> "$output_env"
echo "RELAY_TENANT_ID=$(cat /proc/sys/kernel/random/uuid)" >> "$output_env"
append_random_hex RELAY_API_KEY
append_random_hex RELAY_OPERATIONS_API_KEY
append_random_hex RELAY_CALLBACK_SIGNING_SECRET
append_random_hex RELAY_ARTIFACT_SIGNING_SECRET
append_random_hex INPUT_ASSET_SIGNING_SECRET
append_random_hex INTERNAL_SERVICE_TOKEN
append_random_hex CHANNEL_COST_SIGNING_SECRET
append_random_hex RELAY_TELEMETRY_SIGNING_SECRET
append_random_hex DOWNLOAD_COMPLETION_EDGE_GATEWAY_SIGNING_SECRET
append_random_hex DOWNLOAD_COMPLETION_OBS_ACCESS_LOG_SIGNING_SECRET
echo "PUBLISHING_WORKER_ENABLED=false" >> "$output_env"

chmod 0600 "$output_env"

required_keys='POSTGRES_PASSWORD PLATFORM_BOOTSTRAP_TOKEN RELAY_CLIENT_ID RELAY_TENANT_ID RELAY_API_KEY RELAY_OPERATIONS_API_KEY RELAY_CALLBACK_SIGNING_SECRET RELAY_ARTIFACT_SIGNING_SECRET INPUT_ASSET_SIGNING_SECRET INTERNAL_SERVICE_TOKEN CHANNEL_COST_SIGNING_SECRET RELAY_TELEMETRY_SIGNING_SECRET'
for key in $required_keys; do
  count=$(grep -c "^${key}=" "$output_env")
  if [ "$count" -ne 1 ]; then
    echo "invalid generated environment key: ${key}" >&2
    exit 65
  fi
done

echo "internal pilot secrets generated"
