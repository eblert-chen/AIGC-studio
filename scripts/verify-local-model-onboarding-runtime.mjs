#!/usr/bin/env node

/**
 * Read-only local runtime smoke for reviewed video-model onboarding.
 *
 * This script never starts or restarts Compose, never changes a database and
 * never reads a provider credential into the host process.  The authenticated
 * Relay catalog read runs inside the existing Platform catalog-sync container
 * and prints only model ids and immutable capability revisions.
 *
 * Usage:
 *   node scripts/verify-local-model-onboarding-runtime.mjs --plan
 *   node scripts/verify-local-model-onboarding-runtime.mjs --check
 *
 * Container names can be overridden without changing the script:
 *   MODEL_SMOKE_RELAY_DB_CONTAINER
 *   MODEL_SMOKE_PLATFORM_DB_CONTAINER
 *   MODEL_SMOKE_CATALOG_SYNC_CONTAINER
 */

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const workspace = resolve(import.meta.dirname, "..");
const manifests = Object.freeze([
  {
    family: "volcengine",
    path: "backend/new-api-relay/generationprofile/seedance_models.v1.json",
  },
  {
    family: "minimax",
    path: "backend/new-api-relay/generationprofile/minimax_h3_models.v1.json",
  },
  {
    family: "google",
    path: "backend/new-api-relay/generationprofile/google_video_models.v1.json",
  },
]);

const identifierPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

function fail(message) {
  throw new Error(message);
}

function loadReviewedCandidates() {
  const candidates = [];
  for (const manifest of manifests) {
    const document = JSON.parse(
      readFileSync(resolve(workspace, manifest.path), "utf8"),
    );
    if (document?.schema_version !== 1 || !Array.isArray(document.models)) {
      fail(`${manifest.path} is not a reviewed schema-v1 model manifest`);
    }
    for (const model of document.models) {
      if (
        model?.lifecycle !== "acceptance_candidate" ||
        model?.new_routes_allowed !== true
      ) {
        continue;
      }
      const publicId = model.public_model_id;
      const providerId = model.provider_model_id;
      if (
        !identifierPattern.test(publicId ?? "") ||
        !identifierPattern.test(providerId ?? "")
      ) {
        fail(`${manifest.path} contains a non-canonical model identity`);
      }
      candidates.push({
        family: manifest.family,
        public_id: publicId,
        provider_id: providerId,
      });
    }
  }
  const publicIds = candidates.map((candidate) => candidate.public_id);
  if (candidates.length === 0 || new Set(publicIds).size !== publicIds.length) {
    fail("reviewed model manifests contain no candidates or duplicate public ids");
  }
  return candidates.sort((left, right) =>
    left.public_id.localeCompare(right.public_id),
  );
}

function sqlStrings(values) {
  for (const value of values) {
    if (!identifierPattern.test(value)) fail("unsafe SQL model identity");
  }
  return values.map((value) => `'${value}'`).join(",");
}

