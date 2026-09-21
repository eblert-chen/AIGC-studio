import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const MANIFEST_KIND = "ai-video-local-smoke-disposable";
const MAX_MANIFEST_LIFETIME_MS = 4 * 60 * 60 * 1000;
const MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;
const PROTECTED_DEFAULT_PORTS = Object.freeze({
  gateway_base: "8180",
  platform_base: "8200",
  relay_base: "8300",
});
const TARGET_KEYS = Object.freeze([
  "gateway_base",
  "platform_base",
  "relay_base",
]);

function fail(message) {
  throw new Error(message);
}

function requireRecord(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail(`${label} must be an object`);
  }
  return value;
}

function requireExactKeys(value, expectedKeys, label) {
  const actualKeys = Object.keys(value).sort();
  const expected = [...expectedKeys].sort();
  if (
    actualKeys.length !== expected.length ||
    actualKeys.some((key, index) => key !== expected[index])
  ) {
    fail(`${label} must contain exactly: ${expected.join(", ")}`);
  }
}

function normalizeEnvironmentId(value) {
  if (
    typeof value !== "string" ||
    !/^[a-z0-9][a-z0-9-]{7,31}$/.test(value)
  ) {
    fail(
      "environment_id must be 8-32 lowercase letters, digits, or hyphens",
    );
  }
  return value;
}

function normalizeLoopbackBase(value, label) {
  if (typeof value !== "string" || value.trim() !== value || value === "") {
    fail(`${label} must be a non-empty canonical URL`);
  }

  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    fail(`${label} must be an absolute URL`);
  }
  if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
    fail(`${label} must use HTTP or HTTPS`);
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    fail(`${label} must not contain credentials, query parameters, or fragments`);
  }
  if (parsed.pathname !== "/") {
    fail(`${label} must be an origin without a path`);
  }
  if (!parsed.port) {
    fail(`${label} must use an explicit non-default port`);
  }

  const hostname = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (hostname !== "127.0.0.1" && hostname !== "::1") {
    fail(`${label} must use an exact loopback IP address`);
  }
  return parsed.origin;
}

function normalizeTargets(targets, label) {
  const record = requireRecord(targets, label);
  requireExactKeys(record, TARGET_KEYS, label);
  const normalized = {};
  for (const key of TARGET_KEYS) {
    normalized[key] = normalizeLoopbackBase(record[key], `${label}.${key}`);
    const port = new URL(normalized[key]).port;
    if (port === PROTECTED_DEFAULT_PORTS[key]) {
      fail(
        `${label}.${key} uses protected long-lived development port ${port}`,
      );
    }
  }
  if (new Set(Object.values(normalized)).size !== TARGET_KEYS.length) {
    fail(`${label} must bind three distinct service origins`);
  }
  return normalized;
}

function normalizeTimestamp(value, label) {
  if (typeof value !== "string" || value.trim() !== value) {
    fail(`${label} must be an ISO-8601 UTC timestamp`);
  }
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp) || !value.endsWith("Z")) {
    fail(`${label} must be an ISO-8601 UTC timestamp`);
  }
  return timestamp;
}

function normalizeNow(now) {
  const timestamp = now instanceof Date ? now.getTime() : Number(now);
  if (!Number.isFinite(timestamp)) fail("now must be a valid timestamp");
  return timestamp;
}

