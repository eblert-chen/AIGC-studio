import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowSquareOut,
  ArrowUp,
  ArrowDown,
  BookmarkSimple,
  CaretDown,
  CheckCircle,
  CircleNotch,
  ClipboardText,
  Cube,
  DownloadSimple,
  FilmSlate,
  FolderPlus,
  GitBranch,
  ImageSquare,
  MagnifyingGlass,
  PaperPlaneTilt,
  PencilSimple,
  Plus,
  SlidersHorizontal,
  Queue,
  WarningCircle,
} from "@phosphor-icons/react";
import StoryAiDirectorDesk from "../../components/director3d/StoryAiDirectorDesk.jsx";

const LazyLineageGraphWorkspace = lazy(() => import("./LineageGraphWorkspace.jsx"));

const PATHS = [
  {
    id: "console",
    title: "3D 导演台",
    subtitle: "打磨一个镜头",
    description: "对着参考与结果改场记，比较同一镜头的不同版本。",
    icon: FilmSlate,
  },
  {
    id: "notebook",
    title: "导演手记",
    subtitle: "按场次推进",
    description: "把故事分场写作，在每一场里生成、重拍和选片。",
    icon: ClipboardText,
  },
  {
    id: "canvas",
    title: "谱系图谱",
    subtitle: "看清创作选择",
    description: "从精确 Take 另开方向，比较差异并拍板镜头主线。",
    icon: GitBranch,
  },
];

const ENTRY_IDEAS = [
  {
    id: "product-launch",
    category: "产品",
    title: "新品发布宣传片",
    description: "旋转展示 · 光影氛围",
    image: "/community/cobalt-fragrance.webp",
    prompt: "新品在柔和水光中缓慢旋转，镜头从材质细节拉到完整产品，光影克制，适合品牌发布。",
  },
  {
    id: "city-timelapse",
    category: "城市",
    title: "城市延时大片",
    description: "昼夜交替 · 车流光轨",
    image: "/community/last-tram-platform.webp",
    prompt: "城市从蓝调时刻进入夜晚，镜头缓慢推进，立交桥车流形成光轨，电影感宽银幕构图。",
  },
  {
    id: "lifestyle-story",
    category: "生活",
    title: "生活方式短片",
    description: "轻松治愈 · 竖屏叙事",
    image: "/community/ceramic-audio.webp",
    prompt: "清晨的生活方式场景，柔和自然光，人物与产品轻松互动，竖屏近景，节奏自然。",
  },
  {
    id: "space-sunrise",
    category: "科幻",
    title: "太空站日出",
    description: "极简结构 · 电影光效",
    image: "/community/salt-observatory.webp",
    prompt: "太空观测站迎来日出，镜头沿结构缓慢横移，远处星球边缘亮起，真实电影摄影质感。",
  },
];

function taskPreviewAlt(take, liveMode) {
  if (!liveMode) return "演示任务视觉样例";
  return `${take.prompt || take.label}的任务预览`;
}

export function CreationTaskVisual({
  take,
  liveMode,
  compact = false,
  allowPreviewLoad = true,
  artifactAccessAvailable,
  previewActionKey,
  onRequestPreview,
  onPreviewError,
}) {
  if (!take) {
    return (
      <div className={`creation-task-visual ${compact ? "is-compact" : ""} is-empty`}>
        <FilmSlate size={compact ? 24 : 32} aria-hidden="true" />
        <span>等待第一条任务</span>
      </div>
    );
  }

  const source = take.previewUrl || take.demoPreview;
  const canLoadPreview = Boolean(
    allowPreviewLoad
      && liveMode
      && take.status === "succeeded"
      && !take.previewUrl
      && take.artifact,
  );
  const previewPending = previewActionKey === `preview:${take.previewKey}`;

  return (
    <div className={`creation-task-visual ${compact ? "is-compact" : ""} is-${take.statusTone}`}>
      {take.previewUrl && take.media === "video" ? (
        <video
          src={take.previewUrl}
          controls
          muted
          playsInline
          preload="metadata"
          aria-label={`${take.prompt || take.label}的视频预览`}
          onError={onPreviewError}
        />
      ) : source ? (
        <img
          src={source}
          alt={taskPreviewAlt(take, liveMode)}
          onError={take.previewUrl ? onPreviewError : undefined}
        />
      ) : (
        <div className="creation-task-visual-placeholder">
          {["processing", "queued", "accepted"].includes(take.status) ? (
            <CircleNotch className="spin" size={compact ? 22 : 30} aria-hidden="true" />
          ) : take.media === "image" ? (
            <ImageSquare size={compact ? 24 : 32} aria-hidden="true" />
          ) : (
            <FilmSlate size={compact ? 24 : 32} aria-hidden="true" />
          )}
          <strong>{take.statusLabel}</strong>
          {!compact && <span>{take.statusDetail}</span>}
        </div>
      )}
      {canLoadPreview && (
        artifactAccessAvailable ? (
          <button
            className="creation-task-preview-load"
            type="button"
            disabled={previewPending}
            onClick={() => onRequestPreview?.(take.task, take.artifact)}
          >
            {previewPending ? "正在加载预览" : "加载预览"}
          </button>
        ) : (
          <span className="creation-task-preview-note">当前空间暂不支持预览</span>
        )
      )}
    </div>
  );
}