function docker(container, args, { maxBuffer = 8 * 1024 * 1024 } = {}) {
  return execFileSync("docker", ["exec", container, ...args], {
    cwd: workspace,
    encoding: "utf8",
    maxBuffer,
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

function postgresJson(container, user, database, sql) {
  const output = docker(container, [
    "psql",
    "-X",
    "-v",
    "ON_ERROR_STOP=1",
    "-U",
    user,
    "-d",
    database,
    "-At",
    "-c",
    sql,
  ]);
  return JSON.parse(output || "null");
}

function readRelayCatalog(container) {
  // The existing process-local secret loader and RelayClient own the service
  // credential.  Neither the command line nor stdout contains the credential.
  const program = [
    "import json",
    "from platform_api.config import get_settings,runtime_settings_are_protected",
    "from platform_api.relay_backends import build_relay_backend_registry",
    "s=get_settings('relay-catalog-sync')",
    "r=build_relay_backend_registry(default_backend_id=s.relay_default_backend_id,default_contract_revision=s.relay_default_contract_revision,configurations=s.relay_backends,legacy_base_url=s.relay_base_url,legacy_client_id=s.relay_client_id,legacy_api_key=s.relay_api_key,allow_local_http=not runtime_settings_are_protected(s),legacy_compatibility_enabled=s.relay_legacy_compatibility_enabled)",
    "c=r.default_client_or_none()",
    "x=c.get_model_catalog(request_id='read-only-model-onboarding-smoke')",
    "print(json.dumps({'catalog_revision':x.catalog.catalog_revision,'models':[{'id':m.id,'capability_revision':m.capability_revision} for m in x.catalog.data]},sort_keys=True,separators=(',',':')))",
    "r.close()",
  ].join("; ");
  return JSON.parse(docker(container, ["python", "-c", program]));
}

function assertExpectedIds(label, expected, actual) {
  const missing = expected.filter((id) => !actual.includes(id));
  if (missing.length > 0) {
    fail(`${label} is missing reviewed candidates: ${missing.join(", ")}`);
  }
}

function checkRuntime(candidates) {
  const relayDatabaseContainer =
    process.env.MODEL_SMOKE_RELAY_DB_CONTAINER ||
    "ai-video-relay-new-api-postgres-1";
  const platformDatabaseContainer =
    process.env.MODEL_SMOKE_PLATFORM_DB_CONTAINER || "ai-video-postgres-1";
  const catalogSyncContainer =
    process.env.MODEL_SMOKE_CATALOG_SYNC_CONTAINER ||
    "ai-video-platform-relay-catalog-sync-1";
  const expectedIds = candidates.map((candidate) => candidate.public_id);
  const expectedSql = sqlStrings(expectedIds);

  const nativeModels = postgresJson(
    relayDatabaseContainer,
    "new_api",
    "new_api",
    `select coalesce(json_agg(row_to_json(candidate) order by candidate.model_name),'[]'::json)::text from (select model_name,status,sync_official,deleted_at from models where model_name in (${expectedSql})) candidate;`,
  );
  assertExpectedIds(
    "Relay native models",
    expectedIds,
    nativeModels.map((model) => model.model_name),
  );
  const unsafeNative = nativeModels.filter(
    (model) =>
      model.status !== 0 ||
      model.sync_official !== 0 ||
      model.deleted_at !== null,
  );
  if (unsafeNative.length > 0) {
    fail(
      `reviewed Relay native candidates must remain visible but disabled: ${unsafeNative
        .map((model) => model.model_name)
        .join(", ")}`,
    );
  }

  const enabledBindings = postgresJson(
    relayDatabaseContainer,
    "new_api",
    "new_api",
    `select json_build_object('abilities',coalesce((select json_agg(model order by model) from abilities where enabled is true and model in (${expectedSql})),'[]'::json),'routes',coalesce((select json_agg(model order by model) from platform_generation_provider_routes where enabled is true and model in (${expectedSql})),'[]'::json))::text;`,
  );
  if (enabledBindings.abilities.length || enabledBindings.routes.length) {
    fail("reviewed candidates became executable before route acceptance");
  }

  const relayCatalog = readRelayCatalog(catalogSyncContainer);
  const relayRevisions = new Map(
    relayCatalog.models.map((model) => [model.id, model.capability_revision]),
  );
  assertExpectedIds(
    "Relay /v1/models",
    expectedIds,
    relayCatalog.models.map((model) => model.id),
  );

  const platformDrafts = postgresJson(
    platformDatabaseContainer,
    "ai_video",
    "ai_video_platform",
    `select coalesce(json_agg(row_to_json(draft) order by draft.slug),'[]'::json)::text from (select m.id,m.slug,m.active,m.published_at,m.relay_capability_revision,m.relay_capability_candidate_revision,exists(select 1 from audit_logs a where a.target_id=m.id and a.actor_kind='system' and a.actor_key='relay-catalog-sync' and a.action in ('model.create','model.relay_capability.candidate_sync')) as catalog_sync_audited from model_definitions m where m.slug in (${expectedSql})) draft;`,
  );
  assertExpectedIds(
    "Platform catalog-sync drafts",
    expectedIds,
    platformDrafts.map((model) => model.slug),
  );
  for (const draft of platformDrafts) {
    if (draft.active || draft.published_at !== null) {
      fail(`${draft.slug} is no longer an unpublished onboarding draft`);
    }
    if (draft.relay_capability_revision !== null) {
      fail(`${draft.slug} has an approved Relay revision before approval`);
    }
    if (
      draft.relay_capability_candidate_revision !== relayRevisions.get(draft.slug)
    ) {
      fail(`${draft.slug} Platform candidate revision does not match Relay`);
    }
    if (!draft.catalog_sync_audited) {
      fail(`${draft.slug} lacks catalog-sync system audit evidence`);
    }
  }

  const personalState = postgresJson(
    platformDatabaseContainer,
    "ai_video",
    "ai_video_platform",
    `select json_build_object('usable',coalesce((select json_agg(m.slug order by m.slug) from model_definitions m join personal_retail_model_grants g on g.model_id=m.id where m.active is true and m.published_at is not null and m.relay_capability_revision is not null and m.relay_capability_approved_ceiling is not null and m.relay_capability_candidate_revision=m.relay_capability_revision and g.enabled is true and ((m.billing_mode='per_second' and g.price_per_second_points>0 and g.price_per_item_points is null) or (m.billing_mode='per_item' and g.price_per_item_points>0 and g.price_per_second_points is null))),'[]'::json),'pending_preview',coalesce((select json_agg(m.slug order by m.slug) from model_definitions m where m.slug in (${expectedSql}) and m.active is false and m.published_at is null and m.relay_capability_candidate_revision is not null and m.relay_capability_revision is null),'[]'::json))::text;`,
  );
  const prematurelyUsable = personalState.usable.filter((id) =>
    expectedIds.includes(id),
  );
  if (prematurelyUsable.length > 0) {
    fail(
      `onboarding candidates leaked into personal usable models: ${prematurelyUsable.join(", ")}`,
    );
  }
  assertExpectedIds(
    "personal integration preview",
    expectedIds,
    personalState.pending_preview,
  );

  return {
    status: "PASS",
    read_only: true,
    relay_catalog_revision: relayCatalog.catalog_revision,
    reviewed_candidates: candidates,
    relay_native_disabled: nativeModels.map((model) => model.model_name),
    relay_catalog_models: relayCatalog.models.map((model) => model.id),
    platform_catalog_sync_drafts: platformDrafts.map((model) => model.slug),
    personal_usable_models: personalState.usable,
    personal_integration_preview: personalState.pending_preview,
  };
}

function main() {
  const [mode, extra] = process.argv.slice(2);
  if (extra || !["--plan", "--check"].includes(mode)) {
    console.error(
      "Usage: node scripts/verify-local-model-onboarding-runtime.mjs --plan|--check",
    );
    process.exitCode = 2;
    return;
  }
  const candidates = loadReviewedCandidates();
  const result =
    mode === "--plan"
      ? {
          status: "PLAN_ONLY",
          read_only: true,
          checks: [
            "Relay native candidate rows are present, disabled and unbound",
            "Relay /v1/models exposes exact immutable candidate revisions",
            "Platform catalog-sync creates audited unpublished drafts",
            "personal usable models contain only published, approved, priced grants",
            "reviewed candidates remain visible as integration previews",
          ],
          reviewed_candidates: candidates,
        }
      : checkRuntime(candidates);
  console.log(JSON.stringify(result, null, 2));
}

try {
  main();
} catch (error) {
  console.error(`model onboarding runtime smoke failed: ${error.message}`);
  process.exitCode = 1;
}