export function validateDisposableSmokeManifest(
  rawManifest,
  requestedTargets,
  now = Date.now(),
) {
  const manifest = requireRecord(rawManifest, "manifest");
  requireExactKeys(
    manifest,
    [
      "schema_version",
      "kind",
      "environment_id",
      "created_at_utc",
      "expires_at_utc",
      "targets",
      "isolation",
    ],
    "manifest",
  );
  if (manifest.schema_version !== 1 || manifest.kind !== MANIFEST_KIND) {
    fail("manifest schema or kind is not supported");
  }

  const environmentId = normalizeEnvironmentId(manifest.environment_id);
  const targets = normalizeTargets(manifest.targets, "manifest.targets");
  const requested = normalizeTargets(requestedTargets, "requested_targets");
  for (const key of TARGET_KEYS) {
    if (targets[key] !== requested[key]) {
      fail(`${key} does not match the disposable manifest`);
    }
  }

  const isolation = requireRecord(manifest.isolation, "manifest.isolation");
  requireExactKeys(
    isolation,
    ["kind", "compose_project", "cleanup_mode", "remove_volumes"],
    "manifest.isolation",
  );
  const expectedProject = `ai-video-smoke-${environmentId}`;
  if (
    isolation.kind !== "docker-compose-project" ||
    isolation.compose_project !== expectedProject ||
    isolation.cleanup_mode !== "docker-compose-down-volumes" ||
    isolation.remove_volumes !== true
  ) {
    fail(
      "manifest isolation must bind the run to its exact disposable Compose project and volume-removing cleanup",
    );
  }

  const nowMs = normalizeNow(now);
  const createdAt = normalizeTimestamp(
    manifest.created_at_utc,
    "manifest.created_at_utc",
  );
  const expiresAt = normalizeTimestamp(
    manifest.expires_at_utc,
    "manifest.expires_at_utc",
  );
  if (createdAt > nowMs + MAX_CLOCK_SKEW_MS) {
    fail("manifest creation time is in the future");
  }
  if (expiresAt <= nowMs) fail("disposable manifest has expired");
  if (expiresAt <= createdAt || expiresAt - createdAt > MAX_MANIFEST_LIFETIME_MS) {
    fail("disposable manifest lifetime must be positive and no longer than four hours");
  }

  return {
    schema_version: 1,
    kind: MANIFEST_KIND,
    environment_id: environmentId,
    created_at_utc: new Date(createdAt).toISOString(),
    expires_at_utc: new Date(expiresAt).toISOString(),
    targets,
    isolation: {
      kind: "docker-compose-project",
      compose_project: expectedProject,
      cleanup_mode: "docker-compose-down-volumes",
      remove_volumes: true,
    },
  };
}

export function issueDisposableSmokeManifest(
  { environmentId, targets },
  now = Date.now(),
) {
  const nowMs = normalizeNow(now);
  const normalizedEnvironmentId = normalizeEnvironmentId(environmentId);
  const normalizedTargets = normalizeTargets(targets, "requested_targets");
  const manifest = {
    schema_version: 1,
    kind: MANIFEST_KIND,
    environment_id: normalizedEnvironmentId,
    created_at_utc: new Date(nowMs).toISOString(),
    expires_at_utc: new Date(nowMs + 2 * 60 * 60 * 1000).toISOString(),
    targets: normalizedTargets,
    isolation: {
      kind: "docker-compose-project",
      compose_project: `ai-video-smoke-${normalizedEnvironmentId}`,
      cleanup_mode: "docker-compose-down-volumes",
      remove_volumes: true,
    },
  };
  return validateDisposableSmokeManifest(manifest, normalizedTargets, nowMs);
}

function parseFlags(args, allowed) {
  const parsed = {};
  for (let index = 0; index < args.length; index += 2) {
    const name = args[index];
    const value = args[index + 1];
    if (!name?.startsWith("--") || value === undefined) {
      fail("arguments must be provided as --name value pairs");
    }
    const key = name.slice(2);
    if (!allowed.has(key) || Object.hasOwn(parsed, key)) {
      fail(`unsupported or duplicate argument: ${name}`);
    }
    parsed[key] = value;
  }
  for (const key of allowed) {
    if (!Object.hasOwn(parsed, key)) fail(`missing required argument: --${key}`);
  }
  return parsed;
}

function commandTargets(flags) {
  return {
    gateway_base: flags["gateway-base"],
    platform_base: flags["platform-base"],
    relay_base: flags["relay-base"],
  };
}

function runCli() {
  const command = process.argv[2];
  if (command === "issue") {
    const flags = parseFlags(
      process.argv.slice(3),
      new Set([
        "environment-id",
        "gateway-base",
        "platform-base",
        "relay-base",
      ]),
    );
    const manifest = issueDisposableSmokeManifest({
      environmentId: flags["environment-id"],
      targets: commandTargets(flags),
    });
    process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`);
    return;
  }
  if (command === "validate") {
    const flags = parseFlags(
      process.argv.slice(3),
      new Set([
        "manifest",
        "gateway-base",
        "platform-base",
        "relay-base",
      ]),
    );
    const rawText = readFileSync(resolve(flags.manifest), "utf8").replace(
      /^\uFEFF/,
      "",
    );
    const manifest = validateDisposableSmokeManifest(
      JSON.parse(rawText),
      commandTargets(flags),
    );
    process.stdout.write(`${JSON.stringify(manifest)}\n`);
    return;
  }
  fail("expected command: issue or validate");
}

const invokedPath = process.argv[1]
  ? pathToFileURL(resolve(process.argv[1])).href
  : "";
if (invokedPath === import.meta.url) {
  try {
    runCli();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(
      `smoke disposable environment validation failed: ${message}\n`,
    );
    process.exitCode = 1;
  }
}
