import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const compose = readFileSync(new URL("../docker-compose.yml", import.meta.url), "utf8");
const dockerfile = readFileSync(
  new URL("../backend/new-api-relay/Dockerfile", import.meta.url),
  "utf8",
);
const envExample = readFileSync(new URL("../.env.example", import.meta.url), "utf8");
const serviceSource = readFileSync(
  new URL("../backend/new-api-relay/service/platform_download_edge.go", import.meta.url),
  "utf8",
);
const commandSource = readFileSync(
  new URL("../backend/new-api-relay/cmd/relay-download-edge/main.go", import.meta.url),
  "utf8",
);
const modelSource = readFileSync(
  new URL("../backend/new-api-relay/model/platform_download_edge.go", import.meta.url),
  "utf8",
);
const migrationSource = readFileSync(
  new URL("../backend/new-api-relay/model/platform_channel_cost.go", import.meta.url),
  "utf8",
);
const postgresTest = readFileSync(
  new URL("../backend/new-api-relay/model/platform_download_edge_postgres_test.go", import.meta.url),
  "utf8",
);
const privilegeManifest = readFileSync(
  new URL("../backend/new-api-relay/model/download_edge_database_privilege_manifest.go", import.meta.url),
  "utf8",
);
const schemaMigration = readFileSync(
  new URL("../backend/new-api-relay/model/platform_download_edge_schema.go", import.meta.url),
  "utf8",
);
const runbook = readFileSync(
  new URL("../docs/relay-download-edge.md", import.meta.url),
  "utf8",
);
const imageSmoke = readFileSync(
  new URL("../scripts/smoke-relay-download-edge-image.mjs", import.meta.url),
  "utf8",
);

function serviceBlock(name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = compose.match(
    new RegExp(
      `^  ${escaped}:\\r?\\n([\\s\\S]*?)(?=^  [a-zA-Z0-9][a-zA-Z0-9_-]*:\\r?\\n|^volumes:)`,
      "m",
    ),
  );
  assert.ok(match, `missing Compose service ${name}`);
  return match[0];
}

function anchorBlock(name, nextName) {
  const match = compose.match(
    new RegExp(`^${name}:.*?\\r?\\n([\\s\\S]*?)(?=^${nextName}:)`, "m"),
  );
  assert.ok(match, `missing Compose anchor ${name}`);
  return match[0];
}

