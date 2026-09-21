import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  assertSecretFreeEvidence,
  candidateImageBuildLabelArgs,
  directorDeskBuildInputFiles,
  expectedCandidateImageLabels,
  harnessSourceSnapshot,
  relaySourceSnapshot,
  synchronizeRelaySourceEnvironment,
  validateCandidateImageLabels,
  validateRelaySourceEnvironment,
  validateRouteAcceptanceTrustDigest,
} from "../scripts/relay-fault-source-snapshot.mjs";

const upstreamRevision = "0ab02020603d22e5613bc4cf46bfab06f8567769";
const routeAcceptanceTrustDigest = `sha256:${"1".repeat(64)}`;
const relayRoot = fileURLToPath(new URL("../backend/new-api-relay/", import.meta.url));

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
    "backend/new-api-relay/Dockerfile.dev",
    "backend/new-api-relay/.dockerignore",
    "backend/new-api-relay/web/package.json",
    "backend/new-api-relay/web/src/main.tsx",
    "backend/new-api-relay/i18n/locales/en.yaml",
    "backend/new-api-relay/generationprofile/seedance_models.v1.json",
    "backend/new-api-relay/generationprofile/minimax_h3_models.v1.json",
    "backend/new-api-relay/cmd/relay-minimax-h3-plan/main.go",
    "backend/new-api-relay/cmd/relay-local-video-lab/main.go",
    "backend/new-api-relay/cmd/relay-local-video-lab/fixture_video.go",
    "backend/new-api-relay/cmd/relay-source-snapshot/main.go",
    "backend/new-api-relay/localvideoconfig/config.go",
    "backend/new-api-relay/localvideoconfig/environment.go",
    "backend/new-api-relay/model/local_video_lab_route_guards.go",
    "backend/new-api-relay/model/platform_generation_provider_proof.go",
    "backend/new-api-relay/service/billing.go",
    "backend/new-api-relay/service/platform_artifact_store_obs.go",
    "backend/new-api-relay/common/limiter/lua/rate_limit.lua",
  ]) assert(snapshot.files.includes(required), `snapshot is missing ${required}`);
  assert(snapshot.files.every((file) => !file.includes("/.git/")));
  assert.match(snapshot.sha1, /^[0-9a-f]{40}$/);
  assert.match(snapshot.sha256, /^sha256:[0-9a-f]{64}$/);
  assert.equal(snapshot.format, "sorted-portable-path-nul-content-nul-v1");
});

