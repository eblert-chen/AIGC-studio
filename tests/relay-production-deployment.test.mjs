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
const deploymentRunbook = read("docs/deployment-runbook.md");
const releaseReadiness = read("docs/release-readiness.md");
const projectReadme = read("README.md");
const relayMigrationGuide = read("docs/relay-new-api-migration.md");
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
const relaySchemaArtifactGate = read(
  "backend/new-api-relay/model/schema_artifact_test.go",
);
const relaySchemaMigrationGate = read(
  "backend/new-api-relay/model/schema_migration_test.go",
);
const relaySchemaV1FixturePatch = read(
  "backend/new-api-relay/scripts/fixtures/relay-schema-v1-pg16-tls-test-fixture.patch",
);
const relayDatabaseRoleAttestation = read(
  "backend/new-api-relay/model/database_role_attestation.go",
);
const relayMain = read("backend/new-api-relay/main.go");
const relayEdgeMain = read("backend/new-api-relay/cmd/relay-download-edge/main.go");
const relayEdgeService = read("backend/new-api-relay/service/platform_download_edge.go");
const relayDatabaseReleaseProof = read(
  "backend/new-api-relay/service/platform_database_release_proof.go",
);

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
    "fresh-v3-row3-only-gate=PASS",
    "legacy-to-v1-gate=PASS",
    "v1-compatible-no-runtime-side-effects=PASS",
    "historical-frozen-v1-to-v2-no-catalog-delta-gate=PASS",
    "v1-to-v2-no-catalog-delta-gate=PASS",
    "v2-to-v3-one-shot-gate=PASS",
    "exact-v1-to-v3-ledger-gate=PASS",
    "post-v3-proof-root-principal-api-current-gate=PASS",
    "max-v2-ahead-no-direct-rollback-gate=PASS",
    "legacy-schema-upgrade-gate=PASS",
  ]) {
    assert.ok(relayLegacySchemaGate.includes(evidence), `missing legacy gate evidence ${evidence}`);
  }
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
  assert.match(relayLegacySchemaGate, /relay-provision-database-roles/);
  assert.match(relayLegacySchemaGate, /\/release\/new-api relay-migrate/);
  assert.match(relayLegacySchemaGate, /3\|3\|3\|clean\|3/);
  assert.match(relayLegacySchemaGate, /1\|3\|3\|clean\|1,2,3/);
  assert.match(relayLegacySchemaGate, /status\.classification -ne "ahead"/);
  assert.match(
    relayLegacySchemaGate,
    /TestRelaySchemaPostgresProtectedLifecycleProcess/,
  );
  assert.match(
    relayLegacySchemaGate,
    /1\|1\|1\|clean\|1\|1\|1\|0\|0/,
  );
  assert.match(
    relayLegacySchemaGate,
    /pinnedV1FixturePatchSHA256 = "dd3bbe7dea195bf83222f2acb32ea0ab96208ac64d7f66dcbc2ddb5f5e3a3449"/,
  );
  assert.match(relayLegacySchemaGate, /'lifecycle_root'/);
  assert.match(relayLegacySchemaGate, /'v0\.0\.0'/);
  assert.match(relayLegacySchemaGate, /'SUCCESS','100%'/);
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
  assert.doesNotMatch(relayLegacySchemaGate, /postgres:16-alpine|sslmode=disable/);
  assert.match(runbook, /make test-relay-schema-legacy-pg16/);
  assert.match(runbook, /not an optional developer\s+smoke test/);
  assert.match(runbook, /missing image[\s\S]+release failure/);
  assert.match(runbook, /raw\/unversioned[\s\S]+immutable schema-v1[\s\S]+v1-to-v2[\s\S]+v2-to-v3/i);
  assert.match(runbook, /absent test event or `skip` as failure/);
});

