import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const secure = read("deploy/compose.relay.secure.yml");
const sharedEnv = read("deploy/relay-secure.env.example");
const schema = JSON.parse(read("docs/schemas/platform-process-runtime-secrets.schema.json"));
const loader = read("backend/platform/platform_api/process_secrets.py");
const receipt = read("backend/platform/platform_api/platform_secret_receipt.py");
const platformDockerfile = read("backend/platform/Dockerfile");
const config = read("backend/platform/platform_api/config.py");
const adapters = read("backend/platform/platform_api/publishing_adapters.py");
const publishingWorker = read("backend/platform/platform_api/publishing_worker.py");
const migration = read("backend/platform/migrations/env.py");
const rolePre = read("backend/platform/platform_api/database_role_pre.py");
const rawEnvironmentManifest = read(
  "backend/new-api-relay/common/protected_raw_secret_environment.go",
);
const entrypoints = new Map([
  ["platform-api", read("backend/platform/platform_api/main.py")],
  ["dispatcher", read("backend/platform/platform_api/dispatcher.py")],
  ["relay-sync", read("backend/platform/platform_api/relay_sync_worker.py")],
  ["timeout-worker", read("backend/platform/platform_api/timeout_worker.py")],
  ["publishing-worker", read("backend/platform/platform_api/publishing_worker.py")],
  [
    "download-gateway-registration-worker",
    read("backend/platform/platform_api/download_gateway_registration_worker.py"),
  ],
]);

function serviceBlock(name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = secure.match(
    new RegExp(
      `^  ${escaped}:\\r?\\n([\\s\\S]*?)(?=^  [a-zA-Z0-9][a-zA-Z0-9_-]*:\\r?\\n|^networks:)`,
      "m",
    ),
  );
  assert.ok(match, `missing secure Compose service ${name}`);
  return match[0];
}

const roles = [
  ["platform-migrate", "migration", "PLATFORM_MIGRATION_RUNTIME_SECRETS_FILE"],
  ["platform-api", "platform-api", "PLATFORM_API_RUNTIME_SECRETS_FILE"],
  ["platform-dispatcher", "dispatcher", "PLATFORM_DISPATCHER_RUNTIME_SECRETS_FILE"],
  ["platform-relay-sync", "relay-sync", "PLATFORM_RELAY_SYNC_RUNTIME_SECRETS_FILE"],
  ["platform-timeout-worker", "timeout-worker", "PLATFORM_TIMEOUT_WORKER_RUNTIME_SECRETS_FILE"],
  ["platform-publishing-worker", "publishing-worker", "PLATFORM_PUBLISHING_WORKER_RUNTIME_SECRETS_FILE"],
  [
    "platform-download-gateway-registration-worker",
    "download-gateway-registration-worker",
    "PLATFORM_DOWNLOAD_GATEWAY_WORKER_RUNTIME_SECRETS_FILE",
  ],
];

const rawSecretNames = [
  "DATABASE_URL",
  "RELAY_BACKENDS",
  "RELAY_CLIENT_ID",
  "RELAY_API_KEY",
  "RELAY_OPERATIONS_TOKEN",
  "RELAY_RECONCILIATION_APPROVAL_SECRET",
  "RELAY_CALLBACK_SIGNING_SECRET",
  "RELAY_CALLBACK_SIGNING_SECRETS",
  "INTERNAL_SERVICE_TOKEN",
  "DOWNLOAD_EDGE_COMPLETION_SERVICE_TOKEN",
  "CHANNEL_COST_SIGNING_SECRET",
  "RELAY_TELEMETRY_SIGNING_SECRET",
  "PROVIDER_ALERT_SIGNING_SECRET",
  "PROVIDER_ALERT_FORWARD_SIGNING_SECRET",
  "DOWNLOAD_COMPLETION_EDGE_GATEWAY_SIGNING_SECRET",
  "DOWNLOAD_COMPLETION_OBS_ACCESS_LOG_SIGNING_SECRET",
  "DOWNLOAD_GATEWAY_SERVICE_TOKEN",
  "DOWNLOAD_GATEWAY_REGISTRATION_SIGNING_SECRET",
  "DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64",
  "JWT_SIGNING_SECRET",
  "INPUT_ASSET_SIGNING_SECRET",
  "HUAWEI_OBS_ACCESS_KEY_ID",
  "HUAWEI_OBS_SECRET_ACCESS_KEY",
  "HUAWEI_OBS_SECURITY_TOKEN",
];

