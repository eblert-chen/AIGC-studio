import assert from "node:assert/strict";
import test from "node:test";

import {
  assertSecretFreeEvidence,
  candidateImageBuildLabelArgs,
  expectedCandidateImageLabels,
  harnessSourceSnapshot,
  relaySourceSnapshot,
  validateCandidateImageLabels,
  validateRouteAcceptanceTrustDigest,
} from "../scripts/relay-fault-source-snapshot.mjs";

const upstreamRevision = "0ab02020603d22e5613bc4cf46bfab06f8567769";
const routeAcceptanceTrustDigest = `sha256:${"1".repeat(64)}`;

test("Relay source snapshot covers production, acceptance CLI, and module inputs", async () => {
  const snapshot = await relaySourceSnapshot();
  assert.equal(snapshot.file_count, snapshot.files.length);
  assert(snapshot.file_count > 100);
  for (const required of [
    "backend/new-api-relay/controller/platform_generation.go",
    "backend/new-api-relay/service/platform_generation.go",
    "backend/new-api-relay/service/platform_generation_routes.go",
    "backend/new-api-relay/service/platform_generation_operations.go",
    "backend/new-api-relay/service/platform_provider_runtime.go",
    "backend/new-api-relay/cmd/relay-real-channel-acceptance/main.go",
    "backend/new-api-relay/go.mod",
    "backend/new-api-relay/Dockerfile",
    "backend/new-api-relay/web/package.json",
    "backend/new-api-relay/web/src/main.tsx",
    "backend/new-api-relay/i18n/locales/en.yaml",
    "backend/new-api-relay/common/limiter/lua/rate_limit.lua",
  ]) assert(snapshot.files.includes(required), `snapshot is missing ${required}`);
  assert(snapshot.files.every((file) => !file.includes("/.git/")));
  assert.match(snapshot.sha1, /^[0-9a-f]{40}$/);
  assert.match(snapshot.sha256, /^sha256:[0-9a-f]{64}$/);
  assert.equal(snapshot.format, "sorted-portable-path-nul-content-nul-v1");
});

test("candidate build arguments compile the same frozen provenance as the OCI labels", async () => {
  const snapshot = await relaySourceSnapshot();
  const args = candidateImageBuildLabelArgs(
    snapshot,
    upstreamRevision,
    routeAcceptanceTrustDigest,
  ).join("\n");
  assert.match(args, new RegExp(`RELAY_BUILD_UPSTREAM_REVISION=${upstreamRevision}`));
  assert.match(args, new RegExp(`RELAY_BUILD_SOURCE_REVISION=${snapshot.sha1}`));
  assert.match(args, new RegExp(`RELAY_BUILD_SOURCE_SNAPSHOT_SHA256=${snapshot.sha256}`));
  assert.match(args, new RegExp(`RELAY_BUILD_SOURCE_SNAPSHOT_FILE_COUNT=${snapshot.file_count}`));
  assert.match(args, new RegExp(`RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256=${routeAcceptanceTrustDigest}`));
});

test("candidate build rejects a missing, malformed, uppercase, or zero trust digest", async () => {
  const snapshot = await relaySourceSnapshot();
  for (const invalid of [
    undefined,
    "unknown",
    `sha256:${"0".repeat(64)}`,
    `sha256:${"A".repeat(64)}`,
    `sha256:${"1".repeat(63)}`,
    ` ${routeAcceptanceTrustDigest}`,
  ]) {
    assert.throws(
      () => candidateImageBuildLabelArgs(snapshot, upstreamRevision, invalid),
      /NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256/,
    );
  }
  assert.equal(validateRouteAcceptanceTrustDigest(routeAcceptanceTrustDigest), routeAcceptanceTrustDigest);
});

test("candidate labels bind the complete source snapshot and file count", async () => {
  const snapshot = await relaySourceSnapshot();
  const labels = expectedCandidateImageLabels(snapshot, upstreamRevision);
  assert.deepEqual(validateCandidateImageLabels(labels, snapshot, upstreamRevision), []);
  const changed = { ...labels, "ai.video.relay.source-file-count": String(snapshot.file_count - 1) };
  assert(validateCandidateImageLabels(changed, snapshot, upstreamRevision).length > 0);
});

