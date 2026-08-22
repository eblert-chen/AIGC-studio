# Extended new-api staging/production gate

This is the release gate for the extended new-api Relay. It is a deployment
template, not evidence that a Provider, Huawei OBS, or an operator alert sink
has passed. The committed environment examples are deliberately non-runnable:
their zero provenance, empty routes/rates, `.invalid` URLs, and
`replace-with-*` secrets must be replaced from an approved secret manager.

## Mandatory previous-candidate schema gate

Every release that can migrate an existing new-api database must pass the
named `make test-relay-schema-legacy-pg16` gate before an image is promoted or
any deployment Compose file is applied. This is not an optional developer
smoke test. It boots two isolated PostgreSQL 16.14/pgaudit 16.1 TLS clusters
from the same pinned image, creates the exact raw legacy schema with the
immutable previous-candidate image, and inserts only synthetic secret-free
fixtures. Raw/unversioned state is first converted by the immutable schema-v1
source revision `709e9b45b25a6baa415ab985078bd7764a35eaf9`; only after that test
records an explicit, non-skipped PASS may the frozen historical v2 contract
execute its v1-to-v2 no-catalog-delta bridge. The current v3 image then executes
only the versioned v2-to-v3 credential-order correction. The current image must
never replay its live v1 or frozen v2 bootstrap against raw legacy state. The
immutable-v1 stage creates only the
exact schema, ledger, catalog, and protected roles. The synthetic legacy
candidate already contains one exact production-shaped root and its setup
marker; immutable v1 must preserve both byte-for-byte while also preserving
the ordinary user and credential fixtures. It must create no service-principal
rows, and no protected API or edge runtime may start at v1.
The gate compares the catalog with an independently bootstrapped fresh-v3
database, verifies the complete business-row evidence and intended v1 data
transforms, and decrypts the migrated synthetic credentials through the
production vault boundary. After the historical v2 bridge and current v3
correction it compares every legacy fixture and encrypted credential row with
its pre-bridge digest, then runs the complete
proof -> exact-root replay (`unchanged`) -> principal creation -> API lifecycle
on that same legacy database. The
fresh reference database cannot substitute for this same-database terminal
proof.

The currently frozen input is
`ai-video/new-api-relay@sha256:142185d134d0427cc073e7235a5bb10c248d5eabad1c1e737abdf83e56c611e6`.
The script also pins and prints its OCI/source evidence: candidate ID,
RepoDigest, source revision, upstream revision, source-snapshot SHA-256, and
source file count. The script also pins the qualified PostgreSQL image, the v1
and max-v2 source revisions, and the digest of a test-only TLS/side-effect assertion patch; that
patch changes no v1 production declaration or behavior. Archive those lines
together with explicit test PASS events and
`fresh-v3-row3-only-gate=PASS`, `legacy-to-v1-gate=PASS`,
`v1-compatible-no-runtime-side-effects=PASS`,
`historical-frozen-v1-to-v2-no-catalog-delta-gate=PASS`,
`v2-to-v3-one-shot-gate=PASS`, `exact-v1-to-v3-ledger-gate=PASS`,
`post-v3-proof-root-principal-api-current-gate=PASS`,
`max-v2-ahead-no-direct-rollback-gate=PASS`, and
`legacy-schema-upgrade-gate=PASS`. The runner parses `go test -json` and treats
an absent test event or `skip` as failure for the schema, post-v3 lifecycle,
and both rotation tests; process exit zero alone is not PASS.
A missing image, missing or changed label/revision/fixture digest, wrong
PostgreSQL/pgaudit/TLS identity, partial old-image startup, catalog drift,
fixture drift, or skipped test is a release failure. Never replace this with an
unset test DSN that silently skips the Go acceptance test.
When a new candidate becomes the supported predecessor, update the pinned
image and all provenance values in one reviewed change and rerun this gate.

The ordinary `docker-compose.yml` and every protected overlay run new-api as
the only active Relay. Root/secure Compose defines no Python service, profile,
secret, dependency, or volume. Protected Platform accepts exactly one backend,
`new-api-v1 / generations.v1`; historical `legacy-default-v1` affinity remains
read-only audit data and has no callable production client. Python source may
run only as an isolated offline contract oracle with test credentials.

## Render the intended topology

Create an untracked rendered environment by combining
`deploy/relay-secure.env.example` with exactly one of the staging/production
examples and replacing every placeholder. Then render the same file set used
for deployment:

```powershell
# Select exactly one: staging or production.
$relayEnvironment = 'production'
$relayCompose = @(
  '--env-file', 'deploy/relay-secure.env',
  '--env-file', "deploy/relay-$relayEnvironment.env",
  '-f', 'docker-compose.yml',
  '-f', 'deploy/compose.relay.secure.yml',
  '-f', "deploy/compose.relay.$relayEnvironment.yml"
)
docker compose @relayCompose config --quiet
```

Run `config --services` with the same arguments. It must include
`relay-new-api-secret-isolation`,
`relay-new-api-db-role-pre`, `relay-new-api-migrate`,
`relay-new-api-db-role-post`, `relay-new-api-service-principal-provision`,
`relay-new-api-volume-init`, and `relay-new-api`,
and must not include `relay-api`, `relay-outbox`,
`relay-worker`, `relay-transfer-worker`, `relay-provider-sync`,
`relay-provider-monitor`, or `relay-callback-worker`. Starting one of the
`secure-state-*` profiles is a separate rehearsal action, not part of the
managed-state deployment.

Run `npm run test:relay-secret-paths` in the checked-out release worktree before
building. The gate fails if Git tracks any path under `deploy/secrets/` or
`infra/postgres/local/`, including a path added with `git add -f`. The candidate
OCI build context is exactly `backend/new-api-relay`; the source and harness
snapshots use explicit allowlists and must contain neither directory. A source
tree without Git metadata is not a releasable tree: the tracking gate must fail
closed rather than infer safety from `.gitignore`.

### Managed-state network, TLS, and secret files

Create `NEW_API_RELAY_MANAGED_STATE_NETWORK` before Compose. It is an external
VPC network used by the role pre/post jobs, migrator, root provisioner, API and
download edge to reach managed PostgreSQL (and only the state endpoints each
service needs). It is not the public edge network. Enforce egress at the
network/firewall layer to the exact PostgreSQL/Redis addresses and ports, then
record a positive PostgreSQL probe and a rejected probe to an unapproved target
as deployment evidence.

