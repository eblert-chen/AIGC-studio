#!/usr/bin/env node

import { createHash, randomUUID } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { access, mkdir, open, readFile } from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const CANDIDATE_UPSTREAM_GIT_REVISION =
  "0ab02020603d22e5613bc4cf46bfab06f8567769";

export const REQUIRED_FAULT_SCENARIOS = Object.freeze({
  provider_post_pre_disconnect: [
    "provider_post_not_attempted",
    "job_retry_safe",
    "reservation_action_valid",
  ],
  provider_post_response_loss: [
    "single_provider_task",
    "reconciliation_required",
    "route_unchanged",
    "account_slot_retained",
    "automatic_resubmit_absent",
  ],
  unknown_reconciliation_created: [
    "reconciliation_required_observed",
    "single_provider_task",
    "original_route_resumed",
    "account_slot_retained_until_terminal",
    "cross_channel_retry_absent",
  ],
  unknown_reconciliation_not_created: [
    "reconciliation_required_observed",
    "proven_non_creation_recorded",
    "cross_channel_retry_absent",
    "account_slot_released",
    "reservation_action_valid",
  ],
  db_commit_failure: [
    "external_side_effect_not_duplicated",
    "state_recoverable",
    "fencing_preserved",
  ],
  worker_kill: [
    "lease_recovered",
    "duplicate_provider_task_absent",
    "terminal_state_not_regressed",
  ],
  lease_expiry: ["new_owner_acquired", "old_owner_fenced"],
  stale_token_late_write: [
    "late_write_rejected",
    "terminal_state_not_regressed",
  ],
  redis_outage: [
    "job_durable_in_postgres",
    "provider_retry_budget_not_consumed",
    "recovery_completed",
  ],
  db_jitter: [
    "database_clock_used",
    "duplicate_claim_absent",
    "recovery_completed",
  ],
  account_pool_cross_mode_shared: [
    "single_physical_account_state",
    "rpm_shared_across_modes",
    "active_capacity_shared_across_modes",
    "cooldown_shared_across_modes",
    "existing_task_polling_unchanged",
  ],
  poll_timeout: [
    "route_unchanged",
    "provider_retry_budget_not_consumed",
    "terminal_state_not_falsified",
  ],
  artifact_corruption: [
    "artifact_rejected",
    "success_not_published",
    "provider_url_not_exposed",
  ],
  artifact_oversize: [
    "artifact_rejected",
    "success_not_published",
    "provider_url_not_exposed",
  ],
  artifact_mime_mismatch: [
    "artifact_rejected",
    "success_not_published",
    "provider_url_not_exposed",
  ],
  artifact_ssrf: [
    "private_or_disallowed_target_blocked",
    "network_fetch_not_attempted",
    "success_not_published",
  ],
  callback_retry_dlq: [
    "signature_verified",
    "at_least_once_retry_observed",
    "dead_letter_observed",
    "single_item_claimed",
    "stale_delivery_token_fenced",
  ],
  provider_success_rate_drop_recovery: [
    "database_clock_lease_used",
    "stale_monitor_token_fenced",
    "drop_transition_deduplicated",
    "recovery_transition_deduplicated",
    "signed_alert_delivery_observed",
  ],
  provider_widespread_route_failure: [
    "widespread_trigger_deduplicated",
    "missing_probe_not_recovery",
    "signed_alert_delivery_observed",
  ],
  provider_batch_account_invalidation: [
    "provider_caused_only",
    "batch_trigger_deduplicated",
    "signed_alert_delivery_observed",
  ],
  provider_alert_retry_dlq: [
    "at_least_once_retry_observed",
    "dead_letter_observed",
    "single_item_claimed",
    "stale_delivery_token_fenced",
    "readiness_backlog_reported",
  ],
});

const REQUIRED_REAL_EVIDENCE = Object.freeze([
  "provider_task",
  "provider_bill",
  "obs_head",
  "callback_delivery",
  "wallet_settlement",
  "provider_cost_ledger",
]);

const STATUS_TO_RESERVATION = Object.freeze({
  queued: "hold",
  submitting: "hold",
  reconciliation_required: "hold",
  processing: "hold",
  transferring: "hold",
  succeeded: "settle",
  failed: "release",
  cancelled: "release",
});

const MODEL_LIST_KEYS = [
  "api_version",
  "schema_version",
  "object",
  "catalog_revision",
  "data",
];
const MODEL_KEYS = [
  "api_version",
  "schema_version",
  "id",
  "object",
  "capability_revision",
  "capabilities",
];
const ACCEPTED_KEYS = [
  "api_version",
  "schema_version",
  "object",
  "id",
  "job_id",
  "status",
  "idempotent_replay",
  "expected_capability_revision",
  "capability_revision",
  "reservation_action",
  "created_at",
];
const JOB_KEYS = [
  "api_version",
  "schema_version",
  "object",
  "id",
  "client_reference_id",
  "model",
  "mode",
  "inputs",
  "output",
  "metadata",
  "status",
  "progress",
  "outputs",
  "error",
  "expected_capability_revision",
  "capability_revision",
  "reservation_action",
  "created_at",
  "updated_at",
];
const REQUIRED_JOB_KEYS = JOB_KEYS.filter((key) => key !== "client_reference_id");
const REVISION_RE = /^sha256:[0-9a-f]{64}$/;
const IMAGE_DIGEST_RE = /^sha256:[0-9a-f]{64}$/;
const GIT_REVISION_RE = /^[0-9a-f]{40}$/;
const CANDIDATE_INSTANCE_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$/;
const CHANNEL_CLASSES = new Set(["reverse", "third_party_api", "official"]);
const DEFAULT_FAULT_CLOCK_SKEW_MS = 5_000;

class GateError extends Error {
  constructor(status, message, evidence = []) {
    super(message);
    this.name = "GateError";
    this.status = status;
    this.evidence = evidence;
  }
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function canonicalJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function compactError(error) {
  const text = error instanceof Error ? error.message : String(error);
  return text.replace(/[\r\n\t]+/g, " ").slice(0, 240);
}

function gate(id, status, summary, evidence = []) {
  const safeEvidence = evidence.map((entry) => ({ ...entry }));
  return {
    id,
    status,
    summary,
    evidence: safeEvidence,
    evidence_sha256: `sha256:${sha256(canonicalJson(safeEvidence))}`,
  };
}

function pass(id, summary, evidence = []) {
  return gate(id, "PASS", summary, evidence);
}

function fail(id, summary, evidence = []) {
  return gate(id, "FAIL", summary, evidence);
}

function blocked(id, summary, evidence = []) {
  return gate(id, "BLOCKED", summary, evidence);
}

function requireCondition(condition, message, evidence = []) {
  if (!condition) {
    throw new GateError("FAIL", message, evidence);
  }
}

function exactKeys(value, allowed, required, label) {
  requireCondition(value && typeof value === "object" && !Array.isArray(value), `${label} must be an object`);
  const keys = Object.keys(value);
  const unknown = keys.filter((key) => !allowed.includes(key));
  const missing = required.filter((key) => !keys.includes(key));
  requireCondition(unknown.length === 0, `${label} returned unknown fields: ${unknown.join(", ")}`);
  requireCondition(missing.length === 0, `${label} omitted required fields: ${missing.join(", ")}`);
}

function validateReservation(value, label) {
  const expected = STATUS_TO_RESERVATION[value?.status];
  requireCondition(Boolean(expected), `${label} returned an unknown status`);
  requireCondition(
    value.reservation_action === expected,
    `${label} mapped ${value.status} to ${value.reservation_action}, expected ${expected}`,
  );
}

function validateAccepted(value, label) {
  exactKeys(value, ACCEPTED_KEYS, ACCEPTED_KEYS, label);
  requireCondition(value.api_version === "v1", `${label} api_version is not v1`);
  requireCondition(value.schema_version === 1, `${label} schema_version is not 1`);
  requireCondition(value.object === "generation", `${label} object is not generation`);
  requireCondition(value.id === value.job_id, `${label} id and job_id differ`);
  requireCondition(REVISION_RE.test(value.capability_revision), `${label} capability revision is invalid`);
  requireCondition(
    REVISION_RE.test(value.expected_capability_revision),
    `${label} expected capability revision is invalid`,
  );
  validateReservation(value, label);
}

function validateJob(value, label) {
  exactKeys(value, JOB_KEYS, REQUIRED_JOB_KEYS, label);
  requireCondition(value.api_version === "v1", `${label} api_version is not v1`);
  requireCondition(value.schema_version === 1, `${label} schema_version is not 1`);
  requireCondition(value.object === "generation", `${label} object is not generation`);
  validateReservation(value, label);
}

function validateModelList(value, label) {
  exactKeys(value, MODEL_LIST_KEYS, MODEL_LIST_KEYS, label);
  requireCondition(value.api_version === "v1", `${label} api_version is not v1`);
  requireCondition(value.schema_version === 1, `${label} schema_version is not 1`);
  requireCondition(value.object === "list", `${label} object is not list`);
  requireCondition(REVISION_RE.test(value.catalog_revision), `${label} catalog revision is invalid`);
  requireCondition(Array.isArray(value.data), `${label} data is not an array`);
  for (const [index, model] of value.data.entries()) {
    exactKeys(model, MODEL_KEYS, MODEL_KEYS, `${label}.data[${index}]`);
    requireCondition(model.api_version === "v1", `${label}.data[${index}] api_version is not v1`);
    requireCondition(model.schema_version === 1, `${label}.data[${index}] schema_version is not 1`);
    requireCondition(model.object === "model", `${label}.data[${index}] object is not model`);
    requireCondition(
      REVISION_RE.test(model.capability_revision),
      `${label}.data[${index}] capability revision is invalid`,
    );
    exactKeys(model.capabilities, ["schema_version", "modes"], ["schema_version", "modes"], `${label}.data[${index}].capabilities`);
    requireCondition(model.capabilities.schema_version === 1, `${label}.data[${index}] capability schema is not 1`);
    requireCondition(
      model.capabilities.modes && typeof model.capabilities.modes === "object",
      `${label}.data[${index}] modes is invalid`,
    );
  }
}

function responseEvidence(label, response) {
  const etag = response.headers.get("etag");
  const requestId = response.headers.get("x-request-id");
  return {
    kind: "http_response",
    label,
    status: response.status,
    body_sha256: `sha256:${sha256(response.text)}`,
    etag: /^"sha256:[0-9a-f]{64}"$/.test(etag || "") ? etag : null,
    request_id_sha256: requestId ? `sha256:${sha256(requestId)}` : null,
    upstream_revision: response.headers.get("x-relay-upstream-revision"),
    source_revision: response.headers.get("x-relay-source-revision"),
    image_digest: response.headers.get("x-relay-image-digest"),
  };
}

function normalizedErrorCode(response) {
  return response.json?.error?.code ?? response.json?.code ?? null;
}

function jsonShape(value) {
  if (Array.isArray(value)) return value.length ? [jsonShape(value[0])] : [];
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, jsonShape(value[key])]),
    );
  }
  if (value === null) return "null";
  return typeof value;
}

