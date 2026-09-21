import { createHash } from "node:crypto";
import { lstat, readdir, readFile } from "node:fs/promises";
import { dirname, extname, relative, resolve, sep } from "node:path";
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

const staticHarnessFiles = [
  ".github/workflows/ci.yml",
  ".github/workflows/relay-live-staging-acceptance.yml",
  ".env.example",
  ".gitattributes",
  ".gitignore",
  "README.md",
  "backend/new-api-relay/makefile",
  "backend/new-api-relay/integration/docker-compose.yml",
  "backend/new-api-relay/scripts/test-relay-schema-legacy-pg16.ps1",
  "backend/new-api-relay/scripts/fixtures/relay-schema-v1-pg16-tls-test-fixture.patch",
  "backend/platform/scripts/platform_source_snapshot.py",
  "backend/platform/scripts/prepare_ark_video_drafts.py",
  "backend/platform/scripts/prepare_google_video_drafts.py",
  "backend/platform/scripts/prepare_minimax_h3_drafts.py",
  "backend/platform/scripts/local_video_lab.py",
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
  "docs/ark-video-integration.md",
  "docs/deployment-runbook.md",
  "docs/google-video-integration.md",
  "docs/internal-staging-deployment-readiness-form.md",
  "docs/local-video-lab.md",
  "docs/minimax-h3-integration.md",
  "docs/new-api-production-deployment.md",
  "docs/official-provider-adapters.md",
  "docs/payment-finance-closure.md",
  "docs/project-tour-2026-08-12.md",
  "docs/provider-adapter-v1.md",
  "docs/provider-monitoring.md",
  "docs/release-readiness.md",
  "docs/relay-new-api-migration.md",
  "docs/reverse-account-pool.md",
  "infra/nginx/platform-api.conf",
  "infra/postgres/init-local-databases.sql",
  "package.json",
  "package-lock.json",
  "playwright.config.mjs",
  "vite.config.mjs",
  "backend/platform/tests/integration/cost_acceptance_server.py",
  "backend/platform/tests/integration/test_new_api_channel_cost_delivery.py",
  "scripts/collect-cost-acceptance-postgres.py",
  "scripts/local-video-lab.mjs",
  "scripts/local-video-lab-gateway.mjs",
  "scripts/local-video-lab-network-edge.mjs",
  "scripts/local-video-lab-object-store.mjs",
  "scripts/local-video-lab-object-store.test.mjs",
  "scripts/local-video-lab-verify.mjs",
  "scripts/relay-fault-source-snapshot.mjs",
  "scripts/relay-candidate-baseline.mjs",
  "scripts/relay-candidate-image-args.mjs",
  "scripts/check-relay-secret-paths.mjs",
  "scripts/relay-migration-acceptance.mjs",
  "scripts/run-cross-service-cost-acceptance.mjs",
  "scripts/run-root-tests.mjs",
  "scripts/run-relay-fault-harness.mjs",
  "scripts/run-relay-live-fault-harness.mjs",
  "scripts/run-relay-obs-live-acceptance.mjs",
  "scripts/smoke-local.ps1",
  "scripts/start-all-local.ps1",
  "src/app/useStudioCollections.js",
  "src/taskArtifacts.js",
  "src/modelCapabilities.js",
  "src/billingPresentation.js",
  "src/api/platformClient.js",
  "src/api/assetsTasksApi.js",
  "src/api/companyApi.js",
  "src/api/platformAdminApi.js",
  "src/api/platformCore.js",
  "src/api/publishingApi.js",
  "src/api/sessionPersonalApi.js",
  "src/api/showcaseApi.js",
  "tests/relay-fault-harness/Dockerfile",
  "tests/relay-fault-harness/docker-compose.yml",
  "tests/relay-fault-harness/main.go",
  "tests/ark-video-creation.test.mjs",
  "tests/google-video-creation.test.mjs",
  "tests/minimax-h3-creation.test.mjs",
  "tests/local-video-lab.test.mjs",
  "tests/local-video-lab-gateway.test.mjs",
  "tests/local-video-lab-object-store.test.mjs",
  "tests/local-video-lab-verify.test.mjs",
  "tests/local-services-startup-contract.test.mjs",
  "tests/studio-collection-reset.test.mjs",
  "tests/fixtures/ark-video-discovery.mjs",
  "tests/fixtures/minimax-h3-discovery.mjs",
  "tests/browser-fixtures/ark-video-platform.mjs",
  "tests/browser-fixtures/minimax-h3-platform.mjs",
  "tests/browser/ark-video-creation.spec.mjs",
  "tests/browser/ark-video.playwright.config.mjs",
  "tests/browser/minimax-h3-creation.spec.mjs",
  "tests/browser/minimax-h3.playwright.config.mjs",
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
  "tests/relay-live-staging-workflow.test.mjs",
  "tests/relay-migration-deployment.test.mjs",
  "tests/relay-migration-acceptance.test.mjs",
  "tests/relay-obs-live-runner.test.mjs",
  "tests/relay-production-deployment.test.mjs",
  "tests/relay-real-channel-acceptance.config.example.json",
];