The protected PostgreSQL roles are cluster-global, so the Relay database must
use a dedicated PostgreSQL cluster/instance, not merely a separate database in
a shared cluster. Before the first pre job, the infrastructure owner must revoke
PUBLIC `CONNECT`, `CREATE`, and `TEMPORARY` on every non-Relay database
(including `postgres`, `template0`, and `template1`) and prove the four Relay
roles do not yet exist. Pre creates them with a server-owned comment binding
each name to the exact application database name and OID. A same-named role
without that marker, any protected role access to or ownership of another
database, or any cross-database object/ACL dependency fails pre and runtime
readiness. Pre never repairs or takes over another database; external-database
ownership inspection remains an infrastructure acceptance item for managed
services that restrict `pg_shdepend` visibility.
The accepted PostgreSQL 16 system-semantic fingerprints are bound to exact
image, package, and extension provenance in the
[PostgreSQL 16 system-semantic baseline record](relay-postgres16-system-semantic-baselines.md).
An unlisted flavor or package set is a release failure, not an implicit new
baseline.

All four Relay PostgreSQL URLs are secret-manager-rendered regular files:
role-admin, migration, runtime, and download-edge. Secure staging and production
set `RELAY_DATABASE_SECRET_FILES_REQUIRED=true`; a raw `SQL_DSN`,
`RELAY_DOWNLOAD_EDGE_SQL_DSN`, `service`, or `servicefile` source is rejected.
Each URL must contain exactly one `sslmode=verify-full` and exactly one
`search_path=public`. Only the migration URL may contain `options`, exactly
`-c role=<configured NOLOGIN schema owner>`, and it must authenticate as the
configured migration login. Startup also proves the current session appears in
`pg_catalog.pg_stat_ssl`; `sslmode=require`, `verify-ca`, duplicate parameters,
and hostile `options` fail before serving. The protected URI query is an exact
allowlist: `sslmode` and `search_path`, the migration-only `options`, and an
optional single absolute clean `sslrootcert` path. Driver overrides and secret
sources such as `password`, `passfile`, `sslpassword`, `sslkey`, `host`, `user`,
unknown keys, duplicates, and present-empty values are rejected by the same
parser used by the isolation validator and every database consumer.

The role-admin DSN password plus the migration, runtime and edge database
passwords are four pairwise-distinct 32–128 byte
base64url strings (`A-Z`, `a-z`, `0-9`, `_`, `-`; no `=`, Unicode, whitespace,
or newline). The same immutable image derives PostgreSQL SCRAM verifiers in
memory. Raw passwords and DSNs never enter Compose environment, argv, JSON
receipts, or evidence. The role-admin transaction suppresses standard statement,
parameter and error-statement logging before sending a verifier. Managed
PostgreSQL must additionally configure audit extensions so password-management
statements and bind parameters are not audited; if that policy cannot be
proved, provisioning is blocked.

Bind mounts must be regular, non-symlink and owner-only. On Linux the mounted
file must appear inside the container with exact mode `0400` or `0600` and be
owned by the effective uid; a host ACL is acceptable only when it produces
those exact container-visible attributes. Mode bits alone are insufficient under rootless Docker or user
namespace remapping. Before every one-shot, run its final image/user with an
entrypoint that performs only `test -f`, `test ! -L`, `test -r`, and
`test ! -w` on every mounted secret and prints only `PASS`. Pre, migrate, post,
API, edge and root run as uid 10001; repeat the check separately for each
service because their mount sets differ. A missing or unreadable bind is a
deployment failure, never permission to copy the value into environment.

### Typed Relay secret bundles

The three new-api secret bundles have committed, secret-free Draft 2020-12
schemas. The schemas describe shape only; they do not contain a `default`, an
`example`, or any value that can boot a service:

- [service principals (A)](schemas/relay-service-principals.schema.json)
- [API runtime secrets (B)](schemas/relay-api-runtime-secrets.schema.json)
- [download-edge runtime secrets (C)](schemas/relay-download-edge-runtime-secrets.schema.json)

Have the approved secret manager generate all raw values independently with a
cryptographically secure random source and render compact UTF-8 JSON directly
to owner-only files. Do not start from a checked-in value, copy a value between
fields, paste the document through a shell argument, or archive the rendered
JSON as evidence. The Go readers reject a relative or non-clean path, symlink,
non-regular file, wrong owner, mode other than `0400`/`0600`, writable mount,
oversize input, duplicate JSON key, unknown field, trailing document, control
byte, placeholder, and low-diversity secret. The opened file descriptor and a
second path stat must identify the same inode. On Linux the opened filesystem
must itself report read-only; changing a writable file to mode `0400` is not a
substitute for a read-only bind mount.

Bundle A has `kind=relay_service_principals`, `schema_version=1`, and a
non-empty `principals` array sorted by strictly increasing `client_id`. Each
item is exactly `client_id`, canonical lowercase tenant UUID, and a canonical
`sk-` plus 48-alphanumeric upstream token. Client, purpose, reserved user, and
token identities are unique. Only the principal one-shot and API receive A.

Bundle B has `kind=relay_api_runtime_secrets`, `schema_version=1`, a passworded
`rediss://` DSN, `application` session/crypto secrets, and a `clients` array in
the exact same client/tenant order as A. A client has an API key and either both
callback URL/signing secret or neither. `operations_credentials` are sorted by
tenant then lowercase SHA-256 of caller-owned raw operations tokens;
`reconciliation_approval_keys` are sorted by tenant then key id. Both arrays
cover every client tenant. B also contains internal-admission, artifact,
Huawei OBS, provider-alert, Platform-internal, channel-cost, and telemetry
secrets. Only the API receives B; the principal job does not.

Redis trust is a fourth, API-only protected input rather than an ambient TLS
override. Secret Manager renders `NEW_API_RELAY_REDIS_TLS_CA_FILE` as one
owner-only canonical PEM bundle. The global, fresh-root, and rotation
networkless validators bind its exact bytes into the API runtime commitment;
only the long-lived API mounts the same file and installs the committed roots
directly into the `rediss://` client with TLS 1.2 or newer and endpoint SNI.
`SSL_CERT_FILE`, `SSL_CERT_DIR`, and other process-wide trust overrides remain
forbidden. Pre/migrate/post, principal, root, edge, and the rotation database
job never receive the Redis CA.

Bundle C has `kind=relay_download_edge_runtime_secrets`, `schema_version=1`,
five independent text secrets (registration token/signature, scoped Platform
edge-completion token/signature, and proof-read token), plus three canonical
padded Base64 values. Each Base64 value decodes to exactly 32 independently
generated bytes: ticket HMAC key, source AES key, and an Ed25519 seed. Do not
store a 64-byte Ed25519 private key in C. Only the download edge receives C.