test("preserves frozen Relay schema v2 and versions the credential-order correction as v3", () => {
  assert.match(relaySchemaContract, /RelaySchemaTargetVersion\s+int64\s*=\s*3/);
  assert.match(relaySchemaContract, /RelaySchemaMinVersion\s+int64\s*=\s*1/);
  assert.match(relaySchemaContract, /RelaySchemaMaxVersion\s+int64\s*=\s*3/);
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

  for (const evidence of [
    /Equal\(t, int64\(0\), result\.FromVersion\)/,
    /Equal\(t, RelaySchemaTargetVersion, result\.Status\.BaselineVersion\)/,
    /Len\(t, freshLedger, 1, "fresh v3 must not fabricate unexecuted historical ledger events"\)/,
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
    /TestRelaySchemaV3TopLevelMigrationNeverExecutesLiveV1[\s\S]+liveV1Sentinel[\s\S]+fresh v3 bootstrap[\s\S]+exact v1 through v3 bridge/,
  );

  assert.match(
    relayDatabaseRoleAttestation,
    /GetRelayRuntimeDatabaseRoleStatus[\s\S]+RequireRelaySchemaCurrent\(db\)/,
  );
  assert.match(
    relayMain,
    /RelayDatabaseRoleAttestationRequired\(\)[\s\S]+RequireRelaySchemaCurrent\(model\.DB\)[\s\S]+else if[\s\S]+RequireRelaySchemaCompatible\(model\.DB\)/,
  );
  for (const source of [relayEdgeMain, relayEdgeService]) {
    assert.match(
      source,
      /protected[\s\S]+RequireRelaySchemaCurrent\(model\.DB\)[\s\S]+RequireRelaySchemaCompatible\(model\.DB\)/i,
    );
  }
  const verifyProofFunction = relayDatabaseReleaseProof.match(
    /func VerifyPlatformRelayDatabaseReleaseProof\([\s\S]+?\n\}/,
  )?.[0];
  assert.ok(verifyProofFunction, "missing database-proof verifier");
  const proofCurrentConsumers = verifyProofFunction.match(
    /switch consumer \{[\s\S]+?Relay database release proof requires the current schema/,
  )?.[0];
  assert.ok(proofCurrentConsumers, "missing database-proof Current-v3 consumer gate");
  for (const consumer of ["Post", "Principal", "API", "RootBootstrap", "Edge"]) {
    assert.match(proofCurrentConsumers, new RegExp(`Consumer${consumer}`));
  }
  assert.doesNotMatch(proofCurrentConsumers, /ConsumerPre|ConsumerMigrate/);
  assert.match(
    relayDatabaseReleaseProof,
    /root database release proof requires the current schema[\s\S]+principal rotation database release proof requires the current schema/,
  );

  for (const document of [runbook, deploymentRunbook, releaseReadiness]) {
    assert.match(document, /no-catalog-delta/i);
    assert.match(document, /root[\s\S]+principal[\s\S]+API/i);
  }
  for (const document of [
    projectReadme,
    runbook,
    deploymentRunbook,
    releaseReadiness,
    relayMigrationGuide,
  ]) {
    assert.match(document, /target=3,min=1,max=3/);
    assert.match(document, /fresh v3[\s\S]+\[3\]/i);
    assert.match(document, /\[1,2,3\]/);
    assert.match(document, /max(?:[=-]|-v)2[\s\S]+ahead/i);
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
  assert.match(relayDockerfile, /ARG RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256=unknown/);
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
  assert.match(runbook, /kind=relay_secret_isolation[\s\S]+state=validated[\s\S]+consumers=14/);
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
  assert.match(sharedEnv, /^NEW_API_RELAY_MODEL_ROUTES_JSON=\{\}$/m);
  assert.match(sharedEnv, /^NEW_API_RELAY_PROVIDER_CONTRACT_RATES_JSON=\[\]$/m);
  assert.match(sharedEnv, /replace EVERY[\s\S]+intentionally rejected/);
});

test("defines distinct staging/production overlays and the current Platform migration head", () => {
  assert.match(staging, /deployment-environment:\s*staging/);
  assert.match(production, /deployment-environment:\s*production/);
  assert.match(stagingEnv, /^RELAY_DEPLOYMENT_ENV=staging$/m);
  assert.match(productionEnv, /^RELAY_DEPLOYMENT_ENV=production$/m);
  assert.match(runbook, /0040_showcase_management/);
  assert.match(runbook, /0039_new_api_relay_defaults/);
  assert.match(runbook, /0038_download_evidence_checks/);
  assert.match(runbook, /Never translate a successful Compose render[\s\S]+real-provider\/OBS PASS/);
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
