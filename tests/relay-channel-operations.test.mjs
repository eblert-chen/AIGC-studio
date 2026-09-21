import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  adaptRelayChannelOperation,
  buildRelayChannelOperationRequest,
  readRelayChannelOperation,
  relayChannelTestErrorLabel,
  runRelayChannelOperationWithReadback,
} from "../src/admin/relayChannelOperations.js";

const relayDrawerSource = await readFile(
  new URL("../src/admin/operations/OperationsDrawers.jsx", import.meta.url),
  "utf8",
);
const platformGatewaySource = await readFile(
  new URL("../infra/nginx/platform-api.conf", import.meta.url),
  "utf8",
);

const channelId = 17;
const reason = "Approved connectivity verification";
const operationId = "relay-channel-test-operation-0001";
const routeIdentity = Object.freeze({
  publicModelId: "seedance-1.5-pro",
  routeId: "volcengine-seedance-primary-01",
});

function receipt(overrides = {}) {
  return {
    api_version: "v1",
    schema_version: 1,
    object: "relay.channel_control_operation",
    operation_id: operationId,
    tenant_id: "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
    channel_id: channelId,
    kind: "test",
    state: "succeeded",
    actor: "admin-1",
    reason,
    request_id: "request-1",
    intent_sha256: "f".repeat(64),
    previous_revision: null,
    result_revision: null,
    expected_revision: null,
    target_status: null,
    result: { success: true, response_time_ms: 413, error_code: null },
    created_at: "2026-08-14T00:00:00Z",
    completed_at: "2026-08-14T00:00:01Z",
    idempotent_replay: false,
    ...overrides,
  };
}

test("channel approval request exposes only the exact public test fields", () => {
  assert.deepEqual(buildRelayChannelOperationRequest("test", {
    operationId,
    reason: `  ${reason}  `,
    approved: true,
    ...routeIdentity,
    model: "must-not-cross-facade",
    endpoint: "/v1/models",
    stream: true,
  }), {
    operationId,
    reason,
    approved: true,
    ...routeIdentity,
  });
  assert.throws(() => buildRelayChannelOperationRequest("test", {
    operationId,
    reason,
    approved: true,
  }), /必须选择受控的 public_model_id 和 route_id/);
  assert.throws(() => buildRelayChannelOperationRequest("status", {
    operationId,
    reason,
    approved: true,
    expectedRevision: `sha256:${"a".repeat(64)}`,
    targetStatus: "auto_disabled",
  }), /只能切换为启用或手动停用/);
});

test("route-bound channel approval carries an exact public model and route pair", () => {
  assert.deepEqual(buildRelayChannelOperationRequest("test", {
    operationId,
    reason,
    approved: true,
    publicModelId: "seedance-1.5-pro",
    routeId: "volcengine-seedance-primary-01",
  }), {
    operationId,
    reason,
    approved: true,
    publicModelId: "seedance-1.5-pro",
    routeId: "volcengine-seedance-primary-01",
  });
  assert.throws(() => buildRelayChannelOperationRequest("test", {
    operationId,
    reason,
    approved: true,
    publicModelId: "seedance-1.5-pro",
  }), /必须选择受控的 public_model_id 和 route_id/);
  assert.throws(() => buildRelayChannelOperationRequest("test", {
    operationId,
    reason,
    approved: true,
    publicModelId: "seedance-1.5-pro",
    routeId: "route with spaces",
  }), /格式无效/);

  assert.deepEqual(buildRelayChannelOperationRequest("test", {
    operationId,
    reason,
    approved: true,
    publicModelId: "veo-3.1",
    routeId: "google-veo-primary-01",
    mode: "image_to_video",
  }), {
    operationId,
    reason,
    approved: true,
    publicModelId: "veo-3.1",
    routeId: "google-veo-primary-01",
    mode: "image_to_video",
  });
  assert.throws(() => buildRelayChannelOperationRequest("test", {
    operationId,
    reason,
    approved: true,
    publicModelId: "veo-3.1",
    routeId: "google-veo-primary-01",
    mode: "audio_to_video",
  }), /测试模式无效/);
});

