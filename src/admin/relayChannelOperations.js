const OPERATION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const REVISION_PATTERN = /^sha256:[0-9a-f]{64}$/;
const CHANNEL_STATUSES = new Set(["enabled", "manually_disabled", "auto_disabled"]);
const TARGET_STATUSES = new Set(["enabled", "manually_disabled"]);
const OPERATION_STATES = new Set(["pending", "succeeded", "failed"]);

function requiredReason(value) {
  const reason = String(value || "").trim();
  if (reason.length < 3 || reason.length > 240) {
    throw new Error("操作原因须为 3 至 240 个字符。");
  }
  return reason;
}

export function buildRelayChannelOperationRequest(kind, values = {}) {
  const operationId = String(values.operationId || "");
  if (!OPERATION_ID_PATTERN.test(operationId)) {
    throw new Error("缺少稳定 operation_id；页面不会提交 Relay 操作。");
  }
  if (values.approved !== true) {
    throw new Error("请明确确认本次 Relay 渠道操作。");
  }
  const request = {
    operationId,
    reason: requiredReason(values.reason),
    approved: true,
  };
  if (kind === "test") return request;
  if (kind !== "status") throw new Error("Relay 渠道操作类型无效。");
  if (!REVISION_PATTERN.test(String(values.expectedRevision || ""))) {
    throw new Error("渠道 revision 无效，请刷新详情后重新审批。");
  }
  if (!TARGET_STATUSES.has(values.targetStatus)) {
    throw new Error("渠道状态只能切换为启用或手动停用。");
  }
  return {
    ...request,
    expectedRevision: values.expectedRevision,
    targetStatus: values.targetStatus,
  };
}

function adaptResult(raw, kind) {
  if (raw == null) return null;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("Relay 渠道操作结果结构无效。");
  }
  if (kind === "test") {
    const success = raw.success;
    const responseTimeMs = Number(raw.response_time_ms);
    const errorCode = raw.error_code ?? null;
    if (
      typeof success !== "boolean"
      || !Number.isInteger(responseTimeMs)
      || responseTimeMs < 0
      || ![null, "CHANNEL_TEST_FAILED", "CHANNEL_TEST_UNAVAILABLE"].includes(errorCode)
      || success === Boolean(errorCode)
    ) {
      throw new Error("Relay 渠道测试回执无效。");
    }
    return { success, responseTimeMs, errorCode };
  }
  const previousStatus = raw.previous_status;
  const currentStatus = raw.current_status;
  const changed = raw.changed;
  const errorCode = raw.error_code ?? null;
  if (
    !CHANNEL_STATUSES.has(previousStatus)
    || !CHANNEL_STATUSES.has(currentStatus)
    || typeof changed !== "boolean"
    || changed === (previousStatus === currentStatus)
    || ![null, "CHANNEL_REVISION_CONFLICT"].includes(errorCode)
    || (errorCode === "CHANNEL_REVISION_CONFLICT" && (changed || previousStatus !== currentStatus))
  ) {
    throw new Error("Relay 渠道状态回执无效。");
  }
  return { previousStatus, currentStatus, changed, errorCode };
}

