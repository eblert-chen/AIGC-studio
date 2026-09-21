const RECONCILIATION_TOKEN_PATTERN = /^sha256:[0-9a-f]{64}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const OPERATION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;
const KEY_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/;
const SHA256_HEX_PATTERN = /^[0-9a-f]{64}$/;
const CANONICAL_UTC_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const APPROVAL_SIGNATURE_PATTERN = /^hmac-sha256:[0-9a-f]{64}$/;
const RELAY_JOB_STATUSES = new Set([
  "queued",
  "submitting",
  "reconciliation_required",
  "processing",
  "transferring",
  "succeeded",
  "failed",
  "cancelled",
]);

function requiredText(value, label, { minLength = 1, maxLength } = {}) {
  const normalized = String(value || "").trim();
  if (normalized.length < minLength) {
    throw new Error(`${label}至少填写 ${minLength} 个字符。`);
  }
  if (maxLength && normalized.length > maxLength) {
    throw new Error(`${label}不能超过 ${maxLength} 个字符。`);
  }
  return normalized;
}

function positiveInteger(value, label) {
  const normalized = Number(value);
  if (!Number.isInteger(normalized) || normalized <= 0) {
    throw new Error(`${label}无效，请刷新详情后重新核实。`);
  }
  return normalized;
}

function boundedInteger(value, label, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  const normalized = Number(value);
  if (!Number.isSafeInteger(normalized) || normalized < min || normalized > max) {
    throw new Error(`${label}必须是 ${min} 到 ${max} 之间的整数。`);
  }
  return normalized;
}

function canonicalUtc(value) {
  const normalized = String(value || "").trim();
  if (!CANONICAL_UTC_PATTERN.test(normalized) || Number.isNaN(Date.parse(normalized))) {
    throw new Error("Provider 创建时间必须使用 UTC 格式 YYYY-MM-DDTHH:mm:ssZ。");
  }
  return normalized;
}

function safeTemporaryArtifactUrl(value) {
  const normalized = String(value || "").trim();
  let parsed;
  try {
    parsed = new URL(normalized);
  } catch {
    throw new Error("临时 Artifact URL 必须是完整的 HTTPS 地址。");
  }
  if (
    normalized.length > 8192
    || parsed.protocol !== "https:"
    || !parsed.hostname
    || parsed.username
    || parsed.password
    || parsed.hash
  ) {
    throw new Error("临时 Artifact URL 必须是无账号、密码和片段的完整 HTTPS 地址。");
  }
  return normalized;
}

function buildSynchronousResultEvidence(values = {}) {
  const providerModelId = requiredText(values.providerModelId, "Provider 模型", {
    maxLength: 128,
  });
  const providerResponseSha256 = String(values.providerResponseSha256 || "").trim();
  if (!SHA256_HEX_PATTERN.test(providerResponseSha256)) {
    throw new Error("Provider 响应 SHA-256 必须是 64 位小写十六进制值。");
  }
  const outputTokens = boundedInteger(values.outputTokens, "输出 token 数");
  const totalTokens = boundedInteger(values.totalTokens, "总 token 数");
  if (totalTokens < outputTokens) {
    throw new Error("总 token 数不能小于输出 token 数。");
  }
  return {
    provider_model_id: providerModelId,
    provider_response_sha256: providerResponseSha256,
    artifact_url: safeTemporaryArtifactUrl(values.artifactUrl),
    provider_created_at: canonicalUtc(values.providerCreatedAt),
    generated_images: boundedInteger(values.generatedImages, "生成图片数", {
      min: 1,
      max: 16,
    }),
    output_tokens: outputTokens,
    total_tokens: totalTokens,
  };
}

