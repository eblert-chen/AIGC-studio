# Relay PostgreSQL 16 system-semantic baselines

The protected Relay does not accept an arbitrary PostgreSQL server merely
because its major version is 16. Startup and readiness compare a normalized,
security-relevant system-catalog fingerprint with one of the qualified
baselines below. A different image, package build, initdb result, extension
version, or extension schema must be requalified deliberately.

## Qualified baselines

### Alpine rehearsal baseline

- Base image: `postgres@sha256:e013e867e712fec275706a6c51c966f0bb0c93cfa8f51000f85a15f9865a28cb`
- Server: PostgreSQL 16.14 (Alpine)
- Initial extensions: `plpgsql` 1.0 in `pg_catalog`; no `pgaudit`
- System-semantic fingerprint: `sha256:d67b2a78cc769e306723fee1dc7a7282ee4d481b6e2b8353ee1ef1bf81d574eb`
- Intended use: isolated local/fault-injection rehearsal. This does not replace
  the production audit-logging acceptance gate.

### Debian 13 / pgAudit production-candidate baseline

- Base image: `postgres@sha256:95206741a5b214807675e14165369d05b93a9cf692223b616d07cca227e74b0b`
- Server package: PostgreSQL `16.14-1.pgdg13+1`
- pgAudit package: `postgresql-16-pgaudit` `16.1-2.pgdg13+1`
- Required normalized preload set: exactly `auto_explain,pgaudit` (ordering in
  PostgreSQL input is normalized; duplicates, omissions, and extra libraries
  are rejected)
- Initial extensions: `plpgsql` 1.0 and `pgaudit` 16.1, both in `pg_catalog`
- System-semantic fingerprint: `sha256:f97e2f23386ec637defd1cf62f84def8cd76198bfd9e784a1646d1942215b12a`

The final managed image must itself be digest-pinned after installing pgAudit;
the base-image digest and package versions above are provenance inputs, not a
substitute for pinning the resulting deployable image.

### Platform production audit boundary

The protected Platform uses this same Debian 13 / pgAudit fingerprint in
production. Its one-shot database role predecessor must prove that `pgaudit`
is present in the exact normalized `auto_explain,pgaudit` preload set, that the
`pgaudit` 16.1 extension is
installed in `pg_catalog` with its exact member functions and enabled event
triggers, and that `pgaudit.log` covers at least `ddl`, `role`, and `write`.
Every migration/runtime connection also fails closed unless pgAudit is loaded,
those audit classes remain covered, and bind-parameter logging remains disabled
for PostgreSQL, error statements, auto_explain, and pgAudit. The runtime roles
do not receive the broad `pg_read_all_settings` membership; the privileged
shared-preload proof therefore belongs to the predecessor, while each runtime
backend independently proves the registered pgAudit/session policy.

Production PostgreSQL and pgAudit logs must be exported to an access-controlled,
externally immutable (WORM or equivalently append-only) sink with an explicit
retention policy and release evidence linking the sink configuration to the
database image. A managed
provider must supply equivalent preload, extension, audit-class, export,
retention, and access-control evidence. Container stdout and local Docker logs
are fault-injection evidence only; they are never the production audit archive.

## Explicitly unqualified variants

`postgres:16.14-bookworm` (pgdg12) is not equivalent to the Debian 13 baseline.
On the inspected image its stock fingerprint was
`sha256:93d6a6147e4adc3bfe63155d136b2b24e1888a75ee76fe3a111d956340b47506`,
and with pgAudit 16.1 installed in `pg_catalog` it was
`sha256:b61baf7a04909ca733439a0cca6fd6628f793a536051c30f1241b94ac7d1c99f`.
Both must remain rejected unless that exact deployable image is independently
qualified. If production later selects Bookworm and requires pgAudit, qualify
only the pgAudit-enabled fingerprint; do not approve the stock fingerprint as
a shortcut.

## Qualification evidence

Qualification uses the same immutable Relay source and Go toolchain as the
release candidate. At minimum it must prove:

1. exact server and extension versions, schemas, owners, and member sets;
2. the focused `TestRelayPostgres16SystemSemanticBaseline` result;
3. the full fresh PostgreSQL gate, including hostile drift and recovery for
   existing system functions/types, information-schema objects, operator-family
   members, text-search mappings, application catalog definitions, and public
   plus TOAST tablespaces;
4. runtime and download-edge readiness both fail on drift and recover only
   after the exact semantic state is restored; and
5. the final database image RepoDigest, package inventory, test output, and
   Relay source/image identity are stored in the release evidence bundle.

Never add a newly observed hash to the allowlist merely to make startup pass.