The strict parsers additionally enforce ordering, tenant coverage, A/B identity
equality, canonical encoding, and within-bundle/cross-A+B digest independence;
these relations cannot be expressed completely by JSON Schema. A schema
validator is useful only as an early authoring check and never replaces the
same-image fail-closed parser. Render A, B, and C with the same atomic secret
release, set the three `NEW_API_RELAY_*_FILE` host paths, mount them read-only,
and retain only the secret-free one-shot/readiness receipts. A parse error is a
release failure; do not weaken a field, switch back to a raw environment value,
or print the document to diagnose it.

The networkless same-image `relay-new-api-secret-isolation` one-shot is the
cross-process boundary for that release. It receives A, B, C, the Provider KEK,
the dedicated Redis CA, both pinned database CA files, all four Relay PostgreSQL DSN files, the three
Relay role-password files, all seven typed Platform process bundles, the
Platform role-admin DSN, and all seven Platform role-password files, but no
network.
It compares canonical and bare service-token forms, raw and decoded Redis
passwords, every API text secret, operations-token SHA-256 values, edge text
secrets, encoded and decoded edge keys, encoded and decoded KEKs, and decoded
database passwords. It also proves the exact A/B/Platform caller identities,
tenant and callback binding; operations and approval credentials; Relay sink
signatures; edge completion credentials; the three-party gateway registration
binding; the API/worker attempt key; and each API/publishing-worker adapter
credential pair. Platform API and dispatcher OBS credentials are deliberately
separate IAM identities and equality is rejected. Every unlisted
representation must be globally distinct. The only database-password
equalities are each Relay migration/runtime/edge password file and each of the
seven Platform role-password files with the password decoded from its matching
DSN; both role-admin passwords are independent. The validator also binds each
domain's normalized database endpoint and committed CA bytes, keeps the Relay
and Platform database anchors distinct, and binds every credential-bearing
HTTP endpoint to the release-owned Platform, Relay, or download origin and its
fixed path.

Before checking the current files, the validator durably removes all old
receipts and the shared commit marker. It commits one `0400` receipt into each
of fourteen separate named volumes: Relay `pre`, `migrate`, `post`,
`principal`, `api`, and `edge`, plus Platform `db-role-pre`, `migration`,
`platform-api`, `dispatcher`, `relay-sync`, `timeout-worker`,
`publishing-worker`, and `download-gateway-registration-worker`. Only after all
fourteen receipt renames and directory syncs succeed does it atomically publish
the value-free schema-v2 commit marker that binds their hashes, one random run
id, the exact release, and the root-proof generation. A kill after any strict
source read or receipt write leaves no reusable marker. A receipt contains the
immutable image/source identity plus commitments to the complete bytes and the
semantic representations of only that consumer's mounted sources. It contains
no raw value, path, tenant, client, digest on stdout, or reusable credential.
Every consumer mounts only its own receipt volume read-only and recomputes the
commitment before opening a logger, database, Redis client, HTTP client, or
listener. A successful comparison atomically installs copies of those exact
strict-read bytes as the process's immutable protected-file snapshots; all
subsequent A/B/C, KEK, DSN, Redis/database CA, and role-password loaders consume the snapshots
and do not reopen the bind mount. PostgreSQL clients build their TLS root pool
from the same committed CA bytes rather than reopening `sslrootcert`. Replacing
a JSON document, DSN host/user/query, CA, password, KEK, image, or source after
validation therefore cannot substitute different bytes for use. Never mount
A/B into the edge or C into the API merely to perform this comparison.

### Typed Platform process secrets

The customer Platform has a separate closed Draft 2020-12 contract:
[Platform process runtime secrets](schemas/platform-process-runtime-secrets.schema.json).
It is a role-discriminated document with
`kind=platform_process_runtime_secrets`, `schema_version=1`, one exact
`process_role`, and that role's exact `secrets` object. Secret Manager renders
seven independent files: DB-only `migration`, plus `platform-api`,
`dispatcher`, `relay-sync`, `timeout-worker`, `publishing-worker`, and
`download-gateway-registration-worker`. A file for one role cannot start any
other role.

The secure overlay completely replaces the inherited Platform environment for
every process. It never injects `DATABASE_URL`, Relay API keys, service tokens,
HMAC/AES/JWT material, or OBS credentials. Each process receives only
`PLATFORM_PROCESS_ROLE`, its one
`PLATFORM_PROCESS_RUNTIME_SECRETS_FILE`, and non-secret policy/endpoints. The
Platform reader applies the same absolute clean path, no-symlink, owner UID,
`0400`/`0600`, inode-stability, bounded-size, and Linux read-only-filesystem
checks as Relay. Its JSON decoder rejects leading/trailing bytes, duplicate
keys at every depth, unknown fields, missing fields, role substitution, weak
or placeholder secrets, and non-canonical AES keys. A raw secret environment
variable is a startup error even when its value is empty.
`ENVIRONMENT=production` or `ENVIRONMENT=staging` always activates this
bootstrap. `PLATFORM_PROTECTED_RUNTIME` cannot downgrade either environment:
when the variable is present there, its only accepted value is the exact string
`true`; `false`, `0`, an empty value, or a malformed value fails before the
secret file, Settings, logging, or database initialization.

Every Platform process receives only its own read-only isolation receipt and
verifies the same closed nine-field Relay/Platform release identity before it
parses the exact already-read source bytes. The role-pre one-shot receives only
the role-admin DSN, seven role-password files, and Platform CA; migration and
the six long-lived processes each receive one typed bundle and the CA. All
eight consumers use the same `PLATFORM_IMAGE` value, which must be a complete
digest-pinned image reference;
the image also embeds the exact Platform source revision and source-snapshot
digest. A receipt/file/release mismatch fails before database, logging, Redis,
HTTP client, worker, or listener initialization.

The process boundaries are deliberate:

- migration receives only its PostgreSQL DSN; `platform-api` waits for that
  one-shot and runs Uvicorn without an embedded Alembic shell step;
- dispatcher receives DB, Relay caller credentials, and Platform-scoped OBS
  credentials. Huawei OBS creates its own short-lived signed object URL, so the
  API and dispatcher do not receive or construct the filesystem-only input
  signer in protected deployments; the raw signer environment name remains
  rejected to prevent a latent over-grant;
- relay-sync and timeout-worker receive only DB and Relay caller credentials;
- the Download Gateway registration worker receives DB, its scoped bearer,
  registration HMAC, and attempt AES key;