export function adaptRelayUnknownSubmission(item = {}) {
  return {
    jobId: item.job_id || item.jobId || "",
    tenantId: item.tenant_id || item.tenantId || "",
    clientReferenceId: item.client_reference_id ?? item.clientReferenceId ?? null,
    model: item.model || "",
    mode: item.mode || "",
    status: item.status || "reconciliation_required",
    providerRouteId: item.provider_route_id ?? item.providerRouteId ?? null,
    providerRouteKey: item.provider_route_key || item.providerRouteKey || "",
    providerName: item.provider_name || item.providerName || "",
    providerAccountId: item.provider_account_id || item.providerAccountId || "",
    providerChannelId: item.provider_channel_id ?? item.providerChannelId ?? null,
    providerKeyIndex: item.provider_key_index ?? item.providerKeyIndex ?? null,
    providerChannelClass: item.provider_channel_class || item.providerChannelClass || "",
    providerUpstreamModel: item.provider_upstream_model || item.providerUpstreamModel || "",
    providerSubmissionAttempt:
      item.provider_submission_attempt ?? item.providerSubmissionAttempt ?? null,
    unknownAt: item.unknown_at || item.unknownAt || null,
    reconciliationToken: item.reconciliation_token || item.reconciliationToken || "",
    errorCode: item.error_code || item.errorCode || "",
    errorMessage: item.error_message || item.errorMessage || "",
    createdAt: item.created_at || item.createdAt || null,
    updatedAt: item.updated_at || item.updatedAt || null,
    raw: item.raw || item,
  };
}

export function adaptRelayUnknownPage(page) {
  const available = Boolean(page && typeof page === "object" && Array.isArray(page.data));
  const items = available
    ? page.data.map(adaptRelayUnknownSubmission)
    : [];
  const currentPage = Number(page?.page);
  const pageSize = Number(page?.page_size);
  const total = Number(page?.total);
  return {
    items,
    sourceStatus: available ? "available" : "unavailable",
    page: Number.isInteger(currentPage) && currentPage > 0 ? currentPage : 1,
    pageSize: Number.isInteger(pageSize) && pageSize > 0 ? pageSize : items.length,
    total: available
      ? (Number.isInteger(total) && total >= 0 ? total : items.length)
      : null,
  };
}

export function buildRelayUnknownResolution(
  item,
  {
    outcome,
    upstreamTaskId = "",
    verificationReference,
    reason,
    approved = false,
    synchronousResult,
  } = {},
) {
  if (!approved) {
    throw new Error("请明确确认已在 Provider 控制台完成核实。");
  }
  if (!item?.jobId) {
    throw new Error("未知提交详情无效，请刷新列表后重试。");
  }
  if (!["created", "not_created"].includes(outcome)) {
    throw new Error("请选择明确的 Provider 提交结果。");
  }
  const routeId = positiveInteger(item.providerRouteId, "路由 fencing 证明");
  const submissionAttempt = positiveInteger(
    item.providerSubmissionAttempt,
    "提交 attempt fencing 证明",
  );
  const reconciliationToken = String(item.reconciliationToken || "");
  if (!RECONCILIATION_TOKEN_PATTERN.test(reconciliationToken)) {
    throw new Error("对账 token fencing 证明无效，请刷新详情后重新核实。");
  }

  const requiresSynchronousResult = item.mode === "text_to_image" && outcome === "created";
  const evidence = requiresSynchronousResult
    ? buildSynchronousResultEvidence(synchronousResult)
    : null;
  const normalizedUpstreamTaskId = requiresSynchronousResult
    ? `seedream:${evidence.provider_response_sha256}`
    : String(upstreamTaskId || "").trim();
  if (outcome === "created" && !normalizedUpstreamTaskId) {
    throw new Error("确认已创建时必须填写 Provider 上游任务 ID。");
  }
  if (normalizedUpstreamTaskId.length > 191) {
    throw new Error("Provider 上游任务 ID 不能超过 191 个字符。");
  }

  return {
    outcome,
    upstream_task_id: outcome === "created" ? normalizedUpstreamTaskId : "",
    expected_route_id: routeId,
    expected_submission_attempt: submissionAttempt,
    expected_reconciliation_token: reconciliationToken,
    verification_reference: requiredText(verificationReference, "核实凭证", {
      maxLength: 191,
    }),
    reason: requiredText(reason, "审批原因", { minLength: 3, maxLength: 240 }),
    ...(evidence ? { synchronous_result: evidence } : {}),
  };
}

