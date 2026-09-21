const TASK_STATUS_DEFINITIONS = Object.freeze({
  draft: Object.freeze({
    status: "draft",
    stage: "idle",
    label: "等待提交",
    tone: "neutral",
    progress: 0,
    active: false,
    terminal: false,
    detail: "草稿尚未提交。确认创作设置后即可开始生成。",
  }),
  accepted: Object.freeze({
    status: "accepted",
    stage: "accepted",
    label: "已接收",
    tone: "progress",
    progress: 6,
    active: true,
    terminal: false,
    detail: "任务已接收，正在等待调度。无需重复提交。",
  }),
  queued: Object.freeze({
    status: "queued",
    stage: "queued",
    label: "排队中",
    tone: "progress",
    progress: 12,
    active: true,
    terminal: false,
    detail: "任务正在排队，开始生成后会自动更新状态。",
  }),
  processing: Object.freeze({
    status: "processing",
    stage: "rendering",
    label: "生成中",
    tone: "progress",
    progress: 55,
    active: true,
    terminal: false,
    detail: "任务正在生成，完成后会自动更新结果。",
  }),
  succeeded: Object.freeze({
    status: "succeeded",
    stage: "complete",
    label: "生成完成",
    tone: "success",
    progress: 100,
    active: false,
    terminal: true,
    detail: "生成已完成。结果准备好后即可预览、下载或继续使用。",
  }),
  failed: Object.freeze({
    status: "failed",
    stage: "failed",
    label: "生成失败",
    tone: "danger",
    progress: 0,
    active: false,
    terminal: true,
    detail: "生成失败，失败任务不会结算生成费用。你可以复用原设置后调整。",
  }),
  cancelled: Object.freeze({
    status: "cancelled",
    stage: "cancelled",
    label: "已取消",
    tone: "danger",
    progress: 0,
    active: false,
    terminal: true,
    detail: "任务已取消，不会继续生成。",
  }),
  timed_out: Object.freeze({
    status: "timed_out",
    stage: "timed-out",
    label: "已超时",
    tone: "danger",
    progress: 0,
    active: false,
    terminal: true,
    detail: "等待已超时。请先查看任务详情，费用以最终任务记录为准。",
  }),
  reconciliation_required: Object.freeze({
    status: "reconciliation_required",
    stage: "reconciliation-required",
    label: "待人工确认",
    tone: "warning",
    progress: 0,
    active: false,
    terminal: true,
    detail: "提交结果尚未确认。系统不会自动再次提交或改用其他渠道；请先核对任务结果，避免重复提交。",
  }),
});

const UNKNOWN_TASK_STATUS = Object.freeze({
  status: "unknown",
  stage: "unknown",
  label: "状态未知",
  tone: "warning",
  progress: 0,
  active: false,
  terminal: true,
  detail: "暂时无法识别当前任务状态。页面已停止等待，请刷新后再查看；仍未恢复时请联系管理员。",
});

const TASK_MESSAGE_RULES = Object.freeze([
  Object.freeze({
    pattern: /\btasks\.read\b/i,
    message: "当前账号没有查看任务记录的权限。请联系管理员开通权限。",
  }),
  Object.freeze({
    pattern: /\btasks\.create\b/i,
    message: "当前账号不能提交生成任务。请联系管理员开通权限。",
  }),
  Object.freeze({
    pattern: /\bassets\.read\b/i,
    message: "当前账号没有查看素材的权限。请联系管理员开通权限。",
  }),
  Object.freeze({
    pattern: /\bassets\.manage\b/i,
    message: "当前账号不能上传或保存素材。请联系管理员开通权限。",
  }),
  Object.freeze({
    pattern: /\bmodel_call_quota_exhausted\b/i,
    message: "当前模型的调用额度已用完。请切换模型或联系管理员。",
  }),
  Object.freeze({
    pattern: /\bmodel_concurrency_saturated\b/i,
    message: "当前模型的生成任务已满。请稍后再试。",
  }),
  Object.freeze({
    pattern: /\b(?:required_resource_keys?|resource_key|resource_call_quota_exhausted|resource_concurrency_saturated|mode_readiness|capability_revision|capability_snapshot)\b/i,
    message: "当前创作所需的权限、额度或资源暂不可用。请调整设置，或联系管理员确认账号权限。",
  }),
]);

const RAW_TASK_CODE_PATTERN = /\b(?:tasks|assets|publish|reports|members|wallet|company|models|features|agents|external_apis)\.[a-z][a-z0-9_.-]*\b|\b[A-Z][A-Z0-9_]{2,}\b/;

export function taskUserMessage(value, fallback = "暂时无法读取任务信息，请稍后再试。") {
  const message = String(value || "").trim();
  if (!message) return fallback;
  const matchedRule = TASK_MESSAGE_RULES.find((rule) => rule.pattern.test(message));
  if (matchedRule) return matchedRule.message;
  return RAW_TASK_CODE_PATTERN.test(message) ? fallback : message;
}

export function resolveTaskStatus(value) {
  const status = String(value || "").trim().toLowerCase();
  const definition = TASK_STATUS_DEFINITIONS[status];
  if (definition) return definition;
  return {
    ...UNKNOWN_TASK_STATUS,
    rawStatus: status,
    label: status ? "状态待确认" : UNKNOWN_TASK_STATUS.label,
  };
}

export function isTaskActive(value) {
  return resolveTaskStatus(value).active;
}

export function isTaskAttentionRequired(value) {
  return ["timed_out", "reconciliation_required"].includes(
    resolveTaskStatus(value).status,
  );
}

const TASK_ETA_SECONDS_FIELDS = Object.freeze([
  "eta_seconds",
  "estimated_remaining_seconds",
  "remaining_seconds",
]);

const TASK_ETA_AT_FIELDS = Object.freeze([
  "eta_at",
  "estimated_completion_at",
]);

function positiveEtaSeconds(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? Math.ceil(value)
    : null;
}

export function taskEtaSeconds(task, { now = Date.now() } = {}) {
  if (!task || typeof task !== "object") return null;

  for (const field of TASK_ETA_SECONDS_FIELDS) {
    const seconds = positiveEtaSeconds(task[field]);
    if (seconds !== null) return seconds;
  }

  for (const field of TASK_ETA_AT_FIELDS) {
    const value = task[field];
    if (
      typeof value !== "string" ||
      !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
    ) {
      continue;
    }
    const target = Date.parse(value);
    if (!Number.isFinite(target)) continue;
    const seconds = positiveEtaSeconds((target - now) / 1_000);
    if (seconds !== null) return seconds;
  }

  return null;
}

export function taskTimingLabel(
  task,
  { status = task?.status, now = Date.now() } = {},
) {
  const resolved = resolveTaskStatus(status);
  if (resolved.active) {
    const etaSeconds = taskEtaSeconds(task, { now });
    if (etaSeconds !== null) return `预计剩余 ${etaSeconds} 秒`;
  }

  if (resolved.stage === "accepted") return "任务已接收";
  if (resolved.stage === "queued") return "等待调度";
  if (resolved.stage === "rendering") return "生成处理中";
  return resolved.detail;
}
