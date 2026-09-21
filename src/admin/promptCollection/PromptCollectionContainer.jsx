import { useCallback, useEffect, useRef, useState } from "react";
import {
  CaretLeft,
  CaretRight,
  Database,
  Funnel,
  MagnifyingGlass,
} from "@phosphor-icons/react";
import { adminRangeWindow } from "../adminApiAdapter.js";
import { formatDateTime, formatInteger } from "../adminConsoleUtils.js";
import {
  adaptTaskContentPage,
  buildTaskContentListFilters,
  taskContentWorkspaceLabel,
} from "../taskContentCollection.js";
import {
  compactIdentifier,
  DatasetState,
  EmptyState,
  PanelHeader,
  StatusPill,
  TableScroller,
} from "../operations/operationsShared.jsx";

const PAGE_SIZE = 25;
const PROMPT_COLLAPSE_LENGTH = 160;
const EMPTY_PAGE = Object.freeze({
  page: 1,
  pageSize: PAGE_SIZE,
  pageCount: 1,
  total: 0,
  items: [],
});

const TASK_STATUS_OPTIONS = [
  ["all", "全部任务状态"],
  ["draft", "等待提交"],
  ["queued", "排队中"],
  ["processing", "生成中"],
  ["succeeded", "生成完成"],
  ["failed", "生成失败"],
  ["cancelled", "已取消"],
];

function datasetStatus(status) {
  if (status === "loading") return "loading";
  if (status === "unauthorized") return "unauthorized";
  if (status === "failed") return "failed";
  if (status === "unavailable") return "unavailable";
  return "failed";
}

function taskContentErrorMessage(error, fallback) {
  if (error?.status === 401) return "管理员会话已失效，请重新登录。";
  if (error?.status === 403) return "当前平台管理员没有查看提示词收集库的权限。";
  if (error?.status === 422) return "筛选条件未通过服务端校验，请调整后重试。";
  return error?.message || fallback;
}

function taskStatusPillTone(tone) {
  if (tone === "success") return "success";
  if (tone === "danger") return "critical";
  if (tone === "progress") return "active";
  if (tone === "warning") return "warning";
  return "unknown";
}

function collectionCountLabel(status, total) {
  if (status === "available") return `${formatInteger(total)} 条`;
  if (status === "loading") return "读取中";
  if (status === "unauthorized") return "无权访问";
  if (status === "failed") return "加载失败";
  return "待加载";
}

function promptExcerpt(prompt) {
  const normalized = prompt.replace(/\s+/g, " ").trim();
  const characters = [...normalized];
  return characters.length > PROMPT_COLLAPSE_LENGTH
    ? `${characters.slice(0, PROMPT_COLLAPSE_LENGTH).join("")}…`
    : normalized;
}

function PromptContent({ item }) {
  if (!item.prompt) {
    return <span className="ops-prompt-unavailable">未记录提示词</span>;
  }
  if (item.promptLength <= PROMPT_COLLAPSE_LENGTH) {
    return (
      <div className="ops-prompt-entry">
        <p>{item.prompt}</p>
        <small>{formatInteger(item.promptLength)} 字</small>
      </div>
    );
  }
  return (
    <details className="ops-prompt-entry is-expandable">
      <summary>
        <span>{promptExcerpt(item.prompt)}</span>
        <em className="ops-prompt-expand-label">展开全文</em>
        <em className="ops-prompt-collapse-label">收起全文</em>
      </summary>
      <p>{item.prompt}</p>
      <small>{formatInteger(item.promptLength)} 字</small>
    </details>
  );
}

function demoPage(items, filters) {
  const filtered = (Array.isArray(items) ? items : []).filter((item) => (
    (filters.workspaceType === "all" || item.workspace_type === filters.workspaceType)
    && (filters.status === "all" || item.status === filters.status)
    && (!filters.userId || item.user_id === filters.userId)
    && (!filters.taskId || item.task_id === filters.taskId)
  ));
  const offset = (filters.page - 1) * PAGE_SIZE;
  return {
    page: filters.page,
    page_size: PAGE_SIZE,
    total: filtered.length,
    items: filtered.slice(offset, offset + PAGE_SIZE),
  };
}