test("publishes one closed role-discriminated Platform secret schema", () => {
  assert.equal(schema.$schema, "https://json-schema.org/draft/2020-12/schema");
  assert.equal(schema.oneOf.length, 7);
  assert.equal(schema.$defs.apiDocument.properties.process_role.const, "platform-api");
  assert.equal(
    schema.$defs.downloadGatewayDocument.properties.process_role.const,
    "download-gateway-registration-worker",
  );
  assert.equal(schema.$defs.apiSecrets.unevaluatedProperties, false);
  assert.equal(schema.$defs.dispatcherSecrets.unevaluatedProperties, false);
  assert.equal(schema.$defs.publishingSecrets.additionalProperties, false);
  assert.equal(schema.$defs.downloadGatewaySecrets.additionalProperties, false);
  assert.equal(schema.$defs.migrationSecrets.additionalProperties, false);
  const apiRole = schema.$defs.apiSecrets.allOf[1];
  const dispatcherRole = schema.$defs.dispatcherSecrets.allOf[1];
  for (const legacyField of [
    "relay_client_id",
    "relay_api_key",
    "relay_callback_signing_secret",
  ]) {
    assert.ok(!schema.$defs.relaySecrets.required.includes(legacyField));
    assert.ok(!Object.hasOwn(schema.$defs.relaySecrets.properties, legacyField));
    assert.ok(!apiRole.required.includes(legacyField));
    assert.ok(!Object.hasOwn(apiRole.properties, legacyField));
  }
  for (const roleSchema of [apiRole, dispatcherRole]) {
    assert.ok(!roleSchema.required.includes("input_asset_signing_secret"));
    assert.ok(!Object.hasOwn(roleSchema.properties, "input_asset_signing_secret"));
  }
  assert.match(
    entrypoints.get("platform-api"),
    /if app\.state\.input_asset_store\.kind == "filesystem"/,
  );
  assert.match(
    entrypoints.get("dispatcher"),
    /if input_asset_store\.kind == "filesystem"/,
  );
  assert.match(loader, /"INPUT_ASSET_SIGNING_SECRET"/);
});

