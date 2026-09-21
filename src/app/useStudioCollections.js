import { useCallback, useEffect, useRef, useState } from "react";

import { PlatformApiError } from "../api/platformClient.js";
import {
  deriveArtworksFromTasks,
  normalizePage,
} from "../taskArtifacts.js";

function missingCollectionEndpoint(error) {
  return error instanceof PlatformApiError && [404, 405].includes(error.status);
}

export async function fetchTaskHistoryPage(client, filters, { signal } = {}) {
  try {
    const response = await client.listTaskHistory(filters, { signal });
    return normalizePage(response, {
      page: filters.page,
      pageSize: filters.page_size,
    });
  } catch (error) {
    if (!missingCollectionEndpoint(error)) throw error;
    const tasks = await client.listTasks({ signal });
    const filtered = (Array.isArray(tasks) ? tasks : []).filter(
      (task) => !filters.status || task.status === filters.status,
    );
    return normalizePage(filtered, {
      page: 1,
      pageSize: filters.page_size,
    });
  }
}

export async function fetchArtworkPage(client, filters, { signal } = {}) {
  try {
    const response = await client.listArtworks(filters, { signal });
    return normalizePage(response, {
      page: filters.page,
      pageSize: filters.page_size,
    });
  } catch (error) {
    if (!missingCollectionEndpoint(error)) throw error;
    const tasks = await client.listTasks({ signal });
    const derived = deriveArtworksFromTasks(Array.isArray(tasks) ? tasks : []);
    const filtered = derived.filter((artwork) => {
      if (filters.media_type && artwork.media_type !== filters.media_type) return false;
      if (filters.downloaded !== undefined && filters.downloaded !== "") return false;
      return true;
    });
    const page = Number(filters.page) || 1;
    const pageSize = Number(filters.page_size) || 24;
    const start = (page - 1) * pageSize;
    return {
      page,
      page_size: pageSize,
      total: filtered.length,
      items: filtered.slice(start, start + pageSize),
      legacy: true,
    };
  }
}

