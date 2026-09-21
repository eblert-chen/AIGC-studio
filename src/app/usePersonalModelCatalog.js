import { useCallback, useEffect, useRef, useState } from "react";

const EMPTY_CATALOG_STATE = Object.freeze({
  items: [],
  loading: false,
  error: "",
});

function normalizePersonalModelCatalogEntry(source) {
  if (!source || typeof source !== "object") return null;
  const id = typeof source.id === "string" ? source.id.trim() : "";
  if (!id) return null;
  const unavailableReason = source.unavailable_reason;
  return {
    id,
    slug: typeof source.slug === "string" ? source.slug : "",
    name: source.display_name || source.slug || id,
    billingMode: source.billing_mode === "per_second" ? "per_second" : "per_item",
    capabilityVersion: Number.isInteger(Number(source.capability_version))
      ? Number(source.capability_version)
      : null,
    available: source.available === true,
    unavailableReasonCode:
      typeof unavailableReason?.code === "string" ? unavailableReason.code : "",
    unavailableReason:
      typeof unavailableReason?.message === "string" && unavailableReason.message.trim()
        ? unavailableReason.message.trim()
        : "可用状态正在同步，请刷新后再选择。",
  };
}

export function usePersonalModelCatalog({
  liveMode,
  authExpired,
  hasStudioSession,
  creationScopeKey,
  isPersonalWorkspace,
  effectiveSurface,
  canReadStudioModels,
  studioClient,
  onAuthenticationError,
  formatError,
}) {
  const [state, setState] = useState(EMPTY_CATALOG_STATE);
  const requestEpochRef = useRef(0);
  const callbacksRef = useRef({ onAuthenticationError, formatError });
  callbacksRef.current = { onAuthenticationError, formatError };

  const clear = useCallback(() => {
    requestEpochRef.current += 1;
    setState(EMPTY_CATALOG_STATE);
  }, []);

  useEffect(() => {
    if (
      !liveMode ||
      authExpired ||
      !hasStudioSession ||
      !creationScopeKey ||
      !isPersonalWorkspace ||
      effectiveSurface !== "personal"
    ) {
      clear();
      return undefined;
    }
    if (!canReadStudioModels) {
      requestEpochRef.current += 1;
      setState({
        items: [],
        loading: false,
        error: "当前个人空间没有模型目录读取能力。",
      });
      return undefined;
    }

    const controller = new AbortController();
    const requestEpoch = requestEpochRef.current + 1;
    requestEpochRef.current = requestEpoch;
    setState({ items: [], loading: true, error: "" });
    studioClient
      .listModelCatalog({ signal: controller.signal })
      .then((items) => {
        if (controller.signal.aborted || requestEpochRef.current !== requestEpoch) return;
        const normalized = Array.isArray(items)
          ? items.map(normalizePersonalModelCatalogEntry).filter(Boolean)
          : [];
        setState({ items: normalized, loading: false, error: "" });
      })
      .catch((error) => {
        if (error?.name === "AbortError") return;
        if (controller.signal.aborted || requestEpochRef.current !== requestEpoch) return;
        callbacksRef.current.onAuthenticationError?.(error);
        setState({
          items: [],
          loading: false,
          error: callbacksRef.current.formatError?.(error) || "模型接入进度暂时无法读取。",
        });
      });

    return () => controller.abort();
  }, [
    authExpired,
    canReadStudioModels,
    clear,
    creationScopeKey,
    effectiveSurface,
    hasStudioSession,
    isPersonalWorkspace,
    liveMode,
    studioClient,
  ]);

  return {
    personalModelCatalog: state.items,
    personalModelCatalogLoading: state.loading,
    personalModelCatalogError: state.error,
    clearPersonalModelCatalog: clear,
  };
}
