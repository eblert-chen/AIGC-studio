#!/usr/bin/env node

/**
 * Credential-late provider model onboarding.
 *
 * plan  - offline, secret-free and non-mutating.
 * check - verifies UI-managed credentials inside Relay and reads live local
 *         control-plane state; it never calls a provider generation endpoint.
 * apply - resumable and fail-closed. It stages exact channels, derives the
 *         complete development route inventory from compiled Relay facts,
 *         performs bounded paid route tests, publishes only cost-qualified
 *         models, distributes points-v2 grants, and reconciles one customer
 *         task. Protected route signatures and production cutover remain a
 *         separate release workflow. A dry run is never acceptance.
 */

import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";

export const WORKSPACE = resolve(import.meta.dirname, "..");
export const DEFAULT_PRIVATE_ENV = "deploy/secrets/provider-model-onboarding.runtime.env";
export const DEFAULT_INTENT = "deploy/secrets/provider-model-onboarding.intent.json";
export const DEFAULT_FX_EVIDENCE = "deploy/secrets/provider-model-onboarding.fx.json";
export const DEFAULT_BOOTSTRAP_RECEIPT =
  "deploy/secrets/provider-model-onboarding.channels.json";
export const DEFAULT_CURRENT_ROUTES =
  "deploy/secrets/provider-model-onboarding.current-routes.json";
export const DEFAULT_PLANNED_ROUTES =
  "deploy/secrets/provider-model-onboarding.planned-routes.json";
export const DEFAULT_RATE_SETS =
  "deploy/secrets/provider-model-onboarding.rate-sets.json";
export const DEFAULT_APPLY_RECEIPT =
  "deploy/secrets/provider-model-onboarding.apply-receipt.json";
export const PAID_CANARY_RUNTIME_ENV =
  "deploy/secrets/paid-canary.runtime.env";
export const ECB_DAILY_XML_URL =
  "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml";
export const APPLY_CONFIRMATION =
  "I_ACKNOWLEDGE_PROVIDER_CHARGES_AND_CUSTOMER_DISTRIBUTION";

const LOCAL_COMPOSE_ENV_FILES = Object.freeze([
  ".env",
  "deploy/secrets/huawei-obs.runtime.env",
  PAID_CANARY_RUNTIME_ENV,
  "deploy/secrets/platform-canary.runtime.env",
]);
const LOCAL_COMPOSE_FILES = Object.freeze([
  "docker-compose.yml",
  "deploy/compose.internal-pilot.yml",
]);

const SHA256_PATTERN = /^(?:sha256:)?[0-9a-f]{64}$/;
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const CHANNEL_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._ -]{2,79}$/;
const SAFE_IDEMPOTENCY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$/;
const PRIVATE_ROOT = resolve(WORKSPACE, "deploy/secrets");
const RELAY_ONBOARDING_CREDENTIAL_CONTAINER_PATH =
  "/run/provider-onboarding/credential.json";
const RELAY_ONBOARDING_CREDENTIAL_ENV = "RELAY_PROVIDER_ONBOARDING_FILE";
const RELAY_ONBOARDING_ROUTES_CONTAINER_PATH =
  "/run/provider-onboarding/signed-routes.json";
const MANAGED_PROVIDER_IDS = new Set([
  "google-gemini-api",
  "minimax",
  "volcengine-ark",
]);

const PROVIDERS = Object.freeze({
  google: Object.freeze({
    channelType: 24,
    managedProvider: "google-gemini-api",
    credential: "MODEL_ONBOARDING_GOOGLE_API_KEY",
    allowedBaseUrls: Object.freeze([
      "https://generativelanguage.googleapis.com",
    ]),
  }),
  minimax: Object.freeze({
    channelType: 35,
    managedProvider: "minimax",
    credential: "MODEL_ONBOARDING_MINIMAX_API_KEY",
    allowedBaseUrls: Object.freeze([
      "https://api.minimax.io",
      "https://api.minimax.cn",
    ]),
  }),
  volcengine: Object.freeze({
    channelType: 45,
    managedProvider: "volcengine-ark",
    credential: "MODEL_ONBOARDING_ARK_API_KEY",
    allowedBaseUrls: Object.freeze([
      "https://ark.cn-beijing.volces.com/api/v3",
    ]),
  }),
});

const MODEL_MANIFESTS = Object.freeze([
  ["volcengine", "backend/new-api-relay/generationprofile/seedance_models.v1.json"],
  ["minimax", "backend/new-api-relay/generationprofile/minimax_h3_models.v1.json"],
  ["google", "backend/new-api-relay/generationprofile/google_video_models.v1.json"],
]);

const HISTORICAL_REVIEWED_MODELS = Object.freeze([
  Object.freeze({
    provider: "volcengine",
    public_model_id: "seedream-5",
    provider_model_id: "doubao-seedream-5-0-260128",
    display_name: "Seedream 5",
  }),
]);

export class OnboardingError extends Error {
  constructor(code, message, { exitCode = 1, details = undefined } = {}) {
    super(message);
    this.name = "OnboardingError";
    this.code = code;
    this.exitCode = exitCode;
    this.details = details;
  }
}

function fail(code, message, options) {
  throw new OnboardingError(code, message, options);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function asWorkspacePath(value, fallback) {
  const selected = value || fallback;
  return isAbsolute(selected) ? resolve(selected) : resolve(WORKSPACE, selected);
}

export function assertIgnoredPrivatePath(value, label) {
  const absolute = asWorkspacePath(value);
  const rel = relative(PRIVATE_ROOT, absolute);
  if (rel.startsWith("..") || isAbsolute(rel) || rel === "") {
    fail(
      "PRIVATE_PATH_REQUIRED",
      `${label} must be a file below the ignored deploy/secrets directory`,
    );
  }
  return absolute;
}

function assertStablePrivateInput(value, label, maximumBytes) {
  const absolute = assertIgnoredPrivatePath(value, label);
  let stat;
  try {
    stat = lstatSync(absolute);
  } catch {
    fail("PRIVATE_INPUT_UNAVAILABLE", `${label} is unavailable`);
  }
  if (
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    stat.size < 1 ||
    stat.size > maximumBytes
  ) {
    fail("PRIVATE_INPUT_INVALID", `${label} must be a bounded regular file`);
  }
  return absolute;
}

function assertComposeInput(value) {
  const absolute = isAbsolute(value) ? resolve(value) : resolve(WORKSPACE, value);
  const rel = relative(WORKSPACE, absolute);
  let stat;
  try {
    stat = lstatSync(absolute);
  } catch {
    fail("COMPOSE_INPUT_UNAVAILABLE", "Relay onboarding Compose file is unavailable");
  }
  if (
    rel.startsWith("..") ||
    isAbsolute(rel) ||
    rel === "" ||
    !/\.ya?ml$/i.test(absolute) ||
    !stat.isFile() ||
    stat.isSymbolicLink()
  ) {
    fail("COMPOSE_INPUT_INVALID", "Relay onboarding Compose file is invalid");
  }
  return absolute;
}

function assertComposeEnvInput(value) {
  const absolute = isAbsolute(value) ? resolve(value) : resolve(WORKSPACE, value);
  const rel = relative(WORKSPACE, absolute);
  let stat;
  try {
    stat = lstatSync(absolute);
  } catch {
    fail("COMPOSE_ENV_INPUT_UNAVAILABLE", "Relay onboarding Compose environment file is unavailable");
  }
  if (
    rel.startsWith("..") ||
    isAbsolute(rel) ||
    rel === "" ||
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    stat.size > 8 * 1024 * 1024
  ) {
    fail("COMPOSE_ENV_INPUT_INVALID", "Relay onboarding Compose environment file is invalid");
  }
  return absolute;
}

/**
 * Resolve the local Platform owner from the running Platform image. The
 * configured allowlist contains OIDC subjects while development header auth
 * requires a local users.id, so copying the allowlist value into a request is
 * both incorrect and unsafe. This read-only command owns that translation and
 * refuses protected runtimes server-side.
 */
export function runLocalPlatformOwnerResolver({
  composeEnvFiles = LOCAL_COMPOSE_ENV_FILES,
  composeFiles = LOCAL_COMPOSE_FILES,
  composeService = "platform-api",
  dockerExecutable = process.platform === "win32" ? "docker.exe" : "docker",
  runner = spawnSync,
} = {}) {
  if (!ID_PATTERN.test(composeService || "")) {
    fail("PLATFORM_CONTROL_SERVICE_INVALID", "Platform control service is invalid");
  }
  const envFiles = composeEnvFiles.map(assertComposeEnvInput);
  const files = composeFiles.map(assertComposeInput);
  const args = ["compose", "--project-directory", WORKSPACE];
  for (const file of envFiles) args.push("--env-file", file);
  for (const file of files) args.push("--file", file);
  args.push(
    "exec",
    "-T",
    composeService,
    "python",
    "-m",
    "platform_api.provider_model_onboarding_control",
    "resolve-owner",
  );
  let result;
  try {
    result = runner(dockerExecutable, args, {
      cwd: WORKSPACE,
      encoding: "utf8",
      windowsHide: true,
      shell: false,
      maxBuffer: 1024 * 1024,
      env: { ...process.env, COMPOSE_ANSI: "never" },
    });
  } catch {
    fail("LOCAL_OWNER_RESOLUTION_FAILED", "the Platform owner resolver could not run");
  }
  if (result?.status === 3 && String(result.stderr || "").includes('"code":"LOCAL_OWNER_NOT_FOUND"')) {
    return null;
  }
  if (result?.error || result?.status !== 0) {
    fail(
      "LOCAL_OWNER_RESOLUTION_FAILED",
      "the Platform owner resolver rejected the local runtime",
      { details: { exit_status: Number.isInteger(result?.status) ? result.status : null } },
    );
  }
  let receipt;
  try {
    receipt = JSON.parse(String(result.stdout || ""));
  } catch {
    fail("LOCAL_OWNER_RESOLUTION_INVALID", "the Platform owner resolver returned invalid JSON");
  }
  if (
    receipt?.schema_version !== 1 ||
    receipt?.kind !== "provider_model_onboarding_local_owner" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      receipt?.user_id || "",
    ) ||
    !["configured_oidc_owner", "development_first_admin"].includes(receipt?.source)
  ) {
    fail("LOCAL_OWNER_RESOLUTION_INVALID", "the Platform owner resolver receipt is invalid");
  }
  return receipt;
}

/**
 * Execute the protected Relay credential-late materializer from the exact
 * Relay image. By default, the one-shot command loads a write-only credential
 * directly from New API's encrypted vault. The compatibility credential-file
 * path is explicit break-glass only. Credentials never enter argv, Compose
 * environment, a host-side generated JSON file, or this function's result.
 * The route inventory is still sealed and mounted read-only. In
 * staging/production the Go command verifies it against the image's compiled
 * trust anchor; exact non-protected development accepts only an unsigned,
 * code-reviewed inventory and never calls it provider acceptance.
 *
 * The apply workflow calls this only after its own intent/cost gates have
 * selected an exact provider binding. Keeping it as an exported boundary also
 * makes the no-secret argv contract independently testable.
 */
