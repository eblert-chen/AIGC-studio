import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const base = read("docker-compose.yml");
const secure = read("deploy/compose.relay.secure.yml");
const staging = read("deploy/compose.relay.staging.yml");
const production = read("deploy/compose.relay.production.yml");
const sharedEnv = read("deploy/relay-secure.env.example");
const stagingEnv = read("deploy/relay-staging.env.example");
const productionEnv = read("deploy/relay-production.env.example");
const runbook = read("docs/new-api-production-deployment.md");
const relayMigration = read("docs/relay-new-api-migration.md");
const deploymentRunbook = read("docs/deployment-runbook.md");
const releaseReadiness = read("docs/release-readiness.md");
const platformIngress = read("infra/nginx/platform-api.conf");
const gitignore = read(".gitignore");
const relayVersion = read("backend/new-api-relay/VERSION");
const relayDockerfile = read("backend/new-api-relay/Dockerfile");
const relayDevelopmentDockerfile = read("backend/new-api-relay/Dockerfile.dev");
const relayDevelopmentCompose = read("backend/new-api-relay/docker-compose.dev.yml");
const relayMakefile = read("backend/new-api-relay/makefile");
const relayLegacySchemaGate = read(
  "backend/new-api-relay/scripts/test-relay-schema-legacy-pg16.ps1",
);
const relaySchemaContract = read("backend/new-api-relay/model/schema_contract.go");
const relaySchemaIntegrity = read("backend/new-api-relay/model/schema_integrity.go");
const relaySchemaPostgresGate = read(
  "backend/new-api-relay/model/schema_migration_postgres_test.go",
);
const relaySchemaV2PostgresGate = read(
  "backend/new-api-relay/model/schema_migration_v2_postgres_test.go",
);
const relaySchemaV5PostgresGate = read(
  "backend/new-api-relay/model/schema_migration_v5_postgres_test.go",
);
const relaySchemaV6PostgresGate = read(
  "backend/new-api-relay/model/schema_migration_v6_postgres_test.go",
);
const relaySchemaV7PostgresGate = read(
  "backend/new-api-relay/model/schema_migration_v7_postgres_test.go",
);
const relaySchemaV8PostgresGate = read(
  "backend/new-api-relay/model/schema_migration_v8_postgres_test.go",
);
const relaySchemaArtifactGate = read(
  "backend/new-api-relay/model/schema_artifact_test.go",
);
const relaySchemaMigrationGate = read(
  "backend/new-api-relay/model/schema_migration_test.go",
);
const relaySchemaV1FixturePatch = read(
  "backend/new-api-relay/scripts/fixtures/relay-schema-v1-pg16-tls-test-fixture.patch",
);
const relaySchemaV1ApplicationReferenceFixture = read(
  "backend/new-api-relay/scripts/fixtures/relay-schema-v1-application-reference-test.go.fixture",
);
const relayDatabaseRoleAttestation = read(
  "backend/new-api-relay/model/database_role_attestation.go",
);
const relayRuntimeDatabaseRoleProof = read(
  "backend/new-api-relay/model/runtime_database_role_proof.go",
);
const relayDownloadEdgeDatabaseRoleProof = read(
  "backend/new-api-relay/model/download_edge_database_privilege_manifest.go",
);
const relayAPIReadiness = read(
  "backend/new-api-relay/service/platform_api_readiness.go",
);
const relayMain = read("backend/new-api-relay/main.go");
const relayEdgeMain = read("backend/new-api-relay/cmd/relay-download-edge/main.go");
const relayEdgeService = read("backend/new-api-relay/service/platform_download_edge.go");
const relayDatabaseReleaseProof = read(
  "backend/new-api-relay/service/platform_database_release_proof.go",
);
const relaySchemaVersionMatch = relaySchemaContract.match(
  /RelaySchemaTargetVersion\s+int64\s*=\s*([0-9]+)/,
);
assert.ok(relaySchemaVersionMatch, "Relay schema contract must expose a target version");
const currentRelaySchemaVersion = Number.parseInt(relaySchemaVersionMatch[1], 10);
const platformPrivilegeFacade = read("backend/platform/platform_api/database_privileges.py");
const currentPlatformPolicyMatch = platformPrivilegeFacade.match(
  /^CURRENT_PLATFORM_DATABASE_PRIVILEGE_POLICY = _policy_v([0-9]+)$/m,
);
assert.ok(currentPlatformPolicyMatch, "Platform privilege facade must select one current policy");
const currentPlatformPolicyVersion = Number.parseInt(currentPlatformPolicyMatch[1], 10);
const currentPlatformPolicy = read(
  `backend/platform/platform_api/database_privileges_v${currentPlatformPolicyVersion}.py`,
);
const currentPlatformHeadMatch = currentPlatformPolicy.match(/^ALEMBIC_HEAD = "([a-z0-9_]+)"$/m);
assert.ok(currentPlatformHeadMatch, "current Platform policy must bind one Alembic head");
const currentPlatformHead = currentPlatformHeadMatch[1];
const currentPlatformMigration = read(
  `backend/platform/migrations/versions/${currentPlatformHead}.py`,
);
const currentPlatformPreviousMatch = currentPlatformMigration.match(
  /^down_revision: str \| None = "([a-z0-9_]+)"$/m,
);
assert.ok(currentPlatformPreviousMatch, "current Platform migration must bind one direct predecessor");
const currentPlatformPrevious = currentPlatformPreviousMatch[1];

function serviceBlock(source, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(
    new RegExp(
      `^  ${escaped}:\\r?\\n([\\s\\S]*?)(?=^  [a-zA-Z0-9][a-zA-Z0-9_-]*:\\r?\\n|(?![\\s\\S]))`,
      "m",
    ),
  );
  assert.ok(match, `missing Compose service ${name}`);
  return match[0];
}

function envKeys(source) {
  return new Set(
    source
      .split(/\r?\n/)
      .filter((line) => /^[A-Z0-9_]+=/.test(line))
      .map((line) => line.slice(0, line.indexOf("="))),
  );
}