function TaskMetaLine({ take }) {
  if (!take) return null;
  return (
    <span className="creation-task-meta-line">
      <span>{take.statusLabel}</span>
      <span>{take.modeLabel}</span>
      <span>{take.dateLabel}</span>
    </span>
  );
}

export function CreationEntryView({
  takes,
  activeTake,
  liveMode,
  onOpenWorkbench,
  onSelectTake,
  onOpenTask,
  onContinueTask,
  onUsePrompt,
  artifactAccessAvailable,
  previewActionKey,
  onRequestPreview,
  onPreviewError,
  workbenchController,
  advancedWorkbenchesDesktopOnly = false,
}) {
  const recentTakes = takes.slice(0, 3);

  return (
    <div className="creation-entry" data-ui="creation-invitation-entry">
      <section className="creation-entry-hero" aria-labelledby="creation-entry-title">
        <div className="creation-entry-copy">
          <div>
            <h2 id="creation-entry-title">今天想创作什么？</h2>
            <p>从一句描述开始，也可以选一条灵感。</p>
          </div>
        </div>

        <section className="wb-entry-ways" aria-labelledby="creation-work-modes-title">
          <header><h3 id="creation-work-modes-title">工作方式</h3><span>选择适合这次创作的工作台</span></header>
          <div className="wb-pathways">
            {PATHS.map(({ id, title, subtitle, description, icon: Icon }) => (
              <div className="wb-pathway" key={id}>
                <button type="button" data-workbench={id} onClick={() => onOpenWorkbench(id)}>
                  <Icon size={22} weight="duotone" aria-hidden="true" />
                  <span>
                    <span className="wb-pathway-title">
                      <strong>{title}</strong>
                      {advancedWorkbenchesDesktopOnly && <span className="wb-desktop-badge">仅桌面端</span>}
                    </span>
                    <small>{subtitle}</small>
                  </span>
                  <ArrowRight size={17} aria-hidden="true" />
                </button>
                <p>{description}</p>
                {!advancedWorkbenchesDesktopOnly && workbenchController?.onTransfer && (
                  <button className="wb-transfer-entry" type="button" disabled={workbenchController.locked} onClick={() => workbenchController.onTransfer(id)}>
                    带当前草稿进入 <ArrowRight size={14} aria-hidden="true" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>

        <section className="creation-ideas" aria-labelledby="creation-ideas-title">
          <header>
            <h3 id="creation-ideas-title">从灵感开始</h3>
            <p>示例只填入下方草稿，不会直接生成。</p>
          </header>
          <div className="creation-idea-grid">
            {ENTRY_IDEAS.map((idea) => (
              <button
                key={idea.id}
                className="creation-idea-card"
                type="button"
                onClick={() => onUsePrompt?.(idea.prompt)}
                aria-label={`使用“${idea.title}”的制作说明`}
              >
                <span className="creation-idea-media">
                  <img src={idea.image} alt="" loading="lazy" decoding="async" />
                  <small>{idea.category}</small>
                </span>
                <span className="creation-idea-copy">
                  <strong>{idea.title}</strong>
                  <small>{idea.description}</small>
                </span>
              </button>
            ))}
          </div>
        </section>
      </section>

      {recentTakes.length > 0 && (
        <section className="creation-recent" aria-labelledby="creation-recent-title">
          <header>
            <div>
              <h3 id="creation-recent-title">接着上次的镜头</h3>
              <p>从最近任务挑一条，查看详情或带着原设置继续。</p>
            </div>
            {activeTake && (
              <button type="button" onClick={() => onContinueTask?.(activeTake.task)}>
                <PencilSimple size={16} aria-hidden="true" />
                复用当前设置
              </button>
            )}
          </header>
          <div className="creation-recent-track" role="list" aria-label="最近创作任务">
            {recentTakes.map((take) => (
              <article
                key={take.id}
                className={`creation-recent-item ${activeTake?.id === take.id ? "is-active" : ""}`}
                role="listitem"
              >
                <CreationTaskVisual
                  take={take}
                  liveMode={liveMode}
                  compact
                  artifactAccessAvailable={artifactAccessAvailable}
                  previewActionKey={previewActionKey}
                  onRequestPreview={onRequestPreview}
                  onPreviewError={() => onPreviewError?.(take.previewKey)}
                />
                <button type="button" onClick={() => onSelectTake?.(take)} aria-pressed={activeTake?.id === take.id}>
                  <strong>{take.label}</strong>
                  <TaskMetaLine take={take} />
                </button>
                <button className="creation-recent-open" type="button" onClick={() => onOpenTask?.(take.task)}>
                  <ArrowSquareOut size={16} aria-hidden="true" />
                  <span className="visually-hidden">查看{take.label}</span>
                </button>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function orderedWorkbenchEntries(controller) {
  return [...(controller?.entries || [])].sort((left, right) => (left.order || 0) - (right.order || 0));
}

function selectedEntryTake(entry, explicitOnly = false) {
  const takes = entry?.takes || [];
  const selected = takes.find((take) => (
    String(take.taskId || take.task?.id || take.id) === String(entry?.selectedTaskId)
    && (!entry?.selectedArtifactId || String(take.artifactId || take.artifact?.artifact_id || "") === String(entry.selectedArtifactId))
  ));
  return selected || (explicitOnly ? null : takes.at(-1)) || null;
}

function WorkbenchHeading({ kind, onBack, controller, children }) {
  const current = PATHS.find((path) => path.id === kind);
  const Icon = current.icon;
  return (
    <header className="wb-heading">
      <div className="wb-heading-main">
        <button className="wb-back" type="button" aria-label="返回创作入口" onClick={onBack}>
          <ArrowLeft size={17} aria-hidden="true" />
          <span className="wb-back-long">返回创作入口</span>
          <span className="wb-back-short" aria-hidden="true">入口</span>
        </button>
        <h2><Icon size={22} weight="duotone" aria-hidden="true" />{current.title}</h2>
        {children}
      </div>
      <div className="wb-heading-secondary">
        <nav className="wb-switch" aria-label="切换工作方式">
          {PATHS.map((path) => (
            <button
              key={path.id}
              type="button"
              aria-current={path.id === kind ? "page" : undefined}
              onClick={() => controller?.onOpenWorkbench?.(path.id)}
              disabled={!controller?.onOpenWorkbench}
            >{path.title}</button>
          ))}
        </nav>
        <p className="wb-storage-note" role="status">{controller?.storageNotice || "本机保存，不跨设备同步"}</p>
      </div>
    </header>
  );
}

function WorkbenchTitle({ entry, controller, label }) {
  const [title, setTitle] = useState(entry?.title || "");
  useEffect(() => setTitle(entry?.title || ""), [entry?.id, entry?.title]);
  if (!entry) return null;
  return (
    <label className="wb-title-field">
      <span className="visually-hidden">{label}</span>
      <input
        aria-label={label}
        value={title}
        maxLength={120}
        disabled={controller?.locked}
        onChange={(event) => setTitle(event.target.value)}
        onBlur={() => {
          const next = title.trim();
          if (next && next !== entry.title) controller?.onRenameEntry?.(entry.id, next);
          else setTitle(entry.title || "");
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") { event.preventDefault(); setTitle(entry.title || ""); }
        }}
      />
    </label>
  );
}

function WorkbenchPreview({ take, compact = false, props }) {
  return (
    <div className="wb-preview">
      <CreationTaskVisual
        take={take}
        compact={compact}
        liveMode={props.liveMode}
        artifactAccessAvailable={props.artifactAccessAvailable}
        previewActionKey={props.previewActionKey}
        onRequestPreview={props.onRequestPreview}
        onPreviewError={() => props.onPreviewError?.(take?.previewKey)}
      />
    </div>
  );
}

function WorkbenchResultActions({ take, props }) {
  if (!take) return null;
  return (
    <div className="wb-result-actions" aria-label="当前版本操作">
      {props.onOpenTask && <button type="button" onClick={() => props.onOpenTask(take.task)}><ArrowSquareOut size={16} aria-hidden="true" />任务详情</button>}
      {take.status === "succeeded" && take.artifact && (
        <>
          {props.onPromoteArtifact && <button
            type="button"
            disabled={!props.canPromoteArtifact}
            title={!props.canPromoteArtifact ? "当前账号没有转存素材权限" : "转存为素材后，可在创作时选择"}
            onClick={() => props.onPromoteArtifact(take.task, take.artifact)}
          ><FolderPlus size={16} aria-hidden="true" />存为参考</button>}
          {props.onDownloadArtifact && <button
            type="button"
            disabled={!props.canDownloadArtifact}
            title={!props.canDownloadArtifact ? "当前空间暂不支持作品下载" : undefined}
            onClick={() => props.onDownloadArtifact(take.task, take.artifact)}
          ><DownloadSimple size={16} aria-hidden="true" />下载</button>}
          {props.onPublishArtifact && <button
            type="button"
            disabled={!props.canPublishArtifact}
            title={!props.canPublishArtifact ? "当前账号没有发布交接权限" : undefined}
            onClick={() => props.onPublishArtifact(take.task, take.artifact)}
          ><PaperPlaneTilt size={16} aria-hidden="true" />去发布</button>}
        </>
      )}
    </div>
  );
}

function WorkbenchVersions({ entry, controller, label = "本镜头版本" }) {
  const takes = entry?.takes || [];
  if (!takes.length) return null;
  const selectedTake = selectedEntryTake(entry, true);
  return (
    <div className="wb-versions" aria-label={label}>
      {takes.map((take, index) => {
        const taskId = take.taskId || take.task?.id || take.id;
        const artifactId = take.artifactId || take.artifact?.artifact_id || "";
        const canChoose = take.status === "succeeded"
          && Boolean(take.artifact?.asset_id)
          && Boolean(taskId)
          && Boolean(artifactId);
        const selected = canChoose && selectedTake === take;
        return (
          <button
            key={take.id || `${taskId}:${artifactId || index}`}
            type="button"
            className={selected ? "is-selected" : ""}
            disabled={!canChoose}
            title={canChoose ? "选为本镜头使用的版本" : "任务尚未成功归档，暂不能选用"}
            aria-pressed={selected}
            aria-label={`选用第 ${index + 1} 版，${take.statusLabel}`}
            onClick={() => canChoose && controller?.onSelectVersion?.(entry.id, taskId, artifactId)}
          >
            {selected && <CheckCircle size={15} weight="fill" aria-hidden="true" />}
            <span>第 {index + 1} 版</span>
            <small>{take.statusLabel}</small>
          </button>
        );
      })}
    </div>
  );
}

function WorkbenchTransfers({ kind, controller }) {
  if (!controller?.onTransfer) return null;
  return (
    <details className="wb-transfers">
      <summary>在另一工作台继续 <CaretDown size={15} aria-hidden="true" /></summary>
      <div>
        <p>复制为新草稿，不覆盖已有内容。</p>
        {PATHS.filter((path) => path.id !== kind).map((path) => (
          <button type="button" key={path.id} disabled={controller.locked} onClick={() => controller.onTransfer(path.id)}>
            复制到{path.title}<ArrowRight size={15} aria-hidden="true" />
          </button>
        ))}
      </div>
    </details>
  );
}

function WorkbenchHistoryImport({ takes = [], controller, ownedTaskIds = [], expanded = false }) {
  const [query, setQuery] = useState("");
  const imported = new Set([
    ...ownedTaskIds,
    ...(controller?.ownedTaskIds || []),
    ...(controller?.entries || []).flatMap((entry) => (entry.takes || []).map((take) => take.taskId || take.task?.id || take.id)),
  ].map((id) => String(id || "")).filter(Boolean));
  const available = takes.filter((take) => !imported.has(String(take.task?.id || take.id)));
  const filtered = available.filter((take) => `${take.prompt || ""} ${take.label || ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  if (!controller?.onImportTask) return null;
  return (
    <details className="wb-import" open={expanded || undefined}>
      <summary><FolderPlus size={17} aria-hidden="true" /><span>从任务记录加入</span><CaretDown size={15} aria-hidden="true" /></summary>
      <div className="wb-import-panel">
        <p>只加入你选中的任务。未指定来源的记录保持独立。</p>
        {available.length > 5 && <label className="wb-import-search"><MagnifyingGlass size={17} aria-hidden="true" /><span className="visually-hidden">查找可加入的任务</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="查找任务或制作说明" /></label>}
        {filtered.length ? <ul>
          {filtered.map((take) => (
            <li key={take.id}>
              <div><strong>{take.prompt || take.label}</strong><small>{take.statusLabel} {take.dateLabel}</small></div>
              <button type="button" disabled={controller.locked} onClick={() => controller.onImportTask(take.task)} aria-label={`加入工作台：${take.prompt || take.label}`}>加入工作台<Plus size={15} aria-hidden="true" /></button>
            </li>
          ))}
        </ul> : <p>{query ? "没有匹配的任务。" : "当前记录中没有可加入的任务。"}</p>}
      </div>
    </details>
  );
}

function EmptyWorkbench({ title, description, action, onAction, locked }) {
  return (
    <div className="wb-empty">
      <h3>{title}</h3><p>{description}</p>
      <button className="wb-primary" type="button" disabled={locked} onClick={onAction}><Plus size={18} aria-hidden="true" />{action}</button>
    </div>
  );
}

export function DesktopWorkbenchGate({ kind, onBack }) {
  const path = PATHS.find((item) => item.id === kind) || PATHS[0];
  const Icon = path.icon;

  return (
    <div className="wb-surface wb-desktop-gate" data-ui="desktop-workbench-gate" data-workbench={path.id}>
      <header className="wb-desktop-gate-header">
        <button className="wb-back" type="button" onClick={onBack} aria-label="返回创作入口">
          <ArrowLeft size={18} aria-hidden="true" />
          <span>返回</span>
        </button>
        <strong>{path.title}</strong>
        <span aria-hidden="true" />
      </header>
      <main className="wb-desktop-gate-body" aria-labelledby="desktop-workbench-gate-title">
        <span className="wb-desktop-gate-icon" aria-hidden="true"><Icon size={30} weight="duotone" /></span>
        <h2 id="desktop-workbench-gate-title">请在桌面端使用{path.title}</h2>
        <p>这类工作台需要更大的操作空间和精确输入，手机端不会加载编辑器。</p>
        <button className="wb-primary" type="button" onClick={onBack}>使用手机快速生成</button>
        <small>任务进度与作品查看仍可在手机端使用。</small>
      </main>
    </div>
  );
}

export function DirectorConsoleView(props) {
  const { workbenchController: controller, renderEditor, onBack, takes = [], directorHost } = props;
  const entries = orderedWorkbenchEntries(controller);
  const entry = entries.find((item) => item.id === controller?.activeEntryId) || null;
  const take = selectedEntryTake(entry);
  const editing = Boolean(entry && controller?.editing);
  const versions = entry?.takes || [];
  const [captureStatus, setCaptureStatus] = useState("");
  const [persistenceStatus, setPersistenceStatus] = useState({
    state: "loading",
    message: "正在建立本机场景存储…",
  });
  useEffect(() => {
    setCaptureStatus("");
    setPersistenceStatus({ state: "loading", message: "正在建立本机场景存储…" });
  }, [entry?.id]);
  const handleCaptureFiles = useCallback(async (files) => {
    if (!entry?.id || !directorHost?.onCaptureFiles) {
      throw new Error("当前镜头没有可用的素材接入链路。");
    }
    return directorHost.onCaptureFiles(files, {
      scopeKey: controller?.state?.scopeKey || "",
      shotId: entry.id,
    });
  }, [controller?.state?.scopeKey, directorHost, entry?.id]);

  return (
    <div className="wb-surface wb-console wb-console-3d" data-ui="director-console" data-editing={editing}>
      <header className="wb3d-header">
        <button className="wb-back" type="button" onClick={onBack} aria-label="返回创作入口"><ArrowLeft size={18} aria-hidden="true" /><span className="wb-back-long">创作入口</span><span className="wb-back-short">返回</span></button>
        <div className="wb3d-current">
          <FilmSlate size={18} weight="duotone" aria-hidden="true" />
          {entry ? <WorkbenchTitle entry={entry} controller={controller} label="镜头名称" /> : <h2>3D 导演台</h2>}
          <small>场景保存于本机</small>
        </div>
        <div className="wb3d-header-actions">
          {entry && !editing && <button className="wb-primary" type="button" onClick={() => controller?.onEditEntry?.(entry.id)}><PencilSimple size={17} aria-hidden="true" />生成设置</button>}
          {entries.length > 0 && <button className="wb-new" type="button" disabled={controller?.locked} onClick={() => controller?.onNewEntry?.({})}><Plus size={17} aria-hidden="true" /><span>新镜头</span></button>}
        </div>
      </header>
      {entries.length > 0 && <nav className="wb-shot-index" aria-label="选择镜头">
        {entries.map((item, index) => <button key={item.id} type="button" aria-pressed={item.id === entry?.id} onClick={() => controller?.onSelectEntry?.(item.id, { edit: false })}>
          <span>{String(index + 1).padStart(2, "0")}</span>{item.title || `镜头 ${index + 1}`}
        </button>)}
      </nav>}

      {entry ? (
        <main className="wb3d-workspace">
          <section className="wb3d-stage" aria-label="3D 场景编辑器">
            <StoryAiDirectorDesk
              key={entry.id}
              scopeKey={controller?.state?.scopeKey || "unresolved"}
              shotId={entry.id}
              shotTitle={entry.title}
              onCaptureFiles={handleCaptureFiles}
              onCaptureStatus={setCaptureStatus}
              onPersistenceStatus={setPersistenceStatus}
              onClose={onBack}
            />
            <footer className="wb3d-stage-note">
              <span data-persistence-state={persistenceStatus.state}>StoryAI 3D Director Desk · {persistenceStatus.message}</span>
              {captureStatus && <strong role="status">{captureStatus}</strong>}
            </footer>
          </section>

          <section className="wb3d-production" aria-label="当前镜头生成与版本">
            <div className="wb3d-generation">
              {editing ? renderEditor?.({ variant: "console", title: "当前镜头", onClose: controller?.onStopEditing }) : (
                <div className="wb3d-generation-summary">
                  <span>生成说明</span>
                  <p>{entry.draft?.prompt || "写下画面、动作和节奏，再把 3D 取景图作为参考。"}</p>
                  <button className="wb-primary" type="button" onClick={() => controller?.onEditEntry?.(entry.id)}><PencilSimple size={17} aria-hidden="true" />打开生成设置</button>
                </div>
              )}
              <WorkbenchTransfers kind="console" controller={controller} />
            </div>

            <aside className="wb3d-result" aria-label="当前镜头版本">
              <header><span>生成版本</span><strong>{take ? `第 ${versions.indexOf(take) + 1} 版` : "尚未生成"}</strong></header>
              {take ? <>
                <WorkbenchPreview take={take} props={props} />
                <WorkbenchVersions entry={entry} controller={controller} />
                <WorkbenchResultActions take={take} props={props} />
                {take.sources?.length > 0 && <p className="wb-source-facts"><ImageSquare size={17} aria-hidden="true" />本版本使用 {take.sources.map((source) => `${source.label} ${source.count}`).join("、")}</p>}
              </> : <p className="wb3d-result-empty">3D 场景不会自动生成或计费。准备好取景与提示词后，再由你明确提交。</p>}
            </aside>
          </section>
        </main>
      ) : <EmptyWorkbench title="先写一个镜头" description="在场记单里写画面和动作，对着生成结果继续打磨。" action="新建镜头" onAction={() => controller?.onNewEntry?.({})} locked={controller?.locked} />}
      <WorkbenchHistoryImport takes={takes} controller={controller} ownedTaskIds={props.ownedTaskIds} />
    </div>
  );
}

function NotebookScene({ entry, index, total, controller, renderEditor, props }) {
  const selected = entry.id === controller?.activeEntryId;
  const editing = Boolean(selected && controller?.editing);
  const take = selectedEntryTake(entry);
  return (
    <article className={`wb-scene ${selected ? "is-active" : ""} ${editing ? "is-editing" : ""}`} data-scene-id={entry.id} aria-label={`第 ${index + 1} 场`}>
      <header className="wb-scene-heading">
        <span>第 {index + 1} 场</span>
        {selected ? <WorkbenchTitle entry={entry} controller={controller} label="场次名称" /> : <h3>{entry.title || `第 ${index + 1} 场`}</h3>}
        {selected && <div className="wb-scene-order" aria-label="调整场次顺序">
          <button type="button" disabled={controller.locked || index === 0} aria-label="本场上移" onClick={() => controller.onMoveEntry?.(entry.id, "up")}><ArrowUp size={16} aria-hidden="true" /></button>
          <button type="button" disabled={controller.locked || index === total - 1} aria-label="本场下移" onClick={() => controller.onMoveEntry?.(entry.id, "down")}><ArrowDown size={16} aria-hidden="true" /></button>
        </div>}
      </header>
      {editing ? renderEditor?.({ variant: "notebook", title: `第 ${index + 1} 场`, onClose: controller?.onStopEditing }) : (
        <>
          <p className="wb-scene-prose">{entry.draft?.prompt || "这一场还没有场文。"}</p>
          <button className="wb-text-action" type="button" onClick={() => controller?.onEditEntry?.(entry.id)}><PencilSimple size={16} aria-hidden="true" />编辑这一场</button>
        </>
      )}
      {take && <div className="wb-scene-result">
        <WorkbenchPreview take={take} compact={!selected} props={props} />
        <div><span>{entry.takes?.length || 0} 个版本</span>{selected && <WorkbenchVersions entry={entry} controller={controller} label="本场版本" />}{selected && <WorkbenchResultActions take={take} props={props} />}</div>
      </div>}
      {selected && <WorkbenchTransfers kind="notebook" controller={controller} />}
    </article>
  );
}

export function DirectorNotebookView(props) {
  const { workbenchController: controller, renderEditor, takes = [], onBack } = props;
  const entries = orderedWorkbenchEntries(controller);
  const [page, setPage] = useState("notes");
  const editing = Boolean(controller?.editing && entries.some((entry) => entry.id === controller.activeEntryId));
  const chosen = entries.map((entry, index) => ({ entry, index, take: selectedEntryTake(entry, true) })).filter(({ take }) => take?.status === "succeeded" && take.artifact);
  useEffect(() => { if (controller?.editing) setPage("notes"); }, [controller?.editing, controller?.activeEntryId]);

  return (
    <div className="wb-surface wb-notebook" data-ui="director-notebook" data-editing={editing}>
      <WorkbenchHeading kind="notebook" onBack={onBack} controller={controller} />
      <div className="wb-notebook-layout">
        <nav className="wb-scene-index" aria-label="场次目录">
          <strong>这本手记</strong>
          {entries.map((entry, index) => <button key={entry.id} type="button" aria-current={entry.id === controller?.activeEntryId ? "true" : undefined} onClick={() => { setPage("notes"); controller?.onSelectEntry?.(entry.id, { edit: false }); }}>
            <span>{String(index + 1).padStart(2, "0")}</span><span>{entry.title || `第 ${index + 1} 场`}</span>
          </button>)}
        </nav>
        <section className="wb-notebook-paper" aria-label="分场手记">
          <header className="wb-notebook-title">
            <div><h2>这一册，共 {entries.length} 场</h2><p>一场可以重拍多版，选中的镜头按场次排列。</p></div>
            <nav aria-label="手记内容">
              <button type="button" aria-current={page === "notes" ? "page" : undefined} onClick={() => setPage("notes")}>手记</button>
              <button type="button" aria-current={page === "selection" ? "page" : undefined} onClick={() => { controller?.onStopEditing?.(); setPage("selection"); }}>已选镜头</button>
            </nav>
          </header>
          {page === "notes" ? (
            <>
              {entries.length ? entries.map((entry, index) => <NotebookScene key={entry.id} entry={entry} index={index} total={entries.length} controller={controller} renderEditor={renderEditor} props={props} />) : <EmptyWorkbench title="从第一场开始" description="写下场景、人物和镜头。在这一页里改稿与生成。" action="写第一场" onAction={() => controller?.onNewEntry?.({})} locked={controller?.locked} />}
              {entries.length > 0 && <button className="wb-add-scene" type="button" disabled={controller?.locked} onClick={() => controller?.onNewEntry?.({})}><Plus size={18} aria-hidden="true" />写下一场</button>}
            </>
          ) : (
            <div className="wb-selected-shots">
              <p className="wb-selection-note">这里是单独的已选镜头，尚未拼接成片。每条镜头可单独交给发布。</p>
              {chosen.length ? chosen.map(({ entry, index, take }) => (
                <article key={entry.id}><h3>第 {index + 1} 场 · {entry.title}</h3><WorkbenchPreview take={take} props={props} /><WorkbenchResultActions take={take} props={props} /></article>
              )) : <div className="wb-empty-selection"><Queue size={27} aria-hidden="true" /><h3>先为场次选一个版本</h3><p>在手记里选择已完成且已保存的版本后，它会出现在这里。</p><button type="button" onClick={() => setPage("notes")}>返回手记</button></div>}
            </div>
          )}
        </section>
      </div>
      <WorkbenchHistoryImport takes={takes} controller={controller} ownedTaskIds={props.ownedTaskIds} />
    </div>
  );
}

export function LineageCanvasView(props) {
  const { workbenchController: controller, renderEditor, takes = [], ownedTaskIds = [], onBack } = props;
  const renderPreview = useCallback(
    (take, compact = false) => <WorkbenchPreview take={take} compact={compact} props={props} />,
    [props.liveMode, props.artifactAccessAvailable, props.previewActionKey, props.onRequestPreview, props.onPreviewError],
  );
  const renderResultActions = useCallback(
    (take) => <WorkbenchResultActions take={take} props={props} />,
    [props.onOpenTask, props.onPromoteArtifact, props.onDownloadArtifact, props.onPublishArtifact,
      props.canPromoteArtifact, props.canDownloadArtifact, props.canPublishArtifact],
  );
  const renderTransfers = useCallback(
    () => <WorkbenchTransfers kind="canvas" controller={controller} />,
    [controller],
  );
  const renderHistoryImport = useCallback(
    (expanded = false) => (
      <WorkbenchHistoryImport
        takes={takes}
        controller={controller}
        ownedTaskIds={ownedTaskIds}
        expanded={expanded}
      />
    ),
    [controller, ownedTaskIds, takes],
  );

  return (
    <Suspense fallback={<div className="wb-surface lineage-loading" role="status">正在打开谱系图谱…</div>}>
      <LazyLineageGraphWorkspace
        controller={controller}
        renderEditor={renderEditor}
        renderPreview={renderPreview}
        renderResultActions={renderResultActions}
        renderTransfers={renderTransfers}
        renderHistoryImport={renderHistoryImport}
        onBack={onBack}
      />
    </Suspense>
  );
}

export function CreationRecordBrowser({
  open,
  onOpenChange,
  mediaTabs,
  media,
  mediaTabRefs,
  onSelectMedia,
  onMediaKeyDown,
  takes,
  activeTake,
  onSelectTake,
  liveMode,
  loading,
  historyAccessAvailable,
  error,
  errorMessage,
  query,
  onQueryChange,
  supportsSearch,
  supportsDateFilter,
  statusFilter,
  onStatusFilterChange,
  days,
  onDaysChange,
  modelId,
  onModelIdChange,
  models,
  grouping,
  onGroupingChange,
  activeFilterCount,
  onClearFilters,
  onOpenHistory,
  onOpenTask,
  onContinueTask,
  artifactAccessAvailable,
  previewActionKey,
  onRequestPreview,
  onPreviewError,
  total,
  page,
  pageSize,
  onPageChange,
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  return (
    <details
      className="creation-record-browser"
      open={open}
      onToggle={(event) => onOpenChange?.(event.currentTarget.open)}
    >
      <summary>
        <span><SlidersHorizontal size={18} aria-hidden="true" /><strong>任务与筛选</strong></span>
        <span>{historyAccessAvailable ? `${total || takes.length} 条记录` : "记录不可用"}</span>
        {activeFilterCount > 0 && <em>{activeFilterCount}</em>}
        <CaretDown size={16} aria-hidden="true" />
      </summary>
      <div className="creation-record-browser-panel">
        <header className="creation-record-filter-head">
          <div className="creation-media-tabs" role="tablist" aria-label="创作内容类型">
            {mediaTabs.map(({ id, label, icon: Icon, unavailableReason }, index) => (
              <button
                key={id}
                ref={(element) => { mediaTabRefs.current[index] = element; }}
                id={`creation-media-tab-${id}`}
                type="button"
                role="tab"
                aria-selected={media === id}
                aria-controls="creation-record-list"
                tabIndex={media === id ? 0 : -1}
                className={media === id ? "is-active" : ""}
                disabled={Boolean(unavailableReason)}
                aria-disabled={unavailableReason ? "true" : undefined}
                title={unavailableReason}
                onClick={() => onSelectMedia(id)}
                onKeyDown={(event) => onMediaKeyDown(event, index)}
              >
                <Icon size={17} aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
          {supportsSearch && historyAccessAvailable && (
            <label className="creation-record-search">
              <MagnifyingGlass size={18} aria-hidden="true" />
              <span className="visually-hidden">搜索创作记录</span>
              <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索任务、模型或制作说明" />
            </label>
          )}
        </header>

        <div className="creation-record-filters">
          <label><span>状态</span><select value={statusFilter} onChange={(event) => onStatusFilterChange(event.target.value)}>
            <option value="">全部状态</option>
            <option value="succeeded">已完成</option>
            <option value="processing">生成中</option>
            <option value="queued">排队中</option>
            <option value="failed">失败</option>
            <option value="cancelled">已取消</option>
            <option value="timed_out">已超时</option>
            <option value="reconciliation_required">待人工确认</option>
          </select></label>
          {supportsDateFilter && <label><span>时间</span><select value={days} onChange={(event) => onDaysChange(event.target.value)}>
            <option value="7">最近 7 天</option>
            <option value="30">最近 30 天</option>
            <option value="90">最近 90 天</option>
            <option value="all">全部时间</option>
          </select></label>}
          <label><span>模型</span><select value={modelId} onChange={(event) => onModelIdChange(event.target.value)}>
            <option value="">全部模型</option>
            {models.map((model) => <option key={model.id} value={model.id}>{model.name || model.display_name}</option>)}
          </select></label>
          <label><span>排列</span><select value={grouping} onChange={(event) => onGroupingChange(event.target.value)}>
            <option value="none">按时间排列</option>
            <option value="model">按模型</option>
            <option value="status">按状态</option>
          </select></label>
          {activeFilterCount > 0 && <button type="button" onClick={onClearFilters}>清除筛选</button>}
          <button type="button" onClick={onOpenHistory}>打开完整历史</button>
        </div>

        <div id="creation-record-list" className="creation-record-list" aria-live="polite">
          {!historyAccessAvailable ? (
            <div className="creation-record-message" role="note">
              <WarningCircle size={24} weight="fill" />
              <strong>无法查看创作记录</strong>
              <p>当前账号没有查看任务记录的权限，因此这里暂时不会更新任务进度。仍可在当前工作方式中准备草稿。</p>
            </div>
          ) : loading ? (
            <div className="creation-record-skeleton" role="status" aria-label="正在读取创作记录">
              <span className="visually-hidden">正在读取创作记录，请稍候。</span>
              {[0, 1, 2].map((item) => <div key={item} aria-hidden="true"><span /><span /></div>)}
            </div>
          ) : error ? (
            <div className="creation-record-message is-error" role="alert">
              <WarningCircle size={24} weight="fill" />
              <strong>创作记录读取失败</strong>
              <p>{errorMessage}</p>
              <button type="button" onClick={onOpenHistory}>前往历史页面</button>
            </div>
          ) : takes.length ? (
            <div className="creation-record-items" role="list" aria-label="创作任务记录">
              {takes.map((take) => (
                <article key={take.id} className={activeTake?.id === take.id ? "is-active" : ""} role="listitem">
                  <CreationTaskVisual
                    take={take}
                    liveMode={liveMode}
                    compact
                    artifactAccessAvailable={artifactAccessAvailable}
                    previewActionKey={previewActionKey}
                    onRequestPreview={onRequestPreview}
                    onPreviewError={() => onPreviewError?.(take.previewKey)}
                  />
                  <button type="button" onClick={() => onSelectTake(take)} aria-pressed={activeTake?.id === take.id}>
                    <strong>{take.label}</strong><TaskMetaLine take={take} />
                  </button>
                  <div>
                    <button type="button" onClick={() => onOpenTask?.(take.task)}>查看</button>
                    <button type="button" onClick={() => onContinueTask?.(take.task)}>复用</button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="creation-record-message">
              <BookmarkSimple size={24} />
              <strong>{activeFilterCount ? "当前筛选没有任务" : "任务记录会从这里出现"}</strong>
              <p>{activeFilterCount ? "可以清除筛选后继续查找。" : "提交任务后，这里会显示状态和结果。"}</p>
              {activeFilterCount > 0 && <button type="button" onClick={onClearFilters}>清除筛选</button>}
            </div>
          )}
        </div>

        {historyAccessAvailable && total > pageSize && (
          <nav className="creation-pagination" aria-label="创作记录分页">
            <button type="button" disabled={page <= 1 || loading} onClick={() => onPageChange?.(page - 1)}>上一页</button>
            <span aria-live="polite">第 {page} / {pageCount} 页，共 {total} 条</span>
            <button type="button" disabled={page >= pageCount || loading} onClick={() => onPageChange?.(page + 1)}>下一页</button>
          </nav>
        )}
      </div>
    </details>
  );
}