export function runFormalRelayProviderOnboarding({
  environment,
  phase = "finalize",
  managedProvider,
  credentialFile,
  signedRoutesFile,
  currentRoutesFile,
  replaceAllRoutes = false,
  createdAt,
  createdBy,
  reason,
  rpmLimit,
  activeTaskLimit,
  composeEnvFiles = LOCAL_COMPOSE_ENV_FILES,
  composeFiles = LOCAL_COMPOSE_FILES,
  composeService = "relay-new-api",
  dockerExecutable = process.platform === "win32" ? "docker.exe" : "docker",
  runner = spawnSync,
} = {}) {
  if (
    environment !== "development" &&
    environment !== "staging" &&
    environment !== "production"
  ) {
    fail(
      "RELAY_ONBOARDING_ENVIRONMENT_INVALID",
      "formal Relay onboarding requires development, staging, or production",
    );
  }
  if (!["check-credential", "stage", "plan-routes", "finalize"].includes(phase)) {
    fail(
      "RELAY_ONBOARDING_PHASE_INVALID",
      "formal Relay onboarding phase must be check-credential, stage, plan-routes, or finalize",
    );
  }
  if (phase === "plan-routes" && environment !== "development") {
    fail(
      "RELAY_ONBOARDING_PLAN_ENVIRONMENT_INVALID",
      "route planning is restricted to the non-protected development runtime",
    );
  }
  if (!ID_PATTERN.test(composeService || "")) {
    fail("RELAY_ONBOARDING_SERVICE_INVALID", "Relay onboarding service is invalid");
  }
  const usesManagedCredential = managedProvider !== undefined && managedProvider !== null;
  if (
    usesManagedCredential &&
    (typeof managedProvider !== "string" || !MANAGED_PROVIDER_IDS.has(managedProvider))
  ) {
    fail(
      "RELAY_ONBOARDING_MANAGED_PROVIDER_INVALID",
      "managedProvider must be google-gemini-api, minimax, or volcengine-ark",
    );
  }
  if (usesManagedCredential === Boolean(credentialFile)) {
    fail(
      "RELAY_ONBOARDING_CREDENTIAL_SOURCE_INVALID",
      "exactly one of managedProvider or credentialFile is required",
    );
  }
  const credentialPath = credentialFile
    ? assertStablePrivateInput(
        credentialFile,
        "provider onboarding credential",
        128 * 1024,
      )
    : null;
  const routesInput = phase === "finalize" ? signedRoutesFile : currentRoutesFile;
  const routesPath =
    phase === "finalize" || (phase === "plan-routes" && !replaceAllRoutes)
      ? assertStablePrivateInput(
          routesInput,
          phase === "finalize" ? "signed route inventory" : "current route inventory",
          32 * 1024 * 1024,
        )
      : null;
  if (
    ["check-credential", "stage"].includes(phase) &&
    (signedRoutesFile || currentRoutesFile || replaceAllRoutes)
  ) {
    fail(
      "RELAY_ONBOARDING_STAGE_ROUTES_FORBIDDEN",
      "credential check and stage must not receive route inventory options",
    );
  }
  if (phase === "finalize" && currentRoutesFile) {
    fail("RELAY_ONBOARDING_FINALIZE_ROUTES_INVALID", "finalize accepts only signedRoutesFile");
  }
  if (
    phase === "plan-routes" &&
    (signedRoutesFile || Boolean(currentRoutesFile) === Boolean(replaceAllRoutes))
  ) {
    fail(
      "RELAY_ONBOARDING_PLAN_ROUTES_INVALID",
      "plan-routes requires exactly one of currentRoutesFile or replaceAllRoutes",
    );
  }
  if (replaceAllRoutes && environment !== "development") {
    fail(
      "RELAY_ONBOARDING_REPLACE_FORBIDDEN",
      "complete route replacement is restricted to development",
    );
  }
  if (phase === "plan-routes") {
    if (
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(createdAt || "") ||
      typeof createdBy !== "string" ||
      createdBy.length < 3 ||
      createdBy.length > 120 ||
      typeof reason !== "string" ||
      reason.length < 3 ||
      reason.length > 500 ||
      !Number.isSafeInteger(rpmLimit) ||
      rpmLimit < 1 ||
      rpmLimit > 1_000_000 ||
      !Number.isSafeInteger(activeTaskLimit) ||
      activeTaskLimit < 1 ||
      activeTaskLimit > 10_000 ||
      /[\0\r\n\t]/.test(`${createdBy}${reason}`)
    ) {
      fail("RELAY_ONBOARDING_PLAN_AUDIT_INVALID", "route plan audit and limits are invalid");
    }
  }
  if (!Array.isArray(composeFiles) || composeFiles.length < 1) {
    fail("COMPOSE_INPUT_INVALID", "at least one Relay Compose file is required");
  }
  if (!Array.isArray(composeEnvFiles) || composeEnvFiles.length < 1) {
    fail("COMPOSE_ENV_INPUT_INVALID", "at least one Relay Compose environment file is required");
  }
  const envFiles = composeEnvFiles.map(assertComposeEnvInput);
  const files = composeFiles.map(assertComposeInput);
  const composeArgs = ["compose", "--project-directory", WORKSPACE];
  for (const file of envFiles) composeArgs.push("--env-file", file);
  for (const file of files) composeArgs.push("--file", file);
  const childEnvironment = { ...process.env, COMPOSE_ANSI: "never" };
  delete childEnvironment[RELAY_ONBOARDING_CREDENTIAL_ENV];
  for (const provider of Object.values(PROVIDERS)) {
    delete childEnvironment[provider.credential];
  }
  const runOptions = {
    cwd: WORKSPACE,
    encoding: "utf8",
    windowsHide: true,
    shell: false,
    maxBuffer: 1024 * 1024,
    env: childEnvironment,
  };
  const invoke = (args, code, message) => {
    let result;
    try {
      result = runner(dockerExecutable, args, runOptions);
    } catch {
      fail(code, message);
    }
    if (result?.error || result?.status !== 0) {
      fail(code, message, {
        details: {
          exit_status: Number.isInteger(result?.status) ? result.status : null,
        },
      });
    }
    return result;
  };

  // Docker Desktop bind mounts cannot truthfully satisfy the Relay protected
  // reader's uid/mode contract. Seal the route inventory, and the credential
  // only in explicit legacy-file mode, into a fresh named volume using the
  // exact Relay image. The volume is removed on every exit path.
  const imageLookup = invoke(
    [...composeArgs, "images", "--quiet", composeService],
    "RELAY_PROVIDER_IMAGE_UNAVAILABLE",
    "the exact Relay service image is unavailable; start the local stack first",
  );
  const imageID = String(imageLookup.stdout || "").trim();
  if (!/^(?:sha256:)?[0-9a-f]{64}$/.test(imageID)) {
    fail(
      "RELAY_PROVIDER_IMAGE_UNAVAILABLE",
      "the exact Relay service image identity is unavailable; start the local stack first",
    );
  }
  const volumeName = `ai-video-provider-onboarding-${randomUUID().toLowerCase()}`;
  let volumeCleanupRequired = false;
  let receipt;
  let workflowError;
  try {
    // Treat volume creation as an unknown-outcome mutation until an explicit
    // remove succeeds. This prevents a daemon disconnect after creation from
    // leaving provider material behind unnoticed.
    volumeCleanupRequired = true;
    const created = invoke(
      [
        "volume",
        "create",
        "--label",
        "com.ai-video.purpose=provider-onboarding-ephemeral",
        volumeName,
      ],
      "RELAY_PROVIDER_CREDENTIAL_SEAL_FAILED",
      "the protected Relay onboarding volume could not be created",
    );
    if (String(created.stdout || "").trim() !== volumeName) {
      fail(
        "RELAY_PROVIDER_CREDENTIAL_SEAL_FAILED",
        "the protected Relay onboarding volume identity is ambiguous",
      );
    }
    const sealArgs = [
      "run",
      "--rm",
      "--network",
      "none",
      "--read-only",
      "--user",
      "0:0",
    ];
    if (credentialPath) {
      sealArgs.push(
        "--mount",
        `type=bind,source=${credentialPath},target=/input/credential.json,readonly`,
      );
    }
    if (routesPath) {
      sealArgs.push(
        "--mount",
        `type=bind,source=${routesPath},target=/input/signed-routes.json,readonly`,
      );
    }
    sealArgs.push(
      "--mount",
      `type=volume,source=${volumeName},target=/sealed`,
      "--entrypoint",
      "/bin/sh",
      imageID,
      "-ec",
      [
        "set -eu",
        "umask 077",
        ...(credentialPath
          ? ["install -o 10001 -g 10001 -m 0400 /input/credential.json /sealed/credential.json"]
          : []),
        ...(routesPath
          ? ["install -o 10001 -g 10001 -m 0400 /input/signed-routes.json /sealed/signed-routes.json"]
          : []),
        "sync",
      ].join("\n"),
    );
    invoke(
      sealArgs,
      "RELAY_PROVIDER_CREDENTIAL_SEAL_FAILED",
      "the protected Relay onboarding inputs could not be sealed",
    );

    const args = [
      ...composeArgs,
      "run",
      "--rm",
      "--no-deps",
      "--no-build",
      "--pull",
      "never",
      "--quiet-pull",
      "--entrypoint",
      "/relay-provider-onboarding",
      "--volume",
      `${volumeName}:/run/provider-onboarding:ro`,
      composeService,
      "--environment",
      environment,
      "--phase",
      phase,
    ];
    if (credentialPath) {
      args.splice(
        args.indexOf("--volume"),
        0,
        "--env",
        `${RELAY_ONBOARDING_CREDENTIAL_ENV}=${RELAY_ONBOARDING_CREDENTIAL_CONTAINER_PATH}`,
      );
    } else {
      args.splice(
        args.indexOf("--volume"),
        0,
        "--env",
        `${RELAY_ONBOARDING_CREDENTIAL_ENV}=`,
      );
      args.push("--managed-provider", managedProvider);
    }
    if (phase === "finalize") {
      args.push("--signed-routes", RELAY_ONBOARDING_ROUTES_CONTAINER_PATH);
      if (replaceAllRoutes) args.push("--replace-all-routes");
    } else if (phase === "plan-routes") {
      if (routesPath) {
        args.push("--current-routes", RELAY_ONBOARDING_ROUTES_CONTAINER_PATH);
      } else {
        args.push("--replace-all-routes");
      }
      args.push(
        "--created-at",
        createdAt,
        "--created-by",
        createdBy,
        "--reason",
        reason,
        "--rpm-limit",
        String(rpmLimit),
        "--active-task-limit",
        String(activeTaskLimit),
      );
    }
    const result = invoke(
      args,
      "RELAY_PROVIDER_ONBOARDING_FAILED",
      "formal Relay provider onboarding failed; inspect protected operator logs",
    );
    const imageAfter = invoke(
      [...composeArgs, "images", "--quiet", composeService],
      "RELAY_PROVIDER_IMAGE_UNAVAILABLE",
      "the exact Relay service image identity could not be rechecked",
    );
    if (String(imageAfter.stdout || "").trim() !== imageID) {
      fail(
        "RELAY_PROVIDER_IMAGE_CHANGED",
        "the Relay service image changed during provider onboarding",
      );
    }
    try {
      receipt = JSON.parse(String(result.stdout || ""));
    } catch {
      fail(
        "RELAY_PROVIDER_ONBOARDING_RECEIPT_INVALID",
        "formal Relay provider onboarding returned an invalid receipt",
      );
    }
  } catch (error) {
    workflowError = error;
  } finally {
    if (volumeCleanupRequired) {
      let cleanup;
      try {
        cleanup = runner(
          dockerExecutable,
          ["volume", "rm", "--force", volumeName],
          runOptions,
        );
      } catch {
        cleanup = null;
      }
      if (!cleanup || cleanup.error || cleanup.status !== 0) {
        workflowError = new OnboardingError(
          "RELAY_PROVIDER_CREDENTIAL_CLEANUP_FAILED",
          "the ephemeral Relay onboarding credential volume could not be removed",
          { details: { volume_name: volumeName } },
        );
      }
    }
  }
  if (workflowError) throw workflowError;
  if (phase === "check-credential") {
    if (
      receipt?.credential_available !== true ||
      !Number.isInteger(receipt?.channel_id) ||
      receipt.channel_id <= 0 ||
      !ID_PATTERN.test(receipt?.provider || "") ||
      !ID_PATTERN.test(receipt?.account_id || "") ||
      !Array.isArray(receipt?.public_model_ids) ||
      receipt.public_model_ids.length < 1 ||
      !receipt.public_model_ids.every((id) => ID_PATTERN.test(id))
    ) {
      fail(
        "RELAY_PROVIDER_CREDENTIAL_CHECK_INVALID",
        "formal Relay provider credential check returned an invalid receipt",
      );
    }
    return receipt;
  }
  if (phase === "plan-routes") {
    if (
      !receipt ||
      typeof receipt !== "object" ||
      Array.isArray(receipt) ||
      Object.keys(receipt).length < 1 ||
      !Object.entries(receipt).every(
        ([modelID, declarations]) =>
          ID_PATTERN.test(modelID) &&
          Array.isArray(declarations) &&
          declarations.length > 0 &&
          declarations.every(
            (declaration) =>
              ID_PATTERN.test(declaration?.route_id || "") &&
              Number.isSafeInteger(declaration?.channel_id) &&
              declaration.channel_id > 0 &&
              declaration?.capabilities?.schema_version === 1 &&
              declaration?.capabilities?.modes &&
              Object.keys(declaration.capabilities.modes).length > 0 &&
              (declaration?.model_release == null ||
                declaration.model_release.public_model_id === modelID) &&
              !declaration?.acceptance &&
              !declaration?.acceptance_digest,
          ),
      )
    ) {
      fail(
        "RELAY_PROVIDER_ROUTE_PLAN_INVALID",
        "formal Relay provider route plan is incomplete or unsafe",
      );
    }
    return receipt;
  }
  const routeIDs = receipt?.route_ids;
  const routeReceiptValid =
    phase === "stage"
      ? (routeIDs === null || (Array.isArray(routeIDs) && routeIDs.length === 0)) &&
        typeof receipt?.route_test_ready === "boolean"
      : Array.isArray(routeIDs) &&
        routeIDs.length > 0 &&
        routeIDs.every(
          (id) =>
            typeof id === "string" &&
            id.length > 0 &&
            id.length <= 120 &&
            id.trim() === id &&
            !/[\0\r\n\t]/.test(id),
        ) &&
        receipt?.route_test_ready === true;
  if (
    !Number.isInteger(receipt?.channel_id) ||
    receipt.channel_id <= 0 ||
    !ID_PATTERN.test(receipt?.provider || "") ||
    !Array.isArray(receipt?.public_model_ids) ||
    receipt.public_model_ids.length < 1 ||
    !receipt.public_model_ids.every((id) => ID_PATTERN.test(id)) ||
    !routeReceiptValid ||
    receipt.native_abilities !== "disabled_until_platform_publication"
  ) {
    fail(
      "RELAY_PROVIDER_ONBOARDING_RECEIPT_INVALID",
      "formal Relay provider onboarding receipt is incomplete or unsafe",
    );
  }
  return receipt;
}