async function readResponse(response) {
  const text = await response.text();
  let json = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
  }
  return { status: response.status, headers: response.headers, text, json };
}

async function requestJson(fetchImpl, baseUrl, path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 15_000);
  const headers = new Headers(options.headers || {});
  if (options.credential) {
    headers.set("X-Client-ID", options.credential.clientId);
    headers.set("X-API-Key", options.credential.apiKey);
  }
  let body;
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = canonicalJson(options.body);
  }
  try {
    const response = await fetchImpl(new URL(path, `${baseUrl.replace(/\/$/, "")}/`), {
      method: options.method || "GET",
      headers,
      body,
      signal: controller.signal,
      redirect: "error",
    });
    return await readResponse(response);
  } catch (error) {
    throw new GateError("BLOCKED", `HTTP evidence unavailable: ${compactError(error)}`);
  } finally {
    clearTimeout(timeout);
  }
}

function validHttpUrl(value, { production = false } = {}) {
  try {
    const parsed = new URL(value);
    if (parsed.username || parsed.password) return false;
    return production ? parsed.protocol === "https:" : ["http:", "https:"].includes(parsed.protocol);
  } catch {
    return false;
  }
}

function endpointOrigin(value) {
  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

function validateConfig(config) {
  const errors = [];
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    return ["configuration must be a JSON object"];
  }
  if (config.schemaVersion !== 2) errors.push("schemaVersion must equal 2");
  if (typeof config.environment !== "string" || !config.environment.trim()) {
    errors.push("environment is required");
  }
  if (!config.candidate || typeof config.candidate !== "object") {
    errors.push("candidate is required");
  } else {
    if (config.candidate.upstreamGitRevision !== CANDIDATE_UPSTREAM_GIT_REVISION) {
      errors.push(`candidate.upstreamGitRevision must equal ${CANDIDATE_UPSTREAM_GIT_REVISION}`);
    }
    if (
      !GIT_REVISION_RE.test(config.candidate.gitRevision || "") ||
      config.candidate.gitRevision === "0".repeat(40) ||
      config.candidate.gitRevision === CANDIDATE_UPSTREAM_GIT_REVISION
    ) {
      errors.push("candidate.gitRevision must identify the committed extension fork, not the upstream baseline");
    }
    if (
      !IMAGE_DIGEST_RE.test(config.candidate.imageDigest || "") ||
      config.candidate.imageDigest === `sha256:${"0".repeat(64)}`
    ) {
      errors.push("candidate.imageDigest must be an immutable non-placeholder sha256 digest");
    }
    if (!CANDIDATE_INSTANCE_RE.test(config.candidate.instanceId || "")) {
      errors.push("candidate.instanceId must identify the exact candidate process/container under test");
    }
    if (!validHttpUrl(config.candidate.baseUrl || "", { production: config.environmentClass === "production" })) {
      errors.push("candidate.baseUrl must be an allowed absolute HTTP(S) URL");
    }
  }
  if (!config.oracle || !validHttpUrl(config.oracle.baseUrl || "")) {
    errors.push("oracle.baseUrl must be an allowed absolute HTTP(S) URL");
  } else {
    if (config.oracle.mode !== "isolated_offline_oracle") {
      errors.push("oracle.mode must equal isolated_offline_oracle");
    }
    if (config.oracle.productionAdmissionAllowed !== false) {
      errors.push("oracle.productionAdmissionAllowed must equal false");
    }
    if (
      endpointOrigin(config.oracle.baseUrl) &&
      endpointOrigin(config.oracle.baseUrl) === endpointOrigin(config.candidate?.baseUrl)
    ) {
      errors.push("oracle.baseUrl and candidate.baseUrl must resolve to different origins");
    }
  }
  if (!["development", "staging", "production"].includes(config.environmentClass)) {
    errors.push("environmentClass must be development, staging, or production");
  }
  if (config.environmentClass === "production") {
    errors.push("offline Python oracle acceptance must never run in a production environment");
  }
  if (!Array.isArray(config.tenants) || config.tenants.length < 2) {
    errors.push("at least two tenant service credentials are required");
  } else {
    const labels = new Set();
    const clients = new Set();
    for (const [index, tenant] of config.tenants.entries()) {
      if (!tenant?.label || !tenant?.clientId || !tenant?.apiKeyEnv) {
        errors.push(`tenants[${index}] requires label, clientId, and apiKeyEnv`);
      }
      labels.add(tenant?.label);
      clients.add(tenant?.clientId);
    }
    if (labels.size !== config.tenants.length || clients.size !== config.tenants.length) {
      errors.push("tenant labels and client IDs must be distinct");
    }
  }
  if (!config.testCase?.model || !config.testCase?.mode) {
    errors.push("testCase.model and testCase.mode are required");
  }
  if (!Array.isArray(config.publicModes) || config.publicModes.length === 0) {
    errors.push("publicModes must contain every published generation mode");
  } else {
    const uniqueModes = new Set(config.publicModes);
    if (uniqueModes.size !== config.publicModes.length) errors.push("publicModes must not contain duplicates");
    if (config.testCase?.mode && !uniqueModes.has(config.testCase.mode)) {
      errors.push("testCase.mode must be listed in publicModes");
    }
  }
  return errors;
}

function resolveCredentials(config, env) {
  const missing = [];
  const credentials = (config.tenants || []).map((tenant) => {
    const apiKey = env[tenant.apiKeyEnv];
    if (!apiKey) missing.push(tenant.apiKeyEnv);
    return { label: tenant.label, clientId: tenant.clientId, apiKey };
  });
  if (missing.length) {
    throw new GateError(
      "BLOCKED",
      `service credential environment variables are missing: ${[...new Set(missing)].join(", ")}`,
    );
  }
  return credentials;
}