test("harness snapshot fixes runners, validator, Compose, Dockerfile, and evidence control", async () => {
  const snapshot = await harnessSourceSnapshot();
  assert.equal(snapshot.format, "sorted-portable-path-nul-content-nul-v1");
  for (const required of [
    ".github/workflows/ci.yml",
    ".env.example",
    ".gitignore",
    "README.md",
    "backend/new-api-relay/makefile",
    "backend/new-api-relay/integration/docker-compose.yml",
    "backend/new-api-relay/scripts/test-relay-schema-legacy-pg16.ps1",
    "backend/new-api-relay/scripts/fixtures/relay-schema-v1-pg16-tls-test-fixture.patch",
    "deploy/compose.internal-pilot.yml",
    "deploy/compose.relay.principal-rotation.yml",
    "deploy/compose.relay.production.yml",
    "deploy/compose.relay.secure.yml",
    "deploy/compose.relay.staging.yml",
    "deploy/scripts/rebuild-relay-pilot.sh",
    "deploy/scripts/verify-internal-pilot.sh",
    "deploy/relay-production.env.example",
    "deploy/relay-secure.env.example",
    "deploy/relay-staging.env.example",
    "docker-compose.yml",
    "docs/architecture.md",
    "docs/deployment-runbook.md",
    "docs/internal-staging-deployment-readiness-form.md",
    "docs/new-api-production-deployment.md",
    "docs/official-provider-adapters.md",
    "docs/project-tour-2026-08-12.md",
    "docs/provider-adapter-v1.md",
    "docs/provider-monitoring.md",
    "docs/release-readiness.md",
    "docs/relay-new-api-migration.md",
    "docs/reverse-account-pool.md",
    "infra/nginx/platform-api.conf",
    "infra/postgres/init-local-databases.sql",
    "scripts/relay-candidate-baseline.mjs",
    "scripts/relay-candidate-image-args.mjs",
    "scripts/relay-migration-acceptance.mjs",
    "scripts/run-cross-service-cost-acceptance.mjs",
    "scripts/run-relay-fault-harness.mjs",
    "scripts/run-relay-live-fault-harness.mjs",
    "scripts/run-relay-obs-live-acceptance.mjs",
    "scripts/smoke-local.ps1",
    "tests/relay-fault-harness/main.go",
    "tests/relay-fault-harness/docker-compose.yml",
    "tests/relay-fault-harness/Dockerfile",
    "tests/relay-candidate-baseline.test.mjs",
    "tests/deployment-contract.test.mjs",
    "tests/platform-process-secrets-deployment.test.mjs",
    "tests/relay-cutover-compose.test.mjs",
    "tests/relay-cutover-release-contract.test.mjs",
    "tests/relay-download-edge-deployment.test.mjs",
    "tests/relay-migration-deployment.test.mjs",
    "tests/relay-obs-live-runner.test.mjs",
    "tests/relay-production-deployment.test.mjs",
    "tests/relay-principal-rotation-deployment.test.mjs",
    "tests/relay-secret-bundle-schema.test.mjs",
    "tests/relay-real-channel-acceptance.config.example.json",
    "tests/cross-service-cost-acceptance.test.mjs",
    "tests/ci-gates.test.mjs",
    "scripts/collect-cost-acceptance-postgres.py",
    "backend/platform/tests/integration/cost_acceptance_server.py",
  ]) assert(snapshot.files.includes(required), `harness snapshot is missing ${required}`);
});

test("secret-free evidence scan rejects raw credentials and permits token hashes", () => {
  assert.throws(() => assertSecretFreeEvidence({ control_token: "raw-secret-value" }), /raw sensitive field/);
  assert.throws(() => assertSecretFreeEvidence({ message: "Bearer abcdefghijklmnop" }), /bearer credential/);
  assert.doesNotThrow(() => assertSecretFreeEvidence({ old_worker_token_sha256: `sha256:${"a".repeat(64)}` }));
});
