const TASK_STATUS_DEFINITIONS = Object.freeze({
  draft: Object.freeze({
    status: "draft",
    stage: "idle",
    label: "等待提交",
    tone: "neutral",
    progress: 0,
    active: false,
    terminal: false,
    detail: "任务仍是草稿，尚未进入生成队列。",
  }),
  accepted: Object.freeze({
    status: "accepted",
    stage: "accepted",
    label: "已接收",
    tone: "progress",
    progress: 6,
    active: true,
    terminal: false,
    detail: "客户平台已接收任务，正在等待安全调度。",
  }),
  queued: Object.freeze({
    status: "queued",
    stage: "queued",
    label: "排队中",
    tone: "progress",
    progress: 12,
    active: true,
    terminal: false,
    detail: "任务正在等待生成渠道调度。",
  }),
  processing: Object.freeze({
    status: "processing",
    stage: "rendering",
    label: "生成中",
    tone: "progress",
    progress: 55,
    active: true,
    terminal: false,
    detail: "任务已提交给生成渠道，状态由客户平台持续同步。",
  }),
  succeeded: Object.freeze({
    status: "succeeded",
    stage: "complete",
    label: "生成完成",
    tone: "success",
    progress: 100,
    active: false,
    terminal: true,
    detail: "任务已经完成，产物以平台归档记录为准。",
  }),
  failed: Object.freeze({
    status: "failed",
    stage: "failed",
    label: "生成失败",
    tone: "danger",
    progress: 0,
    active: false,
    terminal: true,
    detail: "任务生成失败，失败任务不会结算生成费用。",
  }),
  cancelled: Object.freeze({
    status: "cancelled",
    stage: "cancelled",
    label: "已取消",
    tone: "danger",
    progress: 0,
    active: false,
    terminal: true,
    detail: "任务已在安全边界内取消。",
  }),
  timed_out: Object.freeze({
    status: "timed_out",
    stage: "timed-out",
    label: "已超时",
    tone: "danger",
    progress: 0,
    active: false,
    terminal: true,
    detail: "任务已超时，不再处于排队或生成状态。费用处理以平台账本为准。",
  }),
  reconciliation_required: Object.freeze({
    status: "reconciliation_required",
    stage: "reconciliation-required",
    label: "待人工确认",
    tone: "warning",
    progress: 0,
    active: false,
    terminal: true,
    detail: "渠道提交结果尚未确认。平台不会自动重试或切换渠道，请勿重复提交同一任务。",
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
  detail: "客户平台返回了未识别的任务状态。页面已停止等待，请刷新或联系管理员。",
});

export function resolveTaskStatus(value) {
  const status = String(value || "").trim().toLowerCase();
  const definition = TASK_STATUS_DEFINITIONS[status];
  if (definition) return definition;
  return {
    ...UNKNOWN_TASK_STATUS,
    rawStatus: status,
    label: status ? `未知状态：${status}` : UNKNOWN_TASK_STATUS.label,
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