async function captureGate(id, action) {
  try {
    const result = await action();
    return pass(id, result.summary, result.evidence || []);
  } catch (error) {
    if (error instanceof GateError) {
      return gate(id, error.status, error.message, error.evidence || []);
    }
    return fail(id, compactError(error));
  }
}

function generationPayload(config, revision, suffix = "base") {
  const configured = config.testCase.generationRequest || {};
  const metadata = {
    ...(configured.metadata && typeof configured.metadata === "object" ? configured.metadata : {}),
    relay_migration_acceptance: suffix,
  };
  return {
    ...configured,
    model: config.testCase.model,
    mode: config.testCase.mode,
    expected_capability_revision: revision,
    inputs: configured.inputs || { prompt: "relay migration acceptance probe" },
    output: configured.output || { count: 1 },
    metadata,
  };
}

function findTestModel(catalog, config, label) {
  const model = catalog.data.find((entry) => entry.id === config.testCase.model);
  requireCondition(Boolean(model), `${label} does not publish model ${config.testCase.model}`);
  requireCondition(
    Object.hasOwn(model.capabilities.modes, config.testCase.mode),
    `${label} does not publish mode ${config.testCase.mode}`,
  );
  return model;
}

function catalogModes(catalog) {
  return [...new Set(catalog.data.flatMap((entry) => Object.keys(entry.capabilities.modes)))].sort();
}

