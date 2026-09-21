import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { managementSource } from "./management-source.mjs";

import {
  buildCapabilityApprovalPayload,
  buildModelCapabilityReleaseRows,
  capabilityReleasePresentation,
  capabilityReleaseState,
  normalizeCapabilityHistory,
  normalizeRelayCapabilityAudit,
} from "../src/admin/modelCapabilityReleases.js";

const componentSource = await readFile(
  new URL("../src/components/management/PlatformModelReleases.jsx", import.meta.url),
  "utf8",
);
const catalogViewSource = await readFile(
  new URL("../src/components/management/PlatformCatalogViews.jsx", import.meta.url),
  "utf8",
);

const revision = `sha256:${"a".repeat(64)}`;

function model(id) {
  return {
    id,
    slug: id,
    display_name: id,
    capability_version: 3,
    status: "draft",
    active: false,
    effective_capabilities: { schema_version: 1, modes: {} },
  };
}

function relay(overrides = {}) {
  return {
    relay_model_id: overrides.platform_model_id,
    candidate_revision: revision,
    approved_revision: revision,
    requires_approval: false,
    approval_status: "approved",
    capability_diff: { classification: "unchanged", changes: [] },
    ...overrides,
  };
}

test("revision collisions and unknown Relay statuses fail closed", () => {
  const collision = relay({
    platform_model_id: "collision-model",
    status: "revision_collision",
    approval_status: "revision_collision",
  });
  const unknown = relay({
    platform_model_id: "unknown-model",
    status: "future_status",
  });
  const rows = buildModelCapabilityReleaseRows(
    [model("collision-model"), model("unknown-model")],
    { items: [collision, unknown] },
  ).rows;

  assert.equal(capabilityReleaseState(rows[0].model, rows[0].relay), "collision");
  assert.equal(rows[0].canApprove, false);
  assert.equal(rows[0].canPublish, false);
  assert.equal(rows[0].productionReady, false);
  assert.equal(capabilityReleaseState(rows[1].model, rows[1].relay), "unverified");
  assert.equal(rows[1].canApprove, false);
  assert.equal(rows[1].canPublish, false);
  assert.equal(rows[1].productionReady, false);
});

test("release ledger consumes the shared fail-closed presentation mapping", () => {
  assert.deepEqual(capabilityReleasePresentation("collision"), {
    state: "collision",
    label: "能力修订冲突",
    tone: "failed",
  });
  assert.deepEqual(capabilityReleasePresentation("future_state"), {
    state: "unverified",
    label: "Relay 状态待核验",
    tone: "disabled",
  });
  assert.match(componentSource, /capabilityReleasePresentation\(row\.releaseState\)/);
  assert.match(componentSource, /disabled=\{busy \|\| !canManage \|\| !row\.canPublish\}/);
});

test("opening the model catalog remains read-only while manual reconciliation is permission gated", () => {
  const modelsLoadBranch = managementSource.slice(
    managementSource.indexOf('} else if (requestedSection === "models") {', managementSource.indexOf("let identity = data.adminMe")),
    managementSource.indexOf('} else if (requestedSection === "resources") {'),
  );

  assert.match(modelsLoadBranch, /client\.listAdminModels\(\{ signal \}\)/);
  assert.match(modelsLoadBranch, /client\.listAdminRelayModels\(\{ signal \}\)/);
  assert.doesNotMatch(modelsLoadBranch, /reconcileAdminRelayModels|hasUnmappedRelayModel/);

  assert.match(managementSource, /const reconcileRelayModels = async \(\) =>/);
  assert.match(managementSource, /demoMode \|\| !canUsePlatformPermission\("platform\.models\.manage"\)/);
  assert.match(managementSource, /await client\.reconcileAdminRelayModels\(\)/);
  assert.match(componentSource, /onClick=\{reconcileRelayModels\}/);
  assert.match(componentSource, /disabled=\{busy \|\| demoMode \|\| !canManage\}/);
  assert.match(componentSource, /打开本页面不会触发任何写入/);

  assert.match(componentSource, /模型目录对账未完成/);
  assert.match(componentSource, /手动重新同步不会重复创建草稿，也不会自动发布或开放给客户/);
  assert.match(componentSource, /Relay 新公共模型会自动生成未发布草稿/);
  assert.doesNotMatch(componentSource, /建立 Platform 草稿|新建模型草稿/);
  assert.doesNotMatch(catalogViewSource, />\s*新建模型\s*</);
});