- publishing-worker receives DB and an exact adapter/media-resolver credential
  manifest. In production every named factory must accept the explicit
  keyword-only `credential_manifest`; a legacy no-argument factory, a missing
  spec, or an extra spec fails closed before the database engine is created.
  `platform-api` receives only the adapter subset it needs for account/OAuth
  operations. Keep the publishing profile disabled until real production-ready
  factories implement this contract.

Run the same-image parser against every rendered file, then inspect the fully
rendered Compose model to prove each Platform service has exactly one read-only
secret bind and none of the rejected raw names. Evidence may contain role,
schema version, image digest, and file digest; it must never contain a DSN,
credential, plugin manifest value, or exception text from a third-party
library.

### Mandatory database lifecycle sequence

The API and edge hold shared PostgreSQL process-gate and mutation-fence advisory
locks for their entire database-access lifetime. First remove every API and edge
replica from load-balancer admission and wait for accepted work to drain. Then
stop every old instance with the same rendered Compose arguments (or the
equivalent all-replica orchestrator action):

```powershell
docker compose @relayCompose stop --timeout 260 relay-new-api relay-download-edge
```

Confirm both containers are exited. The 260-second Compose budget covers the
API's two normal 120-second drain windows; lifecycle-lock loss still uses the
separate 10-second hard exit path. Cancel and join all database workers, close
their pools, and
prove no shared holder remains. Do not start a role or migration one-shot while
an old runtime is still draining: the exclusive lock is bounded and a timeout
must fail without changing roles, schema state, or ACLs.

From the managed PostgreSQL administrator console, run this exact read-only
check after the old API and edge containers have exited. The Relay lifecycle
keys are process gate A `0x41564944524f4f4c` and mutation fence B
`0x41564944524f4f4d`; PostgreSQL exposes their high and low 32-bit halves in
`classid` and `objid`:

```sql
SELECT objid, count(*) AS relay_lifecycle_lock_holders
FROM pg_catalog.pg_locks
WHERE locktype = 'advisory'
  AND classid = 1096173892
  AND objid IN (1380929356, 1380929357)
  AND objsubid = 1
  AND granted
GROUP BY objid
ORDER BY objid;
```

The only acceptable result before `pre` is no rows (both A and B have zero
holders). Save the empty result with the release evidence; do not print
connection strings or role passwords.

Run every one-shot with `--force-recreate`; Compose may otherwise reuse a
previously completed container and stale receipt. The initializer assigns the
fourteen ordinary consumer receipt volumes, the install-only root-bootstrap
receipt volume, the shared commit-marker volume, and the permanent root-proof
volume. It also prepares two non-secret database-release-proof volumes: only
the corresponding Relay or Platform role predecessor writes its attestation;
all migrations and long-lived consumers mount that proof read-only. Using the
exact rendered file set and immutable image digest, choose exactly one of the
following sequences.

For a fresh protected staging or production database, the proof file must be
truly absent on its fixed read-only mount. Use the explicit profile only for the
pre-root validator, root validator, and root provisioner:

```powershell
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-volume-init relay-new-api-volume-init
docker compose @relayCompose --profile relay-root-provision up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-secret-isolation-pre-root relay-new-api-secret-isolation-pre-root
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-db-role-pre relay-new-api-db-role-pre
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-migrate relay-new-api-migrate
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-db-role-post relay-new-api-db-role-post
docker compose @relayCompose --profile relay-root-provision up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-root-secret-isolation relay-new-api-root-secret-isolation
docker compose @relayCompose --profile relay-root-provision run --rm --no-deps relay-new-api-root-provision
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-secret-isolation relay-new-api-secret-isolation
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-db-role-pre relay-new-api-db-role-pre
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-migrate relay-new-api-migrate
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-db-role-post relay-new-api-db-role-post
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from platform-db-role-pre platform-db-role-pre
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from platform-migrate platform-migrate
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-service-principal-provision relay-new-api-service-principal-provision
docker compose @relayCompose up -d relay-new-api relay-download-edge platform-api platform-dispatcher platform-relay-sync platform-timeout-worker platform-download-gateway-registration-worker
```

The root validator revokes every pre-root receipt and its shared marker before
atomically creating the permanent proof. The immediately following ordinary
validator must therefore publish a new `root-proof-present` generation before
any post-root process. The Relay role predecessor, migrator, and role-post job
must then run a second time so the Relay database release proof is republished
against that exact post-root marker. Only after those three jobs succeed may
Platform role provisioning, Platform migration, service-principal provisioning,
or any long-lived process run. Destroy the rendered root password file after
the root receipt and database audit state are verified; the ordinary validator
uses the permanent proof, not the raw password.

For every ordinary rollout or rollback after that first install, never select
the root profile and never run the root command. Recreate the proof-present
validator before the database sequence:

```powershell
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-volume-init relay-new-api-volume-init
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-secret-isolation relay-new-api-secret-isolation
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-db-role-pre relay-new-api-db-role-pre
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-migrate relay-new-api-migrate
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-db-role-post relay-new-api-db-role-post
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from platform-db-role-pre platform-db-role-pre
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from platform-migrate platform-migrate
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-service-principal-provision relay-new-api-service-principal-provision
docker compose @relayCompose up -d relay-new-api relay-download-edge platform-api platform-dispatcher platform-relay-sync platform-timeout-worker platform-download-gateway-registration-worker
```

Here `@relayCompose` represents the two rendered env files and the base,
secure, and one environment overlay shown above; do not substitute another
image. `--no-deps` is safe only in this displayed sequence: continue to the
next line only when the previous one-shot exits zero. `--abort-on-container-exit`
and `--exit-code-from` make every non-zero one-shot a release failure. The final
detached start intentionally reuses the successful predecessor containers so
the normal Compose dependency conditions remain enforceable.
The isolation validator stdout is exactly one secret-free JSON object with
`kind=relay_secret_isolation`, `schema_version=1`, `state=validated`, and
`consumers=14`; it deliberately contains no commitment digest, run id, proof id,
or source path. Archive that receipt and the fourteen consumer
verification/startup results, not the receipt or marker volume contents. Never
use `docker compose up` alone as proof that a prior completed validator
corresponds to the current host files.
Service-principal exact replay is the only bootstrap predecessor that runs on
every release. It requires an already-complete Setup/root state and fails
closed before API startup if root is absent or the reserved principal set is
partial or different.
The pre command is native code in that same image.
Under one exclusive lock and one transaction it creates/normalizes the protected
owner, migrator, runtime and edge roles, takes over known legacy public objects,
and installs three independent SCRAM verifiers
(migrator, runtime and edge), and establishes edge state C (edge NOLOGIN, zero
ACL).
It receives no KEK, Redis, Provider, OBS, caller, or application-root secret.
The migrator accepts C only for a real change/resume, commits the versioned
schema, catalog fingerprint and runtime/edge manifests, and leaves edge state B
(NOLOGIN, exact current ACL). Post accepts exact B or an already-completed A,
does not mount or receive any password, enables the already-provisioned edge
login, verifies exact A (LOGIN, exact ACL), and is
idempotent after a lost commit acknowledgement. API readiness additionally
requires A; neither B nor C can serve.