async function runContractGates(config, options) {
  const gates = {};
  const fetchImpl = options.fetchImpl;
  let credentials;
  try {
    credentials = resolveCredentials(config, options.env);
  } catch (error) {
    for (const id of [
      "contract.candidate_readiness",
      "contract.auth",
      "contract.models_etag",
      "contract.strict_fields",
      "contract.request_id",
      "contract.idempotency",
      "contract.tenant_non_enumeration",
      "contract.status_reservation",
      "contract.revision_pin_drift",
    ]) {
      gates[id] = blocked(id, error.message);
    }
    return gates;
  }

  const targets = [
    ["python_oracle", config.oracle.baseUrl],
    ["new_api_candidate", config.candidate.baseUrl],
  ];
  const primary = credentials[0];
  const secondary = credentials[1];
  const state = { catalogs: {}, models: {}, jobs: {} };

  gates["contract.candidate_readiness"] = await captureGate("contract.candidate_readiness", async () => {
    const requestId = "accept-candidate-readiness";
    const response = await requestJson(fetchImpl, config.candidate.baseUrl, "/health/ready", {
      credential: primary,
      headers: { "X-Request-ID": requestId },
    });
    const runtimeProvenance = {
      upstream_git_revision: response.headers.get("x-relay-upstream-revision"),
      source_git_revision: response.headers.get("x-relay-source-revision"),
      image_digest: response.headers.get("x-relay-image-digest"),
    };
    const evidence = [
      responseEvidence("new_api_candidate:health_ready", response),
      {
        kind: "candidate_runtime_provenance",
        state: response.json?.state ?? null,
        ...runtimeProvenance,
      },
    ];
    requireCondition(response.status === 200, `candidate readiness returned HTTP ${response.status}`, evidence);
    requireCondition(response.json?.state === "healthy", "candidate readiness state is not healthy", evidence);
    requireCondition(
      runtimeProvenance.upstream_git_revision === config.candidate.upstreamGitRevision,
      "candidate readiness upstream revision does not match acceptance configuration",
      evidence,
    );
    requireCondition(
      runtimeProvenance.source_git_revision === config.candidate.gitRevision,
      "candidate readiness source revision does not match acceptance configuration",
      evidence,
    );
    requireCondition(
      runtimeProvenance.image_digest === config.candidate.imageDigest,
      "candidate readiness image digest does not match acceptance configuration",
      evidence,
    );
    return {
      summary: "candidate readiness is healthy and its live build provenance matches the acceptance candidate",
      evidence,
    };
  });

  gates["contract.auth"] = await captureGate("contract.auth", async () => {
    const evidence = [];
    const targetShapes = [];
    for (const [label, baseUrl] of targets) {
      const absent = await requestJson(fetchImpl, baseUrl, "/v1/models", {
        headers: { "X-Request-ID": `accept-auth-absent-${label}` },
      });
      const invalid = await requestJson(fetchImpl, baseUrl, "/v1/models", {
        headers: {
          "X-Client-ID": primary.clientId,
          "X-API-Key": "relay-acceptance-deliberately-invalid",
          "X-Request-ID": `accept-auth-invalid-${label}`,
        },
      });
      evidence.push(responseEvidence(`${label}:missing`, absent), responseEvidence(`${label}:invalid`, invalid));
      requireCondition(absent.status === 401, `${label} accepted a request without credentials`, evidence);
      requireCondition(invalid.status === 401, `${label} accepted an invalid API key`, evidence);
      requireCondition(
        normalizedErrorCode(absent) === "CLIENT_AUTHENTICATION_REQUIRED",
        `${label} did not return CLIENT_AUTHENTICATION_REQUIRED for missing credentials`,
        evidence,
      );
      requireCondition(
        normalizedErrorCode(invalid) === "INVALID_CLIENT_CREDENTIALS",
        `${label} did not return INVALID_CLIENT_CREDENTIALS for invalid credentials`,
        evidence,
      );
      requireCondition(
        canonicalJson(jsonShape(absent.json)) === canonicalJson(jsonShape(invalid.json)),
        `${label} exposes different authentication error envelopes`,
        evidence,
      );
      targetShapes.push({
        missing_code: normalizedErrorCode(absent),
        invalid_code: normalizedErrorCode(invalid),
        missing_shape: jsonShape(absent.json),
        invalid_shape: jsonShape(invalid.json),
      });
    }
    requireCondition(
      canonicalJson(targetShapes[0]) === canonicalJson(targetShapes[1]),
      "candidate authentication error envelope differs from the Python oracle",
      evidence,
    );
    return { summary: "both relays reject absent and invalid service credentials uniformly", evidence };
  });

  gates["contract.models_etag"] = await captureGate("contract.models_etag", async () => {
    const evidence = [];
    for (const [label, baseUrl] of targets) {
      const requestId = `accept-models-${label}`;
      const first = await requestJson(fetchImpl, baseUrl, "/v1/models", {
        credential: primary,
        headers: { "X-Request-ID": requestId },
      });
      evidence.push(responseEvidence(`${label}:200`, first));
      requireCondition(first.status === 200, `${label} model catalog returned HTTP ${first.status}`, evidence);
      validateModelList(first.json, `${label} model catalog`);
      const etag = first.headers.get("etag");
      requireCondition(etag === `"${first.json.catalog_revision}"`, `${label} ETag does not equal the quoted catalog revision`, evidence);
      const cached = await requestJson(fetchImpl, baseUrl, "/v1/models", {
        credential: primary,
        headers: { "If-None-Match": etag, "X-Request-ID": `${requestId}-304` },
      });
      evidence.push(responseEvidence(`${label}:304`, cached));
      requireCondition(cached.status === 304, `${label} did not honor If-None-Match`, evidence);
      requireCondition(cached.text === "", `${label} returned a body with HTTP 304`, evidence);
      requireCondition(cached.headers.get("etag") === etag, `${label} changed ETag on HTTP 304`, evidence);
      requireCondition(cached.headers.get("x-request-id") === `${requestId}-304`, `${label} did not echo X-Request-ID on HTTP 304`, evidence);
      state.catalogs[label] = first.json;
      state.models[label] = findTestModel(first.json, config, label);
      requireCondition(
        canonicalJson(catalogModes(first.json)) === canonicalJson([...config.publicModes].sort()),
        `${label} published mode set does not equal publicModes`,
        evidence,
      );
      if (label === "new_api_candidate") {
        requireCondition(
          first.headers.get("x-relay-upstream-revision") === config.candidate.upstreamGitRevision,
          "candidate runtime upstream revision does not match acceptance configuration",
          evidence,
        );
        requireCondition(
          first.headers.get("x-relay-source-revision") === config.candidate.gitRevision,
          "candidate runtime source revision does not match acceptance configuration",
          evidence,
        );
        requireCondition(
          first.headers.get("x-relay-image-digest") === config.candidate.imageDigest,
          "candidate runtime image digest does not match acceptance configuration",
          evidence,
        );
      }
    }
    requireCondition(
      canonicalJson(state.models.python_oracle) === canonicalJson(state.models.new_api_candidate),
      "candidate model/mode capability does not equal the Python oracle",
      evidence,
    );
    requireCondition(
      state.catalogs.python_oracle.catalog_revision === state.catalogs.new_api_candidate.catalog_revision,
      "candidate catalog revision does not equal the Python oracle",
      evidence,
    );
    requireCondition(
      canonicalJson(state.catalogs.python_oracle) === canonicalJson(state.catalogs.new_api_candidate),
      "candidate full model catalog does not equal the Python oracle",
      evidence,
    );
    return { summary: "runtime provenance, full catalog/mode set, deterministic revisions, ETag revalidation, and selected capability match", evidence };
  });

  gates["contract.request_id"] = await captureGate("contract.request_id", async () => {
    const evidence = [];
    for (const [label, baseUrl] of targets) {
      const requestId = `accept-request-id-${label}`;
      const response = await requestJson(fetchImpl, baseUrl, "/v1/models", {
        credential: primary,
        headers: { "X-Request-ID": requestId },
      });
      evidence.push(responseEvidence(label, response));
      requireCondition(response.headers.get("x-request-id") === requestId, `${label} did not echo X-Request-ID`, evidence);
    }
    return { summary: "both relays preserve safe caller request IDs", evidence };
  });

  const haveModels = state.models.python_oracle && state.models.new_api_candidate;
  gates["contract.strict_fields"] = haveModels
    ? await captureGate("contract.strict_fields", async () => {
        const evidence = [];
        const targetErrors = [];
        for (const [label, baseUrl] of targets) {
          const revision = state.models[label].capability_revision;
          const baseline = generationPayload(config, revision, "strict-fields");
          const cases = [
            { ...baseline, unexpected_contract_field: true },
            { ...baseline, inputs: { ...baseline.inputs, unexpected_contract_field: true } },
            { ...baseline, output: { ...baseline.output, unexpected_contract_field: true } },
          ];
          const errors = [];
          for (const [index, body] of cases.entries()) {
            const response = await requestJson(fetchImpl, baseUrl, "/v1/generations", {
              method: "POST",
              credential: primary,
              headers: {
                "Idempotency-Key": `accept-strict-${randomUUID()}`,
                "X-Request-ID": `accept-strict-${label}-${index}`,
              },
              body,
            });
            evidence.push(responseEvidence(`${label}:${index}`, response));
            requireCondition(response.status === 422, `${label} accepted unknown request fields in case ${index}`, evidence);
            requireCondition(
              response.headers.get("x-request-id") === `accept-strict-${label}-${index}`,
              `${label} did not echo X-Request-ID for strict-field case ${index}`,
              evidence,
            );
            requireCondition(Boolean(normalizedErrorCode(response)), `${label} strict-field case ${index} omitted an error code`, evidence);
            errors.push({ code: normalizedErrorCode(response), shape: jsonShape(response.json) });
          }
          requireCondition(
            errors.every((item) => canonicalJson(item) === canonicalJson(errors[0])),
            `${label} uses inconsistent strict-field error envelopes`,
            evidence,
          );
          targetErrors.push(errors);
        }
        requireCondition(
          canonicalJson(targetErrors[0]) === canonicalJson(targetErrors[1]),
          "candidate strict-field error envelope differs from the Python oracle",
          evidence,
        );
        return { summary: "root, inputs, and output reject unknown fields with HTTP 422", evidence };
      })
    : blocked("contract.strict_fields", "model catalog gate did not produce a test capability");

  gates["contract.idempotency"] = haveModels
    ? await captureGate("contract.idempotency", async () => {
        const evidence = [];
        for (const [label, baseUrl] of targets) {
          const body = generationPayload(config, state.models[label].capability_revision, `idempotency-${label}`);
          const idempotencyKey = `relay-accept-${randomUUID()}`;
          const headers = {
            "Idempotency-Key": idempotencyKey,
            "X-Request-ID": `accept-submit-${label}`,
          };
          const first = await requestJson(fetchImpl, baseUrl, "/v1/generations", {
            method: "POST",
            credential: primary,
            headers,
            body,
          });
          const replay = await requestJson(fetchImpl, baseUrl, "/v1/generations", {
            method: "POST",
            credential: primary,
            headers: { ...headers, "X-Request-ID": `accept-replay-${label}` },
            body,
          });
          const conflict = await requestJson(fetchImpl, baseUrl, "/v1/generations", {
            method: "POST",
            credential: primary,
            headers: { ...headers, "X-Request-ID": `accept-conflict-${label}` },
            body: { ...body, client_reference_id: `conflict-${randomUUID()}` },
          });
          evidence.push(
            responseEvidence(`${label}:first`, first),
            responseEvidence(`${label}:replay`, replay),
            responseEvidence(`${label}:conflict`, conflict),
          );
          requireCondition(first.status === 202, `${label} initial submission was not accepted`, evidence);
          requireCondition(replay.status === 202, `${label} idempotent replay was not accepted`, evidence);
          validateAccepted(first.json, `${label} first acceptance`);
          validateAccepted(replay.json, `${label} replay acceptance`);
          requireCondition(first.json.id === replay.json.id, `${label} replay returned a different job`, evidence);
          requireCondition(first.json.idempotent_replay === false, `${label} first submission was marked as replay`, evidence);
          requireCondition(replay.json.idempotent_replay === true, `${label} replay was not marked as replay`, evidence);
          requireCondition(conflict.status === 409, `${label} did not reject an idempotency conflict`, evidence);
          requireCondition(first.headers.get("x-request-id") === `accept-submit-${label}`, `${label} did not echo submit X-Request-ID`, evidence);
          requireCondition(replay.headers.get("x-request-id") === `accept-replay-${label}`, `${label} did not echo replay X-Request-ID`, evidence);
          requireCondition(conflict.headers.get("x-request-id") === `accept-conflict-${label}`, `${label} did not echo conflict X-Request-ID`, evidence);
          state.jobs[label] = first.json.id;
        }
        return { summary: "both relays preserve tenant-scoped replay and reject payload conflicts", evidence };
      })
    : blocked("contract.idempotency", "model catalog gate did not produce a test capability");

  const haveJobs = state.jobs.python_oracle && state.jobs.new_api_candidate;
  gates["contract.status_reservation"] = haveJobs
    ? await captureGate("contract.status_reservation", async () => {
        const evidence = [];
        const fieldSets = [];
        for (const [label, baseUrl] of targets) {
          const response = await requestJson(fetchImpl, baseUrl, `/v1/generations/${state.jobs[label]}`, {
            credential: primary,
            headers: { "X-Request-ID": `accept-status-${label}` },
          });
          evidence.push(responseEvidence(label, response));
          requireCondition(response.status === 200, `${label} job lookup returned HTTP ${response.status}`, evidence);
          validateJob(response.json, `${label} job`);
          fieldSets.push(Object.keys(response.json).sort());
        }
        requireCondition(
          canonicalJson(fieldSets[0]) === canonicalJson(fieldSets[1]),
          "candidate and oracle expose different job field sets",
          evidence,
        );
        return { summary: "job states use the frozen public vocabulary and reservation mapping", evidence };
      })
    : blocked("contract.status_reservation", "idempotent submissions did not produce comparable jobs");

  gates["contract.tenant_non_enumeration"] = haveJobs
    ? await captureGate("contract.tenant_non_enumeration", async () => {
        const evidence = [];
        const targetErrors = [];
        for (const [label, baseUrl] of targets) {
          const crossTenant = await requestJson(fetchImpl, baseUrl, `/v1/generations/${state.jobs[label]}`, {
            credential: secondary,
            headers: { "X-Request-ID": `accept-cross-tenant-${label}` },
          });
          const nonexistent = await requestJson(fetchImpl, baseUrl, `/v1/generations/${randomUUID()}`, {
            credential: secondary,
            headers: { "X-Request-ID": `accept-nonexistent-${label}` },
          });
          evidence.push(
            responseEvidence(`${label}:cross_tenant`, crossTenant),
            responseEvidence(`${label}:nonexistent`, nonexistent),
          );
          requireCondition(crossTenant.status === 404, `${label} disclosed a cross-tenant job`, evidence);
          requireCondition(nonexistent.status === 404, `${label} returned a non-404 missing-job response`, evidence);
          requireCondition(
            normalizedErrorCode(crossTenant) === normalizedErrorCode(nonexistent),
            `${label} makes cross-tenant and missing IDs distinguishable by error code`,
            evidence,
          );
          requireCondition(Boolean(normalizedErrorCode(crossTenant)), `${label} omitted its not-found error code`, evidence);
          requireCondition(
            canonicalJson(jsonShape(crossTenant.json)) === canonicalJson(jsonShape(nonexistent.json)),
            `${label} makes cross-tenant and missing IDs distinguishable by envelope`,
            evidence,
          );
          targetErrors.push({ code: normalizedErrorCode(crossTenant), shape: jsonShape(crossTenant.json) });
        }
        requireCondition(
          canonicalJson(targetErrors[0]) === canonicalJson(targetErrors[1]),
          "candidate not-found envelope differs from the Python oracle",
          evidence,
        );
        return { summary: "cross-tenant and nonexistent job identifiers are non-enumerable", evidence };
      })
    : blocked("contract.tenant_non_enumeration", "idempotent submissions did not produce comparable jobs");

  gates["contract.revision_pin_drift"] = haveModels
    ? await captureGate("contract.revision_pin_drift", async () => {
        const evidence = [];
        for (const [label, baseUrl] of targets) {
          const current = state.models[label].capability_revision;
          const drift = current === `sha256:${"0".repeat(64)}`
            ? `sha256:${"1".repeat(64)}`
            : `sha256:${"0".repeat(64)}`;
          const submitted = await requestJson(fetchImpl, baseUrl, "/v1/generations", {
            method: "POST",
            credential: primary,
            headers: {
              "Idempotency-Key": `accept-drift-${randomUUID()}`,
              "X-Request-ID": `accept-drift-${label}`,
            },
            body: generationPayload(config, drift, `revision-drift-${label}`),
          });
          evidence.push(responseEvidence(`${label}:accepted`, submitted));
          requireCondition(submitted.status === 202, `${label} did not durably accept the drift check`, evidence);
          validateAccepted(submitted.json, `${label} drift acceptance`);
          const deadline = Date.now() + (config.contract?.pollTimeoutMs ?? 30_000);
          let terminal;
          do {
            terminal = await requestJson(fetchImpl, baseUrl, `/v1/generations/${submitted.json.id}`, {
              credential: primary,
              headers: { "X-Request-ID": `accept-drift-poll-${label}` },
            });
            if (terminal.json?.status === "failed") break;
            await new Promise((resolveWait) => setTimeout(resolveWait, config.contract?.pollIntervalMs ?? 250));
          } while (Date.now() < deadline);
          evidence.push(responseEvidence(`${label}:terminal`, terminal));
          requireCondition(terminal.status === 200, `${label} drift job lookup failed`, evidence);
          validateJob(terminal.json, `${label} drift job`);
          requireCondition(terminal.json.status === "failed", `${label} revision drift did not reach failed`, evidence);
          requireCondition(terminal.json.reservation_action === "release", `${label} revision drift did not release reservation`, evidence);
          requireCondition(
            terminal.json.error?.code === "CAPABILITY_REVISION_MISMATCH",
            `${label} revision drift returned an unexpected error code`,
            evidence,
          );
        }
        return { summary: "both relays fail pinned revision drift asynchronously before settlement", evidence };
      })
    : blocked("contract.revision_pin_drift", "model catalog gate did not produce a test capability");

  return gates;
}

