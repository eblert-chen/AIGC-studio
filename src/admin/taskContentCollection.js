import { resolveTaskStatus } from "../taskStatus.js";

const WORKSPACE_TYPES = new Set(["company", "personal"]);

function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

function positiveInteger(value, fallback) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function nullableText(value) {
  const normalized = text(value);
  return normalized || null;
}

function invalidListResponse(detail) {
  throw new Error(`提示词收集列表证据无效：${detail}`);
}

function assertItemDoesNotExposeTaskPayloads(item) {
  const forbidden = ["demo_prompt", "request_payload", "relay_payload"];
  const found = forbidden.find((key) => Object.prototype.hasOwnProperty.call(item, key));
  if (found) invalidListResponse(`列表意外包含任务载荷字段 ${found}`);
}

export function taskContentWorkspaceLabel(item) {
  if (item?.workspaceType === "company") {
    return item.companyName || "企业工作区";
  }
  if (item?.workspaceType === "personal") return "个人工作区";
  return "工作区待核验";
}

export function adaptTaskContentItem(item = {}) {
  if (!item || typeof item !== "object" || Array.isArray(item)) {
    invalidListResponse("任务记录不是对象");
  }
  assertItemDoesNotExposeTaskPayloads(item);
  const workspaceType = text(item.workspace_type).toLowerCase();
  if (!text(item.task_id) || !WORKSPACE_TYPES.has(workspaceType)) {
    invalidListResponse("任务 ID 或工作区类型缺失");
  }
  if (
    (workspaceType === "company" && (!text(item.company_id) || text(item.personal_workspace_id)))
    || (workspaceType === "personal" && (!text(item.personal_workspace_id) || text(item.company_id)))
  ) {
    invalidListResponse("任务工作区范围不一致");
  }
  if (!text(item.user_id) || !text(item.model_id) || !text(item.status) || !text(item.created_at)) {
    invalidListResponse("任务主体、模型、状态或时间证据缺失");
  }
  const prompt = typeof item.prompt === "string" && item.prompt.trim()
    ? item.prompt
    : null;
  const promptLength = Number(item.prompt_length);
  if (!Number.isInteger(promptLength) || promptLength < 0) {
    invalidListResponse("提示词长度证据无效");
  }
  if (
    (prompt && promptLength !== [...prompt].length)
    || (!prompt && promptLength !== 0)
  ) {
    invalidListResponse("提示词原文与长度证据不一致");
  }
  return {
    taskId: text(item.task_id),
    workspaceType,
    companyId: nullableText(item.company_id),
    companyName: nullableText(item.company_name),
    personalWorkspaceId: nullableText(item.personal_workspace_id),
    userId: text(item.user_id),
    userDisplayName: nullableText(item.user_display_name) || "未命名用户",
    modelId: text(item.model_id),
    modelDisplayName: nullableText(item.model_display_name) || "未命名模型",
    status: text(item.status).toLowerCase(),
    statusPresentation: resolveTaskStatus(item.status),
    prompt,
    promptLength,
    createdAt: nullableText(item.created_at),
    updatedAt: nullableText(item.updated_at),
  };
}

export function adaptTaskContentPage(payload = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    invalidListResponse("响应不是对象");
  }
  if (!Array.isArray(payload.items)) invalidListResponse("items 缺失");
  if (!Number.isInteger(Number(payload.page)) || Number(payload.page) < 1) {
    invalidListResponse("page 无效");
  }
  if (!Number.isInteger(Number(payload.page_size)) || Number(payload.page_size) < 1) {
    invalidListResponse("page_size 无效");
  }
  if (!Number.isInteger(Number(payload.total)) || Number(payload.total) < 0) {
    invalidListResponse("total 无效");
  }
  const items = payload.items.map(adaptTaskContentItem);
  const pageSize = Number(payload.page_size);
  const page = Number(payload.page);
  const total = Number(payload.total);
  if (items.length > pageSize || total < items.length) {
    invalidListResponse("分页计数与任务记录不一致");
  }
  return {
    page,
    pageSize,
    total,
    pageCount: Math.max(1, Math.ceil(total / pageSize)),
    items,
  };
}

export function buildTaskContentListFilters({
  workspaceType = "all",
  status = "all",
  userId = "",
  taskId = "",
  createdFrom,
  createdBefore,
  page = 1,
  pageSize = 25,
} = {}) {
  const filters = {
    page: positiveInteger(page, 1),
    page_size: positiveInteger(pageSize, 25),
  };
  if (["company", "personal"].includes(workspaceType)) {
    filters.workspace_type = workspaceType;
  }
  if (status && status !== "all") filters.status = text(status).toLowerCase();
  if (text(userId)) filters.user_id = text(userId);
  if (text(taskId)) filters.task_id = text(taskId);
  if (text(createdFrom)) filters.created_from = text(createdFrom);
  if (text(createdBefore)) filters.created_before = text(createdBefore);
  return filters;
}