test("background candidate history identifies the system worker instead of inventing an administrator", () => {
  const history = normalizeCapabilityHistory([{
    id: "audit-system-1",
    event_type: "candidate_sync",
    actor_user_id: null,
    actor_kind: "system",
    actor_key: "relay-catalog-sync",
    reason: "Relay 模型目录自动对账",
  }]);

  assert.equal(history[0].actorUserId, "");
  assert.equal(history[0].actorKind, "system");
  assert.equal(history[0].actorKey, "relay-catalog-sync");
  assert.match(componentSource, /系统任务 · \$\{entry\.actorKey/);
  assert.match(catalogViewSource, /event\.actor_kind === "system"/);
});

test("Relay route summaries remain secret-free and selectable by exact channel identity", () => {
  const audit = normalizeRelayCapabilityAudit({
    items: [{
      relay_model_id: "seedance-public-alias-with-a-deliberately-long-production-name",
      capability_revision: revision,
      route_evidence_status: "blocked",
      route_count: 1,
      enabled_route_count: 0,
      accepted_route_count: 1,
      fresh_test_count: 0,
      routes: [{
        route_id: "volcengine-seedance-primary-01",
        channel_id: 17,
        upstream_model: "doubao-seedance-1-5-pro-250528",
        adapter_profile_id: "volcengine-seedance-v1",
        adapter_profile_revision: "sha256:profile-revision-001",
        enabled: false,
        accepted: true,
        fresh: false,
        required_test_modes: ["text_to_video", "image_to_video"],
        fresh_test_modes: ["text_to_video"],
      }],
    }],
  });

  assert.deepEqual(audit.items[0].routes, [{
    routeId: "volcengine-seedance-primary-01",
    channelId: 17,
    upstreamModel: "doubao-seedance-1-5-pro-250528",
    adapterProfileId: "volcengine-seedance-v1",
    adapterProfileRevision: "sha256:profile-revision-001",
    enabled: false,
    accepted: true,
    fresh: false,
    requiredTestModes: ["text_to_video", "image_to_video"],
    freshTestModes: ["text_to_video"],
  }]);
  assert.equal(audit.items[0].acceptedRouteCount, 1);
  assert.equal(audit.items[0].enabledRouteCount, 0);
  assert.doesNotMatch(JSON.stringify(audit), /credential|fingerprint|api[_-]?key|provider_url/i);
});

test("candidate sync omits the approval-only routing release while approval requires it", () => {
  const routingRelease = `sha256:${"c".repeat(64)}`;
  const relayState = {
    candidateRevision: revision,
    routingReleaseSha256: routingRelease,
    routeEvidenceStatus: "ready",
    routeEvidenceBlockers: [],
  };
  const common = {
    model: model("release-contract"),
    relay: relayState,
    catalogRevision: `sha256:${"b".repeat(64)}`,
    reason: "核验候选能力与路由证据",
  };

  const syncPayload = buildCapabilityApprovalPayload({
    ...common,
    requireRouteEvidence: false,
  });
  assert.equal(syncPayload.expectedRoutingReleaseSha256, undefined);

  const approvalPayload = buildCapabilityApprovalPayload({
    ...common,
    requireRouteEvidence: true,
  });
  assert.equal(approvalPayload.expectedRoutingReleaseSha256, routingRelease);
});

test("candidate sync and approval reject reasons shorter than the backend contract", () => {
  const common = {
    model: model("release-reason-contract"),
    relay: {
      candidateRevision: revision,
      routingReleaseSha256: `sha256:${"c".repeat(64)}`,
      routeEvidenceStatus: "ready",
      routeEvidenceBlockers: [],
    },
    catalogRevision: `sha256:${"b".repeat(64)}`,
    reason: "短",
  };

  assert.throws(
    () => buildCapabilityApprovalPayload({ ...common, requireRouteEvidence: false }),
    /至少需要 3 个字符/,
  );
  assert.throws(
    () => buildCapabilityApprovalPayload({ ...common, requireRouteEvidence: true }),
    /至少需要 3 个字符/,
  );
  assert.throws(
    () => buildCapabilityApprovalPayload({
      ...common,
      reason: "🎬🚀",
      requireRouteEvidence: false,
    }),
    /至少需要 3 个字符/,
  );
});