function faultCandidateIdentity(config) {
  return {
    instance_id: config.candidate.instanceId,
    upstream_git_revision: config.candidate.upstreamGitRevision,
    source_git_revision: config.candidate.gitRevision,
    image_digest: config.candidate.imageDigest,
  };
}

function faultUtcMillis(value) {
  if (typeof value !== "string" || !value.endsWith("Z")) return Number.NaN;
  return Date.parse(value);
}

function faultEvidenceCore(entry) {
  return {
    id: entry.id,
    observed_at_utc: entry.observed_at_utc,
    kind: entry.kind,
    action: entry.action,
    data: entry.data,
  };
}

function validateFaultCandidate(actual, expected, label, evidence) {
  requireCondition(
    actual && typeof actual === "object" && !Array.isArray(actual),
    `${label} omitted candidate identity`,
    evidence,
  );
  requireCondition(
    canonicalJson(actual) === canonicalJson(expected),
    `${label} candidate identity does not match the configured instance/build`,
    evidence,
  );
}

export function validateFaultStart(started, expected, evidence = []) {
  requireCondition(started?.schema_version === 1, `${expected.scenario} start schema is not v1`, evidence);
  requireCondition(
    typeof started?.run_id === "string" && started.run_id.length >= 8,
    `${expected.scenario} control plane omitted run_id`,
    evidence,
  );
  requireCondition(started.run_nonce === expected.runNonce, `${expected.scenario} start nonce mismatch`, evidence);
  requireCondition(started.scenario === expected.scenario, `${expected.scenario} start scenario mismatch`, evidence);
  requireCondition(started.target === expected.target, `${expected.scenario} start target mismatch`, evidence);
  requireCondition(started.model === expected.model, `${expected.scenario} start model mismatch`, evidence);
  requireCondition(started.mode === expected.mode, `${expected.scenario} start mode mismatch`, evidence);
  validateFaultCandidate(started.candidate, expected.candidate, `${expected.scenario} start`, evidence);
  const requestedAt = faultUtcMillis(expected.requestedAtUtc);
  const acceptedAt = faultUtcMillis(started.accepted_at_utc);
  requireCondition(Number.isFinite(acceptedAt), `${expected.scenario} start time is not UTC`, evidence);
  requireCondition(
    acceptedAt >= requestedAt - expected.clockSkewMs && acceptedAt <= expected.nowMs + expected.clockSkewMs,
    `${expected.scenario} start time is outside this request window`,
    evidence,
  );
}

