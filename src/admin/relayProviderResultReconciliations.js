const RECONCILIATION_KINDS = new Set([
  "missing_native_task",
  "provider_result_proof",
  "provider_material",
  "unknown",
]);

const RECONCILIATION_STATUSES = new Set([
  "reconciliation_required",
  "succeeded",
]);

const GENERATION_MODES = new Set([
  "text_to_video",
  "image_to_video",
  "video_to_video",
  "text_to_image",
]);

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;

function safeText(
  value,
  { allowEmpty = true, maxLength = 512, rejectAbsoluteUrls = true } = {},
) {
  if (value == null && allowEmpty) return "";
  if (typeof value !== "string") return null;
  if (value !== value.trim() || value.length > maxLength || /[\u0000-\u001f\u007f]/u.test(value)) {
    return null;
  }
  if (!allowEmpty && value.length === 0) return null;
  // This queue deliberately exposes only server-owned identifiers and copy.
  // Treat any absolute provider URL as a contract violation instead of ever
  // rendering a bearer-like temporary URL in the browser.
  if (rejectAbsoluteUrls && /\bhttps?:\/\//iu.test(value)) return null;
  return value;
}

function positiveInteger(value) {
  return Number.isInteger(value) && value > 0 ? value : null;
}

function timestamp(value) {
  const normalized = safeText(value, { allowEmpty: false, maxLength: 64 });
  if (
    !normalized
    || !/(?:Z|[+-]\d{2}:\d{2})$/u.test(normalized)
    || !Number.isFinite(Date.parse(normalized))
  ) return null;
  return normalized;
}

export function adaptRelayProviderResultReconciliation(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const jobId = safeText(raw.job_id, { allowEmpty: false, maxLength: 191 });
  const clientReferenceId = safeText(raw.client_reference_id, {
    maxLength: 128,
    // This is customer-owned business context, not provider material. The
    // generation contract permits URL-shaped references and React renders it
    // as inert table text; only provider-controlled fields reject URLs.
    rejectAbsoluteUrls: false,
  });
  const model = safeText(raw.model, { allowEmpty: false, maxLength: 128 });
  const mode = safeText(raw.mode, { allowEmpty: false, maxLength: 32 });
  const upstreamTaskId = safeText(raw.upstream_task_id, { maxLength: 191 });
  const errorCode = safeText(raw.error_code, { maxLength: 160 });
  const createdAt = timestamp(raw.created_at);
  const updatedAt = timestamp(raw.updated_at);
  const providerRouteId = positiveInteger(raw.provider_route_id);
  const providerChannelId = positiveInteger(raw.provider_channel_id);
  const providerSubmissionAttempt = positiveInteger(raw.provider_submission_attempt);
  const progress = Number.isInteger(raw.progress) && raw.progress >= 0 && raw.progress <= 100
    ? raw.progress
    : null;

  if (
    raw.api_version !== "v1"
    || raw.schema_version !== 1
    || raw.object !== "generation.provider_result_reconciliation"
    || !jobId
    || !UUID_PATTERN.test(jobId)
    || clientReferenceId == null
    || !model
    || !mode
    || !GENERATION_MODES.has(mode)
    || upstreamTaskId == null
    || errorCode == null
    || !createdAt
    || !updatedAt
    || !providerRouteId
    || !providerChannelId
    || !providerSubmissionAttempt
    || progress == null
    || !RECONCILIATION_KINDS.has(raw.reconciliation_kind)
    || !RECONCILIATION_STATUSES.has(raw.status)
    || (raw.reconciliation_kind !== "provider_material" && raw.status !== "reconciliation_required")
    || raw.resolution_supported !== false
    || typeof raw.evidence_retained !== "boolean"
    || Date.parse(updatedAt) < Date.parse(createdAt)
  ) {
    return null;
  }

  return {
    jobId,
    clientReferenceId,
    model,
    mode,
    status: raw.status,
    progress,
    providerRouteId,
    providerChannelId,
    providerSubmissionAttempt,
    upstreamTaskId,
    reconciliationKind: raw.reconciliation_kind,
    evidenceRetained: raw.evidence_retained,
    errorCode,
    createdAt,
    updatedAt,
  };
}

export function adaptRelayProviderResultReconciliationPage(page) {
  if (
    !page
    || typeof page !== "object"
    || page.api_version !== "v1"
    || page.schema_version !== 1
    || page.object !== "list"
    || !Array.isArray(page.data)
  ) {
    return { items: [], page: 1, pageSize: 0, total: null, sourceStatus: "unavailable" };
  }
  const pageNumber = page.page;
  const pageSize = page.page_size;
  const total = page.total;
  const items = page.data.map(adaptRelayProviderResultReconciliation);
  if (
    !Number.isInteger(pageNumber)
    || pageNumber < 1
    || !Number.isInteger(pageSize)
    || pageSize < 1
    || pageSize > 100
    || !Number.isInteger(total)
    || total < 0
    || page.data.length > pageSize
    || page.data.length > total
    || items.some((item) => item == null)
  ) {
    return { items: [], page: 1, pageSize: 0, total: null, sourceStatus: "unavailable" };
  }
  return {
    items,
    page: pageNumber,
    pageSize,
    total,
    sourceStatus: "available",
  };
}

export function relayProviderResultReconciliationPresentation(item) {
  if (item?.reconciliationKind === "missing_native_task") {
    return {
      label: "原生任务证据缺失",
      tone: "critical",
      guidance: "Relay 已保留固定路由与容量；仅核对证据，不要补单、重试或切换渠道。",
    };
  }
  if (item?.reconciliationKind === "provider_result_proof") {
    return {
      label: "终态证明冲突",
      tone: "critical",
      guidance: "供应商终态与不可变绑定未能相互证明；只读核查，禁止自动重试。",
    };
  }
  if (item?.reconciliationKind === "provider_material") {
    return {
      label: "临时材料待核对",
      tone: "warning",
      guidance: item.status === "succeeded"
        ? "长期作品已发布；不要重生成或删除作品，仅核对临时供应商材料的清理状态。"
        : "作品尚未发布；Relay 已保留固定路由、容量与供应商材料，禁止重试或重新生成。",
    };
  }
  return {
    label: "类型待核验",
    tone: "unknown",
    guidance: "Relay 未提供可安全分类的证据类型；保持只读并按异常状态处理。",
  };
}
