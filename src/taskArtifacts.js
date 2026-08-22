import { modeLabel } from "./modelCapabilities.js";

function finiteCount(value) {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? Math.floor(count) : null;
}

function money(cents) {
  const value = Number(cents);
  return Number.isFinite(value) ? `¥${(value / 100).toFixed(2)}` : "金额未记录";
}

function points(value) {
  const amount = Number(value);
  return Number.isFinite(amount) ? `${Math.max(0, Math.round(amount))} 积分` : "积分未记录";
}

export function normalizePage(payload, { page = 1, pageSize = 50 } = {}) {
  if (Array.isArray(payload)) {
    return {
      page,
      page_size: pageSize,
      total: payload.length,
      items: payload,
      legacy: true,
    };
  }

  const items = Array.isArray(payload?.items) ? payload.items : [];
  return {
    page: finiteCount(payload?.page) || page,
    page_size: finiteCount(payload?.page_size) || pageSize,
    total: finiteCount(payload?.total) ?? items.length,
    items,
    legacy: false,
  };
}

export function deriveArtworksFromTasks(tasks = []) {
  return tasks.flatMap((task) => {
    if (task?.status !== "succeeded" || !Array.isArray(task.output_artifacts)) {
      return [];
    }
    return task.output_artifacts.map((artifact, index) => ({
      artifact_id: `${task.id}:${artifact.asset_id}`,
      task_id: task.id,
      company_id: task.company_id,
      workspace_id: task.workspace_id,
      asset_id: artifact.asset_id,
      output_index: index,
      media_type: artifact.media_type,
      content_type: artifact.content_type,
      size_bytes: artifact.size_bytes,
      sha256: artifact.sha256,
      created_by_user_id: task.user_id,
      created_by_display_name:
        task.user_display_name || task.created_by_display_name || "发起人未记录",
      created_by_email: task.user_email || task.created_by_email || "",
      model_id: task.model_id,
      model_display_name:
        task.model_display_name || task.capability_snapshot?.model_slug || "模型未记录",
      request_payload: task.request_payload || {},
      actual_cost_cents: task.actual_cost_cents,
      actual_cost_points: task.actual_cost_points,
      created_at: task.created_at,
      download_evidence_available: false,
    }));
  });
}

export function downloadState(source = {}, { issuedLocally = false } = {}) {
  const direct = String(
    source.download_status || source.download_state || "",
  ).toLowerCase();
  const issueCount = finiteCount(source.download_issue_count);
  const completedCount = finiteCount(source.download_completed_count);

  if (
    direct === "completed" ||
    source.downloaded === true ||
    completedCount > 0 ||
    source.completed_at
  ) {
    return {
      key: "completed",
      label: "已完成",
      tone: "complete",
      detail: completedCount
        ? `存储侧已确认 ${completedCount} 次下载完成`
        : "存储侧已确认下载完成",
    };
  }

  if (
    direct === "issued" ||
    issuedLocally ||
    issueCount > 0 ||
    source.download_record_id
  ) {
    return {
      key: "issued",
      label: "已签发",
      tone: "issued",
      detail: issueCount
        ? `已签发 ${issueCount} 次，等待存储侧确认完成`
        : "短时地址已签发，等待存储侧确认完成",
    };
  }

  const hasServerEvidence =
    source.download_evidence_available === true ||
    source.downloaded === false ||
    issueCount !== null ||
    completedCount !== null;
  if (direct === "not_downloaded" || hasServerEvidence) {
    return {
      key: "not_downloaded",
      label: "未下载",
      tone: "idle",
      detail: "尚未签发下载地址",
    };
  }

  return {
    key: "unknown",
    label: "记录待同步",
    tone: "unknown",
    detail: "旧接口未返回下载证据，不能判断是否下载",
  };
}

export function downloadRecordState(record = {}) {
  const state = downloadState({
    download_status: record.status,
    downloaded: record.downloaded,
    completed_at: record.completed_at,
    download_record_id: record.id,
  });
  if (state.key !== "unknown") return state;
  return record.id
    ? {
        key: "issued",
        label: "已签发",
        tone: "issued",
        detail: "旧记录证明短时地址已签发，完成状态未回传",
      }
    : state;
}

export function taskCostLabel(task = {}) {
  if (task.actual_cost_points !== null && task.actual_cost_points !== undefined) {
    return points(task.actual_cost_points);
  }
  if (task.actual_cost_cents !== null && task.actual_cost_cents !== undefined) {
    return money(task.actual_cost_cents);
  }
  if (["failed", "cancelled", "timed_out"].includes(task.status)) return "未扣费";
  if (Number(task.reserved_points) > 0) {
    return `预占 ${points(task.reserved_points)}`;
  }
  if (task.quote_points !== null && task.quote_points !== undefined) {
    return `预计 ${points(task.quote_points)}`;
  }
  if (Number(task.reserved_cents) > 0) {
    return `预占 ${money(task.reserved_cents)}`;
  }
  if (task.quote_cents !== null && task.quote_cents !== undefined) {
    return `预计 ${money(task.quote_cents)}`;
  }
  return "金额未记录";
}

export function taskParametersLabel(payload = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return "参数未记录";
  }
  const values = [];
  if (payload.mode) values.push(modeLabel(payload.mode));
  if (payload.aspect_ratio) values.push(payload.aspect_ratio);
  if (payload.resolution) values.push(payload.resolution);
  if (Number(payload.duration_seconds) > 0) {
    values.push(`${Number(payload.duration_seconds)} 秒`);
  }
  if (Number(payload.output_count) > 0) {
    values.push(`${Number(payload.output_count)} 个产物`);
  }

  if (Array.isArray(payload.assets) && payload.assets.length) {
    const counts = payload.assets.reduce(
      (result, item) => {
        if (Object.hasOwn(result, item?.media_type)) {
          result[item.media_type] += 1;
        }
        return result;
      },
      { image: 0, video: 0, audio: 0 },
    );
    const media = [
      counts.image ? `${counts.image} 图` : "",
      counts.video ? `${counts.video} 视频` : "",
      counts.audio ? `${counts.audio} 音频` : "",
    ].filter(Boolean);
    if (media.length) values.push(media.join(" + "));
  }
  if (payload.face_enabled === true) values.push("人脸已启用");
  return values.join("，") || "参数未记录";
}

export function taskAuthor(task = {}, fallback = "") {
  return (
    task.user_display_name ||
    task.created_by_display_name ||
    task.employee_display_name ||
    fallback ||
    "发起人未记录"
  );
}

export function taskCompany(task = {}, fallback = "") {
  if (task.workspace_id && !task.company_id) return "个人空间";
  if (task.company_name || task.company_display_name || fallback) {
    return task.company_name || task.company_display_name || fallback;
  }
  const id = String(task.company_id || "");
  return id ? `公司 ${id.slice(0, 12)}` : "公司未记录";
}