export function validateFaultRunResult(result, expected, evidence = []) {
  requireCondition(result?.schema_version === 1, `${expected.scenario} result schema is not v1`, evidence);
  requireCondition(result?.run_id === expected.runId, `${expected.scenario} result run_id mismatch`, evidence);
  requireCondition(result?.run_nonce === expected.runNonce, `${expected.scenario} result nonce mismatch`, evidence);
  requireCondition(result?.scenario === expected.scenario, `${expected.scenario} result identity mismatch`, evidence);
  requireCondition(result?.target === expected.target, `${expected.scenario} result target mismatch`, evidence);
  requireCondition(result?.model === expected.model, `${expected.scenario} result model mismatch`, evidence);
  requireCondition(result?.mode === expected.mode, `${expected.scenario} result mode mismatch`, evidence);
  validateFaultCandidate(result?.candidate, expected.candidate, `${expected.scenario} result`, evidence);
  requireCondition(result?.status === "PASS", `${expected.scenario} did not produce an explicit PASS`, evidence);

  const requestedAt = faultUtcMillis(expected.requestedAtUtc);
  const acceptedAt = faultUtcMillis(expected.acceptedAtUtc);
  const receivedAt = faultUtcMillis(result?.request_received_at_utc);
  const startedAt = faultUtcMillis(result?.started_at_utc);
  const completedAt = faultUtcMillis(result?.completed_at_utc);
  for (const [label, timestamp] of [
    ["request received", receivedAt],
    ["started", startedAt],
    ["completed", completedAt],
  ]) {
    requireCondition(Number.isFinite(timestamp), `${expected.scenario} ${label} time is not UTC`, evidence);
  }
  requireCondition(
    receivedAt >= requestedAt - expected.clockSkewMs && receivedAt <= acceptedAt + expected.clockSkewMs,
    `${expected.scenario} result is not bound to the accepted request time`,
    evidence,
  );
  requireCondition(
    startedAt >= receivedAt - expected.clockSkewMs && completedAt >= startedAt,
    `${expected.scenario} result timestamps are not monotonic`,
    evidence,
  );
  requireCondition(
    completedAt <= expected.nowMs + expected.clockSkewMs && completedAt - requestedAt <= expected.timeoutMs + expected.clockSkewMs,
    `${expected.scenario} result is outside this execution window`,
    evidence,
  );

  requireCondition(Array.isArray(result?.raw_evidence), `${expected.scenario} omitted raw evidence`, evidence);
  const evidenceByID = new Map();
  for (const entry of result.raw_evidence) {
    requireCondition(entry && typeof entry === "object" && !Array.isArray(entry), `${expected.scenario} returned malformed evidence`, evidence);
    requireCondition(typeof entry.id === "string" && entry.id.length > 0, `${expected.scenario} evidence omitted id`, evidence);
    requireCondition(!evidenceByID.has(entry.id), `${expected.scenario} reused evidence id ${entry.id}`, evidence);
    requireCondition(typeof entry.kind === "string" && entry.kind.length > 0, `${expected.scenario} evidence ${entry.id} omitted kind`, evidence);
    requireCondition(typeof entry.action === "string" && entry.action.length > 0, `${expected.scenario} evidence ${entry.id} omitted action`, evidence);
    requireCondition(
      entry.data && typeof entry.data === "object" && !Array.isArray(entry.data),
      `${expected.scenario} evidence ${entry.id} omitted structured data`,
      evidence,
    );
    const observedAt = faultUtcMillis(entry.observed_at_utc);
    requireCondition(Number.isFinite(observedAt), `${expected.scenario} evidence ${entry.id} time is not UTC`, evidence);
    requireCondition(
      observedAt >= startedAt - expected.clockSkewMs && observedAt <= completedAt + expected.clockSkewMs,
      `${expected.scenario} evidence ${entry.id} is outside the run window`,
      evidence,
    );
    const expectedDigest = `sha256:${sha256(canonicalJson(faultEvidenceCore(entry)))}`;
    requireCondition(
      entry.sha256 === expectedDigest,
      `${expected.scenario} evidence ${entry.id} digest mismatch`,
      evidence,
    );
    evidenceByID.set(entry.id, entry);
  }
  requireCondition(evidenceByID.size > 0, `${expected.scenario} returned no raw evidence`, evidence);

  const requiredAssertions = REQUIRED_FAULT_SCENARIOS[expected.scenario] || [];
  requireCondition(
    result?.assertions && typeof result.assertions === "object" && !Array.isArray(result.assertions),
    `${expected.scenario} omitted assertions`,
    evidence,
  );
  requireCondition(
    result?.assertion_evidence && typeof result.assertion_evidence === "object" && !Array.isArray(result.assertion_evidence),
    `${expected.scenario} omitted assertion evidence mapping`,
    evidence,
  );
  const assertionKeys = Object.keys(result.assertions).sort();
  const mappingKeys = Object.keys(result.assertion_evidence).sort();
  requireCondition(
    canonicalJson(assertionKeys) === canonicalJson([...requiredAssertions].sort()),
    `${expected.scenario} returned an incomplete or unexpected assertion set`,
    evidence,
  );
  requireCondition(
    canonicalJson(mappingKeys) === canonicalJson([...requiredAssertions].sort()),
    `${expected.scenario} returned an incomplete assertion evidence map`,
    evidence,
  );
  for (const assertion of requiredAssertions) {
    requireCondition(result.assertions[assertion] === true, `${expected.scenario} did not prove ${assertion}`, evidence);
    const ids = result.assertion_evidence[assertion];
    requireCondition(
      Array.isArray(ids) && ids.length > 0 && ids.every((id) => typeof id === "string" && evidenceByID.has(id)),
      `${expected.scenario} assertion ${assertion} is not backed by raw evidence`,
      evidence,
    );
  }
  return {
    kind: "fault_run_evidence",
    run_id: result.run_id,
    run_nonce: result.run_nonce,
    scenario: result.scenario,
    target: result.target,
    candidate: result.candidate,
    request_received_at_utc: result.request_received_at_utc,
    started_at_utc: result.started_at_utc,
    completed_at_utc: result.completed_at_utc,
    assertions: result.assertions,
    assertion_evidence: result.assertion_evidence,
    raw_evidence: result.raw_evidence,
  };
}

async function runFaultGates(config, options) {
  const results = {};
  for (const scenario of Object.keys(REQUIRED_FAULT_SCENARIOS)) {
    const id = `fault.${scenario}`;
    if (!options.executeFaults) {
      results[id] = blocked(id, "fault execution was not explicitly enabled");
      continue;
    }
    if (config.environmentClass !== "staging" || config.faultInjection?.confirmIsolatedEnvironment !== true) {
      results[id] = blocked(id, "fault injection requires an explicitly confirmed isolated staging environment");
      continue;
    }
    const controlUrl = config.faultInjection?.controlBaseUrl;
    const tokenEnv = config.faultInjection?.tokenEnv;
    const token = tokenEnv ? options.env[tokenEnv] : null;
    if (!validHttpUrl(controlUrl || "") || !token) {
      results[id] = blocked(id, "fault-injection control plane or its credential is unavailable");
      continue;
    }
    results[id] = await captureGate(id, async () => {
      const evidence = [];
      const runNonce = randomUUID();
      const requestedAtUtc = new Date().toISOString();
      const candidate = faultCandidateIdentity(config);
      const target = "new_api_candidate";
      const started = await requestJson(options.fetchImpl, controlUrl, "/v1/relay-fault-injections", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: {
          schema_version: 1,
          scenario,
          target,
          model: config.testCase.model,
          mode: config.testCase.mode,
          run_nonce: runNonce,
          requested_at_utc: requestedAtUtc,
          candidate,
        },
        timeoutMs: config.faultInjection.requestTimeoutMs ?? 15_000,
      });
      evidence.push(responseEvidence(`${scenario}:start`, started));
      requireCondition([200, 202].includes(started.status), `${scenario} control plane rejected the run`, evidence);
      const startValidation = {
        scenario,
        runNonce,
        target,
        model: config.testCase.model,
        mode: config.testCase.mode,
        candidate,
        requestedAtUtc,
        nowMs: Date.now(),
        clockSkewMs: config.faultInjection.clockSkewMs ?? DEFAULT_FAULT_CLOCK_SKEW_MS,
      };
      validateFaultStart(started.json, startValidation, evidence);
      const deadline = Date.now() + (config.faultInjection.timeoutMs ?? 120_000);
      let result;
      do {
        result = await requestJson(
          options.fetchImpl,
          controlUrl,
          `/v1/relay-fault-injections/${encodeURIComponent(started.json.run_id)}`,
          {
            headers: { Authorization: `Bearer ${token}` },
            timeoutMs: config.faultInjection.requestTimeoutMs ?? 15_000,
          },
        );
        if (["PASS", "FAIL"].includes(result.json?.status)) break;
        await new Promise((resolveWait) => setTimeout(resolveWait, config.faultInjection.pollIntervalMs ?? 500));
      } while (Date.now() < deadline);
      evidence.push(responseEvidence(`${scenario}:result`, result));
      requireCondition(result.status === 200, `${scenario} result lookup returned HTTP ${result.status}`, evidence);
      const faultRunEvidence = validateFaultRunResult(result.json, {
        ...startValidation,
        runId: started.json.run_id,
        acceptedAtUtc: started.json.accepted_at_utc,
        nowMs: Date.now(),
        timeoutMs: config.faultInjection.timeoutMs ?? 120_000,
      }, evidence);
      evidence.push(faultRunEvidence);
      return {
        summary: `${scenario} passed every required invariant through the staging control plane`,
        evidence,
      };
    });
  }
  return results;
}

function presentString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function utcString(value) {
  return presentString(value) && !Number.isNaN(Date.parse(value)) && value.endsWith("Z");
}