export function adaptRelayChannelOperation(raw, expected = {}) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("Relay 渠道操作回执为空。");
  }
  const operationId = String(raw.operation_id || "");
  const channelId = Number(raw.channel_id);
  const kind = raw.kind;
  const state = raw.state;
  const reason = String(raw.reason || "");
  const expectedRevision = raw.expected_revision ?? null;
  const targetStatus = raw.target_status ?? null;
  if (
    raw.object !== "relay.channel_control_operation"
    || raw.schema_version !== 1
    || !OPERATION_ID_PATTERN.test(operationId)
    || !Number.isInteger(channelId)
    || channelId <= 0
    || !["test", "status"].includes(kind)
    || !OPERATION_STATES.has(state)
    || reason.length < 3
    || reason.length > 240
    || reason !== reason.trim()
    || (kind === "test" && (expectedRevision !== null || targetStatus !== null))
    || (kind === "status" && (
      !REVISION_PATTERN.test(String(expectedRevision || ""))
      || !TARGET_STATUSES.has(targetStatus)
    ))
  ) {
    throw new Error("Relay 渠道操作回执结构无效。");
  }
  if (
    (expected.operationId && operationId !== expected.operationId)
    || (expected.channelId && channelId !== Number(expected.channelId))
    || (expected.kind && kind !== expected.kind)
    || (expected.reason && reason !== expected.reason)
    || (expected.expectedRevision && expectedRevision !== expected.expectedRevision)
    || (expected.targetStatus && targetStatus !== expected.targetStatus)
  ) {
    throw new Error("Relay 渠道操作回执与本次审批证据不一致。");
  }
  const result = adaptResult(raw.result, kind);
  if ((state === "pending") !== (result === null)) {
    throw new Error("Relay 渠道操作回执终态不一致。");
  }
  const previousRevision = raw.previous_revision || null;
  const resultRevision = raw.result_revision || null;
  if (
    (kind === "test" && result && ((state === "succeeded") !== result.success))
    || (kind === "status" && result && (
      ((state === "failed") !== Boolean(result.errorCode))
      || (state === "succeeded" && result.currentStatus !== targetStatus)
      || !REVISION_PATTERN.test(String(previousRevision || ""))
      || !REVISION_PATTERN.test(String(resultRevision || ""))
      || result.changed !== (previousRevision !== resultRevision)
    ))
  ) {
    throw new Error("Relay 渠道操作回执与持久化意图不一致。");
  }
  return {
    apiVersion: String(raw.api_version || ""),
    schemaVersion: raw.schema_version,
    object: raw.object,
    operationId,
    channelId,
    kind,
    state,
    reason,
    expectedRevision,
    targetStatus,
    previousRevision,
    resultRevision,
    result,
    createdAt: raw.created_at || null,
    completedAt: raw.completed_at || null,
    idempotentReplay: raw.idempotent_replay === true,
  };
}

function outcomeUnknown(error) {
  if (error?.code === "RELAY_CHANNEL_OPERATION_NOT_STARTED") return false;
  if (error?.code === "RELAY_CHANNEL_OPERATION_OUTCOME_UNKNOWN") return true;
  if (error?.status === 409) return false;
  if (["NETWORK_ERROR", "REQUEST_TIMEOUT", "INVALID_RESPONSE"].includes(error?.code)) {
    return true;
  }
  return error?.status >= 500 && (!error?.code || error.code === "HTTP_ERROR");
}

function lockedError(message, cause) {
  const error = new Error(message, { cause });
  error.relayChannelReadbackRequired = true;
  return error;
}

async function readReceipt({ channelId, request, kind, getResult }) {
  try {
    return adaptRelayChannelOperation(
      await getResult(channelId, request.operationId),
      {
        channelId,
        operationId: request.operationId,
        kind,
        reason: request.reason,
        expectedRevision: request.expectedRevision,
        targetStatus: request.targetStatus,
      },
    );
  } catch (error) {
    throw lockedError(
      "操作结果尚未能只读确认。页面已锁定本次 operation_id，不会再次 POST；请稍后点“只读核对结果”。",
      error,
    );
  }
}

export async function runRelayChannelOperationWithReadback({
  channelId,
  kind,
  request,
  submit,
  getResult,
}) {
  let response;
  try {
    response = await submit(channelId, request);
  } catch (error) {
    if (!outcomeUnknown(error)) throw error;
    const receipt = await readReceipt({ channelId, request, kind, getResult });
    return { confirmation: "result_readback", receipt };
  }
  try {
    return {
      confirmation: "response_receipt",
      receipt: adaptRelayChannelOperation(response, {
        channelId,
        operationId: request.operationId,
        kind,
        reason: request.reason,
        expectedRevision: request.expectedRevision,
        targetStatus: request.targetStatus,
      }),
    };
  } catch {
    const receipt = await readReceipt({ channelId, request, kind, getResult });
    return { confirmation: "result_readback", receipt };
  }
}

export async function readRelayChannelOperation({ channelId, kind, request, getResult }) {
  return readReceipt({ channelId, kind, request, getResult });
}
