import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookmarkSimple,
  Cube,
  FilmSlate,
  ImageSquare,
  MusicNotes,
} from "@phosphor-icons/react";
import { taskCostLabel } from "./taskArtifacts.js";
import { resolveTaskStatus, taskUserMessage } from "./taskStatus.js";
import { activePreviewUrl } from "./previewLeases.js";
import {
  CreationEntryView,
  CreationRecordBrowser,
  DesktopWorkbenchGate,
  DirectorConsoleView,
  DirectorNotebookView,
  LineageCanvasView,
} from "./pages/studio/CreationWorkbenchViews.jsx";

const MEDIA_TABS = [
  { id: "video", label: "视频", icon: FilmSlate },
  { id: "image", label: "图片", icon: ImageSquare },
  {
    id: "audio",
    label: "音频",
    icon: MusicNotes,
    unavailableReason: "部分创作方式支持添加参考音频，暂不支持直接生成音频。",
  },
  {
    id: "app",
    label: "快应用",
    icon: Cube,
    unavailableReason: "暂不支持快应用创作。",
  },
  { id: "saved", label: "已完成", icon: BookmarkSimple },
];

const TASK_MODE_LABELS = Object.freeze({
  text_to_video: "文生视频",
  image_to_video: "图生视频",
  video_to_video: "视频转视频",
  text_to_image: "文生图片",
});

const CREATION_WORKBENCHES = new Set(["console", "notebook", "canvas"]);

function taskId(task) {
  return task?.id || task?.task_id || "";
}