function realRecordMissing(record) {
  const missing = [];
  const require = (condition, field) => {
    if (!condition) missing.push(field);
  };
  require(record?.realProvider === true, "realProvider=true");
  require(utcString(record?.executedAtUtc), "executedAtUtc");
  require(presentString(record?.route?.routeId), "route.routeId");
  require(presentString(record?.route?.providerName), "route.providerName");
  require(presentString(record?.route?.channelId), "route.channelId");
  require(CHANNEL_CLASSES.has(record?.route?.channelClass), "route.channelClass");
  require(presentString(record?.route?.accountId), "route.accountId");
  require(REVISION_RE.test(record?.route?.keyFingerprint || ""), "route.keyFingerprint");
  require(presentString(record?.provider?.taskReference), "provider.taskReference");
  require(presentString(record?.provider?.billReference), "provider.billReference");
  require(presentString(record?.obs?.bucket), "obs.bucket");
  require(presentString(record?.obs?.objectKey), "obs.objectKey");
  require(/^[0-9a-f]{64}$/.test(record?.obs?.sha256 || ""), "obs.sha256");
  require(record?.obs?.head?.verified === true, "obs.head.verified=true");
  require(presentString(record?.obs?.head?.etag), "obs.head.etag");
  require(Number.isSafeInteger(record?.obs?.head?.sizeBytes) && record.obs.head.sizeBytes > 0, "obs.head.sizeBytes");
  require(presentString(record?.obs?.head?.contentType), "obs.head.contentType");
  require(utcString(record?.obs?.head?.checkedAtUtc), "obs.head.checkedAtUtc");
  require(presentString(record?.callback?.eventId), "callback.eventId");
  require(record?.callback?.signatureVerified === true, "callback.signatureVerified=true");
  require(utcString(record?.callback?.deliveredAtUtc), "callback.deliveredAtUtc");
  require(presentString(record?.platformWallet?.taskId), "platformWallet.taskId");
  require(presentString(record?.platformWallet?.reservationReference), "platformWallet.reservationReference");
  require(presentString(record?.platformWallet?.settlementReference), "platformWallet.settlementReference");
  require(record?.platformWallet?.action === "settle", "platformWallet.action=settle");
  require(record?.platformWallet?.reconciled === true, "platformWallet.reconciled=true");
  require(Number.isSafeInteger(record?.platformWallet?.amountMinor) && record.platformWallet.amountMinor > 0, "platformWallet.amountMinor");
  require(presentString(record?.providerCost?.ledgerId), "providerCost.ledgerId");
  require(presentString(record?.providerCost?.idempotencyKey), "providerCost.idempotencyKey");
  require(presentString(record?.providerCost?.externalReference), "providerCost.externalReference");
  require(utcString(record?.providerCost?.occurredAtUtc), "providerCost.occurredAtUtc");
  require(Number.isSafeInteger(record?.providerCost?.amountMinor) && record.providerCost.amountMinor >= 0, "providerCost.amountMinor");
  require(record?.providerCost?.appendOnlyVerified === true, "providerCost.appendOnlyVerified=true");
  require(record?.providerCost?.singleEventVerified === true, "providerCost.singleEventVerified=true");
  require(record?.providerCost?.idempotentReplayVerified === true, "providerCost.idempotentReplayVerified=true");
  require(
    record?.providerCost?.idempotencyConflictRejectedVerified === true,
    "providerCost.idempotencyConflictRejectedVerified=true",
  );
  require(
    record?.providerCost?.externalReference === record?.provider?.billReference,
    "providerCost.externalReference matches provider bill",
  );
  require(record?.providerCost?.channelId === record?.route?.channelId, "providerCost.channelId matches route");
  require(record?.providerCost?.channelClass === record?.route?.channelClass, "providerCost.channelClass matches route");
  return missing;
}

function realRecordSafetyErrors(record, config, env) {
  const errors = [];
  const checkKeys = (value, allowed, path) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return;
    for (const key of Object.keys(value)) {
      if (!allowed.includes(key)) errors.push(`${path}.${key} is not an allowed evidence field`);
    }
  };
  checkKeys(
    record,
    ["mode", "realProvider", "executedAtUtc", "route", "provider", "obs", "callback", "platformWallet", "providerCost", "evidenceFiles"],
    "record",
  );
  checkKeys(record?.route, ["routeId", "providerName", "channelId", "channelClass", "accountId", "keyFingerprint"], "route");
  checkKeys(record?.provider, ["taskReference", "billReference"], "provider");
  checkKeys(record?.obs, ["bucket", "objectKey", "sha256", "head"], "obs");
  checkKeys(record?.obs?.head, ["verified", "etag", "sizeBytes", "contentType", "checkedAtUtc"], "obs.head");
  checkKeys(record?.callback, ["eventId", "signatureVerified", "deliveredAtUtc"], "callback");
  checkKeys(
    record?.platformWallet,
    ["taskId", "reservationReference", "settlementReference", "action", "amountMinor", "reconciled"],
    "platformWallet",
  );
  checkKeys(
    record?.providerCost,
    [
      "ledgerId",
      "idempotencyKey",
      "externalReference",
      "occurredAtUtc",
      "amountMinor",
      "channelId",
      "channelClass",
      "appendOnlyVerified",
      "singleEventVerified",
      "idempotentReplayVerified",
      "idempotencyConflictRejectedVerified",
    ],
    "providerCost",
  );
  for (const [index, item] of (record?.evidenceFiles || []).entries()) {
    checkKeys(item, ["label", "path", "expectedSha256"], `evidenceFiles[${index}]`);
  }
  const knownSecrets = [
    ...(config.tenants || []).map((tenant) => env[tenant.apiKeyEnv]),
    config.faultInjection?.tokenEnv ? env[config.faultInjection.tokenEnv] : null,
  ].filter((value) => typeof value === "string" && value.length >= 8);
  const secretPattern = /(?:-----BEGIN [A-Z ]+PRIVATE KEY-----|\bBearer\s+|\bsk-[A-Za-z0-9_-]{12,}|\bAKIA[A-Z0-9]{12,}|(?:password|secret|api[_-]?key|access[_-]?key)=)/i;
  const scan = (value, path) => {
    if (typeof value === "string") {
      if (secretPattern.test(value) || knownSecrets.some((secret) => value.includes(secret))) {
        errors.push(`${path} appears to contain credential material`);
      }
    } else if (Array.isArray(value)) {
      value.forEach((item, index) => scan(item, `${path}[${index}]`));
    } else if (value && typeof value === "object") {
      for (const [key, item] of Object.entries(value)) scan(item, `${path}.${key}`);
    }
  };
  scan(record, "record");
  return errors;
}

function safeRealSummary(record) {
  return {
    mode: record.mode,
    executed_at_utc: record.executedAtUtc,
    route: {
      route_id: record.route.routeId,
      provider_name: record.route.providerName,
      channel_id: record.route.channelId,
      channel_class: record.route.channelClass,
      account_id: record.route.accountId,
      key_fingerprint: record.route.keyFingerprint,
    },
    provider: {
      task_reference: record.provider.taskReference,
      bill_reference: record.provider.billReference,
    },
    obs: {
      bucket: record.obs.bucket,
      object_key: record.obs.objectKey,
      sha256: record.obs.sha256,
      head: {
        etag: record.obs.head.etag,
        size_bytes: record.obs.head.sizeBytes,
        content_type: record.obs.head.contentType,
        checked_at_utc: record.obs.head.checkedAtUtc,
        verified: true,
      },
    },
    callback: {
      event_id: record.callback.eventId,
      delivered_at_utc: record.callback.deliveredAtUtc,
      signature_verified: true,
    },
    platform_wallet: {
      task_id: record.platformWallet.taskId,
      reservation_reference: record.platformWallet.reservationReference,
      settlement_reference: record.platformWallet.settlementReference,
      action: "settle",
      amount_minor: record.platformWallet.amountMinor,
      reconciled: true,
    },
    provider_cost: {
      ledger_id: record.providerCost.ledgerId,
      idempotency_key: record.providerCost.idempotencyKey,
      external_reference: record.providerCost.externalReference,
      occurred_at_utc: record.providerCost.occurredAtUtc,
      amount_minor: record.providerCost.amountMinor,
      channel_id: record.providerCost.channelId,
      channel_class: record.providerCost.channelClass,
      append_only_verified: true,
      single_event_verified: true,
      idempotent_replay_verified: true,
      idempotency_conflict_rejected_verified: true,
    },
  };
}

async function runRealChannelGates(config, options) {
  const gates = {};
  const records = Array.isArray(config.realChannelAcceptance) ? config.realChannelAcceptance : [];
  for (const mode of config.publicModes || []) {
    const id = `real_channel.${mode}`;
    const matches = records.filter((record) => record?.mode === mode);
    if (matches.length !== 1) {
      gates[id] = blocked(id, `exactly one real-provider acceptance record is required for ${mode}`);
      continue;
    }
    const record = matches[0];
    const safetyErrors = realRecordSafetyErrors(record, config, options.env);
    if (safetyErrors.length) {
      gates[id] = fail(id, `real-provider record is not secret-free or has unknown fields (${safetyErrors.length} issue(s))`, [
        { kind: "record_safety", issue_sha256: `sha256:${sha256(canonicalJson(safetyErrors))}` },
      ]);
      continue;
    }
    const missing = realRecordMissing(record);
    if (missing.length) {
      gates[id] = blocked(id, `real-provider evidence is incomplete: ${missing.join(", ")}`);
      continue;
    }
    const evidenceFiles = Array.isArray(record.evidenceFiles) ? record.evidenceFiles : [];
    const labels = new Map(evidenceFiles.map((item) => [item?.label, item]));
    const absent = REQUIRED_REAL_EVIDENCE.filter((label) => !labels.has(label));
    if (absent.length) {
      gates[id] = blocked(id, `evidence files are missing: ${absent.join(", ")}`);
      continue;
    }
    gates[id] = await captureGate(id, async () => {
      const evidence = [{ kind: "real_channel_record", ...safeRealSummary(record) }];
      for (const label of REQUIRED_REAL_EVIDENCE) {
        const item = labels.get(label);
        requireCondition(presentString(item.path), `${mode}/${label} evidence path is missing`);
        requireCondition(REVISION_RE.test(item.expectedSha256 || ""), `${mode}/${label} expected SHA-256 is missing`);
        const absolute = resolve(options.configDir, item.path);
        let bytes;
        try {
          bytes = await readFile(absolute);
        } catch (error) {
          throw new GateError("BLOCKED", `${mode}/${label} evidence file is unavailable: ${compactError(error)}`);
        }
        const actual = `sha256:${sha256(bytes)}`;
        requireCondition(actual === item.expectedSha256, `${mode}/${label} evidence hash mismatch`);
        evidence.push({
          kind: "evidence_file",
          label,
          file_name: basename(absolute),
          bytes: bytes.length,
          sha256: actual,
        });
      }
      return {
        summary: `real ${mode} route, provider billing, OBS, callback, wallet, and cost evidence are complete`,
        evidence,
      };
    });
  }
  return gates;
}

