#!/usr/bin/env node
import { execFile as execFileCallback, spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { openSync, closeSync } from "node:fs";
import { mkdir, readFile, writeFile, stat } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { createLabGateway } from "./local-video-lab-gateway.mjs";

const execFile = promisify(execFileCallback);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const base = resolve(root, ".tmp/local-video-lab");
const relayBuilder = "ai-video/new-api-relay-builder:derive-seedance";
const platformImage = "ai-video-platform-api:latest";
const protectedValues = new Set();
export const LAB_TARGETS = Object.freeze({ gateway_base: "http://127.0.0.1:18480", platform_base: "http://127.0.0.1:18420", relay_base: "http://127.0.0.1:18430", frontend_base: "http://127.0.0.1:14178" });

export function makeLabManifest(mode, suffix, now = new Date()) {
  if (!["mock", "live"].includes(mode) || !/^[a-f0-9]{12}$/.test(suffix)) throw new Error("Invalid lab mode or identity");
  const labId = mode + "-" + suffix;
  return {
    schema_version: 1, kind: "ai-video-local-video-lab", lab_id: labId,
    instance_nonce: randomBytes(16).toString("hex"), provider_mode: mode,
    created_at_utc: now.toISOString().replace(/\.\d{3}Z$/, "Z"), targets: { ...LAB_TARGETS },
    isolation: { compose_project: "ai-video-lab-" + labId, platform_database: { host: "platform-db-lab", port: 5432, database: "video_lab_" + mode + "_" + suffix }, relay_service_base: "http://relay-lab:3000" },
    budget: { test_points: 2000, unit_price_points_per_second: 1, call_quota: 20, concurrency_limit: 1 },
    paid_probe_approval: null,
  };
}

export function validateLocalManifest(manifest) {
  const match = /^(mock|live)-([a-f0-9]{12})$/.exec(manifest?.lab_id || "");
  if (!match || manifest.schema_version !== 1 || manifest.kind !== "ai-video-local-video-lab" || manifest.provider_mode !== match[1]
      || !/^[a-f0-9]{32}$/.test(manifest.instance_nonce)
      || Object.entries(LAB_TARGETS).some(([key, value]) => manifest.targets?.[key] !== value)
      || Object.keys(manifest.targets || {}).length !== 4
      || manifest.isolation?.compose_project !== "ai-video-lab-" + manifest.lab_id
      || manifest.isolation?.platform_database?.host !== "platform-db-lab"
      || manifest.isolation?.platform_database?.database !== "video_lab_" + match[1] + "_" + match[2]
      || manifest.isolation?.relay_service_base !== "http://relay-lab:3000") throw new Error("Refusing an unbound or ordinary local stack");
  return manifest;
}

function safeMessage(value) {
  let message = String(value);
  for (const secret of [...protectedValues].sort((a, b) => b.length - a.length)) if (secret.length >= 8) message = message.split(secret).join("[REDACTED]");
  return message.replace(/(postgresql(?:\+psycopg)?:\/\/)[^@\s]+@/g, "$1[REDACTED]@").replace(/Bearer\s+\S+/gi, "Bearer [REDACTED]");
}

async function run(command, args, options = {}) {
  try {
    const result = await execFile(command, args, { cwd: root, windowsHide: true, maxBuffer: 24 * 1024 * 1024, timeout: 240_000, ...options });
    return result.stdout;
  } catch (error) {
    throw new Error(safeMessage(command + " failed (" + (error.code || "timeout") + "): " + String(error.stderr || error.stdout || error.message).slice(-10_000)));
  }
}

async function privateJSON(path, value, { createOnly = false } = {}) {
  const target = resolve(path);
  if (!target.startsWith(base + sep)) throw new Error("Runtime output must remain in the dedicated lab directory");
  await mkdir(dirname(target), { recursive: true, mode: 0o700 });
  await writeFile(target, JSON.stringify(value, null, 2) + "\n", { mode: 0o600, flag: createOnly ? "wx" : "w" });
}

function readSecret(environment, name) {
  const value = environment["PLATFORM_LAB_RELAY_" + name];
  if (typeof value !== "string" || value.length < 8) throw new Error("Relay preparation omitted the private " + name + " binding");
  protectedValues.add(value);
  return value;
}

export function platformLabEnvironment(manifest, privateConfig, runtimeSecrets, inputStorage = null) {
  validateLocalManifest(manifest);
  const apiKey = readSecret(privateConfig, "API_KEY"), clientId = readSecret(privateConfig, "CLIENT_ID");
  const tenantId = readSecret(privateConfig, "TENANT_ID"), admissionToken = readSecret(privateConfig, "INTERNAL_ADMISSION_TOKEN");
  const callbackSecret = readSecret(privateConfig, "CALLBACK_SIGNING_SECRET");
  const environment = {
    ENVIRONMENT: "development", AUTO_CREATE_TABLES: "false", DEVELOPMENT_HEADER_AUTH_ENABLED: "true", ENABLE_BOOTSTRAP: "true", OIDC_ENABLED: "false",
    DATABASE_URL: "postgresql+psycopg://video_lab:" + runtimeSecrets.database_password + "@platform-db-lab:5432/" + manifest.isolation.platform_database.database,
    BOOTSTRAP_TOKEN: runtimeSecrets.bootstrap_token, LOCAL_VIDEO_LAB_BOOTSTRAP_TOKEN: runtimeSecrets.bootstrap_token,
    LOCAL_VIDEO_LAB_MANIFEST: "/lab/platform-manifest.json", PYTHONPATH: "/workspace/backend/platform", PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1",
    LOCAL_VIDEO_LAB_BROWSER_ORIGIN: LAB_TARGETS.frontend_base, CORS_ORIGINS: JSON.stringify([LAB_TARGETS.frontend_base, LAB_TARGETS.gateway_base]),
    RELAY_DEFAULT_BACKEND_ID: "new-api-v1", RELAY_DEFAULT_CONTRACT_REVISION: "generations.v1",
    RELAY_BACKENDS: JSON.stringify({ "new-api-v1": { base_url: "http://relay-lab:3000", client_id: clientId, api_key: apiKey, internal_admission_token: admissionToken, contract_revision: "generations.v1" } }),
    RELAY_OPERATIONS_BASE_URL: "http://relay-lab:3000", RELAY_TENANT_ID: tenantId,
    RELAY_OPERATIONS_TOKEN: readSecret(privateConfig, "OPERATIONS_TOKEN"),
    RELAY_RECONCILIATION_APPROVAL_KEY_ID: readSecret(privateConfig, "RECONCILIATION_KEY_ID"),
    RELAY_RECONCILIATION_APPROVAL_SECRET: readSecret(privateConfig, "RECONCILIATION_SECRET"),
    RELAY_CALLBACK_PUBLIC_URL: "http://platform-lab:8000/internal/relay-callbacks",
    RELAY_CALLBACK_SIGNING_SECRETS: JSON.stringify({ "new-api-v1": callbackSecret }),
    INTERNAL_SERVICE_TOKEN: runtimeSecrets.internal_service_token,
    INPUT_ASSET_STORE: "filesystem", INPUT_ASSET_FILESYSTEM_ROOT: "/lab/assets",
    INPUT_ASSET_PUBLIC_BASE_URL: LAB_TARGETS.frontend_base, INPUT_ASSET_RELAY_BASE_URL: "https://lab-objects.local.test",
    INPUT_ASSET_SIGNING_SECRET: runtimeSecrets.input_signing_secret,
    RELAY_ALLOW_LEGACY_ARTIFACT_DOWNLOAD_RESPONSE: "false",
    RELAY_CATALOG_SYNC_ENABLED: "true", RELAY_CATALOG_SYNC_INTERVAL_SECONDS: "60",
    PUBLISHING_WORKER_ENABLED: "false", PUBLISHING_MOCK_ENABLED: "false", DOWNLOAD_GATEWAY_REGISTRATION_WORKER_ENABLED: "false",
  };
  if (manifest.provider_mode === "live") Object.assign(environment, validateLiveInputStorage(inputStorage));
  for (const value of Object.values(runtimeSecrets)) if (typeof value === "string") protectedValues.add(value);
  return environment;
}

export function validateLiveInputStorage(config) {
  const allowed = ["schema_version", "kind", "endpoint", "bucket", "access_key_id", "secret_access_key", "security_token"];
  let endpoint;
  try { endpoint = new URL(config?.endpoint); } catch { /* Rejected below without exposing secret input. */ }
  if (config?.schema_version !== 1 || config.kind !== "local-video-lab-private-input-obs"
      || Object.keys(config).some((key) => !allowed.includes(key))
      || !endpoint || endpoint.protocol !== "https:" || endpoint.username || endpoint.password || endpoint.port
      || endpoint.search || endpoint.hash || !["", "/"].includes(endpoint.pathname)
      || !/^obs\.[a-z0-9-]+\.myhuaweicloud\.(com|cn)$/.test(endpoint.hostname)
      || !/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(config.bucket || "")
      || !/^[A-Za-z0-9]{16,128}$/.test(config.access_key_id || "")
      || typeof config.secret_access_key !== "string" || config.secret_access_key.length < 32
      || config.secret_access_key.length > 4096 || /[\r\n\0]/.test(config.secret_access_key)
      || (config.security_token != null && (typeof config.security_token !== "string" || !config.security_token || /[\r\n\0]/.test(config.security_token)))) {
    throw new Error("Live reference media requires a fresh private input-storage.json with an official HTTPS OBS endpoint, dedicated bucket and valid credentials; retired repository secrets are not loaded");
  }
  protectedValues.add(config.access_key_id); protectedValues.add(config.secret_access_key);
  if (config.security_token) protectedValues.add(config.security_token);
  return { INPUT_ASSET_STORE: "huawei_obs", HUAWEI_OBS_ENDPOINT: endpoint.origin, HUAWEI_OBS_BUCKET: config.bucket,
    HUAWEI_OBS_ACCESS_KEY_ID: config.access_key_id, HUAWEI_OBS_SECRET_ACCESS_KEY: config.secret_access_key,
    ...(config.security_token ? { HUAWEI_OBS_SECURITY_TOKEN: config.security_token } : {}) };
}

export function labRelayArguments(manifest, { prepare = false, paidApproval = false } = {}) {
  const args = ["--mode", manifest.provider_mode, "--namespace", manifest.lab_id, "--state-dir", "/state", "--container-lab", "--listen", "0.0.0.0:3000", "--redis-url", "redis://redis-lab:6379/0", "--public-base-url", "http://relay-lab:3000", "--callback-url", "http://platform-lab:8000/internal/relay-callbacks/new-api-v1"];
  if (prepare) args.push("--prepare");
  if (manifest.provider_mode === "mock") args.push("--fixture-manifest", "/fixtures/manifest.json");
  else {
    args.push("--ark-key-file", "/keys/ark.key", "--minimax-key-file", "/keys/minimax.key");
    if (paidApproval) args.push("--allow-paid-probe", "--paid-probe-approval", "/lab/paid-probe-approval.json");
  }
  return args;
}

export function makeLabCompose(manifest, stateDirectory, environment, { paidApproval = false, objectConfig } = {}) {
  validateLocalManifest(manifest);
  const directory = resolve(stateDirectory);
  if (!directory.startsWith(base + sep) || directory !== resolve(base, manifest.lab_id)) throw new Error("Lab state directory does not match its manifest");
  const ip = manifest.provider_mode === "mock" ? "11.254.93" : "11.254.94";
  const bind = (source, target, readOnly = true) => ({ type: "bind", source: resolve(source), target, read_only: readOnly });
  const objectEnvironment = localObjectEnvironment(manifest, objectConfig);
  const objectHost = objectEnvironment.LAB_OBJECT_STORE_ENDPOINT_HOST;
  const objectBucket = objectEnvironment.LAB_OBJECT_STORE_BUCKET;
  const platformMounts = [
    bind(resolve(root, "backend/platform/platform_api"), "/workspace/backend/platform/platform_api"),
    bind(resolve(root, "backend/platform/migrations"), "/workspace/backend/platform/migrations"),
    bind(resolve(root, "backend/platform/alembic.ini"), "/workspace/backend/platform/alembic.ini"),
    bind(resolve(root, "backend/platform/scripts/local_video_lab.py"), "/workspace/backend/platform/scripts/local_video_lab.py"),
    bind(resolve(root, "backend/new-api-relay/generationprofile/seedance_models.v1.json"), "/workspace/backend/new-api-relay/generationprofile/seedance_models.v1.json"),
    bind(resolve(root, "backend/new-api-relay/generationprofile/minimax_h3_models.v1.json"), "/workspace/backend/new-api-relay/generationprofile/minimax_h3_models.v1.json"),
    bind(resolve(directory, "platform"), "/lab", false),
  ];
  const platformService = (command) => ({ image: platformImage, working_dir: "/workspace/backend/platform", user: "10001:10001", environment, volumes: platformMounts, command, networks: ["lab"], restart: "unless-stopped" });
  const relayMounts = [bind(resolve(base, "build/relay-local-video-lab"), "/runtime/relay-local-video-lab"), bind(resolve(directory, "relay"), "/state", false), bind(resolve(directory, "platform"), "/lab")];
  if (manifest.provider_mode === "mock") relayMounts.push(bind(resolve(directory, "fixtures"), "/fixtures"));
  else relayMounts.push(bind(resolve(base, "keys"), "/keys"));
  const api = platformService(["sh", "-ec", "python -m alembic upgrade head && python -m uvicorn scripts.local_video_lab:create_lab_app --factory --host 0.0.0.0 --port 8000"]);
  api.networks = { lab: { ipv4_address: ip + ".20" } };
  api.depends_on = { "platform-db-lab": { condition: "service_healthy" }, "relay-lab": { condition: "service_started" } };
  api.healthcheck = { test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"], interval: "3s", timeout: "4s", retries: 40 };
  const worker = (module) => ({ ...platformService(["python", "-m", module]), depends_on: { "platform-lab": { condition: "service_healthy" } } });
  return {
    name: manifest.isolation.compose_project,
    services: {
      "platform-db-lab": { image: "postgres:16-alpine", environment: { POSTGRES_USER: "video_lab", POSTGRES_PASSWORD: new URL(environment.DATABASE_URL.replace("postgresql+psycopg:", "postgresql:")).password, POSTGRES_DB: manifest.isolation.platform_database.database }, volumes: ["platform-db:/var/lib/postgresql/data"], networks: { lab: { ipv4_address: ip + ".21" } }, healthcheck: { test: ["CMD-SHELL", "pg_isready -U video_lab"], interval: "2s", timeout: "3s", retries: 40 }, restart: "unless-stopped" },
      "redis-lab": { image: "redis:7-alpine", command: ["redis-server", "--appendonly", "yes"], volumes: ["relay-redis:/data"], networks: { lab: { ipv4_address: ip + ".30" } }, healthcheck: { test: ["CMD", "redis-cli", "ping"], interval: "2s", timeout: "3s", retries: 40 }, restart: "unless-stopped" },
      "object-store-lab": { image: "node:lts-alpine", entrypoint: ["node", "/runtime/object-store.mjs"], environment: objectEnvironment,
        volumes: [bind(resolve(root, "scripts/local-video-lab-object-store.mjs"), "/runtime/object-store.mjs"), bind(resolve(directory, "relay/object-store/certs"), "/certs"), "artifact-objects:/data"],
        networks: { lab: { ipv4_address: ip + ".40", aliases: [objectHost, objectBucket + "." + objectHost] } }, restart: "unless-stopped",
        healthcheck: { test: ["CMD", "node", "-e", "require('node:https').get('https://lab-objects.local.test/health',{ca:require('node:fs').readFileSync('/certs/ca.pem')},r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"], interval: "3s", timeout: "4s", retries: 30 } },
      "relay-lab": { image: relayBuilder, entrypoint: ["/runtime/relay-local-video-lab"], command: labRelayArguments(manifest, { paidApproval }), environment: { GOTOOLCHAIN: "local", APP_ENV: "development", DEPLOYMENT_ENV: "development" }, volumes: relayMounts, networks: { lab: { ipv4_address: ip + ".10" } },
        extra_hosts: [objectHost + ":" + ip + ".40", objectBucket + "." + objectHost + ":" + ip + ".40", ...(manifest.provider_mode === "mock" ? ["video-artifacts.local.test:" + ip + ".10"] : [])],
        depends_on: { "redis-lab": { condition: "service_healthy" }, "object-store-lab": { condition: "service_healthy" } }, restart: "unless-stopped", stop_grace_period: "40s" },
      "platform-lab": api,
      "network-edge-lab": { image: "node:lts-alpine", entrypoint: ["node", "/runtime/network-edge.mjs"],
        environment: { LOCAL_VIDEO_LAB_MODE: manifest.provider_mode, LOCAL_VIDEO_LAB_ENVIRONMENT: "development" },
        volumes: [bind(resolve(root, "scripts/local-video-lab-network-edge.mjs"), "/runtime/network-edge.mjs")],
        ports: ["127.0.0.1:18420:8000", "127.0.0.1:18430:3000", "127.0.0.1:18440:443"],
        networks: { lab: { ipv4_address: ip + ".5" }, edge: {} }, restart: "unless-stopped",
        depends_on: { "relay-lab": { condition: "service_started" }, "platform-lab": { condition: "service_started" }, "object-store-lab": { condition: "service_healthy" } } },
      "platform-dispatcher-lab": worker("platform_api.dispatcher"),
      "platform-relay-sync-lab": worker("platform_api.relay_sync_worker"),
      "platform-catalog-sync-lab": worker("platform_api.relay_catalog_sync_worker"),
      "platform-timeout-lab": worker("platform_api.timeout_worker"),
    },
    networks: { lab: { internal: manifest.provider_mode === "mock", ipam: { config: [{ subnet: ip + ".0/24" }] } }, edge: { internal: false } },
    volumes: { "platform-db": {}, "relay-redis": {}, "artifact-objects": {} },
  };
}

export function localObjectEnvironment(manifest, privateConfig) {
  validateLocalManifest(manifest);
  const value = (key) => privateConfig?.["PLATFORM_LAB_OBJECT_STORE_" + key];
  if (value("MODE") !== manifest.provider_mode || value("KIND") !== "local-persistent-object-store-not-production-obs"
      || value("ENDPOINT_HOST") !== "lab-objects.local.test" || value("STATE_ID") !== privateConfig?.PLATFORM_LAB_RELAY_STATE_ID
      || !new RegExp("^video-" + manifest.provider_mode + "-[0-9a-f]{20}$").test(value("BUCKET") || "")
      || !/^LAB[A-F0-9]{20}$/.test(value("ACCESS_KEY_ID") || "") || !/^[a-f0-9]{64}$/.test(value("SECRET_ACCESS_KEY") || "")) {
    throw new Error("Missing exact, persistent, state-bound local artifact store configuration");
  }
  protectedValues.add(value("SECRET_ACCESS_KEY"));
  return Object.fromEntries(Object.entries({
    ENVIRONMENT: "development", ROOT: "/data", ENDPOINT_HOST: value("ENDPOINT_HOST"), BUCKET: value("BUCKET"), ACCESS_KEY_ID: value("ACCESS_KEY_ID"),
    SECRET_ACCESS_KEY: value("SECRET_ACCESS_KEY"), STATE_ID: value("STATE_ID"), MODE: value("MODE"),
    TLS_CERT_FILE: "/certs/server.pem", TLS_KEY_FILE: "/certs/server-key.pem", LISTEN_HOST: "0.0.0.0", LISTEN_PORT: "443",
  }).map(([key, field]) => ["LAB_OBJECT_STORE_" + key, field]));
}

async function exists(path) {
  try { return (await stat(path)).isFile(); } catch (error) { if (error.code === "ENOENT") return false; throw error; }
}

async function loadLab(mode, { create = false } = {}) {
  if (!["mock", "live"].includes(mode)) throw new Error("Use --mode mock or live");
  const activePath = resolve(base, "active-" + mode + ".json");
  if (await exists(activePath)) return validateLocalManifest(JSON.parse(await readFile(activePath, "utf8")));
  if (!create) throw new Error("No " + mode + " video lab has been prepared");
  const manifest = makeLabManifest(mode, randomBytes(6).toString("hex"));
  await privateJSON(activePath, manifest, { createOnly: true });
  return manifest;
}

async function runtimeSecretFile(directory) {
  const path = resolve(directory, "runtime-secrets.json");
  if (!(await exists(path))) {
    await privateJSON(path, Object.fromEntries(["database_password", "bootstrap_token", "internal_service_token", "input_signing_secret"].map((name) => [name, randomBytes(32).toString("hex")])), { createOnly: true });
  }
  const secrets = JSON.parse(await readFile(path, "utf8"));
  for (const name of ["database_password", "bootstrap_token", "internal_service_token", "input_signing_secret"]) {
    if (!/^[a-f0-9]{64}$/.test(secrets[name] || "")) throw new Error("Invalid private lab runtime binding: " + name);
    protectedValues.add(secrets[name]);
  }
  return secrets;
}

async function waitFor(label, check, timeout = 120_000) {
  const until = Date.now() + timeout;
  let lastError;
  do {
    try { const value = await check(); if (value) return value; } catch (error) { if (error.terminal) throw error; lastError = error; }
    await new Promise((resolveWait) => setTimeout(resolveWait, 750));
  } while (Date.now() < until);
  throw new Error(label + " did not become ready" + (lastError ? ": " + safeMessage(lastError.message) : ""));
}

async function compileLab() {
  await mkdir(resolve(base, "build"), { recursive: true });
  console.log("Building the isolated Relay entry from current source (offline)...");
  await run("docker", ["run", "--rm", "--network", "none", "-e", "GOTOOLCHAIN=local", "-e", "GOPROXY=off", "-e", "GOSUMDB=off", "--entrypoint", "go",
    "--mount", "type=bind,source=" + resolve(root, "backend/new-api-relay") + ",target=/build,readonly",
    "--mount", "type=bind,source=" + resolve(base, "build") + ",target=/out", "-w", "/build", relayBuilder,
    "build", "-tags", "relay_local_video_lab", "-o", "/out/relay-local-video-lab", "./cmd/relay-local-video-lab"], { timeout: 300_000 });
}

async function prepareFixtures(directory) {
  const fixtureDirectory = resolve(directory, "fixtures");
  await mkdir(fixtureDirectory, { recursive: true });
  const fontCandidates = process.platform === "win32"
    ? [resolve(process.env.WINDIR || "C:/Windows", "Fonts/segoeui.ttf"), resolve(root, "node_modules/@fontsource-variable/manrope/files/manrope-latin-wght-normal.woff2")]
    : ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", resolve(root, "node_modules/@fontsource-variable/manrope/files/manrope-latin-wght-normal.woff2")];
  let font;
  for (const candidate of fontCandidates) if (await exists(candidate)) { font = candidate; break; }
  if (!font) throw new Error("A local readable font is required to label mock videos honestly");
  const fixtures = [];
  const referenceImage = resolve(fixtureDirectory, "reference-image.png");
  if (!(await exists(referenceImage))) {
    await run("docker", ["run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "ffmpeg",
      "--mount", "type=bind,source=" + fixtureDirectory + ",target=/fixtures",
      "--mount", "type=bind,source=" + font + ",target=/lab-font,readonly", platformImage,
      "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x087b80:s=1024x576:r=1",
      "-vf", "drawtext=fontfile=/lab-font:text='LOCAL REFERENCE - MOCK VERIFICATION':fontcolor=white:fontsize=28:x=(w-tw)/2:y=(h-th)/2",
      "-frames:v", "1", "-update", "1", "-n", "/fixtures/reference-image.png"]);
  }
  for (const [resolution, width, height] of [["480p", 854, 480], ["720p", 1280, 720], ["768p", 1366, 768]]) {
    for (const duration of [4, 5]) {
      const name = resolution + "-" + duration + "s.mp4";
      if (!(await exists(resolve(fixtureDirectory, name)))) {
        await run("docker", ["run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "ffmpeg",
          "--mount", "type=bind,source=" + fixtureDirectory + ",target=/fixtures",
          "--mount", "type=bind,source=" + font + ",target=/lab-font,readonly", platformImage,
          "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x087b80:s=" + width + "x" + height + ":r=24",
          "-vf", "drawtext=fontfile=/lab-font:text='LOCAL VIDEO LAB - MOCK PROVIDER':fontcolor=white:fontsize=28:x=(w-tw)/2:y=(h-th)/2",
          "-t", String(duration), "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-n", "/fixtures/" + name]);
      }
      fixtures.push({ resolution, duration_seconds: duration, aspect_ratio: "16:9", path: name });
    }
  }
  await privateJSON(resolve(fixtureDirectory, "manifest.json"), { schema_version: 1, fixtures });
}

async function prepareRelay(manifest, directory) {
  const args = ["run", "--rm", "--network", "none", "--entrypoint", "/runtime/relay-local-video-lab",
    "--mount", "type=bind,source=" + resolve(base, "build/relay-local-video-lab") + ",target=/runtime/relay-local-video-lab,readonly",
    "--mount", "type=bind,source=" + resolve(directory, "relay") + ",target=/state"];
  args.push("--mount", manifest.provider_mode === "mock"
    ? "type=bind,source=" + resolve(directory, "fixtures") + ",target=/fixtures,readonly"
    : "type=bind,source=" + resolve(base, "keys") + ",target=/keys,readonly");
  await run("docker", [...args, relayBuilder, ...labRelayArguments(manifest, { prepare: true })]);
  return JSON.parse(await readFile(resolve(directory, "relay/runtime-environment.json"), "utf8"));
}

async function initializeLab(mode) {
  const manifest = await loadLab(mode, { create: true });
  const directory = resolve(base, manifest.lab_id);
  await Promise.all(["relay", "platform", "platform/assets", "logs"].map((name) => mkdir(resolve(directory, name), { recursive: true, mode: 0o700 })));
  await mkdir(resolve(base, "keys"), { recursive: true, mode: 0o700 });
  const inputTemplate = resolve(base, "keys/input-storage.template.json");
  if (!(await exists(inputTemplate))) await privateJSON(inputTemplate, { schema_version: 1, kind: "local-video-lab-private-input-obs", endpoint: "", bucket: "", access_key_id: "", secret_access_key: "" }, { createOnly: true });
  for (const provider of ["ark", "minimax"]) {
    const keyPath = resolve(base, "keys", provider + ".key");
    if (!(await exists(keyPath))) await writeFile(keyPath, "", { mode: 0o600, flag: "wx" });
    if (mode === "live") {
      const key = (await readFile(keyPath, "utf8")).trim();
      if (!key || key.length > 4096 || /[\r\n\0]/.test(key)) throw new Error("Fill the server-side provider key file first: " + keyPath + " (never paste a key into chat)");
      protectedValues.add(key);
    }
  }
  const secrets = await runtimeSecretFile(directory);
  await compileLab();
  if (mode === "mock") await prepareFixtures(directory);
  const privateConfig = await prepareRelay(manifest, directory);
  const summary = JSON.parse(await readFile(resolve(directory, "relay/summary.json"), "utf8"));
  return { manifest, directory, secrets, privateConfig, summary };
}

export function validatePaidApproval(approval, summary, now = new Date()) {
  const expectedModels = summary.models?.map((item) => item.provider_model_id).sort();
  if (summary.mode !== "live" || approval?.schema_version !== 1 || approval.state_id !== summary.state_id
      || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/.test(approval.operation_id || "")
      || !Number.isInteger(approval.max_provider_creates) || approval.max_provider_creates < expectedModels.length || approval.max_provider_creates > 100
      || JSON.stringify([...(approval.provider_model_ids || [])].sort()) !== JSON.stringify(expectedModels)
      || !Number.isFinite(Date.parse(approval.expires_at)) || Date.parse(approval.expires_at) <= now.getTime() || Date.parse(approval.expires_at) > now.getTime() + 24 * 60 * 60 * 1000
      || !Number.isFinite(Date.parse(approval.approved_at_utc)) || Date.parse(approval.approved_at_utc) > now.getTime()
      || typeof approval.actor !== "string" || !approval.actor.trim() || typeof approval.reason !== "string" || !approval.reason.trim()) {
    throw new Error("Live probes require a fresh, explicit, state/model/count/time-bound approval with actor and reason");
  }
  return approval;
}

export function bindInitialPaidApproval(manifest, approval) {
  validateLocalManifest(manifest);
  if (manifest.provider_mode !== "live") throw new Error("Mock identity cannot contain a paid approval");
  // The database identity binds the initial approval only. Renewals have their
  // own immutable journal and must not change the existing database marker.
  return manifest.paid_probe_approval ? structuredClone(manifest) : { ...manifest,
    paid_probe_approval: Object.fromEntries(["actor", "reason", "operation_id", "approved_at_utc"].map((key) => [key, approval[key]])) };
}

export function routeProbeDisposition(operation, now = new Date()) {
  if (operation?.state === "succeeded") {
    if (operation.result?.success !== true || operation.provider_submission_state !== "artifact_verified") {
      throw Object.assign(new Error("Route probe completed without a verified artifact"), { terminal: true });
    }
    const completed = Date.parse(operation.completed_at);
    if (!Number.isFinite(completed) || completed > now.getTime() + 60_000) throw Object.assign(new Error("Route probe has no trustworthy completion timestamp"), { terminal: true });
    // Refresh before the unchanged server-side 24-hour evidence gate expires.
    return now.getTime() - completed < 23 * 60 * 60 * 1000 ? "ready" : "stale";
  }
  if (operation?.provider_submission_state === "reconciliation_unknown" || ["failed", "rejected", "cancelled", "reconciliation_unknown"].includes(operation?.state)) {
    throw Object.assign(new Error("Route probe is terminal or requires reconciliation; no automatic paid retry"), { terminal: true });
  }
  return "pending";
}

export function renewedProbe(probe, { mode, approval, previousRecord, attempt }) {
  if (mode === "live" && (!approval?.operation_id || approval.operation_id === previousRecord?.approval_operation_id)) {
    throw new Error("Stale live route evidence requires a separately approved probe batch; the old batch is not extended");
  }
  const digest = createHash("sha256").update(JSON.stringify([probe.body.operation_id, mode, approval?.operation_id || "mock", attempt])).digest("hex").slice(0, 40);
  return { ...probe, request_id: "lab-probe-" + digest, body: { ...probe.body, operation_id: "lab-probe-" + digest } };
}

async function prepareLive() {
  const lab = await initializeLab("live");
  const templatePath = resolve(lab.directory, "paid-probe-approval.template.json");
  await privateJSON(templatePath, {
    schema_version: 1, state_id: lab.summary.state_id, operation_id: "operator-must-choose-operation",
    provider_model_ids: lab.summary.models.map((item) => item.provider_model_id),
    max_provider_creates: null, expires_at: null, actor: "", reason: "", approved_at_utc: null,
  });
  console.log(JSON.stringify({ status: "LIVE_PREPARED_NOT_STARTED", lab_id: lab.manifest.lab_id, approval_template: templatePath, real_provider_requests: 0 }));
}

function labHeaders(lab, extra = {}) {
  return { "Content-Type": "application/json", "X-Local-Video-Lab-ID": lab.manifest.lab_id, "X-Local-Video-Lab-Nonce": lab.manifest.instance_nonce, ...extra };
}

async function probeRelay(lab) {
  const recordsPath = resolve(lab.directory, "probe-results.json");
  let records = await exists(recordsPath) ? JSON.parse(await readFile(recordsPath, "utf8")) : [];
  for (const originalProbe of lab.summary.probe_requests) {
    let probe = originalProbe;
    let previous = [...records].reverse().find((item) => item.model === probe.body.public_model_id);
    if (previous) probe = { ...probe, request_id: previous.request_id, body: { ...probe.body, operation_id: previous.operation_id || previous.result?.operation_id || probe.body.operation_id } };
    else if (lab.manifest.provider_mode === "live") probe = renewedProbe(probe, { mode: "live", approval: lab.paidApproval, attempt: 0 });
    let ready = false;
    while (!ready) {
    if (!/^\/internal\/platform-generation-operations\/channels\/\d+\/test$/.test(probe.path) || probe.method !== "POST") throw new Error("Unexpected provider probe target");
    const headers = { "Content-Type": "application/json", "X-Relay-Operations-Token": readSecret(lab.privateConfig, "OPERATIONS_TOKEN"), "X-Request-ID": probe.request_id };
    let record = records.find((item) => item.request_id === probe.request_id);
    if (!record) {
      record = { request_id: probe.request_id, operation_id: probe.body.operation_id, model: probe.body.public_model_id, state: "SUBMISSION_RESERVED", started_at: new Date().toISOString(),
        ...(lab.paidApproval ? { approval_operation_id: lab.paidApproval.operation_id } : {}) };
      records.push(record);
      // Record before the POST; a lost response is reconciled via GET only.
      await privateJSON(recordsPath, records);
      let response;
      try {
        response = await fetch(LAB_TARGETS.relay_base + probe.path, { method: "POST", headers, body: JSON.stringify(probe.body), redirect: "error", signal: AbortSignal.timeout(60_000) });
        record.http_status = response.status;
        record.result = await response.json();
        record.state = "SUBMITTED";
        await privateJSON(recordsPath, records);
      } catch { throw new Error("Probe outcome unknown for " + record.model + "; reservation retained, no POST retry"); }
      if (![200, 202].includes(response.status)) throw new Error("Relay rejected route probe for " + record.model + " (HTTP " + response.status + ")");
    }
    const path = "/internal/platform-generation-operations/channels/" + probe.channel_id + "/operations/" + encodeURIComponent(probe.body.operation_id) + "?tenant_id=" + encodeURIComponent(probe.body.tenant_id);
    const disposition = await waitFor("Route probe " + record.model, async () => {
      const response = await fetch(LAB_TARGETS.relay_base + path, { headers, redirect: "error", signal: AbortSignal.timeout(10_000) });
      if (!response.ok) throw new Error("Operation lookup HTTP " + response.status);
      const operation = await response.json();
      if (operation.operation_id !== probe.body.operation_id || operation.channel_id !== probe.channel_id || operation.tenant_id !== probe.body.tenant_id) {
        throw Object.assign(new Error("Route probe operation identity mismatch"), { terminal: true });
      }
      record.result = operation;
      const state = routeProbeDisposition(operation);
      if (state !== "pending") {
        record.state = state === "ready" ? "PASS" : "STALE"; record.finished_at = operation.completed_at; await privateJSON(recordsPath, records); return state;
      }
      return false;
    }, lab.manifest.provider_mode === "mock" ? 120_000 : 900_000);
    if (disposition === "stale") {
      probe = renewedProbe(originalProbe, { mode: lab.manifest.provider_mode, approval: lab.paidApproval, previousRecord: record, attempt: records.filter((item) => item.model === record.model).length });
      continue;
    }
    ready = true;
    console.log("Route evidence PASS: " + record.model);
    }
  }
}

async function runPlatformLab(lab, action) {
  const python = process.platform === "win32" ? resolve(root, ".venv-ci/Scripts/python.exe") : resolve(root, ".venv-ci/bin/python");
  const result = await run(python, ["-B", resolve(root, "backend/platform/scripts/local_video_lab.py"), "--manifest", resolve(lab.directory, "platform/platform-manifest.json"), action], {
    env: { ...process.env, LOCAL_VIDEO_LAB_BOOTSTRAP_TOKEN: lab.secrets.bootstrap_token, PYTHONPATH: resolve(root, "backend/platform"), PYTHONDONTWRITEBYTECODE: "1" },
    timeout: 240_000,
  });
  return selectVerificationRequests(JSON.parse(result));
}

async function validatePlatformEnvironment(environment) {
  const python = process.platform === "win32" ? resolve(root, ".venv-ci/Scripts/python.exe") : resolve(root, ".venv-ci/bin/python");
  const code = [
    "from platform_api.config import Settings", "import sys", "try:", "    Settings(_env_file=None)", "except Exception as error:",
    "    if hasattr(error, 'errors'):", "        for item in error.errors(include_input=False, include_url=False):",
    "            print(str(item.get('loc')) + ': ' + str(item.get('msg')))", "    else:", "        print(type(error).__name__ + ': settings rejected')", "    sys.exit(1)",
  ].join("\n");
  await run(python, ["-B", "-c", code], { env: {
    PATH: process.env.PATH, SYSTEMROOT: process.env.SYSTEMROOT, ...environment, PYTHONPATH: resolve(root, "backend/platform"),
  } });
}

export function selectVerificationRequests(receipt) {
  // Use a documented small real MP4 fixture for each provider while retaining
  // every capability in discovery. Never manufacture an unsupported option.
  return { ...receipt, models: receipt.models.map((model) => {
    const limits = model.effective_capabilities?.modes?.text_to_video?.limits;
    const resolution = ["720p", "768p", "480p"].find((value) => limits?.resolutions?.includes(value));
    const duration = [4, 5].find((value) => limits?.duration_seconds?.includes(value));
    if (!resolution || !duration || !limits?.aspect_ratios?.includes("16:9")) throw new Error("No reviewed local verification fixture matches " + model.slug);
    return { ...model, request_payload: { ...model.request_payload, resolution, duration_seconds: duration, aspect_ratio: "16:9" } };
  }) };
}

function composeArguments(manifest, directory) {
  return ["compose", "--env-file", resolve(root, ".env"), "--project-name", manifest.isolation.compose_project, "-f", resolve(directory, "compose.json")];
}

async function stopOwnedGateway() {
  let identity;
  try {
    const response = await fetch(LAB_TARGETS.frontend_base + "/__local-video-lab__/identity", { redirect: "error", signal: AbortSignal.timeout(1500) });
    if (!response.ok) throw new Error("Unrecognized listener on the dedicated frontend port");
    identity = await response.json();
  } catch (error) {
    if (error.cause?.code === "ECONNREFUSED") return;
    throw new Error("Cannot identify the dedicated frontend listener; it has not been stopped");
  }
  const manifest = await loadLab(identity.provider_mode);
  if (identity.kind !== manifest.kind || identity.lab_id !== manifest.lab_id) throw new Error("Frontend listener belongs to another application; refusing to stop it");
  const secrets = await runtimeSecretFile(resolve(base, manifest.lab_id));
  const response = await fetch(LAB_TARGETS.frontend_base + "/__local-video-lab__/shutdown", {
    method: "POST", redirect: "error", headers: { "X-Local-Video-Lab-Control": secrets.bootstrap_token }, signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error("Could not stop the identified lab gateway");
  await waitFor("Lab gateway shutdown", async () => {
    try { await fetch(LAB_TARGETS.frontend_base + "/__local-video-lab__/identity", { signal: AbortSignal.timeout(500) }); return false; }
    catch (error) { return error.cause?.code === "ECONNREFUSED"; }
  }, 10_000);
}

async function stopOwnedStacks() {
  await stopOwnedGateway();
  for (const mode of ["mock", "live"]) {
    if (!(await exists(resolve(base, "active-" + mode + ".json")))) continue;
    const manifest = await loadLab(mode);
    const directory = resolve(base, manifest.lab_id);
    if (!(await exists(resolve(directory, "compose.json")))) continue;
    const compose = JSON.parse(await readFile(resolve(directory, "compose.json"), "utf8"));
    if (compose.name !== manifest.isolation.compose_project || Object.values(compose.services || {}).some((service) => service.privileged || service.network_mode === "host")) {
      throw new Error("Existing lab Compose identity is invalid; no services were stopped");
    }
    await run("docker", [...composeArguments(manifest, directory), "stop"], { timeout: 90_000 });
  }
}

export function labFrontendDirectory(manifest) {
  validateLocalManifest(manifest);
  return resolve(base, manifest.lab_id, "frontend");
}

export function labFrontendBuildEnvironment(parent = process.env) {
  // The unchanged production UI deliberately rejects HTTP artifact links.
  // Only this isolated development build may consume loopback media; never
  // weaken production URL validation or compile demo personas into the lab.
  return { ...parent, NODE_ENV: "development", VITE_PLATFORM_API_URL: LAB_TARGETS.frontend_base,
    VITE_ENABLE_DEMO: "false", VITE_DEMO_MODE: "false", VITE_ENABLE_DEMO_MODE: "false" };
}

async function buildFrontend(manifest) {
  console.log("Building the original frontend for the isolated Platform...");
  await run(process.execPath, [resolve(root, "node_modules/vite/bin/vite.js"), "build", "--mode", "development", "--outDir", labFrontendDirectory(manifest)], {
    env: labFrontendBuildEnvironment(), timeout: 240_000,
  });
}

async function startGateway(lab) {
  const out = openSync(resolve(lab.directory, "logs/gateway.stdout.log"), "a", 0o600);
  const err = openSync(resolve(lab.directory, "logs/gateway.stderr.log"), "a", 0o600);
  try {
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), "serve", "--mode", lab.manifest.provider_mode], {
      cwd: root, windowsHide: true, detached: true, stdio: ["ignore", out, err],
    });
    child.unref();
    await privateJSON(resolve(lab.directory, "gateway-process.json"), { pid: child.pid, lab_id: lab.manifest.lab_id, started_at: new Date().toISOString() });
  } finally { closeSync(out); closeSync(err); }
  await waitFor("Frontend lab gateway", async () => {
    const response = await fetch(LAB_TARGETS.frontend_base + "/__local-video-lab__/identity", { signal: AbortSignal.timeout(1500) });
    if (!response.ok) return false;
    const identity = await response.json();
    if (identity.lab_id !== lab.manifest.lab_id) throw new Error("Unexpected gateway identity");
    return true;
  }, 30_000);
}

async function startLab(mode, { approvalPath = "", skipFrontend = false } = {}) {
  // Only this repository's established services:start:local dispatcher invokes
  // this operation. No ordinary canary/project services or volumes are touched.
  if (process.env.LOCAL_VIDEO_LAB_START_ENTRY !== "services:start:local") throw new Error("Use npm run services:start:local -- -VideoLab");
  if (mode === "mock" && approvalPath) throw new Error("Mock mode rejects paid approval");
  if (mode === "live" && !approvalPath) throw new Error("Prepare live keys/config first; an explicit bounded paid-probe approval is required to activate real providers");
  await stopOwnedStacks();
  const lab = await initializeLab(mode);
  if (mode === "live") {
    const approval = validatePaidApproval(JSON.parse(await readFile(resolve(approvalPath), "utf8")), lab.summary);
    const historyPath = resolve(lab.directory, "platform/approval-history", createHash("sha256").update(approval.operation_id).digest("hex") + ".json");
    if (await exists(historyPath)) {
      if (JSON.stringify(JSON.parse(await readFile(historyPath, "utf8"))) !== JSON.stringify(approval)) throw new Error("Existing paid approval identity cannot be expanded or rewritten; use a new explicitly approved batch");
    } else await privateJSON(historyPath, approval, { createOnly: true });
    await privateJSON(resolve(lab.directory, "platform/paid-probe-approval.json"), approval);
    lab.manifest = bindInitialPaidApproval(lab.manifest, approval);
    lab.paidApproval = approval;
    await privateJSON(resolve(base, "active-live.json"), lab.manifest);
  }
  await privateJSON(resolve(lab.directory, "platform/platform-manifest.json"), lab.manifest);
  if (!(await exists(resolve(root, "scripts/local-video-lab-object-store.mjs")))) throw new Error("Local artifact store implementation is missing; no services were created");
  const inputPath = resolve(base, "keys/input-storage.json");
  const inputStorage = mode === "live" && await exists(inputPath) ? JSON.parse(await readFile(inputPath, "utf8")) : null;
  const environment = platformLabEnvironment(lab.manifest, lab.privateConfig, lab.secrets, inputStorage);
  await validatePlatformEnvironment(environment);
  await privateJSON(resolve(lab.directory, "compose.json"), makeLabCompose(lab.manifest, lab.directory, environment, { paidApproval: mode === "live", objectConfig: lab.privateConfig }));
  const compose = composeArguments(lab.manifest, lab.directory);
  await run("docker", [...compose, "config", "--quiet"]);
  console.log("Starting the isolated Relay, Platform, PostgreSQL, Redis and workers...");
  await run("docker", [...compose, "up", "-d"], { timeout: 180_000 });
  await waitFor("Relay worker readiness", async () => {
    const response = await fetch(LAB_TARGETS.relay_base + "/lab/health", { signal: AbortSignal.timeout(2000) });
    if (!response.ok) return false;
    const value = await response.json();
    if (value.state_id !== lab.summary.state_id || value.mode !== mode) throw new Error("Unexpected Relay state");
    return true;
  });
  await waitFor("Platform lab identity", async () => {
    const response = await fetch(LAB_TARGETS.platform_base + "/internal/local-video-lab/identity", {
      headers: labHeaders(lab, { "X-Bootstrap-Token": lab.secrets.bootstrap_token }), signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) return false;
    const value = await response.json();
    return value.lab_id === lab.manifest.lab_id && value.storage_bound === true;
  });
  await probeRelay(lab);
  console.log("Applying the actual Platform review, grants and isolated test-point budget...");
  let receipt, submissionReady = true;
  const receiptPath = resolve(lab.directory, "platform-receipt.json");
  try { receipt = await runPlatformLab(lab, "--apply"); }
  catch (error) {
    // A resumed task may legitimately occupy the single concurrency slot.
    // Keep the UI available to observe/recover it, without forging discovery
    // readiness or replaying a generation. The frontend fetches current grants.
    if (!error.message.includes("Lab text-to-video discovery is not ready") || !(await exists(receiptPath))) throw error;
    receipt = JSON.parse(await readFile(receiptPath, "utf8"));
    if (receipt.lab_id !== lab.manifest.lab_id || receipt.provider_mode !== mode) throw error;
    submissionReady = false;
    console.log("The existing test workspace is available; current generation readiness is blocked. No task was resubmitted.");
  }
  if (receipt.lab_id !== lab.manifest.lab_id || receipt.models.length !== lab.summary.models.length) throw new Error("Platform discovery did not match the Relay model set");
  if (submissionReady) await privateJSON(receiptPath, receipt);
  if (!skipFrontend) { await buildFrontend(lab.manifest); await startGateway(lab); }
  console.log(JSON.stringify({ status: submissionReady ? (mode === "mock" ? "MOCK_STACK_READY" : "LIVE_BOUNDED_TEST_STACK_READY") : "STACK_RUNNING_READINESS_BLOCKED", submission_ready: submissionReady, lab_id: lab.manifest.lab_id, models: receipt.models.map((item) => item.slug),
    frontend: skipFrontend ? null : LAB_TARGETS.frontend_base + "/__local-video-lab__", key_directory: resolve(base, "keys"), production_acceptance: false }, null, 2));
}

async function serveLab(mode) {
  const manifest = await loadLab(mode);
  const directory = resolve(base, manifest.lab_id);
  const receipt = JSON.parse(await readFile(resolve(directory, "platform-receipt.json"), "utf8"));
  const secrets = await runtimeSecretFile(directory);
  const privateConfig = JSON.parse(await readFile(resolve(directory, "relay/runtime-environment.json"), "utf8"));
  localObjectEnvironment(manifest, privateConfig);
  const objectStore = {
    environment: "development", mode, stateId: privateConfig.PLATFORM_LAB_OBJECT_STORE_STATE_ID,
    endpointHost: privateConfig.PLATFORM_LAB_OBJECT_STORE_ENDPOINT_HOST, bucket: privateConfig.PLATFORM_LAB_OBJECT_STORE_BUCKET,
    accessKeyId: privateConfig.PLATFORM_LAB_OBJECT_STORE_ACCESS_KEY_ID,
    secretAccessKey: privateConfig.PLATFORM_LAB_OBJECT_STORE_SECRET_ACCESS_KEY,
    ca: await readFile(resolve(directory, "relay/object-store/certs/ca.pem")),
  };
  const servers = [];
  const onShutdown = () => {
    for (const server of servers) { server.close(); server.closeIdleConnections(); }
  };
  for (const port of [14178, 18480]) {
    const server = createLabGateway({ manifest, receipt, bootstrapToken: secrets.bootstrap_token, clientRoot: labFrontendDirectory(manifest), onShutdown, objectStore });
    servers.push(server);
    await new Promise((resolveListen, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolveListen); }).catch((error) => { onShutdown(); throw error; });
  }
  console.log("Isolated " + mode + " frontend ready: " + LAB_TARGETS.frontend_base);
}

async function verifyLab(mode) {
  if (mode !== "mock") throw new Error("Automatic end-to-end verification is mock-only; real generations need a separately approved budget");
  const manifest = await loadLab(mode), directory = resolve(base, manifest.lab_id);
  const secrets = await runtimeSecretFile(directory);
  const receipt = await runPlatformLab({ manifest, directory, secrets }, "--verify");
  const { verifyLocalVideoLab } = await import("./local-video-lab-verify.mjs");
  const privateConfig = JSON.parse(await readFile(resolve(directory, "relay/runtime-environment.json"), "utf8"));
  const readRelayEvidence = async () => {
    const response = await fetch(LAB_TARGETS.relay_base + "/lab/evidence", {
      headers: { "X-Relay-Operations-Token": readSecret(privateConfig, "OPERATIONS_TOKEN") }, redirect: "error", signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("Relay evidence unavailable (HTTP " + response.status + ")");
    const evidence = await response.json();
    if (evidence.mode !== mode || evidence.state_id !== privateConfig.PLATFORM_LAB_RELAY_STATE_ID) throw new Error("Relay evidence belongs to a different state");
    return evidence;
  };
  const result = await verifyLocalVideoLab({ manifest, receipt, bootstrapToken: secrets.bootstrap_token, readRelayEvidence,
    referenceFixtures: { imagePath: resolve(directory, "fixtures/reference-image.png"), videoPath: resolve(directory, "fixtures/768p-4s.mp4") },
    referenceCoverage: "all-modes" });
  await privateJSON(resolve(directory, "verification-history", new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-") + ".json"), result, { createOnly: true });
  await privateJSON(resolve(directory, "verification.json"), result);
  console.log(JSON.stringify(result, null, 2));
  if (result.status !== "passed") throw new Error("Local video chain verification did not pass; see the recorded stage/code. No automatic provider retry was issued.");
}

async function statusLab(mode) {
  const manifest = await loadLab(mode), directory = resolve(base, manifest.lab_id);
  const receipt = await exists(resolve(directory, "platform-receipt.json")) ? JSON.parse(await readFile(resolve(directory, "platform-receipt.json"), "utf8")) : null;
  const health = {};
  for (const [name, url] of [["relay", LAB_TARGETS.relay_base + "/lab/health"], ["platform", LAB_TARGETS.platform_base + "/health"], ["frontend", LAB_TARGETS.frontend_base + "/__local-video-lab__/identity"]]) {
    try { const response = await fetch(url, { redirect: "error", signal: AbortSignal.timeout(2000) }); health[name] = { http_status: response.status, ...(response.ok ? await response.json() : {}) }; }
    catch { health[name] = { reachable: false }; }
  }
  console.log(JSON.stringify({ lab_id: manifest.lab_id, provider_mode: mode, health, configured_models: receipt?.models.map((item) => item.slug) || [], production_acceptance: false }, null, 2));
}

export function parseLabArguments(args) {
  const command = args[0] || "status";
  if (!["start", "prepare-live", "serve", "verify", "status", "stop"].includes(command)) throw new Error("Unknown local video lab command");
  const options = { command, mode: command === "prepare-live" ? "live" : "mock", approvalPath: "", skipFrontend: false };
  for (let index = 1; index < args.length; index++) {
    if (args[index] === "--mode" && ["mock", "live"].includes(args[index + 1])) options.mode = args[++index];
    else if (args[index] === "--paid-probe-approval" && args[index + 1] && !args[index + 1].startsWith("--")) options.approvalPath = args[++index];
    else if (args[index] === "--skip-frontend") options.skipFrontend = true;
    else throw new Error("Unknown or invalid lab option");
  }
  if (command === "prepare-live" && options.mode !== "live") throw new Error("Live preparation cannot use mock state");
  if (command !== "start" && (options.approvalPath || options.skipFrontend)) throw new Error("Startup-only option on a read-only/preparation command");
  return options;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const options = parseLabArguments(process.argv.slice(2));
  const actions = { start: () => startLab(options.mode, options), "prepare-live": prepareLive, serve: () => serveLab(options.mode), verify: () => verifyLab(options.mode), status: () => statusLab(options.mode), stop: stopOwnedStacks };
  try { await actions[options.command](); } catch (error) { console.error(safeMessage(error.message)); process.exitCode = 1; }
}