function adaptSynchronousResultReceipt(result) {
  if (!result || typeof result !== "object") return null;
  return {
    providerModelId: result.provider_model_id ?? result.providerModelId ?? "",
    providerResponseSha256:
      result.provider_response_sha256 ?? result.providerResponseSha256 ?? "",
    artifactUrlSha256: result.artifact_url_sha256 ?? result.artifactUrlSha256 ?? "",
    providerCreatedAt: result.provider_created_at ?? result.providerCreatedAt ?? "",
    generatedImages: result.generated_images ?? result.generatedImages ?? null,
    outputTokens: result.output_tokens ?? result.outputTokens ?? null,
    totalTokens: result.total_tokens ?? result.totalTokens ?? null,
    exposedArtifactUrl: Object.hasOwn(result, "artifact_url")
      || Object.hasOwn(result, "artifactUrl"),
  };
}

export function adaptRelayUnknownResult(result = {}) {
  return {
    apiVersion: result.api_version || result.apiVersion || "",
    schemaVersion: result.schema_version ?? result.schemaVersion ?? null,
    object: result.object || "",
    eventId: result.event_id || result.eventId || "",
    operationId: result.operation_id || result.operationId || "",
    requestId: result.request_id || result.requestId || "",
    tenantId: result.tenant_id || result.tenantId || "",
    jobId: result.job_id || result.jobId || "",
    outcome: result.outcome || "",
    upstreamTaskId: result.upstream_task_id ?? result.upstreamTaskId ?? "",
    expectedRouteId: result.expected_route_id ?? result.expectedRouteId ?? null,
    expectedSubmissionAttempt:
      result.expected_submission_attempt ?? result.expectedSubmissionAttempt ?? null,
    expectedReconciliationToken:
      result.expected_reconciliation_token || result.expectedReconciliationToken || "",
    verificationReference:
      result.verification_reference ?? result.verificationReference ?? "",
    approvedBy: result.approved_by ?? result.approvedBy ?? "",
    approvalReason: result.approval_reason ?? result.approvalReason ?? "",
    approvalKeyId: result.approval_key_id || result.approvalKeyId || "",
    approvalSignature: result.approval_signature || result.approvalSignature || "",
    synchronousResult: adaptSynchronousResultReceipt(
      result.synchronous_result ?? result.synchronousResult,
    ),
    resolvedStatus: result.resolved_status || result.resolvedStatus || "",
    currentStatus: result.current_status || result.currentStatus || "",
    payloadSha256: result.payload_sha256 || result.payloadSha256 || "",
    resolvedAt: result.resolved_at || result.resolvedAt || "",
  };
}

function receiptError(message, {
  cause,
  code = "RELAY_RECEIPT_INVALID",
  proofRequired = false,
  fieldErrors = {},
} = {}) {
  const error = new Error(message, { cause });
  error.code = code;
  error.relayResultProofRequired = proofRequired;
  error.fieldErrors = fieldErrors;
  return error;
}

