import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

const compose = readFileSync(
  new URL("../docker-compose.yml", import.meta.url),
  "utf8",
);
const envExample = readFileSync(
  new URL("../.env.example", import.meta.url),
  "utf8",
);
const architecture = readFileSync(
  new URL("../docs/architecture.md", import.meta.url),
  "utf8",
);
const deploymentRunbook = readFileSync(
  new URL("../docs/deployment-runbook.md", import.meta.url),
  "utf8",
);
const migrationRunbook = readFileSync(
  new URL("../docs/relay-new-api-migration.md", import.meta.url),
  "utf8",
);
const relayGoMod = readFileSync(
  new URL("../backend/new-api-relay/go.mod", import.meta.url),
  "utf8",
);
const relayDockerfile = readFileSync(
  new URL("../backend/new-api-relay/Dockerfile", import.meta.url),
  "utf8",
);
const faultHarnessDockerfile = readFileSync(
  new URL("../tests/relay-fault-harness/Dockerfile", import.meta.url),
  "utf8",
);
const relayIntegrationCompose = readFileSync(
  new URL("../backend/new-api-relay/integration/docker-compose.yml", import.meta.url),
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

function envExampleValue(name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = envExample.match(new RegExp(`^${escaped}=(.*)$`, "m"));
  assert.ok(match, `missing .env.example entry ${name}`);
  return match[1].trim();
}

test("makes new-api the only ordinary Platform Relay data plane", () => {
  assert.match(compose, /^  RELAY_DEFAULT_BACKEND_ID: new-api-v1$/m);
  assert.match(compose, /^  RELAY_DEFAULT_CONTRACT_REVISION: generations\.v1$/m);
  assert.match(
    compose,
    /RELAY_BACKENDS:\s*>-\r?\n\s+\{"new-api-v1":\{"base_url":"\$\{PLATFORM_RELAY_BASE_URL:-http:\/\/relay-new-api:3000\}"/,
  );
  assert.match(
    compose,
    /RELAY_CALLBACK_SIGNING_SECRETS:\s*>-\r?\n\s+\{"new-api-v1":"\$\{RELAY_CALLBACK_SIGNING_SECRET:-/,
  );
  for (const retiredScalar of [
    "RELAY_BASE_URL",
    "RELAY_CLIENT_ID",
    "RELAY_API_KEY",
    "RELAY_CALLBACK_SIGNING_SECRET",
  ]) {
    assert.doesNotMatch(
      compose,
      new RegExp(`^  ${retiredScalar}:`, "m"),
      `Platform environment must not expose retired ${retiredScalar}`,
    );
  }
  assert.match(
    envExample,
    /^PLATFORM_RELAY_BASE_URL=http:\/\/relay-new-api:3000$/m,
  );
  assert.match(
    compose,
    /RELAY_OPERATIONS_BASE_URL:\s*\$\{PLATFORM_RELAY_OPERATIONS_BASE_URL:-http:\/\/relay-new-api:3000\}/,
  );
  assert.match(envExample, /^PLATFORM_RELAY_OPERATIONS_BASE_URL=http:\/\/relay-new-api:3000$/m);
  assert.equal(
    [...compose.matchAll(/environment:\s*\*platform-environment/g)].length,
    5,
    "every long-running Platform process must share the same Relay selector",
  );
  assert.match(
    serviceBlock("platform-api"),
    /relay-new-api:\s*\r?\n\s+condition: service_healthy/,
  );
  assert.doesNotMatch(compose, /^x-relay-environment:|\.\/backend\/relay|^  relay-api:/m);
});

test("makes the new-api Relay the default isolated data plane", () => {
  const api = serviceBlock("relay-new-api");
  const postgres = serviceBlock("relay-new-api-postgres");
  const redis = serviceBlock("relay-new-api-redis");

  for (const block of [api, postgres, redis]) {
    assert.doesNotMatch(block, /^\s+profiles:/m);
  }
  assert.match(api, /build:\s*\*new-api-relay-build/);
  assert.match(
    compose,
    /^x-new-api-relay-build:\s*&new-api-relay-build\r?\n\s+context:\s*\.\/backend\/new-api-relay$/m,
  );
  assert.match(api, /upstream-revision:\s*"0ab02020603d22e5613bc4cf46bfab06f8567769"/);
  assert.doesNotMatch(api, /calciumion\/new-api:latest/);
  assert.match(api, /127\.0\.0\.1:\$\{NEW_API_RELAY_HOST_PORT:-8300\}:3000/);
  assert.match(api, /relay-new-api-postgres:\s*\r?\n\s+condition: service_healthy/);
  assert.match(api, /relay-new-api-redis:\s*\r?\n\s+condition: service_healthy/);
  assert.match(postgres, /relay-new-api-postgres-data:\/var\/lib\/postgresql\/data/);
  assert.match(redis, /relay-new-api-redis-data:\/data/);
  assert.match(postgres, /- relay-new-api-data/);
  assert.match(redis, /- relay-new-api-data/);
  assert.doesNotMatch(postgres, /- default/);
  assert.doesNotMatch(redis, /- default/);
  assert.match(api, /- relay-new-api-edge/);
  assert.doesNotMatch(api, /- default/);
  for (const service of [
    "platform-api",
    "platform-dispatcher",
    "platform-relay-sync",
    "platform-timeout-worker",
    "platform-publishing-worker",
    "platform-download-gateway-registration-worker",
  ]) {
    assert.match(serviceBlock(service), /- relay-new-api-edge/);
  }
  assert.match(
    serviceBlock("platform-download-gateway-registration-worker"),
    /relay-download-edge:\s*\r?\n\s+condition: service_healthy/,
  );
  assert.match(compose, /relay-new-api-data:\s*\r?\n\s+internal:\s*true/);
  assert.match(
    compose,
    /SQL_DSN_FILE:\s*\/run\/relay-local-db-secrets\/relay-local-runtime-sql-dsn/,
  );
  assert.match(
    api,
    /relay-new-api-local-db-secrets:\/run\/relay-local-db-secrets:ro/,
  );
  const volumeInit = serviceBlock("relay-new-api-volume-init");
  assert.match(
    volumeInit,
    /\.\/deploy\/secrets\/relay-local-runtime-sql-dsn:\/bootstrap-secrets\/relay-local-runtime-sql-dsn:ro/,
  );
  assert.match(
    volumeInit,
    /install -o 10001 -g 10001 -m 0400/,
  );
  assert.doesNotMatch(api, /^\s+SQL_DSN:\s/m);
  assert.match(compose, /REDIS_CONN_STRING:\s*redis:\/\/:[^\r\n]+@relay-new-api-redis:6379\/0/);
});

test("keeps the provider alert URL and signing secret an all-or-nothing Compose env-file pair", () => {
  const alertURL = envExampleValue("NEW_API_RELAY_PROVIDER_ALERT_WEBHOOK_URL");
  const alertSecret = envExampleValue(
    "NEW_API_RELAY_PROVIDER_ALERT_SIGNING_SECRET",
  );

  assert.equal(alertURL === "", alertSecret === "");
  assert.equal(alertURL, "", "the development example must leave both disabled");
  assert.match(
    compose,
    /RELAY_PROVIDER_ALERT_WEBHOOK_URL:\s*\$\{NEW_API_RELAY_PROVIDER_ALERT_WEBHOOK_URL:-\}/,
  );
  assert.match(
    compose,
    /RELAY_PROVIDER_ALERT_SIGNING_SECRET:\s*\$\{NEW_API_RELAY_PROVIDER_ALERT_SIGNING_SECRET:-\}/,
  );
  assert.match(
    compose,
    /PROVIDER_ALERT_SIGNING_SECRET:\s*\$\{NEW_API_RELAY_PROVIDER_ALERT_SIGNING_SECRET:-\}/,
  );
  assert.match(
    compose,
    /PROVIDER_ALERT_FORWARD_WEBHOOK_URL:\s*\$\{PROVIDER_ALERT_FORWARD_WEBHOOK_URL:-\}/,
  );
  assert.match(
    compose,
    /PROVIDER_ALERT_FORWARD_SIGNING_SECRET:\s*\$\{PROVIDER_ALERT_FORWARD_SIGNING_SECRET:-\}/,
  );
  assert.match(deploymentRunbook, /PROVIDER_ALERT_FORWARD_WEBHOOK_URL=/);
  assert.match(deploymentRunbook, /入站与出站密钥不得复用/);
  assert.match(
    serviceBlock("relay-new-api"),
    /environment:\s*\*new-api-relay-environment/,
  );
});

test("wires signed new-api telemetry through the example, Compose, and deployment runbook", () => {
  assert.ok(envExampleValue("RELAY_TELEMETRY_SIGNING_SECRET"));
  assert.equal(envExampleValue("NEW_API_RELAY_PLATFORM_TASK_STAGE_URL"), "");
  assert.equal(
    envExampleValue("NEW_API_RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL"),
    "",
  );
  assert.match(
    compose,
    /RELAY_PLATFORM_TASK_STAGE_URL:\s*\$\{NEW_API_RELAY_PLATFORM_TASK_STAGE_URL:-http:\/\/platform-api:8000\/internal\/relay\/task-stages\}/,
  );
  assert.match(
    compose,
    /RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL:\s*\$\{NEW_API_RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL:-http:\/\/platform-api:8000\/internal\/relay\/operations-snapshots\}/,
  );
  assert.match(
    compose,
    /RELAY_TELEMETRY_SIGNING_SECRET:\s*\$\{RELAY_TELEMETRY_SIGNING_SECRET:-[^\r\n]+\}/,
  );
  assert.match(deploymentRunbook, /RELAY_PLATFORM_TASK_STAGE_URL=https:\/\//);
  assert.match(deploymentRunbook, /RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL=https:\/\//);
  assert.match(deploymentRunbook, /0040_showcase_management/);
  assert.match(deploymentRunbook, /0039_new_api_relay_defaults/);
  assert.match(deploymentRunbook, /0038_download_evidence_checks/);
  assert.doesNotMatch(
    deploymentRunbook,
    /alembic upgrade (?:0017_relay_cap_sync|0018_relay_contract)/,
  );
});

test("separates unknown-submission bearer credentials from approval signing keys", () => {
  assert.equal(
    envExampleValue("PLATFORM_RELAY_OPERATIONS_BASE_URL"),
    "http://relay-new-api:3000",
  );
  const tenantId = envExampleValue("PLATFORM_RELAY_OPERATIONS_TENANT_ID");
  const operationsToken = envExampleValue("PLATFORM_RELAY_OPERATIONS_TOKEN");
  const approvalKeyId = envExampleValue("PLATFORM_RELAY_RECONCILIATION_APPROVAL_KEY_ID");
  const approvalSecret = envExampleValue("PLATFORM_RELAY_RECONCILIATION_APPROVAL_SECRET");
  assert.match(tenantId, /^[0-9a-f]{8}-[0-9a-f-]{27}$/);
  assert.ok(operationsToken.length >= 32);
  assert.ok(approvalKeyId);
  assert.ok(approvalSecret.length >= 32);
  assert.notEqual(operationsToken, approvalSecret);
  assert.deepEqual(JSON.parse(envExampleValue("NEW_API_RELAY_OPERATIONS_CREDENTIALS_JSON")), [
    {
      tenant_id: tenantId,
      token_sha256: createHash("sha256").update(operationsToken).digest("hex"),
    },
  ]);
  assert.deepEqual(
    JSON.parse(envExampleValue("NEW_API_RELAY_RECONCILIATION_APPROVAL_KEYS_JSON")),
    [{ tenant_id: tenantId, key_id: approvalKeyId, secret: approvalSecret }],
  );
  assert.match(
    compose,
    /RELAY_OPERATIONS_BASE_URL:\s*\$\{PLATFORM_RELAY_OPERATIONS_BASE_URL:-http:\/\/relay-new-api:3000\}/,
  );
  assert.match(
    compose,
    /RELAY_TENANT_ID:\s*\$\{PLATFORM_RELAY_OPERATIONS_TENANT_ID:-[0-9a-f-]+\}/,
  );
  assert.match(
    compose,
    /RELAY_OPERATIONS_TOKEN:\s*\$\{PLATFORM_RELAY_OPERATIONS_TOKEN:-[^}]+\}/,
  );
  assert.match(
    compose,
    /RELAY_RECONCILIATION_APPROVAL_KEY_ID:\s*\$\{PLATFORM_RELAY_RECONCILIATION_APPROVAL_KEY_ID:-[^}]+\}/,
  );
  assert.match(
    compose,
    /RELAY_RECONCILIATION_APPROVAL_SECRET:\s*\$\{PLATFORM_RELAY_RECONCILIATION_APPROVAL_SECRET:-[^}]+\}/,
  );
  assert.match(
    compose,
    /RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON:\s*'\$\{NEW_API_RELAY_RECONCILIATION_APPROVAL_KEYS_JSON:-\[\{"tenant_id"/,
  );
  assert.match(deploymentRunbook, /RELAY_RECONCILIATION_APPROVAL_SECRET=/);
  assert.match(deploymentRunbook, /RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON=/);
  assert.match(deploymentRunbook, /submission-unknown\/\{job_id\}\/result/);
});

test("wires immutable CNY provider contract rates through Compose", () => {
  assert.equal(envExampleValue("NEW_API_RELAY_PROVIDER_CONTRACT_RATES_JSON"), "");
  assert.match(
    compose,
    /RELAY_PROVIDER_CONTRACT_RATES_JSON:\s*'\$\{NEW_API_RELAY_PROVIDER_CONTRACT_RATES_JSON:-\}'/,
  );
  assert.match(deploymentRunbook, /固定为 `CNY` 的 `currency`/);
});

test("documents supervised permanent artifact cleanup and OBS versioning safety", () => {
  assert.match(deploymentRunbook, /版本控制为“未启用”/);
  assert.match(deploymentRunbook, /cleaned_retrying/);
  assert.match(deploymentRunbook, /每 24 小时永久复删/);
  assert.match(deploymentRunbook, /cleaned 周期复删失败不会死信/);
  assert.match(deploymentRunbook, /maintenance_required=true/);
  assert.match(deploymentRunbook, /不得把最后一个实例缩容为零/);
});

test("uses the go.mod toolchain for production and fault-harness builds", () => {
  const goVersion = relayGoMod.match(/^go\s+(\d+\.\d+\.\d+)$/m)?.[1];
  assert.ok(goVersion, "new-api go.mod must pin a full Go patch version");
  for (const [label, dockerfile] of [
    ["production", relayDockerfile],
    ["fault harness", faultHarnessDockerfile],
  ]) {
    assert.match(
      dockerfile,
      new RegExp(`^FROM golang:${goVersion.replaceAll(".", "\\.")}-`, "m"),
      `${label} Dockerfile must use the same Go patch version as go.mod`,
    );
  }
  assert.match(
    relayIntegrationCompose,
    new RegExp(`image:\\s*golang:${goVersion.replaceAll(".", "\\.")}-bookworm`),
  );
  assert.match(relayIntegrationCompose, /GOEXPERIMENT:\s*greenteagc/);
});

test("documents immutable new-api rollback and Python production retirement", () => {
  assert.match(architecture, /new-api` 是唯一活动生产 Relay/);
  assert.match(architecture, /task\/outbox 上持久化 backend 与合同 revision/);
  assert.match(deploymentRunbook, /relay-new-api-migration\.md/);
  assert.match(migrationRunbook, /契约对照|契约/);
  assert.match(migrationRunbook, /故障注入/);
  assert.match(migrationRunbook, /真实 Provider/);
  assert.match(migrationRunbook, /submission_unknown|reconciliation_required/);
  assert.match(migrationRunbook, /上一版[^\r\n]*new-api[^\r\n]*不可变镜像|schema-compatible/i);
  assert.match(migrationRunbook, /Python[^\r\n]*(?:离线|oracle|生产准入)/i);
  assert.doesNotMatch(migrationRunbook, /--profile\s+python-relay-rollback/);
});