test("every profile catalog compiled with go:embed is bound to the Relay source identity", async () => {
  const snapshot = await relaySourceSnapshot();
  const profileRoot = new URL("../backend/new-api-relay/generationprofile/", import.meta.url);
  const embeddedCatalogs = new Set();
  for (const entry of await readdir(profileRoot, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".go")) continue;
    const source = await readFile(new URL(entry.name, profileRoot), "utf8");
    for (const declaration of source.matchAll(/^\/\/go:embed\s+(.+)$/gm)) {
      for (const pattern of declaration[1].trim().split(/\s+/)) {
        assert.match(pattern, /^[a-z0-9_]+_models\.v[1-9][0-9]*\.json$/,
          "New profile embed forms need an explicit snapshot-coverage review");
        const path = `backend/new-api-relay/generationprofile/${pattern}`;
        assert.ok(snapshot.files.includes(path), `compiled profile catalog is missing: ${path}`);
        embeddedCatalogs.add(path);
      }
    }
  }
  for (const path of [
    "backend/new-api-relay/generationprofile/seedance_models.v1.json",
    "backend/new-api-relay/generationprofile/minimax_h3_models.v1.json",
  ]) assert.ok(embeddedCatalogs.has(path), `expected compiled profile catalog is missing: ${path}`);
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

test("the in-build verifier recomputes the exact deterministic Relay source identity", async (context) => {
  const goVersion = spawnSync("go", ["version"], { encoding: "utf8", windowsHide: true });
  if (goVersion.error?.code === "ENOENT") {
    context.skip("the host has no Go executable; the pinned Docker builder runs this verifier during image build");
    return;
  }
  if (goVersion.status !== 0) throw goVersion.error ?? new Error(goVersion.stderr);
  const snapshot = await relaySourceSnapshot();
  const arguments_ = [
    "run",
    "./cmd/relay-source-snapshot",
    "--root", ".",
    "--expected-revision", snapshot.sha1,
    "--expected-sha256", snapshot.sha256,
    "--expected-file-count", String(snapshot.file_count),
  ];
  const output = execFileSync("go", arguments_, {
    cwd: relayRoot,
    encoding: "utf8",
    env: { ...process.env, GOWORK: "off" },
    windowsHide: true,
  });
  assert.match(output, new RegExp(`verified Relay build source revision=${snapshot.sha1}`));
  assert.throws(
    () => execFileSync("go", arguments_.map((value) => value === snapshot.sha256
      ? `sha256:${"0".repeat(64)}`
      : value), {
      cwd: relayRoot,
      encoding: "utf8",
      env: { ...process.env, GOWORK: "off" },
      stdio: "pipe",
      windowsHide: true,
    }),
    /Command failed/u,
  );
});

test("Docker builds reject caller-supplied provenance unless the in-build source verification passes", async () => {
  for (const name of ["Dockerfile", "Dockerfile.dev"]) {
    const dockerfile = await readFile(new URL(`../backend/new-api-relay/${name}`, import.meta.url), "utf8");
    assert.match(dockerfile, /go run \.\/cmd\/relay-source-snapshot/, `${name} must recompute source identity`);
    assert.match(dockerfile, /--expected-revision "\$\{RELAY_BUILD_SOURCE_REVISION\}"/);
    assert.match(dockerfile, /--expected-sha256 "\$\{RELAY_BUILD_SOURCE_SNAPSHOT_SHA256\}"/);
    assert.match(dockerfile, /--expected-file-count "\$\{RELAY_BUILD_SOURCE_SNAPSHOT_FILE_COUNT\}"/);
    assert.doesNotMatch(dockerfile, /ARG RELAY_BUILD_SOURCE_(?:REVISION|SNAPSHOT_SHA256|SNAPSHOT_FILE_COUNT)=unknown/);
  }
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
  assert.equal(snapshot.file_count, new Set(snapshot.files).size,
    "Harness file_count must count unique validation inputs");
  for (const required of [
    ".github/workflows/ci.yml",
    ".github/workflows/relay-live-staging-acceptance.yml",
    ".env.example",
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
    "vendor/storyai-3d-director-desk/index.html",
    "vendor/storyai-3d-director-desk/LICENSE",
    "vendor/storyai-3d-director-desk/package.json",
    "vendor/storyai-3d-director-desk/tsconfig.json",
    "vendor/storyai-3d-director-desk/tsconfig.node.json",
    "vendor/storyai-3d-director-desk/vite.config.ts",
    "vendor/storyai-3d-director-desk/src/main.tsx",
    "vendor/storyai-3d-director-desk/src/editor/io/hostBridge.ts",
    "vite.config.mjs",
    "scripts/relay-candidate-baseline.mjs",
    "scripts/relay-candidate-image-args.mjs",
    "scripts/relay-migration-acceptance.mjs",
    "scripts/run-cross-service-cost-acceptance.mjs",
    "scripts/run-root-tests.mjs",
    "scripts/run-relay-fault-harness.mjs",
    "scripts/run-relay-live-fault-harness.mjs",
    "scripts/run-relay-obs-live-acceptance.mjs",
    "scripts/smoke-local.ps1",
    "tests/relay-fault-harness/main.go",
    "tests/relay-fault-harness/docker-compose.yml",
    "tests/relay-fault-harness/Dockerfile",
    "tests/ark-video-creation.test.mjs",
    "tests/google-video-creation.test.mjs",
    "src/app/pendingGeneration.js",
    "tests/minimax-h3-creation.test.mjs",
    "tests/fixtures/ark-video-discovery.mjs",
    "tests/fixtures/minimax-h3-discovery.mjs",
    "tests/browser-fixtures/ark-video-platform.mjs",
    "tests/browser-fixtures/minimax-h3-platform.mjs",
    "tests/browser/ark-video-creation.spec.mjs",
    "tests/browser/ark-video.playwright.config.mjs",
    "tests/browser/minimax-h3-creation.spec.mjs",
    "tests/browser/minimax-h3.playwright.config.mjs",
    "tests/relay-candidate-baseline.test.mjs",
    "tests/deployment-contract.test.mjs",
    "tests/platform-process-secrets-deployment.test.mjs",
    "tests/relay-cutover-compose.test.mjs",
    "tests/relay-cutover-release-contract.test.mjs",
    "tests/relay-download-edge-deployment.test.mjs",
    "tests/relay-live-staging-workflow.test.mjs",
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

test("director desk harness closure includes reviewed build inputs and excludes generated material", async () => {
  const inputs = await directorDeskBuildInputFiles();
  for (const required of [
    "vendor/storyai-3d-director-desk/index.html",
    "vendor/storyai-3d-director-desk/LICENSE",
    "vendor/storyai-3d-director-desk/package.json",
    "vendor/storyai-3d-director-desk/tsconfig.json",
    "vendor/storyai-3d-director-desk/tsconfig.node.json",
    "vendor/storyai-3d-director-desk/vite.config.ts",
    "vendor/storyai-3d-director-desk/src/main.tsx",
    "vendor/storyai-3d-director-desk/src/App.tsx",
    "vendor/storyai-3d-director-desk/src/editor/io/hostBridge.ts",
    "vendor/storyai-3d-director-desk/src/styles/index.css",
  ]) assert.ok(inputs.includes(required), `director desk input is missing: ${required}`);

  const excludedPrefixes = [
    "vendor/storyai-3d-director-desk/dist/",
    "vendor/storyai-3d-director-desk/images/",
    "vendor/storyai-3d-director-desk/node_modules/",
    "vendor/storyai-3d-director-desk/public/",
    "vendor/storyai-3d-director-desk/screenshots/",
    "vendor/storyai-3d-director-desk/temp/",
    "vendor/storyai-3d-director-desk/test-results/",
  ];
  for (const path of inputs) {
    assert.ok(
      excludedPrefixes.every((prefix) => !path.startsWith(prefix)),
      `generated or presentation material entered the director source identity: ${path}`,
    );
  }

  const snapshot = await harnessSourceSnapshot();
  for (const path of inputs) {
    assert.ok(snapshot.files.includes(path), `harness snapshot is missing director input: ${path}`);
  }
});

test("harness automatically binds every root test and its local module closure", async () => {
  const snapshot = await harnessSourceSnapshot();
  const entries = await readdir(new URL("../tests/", import.meta.url), { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".test.mjs")) continue;
    assert.ok(snapshot.files.includes(`tests/${entry.name}`), `root test is missing: ${entry.name}`);
  }
  for (const googleDependency of [
    "src/api/platformClient.js",
    "src/api/platformCore.js",
    "src/app/pendingGeneration.js",
    "src/modelCapabilities.js",
    "backend/new-api-relay/generationprofile/google_video_models.v1.json",
  ]) assert.ok(snapshot.files.includes(googleDependency), `Google validation dependency is missing: ${googleDependency}`);
  for (const generatedPrefix of [
    ".local-backups/",
    "audit/",
    "dist/",
    "playwright-report/",
    "public/director-desk/",
    "test-results/",
  ]) {
    assert.ok(
      snapshot.files.every((path) => !path.startsWith(generatedPrefix)),
      `generated output entered the harness source identity: ${generatedPrefix}`,
    );
  }
});

test("Relay source identity environment is exact and synchronizable", async () => {
  const snapshot = await relaySourceSnapshot();
  const stale = [
    "NEW_API_RELAY_SOURCE_REVISION=1111111111111111111111111111111111111111",
    `NEW_API_RELAY_SOURCE_SNAPSHOT_SHA256=sha256:${"1".repeat(64)}`,
    "NEW_API_RELAY_SOURCE_SNAPSHOT_FILE_COUNT=1",
    "",
  ].join("\n");
  assert.equal(validateRelaySourceEnvironment(stale, snapshot).length, 3);
  const synchronized = synchronizeRelaySourceEnvironment(stale, snapshot);
  assert.deepEqual(validateRelaySourceEnvironment(synchronized, snapshot), []);
  assert.match(synchronized, new RegExp(`NEW_API_RELAY_SOURCE_REVISION=${snapshot.sha1}`));
  assert.throws(
    () => synchronizeRelaySourceEnvironment(`${stale}NEW_API_RELAY_SOURCE_REVISION=${snapshot.sha1}\n`, snapshot),
    /exactly one NEW_API_RELAY_SOURCE_REVISION/,
  );
});

test("secret-free evidence scan rejects raw credentials and permits token hashes", () => {
  assert.throws(() => assertSecretFreeEvidence({ control_token: "raw-secret-value" }), /raw sensitive field/);
  assert.throws(() => assertSecretFreeEvidence({ message: "Bearer abcdefghijklmnop" }), /bearer credential/);
  assert.doesNotThrow(() => assertSecretFreeEvidence({ old_worker_token_sha256: `sha256:${"a".repeat(64)}` }));
});

test("local-video lab source and validation programs are bound without private runtime state", async () => {
  const snapshot = await harnessSourceSnapshot();
  for (const required of [
    "backend/platform/scripts/local_video_lab.py",
    "docs/local-video-lab.md",
    "scripts/start-all-local.ps1",
    "tests/local-services-startup-contract.test.mjs",
    "scripts/local-video-lab.mjs",
    "scripts/local-video-lab-gateway.mjs",
    "scripts/local-video-lab-network-edge.mjs",
    "scripts/local-video-lab-object-store.mjs",
    "scripts/local-video-lab-object-store.test.mjs",
    "scripts/local-video-lab-verify.mjs",
    "tests/local-video-lab.test.mjs",
    "tests/local-video-lab-gateway.test.mjs",
    "tests/local-video-lab-object-store.test.mjs",
    "tests/local-video-lab-verify.test.mjs",
  ]) assert.ok(snapshot.files.includes(required), `local lab input is missing: ${required}`);

  for (const directory of ["scripts", "tests"]) {
    const entries = await readdir(new URL(`../${directory}/`, import.meta.url), { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile() || !entry.name.startsWith("local-video-lab") || !entry.name.endsWith(".mjs")) continue;
      const path = `${directory}/${entry.name}`;
      assert.ok(snapshot.files.includes(path), `new local lab program needs source coverage: ${path}`);
    }
  }
  for (const path of snapshot.files) {
    assert.ok(!path.startsWith(".tmp/") && !path.startsWith("artifacts/"),
      `runtime credentials or generated evidence must not enter the harness inputs: ${path}`);
  }
});

test("collection refresh regression binds its focused hook and client dependencies", async () => {
  const snapshot = await harnessSourceSnapshot();
  // This fixture replaces React scheduling; it is not full App/browser evidence.
  for (const required of [
    "tests/studio-collection-reset.test.mjs",
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
  ]) assert.ok(snapshot.files.includes(required), `collection regression input is missing: ${required}`);
});
