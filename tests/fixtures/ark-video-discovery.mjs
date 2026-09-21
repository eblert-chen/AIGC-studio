import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

// Test-only Platform responses. Capabilities come from the single reviewed
// Relay catalog; the synthetic workspace grants/prices below are not releases,
// retail offers, real provider readiness, or production authorization.
export const ARK_VIDEO_CATALOG = JSON.parse(readFileSync(new URL(
  "../../backend/new-api-relay/generationprofile/seedance_models.v1.json",
  import.meta.url,
), "utf8"));

export const ARK_VIDEO_CURRENT_MODELS = ARK_VIDEO_CATALOG.models.filter((model) => (
  ["acceptance_candidate", "deprecated"].includes(model.lifecycle)
  && model.capability?.modes
));

export const ARK_TEST_COMPANY_ID = "aaaaaaaa-0000-4000-8000-000000000001";
export const ARK_TEST_USER_ID = "bbbbbbbb-0000-4000-8000-000000000001";

export function arkDiscoveryResponses({
  publicModelIds = ARK_VIDEO_CURRENT_MODELS.map((model) => model.public_model_id),
  readiness = "ready",
} = {}) {
  const allowed = new Set(publicModelIds);
  return ARK_VIDEO_CURRENT_MODELS.filter((model) => allowed.has(model.public_model_id))
    .map((model, index) => ({
      id: `cccccccc-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
      slug: model.public_model_id,
      display_name: model.display_name,
      pricing_mode: "per_second",
      billing_unit: "POINT",
      billing_version: 2,
      billing_scope: "company",
      unit_price_points: 10 + index,
      capability_version: 7,
      quote_revision: `sha256:${createHash("sha256")
        .update(`ark-browser-fixture:${model.public_model_id}:${index}`)
        .digest("hex")}`,
      effective_capabilities: structuredClone(model.capability),
      ...(readiness === "missing" ? {} : {
        readiness_checked_at: "2026-08-31T08:00:00Z",
        mode_readiness: Object.fromEntries(Object.keys(model.capability.modes).map((mode) => [
          mode,
          {
            default: {
              ready: readiness === "ready",
              status: readiness === "ready" ? "ready" : "blocked",
              blockers: readiness === "ready" ? [] : [{
                code: "model_grant_disabled",
                message: "当前企业尚未获准使用此模型。",
                retryable: false,
              }],
            },
            options: {
              face_enabled: {
                supported: false,
                ready: false,
                status: "unsupported",
                blockers: [],
              },
            },
          },
        ])),
      }),
    }));
}

export function arkModelResponse(publicModelId) {
  const model = arkDiscoveryResponses().find((candidate) => candidate.slug === publicModelId);
  if (!model) throw new Error(`Missing current Ark test model: ${publicModelId}`);
  return model;
}
