import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookmarkSimple,
  CaretDown,
  CheckSquare,
  Cube,
  FilmSlate,
  GridFour,
  ImageSquare,
  Info,
  ListBullets,
  MagnifyingGlass,
  MusicNotes,
  Play,
  Rows,
  WarningCircle,
} from "@phosphor-icons/react";
import { taskCostLabel } from "./taskArtifacts.js";
import { resolveTaskStatus } from "./taskStatus.js";
import { activePreviewUrl } from "./previewLeases.js";

const MEDIA_TABS = [
  { id: "video", label: "视频", icon: FilmSlate },
  { id: "image", label: "图片", icon: ImageSquare },
  {
    id: "audio",
    label: "音频",
    icon: MusicNotes,
    unavailableReason: "音频可作为模型输入，但当前 Platform 尚未开放音频生成任务。",
  },
  {
    id: "app",
    label: "快应用",
    icon: Cube,
    unavailableReason: "快应用创作尚未接入当前 Platform 能力合同。",
  },
  { id: "saved", label: "已保存", icon: BookmarkSimple },
];

function taskId(task) {
  return task?.id || task?.task_id || "";
}

function compactTaskId(task) {
  const value = String(taskId(task));
  if (!value) return "未记录";
  if (value.length <= 12) return value;
  return `${value.slice(0, 7)}…${value.slice(-4)}`;
}

function taskMode(task) {
  return String(task?.request_payload?.mode || task?.mode || "");
}

function taskMedia(task) {
  return taskMode(task) === "text_to_image" ? "image" : "video";
}

function taskDate(task) {
  const value = Date.parse(task?.created_at || task?.updated_at || "");
  return Number.isFinite(value) ? value : 0;
}