test("makes the immutable previous-candidate PostgreSQL 16 upgrade a mandatory release gate", () => {
  const candidateDigest =
    "sha256:142185d134d0427cc073e7235a5bb10c248d5eabad1c1e737abdf83e56c611e6";
  assert.match(relayMakefile, /^test-relay-schema-legacy-pg16:\s*$/m);
  assert.match(
    relayMakefile,
    /scripts\/test-relay-schema-legacy-pg16\.ps1/,
  );
  assert.match(relayMakefile, /PowerShell is required[\s\S]+exit 1/);
  assert.match(
    relayLegacySchemaGate,
    /Get-Command node -CommandType Application -ErrorAction SilentlyContinue/,
  );
  assert.match(
    relayLegacySchemaGate,
    /relay-candidate-baseline\.mjs[\s\S]+--check 2>&1[\s\S]+candidateBaselineCheckExitCode -ne 0/,
  );
  assert.match(
    relayLegacySchemaGate,
    /candidate baseline matches current Relay, Platform, and harness sources/,
  );
  const baselineCheckIndex = relayLegacySchemaGate.indexOf(
    "$candidateBaselineCheckOutput",
  );
  const firstDockerUseIndex = relayLegacySchemaGate.indexOf(
    "docker image inspect $CandidateImage",
  );
  assert.ok(baselineCheckIndex >= 0);
  assert.ok(firstDockerUseIndex > baselineCheckIndex);
  assert.doesNotMatch(
    relayLegacySchemaGate,
    /throw\s+"[^"]*\$candidateBaselineCheckOutput/,
  );
  assert.match(relayLegacySchemaGate, new RegExp(candidateDigest));
  assert.match(
    relayLegacySchemaGate,
    /ai-video\/new-api-relay@sha256:142185d134d0427cc073e7235a5bb10c248d5eabad1c1e737abdf83e56c611e6/,
  );
  for (const evidence of [
    "legacy-candidate-id=",
    "legacy-candidate-repo-digest=",
    "legacy-candidate-source-revision=",
    "legacy-candidate-upstream-revision=",
    "legacy-candidate-source-snapshot=",
    "legacy-candidate-source-file-count=",
    "qualified-postgres-image-id=",
    "relay-schema-v1-source-revision=",
    "relay-schema-v1-test-fixture-patch-sha256=",
    "relay-schema-v1-application-reference-fixture-sha256=",
    "relay-schema-web-dist-fixture-sha256=",
    "relay-schema-v4-image-id=",
    "relay-schema-v4-image-repo-digest=",
    "relay-schema-v5-image-id=",
    "relay-schema-v5-image-repo-digest=",
    "relay-schema-v5-source-revision=",
    "relay-schema-v6-image-id=",
    "relay-schema-v6-image-repo-digest=",
    "relay-schema-v6-source-revision=",
    "relay-schema-v6-upstream-revision=",
    "relay-schema-v6-source-snapshot=",
    "relay-schema-v6-source-file-count=",
    "relay-schema-v7-source-revision=",
    "fresh-v7-row7-only-gate=PASS",
    "fresh-v1-row1-only-reference-gate=PASS",
    "legacy-to-v1-gate=PASS",
    "v1-compatible-no-runtime-side-effects=PASS",
    "historical-frozen-v1-to-v2-no-catalog-delta-gate=PASS",
    "v1-to-v2-no-catalog-delta-gate=PASS",
    "current-v7-owner-mode-preflight=PASS",
    "v2-to-v3-frozen-one-shot-gate=PASS",
    "exact-v1-to-v3-ledger-gate=PASS",
    "pre-v4-zero-acl-role-stub-gate=PASS",
    "v3-to-pinned-v4-one-shot-gate=PASS",
    "exact-v1-to-v4-ledger-gate=PASS",
    "pinned-v4-state-ledger-catalog-acl-guards-gate=PASS",
    "v4-to-v5-diagnostic-taxonomy-rollback-gate=PASS",
    "pre-v5-zero-acl-role-stub-gate=PASS",
    "v4-to-pinned-v5-one-shot-gate=PASS",
    "exact-v1-to-v5-ledger-gate=PASS",
    "pinned-v5-to-v6-lifecycle-rollback-gate=PASS",
    "pre-v6-zero-acl-role-stub-gate=PASS",
    "v5-to-pinned-v6-one-shot-gate=PASS",
    "exact-v1-to-v6-ledger-gate=PASS",
    "pinned-v6-to-v7-artifact-content-rollback-gate=PASS",
    "pre-v7-zero-acl-role-stub-gate=PASS",
    "v6-to-v7-one-shot-gate=PASS",
    "exact-v1-to-v7-ledger-gate=PASS",
    "post-v7-proof-root-principal-api-edge-current-gate=PASS",
    "post-v7-route-binding-mutation-fencing-gate=PASS",
    "max-v6-ahead-no-direct-rollback-gate=PASS",
    "max-v5-ahead-no-direct-rollback-gate=PASS",
    "legacy-schema-upgrade-gate=PASS",
  ]) {
    assert.ok(relayLegacySchemaGate.includes(evidence), `missing legacy gate evidence ${evidence}`);
  }
  assert.ok(
    relayLegacySchemaGate.includes(
      '"1|7|7|clean|1,2,3,4,5,6,7|1|1|5|5"',
    ),
    "the terminal protected lifecycle snapshot must retain all five service principals and tokens",
  );
  assert.match(relayLegacySchemaGate, /immutable previous-candidate image is unavailable/);
  assert.match(
    relayLegacySchemaGate,
    /709e9b45b25a6baa415ab985078bd7764a35eaf9/,
  );
  assert.match(
    relayLegacySchemaGate,
    /sha256:9c2d47297a4a7bfcdeaa8565bc66f40243e73bd3eab03f6cccbaadf652d76e10/,
  );
  assert.match(relayLegacySchemaGate, /go test -json/);
  assert.match(relayLegacySchemaGate, /Action -contains "skip"/);
  assert.match(relayLegacySchemaGate, /Action -notcontains "pass"/);
  assert.match(
    relayLegacySchemaGate,
    /pinnedV1SourceVolume[\s\S]+TestRelaySchemaPostgresLegacyCandidateUpgrade/,
  );
  assert.match(
    relayLegacySchemaGate,
    /TestRelaySchemaPostgresV1ToV2NoCatalogDelta/,
  );
  assert.match(relayLegacySchemaGate, /2535972505c63a059fdbe678e79577671481c358/);
  assert.match(relayLegacySchemaGate, /f0042a96b048b501c9ac76470a234cffc0a54926/);
  assert.match(relayLegacySchemaGate, /53a5d65cefbc4400e9e572665b227cf4a9628774aa77c814be4082f1abd7f84f/);
  assert.match(relayLegacySchemaGate, /d5998561f1142e5189ca15f6086b42da31127ecb5d58269ac492dc4aa3f61b9a/);
  assert.match(relayLegacySchemaGate, /v5-canary-4e1c94bc/);
  assert.match(relayLegacySchemaGate, /576b836d26f19825532feaca1f9f7950f28affc78477d61d7b29dca474b8817f/);
  assert.match(relayLegacySchemaGate, /v6-canary-0b0b8bf5/);
  assert.match(relayLegacySchemaGate, /0b0b8bf597aeb9e69e89a04f9b4d7b1d712e3391/);
  assert.match(relayLegacySchemaGate, /1ad8305e212ac45bca32d2bbcf064c20bdb4337623b952452659748b17d2e836/);
  assert.match(relayLegacySchemaGate, /pinnedV6SourceFileCount = "2106"/);
  assert.match(relayLegacySchemaGate, /e0edb8c300ffb44695341c84541ac0d4c86297535f0e8f7982873ac10c119a67/);
  assert.match(relayLegacySchemaGate, /TEST_POSTGRES_LEGACY_REFERENCE_DSN=\$v1ReferenceDSN/);
  assert.match(relayLegacySchemaGate, /relay-provision-database-roles/);
  assert.match(relayLegacySchemaGate, /\/release\/new-api relay-migrate/);
  assert.match(
    relayLegacySchemaGate,
    /\$legacyRoleAdminDSN = "postgresql:\/\/postgres:[^\r\n]+sslrootcert=\/run\/relay-secrets\/current-v6-ca\.crt&search_path=public"/,
  );
  assert.equal(
    (relayLegacySchemaGate.match(/sslrootcert=\/run\/relay-secrets\/current-v6-ca\.crt/g) ?? []).length,
    3,
  );
  assert.equal(
    (relayLegacySchemaGate.match(/--user 10001:10001/g) ?? []).length,
    8,
  );
  assert.doesNotMatch(relayLegacySchemaGate, /RELAY_DATABASE_CA_FILE=\/tls\/ca\.crt/);
  assert.match(
    relayLegacySchemaGate,
    /cp \/tls\/ca\.crt \/secrets\/current-v6-ca\.crt[\s\S]+chown 10001:10001 \/secrets\/current-v6-\*[\s\S]+chmod 0400 \/secrets\/current-v6-\*/,
  );
  assert.match(
    relayLegacySchemaGate,
    /\$providerKeyringJSONBase64 = \[Convert\]::ToBase64String\(\[System\.Text\.Encoding\]::UTF8\.GetBytes\(\$providerKeyringJSON\)\)/,
  );
  assert.match(
    relayLegacySchemaGate,
    /PROVIDER_KEYRING_BASE64=\$providerKeyringJSONBase64[\s\S]+\$PROVIDER_KEYRING_BASE64" \| base64 -d > \/secrets\/current-v6-provider-keyring\.json/,
  );
  assert.doesNotMatch(relayLegacySchemaGate, /PROVIDER_KEYRING=\$providerKeyringJSON/);
  assert.match(
    relayLegacySchemaGate,
    /test -x \/release-current\/new-api[\s\S]+test -x \/release-v3\/new-api[\s\S]+stat -c %u:%g:%a "\$secret" \| grep -Fqx 10001:10001:400/,
  );
  assert.match(
    relayLegacySchemaGate,
    /\$legacyDiagnosticDSN = "postgresql:\/\/relay_schema_migrator:[^\r\n]+\/new_api\?sslmode=verify-full&sslrootcert=\/tls\/ca\.crt[^\r\n]+"[\s\S]+TEST_RELAY_SCHEMA_V5_POSTGRES_DSN=\$legacyDiagnosticDSN[\s\S]+v4-to-v5-diagnostic-rollback-preserves-exact-v4-gate=PASS/,
  );
  assert.doesNotMatch(
    relayLegacySchemaGate,
    /pg_dump[^\r\n]+--schema-only[^\r\n]+\|[\s\S]{0,300}TEST_RELAY_SCHEMA_V5_POSTGRES_DSN/,
  );
  assert.match(
    relayLegacySchemaGate,
    /\$currentV7RolePreOutput[\s\S]*?RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true[\s\S]*?\/release\/new-api relay-provision-database-roles/,
  );
  assert.match(
    relayLegacySchemaGate,
    /\$pinnedV6MigrationOutput[\s\S]*?RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true[\s\S]*?\$pinnedV6Image relay-migrate/,
  );
  assert.match(
    relayLegacySchemaGate,
    /\$currentV7MigrationOutput[\s\S]*?RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true[\s\S]*?\/release\/new-api relay-migrate/,
  );
  assert.match(
    relayLegacySchemaGate,
    /\$pinnedV3MigrationOutput[\s\S]*?RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true[\s\S]*?RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=false[\s\S]*?SQL_DSN_FILE=\/run\/relay-secrets\/pinned-v3-migration-dsn[\s\S]*?\/release\/new-api relay-migrate/,
  );
  assert.equal(
    (relayLegacySchemaGate.match(/RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=false/g) ?? []).length,
    2,
    "only the frozen-v3 and frozen-v4 local ACL rehearsals may disable TLS attestation",
  );
  assert.match(
    relayLegacySchemaGate,
    /\$pinnedV3MigrationDSN\s*=\s*"postgresql:\/\/relay_schema_migrator:[^\r\n]+\?sslmode=require&search_path=public&options=-c%20role%3Drelay_schema_owner"/,
  );
  assert.doesNotMatch(
    relayLegacySchemaGate.match(/\$pinnedV3MigrationOutput[\s\S]*?\$pinnedV3MigrationExitCode/)?.[0] ?? "",
    /RELAY_DATABASE_CA_FILE|sslrootcert|current-v6-migration-dsn/,
  );
  assert.match(relayLegacySchemaGate, /pinned-v3-local-tls-require-acl-rehearsal=PASS/);
  assert.match(
    relayLegacySchemaGate,
    /\$pinnedV4MigrationOutput[\s\S]*?RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true[\s\S]*?RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=false[\s\S]*?SQL_DSN_FILE=\/run\/relay-secrets\/pinned-v4-migration-dsn[\s\S]*?\$pinnedV4Image relay-migrate/,
  );
  assert.match(
    relayLegacySchemaGate,
    /\$pinnedV4MigrationDSN\s*=\s*"postgresql:\/\/relay_schema_migrator:[^\r\n]+\?sslmode=require&search_path=public&options=-c%20role%3Drelay_schema_owner"/,
  );
  assert.doesNotMatch(
    relayLegacySchemaGate.match(/\$pinnedV4MigrationOutput[\s\S]*?\$pinnedV4MigrationExitCode/)?.[0] ?? "",
    /RELAY_DATABASE_CA_FILE|sslrootcert|current-v6-migration-dsn/,
  );
  assert.match(relayLegacySchemaGate, /pinned-v4-local-tls-require-acl-rehearsal=PASS/);
  const postV7LifecycleStage =
    relayLegacySchemaGate.match(/\$postV7LifecycleOutput[\s\S]*?\$postV7LifecycleExitCode/)?.[0] ?? "";
  assert.match(postV7LifecycleStage, /TEST_POSTGRES_LIFECYCLE_ADMIN_DSN=\$legacyRoleAdminDSN/);
  assert.match(postV7LifecycleStage, /TEST_POSTGRES_LIFECYCLE_MIGRATION_DSN=\$legacyMigrationDSN/);
  assert.match(postV7LifecycleStage, /TEST_POSTGRES_LIFECYCLE_RUNTIME_DSN=\$legacyRuntimeDSN/);
  assert.doesNotMatch(postV7LifecycleStage, /TEST_POSTGRES_LIFECYCLE_ADMIN_DSN=\$legacyDSN/);
  assert.match(
    relayLegacySchemaGate,
    /\$pinnedV5MigrationOutput[\s\S]*?RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true[\s\S]*?RELAY_DATABASE_CA_FILE=\/run\/relay-secrets\/current-v6-ca\.crt[\s\S]*?SQL_DSN_FILE=\/run\/relay-secrets\/current-v6-migration-dsn[\s\S]*?\$pinnedV5Image relay-migrate/,
  );
  assert.match(relayLegacySchemaGate, /7\|7\|7\|clean\|7/);
  assert.match(relayLegacySchemaGate, /1\|3\|3\|clean\|1,2,3/);
  assert.match(relayLegacySchemaGate, /1\|4\|4\|clean\|1,2,3,4/);
  assert.match(relayLegacySchemaGate, /1\|5\|5\|clean\|1,2,3,4,5/);
  assert.match(relayLegacySchemaGate, /1\|6\|6\|clean\|1,2,3,4,5,6/);
  assert.match(relayLegacySchemaGate, /1\|7\|7\|clean\|1,2,3,4,5,6,7/);
  assert.match(relayLegacySchemaGate, /status\.classification -ne "ahead"/);
  assert.match(
    relayLegacySchemaGate,
    /TestRelaySchemaV4RouteBindingGuardsConfiguredDatabases/,
  );
  assert.match(
    relayLegacySchemaGate,
    /TestRelaySchemaPostgresProtectedLifecycleProcess/,
  );
  assert.match(
    relaySchemaPostgresGate,
    /relaySchemaTestDownloadEdgeRuntimeReady[\s\S]+VerifyRelayDownloadEdgeDatabaseRole[\s\S]+\/health\/ready[\s\S]+relaySchemaTestEdgeRole/,
  );
  assert.match(
    relayLegacySchemaGate,
    /same-database current-v7 proof\/root\/principal\/API\/download-edge lifecycle/,
  );
  assert.match(
    relayLegacySchemaGate,
    /TestRelaySchemaPostgresV4ToV5DiagnosticTaxonomy/,
  );
  assert.match(
    relaySchemaV5PostgresGate,
    /TestRelaySchemaPostgresV4ToV5DiagnosticTaxonomy[\s\S]+relaySchemaV4FrozenChecksumSHA256[\s\S]+relaySchemaV5FrozenChecksumSHA256/,
  );
  assert.match(
    relaySchemaV5PostgresGate,
    /TestRelaySchemaPostgresPinnedV4ReleaseFixture[\s\S]+verifyRelayRuntimeDatabasePrivilegeManifest[\s\S]+verifyRelayDownloadEdgeCurrentDatabaseRole/,
  );
  assert.match(
    relaySchemaV5PostgresGate,
    /Model\(&PlatformGenerationProviderRoute\{\}\)[\s\S]+Update\("capability_profile_id", "tampered-partial-profile"\)/,
  );
  assert.match(
    relaySchemaV5PostgresGate,
    /Model\(&RelaySchemaMigration\{\}\)[\s\S]+Update\("name", "tampered-v4-receipt"\)/,
  );
  assert.match(
    relayLegacySchemaGate,
    /\$preV4RoleOutput[\s\S]+?relay-provision-database-roles[\s\S]+?\$preV4Role\.kind -ne "relay_database_role_provision"[\s\S]+?\$preV4Role\.state -ne "provisioned"[\s\S]+?pre-v4-zero-acl-role-stub-gate=PASS/,
  );
  assert.match(
    relayLegacySchemaGate,
    /\$preV5RoleOutput[\s\S]+?relay-provision-database-roles[\s\S]+?\$preV5Role\.kind -ne "relay_database_role_provision"[\s\S]+?\$preV5Role\.state -ne "provisioned"[\s\S]+?pre-v5-zero-acl-role-stub-gate=PASS/,
  );
  const pinnedV3MigrationIndex = relayLegacySchemaGate.indexOf("$pinnedV3MigrationOutput");
  const preV4RoleIndex = relayLegacySchemaGate.indexOf("$preV4RoleOutput");
  const pinnedV4MigrationIndex = relayLegacySchemaGate.indexOf("$pinnedV4MigrationOutput");
  const pinnedV4VerifierIndex = relayLegacySchemaGate.indexOf("$pinnedV4ReleaseFixtureOutput");
  const preV5RoleIndex = relayLegacySchemaGate.indexOf("$preV5RoleOutput");
  const pinnedV5MigrationIndex = relayLegacySchemaGate.indexOf("$pinnedV5MigrationOutput");
  const pinnedV5VerifierIndex = relayLegacySchemaGate.indexOf("$v5ToV6LifecycleOutput");
  const preV6RoleIndex = relayLegacySchemaGate.indexOf("$preV6RoleOutput");
  const pinnedV6MigrationIndex = relayLegacySchemaGate.indexOf("$pinnedV6MigrationOutput");
  const pinnedV6VerifierIndex = relayLegacySchemaGate.indexOf("$v6ToV7ArtifactOutput");
  const preV7RoleIndex = relayLegacySchemaGate.indexOf("$preV7RoleOutput");
  const currentV7MigrationIndex = relayLegacySchemaGate.indexOf("$currentV7MigrationOutput");
  const crossVersionStages = [
    [pinnedV3MigrationIndex, "pinned-v3 migration"],
    [preV4RoleIndex, "pre-v4 zero-ACL role-pre"],
    [pinnedV4MigrationIndex, "pinned-v4 migration"],
    [pinnedV4VerifierIndex, "pinned-v4 full verifier"],
    [preV5RoleIndex, "pre-v5 zero-ACL role-pre"],
    [pinnedV5MigrationIndex, "pinned-v5 migration"],
    [pinnedV5VerifierIndex, "pinned-v5 rollback verifier"],
    [preV6RoleIndex, "pre-v6 zero-ACL role-pre"],
    [pinnedV6MigrationIndex, "pinned-v6 migration"],
    [pinnedV6VerifierIndex, "pinned-v6 rollback verifier"],
    [preV7RoleIndex, "pre-v7 zero-ACL role-pre"],
    [currentV7MigrationIndex, "current-v7 migration"],
  ];
  for (const [index, stage] of crossVersionStages) {
    assert.ok(index >= 0, `missing ${stage} stage`);
  }
  for (let index = 1; index < crossVersionStages.length; index += 1) {
    assert.ok(
      crossVersionStages[index][0] > crossVersionStages[index - 1][0],
      `${crossVersionStages[index][1]} must follow ${crossVersionStages[index - 1][1]}`,
    );
  }
  assert.match(
    relayLegacySchemaGate,
    /1\|1\|1\|clean\|1\|1\|1\|0\|0/,
  );
  assert.match(
    relayLegacySchemaGate,
    /pinnedV1FixturePatchSHA256 = "dd3bbe7dea195bf83222f2acb32ea0ab96208ac64d7f66dcbc2ddb5f5e3a3449"/,
  );
  assert.match(
    relayLegacySchemaGate,
    /pinnedV1ApplicationReferenceFixtureSHA256 = "905ec8ac2cf0915820029292e015ca54dbe833f8b675e78d7404759080aeacfc"/,
  );
  assert.match(relayLegacySchemaGate, /'lifecycle_root'/);
  assert.match(relayLegacySchemaGate, /'v0\.0\.0'/);
  assert.match(relayLegacySchemaGate, /'SUCCESS','100%'/);
  assert.match(
    relayLegacySchemaGate,
    /VALUES \(91001,1,'sk-legacy-channel-fixture-not-real',2,'legacy-migration-channel-fixture'/,
    "the inert legacy migration channel must stay manually disabled so runtime monitoring cannot rewrite its exact preservation digest",
  );
  assert.match(
    relayLegacySchemaGate,
    /\$fixtureSQL \| docker exec -i \$legacyPostgres psql -v ON_ERROR_STOP=1/,
  );
  assert.doesNotMatch(relayLegacySchemaGate, /psql[^\r\n]+-c \$fixtureSQL/);
  assert.match(
    relayLegacySchemaGate,
    /TEST_POSTGRES_LIFECYCLE_ROOT_PROVISION_STATE=unchanged/,
  );
  assert.match(
    relayLegacySchemaGate,
    /TEST_POSTGRES_LIFECYCLE_PRINCIPAL_PROVISION_STATE=created/,
  );
  assert.match(
    relayLegacySchemaGate,
    /TEST_POSTGRES_LIFECYCLE_REQUIRE_LEGACY_FIXTURES=true/,
  );
  assert.ok(
    relayLegacySchemaGate.includes(
      'go test -json ./service -run "^TestProtectedPlatformRelayServicePrincipalRotationPostgresBarrier$"',
    ),
  );
  assert.match(
    relayLegacySchemaGate,
    /Assert-GoTestPassed \$rotationBarrierOutput "TestProtectedPlatformRelayServicePrincipalRotationPostgresBarrier"/,
  );
  assert.ok(
    relayLegacySchemaGate.includes(
      'go test -json . -run "^TestPlatformRelayPrincipalRotationLifecycleLockPostgresTimesOutWithoutWrites$"',
    ),
  );
  assert.match(
    relayLegacySchemaGate,
    /Assert-GoTestPassed \$rotationLifecycleOutput "TestPlatformRelayPrincipalRotationLifecycleLockPostgresTimesOutWithoutWrites"/,
  );
  assert.match(relaySchemaV1FixturePatch, /beforeEvidence\.SetupCount/);
  assert.match(relaySchemaV1FixturePatch, /ValidatePasswordAndHash/);
  assert.match(relaySchemaV1FixturePatch, /rootSetupBefore\.RootDigest/);
  assert.match(relaySchemaV1FixturePatch, /rootSetupBefore\.SetupDigest/);
  assert.match(relaySchemaV1FixturePatch, /rootSetupBefore, rootSetupAfter/);
  assert.match(relaySchemaV1FixturePatch, /protectedSideEffects\.RootCount/);
  assert.match(relaySchemaV1FixturePatch, /protectedSideEffects\.PrincipalTokenCount/);
  assert.doesNotMatch(relaySchemaV1FixturePatch, /web\/dist|main_root_secret_isolation\.go/);
  assert.match(relaySchemaV1ApplicationReferenceFixture, /RunRelaySchemaMigrations/);
  assert.match(relaySchemaV1ApplicationReferenceFixture, /require\.Len\(t, ledger, 1\)/);
  assert.match(relaySchemaV1ApplicationReferenceFixture, /relaySchemaV1PostgresCatalogSHA256/);
  assert.doesNotMatch(relaySchemaV1ApplicationReferenceFixture, /\.Skip|Skipf|SkipNow/);
  assert.match(relayLegacySchemaGate, /TestRelaySchemaPostgresFreshV1ApplicationReference/);
  assert.doesNotMatch(relayLegacySchemaGate, /postgres:16-alpine|sslmode=disable/);
  assert.match(runbook, /make test-relay-schema-legacy-pg16/);
  assert.match(runbook, /not an optional developer\s+smoke test/);
  assert.match(runbook, /missing image[\s\S]+release failure/);
  assert.match(runbook, /raw\/unversioned[\s\S]+immutable schema-v1[\s\S]+v1-to-v2[\s\S]+v2-to-v3[\s\S]+v3-to-v4[\s\S]+v4-to-v5[\s\S]+v5-to-v6/i);
  assert.match(
    runbook,
    /pinned-v1 application-catalog reference[\s\S]+only the frozen v1[\s\S]+application catalog[\s\S]+does not claim old-v1 binary runtime or system[\s\S]+attestation[\s\S]+real `\/health\/ready` process acceptance/i,
  );
  assert.match(
    deploymentRunbook,
    /pinned-v1 application-catalog[\s\S]+只证明冻结的 v1 application catalog[\s\S]+不代表旧 v1 runtime 或 system-catalog acceptance/i,
  );
  assert.match(
    deploymentRunbook,
    /fresh-v1-row1-only[\s\S]+application-reference[\s\S]+post-v7[\s\S]+proof\/root\/principal\/API\/download-edge/i,
  );
  assert.match(runbook, /absent test event or `skip` as failure/);
});

