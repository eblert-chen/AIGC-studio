import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const schemaFiles = [
  [
    "docs/schemas/relay-service-principals.schema.json",
    "relay_service_principals",
    ["kind", "schema_version", "principals"],
  ],
  [
    "docs/schemas/relay-api-runtime-secrets.schema.json",
    "relay_api_runtime_secrets",
    [
      "kind",
      "schema_version",
      "redis_dsn",
      "application",
      "clients",
      "operations_credentials",
      "reconciliation_approval_keys",
      "internal_admission_token",
      "artifact_signing_secret",
      "huawei_obs",
      "provider_alert_signing_secret",
      "platform_internal_service_token",
      "channel_cost_signing_secret",
      "telemetry_signing_secret",
    ],
  ],
  [
    "docs/schemas/relay-download-edge-runtime-secrets.schema.json",
    "relay_download_edge_runtime_secrets",
    [
      "kind",
      "schema_version",
      "registration_token",
      "registration_signing_secret",
      "ticket_token_key_base64",
      "source_encryption_key_base64",
      "platform_edge_completion_token",
      "completion_signing_secret",
      "proof_signing_seed_base64",
      "proof_read_token",
    ],
  ],
];

function assertNoSecretSamples(value, path = "$") {
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertNoSecretSamples(item, `${path}[${index}]`));
    return;
  }
  if (value === null || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    assert.notEqual(key, "default", `${path} must not contain a default`);
    assert.notEqual(key, "example", `${path} must not contain an example`);
    assert.notEqual(key, "examples", `${path} must not contain examples`);
    assertNoSecretSamples(child, `${path}.${key}`);
  }
}

test("publishes secret-free exact schemas for all protected Relay bundles", () => {
  for (const [path, kind, required] of schemaFiles) {
    const raw = readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
    const schema = JSON.parse(raw);
    assert.equal(schema.$schema, "https://json-schema.org/draft/2020-12/schema");
    assert.equal(schema.type, "object");
    assert.equal(schema.additionalProperties, false);
    assert.equal(schema.properties.kind.const, kind);
    assert.equal(schema.properties.schema_version.const, 1);
    assert.deepEqual(schema.required, required);
    assertNoSecretSamples(schema);
  }
});

test("documents generation, ownership, ordering, isolation, and parser authority", () => {
  const runbook = readFileSync(
    new URL("../docs/new-api-production-deployment.md", import.meta.url),
    "utf8",
  );
  for (const path of schemaFiles.map(([path]) => path.replace("docs/", ""))) {
    assert.ok(runbook.includes(path), `runbook must link ${path}`);
  }
  assert.match(runbook, /cryptographically secure random source/);
  assert.match(runbook, /mode other than `0400`\/`0600`/);
  assert.match(runbook, /filesystem\s+must itself report read-only/);
  assert.match(runbook, /strictly increasing `client_id`/);
  assert.match(runbook, /exact same client\/tenant order as A/);
  assert.match(runbook, /Ed25519 seed/);
  assert.match(runbook, /never replaces the\s+same-image fail-closed parser/);
});