function assertRelayUnknownReceiptEnvelope(rawResult, item) {
  const result = adaptRelayUnknownResult(rawResult);
  const edgeWhitespaceFields = [
    result.upstreamTaskId,
    result.verificationReference,
    result.approvedBy,
    result.approvalReason,
  ];
  const resolvedAt = String(result.resolvedAt || "");
  const validResolvedAt = /(?:Z|[+-]\d{2}:\d{2})$/i.test(resolvedAt)
    && !Number.isNaN(Date.parse(resolvedAt));
  const synchronousResult = result.synchronousResult;
  const validSynchronousResult = !synchronousResult || (
    !synchronousResult.exposedArtifactUrl
    && String(synchronousResult.providerModelId).trim() === synchronousResult.providerModelId
    && String(synchronousResult.providerModelId).length >= 1
    && String(synchronousResult.providerModelId).length <= 128
    && SHA256_HEX_PATTERN.test(String(synchronousResult.providerResponseSha256))
    && SHA256_HEX_PATTERN.test(String(synchronousResult.artifactUrlSha256))
    && CANONICAL_UTC_PATTERN.test(String(synchronousResult.providerCreatedAt))
    && !Number.isNaN(Date.parse(synchronousResult.providerCreatedAt))
    && Number.isInteger(synchronousResult.generatedImages)
    && synchronousResult.generatedImages >= 1
    && synchronousResult.generatedImages <= 16
    && Number.isSafeInteger(synchronousResult.outputTokens)
    && synchronousResult.outputTokens >= 0
    && Number.isSafeInteger(synchronousResult.totalTokens)
    && synchronousResult.totalTokens >= synchronousResult.outputTokens
  );
  const structurallyValid = result.apiVersion === "v1"
    && result.schemaVersion === 1
    && result.object === "generation.reconciliation_result"
    && UUID_PATTERN.test(String(result.eventId))
    && OPERATION_ID_PATTERN.test(String(result.operationId))
    && REQUEST_ID_PATTERN.test(String(result.requestId))
    && UUID_PATTERN.test(String(result.tenantId))
    && UUID_PATTERN.test(String(result.jobId))
    && ["created", "not_created"].includes(result.outcome)
    && Number.isInteger(result.expectedRouteId)
    && result.expectedRouteId > 0
    && Number.isInteger(result.expectedSubmissionAttempt)
    && result.expectedSubmissionAttempt > 0
    && RECONCILIATION_TOKEN_PATTERN.test(String(result.expectedReconciliationToken))
    && String(result.verificationReference).length >= 1
    && String(result.verificationReference).length <= 191
    && String(result.approvedBy).length >= 1
    && String(result.approvedBy).length <= 128
    && String(result.approvalReason).length >= 3
    && String(result.approvalReason).length <= 240
    && KEY_ID_PATTERN.test(String(result.approvalKeyId))
    && APPROVAL_SIGNATURE_PATTERN.test(String(result.approvalSignature))
    && SHA256_HEX_PATTERN.test(String(result.payloadSha256))
    && RELAY_JOB_STATUSES.has(result.currentStatus)
    && validResolvedAt
    && validSynchronousResult
    && edgeWhitespaceFields.every((value) => String(value) === String(value).trim())
    && (
      result.outcome === "created"
        ? Boolean(result.upstreamTaskId)
          && result.resolvedStatus === "processing"
          && (
            !synchronousResult
            || result.upstreamTaskId === `seedream:${synchronousResult.providerResponseSha256}`
          )
        : !result.upstreamTaskId
          && result.resolvedStatus === "failed"
          && !synchronousResult
    );

  if (!structurallyValid) {
    throw receiptError(
      "Relay receipt 的签名元数据或最终状态无效，已保持锁定；请联系平台运维核查。",
    );
  }
  if (String(result.jobId) !== String(item?.jobId || "")) {
    throw receiptError(
      "读取到的 Relay receipt 不属于当前 job，已保持锁定；禁止再次 resolve。",
      { code: "RELAY_RECEIPT_MISMATCH" },
    );
  }
  if (item?.tenantId && String(result.tenantId) !== String(item.tenantId)) {
    throw receiptError(
      "读取到的 Relay receipt 租户不一致，已保持锁定；禁止再次 resolve。",
      { code: "RELAY_RECEIPT_MISMATCH" },
    );
  }
  if (
    (item?.mode === "text_to_image" && result.outcome === "created" && !synchronousResult)
    || (item?.mode !== "text_to_image" && synchronousResult)
  ) {
    throw receiptError(
      "Relay receipt 的同步图片证据与当前任务模式不一致，已保持锁定；禁止再次 resolve。",
      { code: "RELAY_RECEIPT_MISMATCH" },
    );
  }
  return result;
}

