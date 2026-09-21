import { modeLabel, modeUsesDuration } from "./modelCapabilities.js";
import {
  billingAmountLabel,
  billingPresentationState,
} from "./billingPresentation.js";

function finiteCount(value) {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? Math.floor(count) : null;
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

export function taskArtifactEvidence(task = {}) {
  if (task?.status !== "succeeded") {
    return {
      complete: false,
      state: "not_applicable",
      detail: "任务尚未进入成功归档状态。",
      artifacts: [],
    };
  }

  const expectedCount = Number(task?.request_payload?.output_count);
  if (!Number.isSafeInteger(expectedCount) || expectedCount < 1) {
    return {
      complete: false,
      state: "evidence_missing",
      detail: "任务报告成功，但缺少可核验的预期产物数量。",
      artifacts: [],
    };
  }

  const artifacts = Array.isArray(task.output_artifacts) ? task.output_artifacts : [];
  if (artifacts.length !== expectedCount) {
    return {
      complete: false,
      state: "evidence_missing",
      detail: `任务报告成功，但归档产物为 ${artifacts.length}/${expectedCount}，已关闭下载与后续操作。`,
      artifacts: [],
    };
  }

  const valid = artifacts.every((artifact) => (
    artifact !== null
    && typeof artifact === "object"
    && !Array.isArray(artifact)
    && typeof artifact.asset_id === "string"
    && artifact.asset_id.trim().length > 0
    && typeof artifact.media_type === "string"
    && artifact.media_type.trim().length > 0
  ));
  if (!valid) {
    return {
      complete: false,
      state: "evidence_missing",
      detail: "任务报告成功，但归档产物标识或媒体类型不完整，已关闭下载与后续操作。",
      artifacts: [],
    };
  }

  return {
    complete: true,
    state: "complete",
    detail: `${artifacts.length} 个产物已通过平台归档证据核验。`,
    artifacts,
  };
}

export function deriveArtworksFromTasks(tasks = []) {
  return tasks.flatMap((task) => {
    const evidence = taskArtifactEvidence(task);
    if (!evidence.complete) return [];
    return evidence.artifacts.map((artifact, index) => ({
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
      billing_unit: task.billing_unit,
      billing_version: task.billing_version,
      billing_scope: task.billing_scope,
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
  const billing = billingPresentationState(task);
  const amountLabel = (pointsField, centsField, options = {}) => billingAmountLabel(task, {
    pointsField,
    centsField,
    ...options,
  });

  if (["failed", "cancelled"].includes(task.status)) {
    if (!billing.available) return "账务单位待确认";
    const actualField = billing.kind === "legacy_cents"
      ? "actual_cost_cents"
      : "actual_cost_points";
    const reservedField = billing.kind === "legacy_cents"
      ? "reserved_cents"
      : "reserved_points";
    const actualDeclared = Object.hasOwn(task, actualField);
    const reservedDeclared = Object.hasOwn(task, reservedField);
    const actualRaw = task[actualField];
    const reservedRaw = task[reservedField];
    const actualIsValid = actualRaw === null
      || (typeof actualRaw === "number" && Number.isSafeInteger(actualRaw) && actualRaw >= 0);
    const reservedIsValid = typeof reservedRaw === "number"
      && Number.isSafeInteger(reservedRaw)
      && reservedRaw >= 0;
    const actual = actualRaw === null ? 0 : actualRaw;
    if (actualDeclared && !actualIsValid) {
      return "账务数据异常";
    }
    if (actualDeclared && actual > 0) {
      return `账务异常：${amountLabel("actual_cost_points", "actual_cost_cents")}`;
    }
    if (reservedDeclared && !reservedIsValid) {
      return "账务数据异常";
    }
    if (reservedDeclared && reservedRaw > 0) {
      return `预占未释放：${amountLabel("reserved_points", "reserved_cents")}`;
    }
    if (!actualDeclared || !reservedDeclared) return "扣费状态待核验";
    return "未扣费";
  }
  if (billing.kind === "points" || billing.kind === "internal_test") {
    if (task.actual_cost_points !== null && task.actual_cost_points !== undefined) {
      return amountLabel("actual_cost_points", "actual_cost_cents", {
        pointNoun: billing.kind === "internal_test" ? "影子积分" : "积分",
      });
    }
  } else if (billing.kind === "legacy_cents") {
    if (task.actual_cost_cents !== null && task.actual_cost_cents !== undefined) {
      return amountLabel("actual_cost_points", "actual_cost_cents");
    }
  } else if (
    task.actual_cost_points !== null && task.actual_cost_points !== undefined
    || task.actual_cost_cents !== null && task.actual_cost_cents !== undefined
  ) {
    return "结算单位待确认";
  }
  if (task.status === "succeeded") return "结算金额未记录";
  if (task.status === "timed_out") return "费用待核验";
  if (billing.kind === "points" || billing.kind === "internal_test") {
    const pointNoun = billing.kind === "internal_test" ? "影子积分" : "积分";
    if (Number(task.reserved_points) > 0) {
      return `预占 ${amountLabel("reserved_points", "reserved_cents", { pointNoun })}`;
    }
    if (task.quote_points !== null && task.quote_points !== undefined) {
      return `预计 ${amountLabel("quote_points", "quote_cents", { pointNoun })}`;
    }
  }
  if (billing.kind === "legacy_cents") {
    if (Number(task.reserved_cents) > 0) {
      return `预占 ${amountLabel("reserved_points", "reserved_cents")}`;
    }
    if (task.quote_cents !== null && task.quote_cents !== undefined) {
      return `预计 ${amountLabel("quote_points", "quote_cents")}`;
    }
  }
  return billing.available ? "计费金额未记录" : "计费单位待确认";
}

export function taskParametersLabel(payload = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return "参数未记录";
  }
  const values = [];
  if (payload.mode) values.push(modeLabel(payload.mode));
  if (payload.aspect_ratio) values.push(payload.aspect_ratio);
  if (payload.resolution) values.push(payload.resolution);
  if (modeUsesDuration(payload.mode) && Number(payload.duration_seconds) > 0) {
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