export function parsePrivateEnv(text) {
  const values = Object.create(null);
  const lines = String(text).replace(/^\uFEFF/, "").split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const trimmed = lines[index].trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = /^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$/.exec(trimmed);
    if (!match) {
      fail("PRIVATE_ENV_INVALID", `private env line ${index + 1} is invalid`);
    }
    const [, name, raw] = match;
    if (Object.hasOwn(values, name)) {
      fail("PRIVATE_ENV_DUPLICATE", `private env contains duplicate ${name}`);
    }
    let value = raw;
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }
    if (/\$\{|\$\(|`/.test(value) || value.includes("\0")) {
      fail(
        "PRIVATE_ENV_EXPANSION_FORBIDDEN",
        `private env ${name} must be a literal value without expansion`,
      );
    }
    values[name] = value;
  }
  return values;
}

function loadPrivateEnv(pathValue) {
  const path = assertIgnoredPrivatePath(pathValue || DEFAULT_PRIVATE_ENV, "private env");
  if (!existsSync(path)) {
    fail(
      "PRIVATE_ENV_MISSING",
      `private credential file is missing: ${relative(WORKSPACE, path)}`,
    );
  }
  if (!statSync(path).isFile()) fail("PRIVATE_ENV_INVALID", "private env is not a file");
  return { path, values: parsePrivateEnv(readFileSync(path, "utf8")) };
}

function requiredSecret(values, name) {
  const value = String(values[name] || "").trim();
  if (
    value.length < 16 ||
    /^(?:replace|example|changeme|todo|none|null|test|dummy|placeholder)/i.test(value)
  ) {
    fail("CREDENTIAL_MISSING", `required private variable ${name} is missing or a placeholder`);
  }
  return value;
}

function safeBaseUrl(raw, fallback, label) {
  const value = String(raw || fallback).trim().replace(/\/+$/, "");
  let url;
  try {
    url = new URL(value);
  } catch {
    fail("BASE_URL_INVALID", `${label} is not a valid URL`);
  }
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.pathname !== "/"
  ) {
    fail("BASE_URL_INVALID", `${label} must be an exact loopback HTTP origin`);
  }
  return value;
}

export function loadReviewedCandidates() {
  const candidates = [...HISTORICAL_REVIEWED_MODELS];
  for (const [provider, path] of MODEL_MANIFESTS) {
    const document = JSON.parse(readFileSync(resolve(WORKSPACE, path), "utf8"));
    if (document?.schema_version !== 1 || !Array.isArray(document.models)) {
      fail("MODEL_MANIFEST_INVALID", `${path} is not a schema-v1 manifest`);
    }
    for (const model of document.models) {
      if (model.lifecycle !== "acceptance_candidate" || model.new_routes_allowed !== true) {
        continue;
      }
      if (!ID_PATTERN.test(model.public_model_id) || !ID_PATTERN.test(model.provider_model_id)) {
        fail("MODEL_MANIFEST_INVALID", `${path} contains an invalid model identity`);
      }
      candidates.push({
        provider,
        public_model_id: model.public_model_id,
        provider_model_id: model.provider_model_id,
        display_name: model.display_name,
      });
    }
  }
  const ids = candidates.map((item) => item.public_model_id);
  if (!ids.length || new Set(ids).size !== ids.length) {
    fail("MODEL_MANIFEST_INVALID", "reviewed candidate ids are empty or duplicated");
  }
  return candidates.sort((a, b) => a.public_model_id.localeCompare(b.public_model_id));
}

export function loadPricingReview() {
  const path = resolve(WORKSPACE, "deploy/provider-model-pricing.v1.json");
  const bytes = readFileSync(path);
  const document = JSON.parse(bytes.toString("utf8"));
  if (
    document?.schema_version !== 1 ||
    document?.kind !== "provider_model_official_pricing_review" ||
    document?.reviewed_at !== "2026-09-02" ||
    document?.points_per_cny !== 10 ||
    !Array.isArray(document.facts)
  ) {
    fail("PRICING_REVIEW_INVALID", "official pricing review is invalid");
  }
  const allowedHosts = new Set([
    "ai.google.dev",
    "platform.minimax.io",
    "platform.minimaxi.com",
    "www.volcengine.com",
    "docs.volcengine.com",
  ]);
  for (const fact of document.facts) {
    if (!ID_PATTERN.test(fact.public_model_id) || !Array.isArray(fact.official_sources)) {
      fail("PRICING_REVIEW_INVALID", "pricing fact identity or sources are invalid");
    }
    for (const source of fact.official_sources) {
      let parsed;
      try {
        parsed = new URL(source);
      } catch {
        fail("PRICING_REVIEW_INVALID", "pricing source URL is invalid");
      }
      if (parsed.protocol !== "https:" || !allowedHosts.has(parsed.hostname)) {
        fail("PRICING_REVIEW_INVALID", "pricing sources must be official HTTPS origins");
      }
    }
  }
  return { path, sha256: sha256(bytes), ...document };
}

function gcd(left, right) {
  let a = left < 0n ? -left : left;
  let b = right < 0n ? -right : right;
  while (b) [a, b] = [b, a % b];
  return a || 1n;
}

function decimalRational(raw) {
  const value = String(raw);
  if (!/^\d+(?:\.\d+)?$/.test(value)) fail("FX_INVALID", "ECB rate is not decimal");
  const [whole, fraction = ""] = value.split(".");
  let numerator = BigInt(`${whole}${fraction}`);
  let denominator = 10n ** BigInt(fraction.length);
  const divisor = gcd(numerator, denominator);
  numerator /= divisor;
  denominator /= divisor;
  return { numerator, denominator };
}

export function parseEcbDailyXml(bytes, now = new Date(), maxAgeDays = 7) {
  const buffer = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes);
  const text = buffer.toString("utf8");
  const date = /<Cube\s+time=['"](\d{4}-\d{2}-\d{2})['"]/.exec(text)?.[1];
  const usd = /<Cube\s+currency=['"]USD['"]\s+rate=['"]([0-9.]+)['"]/.exec(text)?.[1];
  const cny = /<Cube\s+currency=['"]CNY['"]\s+rate=['"]([0-9.]+)['"]/.exec(text)?.[1];
  if (!date || !usd || !cny) fail("FX_INVALID", "ECB daily XML lacks date, USD, or CNY");
  const observed = new Date(`${date}T00:00:00.000Z`);
  if (Number.isNaN(observed.valueOf())) fail("FX_INVALID", "ECB date is invalid");
  const ageMs = now.valueOf() - observed.valueOf();
  if (ageMs < -24 * 60 * 60 * 1000 || ageMs > maxAgeDays * 86400000) {
    fail("FX_STALE", `ECB FX date ${date} is outside the ${maxAgeDays}-day freshness window`);
  }
  const usdRate = decimalRational(usd);
  const cnyRate = decimalRational(cny);
  let numerator = cnyRate.numerator * usdRate.denominator;
  let denominator = cnyRate.denominator * usdRate.numerator;
  const divisor = gcd(numerator, denominator);
  numerator /= divisor;
  denominator /= divisor;
  const scaled = (numerator * 1_000_000_000n) / denominator;
  return {
    schema_version: 1,
    kind: "ecb_usd_cny_cross_rate_evidence",
    observed_date: date,
    source_url: ECB_DAILY_XML_URL,
    response_sha256: sha256(buffer),
    source_quotes_per_eur: { USD: usd, CNY: cny },
    usd_to_cny: {
      formula: "CNY_per_EUR / USD_per_EUR",
      numerator: numerator.toString(),
      denominator: denominator.toString(),
      decimal_9dp: `${scaled / 1_000_000_000n}.${String(scaled % 1_000_000_000n).padStart(9, "0")}`,
    },
  };
}

export async function fetchEcbFxEvidence({
  fetchImpl = globalThis.fetch,
  now = new Date(),
  maxAgeDays = 7,
} = {}) {
  let response;
  try {
    response = await fetchImpl(ECB_DAILY_XML_URL, {
      method: "GET",
      headers: { Accept: "application/xml,text/xml;q=0.9" },
      redirect: "error",
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    fail("FX_FETCH_FAILED", "ECB daily FX fetch failed; activation is blocked");
  }
  if (!response.ok) {
    fail("FX_FETCH_FAILED", `ECB daily FX fetch returned HTTP ${response.status}`);
  }
  const bytes = Buffer.from(await response.arrayBuffer());
  return parseEcbDailyXml(bytes, now, maxAgeDays);
}

export function buildCnyIdentityEvidence(pricingReview) {
  const evidence = {
    schema_version: 1,
    kind: "cny_identity_rate_evidence",
    observed_date: pricingReview.reviewed_at,
    source: "official provider price denominated in CNY",
    source_reference: "deploy/provider-model-pricing.v1.json",
    source_document_sha256: pricingReview.sha256,
    currency: "CNY",
    cny_micros_per_currency_unit: 1_000_000,
    rational: { numerator: "1", denominator: "1" },
  };
  return {
    ...evidence,
    evidence_sha256: sha256(Buffer.from(canonicalJson(evidence))),
  };
}

function reducedRational(numerator, denominator) {
  if (denominator <= 0n || numerator <= 0n) {
    fail("PRICING_REVIEW_INVALID", "provider rate must be a positive rational");
  }
  const divisor = gcd(numerator, denominator);
  const reducedNumerator = numerator / divisor;
  const reducedDenominator = denominator / divisor;
  if (
    reducedNumerator > BigInt(Number.MAX_SAFE_INTEGER) ||
    reducedDenominator > BigInt(Number.MAX_SAFE_INTEGER)
  ) {
    fail("PRICING_REVIEW_INVALID", "provider rate rational exceeds the safe integer range");
  }
  return {
    unit_amount_micros_numerator: Number(reducedNumerator),
    unit_amount_micros_denominator: Number(reducedDenominator),
  };
}

function rateMicrosRational(rate) {
  const amount = decimalRational(rate.amount);
  let numerator = amount.numerator * 1_000_000n;
  let denominator = amount.denominator;
  if (rate.unit === "million_tokens") denominator *= 1_000_000n;
  return reducedRational(numerator, denominator);
}

function rateMicrosInteger(rate) {
  const rational = rateMicrosRational(rate);
  if (rational.unit_amount_micros_denominator !== 1) {
    fail("COMMERCIAL_RATE_NOT_INTEGRAL", "customer unit rate does not resolve to whole currency micros");
  }
  return rational.unit_amount_micros_numerator;
}

function deterministicUuid(identity) {
  const bytes = createHash("sha256").update(identity).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 0x0f) | 0x50;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function fxCnyMicros(fx) {
  const numerator = BigInt(fx.usd_to_cny.numerator) * 1_000_000n;
  const denominator = BigInt(fx.usd_to_cny.denominator);
  const rounded = (numerator * 2n + denominator) / (2n * denominator);
  if (rounded <= 0n || rounded > 1_000_000_000_000n) {
    fail("FX_INVALID", "ECB USD/CNY conversion is outside the accepted range");
  }
  return Number(rounded);
}

function modelResolutions(declaration) {
  const rows = [];
  const modes = declaration?.capabilities?.modes;
  if (!modes || typeof modes !== "object") {
    fail("ROUTE_INVENTORY_INVALID", "route capability modes are missing");
  }
  for (const [mode, capability] of Object.entries(modes)) {
    const resolutions = capability?.limits?.resolutions;
    if (!Array.isArray(resolutions) || !resolutions.length) {
      fail("ROUTE_INVENTORY_INVALID", `route ${declaration.route_id} lacks resolution bounds`);
    }
    for (const resolution of resolutions) {
      rows.push({ mode, resolution: String(resolution).toLowerCase(), capability });
    }
  }
  return rows;
}

function exactPriceRate(pricing, predicate, label) {
  const matches = pricing.rates.filter(predicate);
  if (matches.length !== 1) {
    fail("PRICING_RECTANGLE_MISSING", `${pricing.public_model_id} lacks one exact ${label} rate`);
  }
  return matches[0];
}

function providerRateComponents(pricing, mode, resolution, capability) {
  const maxImages = Number(capability?.limits?.max_images || 0);
  const maxVideos = Number(capability?.limits?.max_videos || 0);
  const components = [];
  const add = (metric, rate, extra = {}) => {
    components.push({ metric, ...rateMicrosRational(rate), ...extra });
  };
  switch (pricing.public_model_id) {
    case "gemini-omni-1.1-flash": {
      add("input_token", exactPriceRate(pricing, (rate) => rate.component === "input", "input-token"));
      const text = exactPriceRate(
        pricing,
        (rate) => rate.component === "text_output_including_thinking",
        "text/thinking-token",
      );
      add("output_text_token", text);
      add("thought_token", text);
      add(
        "output_video_token",
        exactPriceRate(pricing, (rate) => rate.component === "video_output", "video-token"),
      );
      break;
    }
    case "veo-3.1":
    case "veo-3.1-fast":
    case "minimax-h3-max":
      add(
        "output_second",
        exactPriceRate(
          pricing,
          (rate) =>
            rate.component === "video_output" &&
            String(rate.resolution || "").toLowerCase() === resolution,
          `${resolution} output-second`,
        ),
      );
      break;
    case "minimax-h3":
      add(
        "output_second",
        exactPriceRate(
          pricing,
          (rate) =>
            rate.component === "video_output" &&
            String(rate.resolution || "").toLowerCase() === resolution,
          `${resolution} output-second`,
        ),
      );
      if (maxVideos > 0 || mode === "video_to_video") {
        add(
          "input_second",
          exactPriceRate(
            pricing,
            (rate) =>
              rate.component === "video_input" &&
              String(rate.resolution || "").toLowerCase() === resolution,
            `${resolution} input-second`,
          ),
        );
      }
      if (maxImages > 5) {
        add(
          "input_image_above_free",
          exactPriceRate(
            pricing,
            (rate) => rate.component === "image_input" && rate.unit === "image_after_first_5",
            "reference-image",
          ),
          { free_units: 5 },
        );
      }
      break;
    case "seedream-5":
      add(
        "output_item",
        exactPriceRate(pricing, (rate) => rate.component === "image_output", "output-item"),
      );
      break;
    default:
      if (pricing.public_model_id.startsWith("seedance-")) {
        add(
          "total_token",
          exactPriceRate(
            pricing,
            (rate) =>
              rate.component === "generation" &&
              (Object.hasOwn(rate, "input_video_present")
                ? rate.input_video_present === (maxVideos > 0 || mode === "video_to_video")
                : true),
            "total-token",
          ),
        );
        break;
      }
      fail("PRICING_RECTANGLE_MISSING", `${pricing.public_model_id} has no runtime cost mapping`);
  }
  return components.sort((left, right) => left.metric.localeCompare(right.metric));
}

/**
 * Build the complete immutable Relay rate-set input from reviewed pricing,
 * exact compiled route capabilities and a hashed FX observation. No customer
 * price or provider credential participates in this projection.
 */
export function buildProviderCostRateSets({ intent, routes, pricing, fx }) {
  if (!routes || typeof routes !== "object" || Array.isArray(routes)) {
    fail("ROUTE_INVENTORY_INVALID", "complete route inventory is invalid");
  }
  const intentModel = new Map(intent.enabled.map((item) => [item.public_model_id, item]));
  const providerConfig = new Map(intent.providers.map((provider) => [provider.id, provider]));
  const providerID = new Map([
    ["google-gemini-api", "google"],
    ["minimax", "minimax"],
    ["volcengine-ark", "volcengine"],
  ]);
  for (const model of intent.enabled) {
    const declarations = routes[model.public_model_id];
    const targetProvider = intent.providers.find(
      (provider) => provider.id === model.candidate.provider,
    );
    if (
      !Array.isArray(declarations) ||
      !declarations.some(
        (declaration) => declaration.channel_id === targetProvider?.channel_id,
      )
    ) {
      fail(
        "ROUTE_INVENTORY_INCOMPLETE",
        `${model.public_model_id} lacks its exact staged provider channel route`,
      );
    }
  }
  const reviewDate = `${pricing.reviewed_at}T00:00:00Z`;
  const rateSets = [];
  for (const [publicModelID, declarations] of Object.entries(routes)) {
    const model = intentModel.get(publicModelID);
    if (!model) continue;
    if (!Array.isArray(declarations) || !declarations.length) {
      fail("ROUTE_INVENTORY_INVALID", `${publicModelID} has no route declarations`);
    }
    for (const declaration of declarations) {
      const provider = providerID.get(declaration.provider_name);
      const config = providerConfig.get(provider);
      if (!provider || !config || model.candidate.provider !== provider) {
        fail("ROUTE_INVENTORY_INVALID", `${publicModelID} route provider is not the reviewed provider`);
      }
      const selectedPricing = model.pricing;
      const currency = selectedPricing.currency;
      if (currency !== "CNY" && currency !== "USD") {
        fail("PRICING_RECTANGLE_MISSING", `${publicModelID} has no usable currency-specific price`);
      }
      if (currency === "USD" && !fx) {
        fail("FX_REQUIRED", `${publicModelID} requires immutable USD/CNY FX evidence`);
      }
      const fxObservedAt = currency === "USD" ? `${fx.observed_date}T00:00:00Z` : null;
      const effectiveFrom =
        currency === "USD" && fxObservedAt > reviewDate ? fxObservedAt : reviewDate;
      for (const { mode, resolution, capability } of modelResolutions(declaration)) {
        const components = providerRateComponents(selectedPricing, mode, resolution, capability);
        const identity = canonicalJson({
          schema_version: 1,
          provider_name: declaration.provider_name,
          channel_id: declaration.channel_id,
          upstream_model: declaration.upstream_model,
          mode,
          resolution,
          effective_from: effectiveFrom,
          source_document_sha256: pricing.sha256,
          components,
        });
        rateSets.push({
          schema_version: 1,
          id: deterministicUuid(`provider-cost-rate-set-v1:${identity}`),
          provider_name: declaration.provider_name,
          provider_origin: new URL(config.base_url).origin,
          pricing_market:
            provider === "minimax" ? config.region : provider === "volcengine" ? "cn" : "global",
          platform_billing_unit: publicModelID === "seedream-5" ? "per_item" : "per_second",
          channel_id: declaration.channel_id,
          upstream_model: declaration.upstream_model,
          mode,
          resolution,
          currency,
          effective_from: effectiveFrom,
          source_reference: selectedPricing.official_sources[0],
          source_document_sha256: pricing.sha256,
          fx_cny_micros_per_currency_unit: currency === "USD" ? fxCnyMicros(fx) : 1_000_000,
          ...(currency === "USD"
            ? {
                fx_source_reference: fx.source_url,
                fx_source_document_sha256: fx.response_sha256,
                fx_observed_at: fxObservedAt,
              }
            : {}),
          components,
        });
      }
    }
  }
  rateSets.sort((left, right) =>
    [left.provider_name, left.channel_id, left.upstream_model, left.mode, left.resolution, left.id]
      .join("\0")
      .localeCompare(
        [right.provider_name, right.channel_id, right.upstream_model, right.mode, right.resolution, right.id].join("\0"),
      ),
  );
  if (!rateSets.length || new Set(rateSets.map((item) => item.id)).size !== rateSets.length) {
    fail("PRICING_RECTANGLE_MISSING", "provider cost rate-set inventory is empty or duplicated");
  }
  return rateSets;
}

function capabilityLimitSummary(capability) {
  const limits = Object.values(capability?.modes || {}).map((mode) => mode?.limits || {});
  if (!limits.length) fail("PLATFORM_MODEL_INVALID", "candidate capability has no modes");
  const resolutions = limits.flatMap((limit) => limit.resolutions || []).map((value) => String(value).toLowerCase());
  const rank = new Map([
    ["360p", 360], ["480p", 480], ["720p", 720], ["768p", 768],
    ["1080p", 1080], ["2k", 2000], ["4k", 4000],
  ]);
  if (!resolutions.length || resolutions.some((value) => !rank.has(value))) {
    fail("COMMERCIAL_CAPABILITY_UNQUALIFIED", "candidate resolution ceiling is not commercially orderable");
  }
  return {
    maxResolution: resolutions.sort((a, b) => rank.get(b) - rank.get(a))[0],
    maxImages: Math.max(...limits.map((limit) => Number(limit.max_images || 0))),
    maxVideos: Math.max(...limits.map((limit) => Number(limit.max_videos || 0))),
    maxAudio: Math.max(...limits.map((limit) => Number(limit.max_audio || 0))),
    maxOutputCount: Math.max(
      ...limits.map((limit) => Math.max(...(limit.output_counts || [0]).map(Number))),
    ),
  };
}

export function buildCommercialReleaseRequest({ model, intentModel, pricing, fx, cnyFx, reason, idempotencyPrefix }) {
  if (!intentModel.commercially_qualified) return null;
  const capability = model?.relay_capability_candidate;
  const revision = model?.relay_capability_candidate_revision;
  const catalogRevision = model?.relay_capability_candidate_catalog_revision;
  if (
    !Number.isSafeInteger(model?.capability_version) ||
    !SHA256_PATTERN.test(revision || "") ||
    !SHA256_PATTERN.test(catalogRevision || "") ||
    !capability
  ) {
    fail("PLATFORM_MODEL_INVALID", `${intentModel.public_model_id} lacks an exact live candidate`);
  }
  const limits = capabilityLimitSummary(capability);
  let kind;
  let billingUnit;
  let components;
  let enforcedLimits;
  if (intentModel.public_model_id === "seedream-5") {
    kind = "output_item";
    billingUnit = "per_item";
    components = [{
      component: "output_item",
      rate_micros: rateMicrosInteger(
        exactPriceRate(intentModel.pricing, (rate) => rate.component === "image_output", "output-item"),
      ),
      quantity_numerator: 1,
      quantity_denominator: 1,
    }];
    enforcedLimits = { max_output_count: limits.maxOutputCount };
  } else {
    if (limits.maxVideos > 0) {
      fail("COMMERCIAL_CAPABILITY_UNQUALIFIED", `${intentModel.public_model_id} retains chargeable video input`);
    }
    kind = "resolution_output_second";
    billingUnit = "per_second";
    const outputRates = intentModel.pricing.rates.filter(
      (rate) => rate.component === "video_output" && rate.unit === "second",
    );
    if (!outputRates.length) {
      fail("COMMERCIAL_CAPABILITY_UNQUALIFIED", `${intentModel.public_model_id} lacks an output-second ceiling`);
    }
    const maxRate = outputRates
      .map(rateMicrosInteger)
      .reduce((maximum, value) => Math.max(maximum, value), 0);
    components = [{
      component: "output_second",
      rate_micros: maxRate,
      quantity_numerator: 1,
      quantity_denominator: 1,
    }];
    enforcedLimits = { max_resolution: limits.maxResolution };
    if (limits.maxImages > 0) {
      enforcedLimits.max_reference_images = limits.maxImages;
      enforcedLimits.included_reference_images = limits.maxImages;
    }
    if (limits.maxAudio > 0) {
      enforcedLimits.max_reference_audio_files = limits.maxAudio;
      enforcedLimits.included_reference_audio_files = limits.maxAudio;
    }
  }
  const currency = intentModel.pricing.currency;
  const fxEvidence = currency === "USD" ? fx : cnyFx;
  if (!fxEvidence) fail("FX_REQUIRED", `${intentModel.public_model_id} lacks FX evidence`);
  const providerEffective =
    currency === "USD" && `${fx.observed_date}T00:00:00Z` > `${pricing.reviewed_at}T00:00:00Z`
      ? `${fx.observed_date}T00:00:00Z`
      : `${pricing.reviewed_at}T00:00:00Z`;
  const idempotencyDigest = sha256(
    Buffer.from(`${intentModel.public_model_id}\0${revision}\0${pricing.sha256}`),
  ).slice(0, 32);
  return {
    expected_capability_version: model.capability_version,
    expected_candidate_revision: revision,
    expected_catalog_revision: catalogRevision,
    provider_cost_currency: currency,
    provider_cost_formula: {
      schema_version: 1,
      kind,
      platform_billing_unit: billingUnit,
      source_capability_revision: revision,
      assumptions: {
        quantity_basis: "relay_effective_capability_ceiling",
        enforced_limits: enforcedLimits,
      },
      components,
    },
    provider_cost_evidence_kind: "provider_price_list",
    provider_cost_evidence_reference: intentModel.pricing.official_sources[0],
    provider_cost_evidence_sha256: pricing.sha256,
    provider_cost_effective_at: providerEffective,
    fx_cny_micros_per_currency_unit: currency === "USD" ? fxCnyMicros(fx) : 1_000_000,
    fx_source: currency === "USD" ? "ECB daily EUR cross rate" : "CNY identity rate",
    fx_version:
      currency === "USD"
        ? `ecb:${fx.observed_date}:${fx.response_sha256}`
        : `pricing-review:${pricing.reviewed_at}:${pricing.sha256}`,
    fx_evidence_sha256:
      currency === "USD"
        ? fx.evidence_sha256 || sha256(Buffer.from(canonicalJson(fx)))
        : cnyFx.evidence_sha256,
    fx_effective_at:
      currency === "USD" ? `${fx.observed_date}T00:00:00Z` : `${pricing.reviewed_at}T00:00:00Z`,
    personal_price_points: null,
    enterprise_price_points: null,
    personal_config_override: {},
    enterprise_config_override: {},
    approval_reason: reason,
    idempotency_key: `${idempotencyPrefix}${idempotencyDigest}`.slice(0, 120),
  };
}

function loadJsonPrivate(pathValue, label) {
  const path = assertIgnoredPrivatePath(pathValue, label);
  if (!existsSync(path)) fail(`${label.toUpperCase().replaceAll(" ", "_")}_MISSING`, `${label} is missing`);
  const bytes = readFileSync(path);
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    fail(`${label.toUpperCase().replaceAll(" ", "_")}_INVALID`, `${label} is not valid JSON`);
  }
  return { path, bytes, sha256: sha256(bytes), document };
}

export function validateIntent(document, candidates, pricing) {
  if (
    document?.schema_version !== 2 ||
    document?.kind !== "provider_model_onboarding_intent" ||
    document?.environment !== "development"
  ) {
    fail("INTENT_INVALID", "intent must be a schema-v2 local development onboarding intent");
  }
  if (!Array.isArray(document.models) || !document.models.length) {
    fail("INTENT_INVALID", "intent models must be a non-empty array");
  }
  const candidateById = new Map(candidates.map((item) => [item.public_model_id, item]));
  const priceById = new Map(pricing.facts.map((item) => [item.public_model_id, item]));
  const enabled = [];
  const seen = new Set();
  for (const model of document.models) {
    if (!ID_PATTERN.test(model?.public_model_id || "") || seen.has(model.public_model_id)) {
      fail("INTENT_INVALID", "intent contains an invalid or duplicate model id");
    }
    seen.add(model.public_model_id);
    const candidate = candidateById.get(model.public_model_id);
    const price = priceById.get(model.public_model_id);
    if (!candidate || !price || price.provider !== candidate.provider) {
      fail("INTENT_INVALID", `${model.public_model_id} is not a reviewed priced candidate`);
    }
    if (model.route_enabled !== true) continue;
    if (
      model.commercial_policy !== "automatic_when_cost_ceiling_is_qualified" ||
      Object.keys(model).some((key) =>
        [
          "personal_price_points",
          "enterprise_price_points",
          "provider_cost_formula",
          "provider_cost_evidence",
        ].includes(key),
      )
    ) {
      fail(
        "INTENT_INVALID",
        `${model.public_model_id} must use repository-reviewed automatic pricing instead of hand-entered money facts`,
      );
    }
    enabled.push({ ...model, candidate, pricing: price });
  }
  if (!enabled.length) fail("NO_MODELS_ENABLED", "intent enables no reviewed models");
  const providerIds = [...new Set(enabled.map((item) => item.candidate.provider))].sort();
  const providers = [];
  for (const providerId of providerIds) {
    const config = document.providers?.[providerId];
    const contract = PROVIDERS[providerId];
    if (!config || !contract || config.enabled !== true) {
      fail("PROVIDER_CONFIG_REQUIRED", `${providerId} provider config must be explicitly enabled`);
    }
    if (!Number.isSafeInteger(config.channel_id) || config.channel_id <= 0) {
      fail("PROVIDER_CONFIG_INVALID", `${providerId} channel_id must be a positive fixed integer`);
    }
    const baseUrl = String(config.base_url || "").replace(/\/+$/, "");
    if (!contract.allowedBaseUrls.includes(baseUrl)) {
      fail("PROVIDER_CONFIG_INVALID", `${providerId} base_url must be an exact reviewed official origin`);
    }
    providers.push({
      id: providerId,
      ...contract,
      channel_id: config.channel_id,
      region: config.region || "",
      base_url: baseUrl,
      models: enabled.filter((item) => item.candidate.provider === providerId),
    });
    for (const item of enabled.filter(
      (candidate) => candidate.candidate.provider === providerId,
    )) {
      if (item.pricing.markets) {
        const market = Object.values(item.pricing.markets).find(
          (entry) => entry.base_url === baseUrl,
        );
        if (!market) {
          fail(
            "PRICING_MARKET_UNAVAILABLE",
            `${item.public_model_id} has no official pricing review for ${baseUrl}`,
          );
        }
        item.pricing = {
          ...item.pricing,
          ...market,
          official_sources: market.official_sources,
        };
      }
      item.commercially_qualified =
        item.pricing.status.startsWith("official_price_reviewed") &&
        !item.pricing.commercial_blocker &&
        Array.isArray(item.pricing.rates) &&
        item.pricing.rates.some((rate) => String(rate.amount) !== "0");
      item.commercial_blocker = item.pricing.commercial_blocker || item.pricing.blocker || null;
    }
  }
  const reason = String(document.commercial_release?.approval_reason || "").trim();
  if (reason.length < 3 || reason.length > 500) {
    fail("INTENT_INVALID", "commercial release approval_reason must be 3 to 500 characters");
  }
  if (
    document.commercial_release?.pricing_policy !==
      "provider_cost_plus_30_percent_margin_ceil_to_point" ||
    document.commercial_release?.points_per_cny !== pricing.points_per_cny
  ) {
    fail("INTENT_INVALID", "commercial release must use the frozen 10-points/CNY cost-plus policy");
  }
  const idempotencyPrefix = String(document.commercial_release?.idempotency_prefix || "");
  if (!SAFE_IDEMPOTENCY_PATTERN.test(`${idempotencyPrefix}12345678`.slice(0, 120))) {
    fail("INTENT_INVALID", "commercial release idempotency_prefix is invalid");
  }
  const paidSmoke = document.customer_smoke;
  if (
    paidSmoke?.enabled !== true ||
    !enabled.some(
      (item) => item.public_model_id === paidSmoke.model_id && item.commercially_qualified,
    ) ||
    !Number.isSafeInteger(paidSmoke.seed_balance_cents) ||
    paidSmoke.seed_balance_cents < 100 ||
    typeof paidSmoke.prompt !== "string" ||
    paidSmoke.prompt.trim().length < 3
  ) {
    fail("PAID_SMOKE_APPROVAL_REQUIRED", "customer smoke must target one commercially qualified model with a bounded local seed balance");
  }
  const paidAcceptance = document.paid_acceptance;
  if (
    paidAcceptance?.enabled !== true ||
    paidAcceptance.scope !== "commercially_qualified_only" ||
    paidAcceptance.acknowledgement !== APPLY_CONFIRMATION ||
    typeof paidAcceptance.max_total_provider_cost_cny !== "string" ||
    !/^\d{1,6}(?:\.\d{1,2})?$/.test(paidAcceptance.max_total_provider_cost_cny) ||
    Number(paidAcceptance.max_total_provider_cost_cny) <= 0
  ) {
    fail("PAID_ACCEPTANCE_REQUIRED", "paid route acceptance requires an exact acknowledgement and CNY budget ceiling");
  }
  return {
    document,
    enabled,
    providers,
    paidSmoke,
    paidAcceptance,
    reason,
    idempotencyPrefix,
  };
}

function buildPlan() {
  const candidates = loadReviewedCandidates();
  const pricing = loadPricingReview();
  return {
    status: "PLAN_ONLY",
    real_acceptance: false,
    mutating: false,
    points_per_cny: pricing.points_per_cny,
    reviewed_at: pricing.reviewed_at,
    pricing_review_sha256: pricing.sha256,
    fx: {
      required_for: ["google", "minimax global endpoint"],
      source_url: ECB_DAILY_XML_URL,
      cross_rate: "CNY_per_EUR / USD_per_EUR",
      freshness_days: 7,
      behavior: "fetch/hash/freshness failure blocks commercial approval; no constant fallback",
    },
    phases: [
      "validate New API managed provider credentials and the owner-approved intent",
      "start or recreate the authenticated local stack only through npm run services:start:local",
      "materialize exact native channels in manually-disabled state through the Relay one-shot CLI",
      "derive the complete reviewed development route inventory and immutable provider rate sets from compiled Relay facts",
      "restart through the unique local entry and execute durable paid route acceptance for every declared mode",
      "submit immutable Platform commercial release plans only where a server-enforced cost ceiling is qualified",
      "verify personal and every active points-v2 company grant, then run one bounded customer task and four-way cost check",
    ],
    required_operator_inputs: [
      "Enter or rotate each enabled provider key in New API > 供应商接入",
      "Use --private-env only for an explicit audited break-glass file workflow",
      "No Relay admin, Platform owner, customer bearer token, or provider key is accepted on argv",
    ],
    reviewed_candidates: candidates.map((candidate) => ({
      ...candidate,
      pricing_status: pricing.facts.find(
        (fact) => fact.public_model_id === candidate.public_model_id,
      )?.status,
    })),
    explicit_blockers: pricing.facts
      .filter((fact) => fact.blocker || fact.commercial_blocker)
      .map((fact) => ({
        public_model_id: fact.public_model_id,
        blocker: fact.blocker || fact.commercial_blocker,
      })),
  };
}

function writePrivateJson(pathValue, value) {
  const path = assertIgnoredPrivatePath(pathValue, "private evidence output");
  mkdirSync(dirname(path), { recursive: true });
  const temp = `${path}.${process.pid}.${randomUUID()}.tmp`;
  writeFileSync(temp, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
    flag: "wx",
  });
  renameSync(temp, path);
  return path;
}

async function requestJson(baseUrl, path, tokenOrOptions = {}, maybeOptions = {}) {
  const options =
    typeof tokenOrOptions === "string"
      ? { ...maybeOptions, token: tokenOrOptions }
      : tokenOrOptions || {};
  const endpoint = new URL(path, `${baseUrl}/`);
  let response;
  try {
    response = await fetch(endpoint, {
      method: options.method || "GET",
      headers: {
        Accept: "application/json",
        ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      redirect: "error",
      signal: AbortSignal.timeout(options.timeoutMs || 15_000),
    });
  } catch {
    if (options.allowUnknown === true) {
      return { status: null, body: null, network_unknown: true };
    }
    fail("CONTROL_PLANE_UNAVAILABLE", `${endpoint.origin}${endpoint.pathname} is unavailable`);
  }
  const acceptedStatuses = options.acceptedStatuses || null;
  if (
    options.acceptAnyStatus !== true &&
    !(acceptedStatuses ? acceptedStatuses.includes(response.status) : response.ok)
  ) {
    fail(
      "CONTROL_PLANE_REJECTED",
      `${endpoint.origin}${endpoint.pathname} returned HTTP ${response.status}`,
    );
  }
  let body;
  try {
    const text = await response.text();
    body = text ? JSON.parse(text) : null;
  } catch {
    if (options.allowInvalidJson === true) {
      body = null;
    } else {
      fail("CONTROL_PLANE_INVALID", `${endpoint.pathname} returned invalid JSON`);
    }
  }
  if (body?.success === false && options.allowErrorBody !== true) {
    fail("CONTROL_PLANE_REJECTED", `${endpoint.pathname} rejected the operation`);
  }
  return options.returnMeta === true ? { status: response.status, body } : body;
}

function readDotEnvValue(pathValue, name, { required = true } = {}) {
  const absolute = isAbsolute(pathValue) ? resolve(pathValue) : resolve(WORKSPACE, pathValue);
  let stat;
  try {
    stat = lstatSync(absolute);
  } catch {
    if (!required) return null;
    fail("RUNTIME_ENV_MISSING", `${relative(WORKSPACE, absolute)} is unavailable`);
  }
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 8 * 1024 * 1024) {
    fail("RUNTIME_ENV_INVALID", `${relative(WORKSPACE, absolute)} is not a bounded regular file`);
  }
  const matches = readFileSync(absolute, "utf8")
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .map((line) => new RegExp(`^${name}=(.*)$`).exec(line))
    .filter(Boolean);
  if (matches.length > 1) fail("RUNTIME_ENV_DUPLICATE", `${name} is defined more than once`);
  if (!matches.length) {
    if (!required) return null;
    fail("RUNTIME_ENV_VALUE_MISSING", `${name} is not defined in ${relative(WORKSPACE, absolute)}`);
  }
  let value = matches[0][1];
  if (
    value.length >= 2 &&
    ((value.startsWith("'") && value.endsWith("'")) ||
      (value.startsWith('"') && value.endsWith('"')))
  ) {
    value = value.slice(1, -1);
  }
  return value;
}

function updatePrivateDotEnv(pathValue, updates) {
  const path = assertIgnoredPrivatePath(pathValue, "local paid-canary runtime env");
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 8 * 1024 * 1024) {
    fail("RUNTIME_ENV_INVALID", "local paid-canary runtime env is invalid");
  }
  const raw = readFileSync(path, "utf8");
  const newline = raw.includes("\r\n") ? "\r\n" : "\n";
  const lines = raw.replace(/^\uFEFF/, "").split(/\r?\n/);
  for (const [name, value] of Object.entries(updates)) {
    if (!/^[A-Z][A-Z0-9_]*$/.test(name) || /[\0\r\n]/.test(value)) {
      fail("RUNTIME_ENV_UPDATE_INVALID", "local runtime environment update is invalid");
    }
    const indexes = [];
    for (let index = 0; index < lines.length; index += 1) {
      if (new RegExp(`^${name}=`).test(lines[index])) indexes.push(index);
    }
    if (indexes.length > 1) fail("RUNTIME_ENV_DUPLICATE", `${name} is defined more than once`);
    const rendered = `${name}=${value}`;
    if (indexes.length) lines[indexes[0]] = rendered;
    else {
      while (lines.length && lines.at(-1) === "") lines.pop();
      lines.push(rendered, "");
    }
  }
  const next = lines.join(newline);
  const temp = `${path}.${process.pid}.${randomUUID()}.tmp`;
  writeFileSync(temp, next, { encoding: "utf8", mode: 0o600, flag: "wx" });
  renameSync(temp, path);
  return { path, previous: raw, sha256: sha256(Buffer.from(next)) };
}

function loadCurrentRouteInventory() {
  const raw = readDotEnvValue(PAID_CANARY_RUNTIME_ENV, "NEW_API_RELAY_MODEL_ROUTES_JSON");
  let routes;
  try {
    routes = JSON.parse(raw);
  } catch {
    fail("ROUTE_INVENTORY_INVALID", "current paid-canary route inventory is not valid JSON");
  }
  if (!routes || typeof routes !== "object" || Array.isArray(routes)) {
    fail("ROUTE_INVENTORY_INVALID", "current paid-canary route inventory is invalid");
  }
  return routes;
}

function loadLocalControlIdentity() {
  const operationsToken =
    readDotEnvValue(".env", "PLATFORM_RELAY_OPERATIONS_TOKEN", { required: false }) ||
    "local-platform-relay-operations-token-change-me-03";
  const operationsTenant =
    readDotEnvValue(".env", "PLATFORM_RELAY_OPERATIONS_TENANT_ID", { required: false }) ||
    "00000000-0000-4000-8000-000000000001";
  const bootstrapToken =
    readDotEnvValue(".env", "PLATFORM_BOOTSTRAP_TOKEN", { required: false }) ||
    "local-platform-bootstrap-secret-2026-08-14";
  if (
    operationsToken.length < 16 ||
    bootstrapToken.length < 16 ||
    /[\0\r\n]/.test(`${operationsToken}${bootstrapToken}`) ||
    !/^[0-9a-f-]{36}$/.test(operationsTenant)
  ) {
    fail("LOCAL_CONTROL_IDENTITY_INVALID", "local Relay operations identity is invalid");
  }
  return {
    ownerID: null,
    ownerHeaders: null,
    operationsToken,
    operationsTenant,
    bootstrapToken,
  };
}

async function attachLocalPlatformOwner(context, { allowBootstrap }) {
  let owner = runLocalPlatformOwnerResolver();
  if (owner === null && allowBootstrap) {
    const suffix = randomUUID().toLowerCase();
    const created = await requestJson(
      context.platformBase,
      "/api/v1/bootstrap/platform-admin",
      {
        method: "POST",
        headers: { "X-Bootstrap-Token": context.control.bootstrapToken },
        body: {
          email: `provider-model-onboarding-${suffix}@demo-ai-video.cn`,
          display_name: "Local provider model onboarding owner",
        },
        acceptedStatuses: [201],
      },
    );
    if (!/^[0-9a-f-]{36}$/i.test(created?.user_id || "")) {
      fail("LOCAL_OWNER_BOOTSTRAP_INVALID", "Platform returned an invalid bootstrapped owner");
    }
    owner = runLocalPlatformOwnerResolver();
  }
  if (owner === null) {
    fail(
      "LOCAL_OWNER_NOT_FOUND",
      allowBootstrap
        ? "the local Platform owner could not be resolved after bootstrap"
        : "the read-only check requires an existing local Platform owner",
    );
  }
  context.control.ownerID = owner.user_id;
  context.control.ownerHeaders = { "X-Platform-Admin-User-ID": owner.user_id };
  context.control.ownerSource = owner.source;
  return owner;
}

function writeProviderCredentialFiles(context) {
  const providerNames = new Map([
    ["google", "google-gemini-api"],
    ["minimax", "minimax"],
    ["volcengine", "volcengine-ark"],
  ]);
  const files = new Map();
  for (const provider of context.intent.providers) {
    const apiKey = context.providerKeys.get(provider.id);
    const accountID = `account-${sha256(Buffer.from(apiKey)).slice(0, 24)}`;
    const file = `deploy/secrets/provider-model-onboarding.${provider.id}.credential.json`;
    writePrivateJson(file, {
      schema_version: 1,
      provider: providerNames.get(provider.id),
      ...(provider.id === "minimax" ? { region: provider.region } : {}),
      account_id: accountID,
      channel_id: provider.channel_id,
      api_key: apiKey,
      public_model_ids: provider.models.map((model) => model.public_model_id).sort(),
    });
    files.set(provider.id, { file, accountID });
  }
  return files;
}

function acceptanceModes(declaration) {
  const allowed = new Set(["text_to_image", "text_to_video", "image_to_video"]);
  const modes = Object.keys(declaration?.capabilities?.modes || {})
    .filter((mode) => allowed.has(mode))
    .sort();
  if (!modes.length) {
    fail("ROUTE_ACCEPTANCE_UNAVAILABLE", `${declaration?.route_id || "route"} has no safe paid acceptance mode`);
  }
  return modes;
}

function qualifiedRouteTestTargets(intent, routes) {
  const targets = [];
  for (const model of intent.enabled.filter((item) => item.commercially_qualified)) {
    const provider = intent.providers.find((item) => item.id === model.candidate.provider);
    const declarations = (routes[model.public_model_id] || []).filter(
      (declaration) => declaration.channel_id === provider.channel_id,
    );
    if (declarations.length !== 1) {
      fail("ROUTE_INVENTORY_INCOMPLETE", `${model.public_model_id} must have one exact onboarding route`);
    }
    for (const mode of acceptanceModes(declarations[0])) {
      targets.push({ model, provider, declaration: declarations[0], mode });
    }
  }
  targets.sort((left, right) =>
    `${left.model.public_model_id}\0${left.mode}`.localeCompare(
      `${right.model.public_model_id}\0${right.mode}`,
    ),
  );
  if (!targets.length || targets.length > 32) {
    fail("ROUTE_ACCEPTANCE_UNAVAILABLE", "paid route acceptance target count is invalid");
  }
  return targets;
}

function acceptanceBudgetCnyMicros(intent, targets, rateSets) {
  let total = 0n;
  for (const target of targets) {
    const capability = target.declaration.capabilities.modes[target.mode];
    const durations = (capability.limits.duration_seconds || []).map(Number);
    const duration = durations.includes(4) ? 4 : durations[0];
    const resolutions = (capability.limits.resolutions || []).map((value) => String(value).toLowerCase());
    const resolution = resolutions.includes("720p") ? "720p" : resolutions[0];
    const rateSet = rateSets.find(
      (item) =>
        item.channel_id === target.provider.channel_id &&
        item.upstream_model === target.declaration.upstream_model &&
        item.mode === target.mode &&
        item.resolution === resolution,
    );
    if (!rateSet) fail("ROUTE_ACCEPTANCE_COST_MISSING", `${target.model.public_model_id} acceptance cost is missing`);
    let sourceNumerator = 0n;
    let sourceDenominator = 1n;
    for (const component of rateSet.components) {
      let quantity;
      if (component.metric === "output_second") quantity = BigInt(duration);
      else if (component.metric === "output_item") quantity = 1n;
      else if (component.metric === "input_image_above_free") quantity = 0n;
      else {
        fail("ROUTE_ACCEPTANCE_COST_UNBOUNDED", `${target.model.public_model_id} paid probe has an unbounded cost component`);
      }
      const numerator =
        BigInt(component.unit_amount_micros_numerator) * quantity;
      const denominator = BigInt(component.unit_amount_micros_denominator);
      sourceNumerator = sourceNumerator * denominator + numerator * sourceDenominator;
      sourceDenominator *= denominator;
      const divisor = gcd(sourceNumerator, sourceDenominator);
      sourceNumerator /= divisor;
      sourceDenominator /= divisor;
    }
    const cnyNumerator =
      sourceNumerator * BigInt(rateSet.fx_cny_micros_per_currency_unit);
    const cnyDenominator = sourceDenominator * 1_000_000n;
    total += (cnyNumerator + cnyDenominator - 1n) / cnyDenominator;
  }
  const budget = decimalRational(intent.paidAcceptance.max_total_provider_cost_cny);
  const budgetMicros =
    (budget.numerator * 1_000_000n) / budget.denominator;
  if (total > budgetMicros) {
    fail(
      "PAID_ACCEPTANCE_BUDGET_EXCEEDED",
      `reviewed acceptance fixtures require up to ${total} CNY micros, above the approved ceiling`,
    );
  }
  return { estimated_cny_micros: Number(total), approved_cny_micros: Number(budgetMicros) };
}

function loadRuntimeJson(name, { required = true } = {}) {
  const raw = readDotEnvValue(PAID_CANARY_RUNTIME_ENV, name, { required });
  if (raw === null) return null;
  let value;
  try {
    value = JSON.parse(raw);
  } catch {
    fail("RUNTIME_ENV_VALUE_INVALID", `${name} is not valid JSON`);
  }
  return value;
}

function exactRouteForModel(intent, routes, publicModelID) {
  const model = intent.enabled.find((item) => item.public_model_id === publicModelID);
  const provider = intent.providers.find((item) => item.id === model?.candidate?.provider);
  const declarations = (routes?.[publicModelID] || []).filter(
    (declaration) => declaration?.channel_id === provider?.channel_id,
  );
  if (declarations.length !== 1) {
    fail("ROUTE_INVENTORY_INCOMPLETE", `${publicModelID} must have one exact onboarding route`);
  }
  return declarations[0];
}

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

async function prepareContext(cli) {
  const pricing = loadPricingReview();
  const candidates = loadReviewedCandidates();
  const privateEnv = cli.privateEnv
    ? loadPrivateEnv(cli.privateEnv)
    : { path: null, values: Object.create(null) };
  const intentLoaded = loadJsonPrivate(cli.intent || DEFAULT_INTENT, "onboarding intent");
  const intent = validateIntent(intentLoaded.document, candidates, pricing);
  const providerKeys = new Map();
  if (cli.credentialMode === "legacy-file") {
    for (const provider of intent.providers) {
      providerKeys.set(provider.id, requiredSecret(privateEnv.values, provider.credential));
    }
  }
  const relayBase = safeBaseUrl(
    privateEnv.values.MODEL_ONBOARDING_RELAY_BASE_URL,
    "http://127.0.0.1:8300",
    "Relay base URL",
  );
  const platformBase = safeBaseUrl(
    privateEnv.values.MODEL_ONBOARDING_PLATFORM_BASE_URL,
    "http://127.0.0.1:8200",
    "Platform base URL",
  );
  const requiresUsd = intent.enabled.some((item) => item.pricing.currency === "USD");
  const maxAge = Number(privateEnv.values.MODEL_ONBOARDING_MAX_FX_AGE_DAYS || 7);
  if (!Number.isInteger(maxAge) || maxAge < 1 || maxAge > 7) {
    fail("FX_POLICY_INVALID", "MODEL_ONBOARDING_MAX_FX_AGE_DAYS must be 1 through 7");
  }
  let fx = requiresUsd ? await fetchEcbFxEvidence({ maxAgeDays: maxAge }) : null;
  if (fx) {
    fx = {
      ...fx,
      evidence_sha256: sha256(Buffer.from(canonicalJson(fx))),
    };
  }
  const cnyFx = buildCnyIdentityEvidence(pricing);
  return {
    pricing,
    candidates,
    privateEnv,
    intentLoaded,
    intent,
    credentialMode: cli.credentialMode,
    providerKeys,
    relayBase,
    platformBase,
    fx,
    cnyFx,
    control: loadLocalControlIdentity(),
  };
}

async function runCheck(cli) {
  const context = await prepareContext(cli);
  const credentialChecks =
    context.credentialMode === "managed-vault"
      ? context.intent.providers.map((provider) =>
          runFormalRelayProviderOnboarding({
            environment: "development",
            phase: "check-credential",
            managedProvider: provider.managedProvider,
          }),
        )
      : [];
  await attachLocalPlatformOwner(context, { allowBootstrap: false });
  const routes = loadCurrentRouteInventory();
  const rateSets = loadRuntimeJson("NEW_API_RELAY_PROVIDER_COST_RATE_SETS_JSON", {
    required: false,
  });
  const relayHealth = await requestJson(context.relayBase, "/health/live");
  const platformHealth = await requestJson(context.platformBase, "/health/live");
  const models = await requestJson(
    context.platformBase,
    "/api/v1/platform-admin/models",
    { headers: context.control.ownerHeaders },
  );
  const releases = await requestJson(
    context.platformBase,
    "/api/v1/platform-admin/model-commercial-releases",
    { headers: context.control.ownerHeaders },
  );
  const targetIDs = new Set(context.intent.enabled.map((item) => item.public_model_id));
  return {
    status: "CHECKED_LOCAL_DEVELOPMENT",
    real_acceptance: false,
    mutating: false,
    production_ready: false,
    relay_live: ["healthy", "live"].includes(relayHealth?.state),
    platform_live: platformHealth?.status === "ok" || platformHealth?.state === "live",
    pricing_review_sha256: context.pricing.sha256,
    route_inventory_sha256: sha256(Buffer.from(canonicalJson(routes))),
    provider_rate_set_count: Array.isArray(rateSets) ? rateSets.length : 0,
    provider_credentials:
      context.credentialMode === "managed-vault"
        ? credentialChecks.map((receipt) => ({
            provider: receipt.provider,
            channel_id: receipt.channel_id,
            configured: receipt.credential_available,
          }))
        : context.intent.providers.map((provider) => ({
            provider: provider.managedProvider,
            channel_id: provider.channel_id,
            configured: true,
            source: "legacy-break-glass-file",
          })),
    models: (Array.isArray(models) ? models : [])
      .filter((model) => targetIDs.has(model.slug))
      .map((model) => ({
        public_model_id: model.slug,
        status: model.status,
        active: model.active,
        capability_version: model.capability_version,
        candidate_revision: model.relay_capability_candidate_revision,
        approved_revision: model.relay_capability_revision,
      })),
    commercial_releases: (Array.isArray(releases) ? releases : [])
      .filter((release) => targetIDs.has(release.model_slug))
      .map((release) => ({
        public_model_id: release.model_slug,
        state: release.state,
        personal_price_points: release.personal_price_points,
        enterprise_price_points: release.enterprise_price_points,
      })),
  };
}

function stageAndPlanRelayRoutes(context) {
  const credentialBindings =
    context.credentialMode === "legacy-file"
      ? new Map(
          [...writeProviderCredentialFiles(context)].map(([providerID, value]) => [
            providerID,
            { credentialFile: value.file },
          ]),
        )
      : new Map(
          context.intent.providers.map((provider) => [
            provider.id,
            { managedProvider: PROVIDERS[provider.id].managedProvider },
          ]),
        );
  const stageReceipts = [];
  for (const provider of context.intent.providers) {
    const credential = credentialBindings.get(provider.id);
    stageReceipts.push(
      runFormalRelayProviderOnboarding({
        environment: "development",
        phase: "stage",
        ...credential,
      }),
    );
  }

  let routes = loadCurrentRouteInventory();
  writePrivateJson(DEFAULT_CURRENT_ROUTES, routes);
  const createdAt = `${context.pricing.reviewed_at}T00:00:00Z`;
  for (const provider of context.intent.providers) {
    const credential = credentialBindings.get(provider.id);
    routes = runFormalRelayProviderOnboarding({
      environment: "development",
      phase: "plan-routes",
      ...credential,
      currentRoutesFile: DEFAULT_CURRENT_ROUTES,
      createdAt,
      createdBy: "local-platform-owner-onboarding",
      reason: "Credential-late reviewed provider route plan for bounded local paid acceptance",
      rpmLimit: 60,
      activeTaskLimit: 4,
    });
    writePrivateJson(DEFAULT_CURRENT_ROUTES, routes);
  }
  writePrivateJson(DEFAULT_PLANNED_ROUTES, routes);
  writePrivateJson(DEFAULT_BOOTSTRAP_RECEIPT, {
    schema_version: 2,
    kind: "provider_model_channel_bootstrap_receipt",
    created_at: new Date().toISOString(),
    accepted: false,
    channels: stageReceipts.map((receipt) => ({
      provider: receipt.provider,
      channel_id: receipt.channel_id,
      public_model_ids: receipt.public_model_ids,
      native_abilities: receipt.native_abilities,
      route_test_ready: receipt.route_test_ready,
    })),
  });
  return { credentialBindings, stageReceipts, routes };
}

function finalizeRelayRoutes(context, credentialBindings) {
  return context.intent.providers.map((provider) => {
    const credential = credentialBindings.get(provider.id);
    return runFormalRelayProviderOnboarding({
      environment: "development",
      phase: "finalize",
      ...credential,
      signedRoutesFile: DEFAULT_PLANNED_ROUTES,
    });
  });
}

function routeOperationIdentity(target) {
  const digest = sha256(
    Buffer.from(
      `${target.provider.channel_id}\0${target.declaration.route_id}\0${target.model.public_model_id}\0${target.mode}`,
    ),
  );
  return {
    operationID: `provider-route-test-${digest.slice(0, 48)}`,
    requestID: `provider-route-test-request-${digest.slice(0, 40)}`,
  };
}

async function performQualifiedRouteTests(context, routes, rateSets) {
  const targets = qualifiedRouteTestTargets(context.intent, routes);
  const budget = acceptanceBudgetCnyMicros(context.intent, targets, rateSets);
  const receipts = [];
  for (const target of targets) {
    const { operationID, requestID } = routeOperationIdentity(target);
    const body = {
      operation_id: operationID,
      tenant_id: context.control.operationsTenant,
      actor: context.control.ownerID,
      reason: "Reviewed local development paid route acceptance",
      public_model_id: target.model.public_model_id,
      route_id: target.declaration.route_id,
      mode: target.mode,
    };
    const headers = {
      "X-Relay-Operations-Token": context.control.operationsToken,
      "X-Request-ID": requestID,
    };
    let terminal = null;
    let missingOperationReads = 0;
    const deadline = Date.now() + 30 * 60 * 1_000;
    while (Date.now() < deadline) {
      const path = `/internal/platform-generation-operations/channels/${target.provider.channel_id}/test`;
      const post = await requestJson(context.relayBase, path, {
        method: "POST",
        headers,
        body,
        timeoutMs: 30_000,
        acceptAnyStatus: true,
        allowErrorBody: true,
        allowInvalidJson: true,
        allowUnknown: true,
        returnMeta: true,
      });
      const query = new URLSearchParams({ tenant_id: context.control.operationsTenant });
      const read = await requestJson(
        context.relayBase,
        `/internal/platform-generation-operations/channels/${target.provider.channel_id}/operations/${operationID}?${query}`,
        {
          headers,
          acceptedStatuses: [200, 404],
          allowErrorBody: true,
          allowInvalidJson: true,
          allowUnknown: true,
          returnMeta: true,
        },
      );
      if (read.unknown || read.status === null) {
        await delay(1_000);
        continue;
      }
      if (read.status === 404) {
        if (
          post.status !== null &&
          post.status >= 400 &&
          post.status < 500 &&
          ![408, 409, 425, 429].includes(post.status)
        ) {
          fail(
            "ROUTE_ACCEPTANCE_REQUEST_REJECTED",
            `${target.model.public_model_id}/${target.mode} route-test intent was rejected before a durable operation existed`,
            { details: { http_status: post.status } },
          );
        }
        missingOperationReads += 1;
        if (missingOperationReads <= 3) {
          await delay(1_000);
          continue;
        }
        fail(
          "ROUTE_ACCEPTANCE_OPERATION_UNKNOWN",
          `${target.model.public_model_id}/${target.mode} has no durable operation after an ambiguous submission`,
        );
      }
      const receipt = read.body;
      if (
        receipt?.operation_id !== operationID ||
        receipt?.tenant_id !== context.control.operationsTenant ||
        receipt?.channel_id !== target.provider.channel_id ||
        receipt?.kind !== "test"
      ) {
        fail("ROUTE_ACCEPTANCE_RECEIPT_INVALID", "durable route-test receipt identity is invalid");
      }
      if (receipt.provider_submission_state === "submission_unknown") {
        fail(
          "ROUTE_ACCEPTANCE_RECONCILIATION_REQUIRED",
          `${target.model.public_model_id}/${target.mode} has an unknown provider submission; the same paid request must not be submitted again`,
          { details: { operation_id: operationID, route_id: target.declaration.route_id } },
        );
      }
      if (receipt.state === "succeeded") {
        if (
          receipt.result?.success !== true ||
          receipt.provider_submission_state !== "artifact_verified"
        ) {
          fail("ROUTE_ACCEPTANCE_RECEIPT_INVALID", "successful route-test receipt lacks artifact evidence");
        }
        terminal = receipt;
        break;
      }
      if (receipt.state === "failed") {
        fail(
          "ROUTE_ACCEPTANCE_FAILED",
          `${target.model.public_model_id}/${target.mode} failed real paid acceptance`,
          {
            details: {
              operation_id: operationID,
              error_code: receipt.result?.error_code || receipt.provider_blocker_code || null,
            },
          },
        );
      }
      if (
        receipt.state !== "pending" ||
        !["not_started", "submitted"].includes(receipt.provider_submission_state)
      ) {
        fail("ROUTE_ACCEPTANCE_RECEIPT_INVALID", "route-test receipt entered an unsupported state");
      }
      await delay(5_000);
    }
    if (!terminal) {
      fail(
        "ROUTE_ACCEPTANCE_TIMEOUT",
        `${target.model.public_model_id}/${target.mode} did not reach a proven terminal result`,
        { details: { operation_id: operationID } },
      );
    }
    receipts.push({
      public_model_id: target.model.public_model_id,
      mode: target.mode,
      route_id: target.declaration.route_id,
      channel_id: target.provider.channel_id,
      operation_id: operationID,
      response_time_ms: terminal.result.response_time_ms,
      provider_submission_state: terminal.provider_submission_state,
      completed_at: terminal.completed_at,
    });
  }
  return { budget, receipts };
}

async function listAllCompanies(context) {
  const companies = [];
  for (let page = 1; page <= 10_000; page += 1) {
    const response = await requestJson(
      context.platformBase,
      `/api/v1/platform-admin/companies?page=${page}&page_size=100`,
      { headers: context.control.ownerHeaders },
    );
    if (!Array.isArray(response?.items) || !Number.isSafeInteger(response?.total)) {
      fail("PLATFORM_COMPANY_RESPONSE_INVALID", "Platform company page is invalid");
    }
    companies.push(...response.items);
    if (companies.length >= response.total) return companies;
  }
  fail("PLATFORM_COMPANY_RESPONSE_INVALID", "Platform company pagination did not terminate");
}

async function ensureSmokeCompany(context) {
  const companyName = "Provider Model Onboarding Acceptance";
  let companies = await listAllCompanies(context);
  let matches = companies.filter((company) => company.name === companyName);
  if (matches.length > 1) {
    fail("SMOKE_COMPANY_COLLISION", "local onboarding smoke company is not unique");
  }
  let company = matches[0];
  if (!company) {
    const ownerDigest = sha256(Buffer.from(context.intentLoaded.sha256)).slice(0, 16);
    company = await requestJson(
      context.platformBase,
      "/api/v1/platform-admin/companies",
      {
        method: "POST",
        headers: context.control.ownerHeaders,
        body: {
          name: companyName,
          owner_email: `provider-onboarding-${ownerDigest}@local.invalid`,
          owner_display_name: "Provider onboarding smoke owner",
        },
      },
    );
    companies = await listAllCompanies(context);
    matches = companies.filter((item) => item.name === companyName);
    if (matches.length !== 1) {
      fail("SMOKE_COMPANY_CREATE_UNKNOWN", "local onboarding smoke company creation is ambiguous");
    }
    company = matches[0];
  }
  if (
    company.status !== "active" ||
    company.owner_activation_required ||
    !company.owner_user_id
  ) {
    fail("SMOKE_COMPANY_UNAVAILABLE", "local onboarding smoke company is not active with an owner");
  }
  const tenantHeaders = {
    "X-User-ID": company.owner_user_id,
    "X-Company-ID": company.id,
  };
  let wallet = await requestJson(
    context.platformBase,
    `/api/v1/companies/${encodeURIComponent(company.id)}/wallet`,
    { headers: tenantHeaders },
  );
  if (wallet.billing_version === 1) {
    if (wallet.reserved_cents !== 0 || !Number.isSafeInteger(wallet.available_cents)) {
      fail("SMOKE_WALLET_UNSAFE", "legacy smoke wallet has an unresolved reservation");
    }
    if (wallet.available_cents < context.intent.paidSmoke.seed_balance_cents) {
      const amount = context.intent.paidSmoke.seed_balance_cents - wallet.available_cents;
      await requestJson(
        context.platformBase,
        `/api/v1/platform-admin/companies/${encodeURIComponent(company.id)}/recharge`,
        {
          method: "POST",
          headers: context.control.ownerHeaders,
          body: {
            amount_cents: amount,
            idempotency_key: `provider-onboarding-recharge-${sha256(Buffer.from(company.id)).slice(0, 32)}`,
            note: "Local provider onboarding smoke funding before points migration",
          },
        },
      );
      wallet = await requestJson(
        context.platformBase,
        `/api/v1/companies/${encodeURIComponent(company.id)}/wallet`,
        { headers: tenantHeaders },
      );
    }
    await requestJson(
      context.platformBase,
      `/api/v1/platform-admin/companies/${encodeURIComponent(company.id)}/billing/migrate-to-points`,
      {
        method: "POST",
        headers: context.control.ownerHeaders,
        body: {
          expected_available_cents: wallet.available_cents,
          idempotency_key: `provider-onboarding-migrate-${sha256(Buffer.from(company.id)).slice(0, 32)}`,
        },
      },
    );
    wallet = await requestJson(
      context.platformBase,
      `/api/v1/companies/${encodeURIComponent(company.id)}/wallet`,
      { headers: tenantHeaders },
    );
  }
  if (
    wallet.billing_version !== 2 ||
    wallet.billing_unit !== "POINT" ||
    wallet.reserved_points !== 0 ||
    !Number.isSafeInteger(wallet.available_points) ||
    wallet.available_points < 100
  ) {
    fail("SMOKE_POINT_WALLET_UNAVAILABLE", "local smoke point wallet is not safely funded");
  }
  return { company, tenantHeaders, wallet, companies: await listAllCompanies(context) };
}

async function publishCommercialModels(context, smokeCompany) {
  await requestJson(
    context.platformBase,
    "/api/v1/platform-admin/relay-models/reconcile",
    { method: "POST", headers: context.control.ownerHeaders },
  );
  let models = await requestJson(
    context.platformBase,
    "/api/v1/platform-admin/models",
    { headers: context.control.ownerHeaders },
  );
  if (!Array.isArray(models)) fail("PLATFORM_MODEL_RESPONSE_INVALID", "Platform model list is invalid");
  const modelBySlug = new Map();
  for (const intentModel of context.intent.enabled) {
    const matches = models.filter((model) => model.slug === intentModel.public_model_id);
    if (matches.length !== 1) {
      fail("PLATFORM_MODEL_RESPONSE_INVALID", `${intentModel.public_model_id} is not materialized exactly once`);
    }
    modelBySlug.set(intentModel.public_model_id, matches[0]);
  }
  for (const intentModel of context.intent.enabled.filter((item) => item.commercially_qualified)) {
    const model = modelBySlug.get(intentModel.public_model_id);
    const body = buildCommercialReleaseRequest({
      model,
      intentModel,
      pricing: context.pricing,
      fx: context.fx,
      cnyFx: context.cnyFx,
      reason: context.intent.reason,
      idempotencyPrefix: context.intent.idempotencyPrefix,
    });
    if (!body) fail("COMMERCIAL_RELEASE_REQUEST_INVALID", "qualified model produced no commercial plan");
    await requestJson(
      context.platformBase,
      `/api/v1/platform-admin/models/${encodeURIComponent(model.id)}/commercial-release-plan`,
      { method: "PUT", headers: context.control.ownerHeaders, body },
    );
  }
  const reconciliation = await requestJson(
    context.platformBase,
    "/api/v1/platform-admin/model-commercial-releases/reconcile",
    { method: "POST", headers: context.control.ownerHeaders },
  );
  const releases = await requestJson(
    context.platformBase,
    "/api/v1/platform-admin/model-commercial-releases",
    { headers: context.control.ownerHeaders },
  );
  if (!Array.isArray(releases)) {
    fail("COMMERCIAL_RELEASE_RESPONSE_INVALID", "commercial release list is invalid");
  }
  models = await requestJson(
    context.platformBase,
    "/api/v1/platform-admin/models",
    { headers: context.control.ownerHeaders },
  );
  const released = [];
  const blocked = [];
  for (const intentModel of context.intent.enabled) {
    const model = models.find((item) => item.slug === intentModel.public_model_id);
    const plans = releases.filter((release) => release.model_slug === intentModel.public_model_id);
    if (intentModel.commercially_qualified) {
      const plan = plans.find(
        (release) => release.candidate_revision === model.relay_capability_candidate_revision,
      );
      if (!plan || plan.state !== "released" || model.status !== "published" || model.active !== true) {
        fail("COMMERCIAL_RELEASE_BLOCKED", `${intentModel.public_model_id} did not reach released state`);
      }
      released.push(plan);
    } else {
      if (model.status === "published" || plans.some((release) => release.state === "released")) {
        fail("UNQUALIFIED_MODEL_PUBLISHED", `${intentModel.public_model_id} was published without an enforceable cost ceiling`);
      }
      blocked.push({
        public_model_id: intentModel.public_model_id,
        blocker: intentModel.commercial_blocker,
        status: model.status,
      });
    }
  }
  const personalGrants = await requestJson(
    context.platformBase,
    "/api/v1/platform-admin/personal-model-grants",
    { headers: context.control.ownerHeaders },
  );
  if (!Array.isArray(personalGrants)) {
    fail("PERSONAL_GRANT_RESPONSE_INVALID", "personal grant list is invalid");
  }
  const activePointCompanies = smokeCompany.companies.filter(
    (company) => company.status === "active" && company.billing_version === 2,
  );
  for (const plan of released) {
    const personal = personalGrants.find(
      (grant) => grant.model_id === plan.model_id && grant.enabled === true,
    );
    const personalPrice =
      plan.billing_mode === "per_item"
        ? personal?.price_per_item_points
        : personal?.price_per_second_points;
    if (!personal || personalPrice !== plan.personal_price_points) {
      fail("PERSONAL_GRANT_INCOMPLETE", `${plan.model_slug} personal points grant is incomplete`);
    }
    const releasedCompanies = new Set(plan.company_ids || []);
    const missingCompanies = activePointCompanies
      .map((company) => company.id)
      .filter((companyID) => !releasedCompanies.has(companyID));
    if (missingCompanies.length || plan.company_grant_count !== releasedCompanies.size) {
      fail("COMPANY_GRANT_INCOMPLETE", `${plan.model_slug} is not granted to every active points-v2 company`);
    }
  }
  return {
    reconciliation,
    released,
    blocked,
    activePointCompanyCount: activePointCompanies.length,
  };
}

async function performCustomerSmoke(context, smokeCompany) {
  const companyID = smokeCompany.company.id;
  const modelCatalog = await requestJson(
    context.platformBase,
    `/api/v1/companies/${encodeURIComponent(companyID)}/models`,
    { headers: smokeCompany.tenantHeaders },
  );
  const available = (Array.isArray(modelCatalog) ? modelCatalog : []).filter(
    (model) => model.slug === context.intent.paidSmoke.model_id,
  );
  if (available.length !== 1 || available[0].billing_version !== 2) {
    fail("CUSTOMER_MODEL_UNAVAILABLE", "the paid smoke model is not available under a points-v2 grant");
  }
  const model = available[0];
  const currentRoutes = loadCurrentRouteInventory();
  const route = exactRouteForModel(context.intent, currentRoutes, model.slug);
  const eligibleRouteIDs = new Set(
    (currentRoutes[model.slug] || []).map((declaration) => declaration?.route_id).filter(Boolean),
  );
  const idempotencyKey = `provider-onboarding-customer-smoke-${sha256(
    Buffer.from(`${companyID}\0${model.id}\0${model.quote_revision}`),
  ).slice(0, 40)}`;
  const taskPath = `/api/v1/companies/${encodeURIComponent(companyID)}/tasks`;
  const taskBody = {
    model_id: model.id,
    expected_capability_version: model.capability_version,
    expected_quote_revision: model.quote_revision,
    idempotency_key: idempotencyKey,
    request_payload: {
      mode: "text_to_image",
      prompt: context.intent.paidSmoke.prompt,
      resolution: "2048x2048",
      aspect_ratio: "1:1",
      output_count: 1,
    },
  };
  const taskDeadline = Date.now() + 30 * 60 * 1_000;
  let task = null;
  while (task === null && Date.now() < taskDeadline) {
    const submitted = await requestJson(context.platformBase, taskPath, {
      method: "POST",
      headers: smokeCompany.tenantHeaders,
      body: taskBody,
      timeoutMs: 30_000,
      acceptAnyStatus: true,
      allowErrorBody: true,
      allowInvalidJson: true,
      allowUnknown: true,
      returnMeta: true,
    });
    if (submitted.status === null) {
      await delay(1_000);
      continue;
    }
    if (submitted.status >= 200 && submitted.status < 300) {
      if (!/^[0-9a-f-]{36}$/i.test(submitted.body?.id || "")) {
        fail("CUSTOMER_SMOKE_TASK_INVALID", "Platform returned an invalid customer task receipt");
      }
      task = submitted.body;
      break;
    }
    if (submitted.status >= 400 && submitted.status < 500) {
      fail(
        "CUSTOMER_SMOKE_TASK_REJECTED",
        "Platform rejected the frozen idempotent customer smoke request",
        { details: { http_status: submitted.status } },
      );
    }
    await delay(1_000);
  }
  if (task === null) {
    fail(
      "CUSTOMER_SMOKE_TASK_UNKNOWN",
      "the frozen customer smoke request has no recoverable Platform task receipt",
      { details: { idempotency_key: idempotencyKey } },
    );
  }
  while (Date.now() < taskDeadline) {
    if (["succeeded", "failed", "cancelled", "reconciliation_required"].includes(task.status)) {
      break;
    }
    await delay(2_000);
    const read = await requestJson(
      context.platformBase,
      `/api/v1/companies/${encodeURIComponent(companyID)}/tasks/${encodeURIComponent(task.id)}`,
      {
        headers: smokeCompany.tenantHeaders,
        acceptedStatuses: [200],
        allowUnknown: true,
        returnMeta: true,
      },
    );
    if (read.status === null) continue;
    task = read.body;
  }
  if (task.status === "reconciliation_required") {
    fail(
      "CUSTOMER_SMOKE_RECONCILIATION_REQUIRED",
      "the customer smoke has an unknown provider result; its exact original task must be reconciled and not recreated",
      { details: { task_id: task.id, relay_job_id: task.relay_job_id } },
    );
  }
  if (
    task.status !== "succeeded" ||
    !/^[0-9a-f-]{36}$/i.test(task.relay_job_id || "") ||
    !Number.isSafeInteger(task.actual_cost_points) ||
    task.actual_cost_points <= 0 ||
    task.reserved_points !== 0 ||
    !Array.isArray(task.output_artifacts) ||
    task.output_artifacts.length < 1
  ) {
    fail("CUSTOMER_SMOKE_FAILED", "the real customer points task did not settle successfully", {
      details: { task_id: task.id, status: task.status, failure_reason: task.failure_reason },
    });
  }
  const wallet = await requestJson(
    context.platformBase,
    `/api/v1/companies/${encodeURIComponent(companyID)}/wallet`,
    { headers: smokeCompany.tenantHeaders },
  );
  const ledger = await requestJson(
    context.platformBase,
    `/api/v1/companies/${encodeURIComponent(companyID)}/ledger`,
    { headers: smokeCompany.tenantHeaders },
  );
  const taskEntries = (Array.isArray(ledger) ? ledger : []).filter(
    (entry) => entry.task_id === task.id,
  );
  const reserve = taskEntries.find((entry) => entry.kind === "reserve");
  const settle = taskEntries.find((entry) => entry.kind === "settle");
  const availableDelta = taskEntries.reduce(
    (total, entry) => total + Number(entry.available_delta_points || 0),
    0,
  );
  const reservedDelta = taskEntries.reduce(
    (total, entry) => total + Number(entry.reserved_delta_points || 0),
    0,
  );
  if (
    wallet.billing_version !== 2 ||
    wallet.reserved_points !== 0 ||
    !reserve ||
    !settle ||
    settle.amount_points !== task.actual_cost_points ||
    availableDelta !== -task.actual_cost_points ||
    reservedDelta !== 0
  ) {
    fail("CUSTOMER_LEDGER_RECONCILIATION_FAILED", "wallet, point ledger and task do not reconcile");
  }
  let costPage = null;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    costPage = await requestJson(
      context.platformBase,
      `/api/v1/platform-admin/channel-costs?task_id=${encodeURIComponent(task.id)}&page_size=200`,
      { headers: context.control.ownerHeaders },
    );
    if (costPage?.total > 0) break;
    await delay(2_000);
  }
  const costItems = Array.isArray(costPage?.items) ? costPage.items : [];
  const actualRouteIDs = new Set(costItems.map((entry) => entry.channel_key));
  if (
    costPage?.total !== costItems.length ||
    costPage.total < 1 ||
    costPage.total_amount_cents <= 0 ||
    costItems.some(
      (entry) =>
        entry.source !== "relay" ||
        entry.task_id !== task.id ||
        entry.relay_job_id !== task.relay_job_id ||
        !eligibleRouteIDs.has(entry.channel_key) ||
        entry.source_document_sha256 !== context.pricing.sha256,
    ) ||
    actualRouteIDs.size !== 1 ||
    task.actual_cost_points * 10 < costPage.total_amount_cents
  ) {
    fail("PROVIDER_COST_RECONCILIATION_FAILED", "provider cost does not reconcile to the paid customer task");
  }
  return {
    company_id: companyID,
    owner_user_id: smokeCompany.company.owner_user_id,
    task_id: task.id,
    relay_job_id: task.relay_job_id,
    route_id: [...actualRouteIDs][0],
    onboarding_route_id: route.route_id,
    used_onboarding_route: actualRouteIDs.has(route.route_id),
    actual_cost_points: task.actual_cost_points,
    provider_cost_cents: costPage.total_amount_cents,
    output_artifact_count: task.output_artifacts.length,
    wallet_available_points: wallet.available_points,
    wallet_reserved_points: wallet.reserved_points,
    ledger_entry_ids: taskEntries.map((entry) => entry.id),
    provider_cost_entry_ids: costItems.map((entry) => entry.id),
  };
}

function runUniqueStartupEntry() {
  const result = spawnSync(
    "npm.cmd",
    ["run", "services:start:local", "--", "-SkipFrontend"],
    {
      cwd: WORKSPACE,
      stdio: "inherit",
      windowsHide: true,
      env: process.env,
    },
  );
  if (result.error || result.status !== 0) {
    fail("LOCAL_START_FAILED", "the unique local service startup entry failed");
  }
}

async function runApply(cli) {
  // All three provider credentials, the owner-approved schema-v2 intent, FX
  // freshness and local control identity are validated before the first write
  // or service mutation. The exact paid acknowledgement and CNY ceiling live
  // in the reviewed intent, not in a fourth operator secret.
  const context = await prepareContext(cli);
  runUniqueStartupEntry();
  await attachLocalPlatformOwner(context, { allowBootstrap: true });
  writePrivateJson(
    context.privateEnv.values.MODEL_ONBOARDING_FX_EVIDENCE_FILE ||
      DEFAULT_FX_EVIDENCE,
    {
      schema_version: 1,
      kind: "provider_model_fx_evidence_set",
      created_at: new Date().toISOString(),
      cny_identity: context.cnyFx,
      ecb_usd_cny: context.fx,
    },
  );
  const relayPlan = stageAndPlanRelayRoutes(context);
  const rateSets = buildProviderCostRateSets({
    intent: context.intent,
    routes: relayPlan.routes,
    pricing: context.pricing,
    fx: context.fx,
  });
  // Calculate the complete reviewed probe budget before any paid route call.
  const plannedTargets = qualifiedRouteTestTargets(context.intent, relayPlan.routes);
  const plannedBudget = acceptanceBudgetCnyMicros(
    context.intent,
    plannedTargets,
    rateSets,
  );
  writePrivateJson(DEFAULT_RATE_SETS, rateSets);
  const runtime = updatePrivateDotEnv(PAID_CANARY_RUNTIME_ENV, {
    NEW_API_RELAY_MODEL_ROUTES_JSON: canonicalJson(relayPlan.routes),
    NEW_API_RELAY_PROVIDER_COST_RATE_SETS_JSON: canonicalJson(rateSets),
  });
  const finalizeReceipts = finalizeRelayRoutes(context, relayPlan.credentialBindings);
  runUniqueStartupEntry();
  const routeAcceptance = await performQualifiedRouteTests(
    context,
    relayPlan.routes,
    rateSets,
  );
  if (
    routeAcceptance.budget.estimated_cny_micros !== plannedBudget.estimated_cny_micros ||
    routeAcceptance.budget.approved_cny_micros !== plannedBudget.approved_cny_micros
  ) {
    fail("PAID_ACCEPTANCE_BUDGET_CHANGED", "paid acceptance budget changed after route activation");
  }
  const smokeCompany = await ensureSmokeCompany(context);
  const commercial = await publishCommercialModels(context, smokeCompany);
  const smoke = await performCustomerSmoke(context, smokeCompany);
  const receipt = {
    schema_version: 2,
    kind: "provider_model_local_development_acceptance_receipt",
    created_at: new Date().toISOString(),
    status: "LOCAL_DEVELOPMENT_ACCEPTED",
    environment: "development",
    real_provider_calls: true,
    production_ready: false,
    production_cutover_authorized: false,
    pricing_review_sha256: context.pricing.sha256,
    onboarding_intent_sha256: context.intentLoaded.sha256,
    fx_evidence_sha256: context.fx?.evidence_sha256 || null,
    route_inventory_sha256: sha256(Buffer.from(canonicalJson(relayPlan.routes))),
    provider_rate_sets_sha256: sha256(Buffer.from(canonicalJson(rateSets))),
    local_runtime_env_sha256: runtime.sha256,
    channels: finalizeReceipts.map((entry) => ({
      provider: entry.provider,
      channel_id: entry.channel_id,
      public_model_ids: entry.public_model_ids,
      route_ids: entry.route_ids,
      native_abilities: entry.native_abilities,
      route_test_ready: entry.route_test_ready,
    })),
    paid_acceptance: routeAcceptance,
    commercial_release: {
      released: commercial.released.map((plan) => ({
        public_model_id: plan.model_slug,
        model_id: plan.model_id,
        state: plan.state,
        billing_mode: plan.billing_mode,
        personal_price_points: plan.personal_price_points,
        enterprise_price_points: plan.enterprise_price_points,
        content_sha256: plan.content_sha256,
        company_grant_count: plan.company_grant_count,
      })),
      blocked: commercial.blocked,
      active_point_company_count: commercial.activePointCompanyCount,
    },
    customer_smoke: smoke,
    limitations: [
      "development route acceptance is unsigned and is not staging or production evidence",
      "protected staging route signatures and production cutover remain separately gated",
    ],
  };
  writePrivateJson(DEFAULT_APPLY_RECEIPT, receipt);
  return receipt;
}

function parseCli(argv) {
  const args = [...argv];
  const command = args.shift();
  if (!command || !["plan", "check", "apply"].includes(command)) {
    fail("USAGE", "Usage: node scripts/provider-model-onboarding.mjs plan|check|apply [--private-env PATH (break-glass)] [--intent PATH]", { exitCode: 2 });
  }
  const result = { command, privateEnv: undefined, intent: undefined };
  while (args.length) {
    const name = args.shift();
    const value = args.shift();
    if (!value || !["--private-env", "--intent"].includes(name)) {
      fail("USAGE", "unknown or incomplete command option", { exitCode: 2 });
    }
    result[name === "--private-env" ? "privateEnv" : "intent"] = value;
  }
  result.privateEnv = result.privateEnv || process.env.MODEL_ONBOARDING_PRIVATE_ENV_FILE || null;
  result.credentialMode = result.privateEnv ? "legacy-file" : "managed-vault";
  result.intent = result.intent || process.env.MODEL_ONBOARDING_INTENT_FILE || DEFAULT_INTENT;
  return result;
}

export async function main(argv = process.argv.slice(2)) {
  const cli = parseCli(argv);
  if (cli.command === "plan") return buildPlan();
  if (cli.command === "check") return runCheck(cli);
  return runApply(cli);
}

function publicError(error) {
  if (error instanceof OnboardingError) {
    return {
      status: "BLOCKED",
      real_acceptance: false,
      code: error.code,
      message: error.message,
      ...(error.details ? { details: error.details } : {}),
    };
  }
  return {
    status: "BLOCKED",
    real_acceptance: false,
    code: "UNEXPECTED_ERROR",
    message: "unexpected onboarding error; inspect local logs without sharing secrets",
  };
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  try {
    const result = await main();
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`${JSON.stringify(publicError(error), null, 2)}\n`);
    process.exitCode = error instanceof OnboardingError ? error.exitCode : 1;
  }
}