function configEvidence(config, errors) {
  return [{
    kind: "configuration",
    environment: config?.environment || null,
    environment_class: config?.environmentClass || null,
    candidate_git_revision: config?.candidate?.gitRevision || null,
    candidate_upstream_git_revision: config?.candidate?.upstreamGitRevision || null,
    candidate_image_digest: config?.candidate?.imageDigest || null,
    candidate_instance_id: config?.candidate?.instanceId || null,
    public_modes: Array.isArray(config?.publicModes) ? [...config.publicModes] : [],
    validation_error_sha256: errors.length
      ? `sha256:${sha256(canonicalJson(errors))}`
      : null,
  }];
}

export async function runAcceptance(config, options = {}) {
  const resolvedOptions = {
    executeContracts: options.executeContracts === true,
    executeFaults: options.executeFaults === true,
    fetchImpl: options.fetchImpl || globalThis.fetch,
    env: options.env || process.env,
    configDir: options.configDir || process.cwd(),
    now: options.now || (() => new Date()),
  };
  const validationErrors = validateConfig(config);
  const gates = {};
  gates.configuration = validationErrors.length
    ? fail("configuration", "acceptance configuration is invalid", configEvidence(config, validationErrors))
    : pass("configuration", "acceptance configuration is structurally valid", configEvidence(config, []));

  const contractIds = [
    "contract.candidate_readiness",
    "contract.auth",
    "contract.models_etag",
    "contract.strict_fields",
    "contract.request_id",
    "contract.idempotency",
    "contract.tenant_non_enumeration",
    "contract.status_reservation",
    "contract.revision_pin_drift",
  ];
  if (validationErrors.length || !resolvedOptions.executeContracts) {
    const reason = validationErrors.length
      ? "configuration must pass before contract traffic is sent"
      : "contract execution was not explicitly enabled";
    for (const id of contractIds) gates[id] = blocked(id, reason);
  } else {
    Object.assign(gates, await runContractGates(config, resolvedOptions));
  }

  if (validationErrors.length) {
    for (const scenario of Object.keys(REQUIRED_FAULT_SCENARIOS)) {
      const id = `fault.${scenario}`;
      gates[id] = blocked(id, "configuration must pass before fault traffic is sent");
    }
    for (const mode of config?.publicModes || []) {
      const id = `real_channel.${mode}`;
      gates[id] = blocked(id, "configuration must pass before real-channel evidence is accepted");
    }
  } else {
    Object.assign(gates, await runFaultGates(config, resolvedOptions));
    Object.assign(gates, await runRealChannelGates(config, resolvedOptions));
  }

  const requiredGates = Object.values(gates);
  const technicalAcceptancePassed = requiredGates.length > 1 && requiredGates.every((item) => item.status === "PASS");
  const anyFail = requiredGates.some((item) => item.status === "FAIL");
  const generatedAt = resolvedOptions.now().toISOString();
  const report = {
    schema_version: 2,
    report_id: randomUUID(),
    generated_at_utc: generatedAt,
    environment: config?.environment || "unknown",
    environment_class: config?.environmentClass || "unknown",
    candidate: {
      git_revision: config?.candidate?.gitRevision || null,
      upstream_git_revision: config?.candidate?.upstreamGitRevision || null,
      image_digest: config?.candidate?.imageDigest || null,
      instance_id: config?.candidate?.instanceId || null,
    },
    execution: {
      contracts_enabled: resolvedOptions.executeContracts,
      faults_enabled: resolvedOptions.executeFaults,
      mutating_service_actions_performed: false,
      python_relay_changed: false,
    },
    gates,
    overall: {
      status: technicalAcceptancePassed ? "PASS" : anyFail ? "FAIL" : "BLOCKED",
      technical_acceptance_passed: technicalAcceptancePassed,
      decision: technicalAcceptancePassed ? "OFFLINE_PARITY_PASSED_REQUIRES_EXTERNAL_RELEASE_GATES" : "NO-GO",
      active_production_relay: "new-api-v1",
      python_relay_artifact_mode: "offline_historical_oracle_only",
      python_relay_production_admission_allowed: false,
      note: technicalAcceptancePassed
        ? "Offline parity passed. This tool does not authorize a production release; Provider, OBS, IdP, payment, backup/restore, capacity, and signed release evidence remain independent fail-closed gates."
        : "The isolated Python oracle remains only a source/test artifact; unresolved or missing evidence is fail-closed and never authorizes Python production admission.",
    },
  };
  report.integrity = {
    algorithm: "sha256",
    canonical_sha256: `sha256:${sha256(canonicalJson(report))}`,
  };
  return report;
}

export async function writeImmutableReport(path, report) {
  const absolute = resolve(path);
  await mkdir(dirname(absolute), { recursive: true });
  const handle = await open(absolute, "wx", 0o444);
  try {
    await handle.writeFile(`${JSON.stringify(report, null, 2)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  return absolute;
}

function usage() {
  return [
    "Usage:",
    "  node scripts/relay-migration-acceptance.mjs --config <file> --out <new-report.json> [options]",
    "",
    "Options:",
    "  --execute-contracts  Send the bounded /v1 contract comparison traffic.",
    "  --execute-faults     Invoke the configured isolated-staging fault control plane.",
    "  --execute-all        Enable both contract and fault execution.",
    "  --help               Show this help.",
    "",
    "Without execution flags the tool validates configuration/evidence and emits NO-GO.",
    "The output path is create-only. The tool never edits Compose/env, stops services, or deletes data.",
  ].join("\n");
}

function parseArgs(argv) {
  const parsed = { executeContracts: false, executeFaults: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--help") parsed.help = true;
    else if (arg === "--execute-contracts") parsed.executeContracts = true;
    else if (arg === "--execute-faults") parsed.executeFaults = true;
    else if (arg === "--execute-all") {
      parsed.executeContracts = true;
      parsed.executeFaults = true;
    } else if (arg === "--config" || arg === "--out") {
      parsed[arg.slice(2)] = argv[index + 1];
      index += 1;
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return parsed;
}

export async function main(argv = process.argv.slice(2)) {
  let args;
  try {
    args = parseArgs(argv);
  } catch (error) {
    process.stderr.write(`${compactError(error)}\n${usage()}\n`);
    return 2;
  }
  if (args.help) {
    process.stdout.write(`${usage()}\n`);
    return 0;
  }
  if (!args.config || !args.out) {
    process.stderr.write(`${usage()}\n`);
    return 2;
  }
  let config;
  const configPath = resolve(args.config);
  try {
    await access(configPath, fsConstants.R_OK);
    config = JSON.parse(await readFile(configPath, "utf8"));
  } catch (error) {
    process.stderr.write(`cannot read configuration: ${compactError(error)}\n`);
    return 2;
  }
  let report;
  try {
    report = await runAcceptance(config, {
      executeContracts: args.executeContracts,
      executeFaults: args.executeFaults,
      configDir: dirname(configPath),
    });
    const output = await writeImmutableReport(args.out, report);
    process.stdout.write(`${JSON.stringify({ report: output, overall: report.overall }, null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`acceptance report was not written: ${compactError(error)}\n`);
    return 2;
  }
  return report.overall.status === "PASS" ? 0 : 1;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
