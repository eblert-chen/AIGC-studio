# ngrok closed pilot

This deployment publishes only a development-demo frontend through an ngrok
HTTPS endpoint. It intentionally does not proxy `/api/`; the development-header
authenticated Platform, Relay, PostgreSQL, Redis, and OBS credentials remain
private.

The public path is:

`ngrok HTTPS -> HTTP Basic Auth -> 127.0.0.1:8080 -> pilot-web`

The server keeps application ports `8100`, `8180`, and `8200` bound to loopback.
No additional Tencent firewall port is required because the ngrok agent creates
an outbound connection.

Secrets are server-only:

- `/etc/ngrok/ngrok.yml`: ngrok agent authtoken, mode `0600`.
- `/etc/ngrok/pilot-policy.yml`: pilot Basic Auth password, mode `0600`.

Do not put either value in Git, screenshots, chat, a frontend `VITE_*` variable,
or the Docker Compose file.

This is not a production ingress. Before exposing real API data, replace the
development-header authentication and Mock provider, provision individual user
sessions, and deploy the normal production frontend behind the filed domain and
TLS ingress.

`install-server.sh` creates the restricted system user, generates the Basic Auth
password once, installs the hardened systemd unit, and starts it only when a
valid ngrok agent configuration is already present. Re-running the script never
rotates an existing pilot password.

`configure-token.sh` reads the account authtoken from standard input, writes it
directly to the root-owned server configuration, and never prints it. It then
starts the installed service. The token must come from the git-ignored local
secret intake rather than a command-line literal.