On an ordinary same-version redeploy the migrator accepts only exact A, B, or C:
A is a no-op; B is the committed/recoverable state for post; C is rebuilt to B
from the versioned manifest. Any partial or extra edge ACL, unexpected role
membership, schema/catalog drift, dirty mismatch, ledger gap, or unknown state
fails closed. Use `relay-schema-status` to record the secret-free classification
and durable attempt ID. Resume only with `relay-migrate --resume <attempt-id>`
from the identical migration artifact. Never edit the ledger/state manually.
If schema commit succeeded but the CLI acknowledgement was lost, rerun the same
migrator: it reconciles clean ledger/catalog state without overwriting it as
failed, then rerun post. Preserve the failed database and logs for diagnosis;
do not run another image against a dirty attempt.

The native new-api database contract for this release is
`target=3,min=1,max=3`. Schema v1 contains the irreversible plaintext-vault
cleanup and write guards and remains compatible only for migration diagnosis;
protected post, service-principal provisioning/rotation, root bootstrap, API,
download edge, database-release proof consumers, and runtime readiness all
require exact Current v3. Role-pre and the migrator proof path are the only
release steps allowed to inspect compatible v1/v2 so they can perform the bridges.

A fresh v3 bootstrap executes the independently frozen v3 source/model
snapshot and records `from=0`, `baseline=current=target=3`, with exactly one
ledger row `[3]`. It must not fabricate version-1 or version-2 events. An exact
v1 database is accepted only when its state, single v1 ledger row, v1 catalog,
runtime manifest, role topology, and pre-migration edge surface are exact. The
frozen historical v1-to-v2 bridge executes no catalog DDL, preserves the v1
ledger row byte-for-structure, and appends the real v2 event. The current
v2-to-v3 correction then preserves both historical rows, appends the real v3
event, and finishes with `baseline=1,current=target=3` and ledger `[1,2,3]`.
The frozen PostgreSQL catalog digest is deliberately unchanged across v1, v2,
and v3. The independently frozen v2 source artifact is
`sha256:03de3ed038c3a9f7b6e160ac720e4350b9d468c09417cdc9e280289ed390fef2`
and its migration checksum is
`sha256:a3dc154ca42086544096cc0c3e3f2c84479e52e2ad76bd4d32aa2806c2c9af0e`;
the v3 source artifact is
`sha256:4d784286e5480a10a83f4408b303eec075a347fa405d45650e12c19425e4659d`
and its migration checksum is
`sha256:0295d36ca5032088cc2e0b3b7f935aaeb24c3c5847a6b0a92a4dc3099d58e553`.
The source closure includes the top-level migration and Current/Compatible
gates, while sentinel tests prove that fresh v3 and exact-v1-through-v3
orchestration execute neither the live v1 definition nor the frozen v2
implementation.
Dirty, unversioned, partial, ahead, unknown, catalog-drifted, ACL-drifted, or
ledger-gap state remains fail-closed.

The safe rollback window for backup restore ends before production traffic and
before accepting new v3 work. After migration, never boot an older max-v2
image: it must classify Current v3 as `ahead` and fail closed. A failed feature
rollout may return only to a v3-compatible image; max=1 and max=2 images are not
direct rollback targets. Existing new-api jobs remain on the
new-api worker/affinity path until drained; historical Python-bound rows must
have reached a reconciled business terminal state before the one-time cutover
and cannot be called from protected runtime. The `schema_version=1` fields in generation,
secret-bundle, receipt, and CLI JSON envelopes are protocol versions and must
not be changed to 3 merely because the database contract is v3.

Before starting application processes, upgrade the customer Platform database
to `0040_showcase_management`. Its direct predecessor is
`0039_new_api_relay_defaults`; the frozen `0038_download_evidence_checks` and
`0037_production_auth_lifecycle` revisions remain in the migration chain. Revision
0040 adds the owner-only homepage showcase draft, immutable releases, and unpublish
journal. Revision 0039 still changes only the server defaults for new task/outbox
affinity and never rewrites historical rows. The protected v5 catalog fingerprint
must be `ecd5b3faae20595e66396c59d37327d1e6e5b742c3d70697aaf6f109866591e6`.
Use the dedicated `platform_migration` login;
the download edge keeps its dedicated DML-only role and never runs migrations.

### Non-root volume upgrade

The long-lived Relay runs as uid/gid `10001:10001`. Older releases could leave
legacy root-owned named volumes at `/data`, `/app/logs`, and `/artifacts`.
`relay-new-api-volume-init` is therefore a mandatory one-shot predecessor: it
runs from the same immutable image with only `CHOWN`, `FOWNER`, and
`DAC_OVERRIDE`, repairs ownership, and exits before `relay-new-api` starts.
Do not remove this dependency or run the main Relay as root as a workaround.
An init failure is a deployment failure; inspect the exact volume and repair
policy before retrying.

### Protected application-root provisioning

Provision the new-api application root before the first protected staging or
production API start.
The `relay-new-api-root-provision` service is a manual one-shot in the
`relay-root-provision` profile. No long-lived service depends on it, so an
ordinary `config`, `up`, restart, or upgrade never runs it automatically. It
uses the exact same immutable image as `relay-new-api`, runs as uid/gid
`10001:10001` with a read-only filesystem, all capabilities dropped, and only
the controlled managed-state network attached for PostgreSQL access. It has the
runtime DML-only DSN and one-time application password, but no migration DSN,
schema-owner credential, Provider KEK, Redis, OBS, or caller credential.

The profile first runs the networkless
`relay-new-api-root-secret-isolation` command. It reads the complete ordinary
global source set and the one-time root password once, rejects every
cross-domain representation reuse, and atomically creates or exactly replays a
validator-only permanent forbidden-root proof. A different root password
conflicts. The proof's `created_release` is audit metadata; its random proof id
and forbidden password digest deliberately survive image/source upgrades and
rollbacks after the raw root file is destroyed. Neither stdout, an ordinary
consumer receipt, nor a long-lived container receives the digest. The root
receipt binds the proof, exact root bytes and username, runtime DSN, and Relay
CA; the root process verifies and consumes those same immutable snapshots.

