#!/usr/bin/env bash
set -Eeuo pipefail

pilot_root=/opt/ai-video/shared/ngrok-pilot
ngrok_config=/etc/ngrok/ngrok.yml
ngrok_policy=/etc/ngrok/pilot-policy.yml

sudo find "$pilot_root/client" -type d -exec chmod 0755 {} +
sudo find "$pilot_root/client" -type f -exec chmod 0644 {} +

if ! command -v ngrok >/dev/null 2>&1; then
  printf 'ngrok is not installed\n' >&2
  exit 1
fi

if ! id ngrok >/dev/null 2>&1; then
  sudo useradd --system --home-dir /var/lib/ngrok --create-home --shell /usr/sbin/nologin ngrok
fi

sudo install -d -o root -g ngrok -m 0750 /etc/ngrok
sudo install -d -o ngrok -g ngrok -m 0750 /var/lib/ngrok

if ! sudo test -f "$ngrok_policy"; then
  pilot_password="$(openssl rand -hex 12)"
  policy_tmp="$(mktemp)"
  trap 'rm -f "$policy_tmp"' EXIT
  sed "s/replace-with-a-long-random-password/$pilot_password/" \
    "$pilot_root/pilot-policy.example.yml" > "$policy_tmp"
  sudo install -o root -g ngrok -m 0640 "$policy_tmp" "$ngrok_policy"
  printf 'PILOT_USERNAME=pilot\n'
  printf 'PILOT_PASSWORD=%s\n' "$pilot_password"
else
  printf 'PILOT_CREDENTIALS=PRESERVED\n'
fi

sudo install -o root -g root -m 0644 \
  "$pilot_root/ngrok-pilot.service" /etc/systemd/system/ngrok-pilot.service
sudo systemctl daemon-reload

if sudo test -s "$ngrok_config" && sudo grep -q 'authtoken:' "$ngrok_config"; then
  sudo chown root:ngrok "$ngrok_config"
  sudo chmod 0640 "$ngrok_config"
  sudo systemctl enable --now ngrok-pilot.service
else
  printf 'NGROK_AUTHTOKEN_REQUIRED\n'
  printf 'SERVICE_INSTALLED_NOT_STARTED\n'
fi