function workbenchArtifactId(task, artifact) {
  const explicit = String(artifact?.artifact_id || "").trim();
  if (explicit) return explicit;
  const ownerTaskId = String(taskId(task)).trim();
  const assetId = String(artifact?.asset_id || "").trim();
  return ownerTaskId && assetId ? `${ownerTaskId}:${assetId}` : "";
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

function taskModeLabel(value) {
  const mode = String(value || "").trim();
  if (!mode) return "未记录";
  return TASK_MODE_LABELS[mode] || "其他创作方式";
}

function taskDisplayLabel(task, liveMode) {
  if (!liveMode) {
    const prompt = String(task?.request_payload?.prompt || "演示任务").trim();
    return `演示 · ${prompt.slice(0, 16)}`;
  }
  return `任务 ${compactTaskId(task)}`;
}

function taskSourceSummary(task) {
  const assets = Array.isArray(task?.request_payload?.assets)
    ? task.request_payload.assets
    : [];
  if (!assets.length) return [];
  return ["image", "video", "audio"].flatMap((kind) => {
    const count = assets.filter((asset) => asset?.media_type === kind).length;
    if (!count) return [];
    return [{
      kind,
      label: kind === "image" ? "参考图片" : kind === "video" ? "参考视频" : "参考音频",
      count,
    }];
  });
}

function durationLabel(task) {
  const duration = Number(task?.request_payload?.duration_seconds);
  if (Number.isFinite(duration) && duration > 0) return `${duration} 秒`;
  return taskMedia(task) === "image" ? "静态图像" : "时长未记录";
}


function demoPreviewForTask(index, status) {
  if (status === "succeeded") {
    return [
      "/community/ice-landscape.webp",
      "/media/speaker-water-hero.webp",
      "/media/scene-lifestyle.webp",
    ][index % 3];
  }
  return "/media/scene-indoor.webp";
}

export function CreationHub({
  tasks = [],
  models = [],
  loading = false,
  error = "",
  historyAccessAvailable = true,
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
  onContinueTask,
  onAdjust,
  onPromoteArtifact,
  onDownloadArtifact,
  onPublishArtifact,
  onUsePrompt,
  canPromoteArtifact = false,
  canDownloadArtifact = false,
  canPublishArtifact = false,
  workbench = "entry",
  advancedWorkbenchesDesktopOnly = false,
  onWorkbenchChange,
  workbenchController,
  ownedTaskIds = [],
  workbenchTasks = [],
  renderEditor,
  directorHost,
}) {
  const mediaTabRefs = useRef([]);
  const [media, setMedia] = useState(
    ["video", "image"].includes(generationMediaKind) ? generationMediaKind : "video",
  );
  const [grouping, setGrouping] = useState("none");
  const [recordBrowserOpen, setRecordBrowserOpen] = useState(false);
  const [query, setQuery] = useState("");
  const queryChangeRef = useRef(onQueryChange);
  queryChangeRef.current = onQueryChange;

  useEffect(() => {
    if (!["video", "image"].includes(generationMediaKind)) return;
    setMedia(generationMediaKind);
    onMediaFilterChange?.(generationMediaKind);
  }, [generationMediaKind]);

  useEffect(() => {
    if (!supportsSearch) return undefined;
    const timer = globalThis.setTimeout?.(() => queryChangeRef.current?.(query), 300);
    return () => globalThis.clearTimeout?.(timer);
  }, [query, supportsSearch]);

  const selectMedia = (nextMedia) => {
    if (MEDIA_TABS.find((item) => item.id === nextMedia)?.unavailableReason) return false;
    if (
      ["video", "image"].includes(nextMedia)
      && onGenerationMediaChange?.(nextMedia) === false
    ) return false;
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
    if (event.key === "ArrowRight") nextPosition = (currentPosition + 1) % availableIndexes.length;
    if (event.key === "ArrowLeft") nextPosition = (currentPosition - 1 + availableIndexes.length) % availableIndexes.length;
    if (event.key === "Home") nextPosition = 0;
    if (event.key === "End") nextPosition = availableIndexes.length - 1;
    const nextIndex = nextPosition === null ? null : availableIndexes[nextPosition];
    if (nextIndex === null) return;
    event.preventDefault();
    if (!selectMedia(MEDIA_TABS[nextIndex].id)) return;
    globalThis.requestAnimationFrame?.(() => mediaTabRefs.current[nextIndex]?.focus());
  };

  const visibleTasks = useMemo(() => {
    if (["audio", "app"].includes(media)) return [];
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
  }, [days, grouping, liveMode, media, modelId, query, statusFilter, tasks]);

  const makeTake = (task, index = 0, artifactOverride, exactArtifact = false) => {
    const statusDefinition = resolveTaskStatus(task?.status || "accepted");
    const rawArtifact = artifactOverride === undefined ? task?.output_artifacts?.[0] || null : artifactOverride;
    const canonicalArtifactId = workbenchArtifactId(task, rawArtifact);
    const artifact = rawArtifact && canonicalArtifactId
      ? { ...rawArtifact, artifact_id: canonicalArtifactId }
      : rawArtifact;
    const exactTakeId = artifact
      ? canonicalArtifactId || `${taskId(task)}:${artifact.asset_id || index}`
      : taskId(task);
    const previewKey = artifact ? `${taskId(task)}:${artifact.asset_id}` : "";
    return {
      task,
      id: exactArtifact ? exactTakeId : taskId(task),
      taskId: taskId(task),
      artifactId: canonicalArtifactId,
      artifactIndex: index,
      label: exactArtifact && Number(task?.output_artifacts?.length || 0) > 1
        ? `${taskDisplayLabel(task, liveMode)}，产物 ${index + 1}`
        : taskDisplayLabel(task, liveMode),
      prompt: String(task?.request_payload?.prompt || "未填写创作说明").trim(),
      media: taskMedia(task),
      modeLabel: taskModeLabel(taskMode(task)),
      status: statusDefinition.status,
      statusLabel: statusDefinition.label,
      statusDetail: statusDefinition.detail,
      statusTone: statusDefinition.tone,
      dateLabel: shortDate(task?.created_at),
      costLabel: taskCostLabel(task || {}),
      ratio: task?.request_payload?.aspect_ratio || "比例未记录",
      resolution: task?.request_payload?.resolution || "画质未记录",
      durationLabel: durationLabel(task),
      modelName: task?.model_display_name || task?.model_name || "模型未记录",
      artifact,
      artifactCount: Number(task?.artifact_count ?? task?.output_artifacts?.length ?? 0),
      previewKey,
      previewUrl: previewKey ? activePreviewUrl(previewUrls[previewKey]) : "",
      demoPreview: liveMode ? "" : demoPreviewForTask(index, statusDefinition.status),
      sources: taskSourceSummary(task),
    };
  };
  const takes = useMemo(() => visibleTasks.map(makeTake), [liveMode, previewUrls, visibleTasks]);
  const normalizedOwnedTaskIds = useMemo(
    () => [...new Set([...(ownedTaskIds || []), ...(workbenchController?.ownedTaskIds || [])].map((id) => String(id || "")).filter(Boolean))],
    [ownedTaskIds, workbenchController?.ownedTaskIds],
  );
  const contextualEntries = workbenchController ? workbenchController.entries.map((entry) => ({
    ...entry,
    takes: entry.taskIds.flatMap((id) => {
      const task = workbenchTasks.find((item) => String(taskId(item)) === String(id));
      if (task) {
        const artifacts = Array.isArray(task.output_artifacts) && task.output_artifacts.length
          ? task.output_artifacts
          : [null];
        return artifacts.map((artifact, index) => makeTake(task, index, artifact, true));
      }
      return [{
        id, label: "任务记录", prompt: "", status: "unknown", statusLabel: "正在读取或暂不可访问",
        statusDetail: "任务与结果需由当前账号重新读取。", statusTone: "muted", artifact: null,
        task: { id }, taskId: id, artifactId: "", sources: [], media: "video", previewUrl: "", demoPreview: "",
      }];
    }),
  })) : [];
  const exactResolvedTake = (entryId, requestedTaskId, requestedArtifactId) => contextualEntries
    .find((entry) => entry.id === entryId)?.takes
    .find((take) => (
      String(take.taskId || take.task?.id || "") === String(requestedTaskId || "")
      && String(take.artifactId || take.artifact?.artifact_id || "") === String(requestedArtifactId || "")
    ));
  const usableResolvedTake = (entryId, requestedTaskId, requestedArtifactId) => {
    const take = exactResolvedTake(entryId, requestedTaskId, requestedArtifactId);
    return take?.status === "succeeded" && take.artifact ? take : null;
  };
  const contextualController = workbenchController ? {
    ...workbenchController,
    ownedTaskIds: normalizedOwnedTaskIds,
    entries: contextualEntries,
    onSelectVersion: (entryId, requestedTaskId, requestedArtifactId) => {
      const take = usableResolvedTake(entryId, requestedTaskId, requestedArtifactId);
      return take
        ? workbenchController.onSelectVersion?.(entryId, take.taskId, take.artifactId)
        : false;
    },
    onSetCanonical: (entryId, requestedTaskId, requestedArtifactId) => {
      const take = usableResolvedTake(entryId, requestedTaskId, requestedArtifactId);
      return take
        ? workbenchController.onSetCanonical?.(entryId, take.taskId, take.artifactId)
        : false;
    },
    onBranchEntry: (entryId, options = {}) => {
      const source = options.sourceTake;
      const take = usableResolvedTake(
        entryId,
        source?.taskId || source?.task?.id,
        source?.artifactId || source?.artifact?.artifact_id,
      );
      return take
        ? workbenchController.onBranchEntry?.(entryId, { ...options, sourceTake: take })
        : null;
    },
  } : undefined;

  const [activeTaskId, setActiveTaskId] = useState("");
  const defaultTake = takes.find((take) => take.status === "succeeded") || takes[0] || null;
  useEffect(() => {
    if (!takes.length) {
      setActiveTaskId("");
      return;
    }
    if (!takes.some((take) => take.id === activeTaskId)) setActiveTaskId(defaultTake.id);
  }, [activeTaskId, defaultTake, takes]);
  const activeTake = takes.find((take) => take.id === activeTaskId) || defaultTake;
  const continueTask = workbench !== "entry" && contextualController
    ? contextualController.onImportTask
    : onAdjust || onContinueTask;

  const activeFilterCount = [
    query.trim(),
    statusFilter,
    supportsDateFilter && days !== "30" ? days : "",
    modelId,
    grouping !== "none" ? grouping : "",
  ].filter(Boolean).length;
  const visibleRecordError = taskUserMessage(
    error,
    "暂时无法读取创作记录。你可以前往历史页面查看，或稍后再试。",
  );

  const clearTaskFilters = () => {
    setQuery("");
    onStatusFilterChange?.("");
    if (supportsDateFilter) onDaysChange?.("30");
    onModelIdChange?.("");
    setGrouping("none");
  };

  const openWorkbench = (nextWorkbench) => {
    if (!CREATION_WORKBENCHES.has(nextWorkbench)) return;
    onWorkbenchChange?.(nextWorkbench);
  };

  const returnToEntry = () => {
    onWorkbenchChange?.("entry");
  };
  const desktopGateActive = advancedWorkbenchesDesktopOnly && CREATION_WORKBENCHES.has(workbench);

  const sharedWorkbenchProps = {
    takes,
    activeTake,
    liveMode,
    onStartCreation,
    onContinueTask: continueTask,
    onOpenTask,
    artifactAccessAvailable,
    previewActionKey,
    onRequestPreview,
    onPreviewError,
    onUsePrompt,
    workbenchController: contextualController,
    renderEditor,
    onPromoteArtifact,
    onDownloadArtifact,
    onPublishArtifact,
    canPromoteArtifact,
    canDownloadArtifact,
    canPublishArtifact,
    directorHost,
    ownedTaskIds: normalizedOwnedTaskIds,
  };

  return (
    <section
      className={`creation-hub creation-route is-${workbench}`}
      aria-labelledby="creation-hub-title"
      aria-busy={loading}
      data-ui="creation-route"
      data-creation-workbench={workbench}
    >
      <h1 id="creation-hub-title" className="visually-hidden">创作</h1>

      {desktopGateActive ? (
        <DesktopWorkbenchGate kind={workbench} onBack={returnToEntry} />
      ) : workbench === "console" ? (
        <DirectorConsoleView
          {...sharedWorkbenchProps}
          onBack={returnToEntry}
          onOpenNotebook={() => openWorkbench("notebook", { replace: true })}
          onSelectTake={(take) => setActiveTaskId(take.id)}
          onPromoteArtifact={onPromoteArtifact}
          onDownloadArtifact={onDownloadArtifact}
          onPublishArtifact={onPublishArtifact}
          canPromoteArtifact={canPromoteArtifact}
          canDownloadArtifact={canDownloadArtifact}
          canPublishArtifact={canPublishArtifact}
        />
      ) : workbench === "notebook" ? (
        <DirectorNotebookView
          {...sharedWorkbenchProps}
          onBack={returnToEntry}
          onPublishArtifact={onPublishArtifact}
          canPublishArtifact={canPublishArtifact}
        />
      ) : workbench === "canvas" ? (
        <LineageCanvasView
          {...sharedWorkbenchProps}
          onBack={returnToEntry}
          onPublishArtifact={onPublishArtifact}
          canPublishArtifact={canPublishArtifact}
        />
      ) : (
        <CreationEntryView
          {...sharedWorkbenchProps}
          advancedWorkbenchesDesktopOnly={advancedWorkbenchesDesktopOnly}
          onOpenWorkbench={openWorkbench}
          onSelectTake={(take) => setActiveTaskId(take.id)}
        />
      )}

      {workbench === "entry" && (
        <CreationRecordBrowser
          open={recordBrowserOpen}
          onOpenChange={setRecordBrowserOpen}
          mediaTabs={MEDIA_TABS}
          media={media}
          mediaTabRefs={mediaTabRefs}
          onSelectMedia={selectMedia}
          onMediaKeyDown={handleMediaTabKeyDown}
          takes={takes}
          activeTake={activeTake}
          onSelectTake={(take) => setActiveTaskId(take.id)}
          liveMode={liveMode}
          loading={loading}
          historyAccessAvailable={historyAccessAvailable}
          error={error}
          errorMessage={visibleRecordError}
          query={query}
          onQueryChange={setQuery}
          supportsSearch={supportsSearch}
          supportsDateFilter={supportsDateFilter}
          statusFilter={statusFilter}
          onStatusFilterChange={(value) => onStatusFilterChange?.(value)}
          days={days}
          onDaysChange={(value) => onDaysChange?.(value)}
          modelId={modelId}
          onModelIdChange={(value) => onModelIdChange?.(value)}
          models={models}
          grouping={grouping}
          onGroupingChange={setGrouping}
          activeFilterCount={activeFilterCount}
          onClearFilters={clearTaskFilters}
          onOpenHistory={onOpenHistory}
          onOpenTask={onOpenTask}
          onContinueTask={continueTask}
          artifactAccessAvailable={artifactAccessAvailable}
          previewActionKey={previewActionKey}
          onRequestPreview={onRequestPreview}
          onPreviewError={onPreviewError}
          total={total || takes.length}
          page={page}
          pageSize={pageSize}
          onPageChange={onPageChange}
        />
      )}
    </section>
  );
}