function PromptCollectionScreen({
  page,
  sourceStatus,
  sourceError,
  draft,
  onDraft,
  onApply,
  onPage,
  onRetry,
  demoMode,
}) {
  const hasPrevious = page.page > 1;
  const hasNext = page.page < page.pageCount;
  return (
    <div className="ops-prompt-collection">
      {demoMode ? (
        <div className="ops-prompt-demo-banner" role="status">
          <Database size={18} aria-hidden="true" />演示模式：当前提示词均为合成数据。
        </div>
      ) : null}
      <section className="ops-panel ops-prompt-ledger">
        <PanelHeader
          title="已收集的生成提示词"
          detail="自动汇集用户随生成任务提交的提示词，可按用户、工作区、任务状态和任务 ID 定位。"
          action={<span className="ops-queue-count">{collectionCountLabel(sourceStatus, page.total)}</span>}
        />
        <form className="ops-filterbar ops-prompt-filterbar" onSubmit={onApply}>
          <label>
            <span className="sr-only">筛选工作区类型</span>
            <Funnel size={15} aria-hidden="true" />
            <select value={draft.workspaceType} onChange={(event) => onDraft({ workspaceType: event.target.value })}>
              <option value="all">全部工作区</option>
              <option value="company">企业工作区</option>
              <option value="personal">个人工作区</option>
            </select>
          </label>
          <label>
            <span className="sr-only">筛选任务状态</span>
            <select value={draft.status} onChange={(event) => onDraft({ status: event.target.value })}>
              {TASK_STATUS_OPTIONS.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label className="ops-search">
            <span className="sr-only">按完整用户 ID 筛选</span>
            <MagnifyingGlass size={16} aria-hidden="true" />
            <input
              value={draft.userId}
              onChange={(event) => onDraft({ userId: event.target.value })}
              placeholder="完整用户 ID"
              maxLength={36}
              autoComplete="off"
            />
          </label>
          <label className="ops-search">
            <span className="sr-only">按完整任务 ID 筛选</span>
            <MagnifyingGlass size={16} aria-hidden="true" />
            <input
              value={draft.taskId}
              onChange={(event) => onDraft({ taskId: event.target.value })}
              placeholder="完整任务 ID"
              maxLength={36}
              autoComplete="off"
            />
          </label>
          <button className="ops-secondary-button" type="submit">应用筛选</button>
        </form>
        {sourceStatus !== "available" ? (
          <DatasetState status={datasetStatus(sourceStatus)} label="提示词收集记录" detail={sourceError} onRetry={onRetry} />
        ) : (
          <>
            <TableScroller label="用户生成提示词收集记录" showScrollHint>
              <table className="ops-table ops-prompt-table">
                <thead>
                  <tr><th>提交时间</th><th>提示词</th><th>用户</th><th>工作区</th><th>模型 / 状态</th></tr>
                </thead>
                <tbody>
                  {page.items.map((item) => (
                    <tr key={item.taskId}>
                      <td><strong>{formatDateTime(item.createdAt)}</strong><small>{compactIdentifier(item.taskId)}</small></td>
                      <td><PromptContent item={item} /></td>
                      <td><strong>{item.userDisplayName}</strong><small>{compactIdentifier(item.userId)}</small></td>
                      <td><strong>{taskContentWorkspaceLabel(item)}</strong><small>{item.workspaceType === "company" ? "企业" : "个人"}</small></td>
                      <td><strong>{item.modelDisplayName}</strong><small><StatusPill value={taskStatusPillTone(item.statusPresentation.tone)} label={item.statusPresentation.label} /></small></td>
                    </tr>
                  ))}
                  {!page.items.length ? (
                    <tr><td colSpan="5"><EmptyState title="没有匹配的提示词" detail="调整时间、用户、工作区、状态或任务 ID 后重试。" /></td></tr>
                  ) : null}
                </tbody>
              </table>
            </TableScroller>
            <div className="ops-table-footer ops-prompt-pagination">
              <span>第 {page.page} / {page.pageCount} 页 · 共 {formatInteger(page.total)} 条</span>
              <div>
                <button type="button" onClick={() => onPage(page.page - 1)} disabled={!hasPrevious} aria-label="上一页提示词记录"><CaretLeft size={16} />上一页</button>
                <button type="button" onClick={() => onPage(page.page + 1)} disabled={!hasNext} aria-label="下一页提示词记录">下一页<CaretRight size={16} /></button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

export function PromptCollectionContainer({
  active,
  client,
  range,
  demoMode = false,
  demoItems = [],
  onAuthenticationError,
}) {
  const [draft, setDraft] = useState({ workspaceType: "all", status: "all", userId: "", taskId: "" });
  const [filters, setFilters] = useState({ workspaceType: "all", status: "all", userId: "", taskId: "", page: 1 });
  const [page, setPage] = useState(EMPTY_PAGE);
  const [sourceStatus, setSourceStatus] = useState("unavailable");
  const [sourceError, setSourceError] = useState("");
  const loadSequence = useRef(0);
  const requestAbortRef = useRef(null);

  const abortLoad = useCallback(() => {
    loadSequence.current += 1;
    requestAbortRef.current?.abort();
    requestAbortRef.current = null;
  }, []);

  const load = useCallback(async () => {
    if (!active) return;
    requestAbortRef.current?.abort();
    const controller = new AbortController();
    requestAbortRef.current = controller;
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setSourceStatus("loading");
    setSourceError("");
    setPage(EMPTY_PAGE);
    try {
      const window = adminRangeWindow(range);
      const payload = demoMode
        ? demoPage(demoItems, filters)
        : await client.listAdminTaskContent(buildTaskContentListFilters({
          ...filters,
          pageSize: PAGE_SIZE,
          createdFrom: window.start_time,
          createdBefore: window.end_time,
        }), { signal: controller.signal });
      if (sequence !== loadSequence.current || controller.signal.aborted) return;
      setPage(adaptTaskContentPage(payload));
      setSourceStatus("available");
    } catch (error) {
      if (controller.signal.aborted || error?.name === "AbortError") return;
      if (sequence !== loadSequence.current) return;
      const accessLost = error?.status === 401 || error?.status === 403;
      const handled = onAuthenticationError?.(error) === true;
      setPage(EMPTY_PAGE);
      setSourceStatus(accessLost ? "unauthorized" : "failed");
      setSourceError(handled ? "正在重新验证管理员身份…" : taskContentErrorMessage(error, "提示词收集记录加载失败，请重试。"));
    } finally {
      if (requestAbortRef.current === controller) requestAbortRef.current = null;
    }
  }, [active, client, demoItems, demoMode, filters, onAuthenticationError, range]);

  useEffect(() => {
    if (!active) {
      abortLoad();
      setPage(EMPTY_PAGE);
      setSourceStatus("unavailable");
      setSourceError("");
      return undefined;
    }
    load();
    return abortLoad;
  }, [abortLoad, active, load]);

  const applyFilters = useCallback((event) => {
    event.preventDefault();
    setFilters({ ...draft, userId: draft.userId.trim(), taskId: draft.taskId.trim(), page: 1 });
  }, [draft]);

  return (
    <PromptCollectionScreen
      page={page}
      sourceStatus={sourceStatus}
      sourceError={sourceError}
      draft={draft}
      onDraft={(change) => setDraft((current) => ({ ...current, ...change }))}
      onApply={applyFilters}
      onPage={(nextPage) => setFilters((current) => ({ ...current, page: nextPage }))}
      onRetry={() => load()}
      demoMode={demoMode}
    />
  );
}

export default PromptCollectionContainer;
