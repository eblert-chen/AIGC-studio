import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";

import { taskArtifactEvidence } from "../taskArtifacts.js";
import {
  isTaskAttentionRequired,
  resolveTaskStatus,
  taskUserMessage,
} from "../taskStatus.js";
import { fetchTaskHistoryPage } from "./useStudioCollections.js";

export function generationTaskStage(status, task) {
  if (status === "succeeded" && !taskArtifactEvidence(task).complete) {
    return "artifact-evidence-missing";
  }
  return resolveTaskStatus(status).stage;
}

export function generationTaskProgress(status, task) {
  if (status === "succeeded" && !taskArtifactEvidence(task).complete) return 100;
  return resolveTaskStatus(status).progress;
}

export function createGenerationTaskRuntimeState({ demoMode = false, pendingCreate = null } = {}) {
  return {
    stage: demoMode ? "rendering" : "idle",
    progress: demoMode ? 65 : 0,
    currentTaskId: "",
    currentTask: null,
    currentTaskScope: "mine",
    detailTask: null,
    detailTaskScope: "mine",
    submitting: false,
    cancelling: false,
    formError: pendingCreate
      ? "上次提交结果尚未确认，已恢复原设置。再次提交只会确认同一次请求，不会创建重复任务。"
      : "",
    promptError: "",
    playing: false,
    playhead: 2,
  };
}

export function generationTaskRuntimeReducer(state, action) {
  switch (action.type) {
    case "set": {
      const currentValue = state[action.field];
      const nextValue = typeof action.value === "function"
        ? action.value(currentValue)
        : action.value;
      if (Object.is(nextValue, currentValue)) return state;
      return { ...state, [action.field]: nextValue };
    }
    case "patch":
      return { ...state, ...action.value };
    case "activate-task": {
      const task = action.task ?? null;
      const taskId = String(task?.id || task?.task_id || "");
      return {
        ...state,
        currentTask: task,
        currentTaskId: taskId,
        currentTaskScope: action.scope === "company" ? "company" : "mine",
        stage: action.stage ?? generationTaskStage(task?.status, task),
        progress: action.progress ?? generationTaskProgress(task?.status, task),
      };
    }
    case "sync-task": {
      const task = action.task ?? null;
      return {
        ...state,
        currentTask: task,
        currentTaskId: String(task?.id || task?.task_id || state.currentTaskId || ""),
        stage: generationTaskStage(task?.status, task),
        progress: generationTaskProgress(task?.status, task),
      };
    }
    case "clear-active-task":
      return {
        ...state,
        currentTask: null,
        currentTaskId: "",
        currentTaskScope: "mine",
        ...(action.resetProgress ? { stage: "idle", progress: 0 } : {}),
      };
    case "open-detail":
      return {
        ...state,
        detailTask: action.task ?? null,
        detailTaskScope: action.scope === "company" ? "company" : "mine",
      };
    case "clear-detail":
      return { ...state, detailTask: null };
    case "reset-workspace":
      return {
        ...state,
        stage: "idle",
        progress: 0,
        currentTaskId: "",
        currentTask: null,
        currentTaskScope: "mine",
        detailTask: null,
        detailTaskScope: "mine",
        submitting: false,
        cancelling: false,
        promptError: "",
        formError: action.formError || "",
        playing: false,
        playhead: 2,
      };
    default:
      return state;
  }
}

/**
 * Owns the mutable task lifecycle as one reducer-backed controller. Platform
 * admission, quote, capability and idempotency decisions remain in App's one
 * submit path; this controller only applies state transitions after a decision.
 */
