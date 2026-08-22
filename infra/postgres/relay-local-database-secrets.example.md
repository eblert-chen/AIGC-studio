# Local Relay database secret inputs

Run `scripts/prepare-relay-local-db-secrets.ps1` to render the development-only
role-admin connection and role passwords under the ignored `deploy/secrets/`
directory. The script defaults match `docker-compose.yml`; pass all four
parameters together when overriding the local PostgreSQL passwords.

The generated files are never release inputs. Secure staging and production
must render independent owner-only files from their secret manager.