test("builds and deploys the controlled edge as a separate new-api Relay binary", () => {
  assert.match(
    dockerfile,
    /go build[^\r\n]+-o relay-download-edge \.\/cmd\/relay-download-edge/,
  );
  assert.match(dockerfile, /COPY --from=builder2 \/build\/relay-download-edge \//);

  const edge = serviceBlock("relay-download-edge");
  assert.doesNotMatch(edge, /^\s+profiles:/m);
  assert.match(edge, /entrypoint:\s*\["\/relay-download-edge"\]/);
  assert.match(edge, /environment:\s*\*new-api-download-edge-environment/);
  assert.match(edge, /relay-new-api-postgres:\s*\r?\n\s+condition: service_healthy/);
  assert.match(edge, /relay-new-api:\s*\r?\n\s+condition: service_healthy/);
  assert.match(edge, /relay-new-api-db-role-post:\s*\r?\n\s+condition: service_completed_successfully/);
  assert.match(edge, /platform-api:\s*\r?\n\s+condition: service_healthy/);
  assert.match(edge, /- relay-new-api-edge/);
  assert.match(edge, /- relay-new-api-data/);
  assert.doesNotMatch(edge, /- default/);
  assert.match(edge, /\/health\/ready/);
  assert.match(edge, /user:\s*"10001:10001"/);
  assert.match(edge, /read_only:\s*true/);
  assert.match(edge, /cap_drop:\s*\["ALL"\]/);
  assert.match(edge, /no-new-privileges:true/);
  assert.match(edge, /stop_grace_period:\s*40s/);
  assert.match(imageSmoke, /test -x \/new-api/);
  assert.match(imageSmoke, /test -x \/relay-download-edge/);
  assert.match(imageSmoke, /missing_configuration_failed_closed/);
});

test("gives the public edge a dedicated least-privilege environment and database role", () => {
  const environment = anchorBlock(
    "x-new-api-download-edge-environment",
    "services",
  );
  assert.doesNotMatch(environment, /<<:\s*\*new-api-relay-environment/);
  assert.match(
    environment,
    /^  RELAY_DOWNLOAD_EDGE_SQL_DSN_FILE:\s*\/run\/relay-local-db-secrets\/relay-local-download-edge-sql-dsn/m,
  );
  for (const forbidden of [
    "SQL_DSN",
    "REDIS_CONN_STRING",
    "SESSION_SECRET",
    "CRYPTO_SECRET",
    "RELAY_COMPAT_CLIENT_CREDENTIALS_JSON",
    "RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
    "RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN",
    "RELAY_ARTIFACT_SIGNING_SECRET",
    "HUAWEI_OBS_ACCESS_KEY_ID",
    "HUAWEI_OBS_SECRET_ACCESS_KEY",
    "HUAWEI_OBS_SECURITY_TOKEN",
    "RELAY_PROVIDER_ALERT_SIGNING_SECRET",
    "RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET",
  ]) {
    assert.doesNotMatch(
      environment,
      new RegExp(`^  ${forbidden}:`, "m"),
      `${forbidden} must not be injected into relay-download-edge`,
    );
  }
  assert.match(
    commandSource,
    /resolveDownloadEdgeDatabaseDSN\("RELAY_DOWNLOAD_EDGE_SQL_DSN"\)/,
  );
  assert.match(commandSource, /ValidateRelayPostgresTransportDSN/);
  assert.match(commandSource, /ValidateRelayPostgresSearchPathDSN/);
  assert.match(commandSource, /common\.IsMasterNode\s*=\s*false/);
  assert.doesNotMatch(commandSource, /common\.IsMasterNode\s*=\s*true/);
  assert.match(runbook, /exact `relay_download_edge`\s*PostgreSQL role/);
  assert.match(runbook, /never runs AutoMigrate/);
  assert.match(commandSource, /VerifyRelayDownloadEdgeDatabaseRole/);
  assert.match(privilegeManifest, /relayDownloadEdgeDatabasePrivilegeManifestForVersion/);
  assert.match(privilegeManifest, /relay_schema_state[\s\S]+relay_schema_migrations/);
  assert.match(privilegeManifest, /platform_download_edge_tickets[\s\S]+claim_token[\s\S]+updated_at/);
  assert.match(privilegeManifest, /"GRANT UPDATE \(" \+ strings\.Join\(names, ", "\) \+ "\) ON TABLE public\."/);
  assert.match(privilegeManifest, /has_column_privilege/);
  assert.match(privilegeManifest, /VerifyRelayDownloadEdgeDatabaseRole/);
  assert.match(schemaMigration, /CREATE POLICY relay_download_edge_outbox_policy/);
  assert.match(schemaMigration, /event_kind = 'download_completion'/);
});

test("makes the controlled edge and registration worker part of the default data plane", () => {
  const platformEnvironment = anchorBlock("x-platform-environment", "x-new-api-relay-environment");
  assert.match(platformEnvironment, /DOWNLOAD_GATEWAY_REGISTRATION_URL:[^\r\n]+relay-download-edge:8080/);
  assert.match(platformEnvironment, /DOWNLOAD_GATEWAY_PUBLIC_BASE_URL:[^\r\n]+127\.0\.0\.1:8400/);
  assert.match(platformEnvironment, /DOWNLOAD_GATEWAY_REGISTRATION_WORKER_ENABLED:[^\r\n]+:-true\}/);
  assert.match(platformEnvironment, /^  RELAY_ALLOW_LEGACY_ARTIFACT_DOWNLOAD_RESPONSE:\s*"false"/m);

  const worker = serviceBlock("platform-download-gateway-registration-worker");
  assert.doesNotMatch(worker, /^\s+profiles:/m);
  assert.match(worker, /<<:\s*\*platform-environment/);
  assert.match(worker, /DOWNLOAD_GATEWAY_REGISTRATION_URL:[^\r\n]+relay-download-edge:8080/);
  assert.match(worker, /DOWNLOAD_GATEWAY_REGISTRATION_WORKER_ENABLED:\s*"true"/);
  assert.match(worker, /relay-download-edge:\s*\r?\n\s+condition:\s*service_healthy/);
});