test("preserves historical Relay schemas and binds the current provider-cost release", () => {
  assert.match(relaySchemaContract, new RegExp(`RelaySchemaTargetVersion\\s+int64\\s*=\\s*${currentRelaySchemaVersion}`));
  assert.match(relaySchemaContract, /RelaySchemaMinVersion\s+int64\s*=\s*1/);
  assert.match(relaySchemaContract, new RegExp(`RelaySchemaMaxVersion\\s+int64\\s*=\\s*${currentRelaySchemaVersion}`));
  assert.equal(currentRelaySchemaVersion, 8, "update the v8 release assertions for a newer schema instead of silently accepting it");
  assert.match(
    relaySchemaContract,
    /relaySchemaV1FrozenChecksumSHA256\s*=\s*"sha256:369af2b5c47652ae9e03a2f79ba64f56c3b517deb7f4c8f933ce3957082698a7"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV2SourceArtifactSHA256\s*=\s*"sha256:03de3ed038c3a9f7b6e160ac720e4350b9d468c09417cdc9e280289ed390fef2"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV2FrozenChecksumSHA256\s*=\s*"sha256:a3dc154ca42086544096cc0c3e3f2c84479e52e2ad76bd4d32aa2806c2c9af0e"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV3SourceArtifactSHA256\s*=\s*"sha256:4d784286e5480a10a83f4408b303eec075a347fa405d45650e12c19425e4659d"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV3FrozenChecksumSHA256\s*=\s*"sha256:0295d36ca5032088cc2e0b3b7f935aaeb24c3c5847a6b0a92a4dc3099d58e553"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV4SourceArtifactSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV4ModelArtifactSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV4FrozenChecksumSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.doesNotMatch(relaySchemaContract, /relaySchemaV4[^\n]*pending/);
  assert.match(
    relaySchemaContract,
    /relaySchemaV5SourceArtifactSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV5ModelArtifactSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV5FrozenChecksumSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.doesNotMatch(relaySchemaContract, /relaySchemaV5[^\n]*pending/);
  assert.match(
    relaySchemaContract,
    /relaySchemaV6SourceArtifactSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV6ModelArtifactSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV6FrozenChecksumSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.doesNotMatch(relaySchemaContract, /relaySchemaV6[^\n]*pending/);
  assert.match(
    relaySchemaContract,
    /relaySchemaV7SourceArtifactSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV7ModelArtifactSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV7FrozenChecksumSHA256\s*=\s*"sha256:da3ddb86260818f894b13fc6b3ad34031b6089dddb83954172984d07e451c4c3"/,
  );
  assert.doesNotMatch(relaySchemaContract, /relaySchemaV7[^\n]*pending/);
  assert.match(
    relaySchemaContract,
    /relaySchemaV8SourceArtifactSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV8ModelArtifactSHA256\s*=\s*"sha256:[0-9a-f]{64}"/,
  );
  assert.match(
    relaySchemaContract,
    /relaySchemaV8FrozenChecksumSHA256\s*=\s*"sha256:1def22667e226cf8dfbd467e18445874dcce6959a8b9e74b43623e14bbc31de7"/,
  );
  assert.doesNotMatch(relaySchemaContract, /relaySchemaV8[^\n]*pending/);
  const catalogDigest =
    "sha256:0ebe3f289439193f207f087452c289504fdd231759ac2b3d0159f8cc61d6cb6d";
  assert.match(
    relaySchemaIntegrity,
    new RegExp(`relaySchemaV1PostgresCatalogSHA256 = "${catalogDigest}"`),
  );
  assert.match(
    relaySchemaIntegrity,
    new RegExp(`relaySchemaV2PostgresCatalogSHA256 = "${catalogDigest}"`),
  );
  assert.match(
    relaySchemaIntegrity,
    new RegExp(`relaySchemaV3PostgresCatalogSHA256 = "${catalogDigest}"`),
  );
  assert.match(
    relaySchemaIntegrity,
    /relaySchemaV4PostgresCatalogSHA256 = "sha256:[0-9a-f]{64}"/,
  );
  assert.doesNotMatch(relaySchemaIntegrity, /relaySchemaV4PostgresCatalogSHA256 = "sha256:pending"/);
  assert.match(
    relaySchemaIntegrity,
    /relaySchemaV5PostgresCatalogSHA256 = "sha256:[0-9a-f]{64}"/,
  );
  assert.doesNotMatch(relaySchemaIntegrity, /relaySchemaV5PostgresCatalogSHA256 = "sha256:pending"/);
  assert.match(
    relaySchemaIntegrity,
    /relaySchemaV6PostgresCatalogSHA256 = "sha256:[0-9a-f]{64}"/,
  );
  assert.doesNotMatch(relaySchemaIntegrity, /relaySchemaV6PostgresCatalogSHA256 = "sha256:pending"/);
  assert.match(
    relaySchemaIntegrity,
    /relaySchemaV7PostgresCatalogSHA256 = "sha256:af1377416cb2788093391a03491d2bb380fc4b81db5f08d0d628f3f8a2abef01"/,
  );
  assert.doesNotMatch(relaySchemaIntegrity, /relaySchemaV7PostgresCatalogSHA256 = "sha256:pending"/);
  assert.match(
    relaySchemaIntegrity,
    /relaySchemaV8PostgresCatalogSHA256 = "sha256:6374866475d3b9c404592c39ef5ad8be1b63066308b502e5362dbc47e469823c"/,
  );
  assert.doesNotMatch(relaySchemaIntegrity, /relaySchemaV8PostgresCatalogSHA256 = "sha256:pending"/);

  for (const evidence of [
    /TEST_RELAY_SCHEMA_V5_RELEASE_DSN/,
    /relaySchemaV5FrozenChecksumSHA256/,
    /relaySchemaV6FrozenChecksumSHA256/,
    /relaySchemaV5PostgresCatalogSHA256/,
    /relaySchemaV6PostgresCatalogSHA256/,
    /submission_unknown/,
    /reconciled_no_creation/,
  ]) {
    assert.match(relaySchemaV6PostgresGate, evidence);
  }
  for (const evidence of [
    /TEST_RELAY_SCHEMA_V6_RELEASE_DSN/,
    /relaySchemaV6FrozenChecksumSHA256/,
    /relaySchemaV7FrozenChecksumSHA256/,
    /relaySchemaV6PostgresCatalogSHA256/,
    /relaySchemaV7PostgresCatalogSHA256/,
    /relaySchemaV7RequirePNGCompletion/,
    /image\/jpeg/,
    /unchangedObjectOIDsBefore/,
    /ledgerBefore/,
  ]) {
    assert.match(relaySchemaV7PostgresGate, evidence);
  }
  for (const evidence of [
    /TEST_RELAY_SCHEMA_V7_TO_V8_DSN/,
    /definitions\[:7\]/,
    /relaySchemaV7FrozenVersion/,
    /relaySchemaV8FrozenChecksumSHA256/,
    /relaySchemaV7PostgresCatalogSHA256/,
    /relaySchemaV8PostgresCatalogSHA256/,
    /rollback v8 probe/,
    /v8 must not rewrite the frozen v7 ledger/,
    /PlatformProviderCostAllocationEvidence/,
  ]) {
    assert.match(relaySchemaV8PostgresGate, evidence);
  }

  for (const evidence of [
    /Equal\(t, int64\(0\), result\.FromVersion\)/,
    /Equal\(t, RelaySchemaTargetVersion, result\.Status\.BaselineVersion\)/,
    /Len\(t, freshLedger, 1, "fresh target must not fabricate unexecuted historical ledger events"\)/,
    /Equal\(t, RelaySchemaTargetVersion, freshLedger\[0\]\.Version\)/,
  ]) {
    assert.match(relaySchemaPostgresGate, evidence);
  }
  for (const evidence of [
    /immutable v1 test image/,
    /RelaySchemaStatusCompatible/,
    /RequireRelaySchemaCompatible\(migrationDB\)[\s\S]+RequireRelaySchemaCurrent\(migrationDB\)/,
    /VerifyRelayRuntimeDatabaseRole\(runtimeBefore\)[\s\S]+protected API readiness must reject compatible-but-not-current v1/,
    /VerifyRelayDownloadEdgeDatabaseRole\(migrationDB, relaySchemaV2FrozenVersion\)[\s\S]+protected edge readiness must reject compatible-but-not-current v1/,
    /Equal\(t, int64\(1\), result\.FromVersion\)/,
    /Equal\(t, int64\(2\), result\.ToVersion\)/,
    /Equal\(t, v1Before, ledger\[0\]/,
    /relaySchemaV1PostgresCatalogSHA256, ledger\[1\]\.CatalogSHA256/,
    /Equal\(t, legacyBefore, legacyAfter/,
    /Equal\(t, legacyRowsBefore, legacyRowsAfter/,
    /relayVerifyLegacyCredentialMigrationEvidence\(migrationDB\)/,
  ]) {
    assert.match(relaySchemaV2PostgresGate, evidence);
  }
  for (const root of [
    "func:GetRelaySchemaContract",
    "func:relaySchemaMigrations",
    "func:RunRelaySchemaMigrations",
    "func:RequireRelaySchemaCompatible",
    "func:RequireRelaySchemaCurrent",
  ]) {
    assert.ok(relaySchemaArtifactGate.includes(root), `missing v2 source-artifact root ${root}`);
  }
  assert.match(
    relaySchemaMigrationGate,
    /TestRelaySchemaV5TopLevelMigrationNeverExecutesLiveV1[\s\S]+liveV1Sentinel[\s\S]+fresh v5 bootstrap[\s\S]+exact v1 through v5 bridge/,
  );
  assert.match(
    relaySchemaV5PostgresGate,
    /exact database produced by the pinned immutable v4 binary[\s\S]+rollback-only transaction[\s\S]+migrateRelaySchemaV5ChannelTestDiagnosticTaxonomy/,
  );

  assert.match(
    relayDatabaseRoleAttestation,
    /GetRelayRuntimeDatabaseRoleStatus[\s\S]+RequireRelaySchemaCurrent\(db\)/,
  );
  assert.match(
    relayRuntimeDatabaseRoleProof,
    /func AttestRelayRuntimeDatabaseRoleWithContext[\s\S]+verifyRelayProtectedDatabaseSurfacePreflight\(pinned[\s\S]+RequireRelaySchemaCurrent\(pinned\)[\s\S]+verifyRelayDatabaseRoleTopologyAfterExactSurface\(pinned/,
  );
  const runtimeLiveProof = relayRuntimeDatabaseRoleProof.slice(
    relayRuntimeDatabaseRoleProof.indexOf("func VerifyRelayRuntimeDatabaseRoleProof"),
  );
  assert.match(
    runtimeLiveProof,
    /verifyRelayDownloadEdgeSchemaReleaseProof\(pinned[\s\S]+verifyRelayDatabaseRoleLiveTopology\(pinned[\s\S]+verifyRelayRuntimeDatabasePrivilegeManifestOptimized\(pinned/,
  );
  assert.doesNotMatch(
    runtimeLiveProof,
    /RequireRelaySchemaCurrent|verifyRelayProtectedDatabaseSurfacePreflight|verifyRelayDatabaseRoleTopology\(pinned/,
  );
  assert.match(
    relayAPIReadiness,
    /platformRelayAPICatalogRefreshAfter[\s\S]+platformRelayAPICatalogMaximumAge[\s\S]+platformRelayAPICatalogRefreshTimeout/,
  );
  assert.match(
    relayAPIReadiness,
    /func \(readiness \*PlatformRelayAPIReadiness\) Verify\(ctx context\.Context\)[\s\S]+verifyPlatformRelayAPIDatabaseRoleProof\(requestDB, proof\)[\s\S]+proofStillValid\(proof\)/,
  );
  assert.match(
    relayMain,
    /AttestRelayRuntimeDatabaseRole\(model\.DB\)[\s\S]+NewProtectedPlatformRelayAPIReadiness[\s\S]+InstallProtectedPlatformRelayAPIReadiness[\s\S]+RequireRelaySchemaCompatible\(model\.DB\)/,
  );
  assert.match(
    relayEdgeMain,
    /if protected \{[\s\S]+AttestRelayDownloadEdgeDatabaseRole\(model\.DB\)[\s\S]+else \{[\s\S]+RequireRelaySchemaCompatible\(model\.DB\)/,
  );
  const edgeLiveProof = relayDownloadEdgeDatabaseRoleProof.slice(
    relayDownloadEdgeDatabaseRoleProof.indexOf("func VerifyRelayDownloadEdgeDatabaseRoleProof"),
    relayDownloadEdgeDatabaseRoleProof.indexOf("func verifyRelayDownloadEdgeDatabaseRoleForVersion(")
  );
  assert.match(
    edgeLiveProof,
    /verifyRelayDownloadEdgeSchemaReleaseProof\(pinned[\s\S]+verifyRelayDownloadEdgeDatabaseRoleForVersionLive\(pinned/,
  );
  assert.doesNotMatch(
    edgeLiveProof,
    /RequireRelaySchemaCurrent|verifyRelayProtectedDatabaseExactSurfaceFromEnvironment|verifyRelayDatabaseRoleTopology\(pinned/,
  );
  assert.match(
    relayEdgeService,
    /requestDB := model\.DB\.WithContext\(request\.Context\(\)\)[\s\S]+RequireRelaySchemaCompatible\(requestDB\)[\s\S]+verifyPlatformDownloadEdgeDatabaseRoleProof\(requestDB, protectedProof\)/,
  );
  const verifyProofFunction = relayDatabaseReleaseProof.match(
    /func VerifyPlatformRelayDatabaseReleaseProof\([\s\S]+?\n\}/,
  )?.[0];
  assert.ok(verifyProofFunction, "missing database-proof verifier");
  assert.match(verifyProofFunction, /attestPlatformRelayDatabaseReleaseProof\(db, consumer\)/);
  const proofAttestationFunction = relayDatabaseReleaseProof.slice(
    relayDatabaseReleaseProof.indexOf("func attestPlatformRelayDatabaseReleaseProofInternal("),
    relayDatabaseReleaseProof.indexOf("func attestPlatformRelayDatabaseReleaseProof(\n"),
  );
  assert.ok(
    proofAttestationFunction.startsWith("func attestPlatformRelayDatabaseReleaseProofInternal("),
    "missing database-proof attestation implementation",
  );
  const proofCurrentConsumers = proofAttestationFunction.match(
    /switch consumer \{[\s\S]+?Relay database release proof requires the current schema/,
  )?.[0];
  assert.ok(proofCurrentConsumers, "missing database-proof current-schema consumer gate");
  for (const consumer of ["Post", "Principal", "API", "RootBootstrap", "Edge"]) {
    assert.match(proofCurrentConsumers, new RegExp(`Consumer${consumer}`));
  }
  assert.doesNotMatch(proofCurrentConsumers, /ConsumerPre|ConsumerMigrate/);
  assert.match(
    relayDatabaseReleaseProof,
    /root database release proof requires the current schema[\s\S]+principal rotation database release proof requires the current schema/,
  );

  for (const document of [runbook, relayMigration, deploymentRunbook, releaseReadiness]) {
    assert.match(document, /no-catalog-delta/i);
    assert.match(document, /root[\s\S]+principal[\s\S]+API/i);
  }
  for (const document of [runbook, relayMigration, deploymentRunbook, releaseReadiness]) {
    assert.match(document, new RegExp(`target=${currentRelaySchemaVersion},min=1,max=${currentRelaySchemaVersion}`));
    assert.match(document, new RegExp(`fresh[- ]v${currentRelaySchemaVersion}[\\s\\S]+\\[${currentRelaySchemaVersion}\\]`, "i"));
    assert.match(document, /\[1,2,3,4,5,6,7,8\]/);
    assert.match(document, /max(?:=7|-v7)[\s\S]+ahead/i);
    assert.match(document, /pinned v3/i);
    assert.match(document, /pinned-v4/i);
    assert.match(document, /pinned-v5/i);
    assert.match(document, /pinned[- ]v6/i);
    assert.match(document, /current[- ]v8/i);
    assert.match(document, /TEST_RELAY_SCHEMA_V7_TO_V8_DSN/);
    assert.match(document, /PG16-constructed-v7→v8-gate=PASS \(9\.021s\)/);
    assert.match(document, /PG16\+TLS\+pgAudit-full-qualification-gate=NOT_RUN/i);
    assert.match(document, /v8[\s\S]{0,240}(?:NOT_RUN|未运行|not been run|尚未完成生产资格)/i);
    assert.match(document, /BLOCKED \/ NO-GO/);
    assert.doesNotMatch(document, /v7-to-v8[^\n`]*=PASS/i);
  }
  assert.match(runbook, /pinned-v4 digest is only an immutable binary behavior fixture/i);
  assert.match(runbook, /gate-owned exact-v3 synthetic/i);
  assert.match(runbook, /pinned-v5 digest is likewise an immutable binary behavior fixture/i);
  assert.match(
    runbook,
    /no pinned image may connect to a business\/live database or act as an operational migration source/i,
  );
  for (const document of [relayMigration, deploymentRunbook, releaseReadiness]) {
    assert.match(document, /pinned-v4[\s\S]{0,120}immutable binary\s+behavior fixture/i);
    assert.match(document, /门禁[\s\S]{0,160}exact-v3/i);
    assert.match(document, /不得[\s\S]{0,160}业务\/live 数据库/i);
    assert.match(document, /不是[\s\S]{0,160}迁移源/i);
  }
  assert.match(runbook, /pinned-v4-state-ledger-catalog-acl-guards-gate=PASS/);
  assert.match(deploymentRunbook, /pinned-v4-state-ledger-catalog-acl-guards-gate=PASS/);
  for (const document of [runbook, relayMigration, deploymentRunbook, releaseReadiness]) {
    assert.match(document, /pre-v4-zero-acl-role-stub-gate=PASS/);
    assert.match(document, /pre-v5-zero-acl-role-stub-gate=PASS/);
    assert.match(document, /pre-v6-zero-acl-role-stub-gate=PASS/);
    assert.match(document, /pre-v7-zero-acl-role-stub-gate=PASS/);
    assert.match(
      document,
      /pinned-v3 migration[\s\S]+pre-v4 zero-ACL role-pre[\s\S]+pinned-v4 migration[\s\S]+pinned-v4 full verifier[\s\S]+pre-v5 zero-ACL role-pre[\s\S]+pinned-v5 migration[\s\S]+pinned-v5 rollback verifier[\s\S]+pre-v6 zero-ACL role-pre[\s\S]+pinned-v6 migration[\s\S]+pinned-v6 rollback verifier[\s\S]+pre-v7 zero-ACL role-pre[\s\S]+historical-v7 migration/i,
    );
    assert.match(document, /role-pre[^\n]+does not perform schema migration/i);
  }
  assert.match(deploymentRunbook, /0012_generation_contract_v1/);
  assert.match(releaseReadiness, /schema_version=1/);
});

test("pins a non-empty Relay release version and rejects invalid image builds", () => {
  assert.match(
    relayVersion,
    /^v[0-9]+\.[0-9]+\.[0-9]+(?:[+.-][0-9A-Za-z.+-]+)?\r?\n$/,
  );
  assert.equal(relayVersion.trim(), "v1.0.0-rc.23");

  for (const [name, source] of [
    ["production", relayDockerfile],
    ["development", relayDevelopmentDockerfile],
  ]) {
    assert.match(source, /test "\$\(wc -l < (?:\/build\/)?VERSION\)" -eq 1/);
    assert.match(source, /test -n "\$version"/);
    assert.match(source, /test "\$\{#version\}" -le 50/);
    assert.match(
      source,
      /grep -Eq '\^v\[0-9\]\+\\\.\[0-9\]\+\\\.\[0-9\]\+\(\[\+\.\-\]\[0-9A-Za-z\.\+\-\]\+\)\?\$'/,
      `${name} image must reject an empty or malformed VERSION before go build`,
    );
  }
  assert.match(relayDockerfile, /^ARG RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256\r?$/m);
  assert.doesNotMatch(relayDockerfile, /ARG RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256=unknown/);
  assert.match(relayDockerfile, /grep -Eq '\^sha256:\[0-9a-f\]\{64\}\$'/);
  assert.match(relayDockerfile, /go run \.\/cmd\/relay-source-snapshot/);
  assert.match(relayDockerfile, /platformRelayCompiledRouteAcceptanceKeysSHA256/);
  assert.match(sharedEnv, /^NEW_API_RELAY_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON=\{\}$/m);
  assert.match(sharedEnv, /^NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256=sha256:0{64}$/m);
  assert.match(
    runbook,
    /NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256[\s\S]+RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256/,
  );
  assert.match(
    runbook,
    /canonical[\s\S]+public-[\s\S]*key set[\s\S]+compiled[\s\S]+digest/i,
  );
  assert.match(
    runbook,
    /NEW_API_RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE[\s\S]+(?:0400|0600)[\s\S]+uid 10001/,
  );
});

test("keeps local defaults development-only and makes new-api the exclusive Relay topology", () => {
  const baseRelay = serviceBlock(base, "relay-new-api");
  assert.doesNotMatch(baseRelay, /^\s+profiles:/m);
  assert.match(baseRelay, /\/health\/ready/);
  assert.doesNotMatch(baseRelay, /\/health\/live/);
  assert.match(base, /NEW_API_RELAY_CHANNEL_TEST_ENABLED:-false/);
  assert.match(base, /BATCH_UPDATE_ENABLED:\s*"false"/);
  assert.match(base, /RELAY_CODEX_CREDENTIAL_AUTO_REFRESH_ENABLED:\s*"false"/);
  assert.match(base, /RELAY_NATIVE_PAID_COMPAT_ENABLED:\s*"false"/);
  assert.match(relayDevelopmentCompose, /BATCH_UPDATE_ENABLED:\s*"false"/);
  assert.match(relayDevelopmentCompose, /RELAY_CODEX_CREDENTIAL_AUTO_REFRESH_ENABLED:\s*"false"/);
  assert.match(relayDevelopmentCompose, /RELAY_NATIVE_PAID_COMPAT_ENABLED:\s*"false"/);
  assert.match(base, /PLATFORM_RELAY_BASE_URL:-http:\/\/relay-new-api:3000/);

  for (const name of [
    "relay-artifact-init",
    "relay-api",
    "relay-outbox",
    "relay-worker",
    "relay-transfer-worker",
    "relay-provider-sync",
    "relay-provider-monitor",
    "relay-callback-worker",
  ]) {
    assert.doesNotMatch(base, new RegExp(`^  ${name}:`, "m"));
    assert.doesNotMatch(secure, new RegExp(`^  ${name}:`, "m"));
  }
  assert.doesNotMatch(base, /\.\/backend\/relay|x-relay-environment|python-relay-rollback/);
  assert.doesNotMatch(secure, /python-relay-rollback/);
  const secureRelay = serviceBlock(secure, "relay-new-api");
  assert.match(secureRelay, /profiles:\s*!reset\s*\[\]/);
  assert.match(secureRelay, /build:\s*!reset\s+null/);
  assert.match(secureRelay, /image:[^\r\n]+@\$\{NEW_API_RELAY_IMAGE_DIGEST:/);
  assert.match(secureRelay, /depends_on:\s*!override/);
  assert.match(secureRelay, /relay-new-api-volume-init:[\s\S]*?service_completed_successfully/);
  assert.doesNotMatch(secureRelay, /condition:\s*service_healthy/);
  assert.match(secureRelay, /SESSION_COOKIE_SECURE:\s*"true"/);
  assert.match(secureRelay, /SESSION_COOKIE_TRUSTED_URL:\s*\$\{NEW_API_RELAY_PUBLIC_BASE_URL:/);
  assert.match(
    secureRelay,
    /SESSION_COOKIE_TRUSTED_URL:[^\r\n]+\$\{PLATFORM_RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN:/,
  );
  assert.match(secureRelay, /user:\s*"10001:10001"/);
  assert.match(secureRelay, /read_only:\s*true/);
  assert.match(secureRelay, /cap_drop:\s*\["ALL"\]/);
  assert.match(secureRelay, /no-new-privileges:true/);
  assert.match(secureRelay, /stop_grace_period:\s*260s/);
  assert.match(secureRelay, /BATCH_UPDATE_ENABLED:\s*"false"/);
  assert.match(secureRelay, /RELAY_CODEX_CREDENTIAL_AUTO_REFRESH_ENABLED:\s*"false"/);
  assert.match(secureRelay, /RELAY_NATIVE_PAID_COMPAT_ENABLED:\s*"false"/);

  const securePlatform = serviceBlock(secure, "platform-api");
  assert.match(securePlatform, /DEVELOPMENT_HEADER_AUTH_ENABLED:\s*"false"/);
  assert.match(securePlatform, /ENABLE_BOOTSTRAP:\s*"false"/);
  assert.doesNotMatch(securePlatform, /BOOTSTRAP_TOKEN/);
  assert.match(securePlatform, /environment:\s*!override/);
  assert.match(
    securePlatform,
    /PLATFORM_PROCESS_RUNTIME_SECRETS_FILE:\s*\/run\/secrets\/platform-api-runtime-secrets\.json/,
  );
  assert.match(
    securePlatform,
    /RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN:\s*\$\{PLATFORM_RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN:/,
  );
});

test("repairs legacy Relay volume ownership before starting the non-root service", () => {
  const baseInit = serviceBlock(base, "relay-new-api-volume-init");
  const baseRelay = serviceBlock(base, "relay-new-api");
  const secureInit = serviceBlock(secure, "relay-new-api-volume-init");
  const secureRelay = serviceBlock(secure, "relay-new-api");

  assert.match(baseInit, /user:\s*"0:0"/);
  assert.match(baseInit, /read_only:\s*true/);
  assert.match(baseInit, /cap_drop:\s*\["ALL"\]/);
  assert.match(baseInit, /cap_add:\s*\["CHOWN",\s*"FOWNER",\s*"DAC_OVERRIDE"\]/);
  assert.match(baseInit, /chown -R 10001:10001 \/data \/app\/logs \/artifacts/);
  assert.match(baseInit, /chmod 0700 \/data \/app\/logs \/artifacts/);
  for (const volume of [
    "relay-new-api-runtime-data:/data",
    "relay-new-api-logs:/app/logs",
    "relay-new-api-artifacts:/artifacts",
  ]) {
    assert.ok(baseInit.includes(volume), `volume initializer missing ${volume}`);
  }
  assert.match(baseRelay, /relay-new-api-volume-init:[\s\S]*?service_completed_successfully/);
  assert.match(secureInit, /profiles:\s*!reset\s*\[\]/);
  assert.match(secureInit, /build:\s*!reset\s+null/);
  assert.match(secureInit, /image:[^\r\n]+@\$\{NEW_API_RELAY_IMAGE_DIGEST:/);
  assert.match(secureRelay, /relay-new-api-volume-init:[\s\S]*?service_completed_successfully/);
  assert.match(runbook, /legacy root-owned named volumes[\s\S]+relay-new-api-volume-init/);
});

test("validates the global new-api secret set before any protected consumer", () => {
  const validator = serviceBlock(secure, "relay-new-api-secret-isolation");
  const init = serviceBlock(secure, "relay-new-api-volume-init");
  assert.match(validator, /entrypoint:\s*!override \["\/new-api",\s*"relay-validate-secret-isolation"\]/);
  assert.match(validator, /network_mode:\s*none/);
  assert.match(validator, /relay-new-api-volume-init:[\s\S]*?service_completed_successfully/);
  assert.match(validator, /RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED:\s*"true"/);
  assert.match(validator, /RELAY_DATABASE_TLS_ATTESTATION_REQUIRED:\s*"true"/);
  assert.match(validator, /RELAY_SECRET_ISOLATION_GENERATION:\s*root-proof-present/);
  assert.match(validator, /RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE:\s*\/run\/relay-root-secret-isolation-proof\/proof\.json/);
  assert.match(validator, /relay-new-api-root-secret-isolation-proof:\/run\/relay-root-secret-isolation-proof:ro/);
  assert.match(validator, /RELAY_SCHEMA_OWNER_DATABASE_ROLE:\s*\$\{NEW_API_RELAY_SCHEMA_OWNER_DATABASE_ROLE:/);
  assert.match(validator, /RELAY_MIGRATION_DATABASE_ROLE:\s*\$\{NEW_API_RELAY_MIGRATION_DATABASE_ROLE:/);
  assert.doesNotMatch(validator, /^\s+(?:SQL_DSN|REDIS_CONN_STRING|SESSION_SECRET):\s/m);
  for (const source of [
    "relay-role-admin-dsn",
    "relay-migration-sql-dsn",
    "relay-runtime-sql-dsn",
    "relay-download-edge-sql-dsn",
    "relay-migration-password",
    "relay-runtime-password",
    "relay-download-edge-password",
    "relay-service-principals.json",
    "relay-api-runtime-secrets.json",
    "relay-download-edge-runtime-secrets.json",
    "relay-provider-credential-keyring",
    "relay-redis-tls-ca.pem",
    "platform-migration-runtime-secrets.json",
    "platform-api-runtime-secrets.json",
    "platform-dispatcher-runtime-secrets.json",
    "platform-relay-sync-runtime-secrets.json",
    "platform-relay-catalog-sync-runtime-secrets.json",
    "platform-timeout-worker-runtime-secrets.json",
    "platform-publishing-worker-runtime-secrets.json",
    "platform-download-gateway-registration-worker-runtime-secrets.json",
  ]) {
    assert.ok(validator.includes(source), `isolation validator missing ${source}`);
  }

  const consumers = new Map([
    ["relay-new-api-db-role-pre", "pre"],
    ["relay-new-api-migrate", "migrate"],
    ["relay-new-api-db-role-post", "post"],
    ["relay-new-api-service-principal-provision", "principal"],
    ["relay-new-api", "api"],
    ["relay-download-edge", "edge"],
    ["platform-db-role-pre", "platform-db-role-pre"],
    ["platform-migrate", "platform-migration"],
    ["platform-api", "platform-api"],
    ["platform-dispatcher", "platform-dispatcher"],
    ["platform-relay-sync", "platform-relay-sync"],
    ["platform-relay-catalog-sync", "platform-relay-catalog-sync"],
    ["platform-timeout-worker", "platform-timeout-worker"],
    ["platform-publishing-worker", "platform-publishing-worker"],
    [
      "platform-download-gateway-registration-worker",
      "platform-download-gateway-registration-worker",
    ],
  ]);
  for (const [name, receipt] of consumers) {
    const block = serviceBlock(secure, name);
    assert.match(block, /RELAY_SECRET_ISOLATION_RECEIPT_FILE:\s*\/run\/relay-secret-isolation\/receipt\.json/);
    assert.ok(
      block.includes(`relay-new-api-secret-isolation-${receipt}:/run/relay-secret-isolation:ro`),
      `${name} must mount only its own isolation receipt`,
    );
    for (const other of consumers.values()) {
      if (other !== receipt) {
        assert.ok(
          !block.includes(`relay-new-api-secret-isolation-${other}:/run/relay-secret-isolation`),
          `${name} must not mount the ${other} receipt`,
        );
      }
    }
    assert.match(block, /RELAY_COMPAT_IMAGE_DIGEST:\s*\$\{NEW_API_RELAY_IMAGE_DIGEST:/);
    assert.match(block, /RELAY_COMPAT_SOURCE_REVISION:\s*\$\{NEW_API_RELAY_SOURCE_REVISION:/);
    assert.match(block, /RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256:\s*\$\{NEW_API_RELAY_SOURCE_SNAPSHOT_SHA256:/);
    assert.match(block, /RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT:\s*\$\{NEW_API_RELAY_SOURCE_SNAPSHOT_FILE_COUNT:/);
    assert.match(block, /RELAY_COMPAT_UPSTREAM_REVISION:\s*0ab02020603d22e5613bc4cf46bfab06f8567769/);
    assert.match(block, /RELAY_COMPAT_ROUTE_ACCEPTANCE_TRUST_KEYS_SHA256:\s*\$\{NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256:/);
    assert.match(block, /PLATFORM_IMAGE:\s*\$\{PLATFORM_IMAGE:/);
    assert.match(block, /PLATFORM_SOURCE_REVISION:\s*\$\{PLATFORM_SOURCE_REVISION:/);
    assert.match(block, /PLATFORM_SOURCE_SNAPSHOT_SHA256:\s*\$\{PLATFORM_SOURCE_SNAPSHOT_SHA256:/);
  }
  assert.match(serviceBlock(secure, "relay-new-api-db-role-pre"), /relay-new-api-secret-isolation:[\s\S]*?service_completed_successfully/);
  for (const receipt of consumers.values()) {
    assert.ok(init.includes(`relay-new-api-secret-isolation-${receipt}:/run/relay-secret-isolation/${receipt}`));
  }
  assert.match(runbook, /relay-new-api-volume-init[\s\S]+relay-new-api-secret-isolation[\s\S]+relay-new-api-db-role-pre/);
  assert.match(
    runbook,
    new RegExp(`kind=relay_secret_isolation[\\s\\S]+state=validated[\\s\\S]+consumers=${consumers.size}`),
  );
  assert.match(
    runbook,
    /canonical and bare service-token forms[\s\S]+encoded and decoded edge keys[\s\S]+decoded\s+database passwords/,
  );
  assert.match(runbook, /atomically installs copies[\s\S]+immutable protected-file snapshots[\s\S]+do not reopen/);
  assert.match(runbook, /exact\s+allowlist[\s\S]+password[\s\S]+passfile[\s\S]+sslpassword[\s\S]+sslkey/);
  assert.match(
    validator,
    /RELAY_REDIS_TLS_CA_FILE:\s*\/run\/secrets\/relay-redis-tls-ca\.pem/,
  );
  assert.match(
    validator,
    /\$\{NEW_API_RELAY_REDIS_TLS_CA_FILE:[^\r\n]+\}[\s\S]+target:\s*\/run\/secrets\/relay-redis-tls-ca\.pem[\s\S]+read_only:\s*true/,
  );
  const relayApi = serviceBlock(secure, "relay-new-api");
  assert.match(
    relayApi,
    /RELAY_REDIS_TLS_CA_FILE:\s*\/run\/secrets\/relay-redis-tls-ca\.pem/,
  );
  assert.match(relayApi, /relay-redis-tls-ca\.pem[\s\S]+read_only:\s*true/);
  for (const service of [
    "relay-new-api-db-role-pre",
    "relay-new-api-migrate",
    "relay-new-api-db-role-post",
    "relay-new-api-root-provision",
    "relay-new-api-service-principal-provision",
    "relay-download-edge",
  ]) {
    assert.doesNotMatch(
      serviceBlock(secure, service),
      /RELAY_REDIS_TLS_CA_FILE|relay-redis-tls-ca\.pem/,
      `${service} must not receive the API-only Redis trust bundle`,
    );
  }
  assert.match(sharedEnv, /^NEW_API_RELAY_REDIS_TLS_CA_FILE=/m);
  assert.match(
    runbook,
    /Redis trust[\s\S]+RELAY_REDIS_TLS_CA_FILE[\s\S]+SSL_CERT_FILE/,
  );
});

test("publishes one generation-bound Relay database proof and mounts it read-only downstream", () => {
  const init = serviceBlock(secure, "relay-new-api-volume-init");
  const pre = serviceBlock(secure, "relay-new-api-db-role-pre");
  assert.match(init, /relay-new-api-database-release-proof:\/run\/relay-database-release-proof/);
  assert.match(init, /\/run\/relay-database-release-proof[\s\S]+chmod 0700/);
  assert.match(secure, /^  relay-new-api-database-release-proof:\s*$/m);
  assert.match(
    pre,
    /RELAY_DATABASE_RELEASE_PROOF_DIRECTORY:\s*\/run\/relay-database-release-proof/,
  );
  assert.match(
    pre,
    /RELAY_DATABASE_RELEASE_PROOF_FILE:\s*\/run\/relay-database-release-proof\/receipt\.json/,
  );
  assert.match(pre, /relay-new-api-database-release-proof:\/run\/relay-database-release-proof(?:\r?\n|$)/);
  assert.doesNotMatch(pre, /relay-new-api-database-release-proof:\/run\/relay-database-release-proof:ro/);

  for (const service of [
    "relay-new-api-migrate",
    "relay-new-api-db-role-post",
    "relay-new-api-root-provision",
    "relay-new-api-service-principal-provision",
    "relay-new-api",
    "relay-download-edge",
  ]) {
    const block = serviceBlock(secure, service);
    assert.match(
      block,
      /RELAY_DATABASE_RELEASE_PROOF_FILE:\s*\/run\/relay-database-release-proof\/receipt\.json/,
    );
    assert.match(
      block,
      /relay-new-api-database-release-proof:\/run\/relay-database-release-proof:ro/,
    );
    assert.doesNotMatch(block, /RELAY_DATABASE_RELEASE_PROOF_DIRECTORY:/);
  }
  assert.match(
    runbook,
    /relay-new-api-root-provision[\s\S]+relay-new-api-secret-isolation relay-new-api-secret-isolation[\s\S]+relay-new-api-db-role-pre relay-new-api-db-role-pre[\s\S]+relay-new-api-migrate relay-new-api-migrate[\s\S]+relay-new-api-db-role-post relay-new-api-db-role-post[\s\S]+platform-db-role-pre/,
  );
  assert.match(
    runbook,
    /Relay database release proof[\s\S]+post-root marker[\s\S]+Platform role provisioning/,
  );
});

test("orders same-image role pre, migration, edge post, and runtime on the managed-state network", () => {
  const pre = serviceBlock(secure, "relay-new-api-db-role-pre");
  const migrate = serviceBlock(secure, "relay-new-api-migrate");
  const post = serviceBlock(secure, "relay-new-api-db-role-post");
  const relay = serviceBlock(secure, "relay-new-api");
  const edge = serviceBlock(secure, "relay-download-edge");

  for (const service of [pre, migrate, post, relay, edge]) {
    assert.match(service, /image:[^\r\n]+@\$\{NEW_API_RELAY_IMAGE_DIGEST:/);
    assert.match(service, /RELAY_DATABASE_SECRET_FILES_REQUIRED:\s*"true"/);
    assert.match(service, /RELAY_DATABASE_TLS_ATTESTATION_REQUIRED:\s*"true"/);
    assert.match(service, /- relay-new-api-managed-state/);
    assert.doesNotMatch(service, /^\s+(?:SQL_DSN|RELAY_DOWNLOAD_EDGE_SQL_DSN):\s/m);
  }
  assert.match(pre, /entrypoint:\s*!override \["\/new-api",\s*"relay-provision-database-roles"\]/);
  assert.match(pre, /SQL_DSN_FILE:\s*\/run\/secrets\/relay-role-admin-dsn/);
  assert.match(pre, /RELAY_MIGRATION_DATABASE_PASSWORD_FILE:\s*\/run\/secrets\/relay-migration-password/);
  assert.match(pre, /RELAY_RUNTIME_DATABASE_PASSWORD_FILE:\s*\/run\/secrets\/relay-runtime-password/);
  assert.match(pre, /RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE:\s*\/run\/secrets\/relay-download-edge-password/);
  assert.doesNotMatch(pre, /RELAY_PROVIDER_CREDENTIAL_KEYRING/);
  assert.doesNotMatch(post, /RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE|relay-download-edge-password/);
  assert.match(migrate, /relay-new-api-db-role-pre:[\s\S]*?service_completed_successfully/);
  assert.match(post, /relay-new-api-migrate:[\s\S]*?service_completed_successfully/);
  assert.match(relay, /relay-new-api-db-role-post:[\s\S]*?service_completed_successfully/);
  assert.match(edge, /relay-new-api-db-role-post:[\s\S]*?service_completed_successfully/);
  assert.match(relay, /stop_grace_period:\s*260s/);
  assert.match(edge, /stop_grace_period:\s*40s/);
  assert.doesNotMatch(secure, /provision-relay-database-roles\.sql|relay-generate-role-verifiers|psql\s/);
  assert.match(secure, /relay-new-api-managed-state:[\s\S]+external:\s*true/);
  assert.match(sharedEnv, /^NEW_API_RELAY_MANAGED_STATE_NETWORK=/m);
  assert.match(runbook, /up --force-recreate --no-deps --abort-on-container-exit --exit-code-from relay-new-api-db-role-pre relay-new-api-db-role-pre[\s\S]+relay-new-api-migrate[\s\S]+relay-new-api-db-role-post/);
  assert.match(runbook, /\$relayEnvironment = 'production'[\s\S]+\$relayCompose = @\([\s\S]+relay-secure\.env[\s\S]+compose\.relay\.secure\.yml[\s\S]+compose\.relay\.\$relayEnvironment\.yml/);
  assert.match(runbook, /stop --timeout 260 relay-new-api relay-download-edge[\s\S]+Confirm both containers are exited[\s\S]+pg_catalog\.pg_locks[\s\S]+relay-new-api-db-role-pre/);
  assert.match(runbook, /process gate A[\s\S]+mutation fence B[\s\S]+pg_catalog\.pg_locks[\s\S]+classid = 1096173892[\s\S]+objid IN \(1380929356, 1380929357\)[\s\S]+acceptable result before `pre` is no rows/);
  assert.match(runbook, /four pairwise-distinct[\s\S]+role-admin[\s\S]+migration[\s\S]+runtime[\s\S]+edge/i);
  assert.match(runbook, /three independent SCRAM verifiers[\s\S]+Post[\s\S]+does not mount or receive any password/);
  assert.match(runbook, /edge state C[\s\S]+state B[\s\S]+exact A/);
  assert.match(runbook, /API readiness[\s\S]+neither B nor C can serve/);
});

test("provisions the production application root only through a hardened manual one-shot", () => {
  const provisioner = serviceBlock(secure, "relay-new-api-root-provision");
  const rootValidator = serviceBlock(secure, "relay-new-api-root-secret-isolation");
  const preRootValidator = serviceBlock(secure, "relay-new-api-secret-isolation-pre-root");
  const relay = serviceBlock(secure, "relay-new-api");

  assert.match(preRootValidator, /profiles:\s*\["relay-root-provision"\]/);
  assert.match(preRootValidator, /service:\s*relay-new-api-secret-isolation/);
  assert.match(preRootValidator, /RELAY_SECRET_ISOLATION_GENERATION:\s*pre-root/);
  assert.match(rootValidator, /entrypoint:\s*!override \["\/new-api",\s*"relay-validate-root-secret-isolation-v1"\]/);
  assert.match(rootValidator, /RELAY_ROOT_SECRET_ISOLATION_PROOF_DIRECTORY:\s*\/run\/relay-root-secret-isolation-proof-write/);
  assert.match(rootValidator, /relay-new-api-root-secret-isolation-proof:\/run\/relay-root-secret-isolation-proof-write/);
  assert.match(rootValidator, /relay-new-api-secret-isolation-root-bootstrap:\/run\/relay-secret-isolation\/root-bootstrap/);

  assert.match(provisioner, /profiles:\s*\["relay-root-provision"\]/);
  assert.match(provisioner, /image:[^\r\n]+@\$\{NEW_API_RELAY_IMAGE_DIGEST:/);
  assert.match(provisioner, /entrypoint:\s*\["\/new-api",\s*"relay-provision-root"\]/);
  assert.match(provisioner, /APP_ENV:\s*\$\{RELAY_DEPLOYMENT_ENV:/);
  assert.match(provisioner, /DEPLOYMENT_ENV:\s*\$\{RELAY_DEPLOYMENT_ENV:/);
  assert.match(provisioner, /NODE_TYPE:\s*master/);
  assert.match(provisioner, /SQL_DSN_FILE:\s*\/run\/secrets\/relay-runtime-sql-dsn/);
  assert.doesNotMatch(provisioner, /^\s+SQL_DSN:\s/m);
  assert.match(provisioner, /RELAY_PROVISION_ROOT_USERNAME:\s*\$\{NEW_API_RELAY_ROOT_USERNAME:/);
  assert.match(
    provisioner,
    /RELAY_PROVISION_ROOT_PASSWORD_FILE:\s*\/run\/secrets\/relay-new-api-root-password/,
  );
  assert.doesNotMatch(provisioner, /^\s+RELAY_PROVISION_ROOT_PASSWORD:\s/m);
  assert.match(provisioner, /source:\s*\$\{NEW_API_RELAY_ROOT_PASSWORD_FILE:/);
  assert.match(provisioner, /target:\s*\/run\/secrets\/relay-new-api-root-password/);
  assert.match(provisioner, /source:\s*\$\{NEW_API_RELAY_RUNTIME_SQL_DSN_FILE:/);
  assert.match(provisioner, /target:\s*\/run\/secrets\/relay-runtime-sql-dsn/);
  assert.match(provisioner, /RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE:\s*\/run\/relay-root-secret-isolation-proof\/proof\.json/);
  assert.match(provisioner, /relay-new-api-root-secret-isolation-proof:\/run\/relay-root-secret-isolation-proof:ro/);
  assert.match(provisioner, /relay-new-api-secret-isolation-root-bootstrap:\/run\/relay-secret-isolation:ro/);
  assert.match(provisioner, /relay-new-api-root-secret-isolation:[\s\S]*?service_completed_successfully/);
  assert.match(provisioner, /user:\s*"10001:10001"/);
  assert.match(provisioner, /read_only:\s*true/);
  assert.match(provisioner, /cap_drop:\s*\["ALL"\]/);
  assert.match(provisioner, /no-new-privileges:true/);
  assert.match(provisioner, /restart:\s*"no"/);
  assert.match(provisioner, /- relay-new-api-managed-state/);
  assert.doesNotMatch(provisioner, /- relay-new-api-edge/);
  assert.doesNotMatch(provisioner, /RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE/);
  assert.doesNotMatch(relay, /relay-new-api-root-provision/);

  assert.doesNotMatch(secure, /^\s{2}relay-new-api-root-password:\s*$/m);
  assert.doesNotMatch(secure, /^\s{2}relay-provider-credential-keyring:\s*$/m);
  assert.match(
    relay,
    /source:\s*\$\{NEW_API_RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE:/,
  );
  assert.match(relay, /target:\s*\/run\/secrets\/relay-provider-credential-keyring/);
  assert.match(relay, /read_only:\s*true/);
  assert.match(relay, /create_host_path:\s*false/);
  assert.match(sharedEnv, /^NEW_API_RELAY_ROOT_USERNAME=root-admin$/m);
  assert.match(
    sharedEnv,
    /^NEW_API_RELAY_ROOT_PASSWORD_FILE=\.\/deploy\/secrets\/relay-new-api-root-password$/m,
  );
  assert.doesNotMatch(sharedEnv, /^NEW_API_RELAY_ROOT_PASSWORD=/m);
  assert.match(
    sharedEnv,
    /^NEW_API_RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE=\.\/deploy\/secrets\/relay-provider-credential-keyring\.json$/m,
  );
  assert.match(gitignore, /^\/deploy\/secrets\/$/m);
  assert.match(runbook, /relay-root-provision[\s\S]+run --rm --no-deps relay-new-api-root-provision/);
  assert.match(runbook, /never runs it automatically/);
  assert.match(runbook, /sslmode=verify-full/);
  assert.match(runbook, /exact\s+retry[\s\S]+without changing the user id, password hash, creation time, or Setup marker/);
  assert.match(runbook, /protected staging or\s+production API start/);
  assert.match(runbook, /Both protected environments disable anonymous `\/api\/setup` before any database\s+access/);
  assert.match(runbook, /Neither staging nor production has an HTTP setup fallback/);
  assert.match(runbook, /securely remove the rendered host password\s+file/);
  assert.match(runbook, /POSIX mode `0600` or an equivalent Windows ACL/);
  assert.match(runbook, /file-source secrets are bind mounts[\s\S]+ignore Compose[\s\S]+`uid`, `gid`, and `mode`/);
  assert.match(runbook, /readable but not\s+writable as uid 10001/);
  assert.match(runbook, /test -r \/run\/secrets\/relay-new-api-root-password/);
  assert.match(runbook, /test ! -w \/run\/secrets\/relay-new-api-root-password/);
  assert.match(runbook, /After every provisioning attempt/);
  assert.match(runbook, /permanent forbidden-root proof[\s\S]+encrypted backup/i);
  assert.match(runbook, /never[\s\S]+down -v/i);
  assert.match(runbook, /pre-root[\s\S]+root-secret-isolation[\s\S]+root-provision[\s\S]+root-proof-present/i);
  assert.match(serviceBlock(secure, "relay-new-api-volume-init"), /\.proof\.lock[\s\S]+chmod 0600/);
  assert.doesNotMatch(sharedEnv, /^PLATFORM_DATABASE_URL=/m);
  assert.match(
    sharedEnv,
    /^PLATFORM_MIGRATION_RUNTIME_SECRETS_FILE=\.\/deploy\/secrets\/platform-migration-runtime-secrets\.json$/m,
  );
  for (const variable of [
    "NEW_API_RELAY_RUNTIME_SQL_DSN_FILE",
    "NEW_API_RELAY_MIGRATION_SQL_DSN_FILE",
    "RELAY_DOWNLOAD_EDGE_SQL_DSN_FILE",
    "NEW_API_RELAY_ROLE_ADMIN_SQL_DSN_FILE",
  ]) {
    assert.match(sharedEnv, new RegExp(`^${variable}=\\.\\/deploy\\/secrets\\/`, "m"));
  }
});

test("provisions the exact service-principal set after the one-time root bootstrap", () => {
  const principal = serviceBlock(secure, "relay-new-api-service-principal-provision");
  const root = serviceBlock(secure, "relay-new-api-root-provision");
  const relay = serviceBlock(secure, "relay-new-api");

  assert.match(root, /profiles:\s*\["relay-root-provision"\]/);
  assert.match(principal, /profiles:\s*!reset\s*\[\]/);
  assert.match(principal, /entrypoint:\s*\["\/new-api",\s*"relay-provision-service-principals"\]/);
  assert.match(principal, /SQL_DSN_FILE:\s*\/run\/secrets\/relay-runtime-sql-dsn/);
  assert.match(principal, /RELAY_SERVICE_PRINCIPALS_FILE:\s*\/run\/secrets\/relay-service-principals\.json/);
  assert.match(principal, /source:\s*\$\{NEW_API_RELAY_SERVICE_PRINCIPALS_FILE:/);
  assert.match(principal, /target:\s*\/run\/secrets\/relay-service-principals\.json/);
  assert.match(principal, /relay-new-api-db-role-post:[\s\S]*?service_completed_successfully/);
  assert.doesNotMatch(principal, /relay-new-api-root-provision/);
  assert.doesNotMatch(
    principal,
    /REDIS_CONN_STRING|SESSION_SECRET|CRYPTO_SECRET|RELAY_PROVIDER_CREDENTIAL_KEYRING|HUAWEI_OBS|RELAY_PROVISION_ROOT_PASSWORD/,
  );
  assert.match(relay, /relay-new-api-service-principal-provision:[\s\S]*?service_completed_successfully/);
  assert.doesNotMatch(relay, /relay-new-api-root-provision/);
  assert.match(
    sharedEnv,
    /^NEW_API_RELAY_SERVICE_PRINCIPALS_FILE=\.\/deploy\/secrets\/relay-service-principals\.json$/m,
  );
  assert.match(
    runbook,
    /fresh protected staging or production database[\s\S]+relay-new-api-secret-isolation-pre-root[\s\S]+relay-new-api-root-secret-isolation[\s\S]+relay-new-api-root-provision[\s\S]+relay-new-api-service-principal-provision/,
  );
  assert.match(
    runbook,
    /ordinary rollout or rollback[\s\S]+never run the root command[\s\S]+relay-new-api-service-principal-provision[\s\S]+up -d relay-new-api relay-download-edge/,
  );
  assert.match(runbook, /long-lived API depends on this successful one-shot, but never on root\s+provisioning/);
});

test("uses readiness for production service traffic while retaining liveness", () => {
  const secureRelay = serviceBlock(secure, "relay-new-api");
  assert.match(secureRelay, /http:\/\/127\.0\.0\.1:3000\/health\/ready/);
  assert.doesNotMatch(secureRelay, /health\/live/);
  assert.match(secureRelay, /body=\$\$\(wget[^\r\n]+health\/ready\)\s*&&/);
  assert.match(secureRelay, /grep -Eq '\^\[\[:space:\]\]\*\\\{/);
  assert.match(secureRelay, /\(healthy\|degraded\)/);
  assert.match(base, /http:\/\/127\.0\.0\.1:3000\/health\/ready/);
  assert.doesNotMatch(serviceBlock(base, "relay-new-api"), /health\/live/);
  assert.match(runbook, /`\/health\/live` is process liveness only/);
  assert.match(runbook, /`\/health\/ready` is the Compose and load-balancer service gate/);
  assert.match(runbook, /`degraded` is not cutover approval/);
});

test("requires workers, native probes, operations, approval, OBS, alerts, cost, telemetry, and provenance", () => {
  const relay = serviceBlock(secure, "relay-new-api");
  for (const pattern of [
    /RELAY_COMPAT_WORKER_ENABLED:\s*"true"/,
    /NODE_TYPE:\s*master/,
    /CHANNEL_TEST_ENABLED:\s*"true"/,
    /CHANNEL_TEST_FREQUENCY:/,
    /RELAY_SERVICE_PRINCIPALS_FILE:\s*\/run\/secrets\/relay-service-principals\.json/,
    /RELAY_API_RUNTIME_SECRETS_FILE:\s*\/run\/secrets\/relay-api-runtime-secrets\.json/,
    /RELAY_PLATFORM_CONTROL_TENANT_ID:\s*\$\{RELAY_TENANT_ID:/,
    /RELAY_ARTIFACT_STORE:\s*huawei_obs/,
    /RELAY_PROVIDER_MONITOR_ENABLED:\s*"true"/,
    /RELAY_PROVIDER_ALERT_WEBHOOK_URL:/,
    /RELAY_PROVIDER_CONTRACT_RATES_JSON:/,
    /RELAY_PLATFORM_CHANNEL_COST_URL:/,
    /RELAY_PLATFORM_TASK_STAGE_URL:/,
    /RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL:/,
    /RELAY_COMPAT_SOURCE_REVISION:/,
    /RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256:/,
    /RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT:/,
    /RELAY_COMPAT_IMAGE_DIGEST:/,
    /RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON:/,
  ]) {
    assert.match(relay, pattern);
  }
  assert.doesNotMatch(
    relay,
    /RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON|RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON|RELAY_TELEMETRY_SIGNING_SECRET/,
  );
  assert.doesNotMatch(relay, /ROUTE_ACCEPTANCE_(?:PRIVATE|SIGNING)_/);
  assert.doesNotMatch(serviceBlock(secure, "platform-api"), /^\s+RELAY_BASE_URL:/m);
  assert.match(sharedEnv, /Native Provider\/channel credentials[\s\S]+encrypted new-api channel control plane/);
  assert.match(sharedEnv, /global channel[\s\S]+control[\s\S]+RELAY_TENANT_ID/i);
  assert.match(runbook, /Platform-owned channel operations/);
  assert.match(runbook, /RELAY_PLATFORM_CONTROL_TENANT_ID[\s\S]+canonical customer/);
  assert.match(runbook, /never returns or accepts Provider keys[\s\S]+proxy settings/);
  assert.match(runbook, /network\s+timeout[\s\S]+reading that receipt/i);
  assert.match(runbook, /embedded new-api root console remains a bootstrap and break-glass surface/);
  assert.match(runbook, /must not be embedded in an iframe/);
  assert.match(runbook, /second explicit click[\s\S]+noopener noreferrer/);
  assert.match(runbook, /dedicated HTTPS origin[\s\S]+identity-aware proxy/);
  assert.match(runbook, /no Platform bearer[\s\S]+shared browser storage/);
  assert.match(runbook, /test_supported[\s\S]+staging generation canary/);
});

test("keeps a complete fail-closed example inventory for both environments", () => {
  const requiredVariables = new Set(
    [...secure.matchAll(/\$\{([A-Z0-9_]+):\?/g)].map((match) => match[1]),
  );
  const sharedKeys = envKeys(sharedEnv);
  for (const [name, overlay] of [
    ["staging", stagingEnv],
    ["production", productionEnv],
  ]) {
    const keys = new Set([...sharedKeys, ...envKeys(overlay)]);
    for (const variable of requiredVariables) {
      assert.ok(keys.has(variable), `${name} inventory missing ${variable}`);
    }
  }
  for (const source of [stagingEnv, productionEnv]) {
    assert.match(source, /^PLATFORM_PUBLIC_BASE_URL=https:\/\//m);
    assert.match(source, /^NEW_API_RELAY_PUBLIC_BASE_URL=https:\/\//m);
    assert.match(source, /^PLATFORM_RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN=https:\/\//m);
    assert.match(source, /^NEW_API_RELAY_PROVIDER_ALERT_WEBHOOK_URL=https:\/\//m);
    assert.match(source, /^NEW_API_RELAY_PLATFORM_CHANNEL_COST_URL=https:\/\//m);
    assert.match(source, /^NEW_API_RELAY_PLATFORM_TASK_STAGE_URL=https:\/\//m);
    assert.match(source, /^NEW_API_RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL=https:\/\//m);
  }
  for (const source of [stagingEnv, productionEnv]) {
    const values = new Map(
      source
        .split(/\r?\n/)
        .filter((line) => /^[A-Z0-9_]+=/.test(line))
        .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
    );
    const relayOrigin = new URL(values.get("NEW_API_RELAY_PUBLIC_BASE_URL"));
    const adminOrigin = new URL(values.get("PLATFORM_RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN"));
    assert.notEqual(adminOrigin.origin, relayOrigin.origin);
    assert.equal(adminOrigin.pathname, "/");
    assert.equal(adminOrigin.search, "");
    assert.equal(adminOrigin.hash, "");
  }
  assert.match(sharedEnv, /^NEW_API_RELAY_IMAGE_DIGEST=sha256:0{64}$/m);
  assert.match(sharedEnv, /^PLATFORM_API_GATEWAY_IMAGE_REPOSITORY=nginx$/m);
  assert.match(sharedEnv, /^PLATFORM_API_GATEWAY_IMAGE_DIGEST=sha256:0{64}$/m);
  assert.match(
    serviceBlock(secure, "api-gateway"),
    /image:\s*\$\{PLATFORM_API_GATEWAY_IMAGE_REPOSITORY:\?[^\r\n]+\}@\$\{PLATFORM_API_GATEWAY_IMAGE_DIGEST:\?[^\r\n]+\}/,
  );
  assert.doesNotMatch(serviceBlock(secure, "api-gateway"), /image:\s*nginx:[^\s]+/);
  assert.match(sharedEnv, /^NEW_API_RELAY_MODEL_ROUTES_JSON=\{\}$/m);
  assert.match(sharedEnv, /^NEW_API_RELAY_PROVIDER_CONTRACT_RATES_JSON=\[\]$/m);
  assert.match(sharedEnv, /replace EVERY[\s\S]+intentionally rejected/);
});

test("defines distinct staging/production overlays and the current Platform migration head", () => {
  assert.match(staging, /deployment-environment:\s*staging/);
  assert.match(production, /deployment-environment:\s*production/);
  assert.match(stagingEnv, /^RELAY_DEPLOYMENT_ENV=staging$/m);
  assert.match(productionEnv, /^RELAY_DEPLOYMENT_ENV=production$/m);
  assert.match(runbook, new RegExp("current Platform source head is `" + currentPlatformHead + "`"));
  assert.match(runbook, new RegExp("direct predecessor is `" + currentPlatformPrevious + "`"));
  assert.match(runbook, new RegExp(`current Platform database\\s+privilege policy is v${currentPlatformPolicyVersion}`));
  assert.match(runbook, /catalog is `UNQUALIFIED`/);
  assert.match(runbook, /v12\/0047 and\s+v13\/0048 catalogs are also unqualified/);
  assert.match(runbook, /Protected release remains `BLOCKED \/ NO-GO`/);
  assert.match(runbook, /historical qualification evidence only:[\s\S]{0,100}neither qualifies the current chain nor\s+authorizes production/);
  assert.doesNotMatch(runbook, /current Platform database privilege policy is v10/);
  assert.match(runbook, /0045_system_audit_actor/);
  assert.match(runbook, /0044_account_product_partition/);
  assert.match(runbook, /0041_model_capability_releases/);
  assert.match(runbook, /0038_download_evidence_checks/);
  assert.match(runbook, /Never translate a successful Compose render[\s\S]+real-provider\/OBS PASS/);
});

test("payment implementation does not claim a live PSP, trusted statements, or deployed billing operations", () => {
  assert.match(runbook, /Payment orders, refunds\/disputes[\s\S]{0,200}implemented software, not a live payment\s+integration/);
  assert.match(runbook, /No real PSP, trusted statement source, production-resident billing worker,\s+or customer notification channel is connected/);
  assert.match(runbook, /SOURCE_AUTHENTICITY_UNVERIFIED/);
  assert.match(runbook, /payment-finance-closure\.md/);
  assert.match(runbook, /refuses legacy settlement rows\s+without bound originals or automatic orders without a Mandate before DDL/);
});

test("pins protected Platform task affinity to new-api without ambient legacy credentials", () => {
  const relay = serviceBlock(secure, "relay-new-api");
  const platform = serviceBlock(secure, "platform-api");

  assert.match(sharedEnv, /^PLATFORM_NEW_API_RELAY_BACKEND_ID=new-api-v1$/m);
  assert.doesNotMatch(sharedEnv, /^PLATFORM_LEGACY_RELAY_BASE_URL=/m);
  assert.doesNotMatch(sharedEnv, /^PLATFORM_LEGACY_RELAY_CLIENT_ID=/m);
  assert.doesNotMatch(sharedEnv, /^PLATFORM_LEGACY_RELAY_API_KEY=/m);
  assert.doesNotMatch(sharedEnv, /^PLATFORM_LEGACY_RELAY_CALLBACK_SIGNING_SECRET=/m);
  assert.match(
    sharedEnv,
    /^PLATFORM_API_RUNTIME_SECRETS_FILE=\.\/deploy\/secrets\/platform-api-runtime-secrets\.json$/m,
  );
  assert.match(relay, /RELAY_SERVICE_PRINCIPALS_FILE:\s*\/run\/secrets\/relay-service-principals\.json/);
  assert.match(relay, /RELAY_API_RUNTIME_SECRETS_FILE:\s*\/run\/secrets\/relay-api-runtime-secrets\.json/);
  assert.doesNotMatch(relay, /RELAY_COMPAT_CLIENT_CREDENTIALS_JSON/);
  assert.match(
    runbook,
    /clients` array[\s\S]+API key[\s\S]+callback URL\/signing secret/,
  );
  assert.ok(
    platform.includes(
      "RELAY_DEFAULT_BACKEND_ID: ${PLATFORM_NEW_API_RELAY_BACKEND_ID:?set stable new-api backend id}",
    ),
  );
  assert.doesNotMatch(platform, /^\s+RELAY_BACKENDS:/m);
  assert.doesNotMatch(platform, /^\s+RELAY_BASE_URL:/m);
  assert.doesNotMatch(platform, /^\s+RELAY_CALLBACK_SIGNING_SECRETS:/m);
  assert.match(
    platform,
    /PLATFORM_PROCESS_RUNTIME_SECRETS_FILE:\s*\/run\/secrets\/platform-api-runtime-secrets\.json/,
  );
});

test("secure HTTPS sinks are present in the exact Platform ingress allowlist", () => {
  const allowed = platformIngress.match(
    /location\s+~\s+"\^\(\?:([^\r\n"]+)\)\$"\s*\{/,
  );
  assert.ok(
    allowed,
    "missing quoted exact Platform internal ingress allowlist",
  );
  const allowedPaths = new Set(allowed[1].split("|"));
  const sinkVariables = [
    "PLATFORM_RELAY_CALLBACK_PUBLIC_URL",
    "NEW_API_RELAY_PROVIDER_ALERT_WEBHOOK_URL",
    "NEW_API_RELAY_PLATFORM_CHANNEL_COST_URL",
    "NEW_API_RELAY_PLATFORM_TASK_STAGE_URL",
    "NEW_API_RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL",
    "NEW_API_RELAY_DOWNLOAD_EDGE_PLATFORM_COMPLETION_URL",
  ];
  for (const environment of [stagingEnv, productionEnv]) {
    const values = new Map(
      environment
        .split(/\r?\n/)
        .filter((line) => /^[A-Z0-9_]+=/.test(line))
        .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
    );
    for (const variable of sinkVariables) {
      const value = values.get(variable);
      assert.ok(value, `missing ${variable}`);
      assert.equal(new URL(value).protocol, "https:");
      assert.ok(allowedPaths.has(new URL(value).pathname), `${variable} is blocked by Platform ingress`);
    }
  }
  const ingressPattern = new RegExp(`^(?:${allowed[1]})$`);
  assert.ok(ingressPattern.test("/internal/relay-callbacks"));
  assert.ok(ingressPattern.test("/internal/relay-callbacks/new-api-v1"));
  assert.ok(
    ingressPattern.test(`/internal/relay-callbacks/a${"b".repeat(63)}`),
  );
  assert.equal(ingressPattern.test("/internal/relay-callbacks/New-API"), false);
  assert.equal(ingressPattern.test("/internal/relay-callbacks//new-api-v1"), false);
  assert.equal(
    ingressPattern.test("/internal/relay-callbacks/new-api-v1/extra"),
    false,
  );
  assert.equal(
    ingressPattern.test("/internal/relay-callbacks/new-api-v1%2Fextra"),
    false,
  );
  assert.equal(
    ingressPattern.test(`/internal/relay-callbacks/a${"b".repeat(64)}`),
    false,
  );
  assert.match(platformIngress, /location\s+\/internal\/\s*\{[\s\S]*?return\s+404\s*;/);
  assert.match(runbook, /exact anchored allowlist/);
});