test("secure Compose mounts one minimum typed bundle per Platform process", () => {
  for (const [service, role, sourceVariable] of roles) {
    const block = serviceBlock(service);
    assert.match(block, /build:\s*!reset null/);
    assert.match(
      block,
      /image:\s*\$\{PLATFORM_IMAGE:\?set immutable Platform image reference with sha256 digest\}/,
    );
    assert.doesNotMatch(block, /context:\s*\.\/backend\/platform/);
    assert.match(block, /user:\s*"10001:10001"/);
    assert.match(block, /PLATFORM_PROTECTED_RUNTIME:\s*"true"/);
    assert.match(
      block,
      /ENVIRONMENT:\s*\$\{RELAY_DEPLOYMENT_ENV:\?set staging or production\}/,
    );
    assert.match(block, new RegExp(`PLATFORM_PROCESS_ROLE:\\s*${role.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&")}`));
    assert.match(block, /PLATFORM_PROCESS_RUNTIME_SECRETS_FILE:\s*\/run\/secrets\/platform-[^\s]+\.json/);
    assert.match(block, new RegExp(`source:\\s*\\$\\{${sourceVariable}:`));
    assert.match(block, /read_only:\s*true/);
    assert.match(block, /create_host_path:\s*false/);
    assert.equal((block.match(/type:\s*bind/g) || []).length, 2);
    assert.match(block, /source:\s*\$\{PLATFORM_DATABASE_CA_FILE:/);
    assert.match(block, /target:\s*\/run\/secrets\/platform-database-ca\.pem/);
    assert.match(
      block,
      /PLATFORM_DATABASE_RELEASE_PROOF_FILE:\s*\/run\/platform-database-release-proof\/attestation\.json/,
    );
    assert.match(
      block,
      /platform-database-release-proof:\/run\/platform-database-release-proof:ro/,
    );
    assert.match(block, /^    read_only:\s*true$/m);
    assert.match(block, /^    cap_drop:\s*\["ALL"\]$/m);
    assert.match(block, /^    security_opt:\s*\["no-new-privileges:true"\]$/m);
    assert.match(
      block,
      /^      - \/run\/platform-database-ca-snapshot:rw,noexec,nosuid,nodev,mode=0700,uid=10001,gid=10001$/m,
    );
    assert.match(
      block,
      /RELAY_SECRET_ISOLATION_RECEIPT_FILE:\s*\/run\/relay-secret-isolation\/receipt\.json/,
    );
    assert.match(block, /relay-new-api-secret-isolation-platform-[^\s]+:\/run\/relay-secret-isolation:ro/);
    assert.match(
      block,
      /relay-new-api-secret-isolation:[\s\S]*?condition:\s*service_completed_successfully/,
    );
    for (const environment of [
      "RELAY_COMPAT_IMAGE_DIGEST",
      "RELAY_COMPAT_SOURCE_REVISION",
      "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256",
      "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT",
      "RELAY_COMPAT_UPSTREAM_REVISION",
      "RELAY_COMPAT_ROUTE_ACCEPTANCE_TRUST_KEYS_SHA256",
      "PLATFORM_IMAGE",
      "PLATFORM_SOURCE_REVISION",
      "PLATFORM_SOURCE_SNAPSHOT_SHA256",
    ]) {
      assert.match(block, new RegExp(`^\\s+${environment}:`, "m"));
    }
    for (const name of rawSecretNames) {
      assert.doesNotMatch(block, new RegExp(`^\\s+${name}:`, "m"), `${service} exposes ${name}`);
    }
    assert.doesNotMatch(block, /^\s+RELAY_BASE_URL:/m);
  }
  const api = serviceBlock("platform-api");
  assert.doesNotMatch(api, /alembic upgrade head/);
  assert.match(api, /platform-migrate:[\s\S]*condition:\s*service_completed_successfully/);
  const migrationBlock = serviceBlock("platform-migrate");
  assert.match(migrationBlock, /command:\s*\["alembic", "upgrade", "head"\]/);
  assert.match(
    migrationBlock,
    /platform-db-role-pre:[\s\S]*?condition:\s*service_completed_successfully/,
  );
  for (const [service] of roles.slice(1)) {
    assert.match(
      serviceBlock(service),
      /platform-migrate:[\s\S]*?condition:\s*service_completed_successfully/,
    );
  }
});

test("role-pre receives only its nine committed database sources", () => {
  const block = serviceBlock("platform-db-role-pre");
  assert.match(block, /build:\s*!reset null/);
  assert.match(
    block,
    /image:\s*\$\{PLATFORM_IMAGE:\?set immutable Platform image reference with sha256 digest\}/,
  );
  assert.match(block, /user:\s*"10001:10001"/);
  assert.match(block, /command:\s*\["python", "-m", "platform_api\.database_role_pre"\]/);
  assert.match(block, /PLATFORM_PROCESS_ROLE:\s*database-role-pre/);
  assert.match(block, /PLATFORM_DATABASE_ROLE_ADMIN_DSN_FILE:\s*\/run\/secrets\/platform-role-admin-sql-dsn/);
  assert.match(block, /PLATFORM_DATABASE_CA_FILE:\s*\/run\/secrets\/platform-database-ca\.pem/);
  assert.match(
    block,
    /PLATFORM_DATABASE_RELEASE_PROOF_FILE:\s*\/run\/platform-database-release-proof\/attestation\.json/,
  );
  for (const source of [
    "MIGRATION",
    "API",
    "DISPATCHER",
    "RELAY_SYNC",
    "TIMEOUT_WORKER",
    "PUBLISHING_WORKER",
    "DOWNLOAD_GATEWAY_WORKER",
  ]) {
    assert.match(block, new RegExp(`PLATFORM_${source}_DATABASE_PASSWORD_FILE:`));
  }
  assert.equal((block.match(/type:\s*bind/g) || []).length, 9);
  assert.doesNotMatch(block, /PLATFORM_PROCESS_RUNTIME_SECRETS_FILE/);
  assert.doesNotMatch(block, /RUNTIME_SECRETS_FILE:/);
  assert.match(block, /relay-new-api-secret-isolation-platform-db-role-pre:\/run\/relay-secret-isolation:ro/);
  assert.match(block, /relay-new-api-secret-isolation-commit:\/run\/relay-secret-isolation-commit:ro/);
  assert.match(
    block,
    /platform-database-release-proof:\/run\/platform-database-release-proof(?:\r?\n|$)/,
  );
  assert.doesNotMatch(
    block,
    /platform-database-release-proof:\/run\/platform-database-release-proof:ro/,
  );
  assert.match(block, /^    read_only:\s*true$/m);
  assert.match(block, /^    cap_drop:\s*\["ALL"\]$/m);
  assert.match(block, /^    security_opt:\s*\["no-new-privileges:true"\]$/m);
  assert.match(
    block,
    /^      - \/run\/platform-database-ca-snapshot:rw,noexec,nosuid,nodev,mode=0700,uid=10001,gid=10001$/m,
  );
  assert.match(
    block,
    /relay-new-api-secret-isolation:[\s\S]*?condition:\s*service_completed_successfully/,
  );
  assert.match(rolePre, /verify_platform_secret_isolation_receipt_sources/);
  assert.match(rolePre, /materialize_verified_platform_database_ca/);
  assert.match(
    rolePre,
    /with _held_provision_lock\(sources\.role_admin_database_url\) as maintenance:[\s\S]+state = _preflight\(sources, maintenance\)[\s\S]+_provision_principals\(sources, state, maintenance\)/,
  );
  assert.ok(
    (rolePre.match(/_require_unchanged_maintenance_state\(/g) || []).length >= 4,
    "every role/database mutation must repeat the adjacent state gate",
  );
  assert.match(rolePre, /"-c statement_timeout=30000 "/);
  assert.match(rolePre, /"-c lock_timeout=5000"/);
  assert.match(secure, /^  platform-database-release-proof:\s*$/m);
  assert.match(
    serviceBlock("relay-new-api-volume-init"),
    /platform-database-release-proof:\/run\/platform-database-release-proof/,
  );
});

test("one immutable Platform image carries a root-owned embedded source identity", () => {
  assert.match(platformDockerfile, /ARG PLATFORM_SOURCE_REVISION=/);
  assert.match(platformDockerfile, /ARG PLATFORM_SOURCE_SNAPSHOT_SHA256=/);
  assert.match(platformDockerfile, /platform_source_snapshot\.py/);
  assert.match(platformDockerfile, /--expected "\$PLATFORM_SOURCE_SNAPSHOT_SHA256"/);
  assert.doesNotMatch(platformDockerfile, /COPY --chown=app:app platform_api/);
  assert.match(platformDockerfile, /> \/app\/platform-release-identity\.json/);
  assert.match(platformDockerfile, /chmod 0444 \/app\/platform-release-identity\.json/);
  assert.match(platformDockerfile, /addgroup -S -g 10001 app/);
  assert.match(platformDockerfile, /adduser -S -D -H -u 10001 -G app app/);
  assert.match(receipt, /PLATFORM_RELEASE_IDENTITY_FILE = "\/app\/platform-release-identity\.json"/);
  assert.match(receipt, /before\.st_uid != 0/);
  assert.match(receipt, /stat\.S_IMODE\(before\.st_mode\) != 0o444/);
  assert.match(receipt, /platform_source_revision/);
  assert.match(receipt, /platform_source_snapshot_sha256/);
});

test("the secure inventory contains only Platform bundle paths, not raw Platform secrets", () => {
  for (const [, , sourceVariable] of roles) {
    assert.match(sharedEnv, new RegExp(`^${sourceVariable}=\\.\\/deploy\\/secrets\\/`, "m"));
  }
  for (const name of rawSecretNames) {
    assert.doesNotMatch(sharedEnv, new RegExp(`^${name}=`, "m"));
  }
  assert.doesNotMatch(sharedEnv, /^PLATFORM_DATABASE_URL=/m);
});

test("Platform bootstrap rejects ambiguous files and present-even-empty raw secret env", () => {
  for (const contract of [
    "object_pairs_hook=_reject_duplicate_object",
    "if end != len(text)",
    "set(secrets) != _PROCESS_SECRET_FIELDS[expected_role]",
    "query_items\n        != [",
    "mode not in {0o400, 0o600}",
    "before.st_uid != effective_uid",
    "not _protected_file_read_only(descriptor)",
    'getattr(os, "O_NOFOLLOW", 0)',
    "if name.upper() in RAW_PLATFORM_SECRET_ENVIRONMENTS",
    "outer_environment_is_protected",
    'explicit is not None and explicit != "true"',
    'raise PlatformProcessSecretError("PLATFORM_PROTECTED_RUNTIME is invalid")',
  ]) {
    assert.ok(loader.includes(contract), `missing strict reader contract: ${contract}`);
  }
  assert.match(loader, /platform_process_secret_semantic_commitments\(normalized, role\)/);
  assert.match(loader, /verify_platform_secret_isolation_receipt_sources\(/);
  assert.match(receipt, /hashlib\.sha256\(files\[identifier\]\)\.hexdigest\(\)/);
  assert.match(receipt, /hmac\.compare_digest/);
  assert.match(receipt, /stat\.S_IMODE\(before\.st_mode\) != 0o400/);
  assert.match(receipt, /not _receipt_mount_is_read_only\(descriptor\)/);
  assert.match(config, /load_platform_process_secret_settings\(\)/);
  assert.match(config, /actual_role != expected_process_role/);
  assert.match(migration, /protected_platform_runtime_requested\(\)/);
  assert.match(migration, /settings = get_settings\("migration"\)/);
  for (const [role, source] of entrypoints) {
    assert.match(source, new RegExp(`get_settings\\("${role}"\\)`));
  }
});

test("Python and Go share the exact Platform raw-secret environment manifest", () => {
  const pythonBlock = loader.match(
    /RAW_PLATFORM_SECRET_ENVIRONMENTS\s*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/,
  );
  assert.ok(pythonBlock);
  const goBlock = rawEnvironmentManifest.match(
    /func ProtectedPlatformRawSecretEnvironmentNamesV1\(\) \[\]string \{[\s\S]*?return \[\]string\{([\s\S]*?)\n\s*\}/,
  );
  assert.ok(goBlock);
  const names = (source) =>
    [...source.matchAll(/"([A-Z][A-Z0-9_]*)"/g)].map((match) => match[1]).sort();
  assert.deepEqual(names(pythonBlock[1]), names(goBlock[1]));
  assert.equal(names(pythonBlock[1]).length, 48);
  assert.match(loader, /name\.casefold\(\)\.startswith\("pg"\)/);
  assert.match(rawEnvironmentManifest, /strings\.HasPrefix\(upperName, "PG"\)/);
});

test("production publishing plug-ins receive only their explicit manifest", () => {
  assert.match(adapters, /require_credential_manifest/);
  assert.match(adapters, /factory\(credential_manifest=credential_manifest\)/);
  assert.match(publishingWorker, /media resolver factory must require keyword-only/);
  assert.match(publishingWorker, /settings\.publishing_plugin_secret_manifest\(\s*"media_resolvers"/);
  assert.match(config, /plug-in specs must exactly match the credential manifest/);
});