export function useStudioTaskCollections({
  activeNav,
  liveMode,
  authExpired,
  hasStudioSession,
  canReadStudioTasks,
  effectiveSurface,
  isPersonalWorkspace,
  studioClient,
  studioWorkspaceKey,
  collectionPageSize,
  onAuthenticationError,
  formatError,
}) {
  const [historyTasks, setHistoryTasks] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(liveMode);
  const [historyError, setHistoryError] = useState("");
  const [historyScope, setHistoryScope] = useState("mine");
  const [historyStatus, setHistoryStatus] = useState("");
  const [historyPage, setHistoryPage] = useState(1);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [creationPage, setCreationPage] = useState(1);
  const [creationTotal, setCreationTotal] = useState(0);
  const [creationStatus, setCreationStatus] = useState("");
  const [creationDays, setCreationDays] = useState("30");
  const [creationModelId, setCreationModelId] = useState("");
  const [creationMediaType, setCreationMediaType] = useState("video");
  const [creationQuery, setCreationQuery] = useState("");
  const [resetRevision, setResetRevision] = useState(0);
  const requestGenerationRef = useRef(0);
  const authenticationErrorRef = useRef(onAuthenticationError);
  const formatErrorRef = useRef(formatError);
  authenticationErrorRef.current = onAuthenticationError;
  formatErrorRef.current = formatError;

  const resetTaskCollections = useCallback(({ resetFilters = true } = {}) => {
    requestGenerationRef.current += 1;
    // A parent workspace effect can reset after this hook has started fetching.
    setResetRevision((current) => current + 1);
    setHistoryTasks([]);
    setHistoryLoading(false);
    setHistoryError("");
    setHistoryTotal(0);
    setCreationTotal(0);
    if (!resetFilters) return;
    setHistoryScope("mine");
    setHistoryStatus("");
    setHistoryPage(1);
    setCreationPage(1);
    setCreationStatus("");
    setCreationDays("30");
    setCreationModelId("");
    setCreationMediaType("video");
    setCreationQuery("");
  }, []);

  useEffect(() => {
    if (!liveMode || authExpired || !hasStudioSession) return;
    if (!canReadStudioTasks) resetTaskCollections({ resetFilters: false });
  }, [authExpired, canReadStudioTasks, hasStudioSession, liveMode, resetTaskCollections, studioWorkspaceKey]);

  useEffect(() => {
    if (
      !liveMode ||
      authExpired ||
      !hasStudioSession ||
      !canReadStudioTasks ||
      !["studio", "personal"].includes(effectiveSurface) ||
      !["create", "history"].includes(activeNav)
    ) return undefined;
    let stopped = false;
    let timer;
    let controller;
    const requestGeneration = ++requestGenerationRef.current;
    setHistoryLoading(true);

    const refreshHistory = async () => {
      controller = new AbortController();
      try {
        const pageData = await fetchTaskHistoryPage(
          studioClient,
          {
            page: activeNav === "create" ? creationPage : historyPage,
            page_size: collectionPageSize,
            ...(!isPersonalWorkspace
              ? { scope: activeNav === "create" ? "mine" : historyScope }
              : {}),
            status: activeNav === "create" ? creationStatus : historyStatus,
            model_id: activeNav === "create" ? creationModelId : "",
            media_type: activeNav === "create" ? creationMediaType : "",
            query: !isPersonalWorkspace && activeNav === "create" ? creationQuery.trim() : "",
            start_time: !isPersonalWorkspace && activeNav === "create" && creationDays !== "all"
              ? new Date(Date.now() - Number(creationDays) * 86_400_000).toISOString()
              : "",
          },
          { signal: controller.signal },
        );
        if (stopped || requestGeneration !== requestGenerationRef.current) return;
        setHistoryTasks(pageData.items);
        if (activeNav === "create") setCreationTotal(pageData.total);
        else setHistoryTotal(pageData.total);
        setHistoryError("");
      } catch (error) {
        if (
          stopped ||
          requestGeneration !== requestGenerationRef.current ||
          error?.name === "AbortError"
        ) return;
        if (authenticationErrorRef.current?.(error)) {
          stopped = true;
          return;
        }
        setHistoryError(formatErrorRef.current(error));
      } finally {
        if (!stopped && requestGeneration === requestGenerationRef.current) {
          setHistoryLoading(false);
          timer = globalThis.setTimeout?.(refreshHistory, 10_000);
        }
      }
    };

    refreshHistory();
    return () => {
      stopped = true;
      requestGenerationRef.current += 1;
      globalThis.clearTimeout?.(timer);
      controller?.abort();
    };
  }, [
    activeNav,
    authExpired,
    canReadStudioTasks,
    collectionPageSize,
    creationDays,
    creationMediaType,
    creationModelId,
    creationPage,
    creationQuery,
    creationStatus,
    effectiveSurface,
    hasStudioSession,
    historyPage,
    historyScope,
    historyStatus,
    isPersonalWorkspace,
    liveMode,
    resetRevision,
    studioClient,
    studioWorkspaceKey,
  ]);

  return {
    historyTasks,
    setHistoryTasks,
    historyLoading,
    historyError,
    historyScope,
    setHistoryScope,
    historyStatus,
    setHistoryStatus,
    historyPage,
    setHistoryPage,
    historyTotal,
    setHistoryTotal,
    creationPage,
    setCreationPage,
    creationTotal,
    setCreationTotal,
    creationStatus,
    setCreationStatus,
    creationDays,
    setCreationDays,
    creationModelId,
    setCreationModelId,
    creationMediaType,
    setCreationMediaType,
    creationQuery,
    setCreationQuery,
    resetTaskCollections,
  };
}

