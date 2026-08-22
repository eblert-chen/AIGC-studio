const OPERATION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;

export function adaptRelayCallbackDeadLetter(item = {}) {
  return {
    eventId: item.event_id || item.eventId || "",
    tenantId: item.tenant_id || item.tenantId || "",
    jobId: item.job_id || item.jobId || "",
    sourceClientId: item.source_client_id || item.sourceClientId || "",
    originalRequestId: item.original_request_id || item.originalRequestId || "",
    payloadSha256: item.payload_sha256 || item.payloadSha256 || "",
    callbackUrlSha256: item.callback_url_sha256 || item.callbackUrlSha256 || "",
    state: item.state || "dead_letter",
    attempts: Number(item.attempts || 0),
    maxAttempts: Number(item.max_attempts || item.maxAttempts || 0),
    responseStatus: Number(item.response_status || item.responseStatus || 0),
    lastError: item.last_error || item.lastError || "",
    deadLetteredAt: item.dead_lettered_at || item.deadLetteredAt || null,
    updatedAt: item.updated_at || item.updatedAt || null,
    redrives: item.redrives || [],
    raw: item.raw || item,
  };
}

export function adaptRelayCallbackDeadLetterPage(page) {
  const available = Boolean(page && typeof page === "object" && Array.isArray(page.data));
  const items = available ? page.data.map(adaptRelayCallbackDeadLetter) : [];
  const total = Number(page?.total);
  return {
    items,
    sourceStatus: available ? "available" : "unavailable",
    total: available && Number.isInteger(total) && total >= 0 ? total : (available ? items.length : null),
  };
}

export function buildRelayCallbackRedrive(item, { actor, reason, approved } = {}) {
  if (!approved) throw new Error("请明确确认已核对回调目的端并批准重新投递。");
  if (!item?.eventId || item.state !== "dead_letter") {
    throw new Error("该记录已不在死信状态，请刷新详情后再决定是否处置。");
  }
  const normalizedActor = String(actor || "").trim();
  const normalizedReason = String(reason || "").trim();
  if (!normalizedActor || normalizedActor.length > 128) throw new Error("处置人无效。");
  if (normalizedReason.length < 3 || normalizedReason.length > 240) {
    throw new Error("处置原因须为 3 至 240 个字符。");
  }
  return { actor: normalizedActor, reason: normalizedReason, approved: true };
}

function isOutcomeUnknown(error) {
  return error?.status === 0
    || error?.status === 503
    || ["NETWORK_ERROR", "REQUEST_TIMEOUT", "INVALID_RESPONSE"].includes(error?.code);
}

function assertResult(raw, item, operationId, request) {
  const result = raw?.redrive_result || raw;
  const evidence = result?.evidence || {};
  const valid = result?.object === "generation.callback_redrive_result"
    && String(result.delivery_event_id || "") === item.eventId
    && String(evidence.operation_id || "") === operationId
    && String(evidence.actor || "") === request.actor
    && String(evidence.reason || "") === request.reason
    && evidence.previous_state === "dead_letter"
    && evidence.result_state === "pending"
    && /^[0-9a-f]{64}$/.test(String(evidence.receipt_sha256 || ""));
  if (!valid) {
    const error = new Error("Relay redrive 回执与本次审批证据不一致，已保持锁定。");
    error.callbackRedriveProofRequired = true;
    throw error;
  }
  return result;
}

export async function redriveRelayCallbackWithReadback({ item, request, redrive, getResult }) {
  let response;
  try {
    response = await redrive(item.eventId, request);
  } catch (error) {
    if (!isOutcomeUnknown(error)) throw error;
    const operationId = String(request.operation_id || "");
    if (!OPERATION_ID_PATTERN.test(operationId)) {
      const locked = new Error("重新投递的网络结果不明，页面不会重复 POST；请刷新详情后只读核对结果。");
      locked.callbackRedriveProofRequired = true;
      throw locked;
    }
    const result = await getResult(item.eventId, operationId);
    return { confirmation: "result_readback", result: assertResult(result, item, operationId, request) };
  }
  const operationId = String(response?.operation_id || "");
  if (!OPERATION_ID_PATTERN.test(operationId)) {
    const error = new Error("Platform 未返回稳定 operation_id，已禁止重复提交。");
    error.callbackRedriveProofRequired = true;
    throw error;
  }
  return {
    confirmation: "response_receipt",
    operationId,
    result: assertResult(response, item, operationId, request),
  };
}

export async function refreshRelayCallbackRedrive({ item, operationId, getDetail, getResult }) {
  try {
    const detail = adaptRelayCallbackDeadLetter(await getDetail(item.eventId));
    if (detail.state === "dead_letter") return { state: "dead_letter", item: detail };
  } catch (error) {
    if (error?.status !== 404) throw error;
  }
  if (!OPERATION_ID_PATTERN.test(String(operationId || ""))) {
    throw new Error("缺少稳定 operation_id；请重新加载异常队列，页面不会重复 POST。");
  }
  return { state: "redriven", result: await getResult(item.eventId, operationId) };
}
