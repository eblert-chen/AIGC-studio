import { useEffect, useMemo, useRef, useState } from "react";
import {
  CaretDown,
  CaretLeft,
  CaretRight,
  Lightning,
  SlidersHorizontal,
  UserCircle,
  WarningCircle,
} from "@phosphor-icons/react";
import { DemoAccountSwitcher } from "../DemoAccountSwitcher.jsx";
import { normalizeSkin, SkinSwitcher } from "../SkinSwitcher.jsx";
import { BrandLogo, BRAND_NAME } from "../BrandLogo.jsx";
import {
  buildEntitlementKey,
  downloadTextFile,
  mergeOperationsData,
  resolveEntitlementState,
  summarizeBatchImpact,
  toAuditCsv,
} from "./adminConsoleUtils.js";
import {
  relayNativeConsoleBlockReason,
  relayNativeConsoleErrorMessage,
} from "./relayNativeConsole.js";

import {
  cloneData,
  createRelayChannelOperationId,
  cx,
  DatasetState,
  EmptyState,
  NAV_ITEMS,
  PageStatus,
  PageTitle,
  RangeControls,
  SOURCE_KEYS,
  TIME_SCOPED_SECTIONS,
} from "./operations/operationsShared.jsx";
import { TaskOperationsScreen } from "./operations/TaskOperationsViews.jsx";
import {
  CompanyHealthScreen,
  EntitlementMatrixScreen,
  ModelProfitabilityScreen,
  OperatingCockpitScreen,
} from "./operations/BusinessEntitlementViews.jsx";
import {
  AuditAccessScreen,
  ChannelOperationsScreen,
  PublishingAssetsScreen,
} from "./operations/ChannelAuditViews.jsx";
import {
  AdminAccessDrawer,
  AuditDrawer,
  BatchPreviewDrawer,
  EntitlementDrawer,
  ExceptionCenterDrawer,
  ExceptionDrawer,
  RelayCallbackDeadLetterDrawer,
  RelayChannelDrawer,
  RelayUnknownDrawer,
} from "./operations/OperationsDrawers.jsx";

function readAuditLocation() {
  if (!globalThis.location) return {};
  const search = new URLSearchParams(globalThis.location.search || "");
  const tab = search.get("ops_audit_tab");
  const result = search.get("ops_audit_result");
  return {
    tab: ["audit", "access"].includes(tab) ? tab : "audit",
    query: search.get("ops_audit_query") || "",
    result: ["all", "success", "failed"].includes(result) ? result : "all",
  };
}

function writeAuditLocation({ tab, query, result }) {
  if (!globalThis.history || !globalThis.location) return;
  const url = new URL(globalThis.location.href);
  if (tab && tab !== "audit") url.searchParams.set("ops_audit_tab", tab);
  else url.searchParams.delete("ops_audit_tab");
  if (query) url.searchParams.set("ops_audit_query", query);
  else url.searchParams.delete("ops_audit_query");
  if (result && result !== "all") url.searchParams.set("ops_audit_result", result);
  else url.searchParams.delete("ops_audit_result");
  globalThis.history.replaceState(
    globalThis.history.state,
    "",
    `${url.pathname}${url.search}${url.hash}`,
  );
}

