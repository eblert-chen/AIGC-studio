import { ClockCounterClockwise, WarningCircle } from "@phosphor-icons/react";
import {
  taskAuthor,
  taskCompany,
  taskCostLabel,
  taskParametersLabel,
} from "../../taskArtifacts.js";
import { resolveTaskStatus } from "../../taskStatus.js";
import {
  DownloadBadge,
  LoadingRows,
  PageControls,
  ScopeControl,
} from "../../components/studio/StudioCollectionControls.jsx";
import { shortDate, shortId } from "../../components/studio/studioPresentation.js";

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
  companyName = "",
  currentUserId = "",
  currentUserName = "",
  workspaceKind = "company",
  statusDefinitions,
}) {
  const displayedTasks = liveMode ? tasks : demoTasks;
  return (
    <section className="secondary-view history-view">
      <div className="secondary-heading">
        <div>
          <span className="view-kicker">生成记录</span>
          <h1>任务历史</h1>
          <p>
            {workspaceKind === "personal"
              ? "只显示当前个人空间的任务、模型、原始参数、状态与积分消耗。"
              : "记录发起人、公司、模型、原始参数、状态与最终费用。"}
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
      <div className="history-list">
        {loading && <LoadingRows label="正在读取任务历史" />}
        {!loading && error && (
          <div className="artifact-empty" role="alert">
            <WarningCircle size={26} aria-hidden="true" />
            <strong>任务历史读取失败</strong>
            <span>{error}</span>
          </div>
        )}
        {!loading && !error && displayedTasks.length === 0 && (
          <div className="artifact-empty">
            <ClockCounterClockwise size={26} aria-hidden="true" />
            <strong>当前范围还没有任务</strong>
            <span>提交第一条任务后会显示在这里。</span>
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
                  <small title={taskId}>任务 {shortId(taskId)}，{shortDate(task.created_at)}</small>
                </span>
                <span className={statusClass} title={task.failure_reason || statusDefinition.detail}>
                  {state.label}
                </span>
                <strong className="history-cost">{taskCostLabel(task)}</strong>
              </header>
              <dl className="task-audit-grid">
                <div><dt>发起人</dt><dd>{taskAuthor(task, task.user_id === currentUserId ? currentUserName : "")}</dd></div>
                <div>
                  <dt>{workspaceKind === "personal" ? "空间" : "公司"}</dt>
                  <dd title={task.company_id || task.workspace_id}>{taskCompany(task, companyName)}</dd>
                </div>
                <div><dt>模型</dt><dd>{modelName}</dd></div>
                <div><dt>参数</dt><dd>{taskParametersLabel(task.request_payload)}</dd></div>
              </dl>
              <footer>
                <span>{artifactCount > 0 ? `${artifactCount} 个归档产物` : "暂无归档产物"}</span>
                {artifactCount > 0 && <DownloadBadge source={task} />}
                <button
                  className="text-button"
                  type="button"
                  disabled={stage === "failed" && !canCreateTasks}
                  title={stage === "failed" && !canCreateTasks ? "缺少 tasks.create 权限" : undefined}
                  onClick={() => stage === "failed" ? onRetry?.(task) : onOpen?.(task)}
                >
                  {stage === "failed" ? "按原参数重试" : "查看任务"}
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
