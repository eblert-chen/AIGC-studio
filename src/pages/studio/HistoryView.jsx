import { useMemo, useState } from "react";
import { ClockCounterClockwise, WarningCircle } from "@phosphor-icons/react";
import {
  taskAuthor,
  taskCostLabel,
} from "../../taskArtifacts.js";
import { resolveTaskStatus } from "../../taskStatus.js";
import { historyBillingSummary } from "../../components/studio/studioBilling.js";
import { LoadingRows, PageControls, ScopeControl } from "../../components/studio/StudioCollectionControls.jsx";
import { DownloadStatus } from "../../components/studio/StudioWorkspaceViews.jsx";
import {
  shortDate,
  shortId,
  studioErrorMessage,
  studioTaskParametersLabel,
} from "../../components/studio/studioPresentation.js";

const ATTENTION_STATUSES = ["failed", "timed_out", "reconciliation_required", "unknown"];
const ACTIVE_TASK_STATUSES = ["accepted", "queued", "processing", "running"];

function taskSourceBadge(source) {
  if (!source) return null;
  const kind = String(source.kind || "");
  return (
    <span className={`source-badge is-${kind || "unknown"}`} title={source.title || source.label}>
      {source.label}
    </span>
  );
}

function ledgerSummary(displayedTasks, { liveMode, total }) {
  const attentionCount = displayedTasks.filter((task) => (
    ATTENTION_STATUSES.includes(String(task?.status || ""))
  )).length;
  return {
    attentionCount,
    ...historyBillingSummary(displayedTasks),
    recordCount: liveMode ? Number(total || displayedTasks.length) : displayedTasks.length,
    pageLabel: liveMode ? `本页 ${displayedTasks.length} 条` : `示例台账 ${displayedTasks.length} 条`,
  };
}

