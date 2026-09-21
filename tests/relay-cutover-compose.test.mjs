import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { platformSourceSnapshot } from "../scripts/run-cross-service-cost-acceptance.mjs";
import {
  relaySourceSnapshot,
  validateRelaySourceEnvironment,
} from "../scripts/relay-fault-source-snapshot.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const platformRoot = fileURLToPath(new URL("../backend/platform/", import.meta.url));
const localEnvironmentExample = readFileSync(new URL("../.env.example", import.meta.url), "utf8");
const platformSourceRevision = environmentExampleValue("PLATFORM_SOURCE_REVISION");
const platformSourceSnapshotSha256 = environmentExampleValue("PLATFORM_SOURCE_SNAPSHOT_SHA256");
const platformImageRootFiles = Object.freeze([
  ".dockerignore",
  "Dockerfile",
  "alembic.ini",
  "requirements.txt",
  "scripts/platform_source_snapshot.py",
]);
const platformBuildServices = [
  "platform-input-asset-init",
  "platform-api",
  "platform-dispatcher",
  "platform-relay-sync",
  "platform-relay-catalog-sync",
  "platform-timeout-worker",
  "platform-publishing-worker",
  "platform-download-gateway-registration-worker",
];
const legacyServices = new Set([
  "relay-artifact-init",
  "relay-api",
  "relay-outbox",
  "relay-worker",
  "relay-transfer-worker",
  "relay-provider-sync",
  "relay-provider-monitor",
  "relay-callback-worker",
]);

function environmentExampleValue(name) {
  const match = localEnvironmentExample.match(new RegExp(`^${name}=([^\\r\\n]*)$`, "m"));
  assert.ok(match, `.env.example is missing ${name}`);
  return match[1];
}

function platformImageSourceFiles(directory) {
  const output = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === "__pycache__") continue;
    const absolute = join(directory, entry.name);
    assert.ok(!entry.isSymbolicLink(), `Platform image source contains a symbolic link: ${absolute}`);
    if (entry.isDirectory()) output.push(...platformImageSourceFiles(absolute));
    else if (entry.isFile() && !/\.py[co]$/i.test(entry.name)) output.push(absolute);
  }
  return output;
}

function platformImageSourceSnapshot() {
  const files = [
    ...platformImageRootFiles.map((name) => join(platformRoot, ...name.split("/"))),
    ...platformImageSourceFiles(join(platformRoot, "platform_api")),
    ...platformImageSourceFiles(join(platformRoot, "migrations")),
  ].sort((left, right) => {
    const leftName = relative(platformRoot, left).split(sep).join("/");
    const rightName = relative(platformRoot, right).split(sep).join("/");
    return leftName < rightName ? -1 : leftName > rightName ? 1 : 0;
  });
  const digest = createHash("sha256");
  for (const file of files) {
    const contents = readFileSync(file);
    const name = relative(platformRoot, file).split(sep).join("/");
    digest.update(JSON.stringify([
      name,
      contents.byteLength,
      createHash("sha256").update(contents).digest("hex"),
    ]));
    digest.update("\n");
  }
  return {
    sha256: `sha256:${digest.digest("hex")}`,
    fileCount: files.length,
    files: files.map((file) => relative(platformRoot, file).split(sep).join("/")),
  };
}

function composeConfigArgs(args) {
  const hasEnvironmentFile = args.some((argument) =>
    argument === "--env-file" || argument.startsWith("--env-file="));
  const environmentFiles = hasEnvironmentFile ? [] : ["--env-file", ".env.example"];
  return ["compose", ...environmentFiles, ...args, "config", "--format", "json"];
}

