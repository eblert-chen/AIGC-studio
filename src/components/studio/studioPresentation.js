import { downloadState, taskParametersLabel } from "../../taskArtifacts.js";

const INTERNAL_MESSAGE_PATTERN = /(?:\b(?:assets|tasks|publish)\.[a-z.]+\b|Platform|canonical|Input asset|non-terminal|访问 URL|能力合同|归档证据|签发)/iu;

export function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "大小未知";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export function artifactKindLabel(mediaType) {
  if (mediaType === "video") return "视频";
  if (mediaType === "image") return "图片";
  return "文件";
}

export function studioTaskParametersLabel(payload = {}) {
  return taskParametersLabel(payload).replaceAll("产物", "作品");
}

export function studioErrorMessage(value, fallback = "暂时无法完成，请稍后重试。") {
  const message = String(value || "").trim();
  if (!message || INTERNAL_MESSAGE_PATTERN.test(message)) return fallback;
  return message;
}

export function artifactEvidenceIssueMessage(evidence = {}) {
  if (evidence.complete) return "";
  return "任务已完成，但作品文件信息不完整。下载和后续操作已关闭，请稍后刷新任务。";
}

export function downloadStatusPresentation(source = {}, { issuedLocally = false } = {}) {
  const state = downloadState(source, { issuedLocally });
  const issueCount = Number(source.download_issue_count);
  const completedCount = Number(source.download_completed_count);

  if (state.key === "completed") {
    return {
      ...state,
      label: "已确认下载",
      detail: Number.isFinite(completedCount) && completedCount > 1
        ? `系统已确认 ${completedCount} 次下载完成`
        : "系统已确认下载完成",
    };
  }
  if (state.key === "issued") {
    return {
      ...state,
      label: "下载链接已生成",
      detail: Number.isFinite(issueCount) && issueCount > 1
        ? `已生成 ${issueCount} 次临时下载链接，尚未确认下载完成`
        : "临时下载链接已生成，尚未确认下载完成",
    };
  }
  if (state.key === "not_downloaded") {
    return {
      ...state,
      label: "未下载",
      detail: "尚未生成下载链接",
    };
  }
  return {
    ...state,
    label: "状态待同步",
    detail: "暂时无法确认历史下载状态",
  };
}

export function shortDate(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "时间未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function shortId(value) {
  const id = String(value || "");
  if (!id) return "未记录";
  if (id.length <= 12) return id;
  return `${id.slice(0, 7)}…${id.slice(-4)}`;
}