// The embedded director desk is rebuilt into the ignored public/director-desk
// directory before the root frontend is bundled. Hash its reviewed source
// inputs, never the generated output, dependency installation, screenshots, or
// upstream presentation material.
const directorDeskRoot = resolve(workspace, "vendor", "storyai-3d-director-desk");
const directorDeskRootFiles = Object.freeze([
  "index.html",
  "LICENSE",
  "package.json",
  "tsconfig.json",
  "tsconfig.node.json",
  "vite.config.ts",
]);
const forbiddenDirectorDeskSourceDirectories = new Set([
  ".vite",
  "dist",
  "node_modules",
  "playwright-report",
  "screenshots",
  "temp",
  "test-results",
  "tmp",
]);

const localModuleExtensions = Object.freeze(["", ".js", ".jsx", ".mjs", ".cjs", ".json"]);
const rootTestSuffix = ".test.mjs";
const generatedHarnessPathPrefixes = Object.freeze([
  ".local-backups/",
  "audit/",
  "dist/",
  "playwright-report/",
  "public/director-desk/",
  "test-results/",
]);

function portable(path) {
  return path.split(sep).join("/");
}

async function walk(directory) {
  const output = [];
  const entries = await readdir(directory, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
  for (const entry of entries) {
    if (entry.isSymbolicLink()) {
      throw new Error(`Relay source contains a symbolic link: ${portable(relative(relayRoot, resolve(directory, entry.name)))}`);
    }
    if (entry.isDirectory() && ignoredDirectories.has(entry.name)) continue;
    const absolute = resolve(directory, entry.name);
    if (entry.isDirectory()) output.push(...await walk(absolute));
    else if (entry.isFile()) output.push(absolute);
  }
  return output;
}

async function walkDirectorDeskSource(directory) {
  const output = [];
  const entries = await readdir(directory, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
  for (const entry of entries) {
    const absolute = resolve(directory, entry.name);
    if (entry.isSymbolicLink()) {
      throw new Error(
        `Director desk build input contains a symbolic link: ${portable(relative(workspace, absolute))}`,
      );
    }
    if (entry.isDirectory()) {
      if (forbiddenDirectorDeskSourceDirectories.has(entry.name.toLowerCase())) {
        throw new Error(
          `Director desk src contains a forbidden generated directory: ${portable(relative(workspace, absolute))}`,
        );
      }
      output.push(...await walkDirectorDeskSource(absolute));
    } else if (entry.isFile()) {
      output.push(portable(relative(workspace, absolute)));
    }
  }
  return output;
}

export async function directorDeskBuildInputFiles() {
  const files = [];
  for (const file of directorDeskRootFiles) {
    const absolute = resolve(directorDeskRoot, file);
    if (!await existingRegularFile(absolute)) {
      throw new Error(`Director desk build input is missing: ${portable(relative(workspace, absolute))}`);
    }
    files.push(portable(relative(workspace, absolute)));
  }
  const sourceRoot = resolve(directorDeskRoot, "src");
  let sourceMetadata;
  try {
    sourceMetadata = await lstat(sourceRoot);
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") {
      throw new Error("Director desk src build input is missing");
    }
    throw error;
  }
  if (sourceMetadata.isSymbolicLink() || !sourceMetadata.isDirectory()) {
    throw new Error("Director desk src build input is not a regular directory");
  }
  files.push(...await walkDirectorDeskSource(sourceRoot));
  return [...new Set(files)].sort((left, right) => left < right ? -1 : left > right ? 1 : 0);
}

async function existingRegularFile(path) {
  try {
    const metadata = await lstat(path);
    if (metadata.isSymbolicLink()) {
      throw new Error(`Harness dependency must not be a symbolic link: ${portable(relative(workspace, path))}`);
    }
    return metadata.isFile();
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") return false;
    throw error;
  }
}

function localModuleSpecifiers(source) {
  const specifiers = new Map();
  const modulePattern = /(?:\b(?:import|export)\s+(?:[^;"'`]*?\s+from\s*)?|\bimport\s*\()\s*["']([^"']+)["']/gs;
  const requirePattern = /\brequire\s*\(\s*["']([^"']+)["']\s*\)/gs;
  const urlPattern = /\bnew\s+URL\s*\(\s*["']([^"']+)["']\s*,\s*import\.meta\.url\s*,?\s*\)/gs;
  for (const pattern of [modulePattern, requirePattern]) {
    for (const match of source.matchAll(pattern)) {
      if (match[1]?.startsWith(".")) specifiers.set(match[1], false);
    }
  }
  for (const match of source.matchAll(urlPattern)) {
    if (match[1]?.startsWith(".") && !specifiers.has(match[1])) specifiers.set(match[1], true);
  }
  return [...specifiers].map(([specifier, optional]) => ({ specifier, optional }));
}

async function resolveLocalModule(importer, specifier, optional) {
  const unresolved = resolve(workspace, dirname(importer), specifier);
  const workspaceRelative = relative(workspace, unresolved);
  if (workspaceRelative.startsWith("..") || resolve(workspace, workspaceRelative) !== unresolved) {
    throw new Error(`Harness dependency escapes the workspace: ${specifier}`);
  }
  const portableWorkspaceRelative = portable(workspaceRelative);
  if (generatedHarnessPathPrefixes.some((prefix) => portableWorkspaceRelative.startsWith(prefix))) {
    if (optional) return null;
    throw new Error(`Harness module dependency points at generated output: ${importer} -> ${specifier}`);
  }
  const candidates = [];
  if (extname(unresolved)) candidates.push(unresolved);
  else {
    for (const extension of localModuleExtensions) candidates.push(`${unresolved}${extension}`);
    for (const extension of localModuleExtensions.slice(1)) candidates.push(resolve(unresolved, `index${extension}`));
  }
  for (const candidate of candidates) {
    if (await existingRegularFile(candidate)) return portable(relative(workspace, candidate));
  }
  try {
    const metadata = await lstat(unresolved);
    if (metadata.isDirectory()) return null;
  } catch (error) {
    if (error?.code !== "ENOENT" && error?.code !== "ENOTDIR") throw error;
  }
  if (optional) return null;
  throw new Error(`Harness local dependency cannot be resolved: ${importer} -> ${specifier}`);
}

async function expandLocalModuleClosure(seedFiles) {
  const files = new Set(seedFiles);
  const pending = [...files];
  while (pending.length > 0) {
    const file = pending.pop();
    if (!/[.](?:c?m?js|jsx)$/u.test(file)) continue;
    const source = await readFile(resolve(workspace, file), "utf8");
    for (const { specifier, optional } of localModuleSpecifiers(source)) {
      const dependency = await resolveLocalModule(file, specifier, optional);
      if (dependency === null) continue;
      if (files.has(dependency)) continue;
      files.add(dependency);
      pending.push(dependency);
    }
  }
  return [...files];
}

async function discoveredRootTestFiles() {
  const entries = await readdir(resolve(workspace, "tests"), { withFileTypes: true });
  return entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(rootTestSuffix))
    .map((entry) => `tests/${entry.name}`);
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
    path === "Dockerfile.dev" ||
    path === ".dockerignore" ||
    path === "VERSION" ||
    path === "LICENSE" ||
    path === "NOTICE" ||
    path === "THIRD-PARTY-LICENSES.md" ||
    path.startsWith("web/") ||
    path.startsWith("i18n/locales/") ||
    (path.startsWith("generationprofile/") && path.endsWith(".json")) ||
    path === "common/limiter/lua/rate_limit.lua";
}

async function hashFiles(files) {
  const normalized = [...new Set(files)].sort((left, right) => left < right ? -1 : left > right ? 1 : 0);
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
  const rootTests = await discoveredRootTestFiles();
  const directorDeskInputs = await directorDeskBuildInputFiles();
  return hashFiles(await expandLocalModuleClosure([
    ...staticHarnessFiles,
    ...rootTests,
    ...directorDeskInputs,
  ]));
}

const relaySourceEnvironmentKeys = Object.freeze({
  NEW_API_RELAY_SOURCE_REVISION: "sha1",
  NEW_API_RELAY_SOURCE_SNAPSHOT_SHA256: "sha256",
  NEW_API_RELAY_SOURCE_SNAPSHOT_FILE_COUNT: "file_count",
});

function environmentAssignments(source, key) {
  const pattern = new RegExp(`^${key}=([^\\r\\n]*)$`, "gmu");
  return [...source.matchAll(pattern)];
}

export function validateRelaySourceEnvironment(source, snapshot) {
  const errors = [];
  for (const [key, field] of Object.entries(relaySourceEnvironmentKeys)) {
    const assignments = environmentAssignments(source, key);
    if (assignments.length !== 1) {
      errors.push(`.env.example must contain exactly one ${key} assignment`);
      continue;
    }
    const expected = String(snapshot[field]);
    if (assignments[0][1] !== expected) {
      errors.push(`.env.example ${key} does not match the current Relay source snapshot`);
    }
  }
  return errors;
}

export function synchronizeRelaySourceEnvironment(source, snapshot) {
  let output = source;
  const structuralErrors = [];
  for (const [key, field] of Object.entries(relaySourceEnvironmentKeys)) {
    const assignments = environmentAssignments(output, key);
    if (assignments.length !== 1) {
      structuralErrors.push(`.env.example must contain exactly one ${key} assignment`);
      continue;
    }
    output = output.replace(
      new RegExp(`^${key}=[^\\r\\n]*$`, "mu"),
      `${key}=${String(snapshot[field])}`,
    );
  }
  if (structuralErrors.length > 0) throw new Error(structuralErrors.join("; "));
  return output;
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
