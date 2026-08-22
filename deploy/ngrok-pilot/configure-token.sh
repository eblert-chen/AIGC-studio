#!/usr/bin/env bash
set -Eeuo pipefail

ngrok_config=/etc/ngrok/ngrok.yml
installer=/opt/ai-video/shared/ngrok-pilot/install-server.sh

IFS= read -r ngrok_authtoken
ngrok_authtoken="${ngrok_authtoken%$'\r'}"
if [[ -z "$ngrok_authtoken" || "$ngrok_authtoken" =~ [[:space:]] ]]; then
  printf 'invalid ngrok authtoken input\n' >&2
  exit 1
fi

sudo install -d -o root -g ngrok -m 0750 /etc/ngrok
sudo ngrok config add-authtoken "$ngrok_authtoken" --config "$ngrok_config" >/dev/null
unset ngrok_authtoken
sudo chown root:ngrok "$ngrok_config"
sudo chmod 0640 "$ngrok_config"

"$installer"
sudo systemctl restart ngrok-pilot.service
sudo systemctl is-active --quiet ngrok-pilot.service
printf 'NGROK_TOKEN_CONFIGURED\n'