function shortDate(value) {
  const parsed = Date.parse(value || "");
  if (!Number.isFinite(parsed)) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function mediaTitle(media) {
  if (media === "image") return "图片";
  if (media === "audio") return "音频";
  if (media === "app") return "快应用";
  if (media === "saved") return "保存内容";
  return "视频";
}

function TaskCard({
  task,
  layout,
  liveMode,
  onOpen,
  previewUrl = "",
  previewLoading = false,
  onRequestPreview,
  onPreviewError,
  artifactAccessAvailable = true,
}) {
  const media = taskMedia(task);
  const MediaIcon = media === "image" ? ImageSquare : FilmSlate;
  const statusDefinition = resolveTaskStatus(task?.status || "accepted");
  const status = statusDefinition.status;
  const prompt = String(task?.request_payload?.prompt || "未填写创作说明").trim();
  const demoPreview = !liveMode
    ? status === "succeeded"
      ? "/media/speaker-water-hero.png"
      : "/media/scene-lifestyle.png"
    : "";

  return (
    <article className={`creation-task-card is-${layout}`}>
      <button
        type="button"
        className="creation-task-preview"
        aria-label={`查看创作记录：${prompt}`}
        onClick={() => onOpen?.(task)}
      >
        {liveMode && media === "video" && previewUrl ? (
          <video
            src={previewUrl}
            muted
            playsInline
            preload="metadata"
            aria-label={`${prompt} 的安全视频预览`}
            onError={onPreviewError}
          />
        ) : previewUrl || demoPreview ? (
          <img
            src={previewUrl || demoPreview}
            alt={liveMode ? `${prompt} 的安全预览` : "演示任务视觉样例"}
            onError={previewUrl ? onPreviewError : undefined}
          />
        ) : (
          <span aria-hidden="true"><MediaIcon size={28} weight="duotone" /></span>
        )}
        <span className={`creation-task-status is-${status} is-${statusDefinition.tone}`}>
          {statusDefinition.label}
        </span>
        {status === "succeeded" && (
          <span className="creation-task-play" aria-hidden="true">
            <Play size={15} weight="fill" />
          </span>
        )}
      </button>
      {liveMode && status === "succeeded" && !previewUrl && task?.output_artifacts?.[0] && (
        artifactAccessAvailable ? (
          <button
            className="creation-task-preview-load"
            type="button"
            disabled={previewLoading}
            onClick={() => onRequestPreview?.(task, task.output_artifacts[0])}
          >
            {previewLoading ? "正在加载" : "加载预览"}
          </button>
        ) : (
          <span className="creation-task-preview-load is-unavailable">预览未开放</span>
        )
      )}
      <div className="creation-task-copy">
        <strong>{prompt}</strong>
        <span>{task?.model_display_name || task?.model_name || "授权模型"}</span>
        <small>
          {shortDate(task?.created_at)}
          <span aria-hidden="true">，</span>
          {taskCostLabel(task)}
          <code title={taskId(task)}>任务 {compactTaskId(task)}</code>
          {!liveMode && <em>演示来源</em>}
        </small>
      </div>
      <button
        className="creation-task-open"
        type="button"
        aria-label={`查看创作记录：${prompt}`}
        onClick={() => onOpen?.(task)}
      >
        查看
      </button>
    </article>
  );
}

export function CreationHub({
  tasks = [],
  models = [],
  loading = false,
  error = "",
  liveMode = false,
  generationMediaKind = "video",
  onGenerationMediaChange,
  onMediaFilterChange,
  onOpenTask,
  onOpenHistory,
  statusFilter = "",
  onStatusFilterChange,
  days = "30",
  onDaysChange,
  modelId = "",
  onModelIdChange,
  onQueryChange,
  page = 1,
  pageSize = 24,
  total = 0,
  onPageChange,
  previewUrls = {},
  previewActionKey = "",
  onRequestPreview,
  onPreviewError,
  supportsSearch = true,
  supportsDateFilter = true,
  artifactAccessAvailable = true,
  onStartCreation,
}) {
  const mediaTabRefs = useRef([]);
  const [media, setMedia] = useState(
    ["video", "image"].includes(generationMediaKind) ? generationMediaKind : "video",
  );
  const [collection, setCollection] = useState("history");
  const [layout, setLayout] = useState("grid");
  const [grouping, setGrouping] = useState("none");

  useEffect(() => {
    if (!["video", "image"].includes(generationMediaKind)) return;
    setMedia(generationMediaKind);
    onMediaFilterChange?.(generationMediaKind);
  }, [generationMediaKind]);

  const selectMedia = (nextMedia) => {
    if (MEDIA_TABS.find((item) => item.id === nextMedia)?.unavailableReason) {
      return false;
    }
    if (
      ["video", "image"].includes(nextMedia) &&
      onGenerationMediaChange?.(nextMedia) === false
    ) {
      return false;
    }
    setMedia(nextMedia);
    onMediaFilterChange?.(
      nextMedia === "image" ? "image" : nextMedia === "video" ? "video" : "",
    );
    return true;
  };

  const handleMediaTabKeyDown = (event, index) => {
    const availableIndexes = MEDIA_TABS.flatMap((item, itemIndex) => (
      item.unavailableReason ? [] : [itemIndex]
    ));
    const currentPosition = Math.max(0, availableIndexes.indexOf(index));
    let nextPosition = null;
    if (event.key === "ArrowRight") {
      nextPosition = (currentPosition + 1) % availableIndexes.length;
    }
    if (event.key === "ArrowLeft") {
      nextPosition = (currentPosition - 1 + availableIndexes.length) % availableIndexes.length;
    }
    if (event.key === "Home") nextPosition = 0;
    if (event.key === "End") nextPosition = availableIndexes.length - 1;
    const nextIndex = nextPosition === null ? null : availableIndexes[nextPosition];
    if (nextIndex === null) return;

    event.preventDefault();
    if (!selectMedia(MEDIA_TABS[nextIndex].id)) return;
    const focusNextTab = () => mediaTabRefs.current[nextIndex]?.focus();
    if (globalThis.requestAnimationFrame) {
      globalThis.requestAnimationFrame(focusNextTab);
    } else {
      focusNextTab();
    }
  };
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const queryChangeRef = useRef(onQueryChange);
  queryChangeRef.current = onQueryChange;

  useEffect(() => {
    if (!supportsSearch) return undefined;
    const timer = globalThis.setTimeout?.(() => queryChangeRef.current?.(query), 300);
    return () => globalThis.clearTimeout?.(timer);
  }, [query, supportsSearch]);

  const visibleTasks = useMemo(() => {
    if (collection === "uploads" || ["audio", "app"].includes(media)) return [];
    const now = Date.now();
    const cutoff = days === "all" ? 0 : now - Number(days) * 86_400_000;
    return [...tasks]
      .filter((task) => {
        if (media === "saved" && task?.status !== "succeeded") return false;
        if (!liveMode && ["video", "image"].includes(media) && taskMedia(task) !== media) return false;
        if (!liveMode && statusFilter && String(task?.status || "accepted") !== statusFilter) return false;
        if (!liveMode && modelId && String(task?.model_id || "") !== modelId) return false;
        if (!liveMode && cutoff && taskDate(task) < cutoff) return false;
        if (!liveMode && query.trim()) {
          const haystack = [
            task?.request_payload?.prompt,
            task?.model_display_name,
            task?.model_name,
            taskId(task),
          ].join(" ").toLocaleLowerCase();
          if (!haystack.includes(query.trim().toLocaleLowerCase())) return false;
        }
        return true;
      })
      .sort((left, right) => {
        if (grouping === "model") {
          const compared = String(left?.model_display_name || left?.model_name || "")
            .localeCompare(String(right?.model_display_name || right?.model_name || ""), "zh-CN");
          if (compared) return compared;
        }
        if (grouping === "status") {
          const compared = String(left?.status || "accepted")
            .localeCompare(String(right?.status || "accepted"));
          if (compared) return compared;
        }
        return taskDate(right) - taskDate(left);
      });
  }, [collection, days, grouping, liveMode, media, modelId, query, statusFilter, tasks]);

  const emptyName = mediaTitle(media);

  return (
    <section
      className="creation-hub"
      aria-labelledby="creation-hub-title"
      aria-busy={loading}
    >
      <div className="creation-hub-titlebar">
        <div className="creation-hub-heading">
          <h1 id="creation-hub-title">创作</h1>
          <p>查看生成任务、筛选创作记录，并继续处理已保存的内容。</p>
        </div>
        <div className="creation-title-tools">
          <div className="creation-title-meta" aria-live="polite">
            <strong>{total || visibleTasks.length}</strong>
            <span>条记录</span>
          </div>
        {supportsSearch && <div className={`creation-search ${searchOpen ? "is-open" : ""}`}>
          {searchOpen && (
            <input
              id="creation-record-search"
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索创作记录"
              aria-label="搜索创作记录"
            />
          )}
          <button
            type="button"
            aria-label={searchOpen ? "关闭搜索" : "打开搜索"}
            aria-expanded={searchOpen}
            aria-controls={searchOpen ? "creation-record-search" : undefined}
            onClick={() => {
              setSearchOpen((value) => !value);
              if (searchOpen) {
                setQuery("");
              }
            }}
          >
            <MagnifyingGlass size={19} aria-hidden="true" />
          </button>
        </div>}
        </div>
      </div>

      <header className="creation-hub-header">
        <div className="creation-media-tabs" role="tablist" aria-label="创作内容类型">
          {MEDIA_TABS.map(({ id, label, icon: Icon, unavailableReason }, index) => (
            <button
              key={id}
              ref={(element) => { mediaTabRefs.current[index] = element; }}
              id={`creation-media-tab-${id}`}
              type="button"
              role="tab"
              aria-selected={media === id}
              aria-controls="creation-hub-panel"
              tabIndex={media === id ? 0 : -1}
              className={media === id ? "is-active" : ""}
              disabled={Boolean(unavailableReason)}
              aria-disabled={unavailableReason ? "true" : undefined}
              aria-describedby={unavailableReason ? "creation-capability-note" : undefined}
              title={unavailableReason}
              onClick={() => selectMedia(id)}
              onKeyDown={(event) => handleMediaTabKeyDown(event, index)}
            >
              <Icon size={17} aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>
        <p className="creation-capability-note" id="creation-capability-note">
          <Info size={15} aria-hidden="true" />
          音频与快应用会在 Platform 发布对应能力合同后开放。
        </p>
      </header>

      <div className="creation-toolbar">
        <div className="creation-toolbar-left">
          <label className="creation-select creation-filter-button">
            <span className="visually-hidden">任务状态</span>
            <select value={statusFilter} onChange={(event) => onStatusFilterChange?.(event.target.value)}>
              <option value="">全部</option>
              <option value="succeeded">已完成</option>
              <option value="processing">生成中</option>
              <option value="queued">排队中</option>
              <option value="failed">失败</option>
              <option value="cancelled">已取消</option>
              <option value="timed_out">已超时</option>
              <option value="reconciliation_required">待人工确认</option>
            </select>
            <CaretDown size={14} aria-hidden="true" />
          </label>
          <button
            className={collection === "history" ? "is-active" : ""}
            type="button"
            onClick={() => setCollection("history")}
          >
            历史创作
          </button>
          <button
            type="button"
            disabled
            title="上传记录请前往素材页查看"
          >
            素材上传记录
          </button>
        </div>

        <div className="creation-toolbar-right">
          {supportsDateFilter && <label className="creation-select">
            <span className="visually-hidden">时间范围</span>
            <select value={days} onChange={(event) => onDaysChange?.(event.target.value)}>
              <option value="7">最近 7 天</option>
              <option value="30">最近 30 天</option>
              <option value="90">最近 90 天</option>
              <option value="all">全部时间</option>
            </select>
            <CaretDown size={14} aria-hidden="true" />
          </label>}
          <label className="creation-select">
            <span className="visually-hidden">模型</span>
            <select value={modelId} onChange={(event) => onModelIdChange?.(event.target.value)}>
              <option value="">全部模型</option>
              {models.map((model) => (
                <option key={model.id} value={model.id}>{model.name || model.display_name}</option>
              ))}
            </select>
            <CaretDown size={14} aria-hidden="true" />
          </label>
          <div className="creation-layout-switch" aria-label="展示方式">
            <button
              type="button"
              aria-label="网格视图"
              aria-pressed={layout === "grid"}
              className={layout === "grid" ? "is-active" : ""}
              onClick={() => setLayout("grid")}
            ><GridFour size={17} aria-hidden="true" /></button>
            <button
              type="button"
              aria-label="紧凑视图"
              aria-pressed={layout === "compact"}
              className={layout === "compact" ? "is-active" : ""}
              onClick={() => setLayout("compact")}
            ><Rows size={17} aria-hidden="true" /></button>
            <button
              type="button"
              aria-label="列表视图"
              aria-pressed={layout === "list"}
              className={layout === "list" ? "is-active" : ""}
              onClick={() => setLayout("list")}
            ><ListBullets size={17} aria-hidden="true" /></button>
          </div>
          <label className="creation-select creation-group-select">
            <span className="visually-hidden">分组方式</span>
            <select value={grouping} onChange={(event) => setGrouping(event.target.value)}>
              <option value="none">分组方式：无</option>
              <option value="model">按模型</option>
              <option value="status">按状态</option>
            </select>
            <CaretDown size={14} aria-hidden="true" />
          </label>
          <button
            className="creation-select-toggle"
            type="button"
            disabled
            title="批量操作尚未开放"
          >
            <CheckSquare size={17} aria-hidden="true" />
            选择
          </button>
        </div>
      </div>

      <div
        className="creation-hub-body"
        id="creation-hub-panel"
        role="tabpanel"
        aria-labelledby={`creation-media-tab-${media}`}
      >
        {loading ? (
          <div className="creation-record-skeleton" role="status" aria-label="正在读取创作记录">
            <span className="visually-hidden">正在读取创作记录</span>
            {[0, 1, 2].map((item) => <div key={item} aria-hidden="true">
              <span />
              <span />
              <span />
            </div>)}
          </div>
        ) : error ? (
          <div className="creation-hub-state is-error" role="alert">
            <WarningCircle size={28} weight="fill" aria-hidden="true" />
            <strong>创作记录读取失败</strong>
            <span>{error}</span>
            <button type="button" onClick={onOpenHistory}>打开详细记录</button>
          </div>
        ) : visibleTasks.length === 0 ? (
          <div className="creation-hub-state">
            <FilmSlate size={30} aria-hidden="true" />
            <strong>{media === "saved" ? "还没有已保存内容" : `未找到${emptyName}`}</strong>
            <p>
              {media === "saved"
                ? "成功任务完成平台归档后，会出现在“已保存”中。"
                : `调整筛选条件，或开始创建您的第一个${emptyName}。`}
            </p>
            {!["saved", "audio", "app"].includes(media) && (
              <button type="button" onClick={onStartCreation}>开始创作</button>
            )}
          </div>
        ) : (
          <div className={`creation-task-grid is-${layout}`} aria-live="polite">
            {visibleTasks.map((task) => {
              const previewKey = `${taskId(task)}:${task?.output_artifacts?.[0]?.asset_id}`;
              return (
                <TaskCard
                  key={taskId(task)}
                  task={task}
                  layout={layout}
                  liveMode={liveMode}
                  onOpen={onOpenTask}
                  previewUrl={activePreviewUrl(previewUrls[previewKey])}
                  previewLoading={previewActionKey === `preview:${previewKey}`}
                  onRequestPreview={onRequestPreview}
                  onPreviewError={() => onPreviewError?.(previewKey)}
                  artifactAccessAvailable={artifactAccessAvailable}
                />
              );
            })}
          </div>
        )}
      </div>

      {collection === "history" && total > pageSize && (
        <nav className="creation-pagination" aria-label="创作记录分页">
          <button
            type="button"
            disabled={page <= 1 || loading}
            onClick={() => onPageChange?.(page - 1)}
          >
            上一页
          </button>
          <span aria-live="polite">
            第 {page} / {Math.max(1, Math.ceil(total / pageSize))} 页 · 共 {total} 条
          </span>
          <button
            type="button"
            disabled={page * pageSize >= total || loading}
            onClick={() => onPageChange?.(page + 1)}
          >
            下一页
          </button>
        </nav>
      )}

    </section>
  );
}