export function useStudioArtworkCollection({
  activeNav,
  liveMode,
  authExpired,
  hasStudioSession,
  canReadStudioArtworks,
  effectiveSurface,
  isPersonalWorkspace,
  studioClient,
  studioWorkspaceKey,
  collectionPageSize,
  onAuthenticationError,
  formatError,
}) {
  const [artworks, setArtworks] = useState([]);
  const [artworksLoading, setArtworksLoading] = useState(liveMode);
  const [artworksError, setArtworksError] = useState("");
  const [artworkScope, setArtworkScope] = useState("mine");
  const [artworkMediaFilter, setArtworkMediaFilter] = useState("");
  const [artworkDownloadFilter, setArtworkDownloadFilter] = useState("");
  const [artworkPage, setArtworkPage] = useState(1);
  const [artworkTotal, setArtworkTotal] = useState(0);
  const [resetRevision, setResetRevision] = useState(0);
  const requestGenerationRef = useRef(0);
  const authenticationErrorRef = useRef(onAuthenticationError);
  const formatErrorRef = useRef(formatError);
  authenticationErrorRef.current = onAuthenticationError;
  formatErrorRef.current = formatError;

  const resetArtworkCollection = useCallback(({ resetFilters = true } = {}) => {
    requestGenerationRef.current += 1;
    // Restart the guarded effect even when every filter retains its default value.
    setResetRevision((current) => current + 1);
    setArtworks([]);
    setArtworksLoading(false);
    setArtworksError("");
    setArtworkTotal(0);
    if (!resetFilters) return;
    setArtworkScope("mine");
    setArtworkMediaFilter("");
    setArtworkDownloadFilter("");
    setArtworkPage(1);
  }, []);

  useEffect(() => {
    if (!liveMode || authExpired || !hasStudioSession) return;
    if (!canReadStudioArtworks) {
      resetArtworkCollection({ resetFilters: false });
      setArtworksError(isPersonalWorkspace
        ? "当前个人空间没有作品读取能力。"
        : "当前账号不能查看任务与作品。请联系企业负责人调整访问权限。");
    }
  }, [
    authExpired,
    canReadStudioArtworks,
    hasStudioSession,
    isPersonalWorkspace,
    liveMode,
    resetArtworkCollection,
    studioWorkspaceKey,
  ]);

  useEffect(() => {
    if (
      !liveMode ||
      authExpired ||
      !hasStudioSession ||
      !canReadStudioArtworks ||
      !["studio", "personal"].includes(effectiveSurface) ||
      activeNav !== "artworks"
    ) return undefined;
    let stopped = false;
    let timer;
    let controller;
    const requestGeneration = ++requestGenerationRef.current;
    setArtworksLoading(true);

    const refreshArtworks = async () => {
      controller = new AbortController();
      try {
        const pageData = await fetchArtworkPage(
          studioClient,
          {
            page: artworkPage,
            page_size: collectionPageSize,
            ...(!isPersonalWorkspace ? { scope: artworkScope } : {}),
            media_type: artworkMediaFilter,
            downloaded: !isPersonalWorkspace ? artworkDownloadFilter : "",
          },
          { signal: controller.signal },
        );
        if (stopped || requestGeneration !== requestGenerationRef.current) return;
        setArtworks(pageData.items);
        setArtworkTotal(pageData.total);
        setArtworksError("");
      } catch (error) {
        if (
          stopped ||
          requestGeneration !== requestGenerationRef.current ||
          error?.name === "AbortError"
        ) return;
        if (authenticationErrorRef.current?.(error)) {
          stopped = true;
          return;
        }
        setArtworksError(formatErrorRef.current(error));
      } finally {
        if (!stopped && requestGeneration === requestGenerationRef.current) {
          setArtworksLoading(false);
          timer = globalThis.setTimeout?.(refreshArtworks, 15_000);
        }
      }
    };

    refreshArtworks();
    return () => {
      stopped = true;
      requestGenerationRef.current += 1;
      globalThis.clearTimeout?.(timer);
      controller?.abort();
    };
  }, [
    activeNav,
    artworkDownloadFilter,
    artworkMediaFilter,
    artworkPage,
    artworkScope,
    authExpired,
    canReadStudioArtworks,
    collectionPageSize,
    effectiveSurface,
    hasStudioSession,
    isPersonalWorkspace,
    liveMode,
    resetRevision,
    studioClient,
    studioWorkspaceKey,
  ]);

  return {
    artworks,
    setArtworks,
    artworksLoading,
    artworksError,
    artworkScope,
    setArtworkScope,
    artworkMediaFilter,
    setArtworkMediaFilter,
    artworkDownloadFilter,
    setArtworkDownloadFilter,
    artworkPage,
    setArtworkPage,
    artworkTotal,
    setArtworkTotal,
    resetArtworkCollection,
  };
}
