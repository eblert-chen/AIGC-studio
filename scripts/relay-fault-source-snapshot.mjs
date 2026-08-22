import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { dirname, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const relayRoot = resolve(workspace, "backend", "new-api-relay");
const ignoredDirectories = new Set([".git", ".gocache", "bin", "dist", "node_modules"]);
const snapshotFormat = "sorted-portable-path-nul-content-nul-v1";
const sha256DigestPattern = /^sha256:[0-9a-f]{64}$/;
const zeroSHA256Digest = `sha256:${"0".repeat(64)}`;
const relayDeploymentRuntimeFiles = [];
const forbiddenRelayRootBuildArtifact = /^\.tmp-new-api-/;

export const CANDIDATE_IMAGE_LABELS = Object.freeze({
  upstreamRevision: "ai.video.relay.upstream-revision",
  sourceRevision: "org.opencontainers.image.revision",
  sourceSnapshot: "ai.video.relay.source-snapshot-sha256",
  sourceFileCount: "ai.video.relay.source-file-count",
});

const harnessFiles = [
  ".github/workflows/ci.yml",
  ".env.example",
  ".gitattributes",
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
  "package.json",
  "backend/platform/tests/integration/cost_acceptance_server.py",
  "backend/platform/tests/integration/test_new_api_channel_cost_delivery.py",
  "scripts/collect-cost-acceptance-postgres.py",
  "scripts/relay-fault-source-snapshot.mjs",
  "scripts/relay-candidate-baseline.mjs",
  "scripts/relay-candidate-image-args.mjs",
  "scripts/check-relay-secret-paths.mjs",
  "scripts/relay-migration-acceptance.mjs",
  "scripts/run-cross-service-cost-acceptance.mjs",
  "scripts/run-relay-fault-harness.mjs",
  "scripts/run-relay-live-fault-harness.mjs",
  "scripts/run-relay-obs-live-acceptance.mjs",
  "scripts/smoke-local.ps1",
  "tests/relay-fault-harness/Dockerfile",
  "tests/relay-fault-harness/docker-compose.yml",
  "tests/relay-fault-harness/main.go",
  "tests/relay-fault-source-snapshot.test.mjs",
  "tests/relay-candidate-baseline.test.mjs",
  "tests/deployment-contract.test.mjs",
  "tests/platform-process-secrets-deployment.test.mjs",
  "tests/relay-cutover-compose.test.mjs",
  "tests/relay-cutover-release-contract.test.mjs",
  "tests/relay-download-edge-deployment.test.mjs",
  "tests/relay-principal-rotation-deployment.test.mjs",
  "tests/relay-secret-bundle-schema.test.mjs",
  "tests/relay-secret-paths.test.mjs",
  "tests/cross-service-cost-acceptance.test.mjs",
  "tests/ci-gates.test.mjs",
  "tests/relay-live-fault-evidence.test.mjs",
  "tests/relay-migration-deployment.test.mjs",
  "tests/relay-migration-acceptance.test.mjs",
  "tests/relay-obs-live-runner.test.mjs",
  "tests/relay-production-deployment.test.mjs",
  "tests/relay-real-channel-acceptance.config.example.json",
];

function portable(path) {
  return path.split(sep).join("/");
}

async function walk(directory) {
  const output = [];
  const entries = await readdir(directory, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
  for (const entry of entries) {
    if (entry.isDirectory() && ignoredDirectories.has(entry.name)) continue;
    const absolute = resolve(directory, entry.name);
    if (entry.isDirectory()) output.push(...await walk(absolute));
    else if (entry.isFile()) output.push(absolute);
  }
  return output;
}

export function assertNoRelayRootBuildArtifacts(paths) {
  const forbidden = paths
    .map((path) => portable(relative(relayRoot, path)))
    .filter((path) => !path.includes("/") && forbiddenRelayRootBuildArtifact.test(path));
  if (forbidden.length > 0) {
    throw new Error("Relay source root contains a forbidden temporary build artifact");
  }
}

function relaySourceIncluded(absolute) {
  const path = portable(relative(relayRoot, absolute));
  const name = path.split("/").at(-1);
  return path.endsWith(".go") ||
    name === "go.mod" ||
    name === "go.sum" ||
    path === "Dockerfile" ||
    path === "VERSION" ||
    path === "LICENSE" ||
    path === "NOTICE" ||
    path === "THIRD-PARTY-LICENSES.md" ||
    path.startsWith("web/") ||
    path.startsWith("i18n/locales/") ||
    path === "common/limiter/lua/rate_limit.lua";
}

async function hashFiles(files) {
  const normalized = [...files].sort((left, right) => left.localeCompare(right, "en"));
  const sha1 = createHash("sha1");
  const sha256 = createHash("sha256");
  for (const file of normalized) {
    const contents = await readFile(resolve(workspace, file));
    for (const hash of [sha1, sha256]) {
      hash.update(file);
      hash.update("\0");
      hash.update(contents);
      hash.update("\0");
    }
  }
  return {
    format: snapshotFormat,
    files: normalized,
    file_count: normalized.length,
    sha1: sha1.digest("hex"),
    sha256: `sha256:${sha256.digest("hex")}`,
  };
}

export async function relaySourceSnapshot() {
  const discoveredFiles = await walk(relayRoot);
  assertNoRelayRootBuildArtifacts(discoveredFiles);
  const absoluteFiles = discoveredFiles.filter(relaySourceIncluded);
  return hashFiles([
    ...absoluteFiles.map((absolute) => portable(relative(workspace, absolute))),
    ...relayDeploymentRuntimeFiles,
  ]);
}

export async function harnessSourceSnapshot() {
  return hashFiles(harnessFiles);
}

export function expectedCandidateImageLabels(snapshot, upstreamRevision) {
  return {
    [CANDIDATE_IMAGE_LABELS.upstreamRevision]: upstreamRevision,
    [CANDIDATE_IMAGE_LABELS.sourceRevision]: snapshot.sha1,
    [CANDIDATE_IMAGE_LABELS.sourceSnapshot]: snapshot.sha256,
    [CANDIDATE_IMAGE_LABELS.sourceFileCount]: String(snapshot.file_count),
  };
}

export function validateRouteAcceptanceTrustDigest(value) {
  if (typeof value !== "string" || !sha256DigestPattern.test(value)) {
    throw new Error(
      "NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256 must be an explicit lowercase sha256: digest",
    );
  }
  if (value === zeroSHA256Digest) {
    throw new Error("NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256 must not be the zero digest");
  }
  return value;
}

export function candidateImageBuildLabelArgs(snapshot, upstreamRevision, routeAcceptanceTrustDigest) {
  const validatedTrustDigest = validateRouteAcceptanceTrustDigest(routeAcceptanceTrustDigest);
  const labels = Object.entries(expectedCandidateImageLabels(snapshot, upstreamRevision))
    .flatMap(([key, value]) => ["--label", `${key}=${value}`]);
  return [
    ...labels,
    "--build-arg", `RELAY_BUILD_UPSTREAM_REVISION=${upstreamRevision}`,
    "--build-arg", `RELAY_BUILD_SOURCE_REVISION=${snapshot.sha1}`,
    "--build-arg", `RELAY_BUILD_SOURCE_SNAPSHOT_SHA256=${snapshot.sha256}`,
    "--build-arg", `RELAY_BUILD_SOURCE_SNAPSHOT_FILE_COUNT=${snapshot.file_count}`,
    "--build-arg", `RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256=${validatedTrustDigest}`,
  ];
}

export function validateCandidateImageLabels(labels, snapshot, upstreamRevision) {
  const expected = expectedCandidateImageLabels(snapshot, upstreamRevision);
  const errors = [];
  for (const [key, value] of Object.entries(expected)) {
    if (labels?.[key] !== value) errors.push(`candidate image label ${key} does not match the frozen source snapshot`);
  }
  return errors;
}

export function assertSecretFreeEvidence(value, forbiddenValues = []) {
  const serialized = JSON.stringify(value);
  for (const forbidden of forbiddenValues) {
    if (typeof forbidden === "string" && forbidden.length >= 6 && serialized.includes(forbidden)) {
      throw new Error("acceptance evidence contains a forbidden credential value");
    }
  }
  const sensitiveKey = /(?:password|secret|authorization|bearer|api[_-]?key|(?:^|[_-])token(?:$|[_-]))/i;
  const safeDigestKey = /(?:sha256|hash|fingerprint|fenced)$/i;
  const visit = (item, key = "") => {
    if (typeof item === "string") {
      if (sensitiveKey.test(key) && !safeDigestKey.test(key)) throw new Error(`acceptance evidence contains raw sensitive field ${key}`);
      if (/\bBearer\s+[A-Za-z0-9._~+\/-]{8,}/i.test(item)) throw new Error("acceptance evidence contains a bearer credential");
      if (/:\/\/[^/@\s:]+:[^/@\s]+@/.test(item)) throw new Error("acceptance evidence contains URL userinfo");
      return;
    }
    if (Array.isArray(item)) {
      for (const child of item) visit(child, key);
      return;
    }
    if (item && typeof item === "object") {
      for (const [childKey, child] of Object.entries(item)) visit(child, childKey);
    }
  };
  visit(value);
}