export function useGenerationTaskRuntime({ demoMode, initialPendingCreate } = {}) {
  const [state, dispatch] = useReducer(
    generationTaskRuntimeReducer,
    { demoMode, pendingCreate: initialPendingCreate },
    createGenerationTaskRuntimeState,
  );
  const detailRequestRef = useRef({ generation: 0, controller: null });

  const commands = useMemo(() => {
    const setField = (field, value) => dispatch({ type: "set", field, value });
    return {
      setStage: (value) => setField("stage", value),
      setProgress: (value) => setField("progress", value),
      setCurrentTaskId: (value) => setField("currentTaskId", value),
      setCurrentTask: (value) => setField("currentTask", value),
      setCurrentTaskScope: (value) => setField("currentTaskScope", value),
      setDetailTask: (value) => setField("detailTask", value),
      setDetailTaskScope: (value) => setField("detailTaskScope", value),
      setSubmitting: (value) => setField("submitting", value),
      setCancelling: (value) => setField("cancelling", value),
      setFormError: (value) => setField("formError", value),
      setPromptError: (value) => setField("promptError", value),
      setPlaying: (value) => setField("playing", value),
      setPlayhead: (value) => setField("playhead", value),
      patch: (value) => dispatch({ type: "patch", value }),
      activateTask: (task, { scope = "mine", stage, progress } = {}) => dispatch({
        type: "activate-task",
        task,
        scope,
        stage,
        progress,
      }),
      syncTask: (task) => dispatch({ type: "sync-task", task }),
      clearActiveTask: ({ resetProgress = false } = {}) => dispatch({
        type: "clear-active-task",
        resetProgress,
      }),
      openTaskDetail: (task, scope = "mine") => dispatch({
        type: "open-detail",
        task,
        scope,
      }),
      clearTaskDetail: () => dispatch({ type: "clear-detail" }),
      resetWorkspace: ({ formError = "" } = {}) => dispatch({
        type: "reset-workspace",
        formError,
      }),
      queueSubmission: () => dispatch({
        type: "patch",
        value: { stage: "queued", progress: 6 },
      }),
      returnToIdle: () => dispatch({
        type: "patch",
        value: { stage: "idle", progress: 0 },
      }),
      markCancelled: (task) => dispatch({
        type: "patch",
        value: {
          ...(task ? { currentTask: task } : {}),
          stage: "cancelled",
          progress: 0,
        },
      }),
    };
  }, []);

  const cancelTaskDetailRequest = useCallback(() => {
    const request = detailRequestRef.current;
    request.generation += 1;
    request.controller?.abort();
    request.controller = null;
  }, []);

  const closeTaskDetail = useCallback(() => {
    cancelTaskDetailRequest();
    commands.clearTaskDetail();
  }, [cancelTaskDetailRequest, commands]);

  const loadTaskDetail = useCallback(async ({
    task,
    scope = "mine",
    client,
    liveMode,
    isPersonalWorkspace,
    onAuthenticationError,
    onError,
  }) => {
    const taskId = String(task?.id || task?.task_id || "");
    if (!taskId) return null;
    commands.openTaskDetail(task, scope);
    if (!liveMode) return task;

    cancelTaskDetailRequest();
    const controller = new AbortController();
    const request = detailRequestRef.current;
    request.controller = controller;
    const generation = ++request.generation;
    try {
      const detail = await client.getTask(taskId, {
        ...(!isPersonalWorkspace ? { scope } : {}),
        signal: controller.signal,
      });
      if (generation !== detailRequestRef.current.generation) return null;
      commands.setDetailTask(detail);
      return detail;
    } catch (error) {
      if (error?.name === "AbortError" || generation !== detailRequestRef.current.generation) {
        return null;
      }
      onAuthenticationError?.(error);
      onError?.(error);
      return null;
    } finally {
      if (generation === detailRequestRef.current.generation) {
        detailRequestRef.current.controller = null;
      }
    }
  }, [cancelTaskDetailRequest, commands]);

  return useMemo(() => ({
    ...state,
    ...commands,
    cancelTaskDetailRequest,
    closeTaskDetail,
    loadTaskDetail,
  }), [cancelTaskDetailRequest, closeTaskDetail, commands, loadTaskDetail, state]);
}

/**
 * Keeps network and timer effects beside their task state. All cross-domain
 * callbacks are read from a latest-value ref so a toast or route render cannot
 * restart an in-flight poll and accidentally duplicate provider work.
 */
