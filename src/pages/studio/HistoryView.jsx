import { ClockCounterClockwise, WarningCircle } from "@phosphor-icons/react";
import {
  taskAuthor,
  taskCostLabel,
} from "../../taskArtifacts.js";
import { resolveTaskStatus } from "../../taskStatus.js";
import { LoadingRows, PageControls, ScopeControl } from "../../components/studio/StudioCollectionControls.jsx";
import { DownloadStatus } from "../../components/studio/StudioWorkspaceViews.jsx";
import {
  shortDate,
  shortId,
  studioErrorMessage,
  studioTaskParametersLabel,
} from "../../components/studio/studioPresentation.js";

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
  statusDefinitions,
}) {
  const displayedTasks = liveMode ? tasks : demoTasks;
  const hasActiveFilters = Boolean(statusFilter);
  const attentionCount = displayedTasks.filter((task) => (
    ["failed", "timed_out", "reconciliation_required", "unknown"].includes(String(task?.status || ""))
  )).length;
  return (
    <section className="secondary-view history-view" aria-labelledby="history-title" aria-busy={loading}>
      <div className="secondary-heading">
        <div>
          <h1 id="history-title">历史</h1>
          <p>
            {!liveMode
              ? "以下为示例任务，用于体验状态、费用与详情。"
              : workspaceKind === "personal"
                ? "按时间查看当前个人空间的任务、状态与积分消耗。"
                : "按时间查看任务状态、模型与最终费用。"}
          </p>
        </div>
        <div className="history-toolbar">
          <ScopeControl
            value={scope}
            onChange={onScopeChange}
            canViewCompany={canViewCompany}
          />
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
        </div>
      </div>
      {!loading && !error && displayedTasks.length > 0 ? (
        <div className="history-summary" aria-label="当前任务摘要">
          <span>当前页 <strong>{displayedTasks.length}</strong> 条任务</span>
          <span>{attentionCount > 0 ? <><strong>{attentionCount}</strong> 条需要关注</> : "当前页没有异常任务"}</span>
        </div>
      ) : null}
      <div className="history-list">
        {loading && <LoadingRows label="正在读取任务历史" />}
        {!loading && error && (
          <div className="artifact-empty" role="alert">
            <WarningCircle size={26} aria-hidden="true" />
            <strong>任务历史读取失败</strong>
            <span>{studioErrorMessage(error, "任务历史暂时无法读取，请稍后重试。")}</span>
          </div>
        )}
        {!loading && !error && displayedTasks.length === 0 && (
          <div className="artifact-empty">
            <ClockCounterClockwise size={26} aria-hidden="true" />
            <strong>{hasActiveFilters ? "没有符合当前筛选的任务" : "还没有任务"}</strong>
            <span>{hasActiveFilters ? "清除筛选即可查看全部任务。" : "提交第一个创作任务后，记录会显示在这里。"}</span>
            {hasActiveFilters ? (
              <button className="text-button" type="button" onClick={() => onStatusChange("")}>
                清除筛选
              </button>
            ) : null}
          </div>
        )}
        {!loading && !error && displayedTasks.map((task) => {
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
          const attentionDetail = ["failed", "timed_out", "reconciliation_required", "unknown"].includes(statusDefinition.status)
            ? studioErrorMessage(task.failure_reason, statusDefinition.detail)
            : "";
          const artifactSummary = artifactCount > 0
            ? `${artifactCount} 个作品文件`
            : statusDefinition.status === "succeeded"
              ? "未找到已保存作品"
              : statusDefinition.active
                ? "作品生成中"
                : "没有生成作品";
          return (
            <article className="task-history-row" key={taskId}>
              <header>
                <span className={`task-row-state is-${stage}`} aria-hidden="true">
                  <StateIcon
                    className={statusDefinition.active ? "spin" : ""}
                    size={19}
                    weight={stage === "complete" ? "fill" : "regular"}
                  />
                </span>
                <span className="task-row-title">
                  <strong>{task.request_payload?.prompt?.slice(0, 56) || `任务 ${shortId(taskId)}`}</strong>
                  <small title={liveMode ? taskId : undefined}>
                    {liveMode ? `任务 ${shortId(taskId)}` : "演示任务"}，{shortDate(task.created_at)}
                  </small>
                </span>
                <span className={statusClass} title={studioErrorMessage(task.failure_reason, statusDefinition.detail)}>
                  {state.label}
                </span>
                <strong className="history-cost">{taskCostLabel(task)}</strong>
              </header>
              <dl className="task-audit-grid">
                {scope === "company" && (
                  <div><dt>发起人</dt><dd>{taskAuthor(task, task.user_id === currentUserId ? currentUserName : "")}</dd></div>
                )}
                <div><dt>模型</dt><dd>{modelName}</dd></div>
                <div><dt>创作设置</dt><dd>{studioTaskParametersLabel(task.request_payload)}</dd></div>
              </dl>
              <footer>
                <span className={`task-row-detail ${attentionDetail ? `is-${statusDefinition.tone}` : ""}`}>
                  {attentionDetail || artifactSummary}
                </span>
                {artifactCount > 0 && <DownloadStatus source={task} />}
                <button
                  className="text-button"
                  type="button"
                  onClick={() => stage === "failed" && canCreateTasks ? onRetry?.(task) : onOpen?.(task)}
                >
                  {stage === "failed" && canCreateTasks ? "恢复为草稿" : "查看任务"}
                </button>
              </footer>
            </article>
          );
        })}
      </div>
      {liveMode && !loading && !error && (
        <PageControls page={page} pageSize={pageSize} total={total} onChange={onPageChange} />
      )}
    </section>
  );
}