test("wires independent registration, completion, encryption, ticket, and proof keys", () => {
  assert.match(compose, /RELAY_ARTIFACT_SIGNED_URL_TTL_SECONDS:\s*\$\{NEW_API_RELAY_ARTIFACT_SIGNED_URL_TTL_SECONDS:-600\}/);
  assert.match(compose, /RELAY_DOWNLOAD_EDGE_TICKET_TTL_SECONDS:\s*\$\{NEW_API_RELAY_DOWNLOAD_EDGE_TICKET_TTL_SECONDS:-300\}/);
  assert.match(compose, /RELAY_DOWNLOAD_EDGE_SOURCE_EXPIRY_MARGIN_SECONDS:\s*\$\{NEW_API_RELAY_DOWNLOAD_EDGE_SOURCE_EXPIRY_MARGIN_SECONDS:-60\}/);
  assert.match(compose, /RELAY_DOWNLOAD_EDGE_TRANSFER_COMPLETION_MARGIN_SECONDS:[^\r\n]+:-10\}/);
  assert.match(compose, /RELAY_DOWNLOAD_EDGE_DELIVERY_COMMIT_MARGIN_SECONDS:[^\r\n]+:-10\}/);
  assert.match(
    compose,
    /RELAY_DOWNLOAD_EDGE_REGISTRATION_TOKEN:\s*\$\{DOWNLOAD_GATEWAY_SERVICE_TOKEN:/,
  );
  assert.match(
    compose,
    /RELAY_DOWNLOAD_EDGE_REGISTRATION_SIGNING_SECRET:\s*\$\{DOWNLOAD_GATEWAY_REGISTRATION_SIGNING_SECRET:/,
  );
  assert.match(
    compose,
    /RELAY_DOWNLOAD_EDGE_COMPLETION_SIGNING_SECRET:\s*\$\{DOWNLOAD_COMPLETION_EDGE_GATEWAY_SIGNING_SECRET:/,
  );
  for (const name of [
    "NEW_API_RELAY_DOWNLOAD_EDGE_TICKET_TOKEN_KEY_BASE64",
    "NEW_API_RELAY_DOWNLOAD_EDGE_SOURCE_ENCRYPTION_KEY_BASE64",
    "NEW_API_RELAY_DOWNLOAD_EDGE_PROOF_PRIVATE_KEY_BASE64",
    "NEW_API_RELAY_DOWNLOAD_EDGE_PROOF_READ_TOKEN",
  ]) {
    assert.match(envExample, new RegExp(`^${name}=$`, "m"));
  }
  assert.match(envExample, /^RELAY_ALLOW_LEGACY_ARTIFACT_DOWNLOAD_RESPONSE=false$/m);
  assert.match(envExample, /^RELAY_DOWNLOAD_EDGE_SQL_DSN=$/m);
  assert.match(serviceSource, /ProofSigningPrivateKey\.Seed\(\)/);
  assert.match(serviceSource, /platformDownloadEdgeDevelopmentCredential/);
  assert.match(serviceSource, /platformDownloadEdgeDevelopmentProofSeed/);
});

test("keeps signed OBS credentials out of ticket, completion, and proof evidence", () => {
  assert.match(modelSource, /SourceURLCiphertext\s+\[\]byte\s+`json:"-"/);
  assert.match(modelSource, /TokenSHA256\s+string\s+`json:"-"/);
  const eventStruct = modelSource.match(
    /type PlatformDownloadCompletionEvent struct \{[\s\S]*?\n\}/,
  );
  assert.ok(eventStruct, "missing immutable completion event model");
  assert.doesNotMatch(eventStruct[0], /SourceURL/);
  assert.match(serviceSource, /aes\.NewCipher/);
  assert.match(serviceSource, /cipher\.NewGCM/);
  assert.match(serviceSource, /download-edge-source\.v1/);
  assert.match(serviceSource, /request\.Header\.Get\("Range"\)/);
  assert.match(serviceSource, /http\.ErrUseLastResponse/);
  assert.match(serviceSource, /io\.LimitReader\(response\.Body, claim\.Ticket\.ExpectedSizeBytes\)/);
  assert.match(serviceSource, /errors\.Is\(tailErr, io\.EOF\)/);
  assert.match(serviceSource, /PayloadSHA256:\s*"sha256:" \+ proof\.PayloadSHA256/);
});

test("enforces append-only evidence in PostgreSQL and documents the external proof", () => {
  assert.match(migrationSource, /platform_download_completion_events/);
  assert.match(migrationSource, /platform_download_completion_proofs/);
  assert.match(migrationSource, /BEFORE UPDATE OR DELETE/);
  assert.match(migrationSource, /BEFORE TRUNCATE/);
  assert.match(postgresTest, /const workers = 16/);
  assert.match(postgresTest, /TRUNCATE TABLE platform_download_completion_events/);
  assert.match(postgresTest, /TRUNCATE TABLE platform_download_completion_proofs/);
  assert.match(runbook, /relay-download-completion-proof\.v1/);
  assert.match(runbook, /\/proof\/signature/);
  assert.match(runbook, /private key or completion HMAC secret/);
});