export function assertRelayUnknownResultMatchesResolution(
  rawResult,
  item,
  resolution,
  { expectedOperationId = "", expectedApprovedBy = "" } = {},
) {
  const result = assertRelayUnknownReceiptEnvelope(rawResult, item);
  const expectedUpstreamTaskId = resolution?.outcome === "created"
    ? String(resolution?.upstream_task_id || "")
    : "";
  const expectedSynchronousResult = resolution?.synchronous_result ?? null;
  const receivedSynchronousResult = result.synchronousResult;
  const synchronousEvidenceMatches = expectedSynchronousResult
    ? receivedSynchronousResult
      && receivedSynchronousResult.providerModelId === expectedSynchronousResult.provider_model_id
      && receivedSynchronousResult.providerResponseSha256 === expectedSynchronousResult.provider_response_sha256
      && receivedSynchronousResult.providerCreatedAt === expectedSynchronousResult.provider_created_at
      && receivedSynchronousResult.generatedImages === expectedSynchronousResult.generated_images
      && receivedSynchronousResult.outputTokens === expectedSynchronousResult.output_tokens
      && receivedSynchronousResult.totalTokens === expectedSynchronousResult.total_tokens
    : !receivedSynchronousResult;
  const matches = [
    result.outcome === resolution?.outcome,
    result.upstreamTaskId === expectedUpstreamTaskId,
    result.expectedRouteId === resolution?.expected_route_id,
    result.expectedSubmissionAttempt === resolution?.expected_submission_attempt,
    result.expectedReconciliationToken === resolution?.expected_reconciliation_token,
    result.verificationReference === resolution?.verification_reference,
    result.approvalReason === resolution?.reason,
    synchronousEvidenceMatches,
    !expectedOperationId || result.operationId === expectedOperationId,
    !expectedApprovedBy || result.approvedBy === expectedApprovedBy,
  ];
  if (!matches.every(Boolean)) {
    throw receiptError(
      "读取到的 Relay receipt 与本次审批证据不一致，已保持锁定；禁止再次 resolve。",
      { code: "RELAY_RECEIPT_MISMATCH" },
    );
  }
  return result;
}

const RELAY_UNKNOWN_FIELD_NAMES = {
  upstream_task_id: "upstream_task_id",
  verification_reference: "verification_reference",
  reason: "reason",
  provider_model_id: "provider_model_id",
  provider_response_sha256: "provider_response_sha256",
  artifact_url: "artifact_url",
  provider_created_at: "provider_created_at",
  generated_images: "generated_images",
  output_tokens: "output_tokens",
  total_tokens: "total_tokens",
};

export function relayUnknownResolveFieldErrors(error) {
  const source = error?.cause ?? error;
  const direct = source?.details?.field_errors ?? source?.details?.fieldErrors;
  if (direct && typeof direct === "object" && !Array.isArray(direct)) {
    return Object.fromEntries(
      Object.entries(direct)
        .map(([key, value]) => [RELAY_UNKNOWN_FIELD_NAMES[key] || key, String(value || "")])
        .filter(([, value]) => value),
    );
  }
  const issues = source?.details?.validationErrors;
  if (!Array.isArray(issues)) return {};
  const fieldErrors = {};
  for (const issue of issues) {
    const location = Array.isArray(issue?.loc) ? issue.loc.map(String) : [];
    const field = [...location].reverse().find((part) => RELAY_UNKNOWN_FIELD_NAMES[part]);
    if (!field || fieldErrors[field]) continue;
    fieldErrors[field] = String(issue?.msg || "该字段未通过 Platform 校验。");
  }
  return fieldErrors;
}

export function isRelayUnknownResolveOutcomeUnknown(error) {
  return error?.status === 0
    || error?.status === 503
    || ["NETWORK_ERROR", "REQUEST_TIMEOUT", "INVALID_RESPONSE"].includes(error?.code);
}

async function readRelayUnknownResolutionResult({
  item,
  resolution,
  getResult,
  expectedOperationId = "",
  expectedApprovedBy = "",
}) {
  let rawResult;
  try {
    rawResult = await getResult(item.jobId, expectedOperationId || undefined);
  } catch (resultError) {
    const pending = resultError?.status === 404;
    throw receiptError(
      pending
        ? "resolve 结果不明，且 receipt 尚未可读。已锁定再次提交；请稍后点“刷新详情”继续只读确认。"
        : "resolve 结果不明，且 receipt 读取失败。已锁定再次提交；请点“刷新详情”继续只读确认。",
      {
        cause: resultError,
        code: pending ? "RELAY_RECEIPT_PENDING" : "RELAY_RECEIPT_READ_FAILED",
        proofRequired: true,
      },
    );
  }
  try {
    return assertRelayUnknownResultMatchesResolution(rawResult, item, resolution, {
      expectedOperationId,
      expectedApprovedBy,
    });
  } catch (error) {
    error.relayResultProofRequired = true;
    throw error;
  }
}