The fixed empty `.proof.lock` inode serializes pre-root validation and proof
creation across the read-only and read/write aliases of the same named volume.
The root validator proves those aliases are the same opened directory before
revoking anything. Lock acquisition has a ten-second deadline and fails before
writing a marker, receipt, proof, or database row. Before proof creation the
root validator durably removes the pre-root marker and all fourteen receipts;
it then creates `proof.json` with no-replace semantics, so two different
concurrent roots cannot overwrite one another. A killed exact attempt can be
resumed with the same root password and cannot be resumed with a different one.

Set `NEW_API_RELAY_ROOT_USERNAME` to 1-20 ASCII bytes: the first byte must be a
letter or digit and the remainder may contain only letters, digits, `.`, `_`,
or `-`. Have the approved secret manager atomically render
`NEW_API_RELAY_ROOT_PASSWORD_FILE` as a regular file containing exactly the
password bytes: 32-72 valid UTF-8 bytes, with no byte-order mark, NUL/control
character, leading/trailing whitespace, or trailing newline. Do not create it
with `echo`, place it in a shell command/history, commit it, or put its contents
in an environment variable. Compose mounts that host file read-only at
`/run/secrets/relay-new-api-root-password`; only the file path and username are
visible in rendered configuration. On the production Linux host, the rendered
source file must be owned by the deployment identity that maps to container
uid/gid `10001:10001`, with POSIX mode `0600` or an equivalent Windows ACL.
Docker Compose file-source secrets are bind mounts and may ignore Compose
`uid`, `gid`, and `mode`, so the overlay deliberately does not claim that those
attributes are enforced. Before provisioning, inspect the rendered service and
prove inside the one-shot container that the mounted file is readable but not
writable as uid 10001. It must be mounted only into the provisioner; the
long-lived API must not receive it.

Run this no-content permission preflight with the same selected environment
file set. It does not print, hash, or copy the password:

```powershell
docker compose @relayCompose --profile relay-root-provision run --rm --no-deps --entrypoint /bin/sh `
  relay-new-api-root-provision -ec `
  'test "$(id -u)" = 10001 && test -r /run/secrets/relay-new-api-root-password && test ! -w /run/secrets/relay-new-api-root-password'
```

With exactly one protected staging or production inventory selected, run the
root validator and then only the provisioner. These are the same two commands
shown in the fresh sequence above:

```powershell
docker compose @relayCompose --profile relay-root-provision up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-root-secret-isolation relay-new-api-root-secret-isolation
docker compose @relayCompose --profile relay-root-provision run --rm --no-deps relay-new-api-root-provision
```

The CLI requires `APP_ENV` and `DEPLOYMENT_ENV` to be identical and exactly
`staging` or `production`, `NODE_TYPE=master`, and a file-sourced PostgreSQL
URL; SQLite, development, mixed-environment and raw-secret inputs fail closed.
Both protected environments disable anonymous `/api/setup` before any database
access. Neither staging nor production has an HTTP setup fallback. The
secure deployment inventory separately requires managed PostgreSQL with
`sslmode=verify-full`; an isolated local concurrency test may use a disposable
non-TLS PostgreSQL instance but is not production deployment evidence. The CLI
first requires an already-current schema and exact runtime role, then takes
process gate A before any root DML. The root insert and Setup marker commit in
one transaction under mutation fence B. It performs no schema or ledger
mutation. Concurrent one-shots against an empty database therefore serialize
the complete root transaction.

A successful first run returns secret-free JSON with `state=created`. An exact
retry before any login or authentication enrollment returns `state=unchanged`
without changing the user id, password hash, creation time, or Setup marker.
Any different password, root-only/setup-only partial state, extra root,
disabled root, prior token/session/login/authentication footprint, or otherwise
conflicting database state fails closed and never rotates or repairs the
account. Investigate such a conflict instead of deleting rows or falling back
to `/api/setup`. After every provisioning attempt, have the secret manager
securely remove the rendered host password file; the `run --rm` container and
its read-only secret mount are already gone. For success, first verify the
secret-free JSON and database audit state, then force-recreate the ordinary
validator and require a `root-proof-present` generation. Rerun Relay role-pre,
migration, and role-post to publish the matching post-root database proof
before any Platform/principal/runtime start. For failure or conflict, first record only
the generic result and inspect authorized database state—never the
password—then destroy the rendered file. A retry requires a fresh secret-manager
render of the same password if the proof was already committed.

Treat the `relay-new-api-root-secret-isolation-proof` named volume as permanent
encrypted release state. Back it up after successful post-root validation, test
restore into an isolated host, and protect backup access at least as strictly
as the root credential. Ordinary `docker compose down` preserves it; never run
`docker compose down -v`, prune the volume, clear it in volume-init, or replace
it with an empty volume. Receipt and commit-marker volumes are reconstructible
by force-recreating the validator, but the root proof is not: after the raw root
password has been destroyed, a missing or corrupt proof must fail closed and
may be restored only from the controlled encrypted backup. Do not attempt to
regenerate it or choose the `pre-root` generation.

There is one explicitly bounded host-bind race. If a normal source is replaced
after root validation but before provisioning, the root transaction may still
commit. The root validator has not promised zero database writes in that host
tampering window. It does promise that the mandatory post-root ordinary
validator rereads every normal source, includes the permanent forbidden-root
digest, and blocks Platform, principal, API, and edge if any source now reuses
the root password. Repair the source, exact-replay the root command if required,
and force-recreate the post-root validator; never bypass that recovery sequence.

### Protected service-principal provisioning

After root/Setup exists, provision the complete non-interactive Platform
service-principal set from the owner-only
`NEW_API_RELAY_SERVICE_PRINCIPALS_FILE`. The file contains only kind/schema and
a sorted list of canonical client id, tenant UUID and `sk-` token identities;
API keys, callbacks, Redis, OBS, provider KEKs and operational signing secrets
are not mounted into this one-shot. The command holds lifecycle process gate A
for its full run and mutation fence B in the single database transaction.

Run it after every successful `post`, including ordinary upgrades:

```powershell
docker compose @relayCompose up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-service-principal-provision relay-new-api-service-principal-provision
```

The first exact batch returns only `kind`, `schema_version`, `state=created` and
`count`. A commit-ack retry returns `state=unchanged`; a missing root/Setup,
partial/stale/extra reserved principal, token collision, credential reuse or
different desired identity fails the whole transaction without repair. The
long-lived API depends on this successful one-shot, but never on root
provisioning. Consequently an ordinary restart cannot accidentally rerun the
one-time root clean-auth proof after the administrator has logged in.