export function AdminOperationsConsole({
  data: inputData,
  demoMode: requestedDemoMode = false,
  loading = false,
  accessPending = false,
  error = "",
  activeSection: controlledSection,
  initialSection = "task-operations",
  range: controlledRange,
  initialRange = "24h",
  administrator = { name: "平台管理员", roleLabel: "平台管理员" },
  onSectionChange,
  onRangeChange,
  onEnvironmentChange,
  onRefresh,
  onEnterExceptionCenter,
  onExceptionAction,
  canResolveException,
  onReliabilityAction,
  onEntitlementSave,
  onBatchEntitlementCommit,
  onAuditExport,
  onAuditRollback,
  onAdminAccessSave,
  onCompanyOpen,
  onModelOpen,
  onRelayChannelDetail,
  onRelayChannelOperation,
  onRelayChannelOperationRead,
  onRelayNativeConsoleAuthorize,
  relayNativeConsoleDisabledReason = "",
  relayNativeConsoleAccessScope = "",
  onRelayUnknownDetail,
  onRelayUnknownRefresh,
  onRelayUnknownResolve,
  onRelayCallbackDeadLetterDetail,
  onRelayCallbackDeadLetterRedrive,
  demoPersonaId = "platform_admin",
  onDemoPersonaChange,
  onLogout,
  skin = "paper",
  onSkinChange,
  onOpenBasicConfig,
  visibleSections,
  environmentOptions,
  canManageEntitlements,
  canManageAdminAccess,
  canReadAudit,
  canReadAdminAccess,
  canManageRelayUnknown = false,
  canManageRelayCallbackDeadLetters = false,
  canManageRelayChannels = false,
  canAuthorizeRelayNativeConsole = false,
  isPlatformOwner = false,
  showcaseContent = null,
  className = "",
}) {
  const demoMode = !import.meta.env.PROD && requestedDemoMode;
  const activeSkin = normalizeSkin(skin);
  const [initialAuditLocation] = useState(readAuditLocation);
  const [demoData, setDemoData] = useState(() => demoMode ? cloneData(inputData) : null);
  const [internalSection, setInternalSection] = useState(initialSection);
  const [internalRange, setInternalRange] = useState(initialRange);
  const [environment, setEnvironment] = useState(inputData?.summary?.environment || (demoMode ? "production" : "production"));
  const [selectedException, setSelectedException] = useState(null);
  const [exceptionCenterOpen, setExceptionCenterOpen] = useState(false);
  const [selectedRelayUnknown, setSelectedRelayUnknown] = useState(null);
  const [relayUnknownDetailVersion, setRelayUnknownDetailVersion] = useState(0);
  const [relayUnknownError, setRelayUnknownError] = useState("");
  const [relayUnknownRequiresRefresh, setRelayUnknownRequiresRefresh] = useState(false);
  const [relayUnknownPendingForm, setRelayUnknownPendingForm] = useState(null);
  const [demoResolvedRelayUnknownIds, setDemoResolvedRelayUnknownIds] = useState(new Set());
  const [selectedRelayCallbackDeadLetter, setSelectedRelayCallbackDeadLetter] = useState(null);
  const [relayCallbackDeadLetterError, setRelayCallbackDeadLetterError] = useState("");
  const [relayCallbackDeadLetterRequiresReadback, setRelayCallbackDeadLetterRequiresReadback] = useState(false);
  const [selectedRelayChannel, setSelectedRelayChannel] = useState(null);
  const [relayChannelIntent, setRelayChannelIntent] = useState("detail");
  const [relayChannelTargetStatus, setRelayChannelTargetStatus] = useState("");
  const [relayChannelOperationId, setRelayChannelOperationId] = useState("");
  const [relayChannelPendingValues, setRelayChannelPendingValues] = useState(null);
  const [relayChannelReceipt, setRelayChannelReceipt] = useState(null);
  const [relayChannelError, setRelayChannelError] = useState("");
  const [relayChannelRequiresReadback, setRelayChannelRequiresReadback] = useState(false);
  const [demoRelayChannelOverrides, setDemoRelayChannelOverrides] = useState({});
  const [relayNativeConsoleGrant, setRelayNativeConsoleGrant] = useState(null);
  const [relayNativeConsoleBlock, setRelayNativeConsoleBlock] = useState("");
  const [entitlementCell, setEntitlementCell] = useState(null);
  const [selectedCompanyIds, setSelectedCompanyIds] = useState(new Set());
  const [selectedProductIds, setSelectedProductIds] = useState(new Set());
  const [batchMode, setBatchMode] = useState("enable");
  const [copySourceId, setCopySourceId] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [batchPreview, setBatchPreview] = useState(null);
  const [auditTab, setAuditTab] = useState(initialAuditLocation.tab);
  const [auditQuery, setAuditQuery] = useState(initialAuditLocation.query);
  const [auditResult, setAuditResult] = useState(initialAuditLocation.result);
  const [selectedAudit, setSelectedAudit] = useState(null);
  const [selectedAdmin, setSelectedAdmin] = useState(null);
  const [busyAction, setBusyAction] = useState("");
  const [localError, setLocalError] = useState("");
  const [toast, setToast] = useState("");
  const [accountOpen, setAccountOpen] = useState(false);
  const operationsNavRef = useRef(null);
  const [navOverflow, setNavOverflow] = useState({ before: false, after: false });

  useEffect(() => {
    const nextEnvironment = inputData?.summary?.environment
      || (environmentOptions?.length === 1 ? environmentOptions[0] : "");
    if (nextEnvironment) setEnvironment(nextEnvironment);
  }, [environmentOptions, inputData?.summary?.environment]);

  useEffect(() => {
    if (demoMode && inputData) setDemoData(cloneData(inputData));
  }, [demoMode, inputData]);

  const data = useMemo(() => {
    if (!demoMode) return mergeOperationsData(inputData);
    let merged = mergeOperationsData({
      ...(demoData || {}),
      ...(inputData || {}),
      business: { ...(demoData?.business || {}), ...(inputData?.business || {}) },
    });
    if (Object.keys(demoRelayChannelOverrides).length) {
      merged = {
        ...merged,
        relayChannels: merged.relayChannels.map((item) => (
          demoRelayChannelOverrides[item.id]
            ? { ...item, ...demoRelayChannelOverrides[item.id] }
            : item
        )),
      };
    }
    if (demoResolvedRelayUnknownIds.size) {
      merged = {
        ...merged,
        relayUnknownSubmissions: merged.relayUnknownSubmissions
          .filter((item) => !demoResolvedRelayUnknownIds.has(item.jobId)),
        relayUnknownSubmissionTotal: Math.max(
          0,
          Number(merged.relayUnknownSubmissionTotal || 0)
            - demoResolvedRelayUnknownIds.size,
        ),
      };
    }
    return {
      ...merged,
      sourceStatus: Object.fromEntries(SOURCE_KEYS.map((key) => [key, "available"])),
      sourceErrors: {},
      relayChannelSourceStatus: "available",
      relayUnknownSubmissionSourceStatus: "available",
      relayCallbackDeadLetterSourceStatus: "available",
    };
  }, [demoData, demoMode, demoRelayChannelOverrides, demoResolvedRelayUnknownIds, inputData]);

  const activeSection = controlledSection || internalSection;
  const range = controlledRange || internalRange;
  const availableNavItems = useMemo(
    () => {
      const ownerFiltered = NAV_ITEMS.filter((item) => (
        item.id !== "showcase" || isPlatformOwner === true
      ));
      return Array.isArray(visibleSections)
        ? ownerFiltered.filter((item) => visibleSections.includes(item.id))
        : ownerFiltered;
    },
    [isPlatformOwner, visibleSections],
  );
  const renderSection = availableNavItems.some((item) => item.id === activeSection)
    ? activeSection
    : availableNavItems[0]?.id || "";
  const allExceptions = useMemo(() => [...data.exceptions, ...data.publishingExceptions, ...data.assetExceptions], [data.assetExceptions, data.exceptions, data.publishingExceptions]);
  const entitlementsWritable = demoMode || canManageEntitlements === true;
  const adminAccessWritable = demoMode || canManageAdminAccess === true;
  const auditReadable = demoMode || canReadAudit === true;
  const adminAccessReadable = demoMode || canReadAdminAccess === true;

  useEffect(() => {
    setRelayNativeConsoleGrant(null);
    setRelayNativeConsoleBlock("");
  }, [canAuthorizeRelayNativeConsole, relayNativeConsoleAccessScope, renderSection]);

  useEffect(() => {
    if (!relayNativeConsoleGrant) return undefined;
    const timeout = window.setTimeout(() => {
      setRelayNativeConsoleGrant(null);
      setToast("Relay 原生控制台临时入口已自动清除；需要时请重新授权。");
    }, 60_000);
    return () => window.clearTimeout(timeout);
  }, [relayNativeConsoleGrant]);

  useEffect(() => {
    if (auditTab === "audit" && !auditReadable && adminAccessReadable) setAuditTab("access");
    if (auditTab === "access" && !adminAccessReadable && auditReadable) setAuditTab("audit");
  }, [adminAccessReadable, auditReadable, auditTab]);

  useEffect(() => {
    writeAuditLocation({ tab: auditTab, query: auditQuery, result: auditResult });
  }, [auditQuery, auditResult, auditTab]);

  useEffect(() => {
    const restoreAuditFilters = () => {
      const restored = readAuditLocation();
      setAuditTab(restored.tab);
      setAuditQuery(restored.query);
      setAuditResult(restored.result);
    };
    globalThis.addEventListener?.("popstate", restoreAuditFilters);
    return () => globalThis.removeEventListener?.("popstate", restoreAuditFilters);
  }, []);

  useEffect(() => {
    if (!availableNavItems.length || availableNavItems.some((item) => item.id === activeSection)) return;
    navigate(availableNavItems[0].id);
  }, [activeSection, availableNavItems]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const navigation = operationsNavRef.current;
    if (!navigation) return undefined;

    const updateOverflow = () => {
      const maxScroll = Math.max(0, navigation.scrollWidth - navigation.clientWidth);
      setNavOverflow({
        before: navigation.scrollLeft > 1,
        after: navigation.scrollLeft < maxScroll - 1,
      });
    };

    const keepActiveModuleVisible = () => {
      const activeButton = navigation.querySelector(`[data-ops-nav-item="${renderSection}"]`);
      const scrollRoot = document.scrollingElement;
      const documentScroll = scrollRoot
        ? { left: scrollRoot.scrollLeft, top: scrollRoot.scrollTop }
        : null;
      activeButton?.scrollIntoView({ block: "nearest", inline: "center" });
      if (scrollRoot && documentScroll) {
        scrollRoot.scrollLeft = documentScroll.left;
        scrollRoot.scrollTop = documentScroll.top;
      }
      globalThis.requestAnimationFrame?.(updateOverflow);
    };

    keepActiveModuleVisible();
    updateOverflow();
    navigation.addEventListener("scroll", updateOverflow, { passive: true });
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(keepActiveModuleVisible);
    resizeObserver?.observe(navigation);
    navigation.querySelectorAll("[data-ops-nav-item]").forEach((button) => resizeObserver?.observe(button));

    let cancelled = false;
    document.fonts?.ready.then(() => {
      if (!cancelled) keepActiveModuleVisible();
    });

    return () => {
      cancelled = true;
      navigation.removeEventListener("scroll", updateOverflow);
      resizeObserver?.disconnect();
    };
  }, [availableNavItems, renderSection]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(""), 3000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const navigate = (section) => {
    const targetSection = availableNavItems.some((item) => item.id === section)
      ? section
      : availableNavItems[0]?.id;
    if (!targetSection) return;
    if (!controlledSection) setInternalSection(targetSection);
    onSectionChange?.(targetSection);
  };

  const changeRange = (value) => {
    if (!controlledRange) setInternalRange(value);
    onRangeChange?.(value);
  };

  const scrollModules = (direction) => {
    const navigation = operationsNavRef.current;
    if (!navigation) return;
    navigation.scrollBy({
      left: direction * Math.max(160, navigation.clientWidth * 0.72),
      behavior: "smooth",
    });
  };

  const changeEnvironment = (value) => {
    setEnvironment(value);
    onEnvironmentChange?.(value);
  };

  const perform = async (name, action, successMessage) => {
    setBusyAction(name);
    setLocalError("");
    try {
      await action();
      if (successMessage) setToast(successMessage);
      return true;
    } catch (actionError) {
      setLocalError(actionError?.message || "操作失败，请稍后重试。");
      return false;
    } finally {
      setBusyAction("");
    }
  };

  const showExceptionCenter = () => {
    setExceptionCenterOpen(true);
    onEnterExceptionCenter?.();
  };

  const refresh = () => {
    if (onRefresh) {
      perform("refresh", () => onRefresh({ section: renderSection, range, environment }), "平台数据已更新");
      return;
    }
    if (demoMode) {
      setDemoData((current) => ({ ...current, summary: { ...current.summary, lastRefreshed: new Date().toISOString() } }));
      setToast("演示数据刷新时间已更新");
      return;
    }
    setLocalError("当前未提供刷新回调，未请求任何数据。");
  };

  const resolveException = async (item, note) => {
    if (!demoMode && !onExceptionAction) {
      setLocalError("当前未提供异常处置回调，未提交任何变更。");
      return;
    }
    const success = await perform(`exception:${item.id}`, async () => {
      await onExceptionAction?.({ action: "resolve", exception: item, note });
      if (demoMode) {
        const resolveList = (items) => items.map((current) => current.id === item.id ? { ...current, status: "resolved", owner: administrator.name } : current);
        setDemoData((current) => ({ ...current, exceptions: resolveList(current.exceptions), publishingExceptions: resolveList(current.publishingExceptions), assetExceptions: resolveList(current.assetExceptions) }));
      }
    }, "异常已标记为解决");
    if (success) setSelectedException(null);
  };

  const openRelayUnknown = async (item) => {
    if (!demoMode && !onRelayUnknownDetail) {
      setLocalError("当前未提供 Relay 未知提交详情回调，未读取任何数据。");
      return;
    }
    setBusyAction(`relay-unknown-open:${item.jobId}`);
    setLocalError("");
    setRelayUnknownError("");
    setRelayUnknownPendingForm(null);
    try {
      const detail = demoMode ? item : await onRelayUnknownDetail(item);
      setSelectedRelayUnknown(detail);
      setRelayUnknownDetailVersion((value) => value + 1);
      setRelayUnknownRequiresRefresh(false);
    } catch (detailError) {
      setLocalError(detailError?.message || "Relay 未知提交详情读取失败，请刷新列表后重试。");
    } finally {
      setBusyAction("");
    }
  };

  const refreshRelayUnknown = async () => {
    const item = selectedRelayUnknown;
    if (!item) return;
    setBusyAction(`relay-unknown-refresh:${item.jobId}`);
    setRelayUnknownError("");
    try {
      const refreshed = demoMode
        ? { state: "pending", item }
        : onRelayUnknownRefresh
          ? await onRelayUnknownRefresh({ item, form: relayUnknownPendingForm })
          : { state: "pending", item: await onRelayUnknownDetail?.(item) };
      if (refreshed?.state === "resolved" && refreshed.receipt) {
        setSelectedRelayUnknown(null);
        setRelayUnknownPendingForm(null);
        setRelayUnknownRequiresRefresh(false);
        setToast("已通过 Relay receipt 确认处理完成；页面未再次提交 resolve");
        return;
      }
      const detail = refreshed?.item;
      if (!detail) throw new Error("Relay 未知提交详情不存在，请刷新列表确认处理状态。");
      setSelectedRelayUnknown(detail);
      setRelayUnknownDetailVersion((value) => value + 1);
      setRelayUnknownPendingForm(null);
      setRelayUnknownRequiresRefresh(false);
      setToast("详情已刷新，请重新核实并审批");
    } catch (detailError) {
      setRelayUnknownError(detailError?.message || "详情刷新失败；请关闭窗口并刷新列表确认处理状态。");
      setRelayUnknownRequiresRefresh(true);
    } finally {
      setBusyAction("");
    }
  };

  const resolveRelayUnknown = async (item, form) => {
    if (!demoMode && !onRelayUnknownResolve) {
      setRelayUnknownError("当前未提供 Relay 人工对账回调，未提交任何变更。");
      return;
    }
    setBusyAction(`relay-unknown-resolve:${item.jobId}`);
    setRelayUnknownError("");
    try {
      const result = await onRelayUnknownResolve?.({ item, form });
      if (demoMode) {
        setDemoResolvedRelayUnknownIds((current) => new Set(current).add(item.jobId));
      }
      setSelectedRelayUnknown(null);
      setRelayUnknownPendingForm(null);
      setRelayUnknownRequiresRefresh(false);
      setToast(
        demoMode
          ? "演示处置已完成（未调用 Relay）"
          : result?.confirmation === "result_readback"
          ? "resolve 响应不明，但已通过只读 Relay receipt 确认完成"
          : "未知提交已完成 resolve，Relay receipt 已校验",
      );
    } catch (resolveError) {
      setRelayUnknownError(resolveError?.message || "resolve 结果不明。禁止重复提交，请刷新详情核实。");
      setRelayUnknownPendingForm(
        resolveError?.relayResultProofRequired === true ? { ...form } : null,
      );
      setRelayUnknownRequiresRefresh(true);
    } finally {
      setBusyAction("");
    }
  };

  const openRelayCallbackDeadLetter = async (item) => {
    if (!onRelayCallbackDeadLetterDetail) return;
    setBusyAction(`relay-callback-dlq-open:${item.eventId}`);
    setRelayCallbackDeadLetterError("");
    setRelayCallbackDeadLetterRequiresReadback(false);
    try {
      setSelectedRelayCallbackDeadLetter(await onRelayCallbackDeadLetterDetail(item));
    } catch (openError) {
      setLocalError(openError?.message || "Callback 死信详情读取失败。");
    } finally {
      setBusyAction("");
    }
  };

  const redriveRelayCallbackDeadLetter = async (item, form) => {
    if (!onRelayCallbackDeadLetterRedrive) return;
    setBusyAction(`relay-callback-dlq-redrive:${item.eventId}`);
    setRelayCallbackDeadLetterError("");
    try {
      const result = await onRelayCallbackDeadLetterRedrive({ item, form });
      setSelectedRelayCallbackDeadLetter(null);
      setToast(result?.confirmation === "result_readback" ? "网络结果已通过只读回执确认，页面未重复 POST" : "Callback 已重新进入签名投递队列");
    } catch (redriveError) {
      setRelayCallbackDeadLetterError(redriveError?.message || "重新投递结果不明；请关闭并刷新，只读核对结果。页面不会重复 POST。");
      if (redriveError?.callbackRedriveProofRequired) {
        setRelayCallbackDeadLetterRequiresReadback(true);
        setToast("结果待只读核对；当前窗口已锁定，不会再次 POST");
      }
    } finally {
      setBusyAction("");
    }
  };

  const resetRelayChannelDrawer = () => {
    setSelectedRelayChannel(null);
    setRelayChannelIntent("detail");
    setRelayChannelTargetStatus("");
    setRelayChannelOperationId("");
    setRelayChannelPendingValues(null);
    setRelayChannelReceipt(null);
    setRelayChannelError("");
    setRelayChannelRequiresReadback(false);
  };

  const closeRelayChannelDrawer = () => {
    if (relayChannelRequiresReadback) {
      setToast("请先只读核对当前 operation_id；页面不会重复提交");
      return;
    }
    resetRelayChannelDrawer();
  };

  const openRelayChannel = async (channel, intent = "detail", targetStatus = "") => {
    if (intent === "test" && !channel.testSupported) {
      setLocalError("该渠道不支持通用连通测试，请在 staging 使用真实 canary 验收。");
      return;
    }
    if (!demoMode && !onRelayChannelDetail) {
      setLocalError("当前未提供 Relay 渠道详情回调，未读取任何数据。");
      return;
    }
    setBusyAction(`relay-channel-open:${channel.id}`);
    setLocalError("");
    setRelayChannelError("");
    setRelayChannelReceipt(null);
    setRelayChannelPendingValues(null);
    setRelayChannelRequiresReadback(false);
    try {
      const detail = demoMode ? channel : await onRelayChannelDetail(channel);
      setSelectedRelayChannel(detail);
      setRelayChannelIntent(intent);
      setRelayChannelTargetStatus(targetStatus);
      setRelayChannelOperationId(intent === "test" || intent === "status"
        ? createRelayChannelOperationId(intent)
        : "");
    } catch {
      setLocalError("Relay 渠道详情读取失败，请刷新列表后重试。");
    } finally {
      setBusyAction("");
    }
  };

  const updateRelayChannelFromReceipt = (channel, receipt) => {
    if (receipt?.kind !== "status" || !receipt.result) return channel;
    return {
      ...channel,
      status: receipt.result.currentStatus,
      revision: receipt.resultRevision || channel.revision,
    };
  };

  const submitRelayChannelOperation = async ({ channel, kind, values }) => {
    if (!demoMode && !onRelayChannelOperation) {
      setRelayChannelError("当前未提供 Relay 渠道操作回调，未提交任何变更。");
      return;
    }
    setBusyAction(`relay-channel-operation:${channel.id}`);
    setRelayChannelError("");
    setRelayChannelPendingValues(values);
    try {
      let result;
      if (demoMode) {
        const now = new Date().toISOString();
        const nextRevision = kind === "status"
          ? `sha256:${(values.targetStatus === "enabled" ? "a" : "b").repeat(64)}`
          : null;
        const receipt = {
          operationId: values.operationId,
          channelId: channel.id,
          kind,
          state: "succeeded",
          reason: values.reason,
          previousRevision: kind === "status" ? channel.revision : null,
          resultRevision: nextRevision,
          result: kind === "test"
            ? { success: true, responseTimeMs: 427, errorCode: null }
            : { previousStatus: channel.status, currentStatus: values.targetStatus, changed: channel.status !== values.targetStatus, errorCode: null },
          createdAt: now,
          completedAt: now,
          idempotentReplay: false,
        };
        result = { confirmation: "demo", receipt };
        const updated = kind === "test"
          ? { ...channel, lastTestedAt: now, responseTimeMs: 427 }
          : updateRelayChannelFromReceipt(channel, receipt);
        setDemoRelayChannelOverrides((current) => ({ ...current, [channel.id]: updated }));
        setSelectedRelayChannel(updated);
      } else {
        result = await onRelayChannelOperation({ channel, kind, values });
        setSelectedRelayChannel((current) => updateRelayChannelFromReceipt(current || channel, result.receipt));
      }
      setRelayChannelReceipt(result.receipt);
      setRelayChannelRequiresReadback(result.receipt?.state === "pending");
      setToast(
        demoMode
          ? "演示操作已完成（未调用 Relay）"
          : result.confirmation === "result_readback"
            ? "已用同一 operation_id 只读确认结果，页面没有重复 POST"
            : "Relay 渠道操作回执已校验",
      );
    } catch (operationError) {
      const requiresReadback = operationError?.relayChannelReadbackRequired === true;
      setRelayChannelRequiresReadback(requiresReadback);
      setRelayChannelError(
        requiresReadback
          ? "提交结果尚未能确认。当前 operation_id 已锁定，请稍后只读核对；页面不会重复 POST。"
          : operationError?.status === 403
            ? "当前平台管理员没有管理 Relay 渠道的权限。"
            : "Relay 渠道操作未完成，未确认任何状态变化。请刷新详情后重新审批。",
      );
    } finally {
      setBusyAction("");
    }
  };

  const readbackRelayChannelOperation = async () => {
    if (!selectedRelayChannel || !relayChannelPendingValues || !onRelayChannelOperationRead) return;
    setBusyAction(`relay-channel-readback:${selectedRelayChannel.id}`);
    setRelayChannelError("");
    try {
      const receipt = await onRelayChannelOperationRead({
        channel: selectedRelayChannel,
        kind: relayChannelIntent,
        values: relayChannelPendingValues,
      });
      setRelayChannelReceipt(receipt);
      setSelectedRelayChannel((current) => updateRelayChannelFromReceipt(current, receipt));
      setRelayChannelRequiresReadback(receipt.state === "pending");
      setToast(receipt.state === "pending" ? "操作仍在处理中，页面保持同一 operation_id" : "只读回执已确认，页面没有重复 POST");
    } catch {
      setRelayChannelRequiresReadback(true);
      setRelayChannelError("仍无法读取该 operation_id 的可信回执。页面保持锁定，不会重复 POST。");
    } finally {
      setBusyAction("");
    }
  };

  const effectiveRelayNativeConsoleDisabledReason = relayNativeConsoleBlock === "unconfigured"
    ? "Platform 尚未配置 Relay 原生控制台入口；配置完成后请刷新页面。"
    : relayNativeConsoleBlock === "forbidden"
      ? "当前平台管理员没有打开 Relay 高风险运维入口的权限。"
      : !canAuthorizeRelayNativeConsole
        ? relayNativeConsoleDisabledReason || "当前会话不能授权 Relay 高风险运维入口。"
        : "";

  const authorizeRelayNativeConsole = async () => {
    setRelayNativeConsoleGrant(null);
    setRelayNativeConsoleBlock("");
    setLocalError("");
    if (!onRelayNativeConsoleAuthorize || !canAuthorizeRelayNativeConsole) {
      setLocalError(effectiveRelayNativeConsoleDisabledReason);
      return;
    }
    setBusyAction("relay-native-console-authorize");
    try {
      const grant = await onRelayNativeConsoleAuthorize();
      setRelayNativeConsoleGrant(grant);
      setToast("Relay 高风险运维入口已授权；请在 60 秒内通过第二次点击打开。尚未证明 new-api 已打开或登录。");
    } catch (authorizeError) {
      setRelayNativeConsoleGrant(null);
      setRelayNativeConsoleBlock(relayNativeConsoleBlockReason(authorizeError));
      setLocalError(relayNativeConsoleErrorMessage(authorizeError));
    } finally {
      setBusyAction("");
    }
  };

  const consumeRelayNativeConsoleGrant = () => {
    setRelayNativeConsoleGrant(null);
    setToast("临时入口已从本页面清除；new-api 仍需在新标签页独立登录。");
  };

  const dismissRelayNativeConsoleGrant = () => {
    setRelayNativeConsoleGrant(null);
    setToast("已取消本次 Relay 高风险运维入口授权展示。");
  };

  const openEntitlement = (company, product, readOnly = false) => {
    const grant = data.entitlementGrants[buildEntitlementKey(company.id, product.id)] || { companyId: company.id, productId: product.id, state: "unconfigured" };
    setEntitlementCell({ company, product, grant, readOnly });
  };

  const saveEntitlement = async ({ company, product, grant, reason }) => {
    if (!demoMode && !onEntitlementSave) {
      setLocalError("当前未提供企业权益保存回调，未提交任何变更。");
      return;
    }
    const success = await perform(`entitlement:${company.id}:${product.id}`, async () => {
      await onEntitlementSave?.({ companyId: company.id, productId: product.id, grant, reason });
      if (demoMode) {
        setDemoData((current) => ({ ...current, entitlementGrants: { ...current.entitlementGrants, [buildEntitlementKey(company.id, product.id)]: grant } }));
      }
    }, `${company.name} 的 ${product.name} 权益已更新`);
    if (success) setEntitlementCell(null);
  };

  const buildBatchPreview = () => {
    const companyIds = Array.from(selectedCompanyIds);
    let productIds = Array.from(selectedProductIds);
    let changes = [];
    if (batchMode === "enable" || batchMode === "disable") {
      const impact = summarizeBatchImpact({ companies: data.companies, products: data.entitlementProducts, grants: data.entitlementGrants, companyIds, productIds, nextState: batchMode === "enable" ? "enabled" : "disabled" });
      setBatchPreview({ ...impact, mode: batchMode, companyIds, productIds });
      return;
    }
    if (batchMode === "copy") {
      productIds = data.entitlementProducts.map((item) => item.id);
      companyIds.forEach((companyId) => productIds.forEach((productId) => {
        const previousState = resolveEntitlementState(data.entitlementGrants, companyId, productId);
        const nextState = resolveEntitlementState(data.entitlementGrants, copySourceId, productId);
        if (previousState !== nextState) {
          changes.push({ companyId, companyName: data.companies.find((item) => item.id === companyId)?.name, productId, productName: data.entitlementProducts.find((item) => item.id === productId)?.name, previousState, nextState });
        }
      }));
    }
    if (batchMode === "template") {
      const template = data.entitlementTemplates.find((item) => item.id === templateId);
      productIds = template?.productIds || [];
      companyIds.forEach((companyId) => productIds.forEach((productId) => {
        const previousState = resolveEntitlementState(data.entitlementGrants, companyId, productId);
        if (previousState !== "enabled") changes.push({ companyId, companyName: data.companies.find((item) => item.id === companyId)?.name, productId, productName: data.entitlementProducts.find((item) => item.id === productId)?.name, previousState, nextState: "enabled" });
      }));
    }
    const total = companyIds.length * productIds.length;
    setBatchPreview({ mode: batchMode, companyIds, productIds, copySourceId, templateId, companyCount: companyIds.length, productCount: productIds.length, cellCount: total, changedCount: changes.length, unchangedCount: total - changes.length, changes });
  };

  const commitBatch = async (preview) => {
    if (!demoMode && !onBatchEntitlementCommit) {
      setLocalError("当前未提供批量权益提交回调，未提交任何变更。");
      return;
    }
    const success = await perform("batch-entitlements", async () => {
      await onBatchEntitlementCommit?.(preview);
      if (demoMode) {
        setDemoData((current) => {
          const grants = { ...current.entitlementGrants };
          preview.changes.forEach((change) => {
            const key = buildEntitlementKey(change.companyId, change.productId);
            const previous = grants[key] || { companyId: change.companyId, productId: change.productId };
            let next = { ...previous, state: change.nextState };
            if (preview.mode === "copy") {
              const source = grants[buildEntitlementKey(preview.copySourceId, change.productId)];
              next = source ? { ...source, companyId: change.companyId, productId: change.productId } : next;
            }
            grants[key] = next;
          });
          return { ...current, entitlementGrants: grants };
        });
      }
    }, `已提交 ${preview.changedCount} 项权益变更`);
    if (success) {
      setBatchPreview(null);
      setSelectedCompanyIds(new Set());
      setSelectedProductIds(new Set());
    }
  };

  const exportAudit = async (items) => {
    if (onAuditExport) {
      await perform("audit-export", () => onAuditExport({ range, items }), "审计导出已开始");
      return;
    }
    if (demoMode) {
      downloadTextFile(`platform-audit-${new Date().toISOString().slice(0, 10)}.csv`, `\uFEFF${toAuditCsv(items)}`, "text/csv;charset=utf-8");
      setToast("演示审计 CSV 已导出");
    }
  };

  const rollbackAudit = async (event, reason) => {
    if (!demoMode && !onAuditRollback) return;
    const success = await perform(`audit-rollback:${event.id}`, async () => {
      await onAuditRollback?.({ event, reason });
      if (demoMode) {
        setDemoData((current) => ({ ...current, auditEvents: [{ id: `demo-rollback-${Date.now()}`, occurredAt: new Date().toISOString(), actorName: administrator.name, actorId: "demo-admin", actionLabel: "创建反向变更", action: `${event.action}.inverse`, targetLabel: event.targetLabel, reason, result: "success", before: event.after, after: event.before, rollbackHint: `关联原审计 ${event.id}` }, ...current.auditEvents] }));
      }
    }, "反向变更已创建");
    if (success) setSelectedAudit(null);
  };

  const saveAdminAccess = async (admin, permissions, reason) => {
    if (!demoMode && !onAdminAccessSave) {
      setLocalError("当前未提供平台管理员权限保存回调，未提交任何变更。");
      return;
    }
    const success = await perform(`admin-access:${admin.id}`, async () => {
      await onAdminAccessSave?.({ adminId: admin.id, permissions, reason });
      if (demoMode) setDemoData((current) => ({ ...current, platformAdmins: current.platformAdmins.map((item) => item.id === admin.id ? { ...item, permissions } : item) }));
    }, `${admin.name} 的平台权限已更新`);
    if (success) setSelectedAdmin(null);
  };

  const brandTargetSection = availableNavItems.some((item) => item.id === "cockpit")
    ? "cockpit"
    : availableNavItems[0]?.id || "";
  const pageMeta = {
    cockpit: { title: "平台经营总览", detail: "从需要处理的异常出发，连续查看经营趋势、模型利润与企业表现。" },
    "task-operations": { title: "任务运营中心", detail: "跟踪提交、排队、生成、转存与回调完整链路。" },
    "model-profit": { title: "模型盈利", detail: "按模型核对调用量、收入、渠道成本、毛利与质量。" },
    "company-health": { title: "企业健康", detail: "提前发现余额、活跃度、消费、预留、失败率与权益风险。" },
    entitlements: { title: "企业权益分发", detail: "按企业统一控制模型、功能、智能体、外部 API 与自动发布。" },
    channels: { title: "Relay 渠道控制面", detail: "通过 Platform 安全门面完成渠道读取、测试和启停，并保留未知提交与死信处置队列。" },
    "publishing-assets": { title: "发布与资产异常", detail: "集中处理发布、OAuth、OBS 转存与下载登记异常。" },
    showcase: { title: "首页精选案例", detail: "编辑草稿、检查桌面与移动端预览，并以不可变版本发布或回滚首页内容。" },
    "access-audit": { title: "权限与审计", detail: "平台管理员最小权限、变更差异、导出和恢复线索。" },
  }[renderSection] || { title: "平台运营" };

  useEffect(() => {
    if (!globalThis.document) return undefined;
    const nextTitle = `${pageMeta.title} · ${BRAND_NAME}`;
    globalThis.document.title = nextTitle;
    return () => {
      if (globalThis.document?.title === nextTitle) globalThis.document.title = BRAND_NAME;
    };
  }, [pageMeta.title]);

  let content = null;
  if (renderSection === "cockpit") content = <OperatingCockpitScreen data={data} onNavigate={navigate} onExceptionSelect={setSelectedException} onCompanyOpen={onCompanyOpen} onModelOpen={onModelOpen} onRetry={refresh} />;
  if (renderSection === "task-operations") content = <TaskOperationsScreen data={data} onExceptionSelect={setSelectedException} onShowExceptionCenter={showExceptionCenter} onReliabilityAction={onReliabilityAction} onRetry={refresh} />;
  if (renderSection === "model-profit") content = <ModelProfitabilityScreen data={data} onModelOpen={onModelOpen} onRetry={refresh} />;
  if (renderSection === "company-health") content = <CompanyHealthScreen data={data} onCompanyOpen={onCompanyOpen} onRetry={refresh} />;
  if (renderSection === "entitlements") content = <EntitlementMatrixScreen data={data} selectedCompanyIds={selectedCompanyIds} onSelectedCompanyIds={setSelectedCompanyIds} selectedProductIds={selectedProductIds} onSelectedProductIds={setSelectedProductIds} batchMode={batchMode} onBatchMode={setBatchMode} copySourceId={copySourceId} onCopySourceId={setCopySourceId} templateId={templateId} onTemplateId={setTemplateId} onPreview={buildBatchPreview} onCellOpen={openEntitlement} onRetry={refresh} readOnly={!entitlementsWritable} />;
  if (renderSection === "channels") content = (
    <ChannelOperationsScreen
      data={data}
      onRelayChannelOpen={demoMode || onRelayChannelDetail ? openRelayChannel : undefined}
      canManageRelayChannels={demoMode || canManageRelayChannels === true}
      onRelayNativeConsoleAuthorize={canAuthorizeRelayNativeConsole
        && !effectiveRelayNativeConsoleDisabledReason
        ? authorizeRelayNativeConsole
        : undefined}
      onRelayNativeConsoleConsume={consumeRelayNativeConsoleGrant}
      onRelayNativeConsoleDismiss={dismissRelayNativeConsoleGrant}
      relayNativeConsoleGrant={relayNativeConsoleGrant}
      relayNativeConsoleBusy={busyAction === "relay-native-console-authorize"}
      relayNativeConsoleDisabledReason={effectiveRelayNativeConsoleDisabledReason}
      onRelayUnknownOpen={demoMode || onRelayUnknownDetail ? openRelayUnknown : undefined}
      openingRelayUnknownId={busyAction.startsWith("relay-unknown-open:")
        ? busyAction.slice("relay-unknown-open:".length)
        : ""}
      onRelayCallbackDeadLetterOpen={onRelayCallbackDeadLetterDetail
        ? openRelayCallbackDeadLetter
        : undefined}
      openingRelayCallbackDeadLetterId={busyAction.startsWith("relay-callback-dlq-open:")
        ? busyAction.slice("relay-callback-dlq-open:".length)
        : ""}
      onRetry={refresh}
    />
  );
  if (renderSection === "publishing-assets") content = <PublishingAssetsScreen data={data} onExceptionSelect={setSelectedException} onRetry={refresh} />;
  if (renderSection === "showcase") content = showcaseContent;
  if (renderSection === "access-audit") content = <AuditAccessScreen data={data} tab={auditTab} onTab={setAuditTab} auditQuery={auditQuery} onAuditQuery={setAuditQuery} auditResult={auditResult} onAuditResult={setAuditResult} onAuditOpen={setSelectedAudit} onAuditExport={exportAudit} canExport={demoMode || Boolean(onAuditExport)} onAdminOpen={setSelectedAdmin} canManageAdmins={adminAccessWritable} canReadAudit={auditReadable} canReadAdminAccess={adminAccessReadable} onRetry={refresh} />;
  if (!availableNavItems.length) content = accessPending
    ? <DatasetState status="loading" label="平台权限" detail="正在核验管理员身份与模块授权。" />
    : error
      ? <DatasetState status="failed" label="平台权限" detail={error} onRetry={refresh} />
      : <EmptyState title="当前平台管理员没有已授权模块" detail="请联系平台所有者分配最小必要权限。" />;

  return (
    <div className={cx("ops-console", className)} data-theme={activeSkin} data-active-section={renderSection || "access"}>
      <header className="ops-topbar">
        <button className="ops-brand" type="button" onClick={() => navigate(brandTargetSection)} disabled={!brandTargetSection} aria-label={brandTargetSection === "cockpit" ? `${BRAND_NAME} · 返回经营总览` : `${BRAND_NAME} · 前往首个已授权模块`}><BrandLogo variant="responsive" mobileBreakpoint={820} /></button>
        <div className="ops-module-navigation">
          <button className="ops-nav-scroll-button is-previous" data-icon-only="true" type="button" onClick={() => scrollModules(-1)} disabled={!navOverflow.before} aria-label="查看前面的平台模块" title="前面的模块"><CaretLeft size={16} /></button>
          <nav ref={operationsNavRef} aria-label="平台管理员模块">
            {availableNavItems.map((item) => <button key={item.id} data-ops-nav-item={item.id} type="button" className={renderSection === item.id ? "is-active" : ""} aria-current={renderSection === item.id ? "page" : undefined} onClick={() => navigate(item.id)}>{item.label}</button>)}
          </nav>
          <button className="ops-nav-scroll-button is-next" data-icon-only="true" type="button" onClick={() => scrollModules(1)} disabled={!navOverflow.after} aria-label="查看更多平台模块" title="更多模块"><CaretRight size={16} /></button>
        </div>
        <div className="ops-admin-tools">
          <SkinSwitcher value={activeSkin} onChange={onSkinChange} />
          {onOpenBasicConfig ? <button type="button" className="ops-basic-config-button" data-icon-only="true" onClick={onOpenBasicConfig} aria-label="打开基础配置" title="基础配置"><SlidersHorizontal size={16} /><span className="ops-basic-config-label">基础配置</span></button> : null}
          <button type="button" className="ops-help-button" onClick={() => setToast("运营控制台说明：先处理异常，再核对经营、利润与企业健康。") }><WarningCircle size={16} />帮助</button>
          <span className="ops-top-divider" />
          {demoMode && onDemoPersonaChange ? (
            <DemoAccountSwitcher value={demoPersonaId} onChange={onDemoPersonaChange} />
          ) : (
            <div className="ops-account-menu">
              <button type="button" className="ops-account-button" aria-expanded={accountOpen} onClick={() => setAccountOpen((value) => !value)}><UserCircle size={18} /><span>{administrator.name}</span><CaretDown size={13} /></button>
              {accountOpen ? <div role="menu"><span>{administrator.roleLabel}</span><button type="button" role="menuitem" onClick={onLogout}>退出登录</button></div> : null}
            </div>
          )}
        </div>
      </header>
      <main aria-labelledby="ops-page-title">
        <PageTitle
          title={pageMeta.title}
          detail={pageMeta.detail}
          controls={renderSection === "showcase" ? null : <><RangeControls range={range} environment={environment} environmentOptions={environmentOptions} lastRefreshed={data.summary.lastRefreshed} onRange={changeRange} onEnvironment={changeEnvironment} onRefresh={refresh} loading={loading || busyAction === "refresh"} showRange={TIME_SCOPED_SECTIONS.has(renderSection)} />{renderSection === "task-operations" ? <button className="ops-primary-button" type="button" onClick={showExceptionCenter}><Lightning size={16} />进入异常处理</button> : null}</>}
        />
        <PageStatus error={localError || error} loading={loading} toast={toast} />
        <div className="ops-page-content">{content || <EmptyState title="模块不存在" />}</div>
      </main>

      {exceptionCenterOpen ? <ExceptionCenterDrawer items={allExceptions} onClose={() => setExceptionCenterOpen(false)} onSelect={(item) => { setExceptionCenterOpen(false); setSelectedException(item); }} /> : null}
      {selectedException ? <ExceptionDrawer item={selectedException} onClose={() => setSelectedException(null)} onResolve={resolveException} onOpenRelayReconciliation={() => { setSelectedException(null); navigate("channels"); }} canResolve={canResolveException ? canResolveException(selectedException) : demoMode} busy={busyAction === `exception:${selectedException.id}`} /> : null}
      {selectedRelayChannel ? <RelayChannelDrawer key={`${selectedRelayChannel.id}:${relayChannelOperationId || "detail"}`} channel={selectedRelayChannel} intent={relayChannelIntent} targetStatus={relayChannelTargetStatus} operationId={relayChannelOperationId} canManage={demoMode || canManageRelayChannels === true} demoMode={demoMode} busy={busyAction === `relay-channel-operation:${selectedRelayChannel.id}` || busyAction === `relay-channel-readback:${selectedRelayChannel.id}`} error={relayChannelError} requiresReadback={relayChannelRequiresReadback} receipt={relayChannelReceipt} onClose={closeRelayChannelDrawer} onSubmit={submitRelayChannelOperation} onReadback={readbackRelayChannelOperation} /> : null}
      {selectedRelayUnknown ? <RelayUnknownDrawer key={`${selectedRelayUnknown.jobId}:${relayUnknownDetailVersion}`} item={selectedRelayUnknown} onClose={() => { setSelectedRelayUnknown(null); setRelayUnknownError(""); setRelayUnknownRequiresRefresh(false); setRelayUnknownPendingForm(null); }} onRefresh={refreshRelayUnknown} onResolve={resolveRelayUnknown} canManage={demoMode || canManageRelayUnknown === true} busy={busyAction === `relay-unknown-resolve:${selectedRelayUnknown.jobId}`} refreshing={busyAction === `relay-unknown-refresh:${selectedRelayUnknown.jobId}`} error={relayUnknownError} requiresRefresh={relayUnknownRequiresRefresh} /> : null}
      {selectedRelayCallbackDeadLetter ? <RelayCallbackDeadLetterDrawer item={selectedRelayCallbackDeadLetter} onClose={() => { setSelectedRelayCallbackDeadLetter(null); setRelayCallbackDeadLetterError(""); setRelayCallbackDeadLetterRequiresReadback(false); }} onRedrive={redriveRelayCallbackDeadLetter} canManage={canManageRelayCallbackDeadLetters === true} busy={busyAction === `relay-callback-dlq-redrive:${selectedRelayCallbackDeadLetter.eventId}`} error={relayCallbackDeadLetterError} requiresReadback={relayCallbackDeadLetterRequiresReadback} /> : null}
      {entitlementCell ? <EntitlementDrawer {...entitlementCell} onClose={() => setEntitlementCell(null)} onSave={saveEntitlement} busy={busyAction.startsWith("entitlement:")} /> : null}
      {batchPreview ? <BatchPreviewDrawer preview={batchPreview} companies={data.companies} products={data.entitlementProducts} onClose={() => setBatchPreview(null)} onConfirm={commitBatch} busy={busyAction === "batch-entitlements"} /> : null}
      {selectedAudit ? <AuditDrawer event={selectedAudit} onClose={() => setSelectedAudit(null)} onRollback={demoMode || onAuditRollback ? rollbackAudit : null} busy={busyAction.startsWith("audit-rollback:")} /> : null}
      {selectedAdmin ? <AdminAccessDrawer admin={selectedAdmin} catalog={data.adminPermissionCatalog} onClose={() => setSelectedAdmin(null)} onSave={saveAdminAccess} busy={busyAction.startsWith("admin-access:")} /> : null}
    </div>
  );
}

export default AdminOperationsConsole;