function render(args, environment = {}) {
  const output = execFileSync("docker", composeConfigArgs(args), {
    cwd: root,
    encoding: "utf8",
    env: {
      ...process.env,
      COMPOSE_PROFILES: "",
      PLATFORM_SOURCE_REVISION: platformSourceRevision,
      PLATFORM_SOURCE_SNAPSHOT_SHA256: platformSourceSnapshotSha256,
      ...environment,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  return JSON.parse(output);
}

function ordinary(extra = []) {
  return render(["-f", "docker-compose.yml", ...extra]);
}

function protectedEnvironment(environment, extra = []) {
  return render([
    "--env-file",
    "deploy/relay-secure.env.example",
    "--env-file",
    `deploy/relay-${environment}.env.example`,
    "-f",
    "docker-compose.yml",
    "-f",
    "deploy/compose.relay.secure.yml",
    "-f",
    `deploy/compose.relay.${environment}.yml`,
    ...extra,
  ]);
}

function assertSingleRelayDataPlane(rendered, label) {
  const services = rendered.services ?? {};
  assert.ok(services["relay-new-api"], `${label} is missing relay-new-api`);
  assert.ok(services["relay-download-edge"], `${label} is missing relay-download-edge`);
  for (const legacy of legacyServices) {
    assert.ok(!(legacy in services), `${label} unexpectedly renders legacy ${legacy}`);
  }
  for (const [name, service] of Object.entries(services)) {
    assert.doesNotMatch(
      service.build?.context ?? "",
      /(?:^|[\\/])backend[\\/]relay$/,
      `${label}/${name} must not build the Python Relay`,
    );
  }
}

function assertLocalPlatformBuilds(rendered, label, installObs) {
  for (const name of platformBuildServices) {
    const build = rendered.services?.[name]?.build;
    assert.ok(build, `${label}/${name} must keep its local Platform build`);
    assert.match(
      build.context,
      /(?:^|[\\/])backend[\\/]platform$/,
      `${label}/${name} must build the Platform context`,
    );
    assert.equal(
      build.args?.PLATFORM_SOURCE_REVISION,
      platformSourceRevision,
      `${label}/${name} lost the frozen source revision`,
    );
    assert.equal(
      build.args?.PLATFORM_SOURCE_SNAPSHOT_SHA256,
      platformSourceSnapshotSha256,
      `${label}/${name} lost the frozen source snapshot digest`,
    );
    assert.equal(
      build.args?.INSTALL_OBS,
      installObs,
      `${label}/${name} rendered the wrong OBS build capability`,
    );
  }
}

function assertPlatformUsesOnlyNewApiBackend(platform, label) {
  const environment = platform.environment ?? {};
  assert.equal(environment.RELAY_DEFAULT_BACKEND_ID, "new-api-v1");
  assert.equal(environment.RELAY_DEFAULT_CONTRACT_REVISION, "generations.v1");
  const backends = JSON.parse(environment.RELAY_BACKENDS);
  assert.deepEqual(Object.keys(backends), ["new-api-v1"]);
  assert.equal(backends["new-api-v1"].base_url, "http://relay-new-api:3000");
  assert.equal(backends["new-api-v1"].client_id, "customer-platform");
  assert.ok(backends["new-api-v1"].api_key, `${label} is missing the new-api credential`);
  assert.equal(backends["new-api-v1"].contract_revision, "generations.v1");
  const callbackSecrets = JSON.parse(environment.RELAY_CALLBACK_SIGNING_SECRETS);
  assert.deepEqual(Object.keys(callbackSecrets), ["new-api-v1"]);
  assert.ok(callbackSecrets["new-api-v1"], `${label} is missing the new-api callback secret`);
  for (const legacyScalar of [
    "RELAY_BASE_URL",
    "RELAY_CLIENT_ID",
    "RELAY_API_KEY",
    "RELAY_CALLBACK_SIGNING_SECRET",
  ]) {
    assert.ok(!(legacyScalar in environment), `${label} exposes retired ${legacyScalar}`);
  }
}

test("Compose contract rendering uses explicit public fixtures, never an implicit local .env", () => {
  assert.deepEqual(composeConfigArgs(["-f", "docker-compose.yml"]), [
    "compose", "--env-file", ".env.example", "-f", "docker-compose.yml",
    "config", "--format", "json",
  ]);
  const protectedArgs = [
    "--env-file", "deploy/relay-secure.env.example",
    "--env-file", "deploy/relay-staging.env.example",
    "-f", "docker-compose.yml",
  ];
  assert.deepEqual(composeConfigArgs(protectedArgs), [
    "compose", ...protectedArgs, "config", "--format", "json",
  ]);
  const inlineArgs = ["--env-file=.env.example", "-f", "docker-compose.yml"];
  assert.deepEqual(composeConfigArgs(inlineArgs), [
    "compose", ...inlineArgs, "config", "--format", "json",
  ]);
});

test("ordinary Compose Relay provenance matches the deterministic source bytes", async () => {
  const snapshot = await relaySourceSnapshot();
  assert.deepEqual(validateRelaySourceEnvironment(localEnvironmentExample, snapshot), []);
  const rendered = ordinary();
  const build = rendered.services?.["relay-new-api"]?.build;
  assert.equal(build?.args?.RELAY_BUILD_SOURCE_REVISION, snapshot.sha1);
  assert.equal(build?.args?.RELAY_BUILD_SOURCE_SNAPSHOT_SHA256, snapshot.sha256);
  assert.equal(build?.args?.RELAY_BUILD_SOURCE_SNAPSHOT_FILE_COUNT, String(snapshot.file_count));
});

test("internal pilot renders the same single new-api data plane", () => {
  const rendered = render(
    ["-f", "docker-compose.yml", "-f", "deploy/compose.internal-pilot.yml"],
    {
      NEW_API_RELAY_HUAWEI_OBS_ENDPOINT: "https://obs.example.test",
      NEW_API_RELAY_HUAWEI_OBS_BUCKET: "relay-test",
      NEW_API_RELAY_HUAWEI_OBS_ACCESS_KEY_ID: "test-access",
      NEW_API_RELAY_HUAWEI_OBS_SECRET_ACCESS_KEY: "test-secret",
      NEW_API_RELAY_DOWNLOAD_EDGE_ALLOWED_OBS_HOSTS: "relay-test.obs.example.test",
      PLATFORM_HUAWEI_OBS_ENDPOINT: "https://obs.example.test",
      PLATFORM_HUAWEI_OBS_BUCKET: "platform-test",
      PLATFORM_HUAWEI_OBS_ACCESS_KEY_ID: "test-access",
      PLATFORM_HUAWEI_OBS_SECRET_ACCESS_KEY: "test-secret",
    },
  );
  assertSingleRelayDataPlane(rendered, "internal-pilot");
  assertPlatformUsesOnlyNewApiBackend(rendered.services["platform-api"], "internal-pilot/platform-api");
  assert.equal(rendered.services["relay-new-api"].environment.RELAY_ARTIFACT_STORE, "huawei_obs");
});

test("every local Platform build keeps frozen provenance across the OBS pilot merge", async () => {
  const fullSource = await platformSourceSnapshot();
  const imageSource = platformImageSourceSnapshot();
  assert.equal(
    platformSourceRevision,
    fullSource.sha1,
    ".env.example Platform revision must match the deterministic full source snapshot",
  );
  assert.equal(
    platformSourceSnapshotSha256,
    imageSource.sha256,
    ".env.example Platform digest must match the exact Docker runtime/build inputs",
  );
  assert.ok(imageSource.fileCount > 100, "Platform image source snapshot is unexpectedly incomplete");
  for (const required of [
    "platform_api/billing_worker.py",
    "platform_api/database_privileges_v14.py",
    "platform_api/database_privileges_behavior_v14.py",
    "migrations/versions/0049_payment_finance_closure.py",
    "scripts/platform_source_snapshot.py",
  ]) assert.ok(imageSource.files.includes(required), `Platform image snapshot is missing ${required}`);
  const base = ordinary(["--profile", "publishing-disabled-until-real-adapter"]);
  assertLocalPlatformBuilds(base, "ordinary", "false");

  const pilot = render(
    [
      "-f",
      "docker-compose.yml",
      "-f",
      "deploy/compose.internal-pilot.yml",
      "--profile",
      "publishing-disabled-until-real-adapter",
    ],
    {
      NEW_API_RELAY_HUAWEI_OBS_ENDPOINT: "https://obs.example.test",
      NEW_API_RELAY_HUAWEI_OBS_BUCKET: "relay-test",
      NEW_API_RELAY_HUAWEI_OBS_ACCESS_KEY_ID: "test-access",
      NEW_API_RELAY_HUAWEI_OBS_SECRET_ACCESS_KEY: "test-secret",
      NEW_API_RELAY_DOWNLOAD_EDGE_ALLOWED_OBS_HOSTS: "relay-test.obs.example.test",
      PLATFORM_HUAWEI_OBS_ENDPOINT: "https://obs.example.test",
      PLATFORM_HUAWEI_OBS_BUCKET: "platform-test",
      PLATFORM_HUAWEI_OBS_ACCESS_KEY_ID: "test-access",
      PLATFORM_HUAWEI_OBS_SECRET_ACCESS_KEY: "test-secret",
    },
  );
  assertLocalPlatformBuilds(pilot, "internal-pilot", "true");
});

test("ordinary Compose renders one active new-api data plane without a profile", () => {
  const rendered = ordinary();
  assertSingleRelayDataPlane(rendered, "ordinary");

  const platform = rendered.services["platform-api"];
  assertPlatformUsesOnlyNewApiBackend(platform, "ordinary/platform-api");
  assert.equal(platform.environment.RELAY_OPERATIONS_BASE_URL, "http://relay-new-api:3000");
  assert.equal(platform.environment.RELAY_ALLOW_LEGACY_ARTIFACT_DOWNLOAD_RESPONSE, "false");
  assert.equal(platform.depends_on["relay-new-api"].condition, "service_healthy");
  assert.equal(rendered.services["relay-new-api"].environment.RELAY_COMPAT_WORKER_ENABLED, "true");
  assert.ok(
    rendered.services["relay-new-api"].environment.RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN,
    "ordinary active workers require a private admission token",
  );
  const relayOperations = JSON.parse(
    rendered.services["relay-new-api"].environment.RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON,
  );
  assert.deepEqual(relayOperations.map(({ tenant_id }) => tenant_id), [platform.environment.RELAY_TENANT_ID]);
  assert.equal(
    relayOperations[0].token_sha256,
    createHash("sha256").update(platform.environment.RELAY_OPERATIONS_TOKEN).digest("hex"),
    "the Platform operations token must match new-api's tenant-bound digest",
  );
  const approvalKeys = JSON.parse(
    rendered.services["relay-new-api"].environment.RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON,
  );
  assert.deepEqual(approvalKeys, [
    {
      tenant_id: platform.environment.RELAY_TENANT_ID,
      key_id: platform.environment.RELAY_RECONCILIATION_APPROVAL_KEY_ID,
      secret: platform.environment.RELAY_RECONCILIATION_APPROVAL_SECRET,
    },
  ]);

  const registration = rendered.services["platform-download-gateway-registration-worker"];
  assert.equal(registration.environment.DOWNLOAD_GATEWAY_REGISTRATION_WORKER_ENABLED, "true");
  assert.equal(
    registration.environment.DOWNLOAD_GATEWAY_REGISTRATION_URL,
    "http://relay-download-edge:8080/internal/v1/download-tickets",
  );
  assert.equal(registration.depends_on["relay-download-edge"].condition, "service_healthy");

  assert.ok(rendered.networks["relay-new-api-data"].internal);
  assert.deepEqual(
    new Set(Object.keys(rendered.services["relay-new-api-postgres"].networks)),
    new Set(["relay-new-api-data"]),
  );
  assert.deepEqual(
    new Set(Object.keys(rendered.services["relay-new-api-redis"].networks)),
    new Set(["relay-new-api-data"]),
  );
});

test("the checked-in local environment example preserves the same active endpoints", () => {
  const rendered = ordinary(["--env-file", ".env.example"]);
  assertSingleRelayDataPlane(rendered, "ordinary-env-example");
  const platform = rendered.services["platform-api"];
  assertPlatformUsesOnlyNewApiBackend(platform, "ordinary-env-example/platform-api");
  assert.equal(platform.environment.RELAY_OPERATIONS_BASE_URL, "http://relay-new-api:3000");
  assert.equal(
    platform.environment.DOWNLOAD_GATEWAY_REGISTRATION_URL,
    "http://relay-download-edge:8080/internal/v1/download-tickets",
  );
  assert.equal(rendered.services["relay-new-api"].environment.RELAY_COMPAT_WORKER_ENABLED, "true");
});

test("a legacy profile name cannot resurrect a second Relay", () => {
  for (const [label, rendered] of [
    ["ordinary+legacy-profile", ordinary(["--profile", "python-relay-rollback"])],
    [
      "staging+legacy-profile",
      protectedEnvironment("staging", ["--profile", "python-relay-rollback"]),
    ],
  ]) {
    assertSingleRelayDataPlane(rendered, label);
  }
});

test("principal rotation keeps the same active new-api plane and adds only maintenance jobs", () => {
  const rendered = protectedEnvironment("staging", [
    "-f",
    "deploy/compose.relay.principal-rotation.yml",
    "--profile",
    "relay-principal-rotation",
  ]);
  assertSingleRelayDataPlane(rendered, "principal-rotation");
  assert.ok(rendered.services["relay-new-api-principal-rotation-volume-init"]);
  assert.ok(rendered.services["relay-new-api-principal-rotation-secret-isolation"]);
  assert.ok(rendered.services["relay-new-api-service-principal-rotation"]);
  assert.deepEqual(
    new Set(Object.keys(rendered.services["relay-new-api-service-principal-rotation"].networks)),
    new Set(["relay-new-api-managed-state"]),
  );
});

for (const environment of ["staging", "production"]) {
  test(`${environment} Compose renders only protected new-api services and managed state`, () => {
    const rendered = protectedEnvironment(environment);
    assertSingleRelayDataPlane(rendered, environment);

    for (const localState of ["postgres", "redis", "relay-new-api-postgres", "relay-new-api-redis"]) {
      assert.ok(!(localState in rendered.services), `${environment} must not render ${localState}`);
    }

    const relay = rendered.services["relay-new-api"];
    assert.equal(relay.labels["com.ai-video.relay.deployment-environment"], environment);
    assert.ok(!relay.build, `${environment} must use an immutable image, not a build context`);
    assert.match(relay.image, /@sha256:[a-f0-9]{64}$/);
    assert.deepEqual(
      new Set(Object.keys(relay.networks)),
      new Set(["relay-new-api-edge", "relay-new-api-data", "relay-new-api-managed-state"]),
    );
    assert.equal(relay.environment.RELAY_REDIS_TLS_CA_FILE, "/run/secrets/relay-redis-tls-ca.pem");
    assert.equal(relay.environment.RELAY_COMPAT_WORKER_ENABLED, "true");

    const edge = rendered.services["relay-download-edge"];
    assert.ok(!("RELAY_REDIS_TLS_CA_FILE" in edge.environment));
    assert.ok(!("RELAY_SERVICE_PRINCIPALS_FILE" in edge.environment));
    assert.ok(!("RELAY_API_RUNTIME_SECRETS_FILE" in edge.environment));

    const platform = rendered.services["platform-api"];
    assert.equal(platform.environment.RELAY_DEFAULT_BACKEND_ID, "new-api-v1");
    assert.equal(platform.environment.RELAY_ALLOW_LEGACY_ARTIFACT_DOWNLOAD_RESPONSE, "false");
    assert.equal(platform.depends_on["relay-new-api"].condition, "service_healthy");
    assert.ok(platform.networks["relay-new-api-edge"]);
    assert.ok(platform.networks["relay-new-api-managed-state"]);

    const relaySync = rendered.services["platform-relay-sync"];
    assert.equal(relaySync.environment.RELAY_DEFAULT_BACKEND_ID, "new-api-v1");
    assert.equal(relaySync.depends_on["relay-new-api"].condition, "service_healthy");
    assert.equal(relaySync.depends_on["platform-api"].condition, "service_healthy");

    const catalogSync = rendered.services["platform-relay-catalog-sync"];
    assert.equal(catalogSync.environment.RELAY_DEFAULT_BACKEND_ID, "new-api-v1");
    assert.equal(catalogSync.environment.RELAY_CATALOG_SYNC_ENABLED, "true");
    assert.equal(catalogSync.depends_on["relay-new-api"].condition, "service_healthy");
    assert.equal(catalogSync.depends_on["platform-api"].condition, "service_healthy");
  });
}