export async function resolveRelayUnknownWithReadback({
  item,
  resolution,
  resolve,
  getResult,
  expectedApprovedBy = "",
}) {
  let response;
  try {
    response = await resolve(item.jobId, resolution);
  } catch (resolveError) {
    if (!isRelayUnknownResolveOutcomeUnknown(resolveError)) {
      throw receiptError(relayUnknownResolveErrorMessage(resolveError), {
        cause: resolveError,
        code: "RELAY_RESOLVE_REJECTED",
        fieldErrors: relayUnknownResolveFieldErrors(resolveError),
      });
    }
    const receipt = await readRelayUnknownResolutionResult({
      item,
      resolution,
      getResult,
      expectedApprovedBy,
    });
    return { state: "resolved", confirmation: "result_readback", receipt };
  }

  const operationId = String(response?.operation_id || "");
  if (!operationId || !response?.reconciliation_result) {
    const receipt = await readRelayUnknownResolutionResult({
      item,
      resolution,
      getResult,
      expectedOperationId: operationId,
      expectedApprovedBy,
    });
    return { state: "resolved", confirmation: "result_readback", receipt };
  }
  try {
    const receipt = assertRelayUnknownResultMatchesResolution(
      response.reconciliation_result,
      item,
      resolution,
      { expectedOperationId: operationId, expectedApprovedBy },
    );
    return {
      state: "resolved",
      confirmation: "response_receipt",
      receipt,
      response,
    };
  } catch (error) {
    error.relayResultProofRequired = true;
    throw error;
  }
}

export async function refreshRelayUnknownWithReadback({
  item,
  resolution,
  getDetail,
  getResult,
  expectedApprovedBy = "",
}) {
  try {
    const detail = await getDetail(item.jobId);
    if (!detail) throw new Error("Relay 未知提交详情为空。");
    return { state: "pending", item: adaptRelayUnknownSubmission(detail) };
  } catch (detailError) {
    if (detailError?.status !== 404) throw detailError;
  }

  let rawResult;
  try {
    rawResult = await getResult(item.jobId);
  } catch (resultError) {
    const pending = resultError?.status === 404;
    throw receiptError(
      pending
        ? "待处置详情已不存在，但 receipt 尚未可读。保持锁定；请稍后再次刷新确认。"
        : "待处置详情已不存在，且 receipt 读取失败。保持锁定；请稍后再次刷新确认。",
      {
        cause: resultError,
        code: pending ? "RELAY_RECEIPT_PENDING" : "RELAY_RECEIPT_READ_FAILED",
        proofRequired: Boolean(resolution),
      },
    );
  }

  let receipt;
  if (resolution) {
    try {
      receipt = assertRelayUnknownResultMatchesResolution(rawResult, item, resolution, {
        expectedApprovedBy,
      });
    } catch (error) {
      error.relayResultProofRequired = true;
      throw error;
    }
  } else {
    receipt = assertRelayUnknownReceiptEnvelope(rawResult, item);
  }
  return { state: "resolved", confirmation: "result_readback", receipt };
}

export function relayUnknownResolveErrorMessage(error) {
  if (error?.status === 404) {
    return "该未知提交已不在待对账队列中。请刷新列表确认是否已由其他管理员处理。";
  }
  if (error?.status === 409) {
    return "详情中的 route、attempt 或 token fencing 证明已经失效。禁止重复提交；请刷新详情并重新核实。";
  }
  if (
    !error?.status
    || error.status >= 500
    || ["NETWORK_ERROR", "REQUEST_TIMEOUT", "INVALID_RESPONSE"].includes(error?.code)
  ) {
    return "本次 resolve 的网络结果不明，可能已经生效。页面只会读取 receipt 核实，严禁自动或手动重复提交；若 receipt 暂不可读，请刷新详情继续只读确认。";
  }
  return error?.message || "resolve 未完成。请刷新详情并重新核实后再决定是否继续。";
}