### Platform ingress allowlist

The public HTTPS terminator must forward the configured Platform host to the
Compose `api-gateway`, which mounts `infra/nginx/platform-api.conf`. That
gateway has one exact anchored allowlist for the production machine ingress:

- `/internal/relay-callbacks/{backend_id}` where `backend_id` matches the
  anchored `[a-z0-9][a-z0-9._-]{0,63}` contract
- `/internal/relay/provider-alerts`
- `/internal/channel-costs`
- `/internal/relay/task-stages`
- `/internal/relay/operations-snapshots`
- `/internal/artifact-download-completions/edge-gateway`

Every other `/internal/*` path returns 404. Do not bypass this gateway or widen
the prefix. TLS is terminated before the Compose gateway; the gateway forwards
the original machine headers, while Platform remains responsible for the
internal-service token, per-purpose HMAC signature, timestamp, event identity,
and replay/idempotency checks. The six secure environment sink URLs must use
that same public HTTPS Platform host and one of the exact paths above.

## Required inventory

The committed secure example is the canonical inventory. Its groups cover:

- immutable new-api image digest plus compiled extension-fork revision,
  source-snapshot digest, and file count;
- managed Platform/new-api PostgreSQL, AOF Redis, and a separate DML-only
  download-edge DSN;
- Platform, internal TikTok, operations-token digest, and independent approval
  identities;
- stable new-api backend id, always-on legacy Relay URL/client credentials, and
  distinct legacy/new-api callback secrets;
- Platform callback, Relay worker admission, artifact signing, cost,
  telemetry, inbound alert, downstream alert, download, JWT, and input-asset
  secrets, all independent;
- separate Relay-output and Platform-input Huawei OBS credentials/buckets;
- approved secret-free route declarations and evidence-backed immutable
  provider contract rates;
- release-pinned Ed25519 route-acceptance public keys (the signing private key
  remains outside every Relay runtime and deployment secret namespace);
- a Secret-Manager-rendered Provider credential keyring file containing only
  versioned AES-256 KEKs, with an owner-only ACL readable by uid 10001;
- complete Provider monitor thresholds and native scheduled channel-test
  cadence; and
- exact public HTTPS Platform, Relay, download, callback, alert, cost, task
  stage, and operations-snapshot URLs.

Actual Provider/channel credentials are not committed as environment values.
Provision them through new-api's encrypted channel control plane. One declared
route must represent one real account and its fixed channel/key/adapter
identity. Mock channels and routes without an unexpired Ed25519 acceptance
manifest bound to the exact environment, capability, source snapshot, and
image digest are not eligible for staging/production. Configuration booleans
such as `staging_ready` or `production_ready` are not acceptance evidence.
Generate the evidence with the offline-only workflow in
[`backend/new-api-relay/docs/platform-route-acceptance-signing.md`](../backend/new-api-relay/docs/platform-route-acceptance-signing.md);
the signer binary is not part of the Relay runtime image.

Render `NEW_API_RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE` as an absolute or
Compose-resolved regular host file, never as inline JSON or a container
environment value. On POSIX use mode `0400` or `0600` with no group/world bits
and verify that uid 10001 can read but not write the mounted target. The secure
Compose overlay intentionally uses a read-only bind mount with
`create_host_path: false`, not a file-backed Compose secret: local Compose
commonly exposes file secrets as `0444`, which the Relay correctly rejects.
The same file is mounted read-only into the migration job so legacy plaintext
conversion and its database guards commit atomically; it is never mounted into
the root, role pre/post, API download edge, or Redis-facing services. Rotating the
active KEK adds a new key id; retain every previous KEK until no channel
credential-set version or pinned task credential references it. Never rewrite
or delete an immutable credential version to rotate a key.
The image identity remains an external attestation: verify the registry
manifest digest and digest-pinned Compose render against the authenticated
runtime provenance response. Relay can compare the signed `image_digest` with
its configured value, but it cannot self-attest its OCI digest from inside the
container.

Before building a release candidate, canonicalize the reviewed Ed25519 public-
key set exactly as Relay does, compute its lowercase SHA-256, and export that
public digest as `NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256`. The candidate
build helper rejects a missing, malformed, or zero value and passes it as
`RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256`; at runtime the canonical public-key
set must reproduce the compiled trust digest or staging/production readiness
fails closed. The public keys and their digest are release metadata, not secret
material; the signing private key remains offline.

### Platform-owned channel operations

Routine channel operations use the customer Platform super-administrator
console, never a browser-to-Relay session and never a proxy of new-api's raw
`/api/channel` objects. Platform calls the dedicated internal Relay operations
API with its server-held operations token. Relay accepts global channel control
only when the authenticated tenant equals `RELAY_PLATFORM_CONTROL_TENANT_ID`;
the secure overlay pins that value to the canonical customer
`RELAY_TENANT_ID`. Other operations tenants can still reconcile only their own
generation jobs and cannot inspect, test, enable, or disable shared channels.

The facade is deliberately narrow. It returns a secret-free channel inventory
and detail, starts one idempotent connectivity test, changes status only to
`enabled` or `manually_disabled` with an expected revision, and reads the
durable operation receipt. It never returns or accepts Provider keys, complete
base URLs, request headers, proxy settings, arbitrary provider payloads, or raw
provider errors. Every test or status change has a stable operation ID,
request ID, actor, reason, Platform approval audit, and Relay receipt. A network
timeout is resolved by reading that receipt; operators must not create a new
operation ID and repeat the Provider side effect. Manual disablement stops new
admission but does not move or cancel already-held generation jobs.

Channel detail also reports `test_supported`. The native generic tester cannot
faithfully exercise several asynchronous video/image channel types (including
the currently supported Kling, Jimeng, Doubao Video, and Vidu families), so the
Platform must disable its connectivity-test action for those rows and require
the documented staging generation canary instead. An unsupported test is never
shown as a successful health proof.

The embedded new-api root console remains a bootstrap and break-glass surface
for operations that the facade does not yet own, such as first credential
provisioning or an approved credential rotation. Restrict it behind the private
operations network and phishing-resistant administrator authentication. It is
not the normal day-to-day channel UI and must not be embedded in an iframe or
presented as the primary action from the customer Platform. The Platform owner
may use the secondary **high-risk operations** launcher: a POST with an exact
empty object first performs the existing recent step-up check, persists the
authorization audit, and returns only the server-owned fixed `/channels` URL.
The UI then requires a second explicit click to open a new tab with
`noopener noreferrer`; no Platform bearer, new-api token, cookie, URL parameter,
or shared browser storage crosses that boundary. new-api still requires its own
login and keeps the authoritative access and mutation audit.

