import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

// Synthetic Platform discovery, never a production offer or provider proof.
// Every model identity and capability comes from the reviewed Relay manifest.
export const MINIMAX_H3_CATALOG = JSON.parse(readFileSync(new URL(
  "../../backend/new-api-relay/generationprofile/minimax_h3_models.v1.json", import.meta.url,
), "utf8"));

export function minimaxH3DiscoveryResponses({ publicModelIds, readiness = "ready" } = {}) {
  const allowed = publicModelIds ? new Set(publicModelIds) : null;
  return MINIMAX_H3_CATALOG.models.filter((model) => (
    model.lifecycle === "acceptance_candidate" && (!allowed || allowed.has(model.public_model_id))
  )).map((model, index) => ({
    id: `cccccccc-0000-4000-8001-${String(index + 1).padStart(12, "0")}`,
    slug: model.public_model_id,
    display_name: model.display_name,
    pricing_mode: "per_second",
    billing_unit: "POINT",
    billing_version: 2,
    billing_scope: "company",
    unit_price_points: 20 + index,
    capability_version: 8,
    quote_revision: `sha256:${createHash("sha256").update(`minimax-h3-fixture:${model.public_model_id}`).digest("hex")}`,
    effective_capabilities: structuredClone(model.capability),
    ...(readiness === "missing" ? {} : {
      readiness_checked_at: "2026-08-31T08:00:00Z",
      mode_readiness: Object.fromEntries(Object.keys(model.capability.modes).map((mode) => [mode, {
        default: {
          ready: readiness === "ready",
          status: readiness === "ready" ? "ready" : "blocked",
          blockers: readiness === "ready" ? [] : [{
            code: "model_grant_disabled", message: "当前企业尚未获准使用此模型。", retryable: false,
          }],
        },
        options: { face_enabled: { supported: false, ready: false, status: "unsupported", blockers: [] } },
      }])),
    }),
  }));
}

export function minimaxH3ModelResponse(publicModelId) {
  const model = minimaxH3DiscoveryResponses().find((entry) => entry.slug === publicModelId);
  if (!model) throw new Error(`Missing MiniMax H3 test model: ${publicModelId}`);
  return model;
}