export function HistoryView({
  onRetry,
  liveMode = false,
  tasks = [],
  demoTasks = [],
  models = [],
  loading = false,
  error = "",
  onOpen,
  canCreateTasks = true,
  canViewCompany = false,
  scope = "mine",
  onScopeChange,
  statusFilter = "",
  onStatusChange,
  page = 1,
  pageSize = 24,
  total = 0,
  onPageChange,
  currentUserId = "",
  currentUserName = "",
  workspaceKind = "company",
  standalonePersonal = false,
  statusDefinitions,
  taskSources = {},
}) {
  const displayedTasks = liveMode ? tasks : demoTasks;
  const [viewTab, setViewTab] = useState("all");
  const filteredTasks = useMemo(() => {
    if (viewTab === "active") {
      return displayedTasks.filter((task) => ACTIVE_TASK_STATUSES.includes(String(task?.status || "")));
    }
    return displayedTasks;
  }, [displayedTasks, viewTab]);
  const hasActiveFilters = viewTab === "all" && Boolean(statusFilter);
  const summary = ledgerSummary(filteredTasks, { liveMode, total });
  const sourceOf = (task) => {
    const taskId = task?.id || task?.task_id;
    return taskSources[taskId]
      || (task?.source ? { kind: "demo", label: String(task.source) } : null);
  };

  return (
    <section className="secondary-view history-view" aria-labelledby="history-title" aria-busy={loading}>
      <div className="secondary-heading">
        <div>
          <h1 id="history-title">历史</h1>
          <nav className="history-tabs" aria-label="历史视图切换">
            <button
              type="button"
              className={`history-tab ${viewTab === "active" ? "is-active" : ""}`}
              onClick={() => setViewTab("active")}
              aria-current={viewTab === "active" ? "page" : undefined}
            >
              运行中
            </button>
            <button
              type="button"
              className={`history-tab ${viewTab === "all" ? "is-active" : ""}`}
              onClick={() => setViewTab("all")}
              aria-current={viewTab === "all" ? "page" : undefined}
            >
              全部
            </button>
          </nav>
          <p>
            {!liveMode
              ? "以下为示例任务台账，用于体验状态、来源、费用与详情。"
              : workspaceKind === "personal"
                ? "按时间查看当前个人空间的任务、来源、状态与积分消耗。"
                : "按时间查看任务来源、状态、模型与最终费用。"}
          </p>
        </div>
        <div className="history-toolbar">
          <ScopeControl
            value={scope}
            onChange={onScopeChange}
            canViewCompany={canViewCompany}
          />
          {viewTab === "all" ? (
            <label>
              <span>任务状态</span>
              <select value={statusFilter} onChange={(event) => onStatusChange(event.target.value)}>
                <option value="">全部</option>
                <option value="queued">排队中</option>
                <option value="processing">生成中</option>
                <option value="succeeded">已完成</option>
                <option value="failed">失败</option>
                <option value="cancelled">已取消</option>
                <option value="timed_out">已超时</option>
                <option value="reconciliation_required">待人工确认</option>
              </select>
            </label>
          ) : null}
        </div>
      </div>
      {!loading && !error && filteredTasks.length > 0 ? (
        <div className="history-summary" aria-label="当前任务摘要">
          <span>共 <strong>{summary.recordCount}</strong> 条任务</span>
          {summary.points !== null ? (
            <span>本页已记录消耗 <strong>{summary.points}</strong> {summary.pointNoun}</span>
          ) : <span>本页费用未完整确认</span>}
          <span>
            {summary.failedCount > 0
              ? <>本页失败或超时 <strong>{summary.failedCount}</strong> 次</>
              : "本页没有失败任务"}
          </span>
          {summary.failedCount > 0 ? (
            <span>{summary.failedUnchargedCount === summary.failedCount
              ? "这些失败任务已确认未扣费"
              : "失败任务扣费状态请查看逐条记录"}</span>
          ) : null}
          {scope === "company" ? <span>{summary.pageLabel}在当前页</span> : null}
          <span className={`history-summary-state ${summary.attentionCount > 0 ? "is-warning" : "is-ok"}`}>
            {summary.attentionCount > 0
              ? `${summary.attentionCount} 条需要关注`
              : "无待处理任务"}
          </span>
        </div>
      ) : null}
      <div className="history-list history-ledger-wrap">
        {loading && <LoadingRows label="正在读取任务历史" />}
        {!loading && error && (
          <div className="artifact-empty" role="alert">
            <WarningCircle size={26} aria-hidden="true" />
            <strong>任务历史读取失败</strong>
            <span>{studioErrorMessage(error, "任务历史暂时无法读取，请稍后重试。")}</span>
          </div>
        )}
        {!loading && !error && filteredTasks.length === 0 && (
          <div className="artifact-empty">
            <ClockCounterClockwise size={26} aria-hidden="true" />
            <strong>
              {viewTab === "active"
                ? "当前没有运行中的任务"
                : hasActiveFilters
                  ? "没有符合当前筛选的任务"
                  : "还没有任务"}
            </strong>
            <span>
              {viewTab === "active"
                ? "提交新任务后会在这里实时显示进度。"
                : hasActiveFilters
                  ? "清除筛选即可查看全部任务。"
                  : "提交第一个创作任务后，记录会显示在这里。"}
            </span>
            {viewTab === "all" && hasActiveFilters ? (
              <button className="text-button" type="button" onClick={() => onStatusChange("")}>
                清除筛选
              </button>
            ) : null}
          </div>
        )}
        {!loading && !error && filteredTasks.length > 0 && (
          <div className="history-table-scroll">
            <table className="history-ledger">
              <thead>
                <tr>
                  <th scope="col">时间</th>
                  <th scope="col">任务</th>
                  <th scope="col">来源</th>
                  <th scope="col">模型</th>
                  <th scope="col">规格</th>
                  <th scope="col">积分</th>
                  <th scope="col">状态</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredTasks.map((task) => {
                  const taskId = task.id || task.task_id;
                  const statusDefinition = resolveTaskStatus(task.status);
                  const stage = statusDefinition.stage;
                  const state = statusDefinitions[stage] ?? statusDefinitions.idle;
                  const StateIcon = state.icon;
                  const statusClass = statusDefinition.tone === "danger"
                    ? "status-error"
                    : statusDefinition.tone === "warning"
                      ? "status-warning"
                      : "status-success";
                  const modelName =
                    task.model_display_name ||
                    models.find((item) => item.id === task.model_id)?.name ||
                    task.capability_snapshot?.model_slug ||
                    "模型未记录";
                  const artifactCount = Number(task.artifact_count ?? task.output_artifacts?.length ?? 0);
                  const attentionDetail = ATTENTION_STATUSES.includes(statusDefinition.status)
                    ? studioErrorMessage(task.failure_reason, statusDefinition.detail)
                    : "";
                  const source = sourceOf(task);
                  const promptLabel = task.request_payload?.prompt?.slice(0, 56) || `任务 ${shortId(taskId)}`;
                  return (
                    <tr className="history-ledger-row" key={taskId}>
                      <td data-label="时间">
                        <span className="ledger-time">{shortDate(task.created_at)}</span>
                      </td>
                      <td data-label="任务">
                        <span className="ledger-task">
                          <strong title={liveMode ? taskId : undefined}>{promptLabel}</strong>
                          <small>
                            {liveMode ? `任务 ${shortId(taskId)}` : "演示任务"}
                            {scope === "company"
                              ? ` · ${taskAuthor(task, task.user_id === currentUserId ? currentUserName : "")}`
                              : ""}
                          </small>
                          {attentionDetail ? (
                            <small className={`ledger-attention is-${statusDefinition.tone}`} title={attentionDetail}>
                              {attentionDetail}
                            </small>
                          ) : null}
                          {artifactCount > 0 && !standalonePersonal ? <DownloadStatus source={task} /> : null}
                        </span>
                      </td>
                      <td data-label="来源">{taskSourceBadge(source) || <span className="source-badge is-unknown">未记录</span>}</td>
                      <td data-label="模型">
                        <span className="ledger-model">{modelName}</span>
                      </td>
                      <td data-label="规格">
                        <span className="ledger-specs">{studioTaskParametersLabel(task.request_payload)}</span>
                      </td>
                      <td data-label="积分">
                        <strong className="ledger-cost">{taskCostLabel(task)}</strong>
                      </td>
                      <td data-label="状态">
                        <span className={`ledger-state ${statusClass}`} title={studioErrorMessage(task.failure_reason, statusDefinition.detail)}>
                          <StateIcon
                            className={statusDefinition.active ? "spin" : ""}
                            size={15}
                            aria-hidden="true"
                            weight={stage === "complete" ? "fill" : "regular"}
                          />
                          {state.label}
                        </span>
                      </td>
                      <td data-label="操作">
                        <span className="ledger-actions">
                          <button
                            className="text-button"
                            type="button"
                            onClick={() => stage === "failed" && canCreateTasks ? onRetry?.(task) : onOpen?.(task)}
                          >
                            {stage === "failed" && canCreateTasks ? "恢复为草稿" : "查看任务"}
                          </button>
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {liveMode && !loading && !error && (
        <PageControls page={page} pageSize={pageSize} total={total} onChange={onPageChange} />
      )}
    </section>
  );
}