`PLATFORM_RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN` is a dedicated HTTPS origin, not
the public Relay API origin. Put that origin behind an operator VPN, an
identity-aware proxy, or an exact IP allowlist with phishing-resistant MFA. The
public Relay ingress must not expose the native administrator routes. Configure
the admin origin in new-api's exact `SESSION_COOKIE_TRUSTED_URL` list, keep its
cookies host-only, Secure, HttpOnly, and SameSite, and return CSP
`frame-ancestors 'none'` plus `X-Frame-Options: DENY` at the administrator
ingress. The Platform never derives this origin from a request host, and an
unset origin makes the launcher unavailable. Moving credential creation or
rotation into Platform requires a separate write-only secret-envelope contract
and is not implied by either the facade or this break-glass launcher.

### Versioned service-principal token rotation

Routine rollout never mounts the previous service-principal document. Rotate
the tokens of the existing immutable principal identity set only through the
maintenance overlay `deploy/compose.relay.principal-rotation.yml` and profile
`relay-principal-rotation`. This v1 operation requires identical sorted
`client_id`/tenant identities in the current and desired A files; adding,
removing, or reassigning a principal is deliberately rejected and requires a
future versioned account-lifecycle operation.

Use a declared maintenance window. Stop the Relay API/download edge and every
Platform process that can call Relay, then render the exact currently
provisioned A revision to `NEW_API_RELAY_CURRENT_SERVICE_PRINCIPALS_FILE` and
the approved desired A revision to `NEW_API_RELAY_SERVICE_PRINCIPALS_FILE`.
Render all matching Platform process bundles from the same desired secret
release. Never obtain the current file from logs, Redis, browser storage, or a
database dump. With the ordinary secure and environment overlays in the same
Compose invocation, initialize and run the networkless validator, then run the
database job:

```powershell
$relayRotationCompose = @($relayCompose + @('-f', 'deploy/compose.relay.principal-rotation.yml'))
docker compose @relayRotationCompose --profile relay-principal-rotation `
  up --force-recreate --no-deps --abort-on-container-exit `
  --exit-code-from relay-new-api-principal-rotation-volume-init `
  relay-new-api-principal-rotation-volume-init
docker compose @relayRotationCompose --profile relay-principal-rotation `
  up --force-recreate --no-deps --abort-on-container-exit `
  --exit-code-from relay-new-api-principal-rotation-secret-isolation `
  relay-new-api-principal-rotation-secret-isolation
docker compose @relayRotationCompose --profile relay-principal-rotation `
  up --force-recreate --no-deps --abort-on-container-exit `
  --exit-code-from relay-new-api-service-principal-rotation `
  relay-new-api-service-principal-rotation
```

The validator is the same immutable image, has no network, reads the complete
ordinary global source set plus current A, and writes only its dedicated
receipt. The database job mounts exactly runtime DSN, Relay database CA,
current A, desired A, that receipt, and the current database-release proof. It
does not receive Redis CA, API/edge bundles, KEK, Platform bundles, role-admin,
migration, or root secrets. Its single JSON result states
`credential_operation=token_rotation_only` and `identity_set=immutable`; the
attempt id is random and contains no token-derived value.

After a successful rotation, force-recreate the ordinary root-proof-present
global validator, then rerun Relay role-pre, migrate, post, Platform role-pre
and migration, and exact service-principal provisioning before restarting any
runtime. This publishes fresh ordinary receipts and both database-release
proofs for the desired release. If the database job times out or its result is
uncertain, rerun the exact same current/desired receipt for idempotent
resolution; do not invent a second desired document. A reverse rotation is a
new reviewed release with the actual database state as current. Preserve only
the secret-free receipt/evidence and destroy the temporary current-A render
after the maintenance record is accepted.

## Startup and health contract

Secure startup fails closed unless the new-api node is explicitly `master`,
generation workers and Provider monitoring are enabled, and native scheduled
channel tests are enabled with a normalized positive integer frequency. The
channel-test interval plus the 15-second scheduler delay must be shorter than
`RELAY_PROVIDER_CHANNEL_TEST_MAX_AGE_SECONDS`. This prevents the Provider
monitor from treating an old native `Channel.TestTime` as a current probe.

Keep both endpoints, but give them different jobs:

- `/health/live` is process liveness only. It must never admit production
  generation traffic.
- `/health/ready` is the Compose and load-balancer service gate. HTTP 503 means
  unavailable. HTTP 200 `degraded` remains serviceable during a Provider-only
  incident so polling, callbacks, and reconciliation are not restarted away.
- A release/cutover requires HTTP 200 with top-level `state=healthy`, a fresh
  Provider monitor cycle, no alert/cost/telemetry dead letter, complete cost
  reconciliation, healthy durable state/artifact storage, and provenance
  headers matching the image under review. `degraded` is not cutover approval.

The first secure boot can therefore remain unhealthy while routes have not
been provisioned, native channel tests have not completed, or the required
cost-evidence canary is missing. Do not bypass readiness to make Compose green.

## Staging evidence and production approval

The repository tests make no external request. In an authorized staging
environment, operators must separately capture all of the following before a
production approval:

1. The image manifest digest and `/api/relay/provenance` compiled identity
   match the reviewed source snapshot.
2. Every approved native channel has a recent scheduled test result, and at
   least one full `/v1/generations` canary completes through the intended real
   Provider without an unknown submission or cross-channel retry.
3. The artifact is verified in the Relay-controlled Huawei OBS bucket and is
   delivered only through the controlled download edge.
4. The Platform receives signed task-stage, operations-snapshot, provider-alert
   transition/recovery, and exact append-only channel-cost evidence; the
   external operator alert sink acknowledges its signed test.
5. `/health/ready` returns `healthy` only after the monitor's first fresh cycle
   and the cost reconciliation is complete.

Record the Provider, OBS, alert-sink, and cost evidence in the release approval
artifact. Never translate a successful Compose render, local unit test, or
`/health/live` response into a real-provider/OBS PASS.

Rollback never changes Relay implementation or task affinity. Stop new
admission, keep accepted `new-api-v1` jobs on their exact job/route/token chain,
   and deploy only a previously verified, Current-v3-compatible new-api immutable
digest through the same secret/role/proof lifecycle. If the previous image is
not compatible, use a forward fix. Python Relay is not a production rollback
target, and no runtime client may be added for it. Unknown submissions never
cross a backend, account, channel, or release boundary.