export function useGenerationTaskLifecycle({
  runtime,
  demoMode,
  liveMode,
  authExpired,
  hasStudioSession,
  canReadStudioTasks,
  effectiveSurface,
  isPersonalWorkspace,
  studioClient,
  studioWorkspaceKey,
  creationScopeKey,
  creationScopeRef,
  creationRouteRef,
  taskBelongsToCurrentDraft,
  refreshPersonalWallet,
  taskCompletionNotices,
  previewDuration,
  onAuthenticationError,
  onOpenResult,
  onClearDownloadError,
  onToast,
  formatError,
}) {
  const activeTaskRequestGenerationRef = useRef(0);
  const runtimeRef = useRef(runtime);
  runtimeRef.current = runtime;
  const latest = useRef({});
  latest.current = {
    taskBelongsToCurrentDraft,
    refreshPersonalWallet,
    onAuthenticationError,
    onOpenResult,
    onClearDownloadError,
    onToast,
    formatError,
  };

  useEffect(() => {
    if (
      !liveMode
      || authExpired
      || !hasStudioSession
      || !canReadStudioTasks
      || !["studio", "personal"].includes(effectiveSurface)
    ) return undefined;
    const controller = new AbortController();
    const requestGeneration = ++activeTaskRequestGenerationRef.current;
    const taskRequestScope = creationScopeKey;
    const activeTaskPage = (status) => fetchTaskHistoryPage(
      studioClient,
      {
        page: 1,
        page_size: 1,
        ...(!isPersonalWorkspace ? { scope: "mine" } : {}),
        status,
      },
      { signal: controller.signal },
    );

    Promise.all([
      activeTaskPage("accepted"),
      activeTaskPage("queued"),
      activeTaskPage("processing"),
    ])
      .then((pages) => {
        if (
          requestGeneration !== activeTaskRequestGenerationRef.current
          || creationScopeRef.current !== taskRequestScope
          || controller.signal.aborted
        ) return;
        const activeTask = pages
          .flatMap((pageData) => pageData.items)
          .sort((left, right) => (
            Date.parse(right.created_at || right.updated_at || 0)
            - Date.parse(left.created_at || left.updated_at || 0)
          ))[0];
        if (activeTask) runtime.activateTask(activeTask, { scope: "mine" });
      })
      .catch((error) => {
        if (
          error?.name !== "AbortError"
          && requestGeneration === activeTaskRequestGenerationRef.current
          && creationScopeRef.current === taskRequestScope
          && !controller.signal.aborted
        ) {
          latest.current.onAuthenticationError?.(error);
        }
      });

    return () => {
      activeTaskRequestGenerationRef.current += 1;
      controller.abort();
    };
  }, [
    authExpired,
    canReadStudioTasks,
    creationScopeKey,
    effectiveSurface,
    hasStudioSession,
    isPersonalWorkspace,
    liveMode,
    runtime.activateTask,
    studioClient,
    studioWorkspaceKey,
  ]);

  useEffect(() => {
    if (!demoMode) return undefined;
    if (runtime.stage === "queued") {
      const timer = globalThis.setTimeout(() => {
        runtime.setStage("rendering");
        runtime.setProgress((value) => Math.max(value, 12));
      }, 850);
      return () => globalThis.clearTimeout(timer);
    }

    if (runtime.stage === "rendering") {
      const timer = globalThis.setInterval(() => {
        const value = runtimeRef.current.progress;
        if (value >= 100) {
          globalThis.clearInterval(timer);
          runtime.setStage("complete");
          if (taskCompletionNotices) {
            latest.current.onToast?.("演示完成：未连接生成渠道，也没有生成或保存真实作品");
          }
          return;
        }
        runtime.setProgress(Math.min(100, value + 3));
      }, 900);
      return () => globalThis.clearInterval(timer);
    }
    return undefined;
  }, [
    demoMode,
    runtime.setProgress,
    runtime.setStage,
    runtime.stage,
    taskCompletionNotices,
  ]);

  useEffect(() => {
    if (
      !liveMode
      || authExpired
      || !hasStudioSession
      || !canReadStudioTasks
      || !["studio", "personal"].includes(effectiveSurface)
      || !runtime.currentTaskId
    ) return undefined;
    let stopped = false;
    let timer;
    let activeController;
    const pollScope = creationScopeKey;

    const poll = async () => {
      activeController = new AbortController();
      try {
        const task = await studioClient.getTask(runtime.currentTaskId, {
          signal: activeController.signal,
          ...(!isPersonalWorkspace ? { scope: runtime.currentTaskScope } : {}),
        });
        if (stopped || creationScopeRef.current !== pollScope) return;
        const nextStatus = resolveTaskStatus(task.status);
        const artifactEvidence = taskArtifactEvidence(task);
        runtime.syncTask(task);
        const mayUpdateQuick = creationRouteRef.current.workbench === "entry"
          && ["home", "create"].includes(creationRouteRef.current.activeNav)
          && latest.current.taskBelongsToCurrentDraft?.(task.id);
        if (task.status === "succeeded") {
          if (isPersonalWorkspace) latest.current.refreshPersonalWallet?.();
          latest.current.onClearDownloadError?.();
          if (mayUpdateQuick) latest.current.onOpenResult?.();
          if (!artifactEvidence.complete) {
            if (mayUpdateQuick) runtime.setFormError(artifactEvidence.detail);
            if (taskCompletionNotices) {
              latest.current.onToast?.(`作品保存未确认：${artifactEvidence.detail}`);
            }
            return;
          }
          if (taskCompletionNotices) {
            latest.current.onToast?.(`任务已完成，共 ${artifactEvidence.artifacts.length} 个作品文件可下载`);
          }
          return;
        }
        if (task.status === "failed") {
          if (isPersonalWorkspace) latest.current.refreshPersonalWallet?.();
          const message = taskUserMessage(task.failure_reason, nextStatus.detail);
          if (mayUpdateQuick) runtime.setFormError(message);
          if (taskCompletionNotices) latest.current.onToast?.(message);
          return;
        }
        if (task.status === "cancelled") {
          if (isPersonalWorkspace) latest.current.refreshPersonalWallet?.();
          return;
        }
        if (isTaskAttentionRequired(task.status) || nextStatus.stage === "unknown") {
          if (isPersonalWorkspace && nextStatus.terminal) latest.current.refreshPersonalWallet?.();
          const message = taskUserMessage(task.failure_reason, nextStatus.detail);
          if (mayUpdateQuick) runtime.setFormError(message);
          if (taskCompletionNotices) latest.current.onToast?.(message);
          return;
        }
        if (nextStatus.terminal) return;
        timer = globalThis.setTimeout(poll, 2000);
      } catch (error) {
        if (
          error?.name === "AbortError"
          || stopped
          || creationScopeRef.current !== pollScope
        ) return;
        if (latest.current.onAuthenticationError?.(error)) return;
        if (
          creationRouteRef.current.workbench === "entry"
          && latest.current.taskBelongsToCurrentDraft?.(runtime.currentTaskId)
        ) {
          const detail = latest.current.formatError?.(error) || "未知错误";
          runtime.setFormError(`任务状态同步失败：${detail}`);
        }
        timer = globalThis.setTimeout(poll, 4000);
      }
    };

    poll();
    return () => {
      stopped = true;
      globalThis.clearTimeout(timer);
      activeController?.abort();
    };
  }, [
    authExpired,
    canReadStudioTasks,
    creationScopeKey,
    effectiveSurface,
    hasStudioSession,
    isPersonalWorkspace,
    liveMode,
    runtime.currentTaskId,
    runtime.currentTaskScope,
    runtime.setFormError,
    runtime.syncTask,
    studioClient,
    taskCompletionNotices,
  ]);

  useEffect(() => {
    if (!runtime.playing) return undefined;
    const timer = globalThis.setInterval(() => {
      const value = runtimeRef.current.playhead;
      if (value >= previewDuration) {
        runtime.setPlaying(false);
        runtime.setPlayhead(0);
        return;
      }
      runtime.setPlayhead(Math.min(previewDuration, value + 0.25));
    }, 250);
    return () => globalThis.clearInterval(timer);
  }, [previewDuration, runtime.playing, runtime.setPlaying, runtime.setPlayhead]);
}