test("route test UI selects a secret-free Relay summary and warns about real provider cost", () => {
  assert.match(relayDrawerSource, /routeOptions = \[\]/);
  assert.match(relayDrawerSource, /String\(route\.channelId\) === String\(channel\.id\)/);
  assert.match(relayDrawerSource, /<select value=\{selectedRouteKey\}/);
  assert.match(relayDrawerSource, /route\.publicModelId\} → \{route\.upstreamModel\} · \{route\.routeId/);
  assert.match(relayDrawerSource, /Platform 不接受手填 route_id/);
  assert.match(relayDrawerSource, /页面不提供手填 route_id/);
  assert.match(relayDrawerSource, /真实低配生成，可能产生供应商费用；失败不会解锁模型发布/);
  assert.doesNotMatch(relayDrawerSource, /name=["'](?:public_model_id|route_id)["']/);
});

test("gateway extends timeout only for the exact paid Relay route-test endpoint", () => {
  assert.match(
    platformGatewaySource,
    /location ~ \^\/api\/v1\/platform-admin\/relay\/channels\/\[1-9\]\[0-9\]\*\/test\$ \{[\s\S]*?proxy_read_timeout 660s;/,
  );
  assert.equal(
    platformGatewaySource.match(/proxy_read_timeout 660s;/g)?.length,
    1,
  );
  assert.match(platformGatewaySource, /proxy_read_timeout 60s;/);
  assert.match(platformGatewaySource, /location \/api\/ \{/);
});

test("an ambiguous POST is attempted once and reconciled by GET with the same operation id", async () => {
  const request = buildRelayChannelOperationRequest("test", {
    operationId, reason, approved: true, ...routeIdentity,
  });
  let submitCount = 0;
  let readCount = 0;
  const result = await runRelayChannelOperationWithReadback({
    channelId,
    kind: "test",
    request,
    submit: async () => {
      submitCount += 1;
      throw Object.assign(new Error("outcome unknown"), { status: 503 });
    },
    getResult: async (readChannelId, readOperationId) => {
      readCount += 1;
      assert.equal(readChannelId, channelId);
      assert.equal(readOperationId, operationId);
      return receipt();
    },
  });
  assert.equal(submitCount, 1);
  assert.equal(readCount, 1);
  assert.equal(result.confirmation, "result_readback");
  assert.equal(result.receipt.operationId, operationId);
});

test("Platform machine codes distinguish preflight failure from an ambiguous accepted operation", async () => {
  const request = buildRelayChannelOperationRequest("test", {
    operationId, reason, approved: true, ...routeIdentity,
  });
  const notStarted = Object.assign(new Error("Relay preflight failed"), {
    status: 503,
    code: "RELAY_CHANNEL_OPERATION_NOT_STARTED",
  });
  let preflightReadCount = 0;
  await assert.rejects(
    runRelayChannelOperationWithReadback({
      channelId,
      kind: "test",
      request,
      submit: async () => { throw notStarted; },
      getResult: async () => { preflightReadCount += 1; },
    }),
    (error) => error === notStarted && error.relayChannelReadbackRequired !== true,
  );
  assert.equal(preflightReadCount, 0);

  const outcomeUnknown = Object.assign(new Error("Relay outcome is unknown"), {
    status: 503,
    code: "RELAY_CHANNEL_OPERATION_OUTCOME_UNKNOWN",
  });
  let ambiguousReadCount = 0;
  const result = await runRelayChannelOperationWithReadback({
    channelId,
    kind: "test",
    request,
    submit: async () => { throw outcomeUnknown; },
    getResult: async () => {
      ambiguousReadCount += 1;
      return receipt();
    },
  });
  assert.equal(ambiguousReadCount, 1);
  assert.equal(result.confirmation, "result_readback");
});

test("a status CAS conflict returned as a durable failed receipt does not need readback", async () => {
  const statusOperationId = "relay-channel-status-operation-0001";
  const statusReason = "Disable admission during credential review";
  const request = buildRelayChannelOperationRequest("status", {
    operationId: statusOperationId,
    reason: statusReason,
    approved: true,
    expectedRevision: `sha256:${"d".repeat(64)}`,
    targetStatus: "manually_disabled",
  });
  let submitCount = 0;
  let readCount = 0;
  const result = await runRelayChannelOperationWithReadback({
    channelId,
    kind: "status",
    request,
    submit: async () => {
      submitCount += 1;
      return receipt({
        operation_id: statusOperationId,
        kind: "status",
        state: "failed",
        reason: statusReason,
        previous_revision: `sha256:${"e".repeat(64)}`,
        result_revision: `sha256:${"e".repeat(64)}`,
        expected_revision: `sha256:${"d".repeat(64)}`,
        target_status: "manually_disabled",
        result: {
          previous_status: "enabled",
          current_status: "enabled",
          changed: false,
          error_code: "CHANNEL_REVISION_CONFLICT",
        },
      });
    },
    getResult: async () => {
      readCount += 1;
      throw new Error("a verified failed receipt must not trigger readback");
    },
  });
  assert.equal(submitCount, 1);
  assert.equal(readCount, 0);
  assert.equal(result.confirmation, "response_receipt");
  assert.equal(result.receipt.state, "failed");
  assert.equal(result.receipt.result.errorCode, "CHANNEL_REVISION_CONFLICT");
});

test("definitive pre-POST 409 responses never read back or lock a nonexistent operation", async () => {
  const scenarios = [
    "revision changed before approval",
    "operation_id conflicts with approved evidence",
    "generic connectivity test is unsupported",
  ];
  for (const message of scenarios) {
    const request = buildRelayChannelOperationRequest("test", {
      operationId,
      reason,
      approved: true,
      ...routeIdentity,
    });
    const conflict = Object.assign(new Error(message), {
      status: 409,
      code: "HTTP_ERROR",
    });
    let readCount = 0;
    await assert.rejects(
      runRelayChannelOperationWithReadback({
        channelId,
        kind: "test",
        request,
        submit: async () => { throw conflict; },
        getResult: async () => {
          readCount += 1;
          throw new Error("no journal should exist for a pre-POST conflict");
        },
      }),
      (error) => error === conflict && error.relayChannelReadbackRequired !== true,
    );
    assert.equal(readCount, 0, message);
  }
});

test("local status-zero configuration errors do not masquerade as ambiguous writes", async () => {
  const request = buildRelayChannelOperationRequest("test", {
    operationId,
    reason,
    approved: true,
    ...routeIdentity,
  });
  const configurationError = Object.assign(new Error("Platform API is not configured"), {
    status: 0,
    code: "PLATFORM_API_NOT_CONFIGURED",
  });
  let readCount = 0;
  await assert.rejects(
    runRelayChannelOperationWithReadback({
      channelId,
      kind: "test",
      request,
      submit: async () => { throw configurationError; },
      getResult: async () => { readCount += 1; },
    }),
    (error) => error === configurationError && error.relayChannelReadbackRequired !== true,
  );
  assert.equal(readCount, 0);
});

test("unavailable or mismatched readback locks the original operation proof", async () => {
  const request = buildRelayChannelOperationRequest("test", {
    operationId, reason, approved: true, ...routeIdentity,
  });
  await assert.rejects(
    runRelayChannelOperationWithReadback({
      channelId,
      kind: "test",
      request,
      submit: async () => { throw Object.assign(new Error("lost"), { code: "NETWORK_ERROR" }); },
      getResult: async () => { throw new Error("not yet visible"); },
    }),
    (error) => error.relayChannelReadbackRequired === true,
  );
  await assert.rejects(
    readRelayChannelOperation({
      channelId,
      kind: "test",
      request,
      getResult: async () => receipt({ operation_id: "different-operation-0001" }),
    }),
    (error) => error.relayChannelReadbackRequired === true,
  );
});

test("operation adapter retains only safe receipt facts", () => {
  const adapted = adaptRelayChannelOperation(receipt({
    credential: { key: "SECRET_CANARY" },
    raw_error: "SECRET_CANARY",
  }), { channelId, operationId, kind: "test", reason });
  assert.equal(adapted.result.responseTimeMs, 413);
  assert.doesNotMatch(JSON.stringify(adapted), /SECRET_CANARY|credential|raw_error/);
});

test("operation adapter accepts every secret-safe provider failure class", () => {
  const cases = new Map([
    ["CHANNEL_TEST_FAILED", "渠道测试失败"],
    ["CHANNEL_TEST_UNAVAILABLE", "渠道暂不可用"],
    ["CHANNEL_TEST_PROVIDER_VALIDATION", "供应商拒绝了请求参数或模型配置"],
    ["CHANNEL_TEST_PROVIDER_AUTH", "供应商身份凭证或模型授权无效"],
    ["CHANNEL_TEST_PROVIDER_QUOTA", "供应商余额、配额或限流阻止了本次测试"],
    ["CHANNEL_TEST_PROVIDER_TERMINAL", "供应商已返回终态失败"],
    ["CHANNEL_TEST_ARTIFACT_INVALID", "生成产物未通过完整性验证"],
    ["CHANNEL_TEST_ROUTE_DRIFT", "受控路由在测试期间发生漂移"],
    ["CHANNEL_TEST_RECONCILED_NO_CREATION", "已核对确认供应商未创建任务"],
  ]);
  assert.equal(cases.size, 9);
  for (const [errorCode, label] of cases) {
    const adapted = adaptRelayChannelOperation(receipt({
      state: "failed",
      result: { success: false, response_time_ms: 4045, error_code: errorCode },
    }), { channelId, operationId, kind: "test", reason });
    assert.equal(adapted.result.errorCode, errorCode);
    assert.equal(relayChannelTestErrorLabel(errorCode), label);
  }
  assert.throws(() => adaptRelayChannelOperation(receipt({
    state: "failed",
    result: {
      success: false,
      response_time_ms: 4045,
      error_code: "PROVIDER_RAW_ERROR",
    },
  }), { channelId, operationId, kind: "test", reason }), /回执无效/);
});

test("status readback fails closed when persisted revision or target intent drifts", () => {
  const statusOperationId = "relay-channel-status-operation-0002";
  const statusReason = "Enable after provider review";
  const expectedRevision = `sha256:${"a".repeat(64)}`;
  const raw = receipt({
    operation_id: statusOperationId,
    kind: "status",
    reason: statusReason,
    expected_revision: expectedRevision,
    target_status: "enabled",
    previous_revision: expectedRevision,
    result_revision: `sha256:${"b".repeat(64)}`,
    result: {
      previous_status: "manually_disabled",
      current_status: "enabled",
      changed: true,
      error_code: null,
    },
  });
  assert.throws(() => adaptRelayChannelOperation(raw, {
    channelId,
    operationId: statusOperationId,
    kind: "status",
    reason: statusReason,
    expectedRevision: `sha256:${"c".repeat(64)}`,
    targetStatus: "enabled",
  }), /审批证据不一致/);
  assert.throws(() => adaptRelayChannelOperation({
    ...receipt(),
    expected_revision: expectedRevision,
    target_status: "enabled",
  }, { channelId, operationId, kind: "test", reason }), /结构无效/);
});
