#!/usr/bin/env node

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  assertSecretFreeEvidence,
  harnessSourceSnapshot,
  relaySourceSnapshot,
} from "./relay-fault-source-snapshot.mjs";
import { CANDIDATE_UPSTREAM_GIT_REVISION } from "./relay-migration-acceptance.mjs";
import { platformSourceSnapshot } from "./run-cross-service-cost-acceptance.mjs";

const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const candidateGateNames = Object.freeze([
  "local_regression",
  "postgresql_redis_race",
  "fault_injection",
  "cross_service_cost",
  "huawei_obs_live",
  "real_provider_staging",
]);
const candidateBaselineNote = "This baseline freezes current source and harness provenance only; it contains no PASS claim and never authorizes the offline Python oracle for production admission.";
const candidateBaselineKeys = Object.freeze([
  "schema_version",
  "kind",
  "generated_at_utc",
  "status",
  "production_cutover_authorized",
  "active_production_relay",
  "python_relay_artifact_mode",
  "python_relay_production_admission_allowed",
  "upstream_git_revision",
  "source",
  "platform_source",
  "acceptance_harness",
  "gates",
  "note",
]);
const candidateSnapshotKeys = Object.freeze(["format", "sha1", "sha256", "file_count"]);
export const candidateBaselinePath = resolve(
  workspace,
  "artifacts",
  "relay-candidate-current.json",
);

export async function buildCandidateBaseline(generatedAt = new Date().toISOString()) {
  const [source, platformSource, harness] = await Promise.all([
    relaySourceSnapshot(),
    platformSourceSnapshot(),
    harnessSourceSnapshot(),
  ]);
  return {
    schema_version: 3,
    kind: "ai_video_new_api_relay_candidate_baseline",
    generated_at_utc: generatedAt,
    status: "UNVERIFIED",
    production_cutover_authorized: false,
    active_production_relay: "new-api-v1",
    python_relay_artifact_mode: "offline_historical_oracle_only",
    python_relay_production_admission_allowed: false,
    upstream_git_revision: CANDIDATE_UPSTREAM_GIT_REVISION,
    source: {
      format: source.format,
      sha1: source.sha1,
      sha256: source.sha256,
      file_count: source.file_count,
    },
    platform_source: {
      format: platformSource.format,
      sha1: platformSource.sha1,
      sha256: platformSource.sha256,
      file_count: platformSource.file_count,
    },
    acceptance_harness: {
      format: harness.format,
      sha1: harness.sha1,
      sha256: harness.sha256,
      file_count: harness.file_count,
    },
    gates: Object.fromEntries(candidateGateNames.map((name) => [name, "NOT_RUN"])),
    note: candidateBaselineNote,
  };
}

function validateExactKeys(value, expectedKeys, label, errors) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    errors.push(`${label} must be an object`);
    return false;
  }
  for (const key of expectedKeys) {
    if (!Object.hasOwn(value, key)) errors.push(`${label}.${key} is missing`);
  }
  for (const key of Object.keys(value)) {
    if (!expectedKeys.includes(key)) errors.push(`${label}.${key} is not recognized`);
  }
  return true;
}

export function validateCandidateBaseline(persisted, current) {
  const errors = [];
  validateExactKeys(persisted, candidateBaselineKeys, "baseline", errors);
  if (persisted?.schema_version !== 3) errors.push("schema_version must be 3");
  if (persisted?.kind !== "ai_video_new_api_relay_candidate_baseline") {
    errors.push("kind is invalid");
  }
  if (persisted?.status !== "UNVERIFIED") errors.push("status must remain UNVERIFIED");
  if (persisted?.production_cutover_authorized !== false) {
    errors.push("candidate baseline cannot authorize production cutover");
  }
  if (persisted?.active_production_relay !== "new-api-v1") {
    errors.push("active_production_relay must equal new-api-v1");
  }
  if (persisted?.python_relay_artifact_mode !== "offline_historical_oracle_only") {
    errors.push("python_relay_artifact_mode must equal offline_historical_oracle_only");
  }
  if (persisted?.python_relay_production_admission_allowed !== false) {
    errors.push("python_relay_production_admission_allowed must equal false");
  }
  if (persisted?.note !== candidateBaselineNote) {
    errors.push("note must equal the canonical non-authorization statement");
  }
  const generatedAt = persisted?.generated_at_utc;
  const parsedGeneratedAt = typeof generatedAt === "string"
    ? new Date(generatedAt)
    : null;
  if (
    typeof generatedAt !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(generatedAt) ||
    parsedGeneratedAt === null ||
    Number.isNaN(parsedGeneratedAt.getTime()) ||
    parsedGeneratedAt.toISOString() !== generatedAt
  ) {
    errors.push("generated_at_utc is invalid");
  }
  for (const section of ["source", "platform_source", "acceptance_harness"]) {
    validateExactKeys(persisted?.[section], candidateSnapshotKeys, section, errors);
    for (const field of candidateSnapshotKeys) {
      if (persisted?.[section]?.[field] !== current?.[section]?.[field]) {
        errors.push(`${section}.${field} does not match the current workspace`);
      }
    }
  }
  if (persisted?.upstream_git_revision !== current?.upstream_git_revision) {
    errors.push("upstream_git_revision does not match the pinned migration revision");
  }
  const persistedGates = persisted?.gates;
  if (!persistedGates || typeof persistedGates !== "object" || Array.isArray(persistedGates)) {
    errors.push("gates must be an object");
  } else {
    for (const gate of candidateGateNames) {
      if (!Object.hasOwn(persistedGates, gate)) {
        errors.push(`baseline gate ${gate} is missing`);
      } else if (persistedGates[gate] !== "NOT_RUN") {
        errors.push(`baseline gate ${gate} cannot claim ${persistedGates[gate]}`);
      }
    }
    for (const gate of Object.keys(persistedGates)) {
      if (!candidateGateNames.includes(gate)) {
        errors.push(`baseline gate ${gate} is not recognized`);
      }
    }
  }
  return errors;
}

export async function writeCandidateBaseline(path = candidateBaselinePath) {
  const baseline = await buildCandidateBaseline();
  assertSecretFreeEvidence(baseline);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(baseline, null, 2)}\n`, "utf8");
  return baseline;
}

export async function checkCandidateBaseline(path = candidateBaselinePath) {
  const persisted = JSON.parse(await readFile(path, "utf8"));
  const current = await buildCandidateBaseline(persisted.generated_at_utc);
  assertSecretFreeEvidence(persisted);
  return validateCandidateBaseline(persisted, current);
}

async function main() {
  const [mode, extra] = process.argv.slice(2);
  if (extra || !["--write", "--check"].includes(mode)) {
    throw new Error("usage: relay-candidate-baseline.mjs --write|--check");
  }
  if (mode === "--write") {
    const baseline = await writeCandidateBaseline();
    process.stdout.write(
      `wrote ${candidateBaselinePath}\nsource=${baseline.source.sha256}\nplatform=${baseline.platform_source.sha256}\nharness=${baseline.acceptance_harness.sha256}\n`,
    );
    return;
  }
  const errors = await checkCandidateBaseline();
  if (errors.length) throw new Error(errors.join("; "));
  process.stdout.write("candidate baseline matches current Relay, Platform, and harness sources\n");
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
