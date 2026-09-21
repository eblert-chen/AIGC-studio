import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ArrowClockwise,
  Buildings,
  ChartLineUp,
  Check,
  FilmSlate,
  Gauge,
  Gift,
  ListBullets,
  Receipt,
  ShieldCheck,
  SlidersHorizontal,
  SpinnerGap,
  UserCircle,
  Users,
  Wallet,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  billingPresentationState,
} from "./billingPresentation.js";
import {
  ACTIVE_PERMISSION_CATALOG,
  requirePermissionCatalog,
} from "./permissionCatalog.js";
import { DemoAccountSwitcher } from "./DemoAccountSwitcher.jsx";
import { normalizeSkin, SkinSwitcher } from "./SkinSwitcher.jsx";
import { BrandLogo, BRAND_NAME } from "./BrandLogo.jsx";
import { canOpenCompanyConsoleSection } from "./companyConsolePermissions.js";
import { preferredCreationSurfaceForManagement } from "./identitySurfaces.js";
import { OperationsWorkspaceActions } from "./admin/OperationsWorkspaceActions.jsx";
import { CapabilityEditorFields } from "./components/management/CapabilityEditorFields.jsx";
import { CompanyEntitlementsPanel } from "./components/management/CompanyEntitlementsPanel.jsx";
import { MemberAccessFields } from "./components/management/MemberAccessFields.jsx";
import {
  ManagementAccessState,
  PrimaryButton,
  QuietButton,
  STATUS_LABELS,
} from "./components/management/ManagementPrimitives.jsx";
import {
  memberAccessStateKey,
  permissionOverrideMap,
  roleList,
  safeId,
  withMemberPermissionState,
} from "./components/management/managementAccess.js";
import {
  money,
  pricingModeLabel,
  shortDate,
} from "./components/management/managementPresentation.js";
import {
  billingNumericValue,
  byteCount,
  copyableInvitationUrl,
  dateMatches,
  grossMarginLabel,
  localDateTimeInput,
  makeOperationKey,
  managementSectionParam,
  mergePageRecords,
  normalizePageCollection,
  personalPointBalanceLabel,
  personalPointGrantEligibilityError,
  pricingQuantityLabel,
  readCapabilityEditor,
  reportApiFilters,
  sectionFromLocation,
} from "./components/management/managementConsoleSupport.js";
import { createCompanyManagementSectionRenderers } from "./components/management/legacy/CompanyManagementSections.jsx";
import { createPlatformBasicSectionRenderers } from "./components/management/legacy/PlatformBasicSections.jsx";
import { useManagementConsoleState } from "./components/management/legacy/useLegacyManagementState.js";
import { createCompanyManagementActions } from "./components/management/legacy/createCompanyManagementActions.js";
import { createPlatformBasicActions } from "./components/management/legacy/createPlatformBasicActions.js";
import { createManagementDrawerSubmitter } from "./components/management/legacy/createManagementDrawerSubmitter.js";
import {
  MANAGEMENT_PAGE_SIZE,
  emptySnapshot,
} from "./components/management/legacy/managementSnapshot.js";

const LazyAdminOperationsContainer = React.lazy(
  () => import("./admin/AdminOperationsContainer.jsx"),
);

const PERMISSIONS = ACTIVE_PERMISSION_CATALOG.map(
  ({ code, description }) => [code, description],
);

const COMPANY_NAV = [
  ["overview", "经营概览", Gauge],
  ["members", "成员与角色", Users],
  ["models", "模型与功能", FilmSlate],
  ["reports", "使用报表", ChartLineUp],
  ["wallet", "余额流水", Wallet],
];

const PLATFORM_NAV = [
  ["overview", "平台总览", Gauge],
  ["users", "账号生命周期", Users],
  ["companies", "企业管理", Buildings],
  ["reports", "消费报表", ChartLineUp],
  ["models", "模型目录", FilmSlate],
  ["resources", "功能资源", SlidersHorizontal],
  ["audit", "操作审计", ListBullets],
];

const PLATFORM_SECTION_PERMISSIONS = {
  overview: ["platform.analytics.read", "platform.companies.read", "platform.provider_costs.read"],
  companies: ["platform.companies.read"],
  reports: ["platform.finance.read"],
  models: ["platform.models.read"],
  resources: ["platform.resources.read"],
  audit: ["platform.audit.read"],
};

function canOpenPlatformSection(identity, section, demoMode = false) {
  if (demoMode || identity?.is_platform_owner) return true;
  if (!identity) return false;
  if (section === "users") return false;
  const permissions = new Set(identity.permission_codes || []);
  return (PLATFORM_SECTION_PERMISSIONS[section] || [])
    .every((permission) => permissions.has(permission));
}

const EMPTY_COMPANY_REPORT_FILTERS = {
  employee_user_id: "",
  model_id: "",
  status: "",
  start_time: "",
  end_time: "",
};

const EMPTY_ADMIN_REPORT_FILTERS = {
  company_id: "",
  employee_query: "",
  model_id: "",
  start_time: "",
  end_time: "",
};


function canOpenCompanySection(identity, section) {
  return canOpenCompanyConsoleSection(identity, section);
}

function Drawer({ title, detail, returnFocusElement, onClose, wide = false, children }) {
  const dialogRef = useRef(null);

  useEffect(() => {
    if (!dialogRef.current?.contains(document.activeElement)) {
      const preferred = dialogRef.current?.querySelector(
        '[autofocus], input:not(:disabled), select:not(:disabled), textarea:not(:disabled)',
      );
      (preferred || dialogRef.current)?.focus();
    }
    return () => {
      if (returnFocusElement?.isConnected) returnFocusElement.focus();
    };
  }, [returnFocusElement]);

  const handleKeyDown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = dialogRef.current?.querySelectorAll(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (document.activeElement === dialogRef.current) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="control-drawer-layer" role="presentation" onMouseDown={onClose}>
      <aside
        ref={dialogRef}
        className={wide ? "control-drawer is-wide" : "control-drawer"}
        role="dialog"
        aria-modal="true"
        aria-labelledby="control-drawer-title"
        aria-describedby={detail ? "control-drawer-detail" : undefined}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <header>
          <div>
            <h2 id="control-drawer-title">{title}</h2>
            {detail && <p id="control-drawer-detail">{detail}</p>}
          </div>
          <button className="control-drawer-close" data-icon-only="true" type="button" onClick={onClose} aria-label="关闭">
            <X size={20} />
          </button>
        </header>
        {children}
      </aside>
    </div>
  );
}

function downloadCsv(filename, text) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function ManagementConfigurationConsole({
  mode,
  client,
  demoMode: requestedDemoMode,
  demoIdentity,
  demoPersonaId,
  allowedSurfaces = [],
  onDemoPersonaChange,
  onSurfaceChange,
  onLogout,
  onSessionError,
  skin = "paper",
  onSkinChange,
  initialPlatformIdentity = null,
  initialPlatformSection = "overview",
  onOpenOperationsConsole,
  onOpenPersonalCreation,
  personalCreationPending = false,
  personalCreationErrorMessageId = "",
  companyContexts = [],
  activeCompanyId = "",
  onCompanyChange,
  managementDemoFixtures = null,
}) {
  const demoMode = !import.meta.env.PROD
    && requestedDemoMode
    && Boolean(managementDemoFixtures);
  const demoSnapshot = managementDemoFixtures?.demoSnapshot;
  const demoCompanyEntitlements = managementDemoFixtures?.demoCompanyEntitlements;
  const DEMO_COMPANIES = managementDemoFixtures?.DEMO_COMPANIES || [];
  const activeSkin = normalizeSkin(skin);
  const {
    section,
    setSection,
    data,
    setData,
    loading,
    setLoading,
    busy,
    setBusy,
    memberAccessLoading,
    setMemberAccessLoading,
    error,
    setError,
    toast,
    setToast,
    drawer,
    setDrawerState,
    drawerError,
    setDrawerError,
    entitlementBusyKey,
    setEntitlementBusyKey,
    paginationBusyKey,
    setPaginationBusyKey,
    accessInvalidated,
    setAccessInvalidated,
    ownerInvitationLinks,
    setOwnerInvitationLinks,
    compactManagementChrome,
    mobileDemoPersonaHost,
    setMobileDemoPersonaHost,
    desktopDemoPersonaHost,
    setDesktopDemoPersonaHost,
    reportFilters,
    setReportFilters,
    appliedReportFilters,
    setAppliedReportFilters,
    adminReportFilters,
    setAdminReportFilters,
    appliedAdminReportFilters,
    setAppliedAdminReportFilters,
    memberAccessRequestRef,
    companyControlRequestRef,
    personalPointGrantRequestRef,
    loadRequestGenerationRef,
    loadAbortControllerRef,
    controlMainRef,
    controlNavRef,
    activeNavItemRef,
  } = useManagementConsoleState({
    createInitialData: () => {
      const initial = demoMode ? demoSnapshot(demoIdentity) : emptySnapshot();
      if (mode === "platform" && initialPlatformIdentity) {
        initial.adminMe = initialPlatformIdentity;
      }
      return initial;
    },
    initialSection: sectionFromLocation(
      mode,
      mode === "platform" ? initialPlatformSection : "overview",
    ),
    initiallyLoading: !demoMode,
    mode,
    initialCompanyReportFilters: EMPTY_COMPANY_REPORT_FILTERS,
    initialAdminReportFilters: EMPTY_ADMIN_REPORT_FILTERS,
  });
  const companyPermissions = useMemo(
    () => new Set(data.me?.permission_codes || []),
    [data.me?.permission_codes],
  );
  const isCurrentOwner = Boolean(
    data.me?.roles?.some((role) => role.system_key === "owner"),
  );
  const canManageUsers = companyPermissions.has("users.manage");
  const canExportReports = companyPermissions.has("reports.export");
  const actionBusy = busy || memberAccessLoading;
  const platformIdentity = data.adminMe || initialPlatformIdentity;
  const platformPermissionSet = new Set(platformIdentity?.permission_codes || []);
  const canUsePlatformPermission = (permission) => (
    demoMode
    || platformIdentity?.is_platform_owner
    || platformPermissionSet.has(permission)
  );
  const nav = accessInvalidated
    ? []
    : mode === "platform"
      ? PLATFORM_NAV.filter(([id]) => canOpenPlatformSection(platformIdentity, id, demoMode))
      : COMPANY_NAV.filter(([id]) => canOpenCompanySection(data.me, id));
  const navKey = nav.map(([id]) => id).join("|");
  const activeCompanyContext = companyContexts.find(
    (company) => company.company_id === activeCompanyId,
  );

  useEffect(() => {
    const onHistoryNavigation = () => {
      const requested = sectionFromLocation(
        mode,
        mode === "platform" ? initialPlatformSection : "overview",
      );
      setSection(requested);
    };
    globalThis.addEventListener?.("popstate", onHistoryNavigation);
    return () => globalThis.removeEventListener?.("popstate", onHistoryNavigation);
  }, [initialPlatformSection, mode]);

  const setDrawer = useCallback((nextDrawer) => {
    setDrawerError("");
    setDrawerState((current) => {
      const resolved = typeof nextDrawer === "function" ? nextDrawer(current) : nextDrawer;
      if (!resolved) return null;
      return {
        ...resolved,
        returnFocusElement: resolved.returnFocusElement
          ?? globalThis.document?.activeElement
          ?? null,
      };
    });
  }, []);

  useEffect(() => {
    if (!demoMode) return;
    setData(demoSnapshot(demoIdentity));
    setAccessInvalidated(false);
  }, [demoMode, demoIdentity?.user_id]);

  useEffect(() => {
    setSection(sectionFromLocation(
      mode,
      mode === "platform" ? initialPlatformSection : "overview",
    ));
    setDrawer(null);
    setError("");
    setAccessInvalidated(false);
    setOwnerInvitationLinks({});
  }, [initialPlatformSection, mode, setDrawer]);

  useEffect(() => {
    if (!nav.length || nav.some(([id]) => id === section)) return;
    setSection(nav[0][0]);
  }, [mode, navKey, section]);

  useEffect(() => {
    const main = controlMainRef.current;
    if (!main) return;
    main.scrollTop = 0;
    main.scrollLeft = 0;
  }, [mode, section]);

  useEffect(() => {
    const navigation = controlNavRef.current;
    const activeItem = activeNavItemRef.current;
    if (!navigation || !activeItem) return undefined;
    const keepActiveItemVisible = () => {
      const scrollRoot = document.scrollingElement;
      const documentScroll = scrollRoot
        ? { left: scrollRoot.scrollLeft, top: scrollRoot.scrollTop }
        : null;
      activeItem.scrollIntoView({ block: "nearest", inline: "center" });
      if (scrollRoot && documentScroll) {
        scrollRoot.scrollLeft = documentScroll.left;
        scrollRoot.scrollTop = documentScroll.top;
      }
    };
    keepActiveItemVisible();
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(keepActiveItemVisible);
    resizeObserver?.observe(navigation);
    return () => resizeObserver?.disconnect();
  }, [mode, navKey, section]);

  const mergeData = useCallback((patch) => {
    setData((current) => ({ ...current, ...patch }));
  }, []);

  const invalidateSensitiveData = useCallback((message) => {
    setData(emptySnapshot());
    setDrawer(null);
    setOwnerInvitationLinks({});
    setAccessInvalidated(true);
    setError(message);
  }, [setDrawer]);

  const load = useCallback(
    async (requestedSection = section, filters) => {
      loadAbortControllerRef.current?.abort();
      const requestGeneration = ++loadRequestGenerationRef.current;
      if (demoMode) {
        loadAbortControllerRef.current = null;
        setLoading(false);
        return;
      }
      const controller = new AbortController();
      const { signal } = controller;
      loadAbortControllerRef.current = controller;
      const isCurrentRequest = () => (
        !signal.aborted && requestGeneration === loadRequestGenerationRef.current
      );
      const commitData = (patch) => {
        if (isCurrentRequest()) mergeData(patch);
      };
      const optionalCollection = (promise) => promise.catch((optionalError) => {
        if (signal.aborted) throw optionalError;
        return [];
      });
      const selectedFilters = filters ?? (
        mode === "platform" ? appliedAdminReportFilters : appliedReportFilters
      );
      const apiFilters = reportApiFilters(selectedFilters);
      setLoading(true);
      setError("");
      if (mode === "platform" && requestedSection === "overview") {
        commitData({ dashboard: null, channelCosts: null });
      } else if (mode === "platform" && requestedSection === "reports") {
        commitData({ adminConsumption: null });
      } else if (mode === "company" && requestedSection === "overview") {
        commitData({ wallet: null, ledger: null, taskReport: null, succeededTaskReport: null });
      } else if (mode === "company" && requestedSection === "reports") {
        commitData({ taskReport: null, consumption: null, downloads: null });
      } else if (mode === "company" && requestedSection === "wallet") {
        commitData({ wallet: null, ledger: null, recharges: null });
      }
      try {
        if (mode === "company") {
          let identity = data.me;
          if (!identity) {
            identity = await client.getCompanyMe({ signal });
            if (!isCurrentRequest()) return;
            commitData({ me: identity });
          }
          if (!canOpenCompanySection(identity, requestedSection)) {
            const nextSection = COMPANY_NAV.find(([id]) => (
              canOpenCompanySection(identity, id)
            ))?.[0];
            if (nextSection && nextSection !== requestedSection) {
              if (isCurrentRequest()) setSection(nextSection);
              return;
            }
            if (isCurrentRequest()) {
              setError("当前账号没有公司管理页面权限，请返回制作台。");
            }
            return;
          }
          if (requestedSection === "overview") {
            const [wallet, ledger, taskReport, succeededTaskReport] = await Promise.all([
              client.listWallet({ signal }),
              client.listLedger({ signal }),
              client.getTaskReport({ page_size: 8 }, { signal }),
              client.getTaskReport({ page_size: 1, status: "succeeded" }, { signal }),
            ]);
            commitData({ wallet, ledger, taskReport, succeededTaskReport });
          } else if (requestedSection === "members") {
            const identityPermissions = new Set(identity?.permission_codes || []);
            const [members, invitations, roles, permissions] = await Promise.all([
              client.listMembers({ signal }),
              identityPermissions.has("users.manage")
                ? client.listInvitations(
                    { page: 1, page_size: MANAGEMENT_PAGE_SIZE },
                    { signal },
                  )
                : Promise.resolve({ page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, items: [] }),
              client.listRoles({ signal }),
              client.listPermissionCatalog({ signal }),
            ]);
            commitData({
              members,
              invitations: normalizePageCollection(invitations),
              roles,
              permissions,
            });
          } else if (requestedSection === "models") {
            const permissions = new Set(identity?.permission_codes || []);
            const canReadModels = permissions.has("models.read");
            const canReadResources = permissions.has("resources.read");
            const [models, grants, resources] = await Promise.all([
              canReadModels ? client.listModels({ signal }) : Promise.resolve([]),
              canReadModels ? client.listModelGrants({ signal }) : Promise.resolve([]),
              canReadResources ? client.listResources({ signal }) : Promise.resolve([]),
            ]);
            commitData({ models, grants, resources });
          } else if (requestedSection === "reports") {
            const permissions = new Set(identity?.permission_codes || []);
            const canReadUsers = permissions.has("users.read");
            const canReadModels = permissions.has("models.read");
            const downloadFilters = {
              page: 1,
              page_size: MANAGEMENT_PAGE_SIZE,
              scope: "company",
              employee_user_id: apiFilters.employee_user_id,
              start_time: apiFilters.start_time,
              end_time: apiFilters.end_time,
            };
            const [taskReport, consumption, downloads, members, models] = await Promise.all([
              client.getTaskReport({ page: 1, page_size: MANAGEMENT_PAGE_SIZE, ...apiFilters }, { signal }),
              client.getConsumptionReport({ page: 1, page_size: MANAGEMENT_PAGE_SIZE, ...apiFilters }, { signal }),
              client.listDownloadRecords(downloadFilters, { signal }),
              canReadUsers
                ? optionalCollection(client.listMembers({ signal }))
                : Promise.resolve([]),
              canReadModels
                ? optionalCollection(client.listModels({ signal }))
                : Promise.resolve([]),
            ]);
            commitData({ taskReport, consumption, downloads, members, models });
          } else if (requestedSection === "wallet") {
            const [wallet, ledger, recharges] = await Promise.all([
              client.listWallet({ signal }),
              client.listLedger({ signal }),
              client.listRecharges({ page: 1, page_size: MANAGEMENT_PAGE_SIZE }, { signal }),
            ]);
            commitData({ wallet, ledger, recharges });
          }
        } else {
          let identity = data.adminMe || initialPlatformIdentity;
          if (accessInvalidated) identity = null;
          if (!identity) {
            identity = await client.getPlatformAdminMe({ signal });
            if (!isCurrentRequest()) return;
            commitData({ adminMe: identity });
          }
          if (!canOpenPlatformSection(identity, requestedSection, demoMode)) {
            const nextSection = PLATFORM_NAV.find(([id]) => (
              canOpenPlatformSection(identity, id, demoMode)
            ))?.[0];
            if (nextSection && nextSection !== requestedSection) {
              if (isCurrentRequest()) setSection(nextSection);
              return;
            }
            if (isCurrentRequest()) {
              setError("当前平台管理员没有基础配置模块权限，请联系平台所有者。");
            }
            return;
          }
          if (requestedSection === "overview") {
            const [dashboard, companies, channelCosts] = await Promise.all([
              client.getPlatformDashboard({ page_size: 50 }, { signal }),
              client.listAdminCompanies({ page: 1, page_size: MANAGEMENT_PAGE_SIZE }, { signal }),
              client.listAdminChannelCosts({ page: 1, page_size: MANAGEMENT_PAGE_SIZE }, { signal }),
            ]);
            commitData({ adminMe: identity, dashboard, companies, channelCosts });
          } else if (requestedSection === "users") {
            commitData({
              adminUsers: normalizePageCollection(await client.listPlatformUsers(
                { page: 1, page_size: MANAGEMENT_PAGE_SIZE },
                { signal },
              )),
            });
          } else if (requestedSection === "companies") {
            const canReadAnalytics = demoMode
              || identity?.is_platform_owner
              || new Set(identity?.permission_codes || []).has("platform.analytics.read");
            const [companies, dashboard] = await Promise.all([
              client.listAdminCompanies({ page: 1, page_size: MANAGEMENT_PAGE_SIZE }, { signal }),
              canReadAnalytics
                ? client.getPlatformDashboard({ page: 1, page_size: MANAGEMENT_PAGE_SIZE }, { signal })
                : Promise.resolve(null),
            ]);
            commitData({ companies, ...(dashboard ? { dashboard } : {}) });
          } else if (requestedSection === "models") {
            const readRelayModelAudit = () => client.listAdminRelayModels({ signal }).catch((relayError) => {
              if (signal.aborted) throw relayError;
              return {
                catalog_revision: "",
                items: [],
                platform_only_model_ids: [],
                error: relayError?.message || "中转站模型目录暂时不可用",
                error_status: Number(relayError?.status || 0),
              };
            });
            const [adminModels, relayModelAudit, personalModelGrants] = await Promise.all([
              client.listAdminModels({ signal }),
              readRelayModelAudit(),
              typeof client.listAdminPersonalModelGrants === "function"
                ? client.listAdminPersonalModelGrants({ signal })
                  .then((items) => ({ items: Array.isArray(items) ? items : [] }))
                  .catch((grantError) => {
                    if (signal.aborted) throw grantError;
                    return {
                      items: [],
                      error: grantError?.message || "个人零售分发目录暂时不可用",
                      error_status: Number(grantError?.status || 0),
                    };
                  })
                : Promise.resolve({ items: [], error: "当前前端客户端未提供个人零售分发接口", error_status: 0 }),
            ]);
            commitData({ adminModels, relayModelAudit, personalModelGrants });
          } else if (requestedSection === "resources") {
            commitData({ adminResources: await client.listAdminResources({ signal }) });
          } else if (requestedSection === "reports") {
            const permissionCodes = new Set(identity?.permission_codes || []);
            const canReadCompanies = demoMode || identity?.is_platform_owner || permissionCodes.has("platform.companies.read");
            const canReadModels = demoMode || identity?.is_platform_owner || permissionCodes.has("platform.models.read");
            const [companies, adminModels, adminConsumption] = await Promise.all([
              canReadCompanies
                ? client.listAdminCompanies({ page: 1, page_size: MANAGEMENT_PAGE_SIZE }, { signal })
                : Promise.resolve({ page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, items: [] }),
              canReadModels ? client.listAdminModels({ signal }) : Promise.resolve([]),
              client.getAdminConsumptionReport({ page: 1, page_size: MANAGEMENT_PAGE_SIZE, ...apiFilters }, { signal }),
            ]);
            commitData({ companies, adminModels, adminConsumption });
          } else if (requestedSection === "audit") {
            commitData({
              audit: await client.listAdminAuditLogs({ page_size: 100 }, { signal }),
            });
          }
        }
        if (isCurrentRequest()) setAccessInvalidated(false);
      } catch (loadError) {
        if (!isCurrentRequest() || loadError?.name === "AbortError") return;
        if (onSessionError?.(loadError)) {
          return;
        } else if (loadError?.status === 401) {
          invalidateSensitiveData("登录状态已失效，请通过正式身份系统重新登录。");
        } else if (loadError?.status === 403) {
          invalidateSensitiveData("权限已变化，现有管理数据已清除。请重新核验身份后再试。");
        } else {
          if (mode === "platform" && requestedSection === "overview") {
            commitData({ dashboard: null, channelCosts: null });
          } else if (mode === "platform" && requestedSection === "reports") {
            commitData({ adminConsumption: null });
          } else if (mode === "company" && requestedSection === "overview") {
            commitData({ wallet: null, ledger: null, taskReport: null, succeededTaskReport: null });
          } else if (mode === "company" && requestedSection === "reports") {
            commitData({ taskReport: null, consumption: null, downloads: null });
          } else if (mode === "company" && requestedSection === "wallet") {
            commitData({ wallet: null, ledger: null, recharges: null });
          }
          setError(loadError?.message || "数据加载失败，请稍后重试。");
        }
      } finally {
        if (isCurrentRequest()) {
          setLoading(false);
          if (loadAbortControllerRef.current === controller) {
            loadAbortControllerRef.current = null;
          }
        }
      }
    }, [
      appliedAdminReportFilters,
      appliedReportFilters,
      accessInvalidated,
      client,
      data.adminMe,
      data.me,
      demoMode,
      initialPlatformIdentity,
      invalidateSensitiveData,
      mergeData,
      mode,
      onSessionError,
      section,
    ],
  );

  useEffect(() => {
    load(section);
    return () => {
      loadRequestGenerationRef.current += 1;
      loadAbortControllerRef.current?.abort();
      loadAbortControllerRef.current = null;
    };
  }, [section, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const loadMore = async (kind) => {
    if (demoMode || paginationBusyKey) return;
    const reportFiltersForApi = reportApiFilters(appliedReportFilters);
    const adminFiltersForApi = reportApiFilters(appliedAdminReportFilters);
    let request;
    let mergeResult;

    if (kind === "company-tasks") {
      const nextPage = Number(data.taskReport?.page || 1) + 1;
      request = client.getTaskReport({ page: nextPage, page_size: MANAGEMENT_PAGE_SIZE, ...reportFiltersForApi });
      mergeResult = (result) => setData((current) => ({
        ...current,
        taskReport: mergePageRecords(current.taskReport, result, "task_id"),
      }));
    } else if (kind === "company-consumption") {
      const nextPage = Number(data.consumption?.page || 1) + 1;
      request = client.getConsumptionReport({ page: nextPage, page_size: MANAGEMENT_PAGE_SIZE, ...reportFiltersForApi });
      mergeResult = (result) => setData((current) => ({
        ...current,
        consumption: mergePageRecords(current.consumption, result, "ledger_entry_id"),
      }));
    } else if (kind === "company-downloads") {
      const nextPage = Number(data.downloads?.page || 1) + 1;
      request = client.listDownloadRecords({
        page: nextPage,
        page_size: MANAGEMENT_PAGE_SIZE,
        scope: "company",
        employee_user_id: reportFiltersForApi.employee_user_id,
        start_time: reportFiltersForApi.start_time,
        end_time: reportFiltersForApi.end_time,
      });
      mergeResult = (result) => setData((current) => ({
        ...current,
        downloads: mergePageRecords(current.downloads, result, "id"),
      }));
    } else if (kind === "company-recharges") {
      const nextPage = Number(data.recharges?.page || 1) + 1;
      request = client.listRecharges({ page: nextPage, page_size: MANAGEMENT_PAGE_SIZE });
      mergeResult = (result) => setData((current) => ({
        ...current,
        recharges: mergePageRecords(current.recharges, result, "id"),
      }));
    } else if (kind === "company-invitations") {
      const nextPage = Number(data.invitations?.page || 1) + 1;
      request = client.listInvitations({
        page: nextPage,
        page_size: data.invitations?.page_size || MANAGEMENT_PAGE_SIZE,
      });
      mergeResult = (result) => setData((current) => ({
        ...current,
        invitations: mergePageRecords(
          current.invitations,
          normalizePageCollection(result),
          "id",
        ),
      }));
    } else if (kind === "platform-companies") {
      const nextPage = Number(data.companies?.page || 1) + 1;
      request = client.listAdminCompanies({ page: nextPage, page_size: MANAGEMENT_PAGE_SIZE });
      mergeResult = (result) => setData((current) => ({
        ...current,
        companies: mergePageRecords(current.companies, result, "id"),
      }));
    } else if (kind === "platform-users") {
      const nextPage = Number(data.adminUsers?.page || 1) + 1;
      request = client.listPlatformUsers({
        page: nextPage,
        page_size: data.adminUsers?.page_size || MANAGEMENT_PAGE_SIZE,
      });
      mergeResult = (result) => setData((current) => ({
        ...current,
        adminUsers: mergePageRecords(
          current.adminUsers,
          normalizePageCollection(result),
          "id",
        ),
      }));
    } else if (kind === "platform-dashboard") {
      const nextPage = Number(data.dashboard?.page || 1) + 1;
      request = client.getPlatformDashboard({ page: nextPage, page_size: MANAGEMENT_PAGE_SIZE });
      mergeResult = (result) => setData((current) => {
        const companies = new Map((current.dashboard?.companies || []).map((item) => [item.company_id, item]));
        (result.companies || []).forEach((item) => companies.set(item.company_id, item));
        return {
          ...current,
          dashboard: { ...current.dashboard, ...result, companies: [...companies.values()] },
        };
      });
    } else if (kind === "platform-costs") {
      const nextPage = Number(data.channelCosts?.page || 1) + 1;
      request = client.listAdminChannelCosts({ page: nextPage, page_size: MANAGEMENT_PAGE_SIZE });
      mergeResult = (result) => setData((current) => ({
        ...current,
        channelCosts: mergePageRecords(current.channelCosts, result, "id"),
      }));
    } else if (kind === "platform-consumption") {
      const nextPage = Number(data.adminConsumption?.page || 1) + 1;
      request = client.getAdminConsumptionReport({ page: nextPage, page_size: MANAGEMENT_PAGE_SIZE, ...adminFiltersForApi });
      mergeResult = (result) => setData((current) => ({
        ...current,
        adminConsumption: mergePageRecords(current.adminConsumption, result, "ledger_entry_id"),
      }));
    } else if (kind === "platform-audit") {
      const nextPage = Number(data.audit?.page || 1) + 1;
      request = client.listAdminAuditLogs({ page: nextPage, page_size: 100 });
      mergeResult = (result) => setData((current) => ({
        ...current,
        audit: mergePageRecords(current.audit, result, "id"),
      }));
    } else {
      return;
    }

    setPaginationBusyKey(kind);
    setError("");
    try {
      mergeResult(await request);
    } catch (pageError) {
      if (pageError?.status === 401) {
        invalidateSensitiveData("登录状态已失效，请通过正式身份系统重新登录。");
      } else if (pageError?.status === 403) {
        invalidateSensitiveData("权限已变化，现有管理数据已清除。请重新核验身份后再试。");
      } else {
        setError(pageError?.message || "下一页加载失败，请稍后重试。");
      }
    } finally {
      setPaginationBusyKey("");
    }
  };

  const mutate = async (work, demoWork, successMessage) => {
    setBusy(true);
    setError("");
    setDrawerError("");
    try {
      if (demoMode) {
        if (typeof demoWork !== "function") {
          throw new Error("演示模式不会写入服务器，也没有可预览的本地变更。");
        }
        demoWork();
      } else {
        await work();
        await load(section);
      }
      setDrawer(null);
      setToast(demoMode ? `演示预览：${successMessage}（未写入服务器）` : successMessage);
    } catch (mutationError) {
      const message = mutationError?.message || "操作失败，请稍后重试。";
      if (onSessionError?.(mutationError)) {
        return;
      } else if (drawer?.type === "ownerTransfer" && mutationError?.status === 409 && !demoMode) {
        setDrawerError("老板成员快照已变化，正在载入最新成员与当前身份…");
        try {
          const [me, members, roles] = await Promise.all([
            client.getCompanyMe(),
            client.listMembers(),
            client.listRoles(),
          ]);
          mergeData({ me, members, roles });
          setDrawerError("老板职责已在其他会话中变化，已载入最新状态；请关闭窗口并按当前身份重新操作。");
        } catch (refreshError) {
          setDrawerError(`${message}；读取最新成员状态失败：${refreshError?.message || "请稍后重试"}`);
        }
      } else if (drawer?.type === "roles" && mutationError?.status === 409 && !demoMode) {
        setDrawerError("配置已被其他会话更新，正在载入最新权限…");
        try {
          const [members, roles, permissions] = await Promise.all([
            client.listMembers(),
            client.listRoles(),
            client.listPermissionCatalog(),
          ]);
          const freshMember = members.find(
            (item) => item.membership_id === drawer.member.membership_id,
          );
          if (!freshMember) throw new Error("成员已不存在，请关闭窗口后刷新。");
          const hydratedMember = withMemberPermissionState(freshMember, roles);
          mergeData({ members, roles, permissions });
          setDrawerState((current) => (
            current?.type === "roles"
              && current.member?.membership_id === hydratedMember.membership_id
              ? { ...current, member: hydratedMember }
              : current
          ));
          setDrawerError("配置已被其他会话更新，已载入最新值，请重新确认。");
        } catch (refreshError) {
          setDrawerError(`${message}；读取最新权限失败：${refreshError?.message || "请稍后重试"}`);
        }
      } else if (mode === "platform" && section === "users" && mutationError?.status === 409 && !demoMode) {
        await load("users");
        setError("账号状态已在其他会话中变化，已刷新全局账号列表；请确认最新状态后再操作。");
      } else if (drawer) {
        setDrawerError(message);
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  };

  const {
    openMemberAccess,
    openRoleEditor,
    setMemberStatus,
    createCompanyInvitation,
    copyInvitationLink,
    reissueCompanyInvitation,
    revokeCompanyInvitation,
    deleteCompanyRole,
  } = createCompanyManagementActions({
    runtime: {
      client,
      data,
      demoMode,
      onSessionError,
    },
    requests: {
      memberAccessRequestRef,
    },
    state: {
      setBusy,
      setData,
      setDrawer,
      setDrawerError,
      setError,
      setMemberAccessLoading,
      setToast,
    },
    orchestration: {
      mergeData,
      mutate,
    },
    helpers: {
      copyableInvitationUrl,
      requirePermissionCatalog,
      roleList,
      withMemberPermissionState,
    },
  });
  const {
    createPlatformCompany,
    copyOwnerInvitationLink,
    reissueOwnerInvitation,
    refreshPersonalPointGrantHistory,
    openPersonalPointGrant,
    startNextPersonalPointGrant,
    submitPersonalPointGrant,
    setGlobalUserStatus,
    setCompanyStatus,
    companyDashboardRow,
    openCompanyControl,
    loadMoreCompanyControl,
    updateEntitlementRow,
    saveCompanyModelEntitlement,
    saveCompanyResourceEntitlement,
    setModelState,
    approveRelayModelRevision,
    syncRelayModelCandidate,
    reconcileRelayModels,
    loadRelayCapabilityHistory,
    savePersonalModelGrant,
    previewPersonalModelGrantBatch,
    savePersonalModelGrantBatch,
  } = createPlatformBasicActions({
    runtime: {
      client,
      data,
      demoMode,
      drawer,
      onSessionError,
      platformIdentity,
    },
    requests: {
      companyControlRequestRef,
      personalPointGrantRequestRef,
    },
    state: {
      setBusy,
      setData,
      setDrawer,
      setDrawerError,
      setDrawerState,
      setEntitlementBusyKey,
      setError,
      setOwnerInvitationLinks,
      setPaginationBusyKey,
      setToast,
    },
    orchestration: {
      invalidateSensitiveData,
      load,
      mergeData,
      mutate,
    },
    helpers: {
      canUsePlatformPermission,
      copyableInvitationUrl,
      makeOperationKey,
      mergePageRecords,
      personalPointBalanceLabel,
      personalPointGrantEligibilityError,
    },
    catalog: {
      DEMO_COMPANIES,
      MANAGEMENT_PAGE_SIZE,
      demoCompanyEntitlements,
      ownerInvitationLinks,
      paginationBusyKey,
    },
  });

  const submitDrawer = createManagementDrawerSubmitter({
    runtime: {
      client,
      data,
      demoMode,
      drawer,
    },
    actions: {
      createCompanyInvitation,
      createPlatformCompany,
      reissueOwnerInvitation,
      submitPersonalPointGrant,
    },
    orchestration: {
      mergeData,
      mutate,
    },
    state: {
      setDrawerError,
    },
    helpers: {
      billingPresentationState,
      money,
      permissionOverrideMap,
      readCapabilityEditor,
      requirePermissionCatalog,
      roleList,
      withMemberPermissionState,
    },
  });

  const filteredDemoTasks = useMemo(() => {
    const tasks = data.taskReport?.items || [];
    if (!demoMode) return tasks;
    return tasks.filter(
      (task) =>
        (!appliedReportFilters.status || task.status === appliedReportFilters.status) &&
        (!appliedReportFilters.employee_user_id || task.employee_user_id === appliedReportFilters.employee_user_id) &&
        (!appliedReportFilters.model_id || task.model_id === appliedReportFilters.model_id) &&
        dateMatches(task.created_at, appliedReportFilters.start_time, appliedReportFilters.end_time),
    );
  }, [appliedReportFilters, data.taskReport, demoMode]);

  const filteredDemoConsumption = useMemo(() => {
    const items = data.consumption?.items || [];
    if (!demoMode) return items;
    return items.filter(
      (item) =>
        (!appliedReportFilters.status || item.task_status === appliedReportFilters.status) &&
        (!appliedReportFilters.employee_user_id || item.employee_user_id === appliedReportFilters.employee_user_id) &&
        (!appliedReportFilters.model_id || item.model_id === appliedReportFilters.model_id) &&
        dateMatches(item.consumed_at, appliedReportFilters.start_time, appliedReportFilters.end_time),
    );
  }, [appliedReportFilters, data.consumption, demoMode]);

  const filteredDemoAdminConsumption = useMemo(() => {
    const items = data.adminConsumption?.items || [];
    if (!demoMode) return items;
    const employeeQuery = appliedAdminReportFilters.employee_query.trim().toLocaleLowerCase("zh-CN");
    return items.filter((item) => {
      const employee = `${item.employee_display_name || ""} ${item.employee_email || ""}`
        .toLocaleLowerCase("zh-CN");
      return (
        (!appliedAdminReportFilters.company_id || item.company_id === appliedAdminReportFilters.company_id) &&
        (!employeeQuery || employee.includes(employeeQuery)) &&
        (!appliedAdminReportFilters.model_id || item.model_id === appliedAdminReportFilters.model_id) &&
        dateMatches(item.consumed_at, appliedAdminReportFilters.start_time, appliedAdminReportFilters.end_time)
      );
    });
  }, [appliedAdminReportFilters, data.adminConsumption, demoMode]);

  const companyReportModels = useMemo(() => {
    const models = new Map();
    data.models.forEach((model) => models.set(model.id, model.display_name));
    (data.taskReport?.items || []).forEach((task) => {
      if (task.model_id) models.set(task.model_id, task.model_display_name || safeId(task.model_id));
    });
    (data.consumption?.items || []).forEach((item) => {
      if (item.model_id) models.set(item.model_id, item.model_display_name || safeId(item.model_id));
    });
    return [...models].map(([id, display_name]) => ({ id, display_name }));
  }, [data.consumption, data.models, data.taskReport]);

  const companyReportMembers = useMemo(() => {
    const members = new Map();
    data.members.forEach((member) => members.set(member.user_id, member.display_name));
    (data.taskReport?.items || []).forEach((task) => {
      if (task.employee_user_id) members.set(
        task.employee_user_id,
        task.employee_display_name || safeId(task.employee_user_id),
      );
    });
    (data.consumption?.items || []).forEach((item) => {
      if (item.employee_user_id) members.set(
        item.employee_user_id,
        item.employee_display_name || safeId(item.employee_user_id),
      );
    });
    return [...members].map(([id, display_name]) => ({ id, display_name }));
  }, [data.consumption, data.members, data.taskReport]);

  const exportReport = async (kind) => {
    if (!canExportReports) return;
    setBusy(true);
    setError("");
    try {
      let csv;
      if (demoMode) {
        csv = kind === "tasks"
          ? `任务ID,员工,模型,状态,计费单位,计费版本,实际消费积分,历史人民币分\n${filteredDemoTasks
              .map((task) => `${task.task_id},${task.employee_display_name},${task.model_display_name},${task.status},${task.billing_unit || ""},${task.billing_version || ""},${task.actual_cost_points ?? ""},${task.actual_cost_cents ?? ""}`)
              .join("\n")}`
          : `流水ID,任务ID,员工,模型,计费单位,计费版本,消费积分,历史人民币分\n${filteredDemoConsumption
              .map((item) => `${item.ledger_entry_id},${item.task_id},${item.employee_display_name},${item.model_display_name},${item.billing_unit || ""},${item.billing_version || ""},${item.amount_points ?? ""},${item.amount_cents ?? ""}`)
              .join("\n")}`;
      } else {
        const filters = reportApiFilters(appliedReportFilters);
        csv = kind === "tasks"
          ? await client.exportTaskReport(filters)
          : await client.exportConsumptionReport(filters);
      }
      downloadCsv(kind === "tasks" ? "task-report.csv" : "consumption-report.csv", csv);
      setToast("报表已导出");
    } catch (exportError) {
      setError(exportError?.message || "报表导出失败");
    } finally {
      setBusy(false);
    }
  };

  const exportAdminReport = async () => {
    setBusy(true);
    setError("");
    try {
      const csv = demoMode
        ? `企业,员工,邮箱,模型,计费方式,数量,计费单位,计费版本,消费积分,历史人民币分,时间\n${filteredDemoAdminConsumption
            .map((item) => `${item.company_name},${item.employee_display_name},${item.employee_email},${item.model_display_name},${pricingModeLabel(item.pricing_mode)},${item.quantity ?? ""},${item.billing_unit || ""},${item.billing_version || ""},${item.amount_points ?? ""},${item.amount_cents ?? ""},${item.consumed_at}`)
            .join("\n")}`
        : await client.exportAdminConsumptionReport(
            reportApiFilters(appliedAdminReportFilters),
          );
      downloadCsv("platform-consumption-report.csv", csv);
      setToast("平台消费报表已导出");
    } catch (exportError) {
      setError(exportError?.message || "平台消费报表导出失败");
    } finally {
      setBusy(false);
    }
  };

  const companySectionRenderers = createCompanyManagementSectionRenderers({
    state: {
      data,
      error,
      loading,
      busy,
      demoMode,
      paginationBusyKey,
    },
    access: {
      companyPermissions,
      canManageUsers,
      canExportReports,
      isCurrentOwner,
      actionBusy,
    },
    actions: {
      load,
      loadMore,
      setSection,
      setDrawer,
      openMemberAccess,
      openRoleEditor,
      setMemberStatus,
      copyInvitationLink,
      reissueCompanyInvitation,
      revokeCompanyInvitation,
      deleteCompanyRole,
      exportReport,
    },
    reporting: {
      reportFilters,
      setReportFilters,
      setAppliedReportFilters,
      appliedReportFilters,
      companyReportMembers,
      companyReportModels,
      filteredDemoTasks,
      filteredDemoConsumption,
      EMPTY_COMPANY_REPORT_FILTERS,
    },
    formatters: {
      byteCount,
      makeOperationKey,
      pricingQuantityLabel,
      billingNumericValue,
    },
  });
  const platformSectionRenderers = createPlatformBasicSectionRenderers({
    state: {
      data,
      error,
      loading,
      busy,
      demoMode,
      paginationBusyKey,
    },
    access: {
      platformIdentity,
      actionBusy,
      canUsePlatformPermission,
    },
    actions: {
      load,
      loadMore,
      setDrawer,
      setGlobalUserStatus,
      openPersonalPointGrant,
      setCompanyStatus,
      openCompanyControl,
      copyOwnerInvitationLink,
      setModelState,
      approveRelayModelRevision,
      syncRelayModelCandidate,
      reconcileRelayModels,
      loadRelayCapabilityHistory,
      savePersonalModelGrant,
      previewPersonalModelGrantBatch,
      savePersonalModelGrantBatch,
    },
    reporting: {
      adminReportFilters,
      setAdminReportFilters,
      setAppliedAdminReportFilters,
      filteredDemoAdminConsumption,
      exportAdminReport,
      EMPTY_ADMIN_REPORT_FILTERS,
    },
    presentation: {
      ownerInvitationLinks,
      companyDashboardRow,
      makeOperationKey,
      personalPointBalanceLabel,
      personalPointGrantEligibilityError,
      grossMarginLabel,
      pricingQuantityLabel,
    },
  });

  const identity = data.adminMe || data.me;
  const sectionIsAccessible = nav.some(([id]) => id === section);
  useEffect(() => {
    if (!sectionIsAccessible) return;
    try {
      const url = new URL(globalThis.location?.href || "http://localhost/");
      const param = managementSectionParam(mode);
      if (url.searchParams.get(param) === section) return;
      url.searchParams.set(param, section);
      globalThis.history?.replaceState?.({}, "", `${url.pathname}${url.search}${url.hash}`);
    } catch {
      // URL persistence is progressive enhancement; in-memory routing still works.
    }
  }, [mode, section, sectionIsAccessible]);
  const isResolvingIdentity = !demoMode && !identity && loading;
  const sectionRenderers = mode === "company"
    ? companySectionRenderers
    : platformSectionRenderers;
  const content = !nav.length
    ? <ManagementAccessState mode={mode} pending={isResolvingIdentity} />
    : sectionIsAccessible
      ? sectionRenderers[section]?.()
      : <ManagementAccessState mode={mode} pending />;
  const preferredCreationSurface = preferredCreationSurfaceForManagement(
    allowedSurfaces,
    mode,
  );
  const canReturnToCreation = Boolean(preferredCreationSurface);
  const canOpenPersonalCreation = mode === "platform"
    && platformIdentity?.is_platform_owner === true
    && Boolean(onOpenPersonalCreation);
  const preferredCreationLabel = "创作";
  const activeSectionLabel = nav.find(([id]) => id === section)?.[1] || "公司管理";
  const managementScopeName = mode === "platform"
    ? "平台基础配置"
    : demoMode
      ? "远创电商"
      : (activeCompanyContext?.name || "公司控制台");
  const activeDemoPersonaHost = compactManagementChrome
    ? mobileDemoPersonaHost
    : desktopDemoPersonaHost;
  const demoPersonaControl = demoMode && activeDemoPersonaHost
    ? createPortal(
      <DemoAccountSwitcher value={demoPersonaId} onChange={onDemoPersonaChange} />,
      activeDemoPersonaHost,
    )
    : null;

  useEffect(() => {
    const previousTitle = globalThis.document?.title || BRAND_NAME;
    if (globalThis.document) {
      globalThis.document.title = `${activeSectionLabel} · ${mode === "platform" ? "平台基础配置" : "公司管理"} · ${BRAND_NAME}`;
    }
    return () => {
      if (globalThis.document) globalThis.document.title = previousTitle;
    };
  }, [activeSectionLabel, mode]);

  return (
    <div
      className={`control-shell is-${mode}-management`}
      data-theme={activeSkin}
      data-management-mode={mode}
      data-section={sectionIsAccessible ? section : "access"}
    >
      <header className="control-topbar">
        {(
          <div
            className="control-mobile-commandbar"
            data-personal-creation-entry={canOpenPersonalCreation ? "true" : undefined}
          >
            {canReturnToCreation ? (
              <button className="control-mobile-home" type="button" onClick={() => onSurfaceChange(preferredCreationSurface)} aria-label={`返回${preferredCreationLabel}`}>
                <BrandLogo variant="symbol" />
              </button>
            ) : (
              <span className="control-mobile-home is-static" aria-label={BRAND_NAME}>
                <BrandLogo variant="symbol" />
              </span>
            )}
            <div className="control-mobile-heading">
              <small>{mode === "platform" ? "平台基础配置" : (demoMode ? "远创电商" : (activeCompanyContext?.name || "公司控制台"))}</small>
              <strong>{activeSectionLabel}</strong>
            </div>
            <OperationsWorkspaceActions
              compact
              canOpenPersonalCreation={canOpenPersonalCreation}
              onOpenPersonalCreation={onOpenPersonalCreation}
              pending={personalCreationPending}
              errorMessageId={personalCreationErrorMessageId}
            />
            <details
              className="control-mobile-command-menu"
              onKeyDown={(event) => {
                const summary = event.currentTarget.querySelector("summary");
                if (event.target === summary && (event.key === "Enter" || event.key === " ")) {
                  event.preventDefault();
                  event.currentTarget.open = !event.currentTarget.open;
                  return;
                }
                if (event.key === "Tab" && event.currentTarget.open && event.target === summary && !event.shiftKey) {
                  const firstControl = event.currentTarget.querySelector("button, select, input, textarea");
                  if (firstControl) {
                    event.preventDefault();
                    firstControl.focus();
                  }
                  return;
                }
                if (event.key !== "Escape") return;
                event.preventDefault();
                event.currentTarget.open = false;
                summary?.focus();
              }}
            >
              <summary aria-label="打开工作区、皮肤与账号菜单"><SlidersHorizontal size={20} aria-hidden="true" /><span>菜单</span></summary>
              <div className="control-mobile-command-panel">
                {mode === "company" && canReturnToCreation && (
                  <section>
                    <span>导航</span>
                    <div className="surface-switch" aria-label="工作台导航">
                      <button type="button" aria-pressed="false" onClick={() => onSurfaceChange(preferredCreationSurface)}>创作</button>
                      <button className="is-active" type="button" aria-pressed="true">管理</button>
                    </div>
                  </section>
                )}
                {mode === "platform" && onOpenOperationsConsole ? (
                  <section>
                    <span>控制台</span>
                    <button className="control-platform-view-switch" type="button" onClick={onOpenOperationsConsole}>
                      <Gauge size={16} />返回运营指挥台
                    </button>
                  </section>
                ) : null}
                {mode === "company" && !demoMode && companyContexts.length > 1 && (
                  <section className="control-mobile-company-context">
                    <span>当前企业</span>
                    <label className="control-company-context-switcher">
                      <span className="visually-hidden">切换当前企业</span>
                      <select
                        value={activeCompanyId || activeCompanyContext?.company_id || companyContexts[0]?.company_id || ""}
                        disabled={!onCompanyChange}
                        onChange={(event) => onCompanyChange?.(event.target.value)}
                      >
                        {companyContexts.map((company) => (
                          <option key={company.company_id} value={company.company_id}>
                            {company.name || company.company_id}
                          </option>
                        ))}
                      </select>
                    </label>
                  </section>
                )}
                <section><span>界面</span><SkinSwitcher value={activeSkin} onChange={onSkinChange} /></section>
                <section className="control-mobile-account">
                  <span>账号</span>
                  {demoMode ? (
                    <div ref={setMobileDemoPersonaHost} className="control-demo-persona-host is-mobile" />
                  ) : (
                    <div>
                      <strong>{identity?.display_name || "公司成员"}</strong>
                      <small>{identity?.email || identity?.roles?.map((role) => role.name).join(" · ") || "企业账号"}</small>
                      <button type="button" onClick={onLogout}>退出登录</button>
                    </div>
                  )}
                </section>
                {demoMode ? <span className="mode-badge">演示数据</span> : mode === "platform" ? <span className="mode-badge is-live">真实 API</span> : null}
              </div>
            </details>
          </div>
        )}
        {canReturnToCreation ? (
          <button className="control-brand" type="button" onClick={() => onSurfaceChange(preferredCreationSurface)} aria-label={`返回旭天${preferredCreationLabel}`}>
            <BrandLogo variant="wordmark" />
            <strong>管理工作台</strong>
          </button>
        ) : (
          <div className="control-brand is-static" aria-label={`${BRAND_NAME} 平台控制台`}>
            <BrandLogo variant="wordmark" />
            <strong>平台控制台</strong>
          </div>
        )}
        <div className="control-command-context" aria-label="当前管理范围">
          <span>{mode === "platform" ? "管理范围" : "当前企业"}</span>
          <strong>{managementScopeName}</strong>
          <small>{activeSectionLabel}</small>
        </div>
        {mode === "company" && canReturnToCreation && (
          <div className="surface-switch" aria-label="工作台导航">
            <button type="button" aria-pressed="false" onClick={() => onSurfaceChange(preferredCreationSurface)}>创作</button>
            <button className="is-active" type="button" aria-pressed="true">企业管理</button>
          </div>
        )}
        {mode === "company" && !demoMode && companyContexts.length > 1 && (
          <label className="control-company-context-switcher is-desktop">
            <span>当前企业</span>
            <select
              value={activeCompanyId || activeCompanyContext?.company_id || companyContexts[0]?.company_id || ""}
              disabled={!onCompanyChange}
              onChange={(event) => onCompanyChange?.(event.target.value)}
              aria-label="切换当前企业"
            >
              {companyContexts.map((company) => (
                <option key={company.company_id} value={company.company_id}>
                  {company.name || company.company_id}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="control-topbar-spacer" />
        <OperationsWorkspaceActions
          canOpenPersonalCreation={canOpenPersonalCreation}
          onOpenPersonalCreation={onOpenPersonalCreation}
          pending={personalCreationPending}
          errorMessageId={personalCreationErrorMessageId}
        />
        <SkinSwitcher value={activeSkin} onChange={onSkinChange} />
        {mode === "platform" && onOpenOperationsConsole ? (
          <button className="control-platform-view-switch" type="button" onClick={onOpenOperationsConsole}>
            <Gauge size={15} />运营指挥台
          </button>
        ) : null}
        {demoMode ? <span className="mode-badge">演示数据</span> : mode === "platform" ? <span className="mode-badge is-live">真实 API</span> : null}
        {demoMode ? <div ref={setDesktopDemoPersonaHost} className="control-demo-persona-host is-desktop" /> : null}
        {!demoMode && (
          <div className="control-live-session-desktop">
            <div className="control-identity"><UserCircle size={24} weight="fill" /><span><strong>{identity?.display_name || (mode === "platform" ? "平台管理员" : "公司成员")}</strong><small>{mode === "platform" ? "平台管理员" : (identity?.roles?.map((role) => role.name).join(" · ") || "企业账号")}</small></span></div>
            <button className="control-logout" type="button" onClick={onLogout}>退出</button>
          </div>
        )}
        {!demoMode && (
          <details className="control-mobile-session">
            <summary aria-label="打开账号菜单"><UserCircle size={22} weight="fill" /><span>账号</span></summary>
            <div>
              <strong>{identity?.display_name || (mode === "platform" ? "平台管理员" : "公司成员")}</strong>
              <small>{identity?.email || (mode === "platform" ? "平台管理员" : (identity?.roles?.map((role) => role.name).join(" · ") || "企业账号"))}</small>
              <button type="button" onClick={onLogout}>退出登录</button>
            </div>
          </details>
        )}
        {demoPersonaControl}
      </header>

      <aside className="control-sidebar">
        <div className="control-context"><div><span>{mode === "platform" ? "平台功能" : "企业功能"}</span><strong>{managementScopeName}</strong><small title={mode === "platform" ? undefined : (data.me?.company_id || activeCompanyId || undefined)}>{mode === "platform" ? "全局控制面" : safeId(data.me?.company_id || activeCompanyId)}</small></div></div>
        <nav ref={controlNavRef} aria-label={mode === "platform" ? "平台管理导航" : "公司管理导航"}>
          {nav.map(([id, label, Icon]) => (
            <button ref={section === id ? activeNavItemRef : null} key={id} className={section === id ? "is-active" : ""} data-management-section={id} type="button" onClick={() => setSection(id)} aria-label={label} title={label} aria-current={section === id ? "page" : undefined}><Icon size={19} aria-hidden="true" /><span>{label}</span></button>
          ))}
        </nav>
        <div className="control-sidebar-foot"><ShieldCheck size={18} /><span><strong>权限由服务端裁决</strong><small>页面显示不替代鉴权</small></span></div>
      </aside>

      <main ref={controlMainRef} className="control-main" aria-busy={loading ? "true" : "false"}>
        {error && <div className="control-alert" role="alert"><WarningCircle size={18} weight="fill" /><span>{error}</span><button className="control-alert-close" data-icon-only="true" type="button" onClick={() => setError("")} aria-label="关闭"><X size={16} /></button></div>}
        {loading && <div className="control-loading"><SpinnerGap size={18} className="spin" /> 正在读取真实数据…</div>}
        <div className={loading ? "control-content is-loading" : "control-content"}>{content}</div>
      </main>

      {drawer && (
        <Drawer
          title={{ invitation: "邀请公司成员", ownerTransfer: "交接老板职责", ownerInvitation: `重新签发老板邀请 · ${drawer.company?.name || ""}`, roles: `成员访问 · ${drawer.member?.display_name || ""}`, role: drawer.role ? `配置角色 · ${drawer.role.name}` : "新建附加权限角色", company: "新建企业", companyControl: `企业管理 · ${drawer.company?.name || ""}`, recharge: `历史人民币人工入账 · ${drawer.company?.name || ""}`, personalPointsGrant: `赠送个人积分 · ${drawer.user?.display_name || ""}`, model: `编辑模型 · ${drawer.model?.display_name || "自动草稿"}`, resource: drawer.resource ? `编辑资源 · ${drawer.resource.display_name}` : "新建功能资源", channelCost: "录入渠道成本" }[drawer.type]}
          detail={{ invitation: "先创建待接受邀请；受邀邮箱完成正式登录和接受后，账号才成为运营或组长。", ownerTransfer: "所有权会原子转移给所选在职成员；当前老板同时降为运营或组长。", ownerInvitation: "旧链接会立即失效。留空替换资料会向当前老板重新签发；若最初邮箱有误，可原子换绑尚未激活的老板账号。", roles: "先选唯一的公司级别，再逐项设置个人权限；个人允许或禁止优先于角色模板。", role: drawer.role?.is_system ? "只有老板可配置组长和运营的权限模板，固定级别名称不能修改。" : (drawer.role ? "更新会重新校验操作者权限并写入审计日志。" : "只授予完成工作所需的最小权限。"), company: "原子创建企业、老板成员与独立钱包；生产环境中的老板需通过一次性邀请完成激活。", companyControl: "查看真实权益状态，逐项配置模型价格、功能开关和公司账务。", recharge: "仅历史人民币计费企业可使用；积分企业必须通过可信支付或受控授权来源入账。", personalPointsGrant: "积分只进入该自然人的个人钱包，不会改变任何企业共享钱包；每笔操作都会写入不可变账本与平台审计。", model: "草稿由 Relay 自动生成；这里只允许收紧能力、设置内部路由与计费方式，保存不会自动发布或分发。", resource: drawer.resource ? "目录状态会影响后续企业开通，不会伪造已有企业权益。" : "创建后可在企业管理中逐家开通。", channelCost: "正数记录成本，负数记录退款或调整，0 表示已确认零成本。" }[drawer.type]}
          returnFocusElement={drawer.returnFocusElement}
          wide={drawer.type === "companyControl"}
          onClose={() => !busy && !entitlementBusyKey && setDrawer(null)}
        >
          {drawer.type === "companyControl" ? (
            <CompanyEntitlementsPanel
              drawer={drawer}
              drawerError={drawerError}
              busyKey={entitlementBusyKey}
              onReload={() => openCompanyControl(drawer.company, { preserveContent: true })}
              onLoadMore={loadMoreCompanyControl}
              onSaveModel={saveCompanyModelEntitlement}
              onToggleResource={saveCompanyResourceEntitlement}
              onRecharge={() => setDrawer({ type: "recharge", company: drawer.company, summary: drawer.summary, idempotencyKey: makeOperationKey("recharge") })}
              onClose={() => setDrawer(null)}
              canManageEntitlements={canUsePlatformPermission("platform.entitlements.manage")}
              canRecharge={!demoMode && canUsePlatformPermission("platform.finance.manage")}
              paginationBusyKey={paginationBusyKey}
            />
          ) : (
          <form className="control-form" onSubmit={submitDrawer}>
            {drawerError && <div className="control-drawer-error" role="alert"><WarningCircle size={18} weight="fill" /><span>{drawerError}</span></div>}
            {drawer.type === "invitation" && <><label><span>姓名</span><input name="displayName" required maxLength={120} autoComplete="name" autoFocus placeholder="例如：王晨" /></label><label><span>受邀邮箱</span><input name="email" type="email" required autoComplete="email" placeholder="name@example.cn" /></label><div className="control-form-grid"><label><span>初始级别</span><select name="primaryRole" defaultValue="operator" required><option value="operator">运营</option><option value="team_lead">组长</option></select></label><label><span>有效小时</span><input name="expiresInHours" type="number" min="1" max="720" step="1" defaultValue="72" required /></label></div><div className="control-form-warning"><WarningCircle size={18} /><span>提交只创建待接受邀请，不会直接激活账号或消耗企业钱包。</span></div></>}
            {drawer.type === "ownerTransfer" && <><label><span>新老板</span><select name="targetMembershipId" required autoFocus defaultValue=""><option value="" disabled>选择一位在职成员</option>{data.members.filter((member) => member.membership_id !== data.me?.membership_id && member.status === "active").map((member) => <option key={member.membership_id} value={member.membership_id}>{member.display_name} · {member.email}</option>)}</select></label><label><span>交接后我的级别</span><select name="formerOwnerPrimaryRole" defaultValue="team_lead" required><option value="team_lead">组长</option><option value="operator">运营</option></select></label><div className="control-form-warning"><WarningCircle size={18} /><span>提交会校验当前老板成员与用户快照；其他窗口已经交接时，本次操作会以 409 拒绝，不会覆盖。</span></div></>}
            {drawer.type === "ownerInvitation" && <><label><span>替换老板邮箱（可选）</span><input name="replacementEmail" type="email" autoComplete="email" autoFocus placeholder="留空则仍邀请当前老板" /></label><label><span>替换老板姓名（可选）</span><input name="replacementDisplayName" autoComplete="name" maxLength={120} placeholder="仅换绑新邮箱时填写" /></label><div className="control-form-warning"><WarningCircle size={18} /><span>提交会校验老板成员与账号快照；仅允许换绑尚未激活且成员关系合格的账号，旧邀请立即失效。</span></div></>}
            {drawer.type === "roles" && <MemberAccessFields key={memberAccessStateKey(drawer.member)} roles={data.roles} permissions={data.permissions} member={drawer.member} />}
            {drawer.type === "role" && <><label><span>角色名称</span><input name="name" required readOnly={Boolean(drawer.role?.is_system)} maxLength={80} placeholder="例如：审阅者" defaultValue={drawer.role?.name || ""} /></label><label><span>说明</span><textarea name="description" maxLength={240} placeholder="说明这个角色适用于谁" defaultValue={drawer.role?.description || ""} /></label><fieldset><legend>权限</legend>{data.permissions.map(({ code, description }) => <label className="control-check" key={code}><input name="permissionCodes" type="checkbox" value={code} defaultChecked={(drawer.role?.permission_codes || []).includes(code)} /><span><strong>{description}</strong><small>{code}</small></span></label>)}</fieldset></>}
            {drawer.type === "company" && <><label><span>企业名称</span><input name="name" required maxLength={160} autoFocus placeholder="例如：远创电商" /></label><label><span>老板姓名</span><input name="ownerDisplayName" required maxLength={120} autoComplete="name" /></label><label><span>老板邮箱</span><input name="ownerEmail" type="email" required autoComplete="email" /></label><div className="control-form-warning"><WarningCircle size={18} /><span>生产环境不会把老板账号直接标为已激活；创建后请复制服务端仅返回一次的 fragment 邀请链接。</span></div></>}
            {drawer.type === "recharge" && <><label><span>人工入账金额（元）</span><input name="amountYuan" type="number" min="0.01" step="0.01" required autoFocus /></label><label><span>账本备注</span><textarea name="note" maxLength={240} placeholder="例如：合同款到账凭证号或人工调整原因" /></label><div className="control-form-warning"><Receipt size={18} /><span>提交后会立即增加企业可用余额并写入审计日志；系统不会代替支付通道确认资金到账。</span></div></>}
            {drawer.type === "personalPointsGrant" && (
              <>
                <div className="control-personal-point-balance" aria-label="个人积分钱包当前余额">
                  <span><small>可用积分</small><strong>{personalPointBalanceLabel(drawer.user.available_points)}</strong></span>
                  <span><small>任务预留</small><strong>{personalPointBalanceLabel(drawer.user.reserved_points)}</strong></span>
                </div>
                <React.Fragment key={drawer.idempotencyKey}>
                  <label><span>赠送积分</span><input name="amountPoints" type="number" inputMode="numeric" min="1" max="9000000000000000" step="1" required autoFocus disabled={Boolean(drawer.success)} placeholder="填写正整数" /><small>积分使用整数记账，不会换算或写入企业人民币钱包。</small></label>
                  <label><span>赠送说明</span><textarea name="note" minLength={1} maxLength={240} required disabled={Boolean(drawer.success)} placeholder="例如：封闭内测体验额度" /><small>说明会进入不可变积分账本和平台审计，请写明真实原因。</small></label>
                </React.Fragment>
                {drawer.success && (
                  <div className={`control-personal-point-result ${drawer.success.created ? "is-created" : "is-replayed"}`} role="status">
                    <Check size={19} weight="bold" aria-hidden="true" />
                    <span>
                      <strong>{drawer.success.created ? "本笔积分已经入账" : "已确认这是安全重放"}</strong>
                      <small>{drawer.success.created
                        ? `${personalPointBalanceLabel(drawer.success.amountPoints)} 已写入个人钱包，账本记录 ${safeId(drawer.success.ledgerEntryId)}。`
                        : "服务端复用了原账本记录，没有再次增加积分。"}</small>
                    </span>
                    <button type="button" onClick={startNextPersonalPointGrant}>开始下一笔</button>
                  </div>
                )}
                <div className="control-form-warning"><Gift size={18} /><span>提交使用稳定操作凭据；网络中断后可直接重试同一笔，服务端不会重复赠送。赠送完成后如需撤回，应通过独立的审计调整流程处理。</span></div>
                <section className="control-personal-point-history" aria-labelledby="personal-point-history-title">
                  <header>
                    <div><strong id="personal-point-history-title">最近赠送记录</strong><small>{drawer.history ? `共 ${drawer.history.total || 0} 笔，累计 ${personalPointBalanceLabel(drawer.history.total_amount_points || 0)}` : "读取个人积分账本"}</small></div>
                    <button type="button" disabled={drawer.historyLoading} onClick={() => refreshPersonalPointGrantHistory(drawer.user)}><ArrowClockwise size={15} className={drawer.historyLoading ? "spin" : ""} aria-hidden="true" />刷新</button>
                  </header>
                  {drawer.historyLoading ? (
                    <div className="control-personal-point-history-state" role="status"><SpinnerGap size={17} className="spin" /> 正在读取赠送记录…</div>
                  ) : drawer.historyError ? (
                    <div className="control-drawer-error" role="alert"><WarningCircle size={18} weight="fill" /><span>{drawer.historyError}</span><button type="button" onClick={() => refreshPersonalPointGrantHistory(drawer.user)}>重试</button></div>
                  ) : drawer.history?.items?.length ? (
                    <ol>
                      {drawer.history.items.map((entry) => (
                        <li key={entry.id}>
                          <span><strong>+{personalPointBalanceLabel(entry.amount_points)}</strong><small>{entry.note || "未填写说明"}</small></span>
                          <span><time dateTime={entry.created_at}>{shortDate(entry.created_at)}</time><small>账本 {safeId(entry.id)}</small></span>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="control-personal-point-history-state">暂无赠送记录</p>
                  )}
                </section>
              </>
            )}
            {drawer.type === "model" && (
              <>
                <label><span>客户公共模型标识</span><input name="slug" disabled value={drawer.model?.slug || ""} /><small>由 Relay 公共模型 ID 自动映射；客户不会看到 new-api 渠道编号。</small></label>
                <label><span>显示名称</span><input name="displayName" required maxLength={120} defaultValue={drawer.model?.display_name || ""} /></label>
                <label><span>Platform 内部路由键</span><input name="providerKey" required pattern="[a-z0-9][a-z0-9._-]*" placeholder="仅填写内部逻辑键，不是 API Key" defaultValue={drawer.model?.provider_key || ""} /><small>不粘贴供应商密钥、new-api Key 或渠道凭据。</small></label>
                <label><span>固定计费方式</span><select name="billingMode" defaultValue={drawer.model?.billing_mode || "per_item"} required><option value="per_second">按秒计费</option><option value="per_item">按条计费</option></select></label>
                <small className="control-form-help">计费方式属于模型目录；发布并授权后不能切换。按秒计费模式单次只允许 1 个产物。</small>
                <CapabilityEditorFields
                  key={drawer.model?.id || "missing-auto-draft"}
                  model={drawer.model || null}
                />
              </>
            )}
            {drawer.type === "resource" && <><label><span>资源标识</span><input name="key" required={!drawer.resource} readOnly={Boolean(drawer.resource)} pattern="[a-z0-9][a-z0-9._-]+[a-z0-9]" placeholder="face.library" defaultValue={drawer.resource?.key || ""} /></label><label><span>显示名称</span><input name="displayName" required defaultValue={drawer.resource?.display_name || ""} /></label><label><span>类型</span><select name="kind" defaultValue={drawer.resource?.kind || "feature"} disabled={Boolean(drawer.resource)}><option value="feature">平台功能</option><option value="agent">智能体</option><option value="external_api">外部 API</option></select></label><label><span>说明</span><textarea name="description" maxLength={500} defaultValue={drawer.resource?.description || ""} /></label><label className="control-check"><input name="active" type="checkbox" defaultChecked={drawer.resource ? drawer.resource.active : true} /><span><strong>目录启用</strong><small>关闭后不能再向企业开通此资源</small></span></label></>}
            {drawer.type === "channelCost" && <><div className="control-form-warning"><Receipt size={18} /><span>正数表示成本，负数表示有凭证的退款或调整，0 表示明确的零成本确认。</span></div><div className="control-form-grid"><label><span>渠道键</span><input name="channelKey" required pattern="[a-z0-9][a-z0-9._-]*" placeholder="provider-key" autoFocus /></label><label><span>渠道类型</span><select name="channelType" defaultValue="official" required><option value="reverse">逆向渠道</option><option value="third_party_api">第三方 API</option><option value="official">官方渠道</option></select></label></div><div className="control-form-grid"><label><span>成本金额（元）</span><input name="amountYuan" type="number" step="0.01" required placeholder="正数、负数或 0" /></label><label><span>发生时间</span><input name="occurredAt" type="datetime-local" required defaultValue={localDateTimeInput()} /></label></div><label><span>外部凭证号</span><input name="externalReference" required maxLength={160} placeholder="账单号、流水号或发票号" /></label><label><span>关联企业（可选）</span><select name="companyId" defaultValue=""><option value="">不关联企业</option>{(data.companies?.items || []).map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</select></label><div className="control-form-grid"><label><span>关联任务 ID（可选）</span><input name="taskId" /></label><label><span>Relay 任务 ID（可选）</span><input name="relayJobId" /></label></div><label><span>备注（可选）</span><textarea name="note" maxLength={240} placeholder="说明账单口径或调整原因" /></label></>}
            <footer><QuietButton onClick={() => setDrawer(null)} disabled={busy}>取消</QuietButton><PrimaryButton type="submit" disabled={busy || Boolean(drawer.type === "personalPointsGrant" && drawer.success)}>{drawer.type === "personalPointsGrant" && drawer.success ? <><Check size={16} /> 本笔已完成</> : busy ? <><SpinnerGap size={16} className="spin" /> 正在提交</> : <><Check size={16} /> 确认提交</>}</PrimaryButton></footer>
          </form>
          )}
        </Drawer>
      )}

      {toast && <div className="control-toast" role="status"><Check size={17} weight="bold" />{toast}</div>}
    </div>
  );
}

function PlatformManagementRouter(props) {
  const [view, setView] = useState("operations");
  const [basicConfigurationContext, setBasicConfigurationContext] = useState({
    identity: null,
    section: "companies",
  });
  const [operationsContext, setOperationsContext] = useState({
    activeSection: "task-operations",
    range: "24h",
  });
  const operationsRestoreRef = useRef({
    pending: false,
    documentScrollTop: 0,
    mainScrollTop: 0,
  });
  const operationsScrollSnapshotRef = useRef({
    initialized: false,
    documentScrollTop: 0,
    lastUserScrollAt: Number.NEGATIVE_INFINITY,
  });

  useEffect(() => {
    let settleTimeout = 0;
    const captureSettledScroll = () => {
      const operationsSurface = document.querySelector(".platform-operations-surface");
      if (!operationsSurface || operationsSurface.hidden) return;
      operationsScrollSnapshotRef.current.initialized = true;
      operationsScrollSnapshotRef.current.documentScrollTop = document.scrollingElement?.scrollTop || 0;
    };
    const scheduleSettledScroll = () => {
      if (settleTimeout) window.clearTimeout(settleTimeout);
      settleTimeout = window.setTimeout(captureSettledScroll, 100);
    };
    const markUserScroll = () => {
      operationsScrollSnapshotRef.current.lastUserScrollAt = performance.now();
    };
    captureSettledScroll();
    document.addEventListener("scroll", scheduleSettledScroll, { capture: true, passive: true });
    document.addEventListener("wheel", markUserScroll, { passive: true });
    document.addEventListener("touchmove", markUserScroll, { passive: true });
    return () => {
      if (settleTimeout) window.clearTimeout(settleTimeout);
      document.removeEventListener("scroll", scheduleSettledScroll, { capture: true });
      document.removeEventListener("wheel", markUserScroll);
      document.removeEventListener("touchmove", markUserScroll);
    };
  }, []);

  useEffect(() => {
    if (view !== "operations" || !operationsRestoreRef.current.pending) return undefined;
    let frame = 0;
    let settleTimeout = 0;
    let attempts = 0;
    const restore = () => {
      const basicConfigButton = document.querySelector(".ops-basic-config-button");
      const operationsMain = document.querySelector(".ops-console > main");
      if (!basicConfigButton && attempts < 40) {
        attempts += 1;
        frame = globalThis.requestAnimationFrame?.(restore) || 0;
        return;
      }
      basicConfigButton?.focus({ preventScroll: true });
      const applySavedPosition = () => {
        if (operationsMain) operationsMain.scrollTop = operationsRestoreRef.current.mainScrollTop;
        if (document.scrollingElement) document.scrollingElement.scrollTop = operationsRestoreRef.current.documentScrollTop;
      };
      let settleFrames = 8;
      const settle = () => {
        applySavedPosition();
        settleFrames -= 1;
        if (settleFrames > 0) {
          frame = globalThis.requestAnimationFrame?.(settle) || 0;
          return;
        }
        operationsRestoreRef.current.pending = false;
        settleTimeout = window.setTimeout(applySavedPosition, 240);
      };
      settle();
    };
    frame = globalThis.requestAnimationFrame?.(restore) || 0;
    return () => {
      if (frame) globalThis.cancelAnimationFrame?.(frame);
      if (settleTimeout) window.clearTimeout(settleTimeout);
    };
  }, [view]);

  const returnToOperations = () => {
    operationsRestoreRef.current.pending = true;
    setView("operations");
  };

  const operationsSurface = (
    <div
      className="platform-operations-surface"
      hidden={view !== "operations"}
      inert={view !== "operations"}
      aria-hidden={view !== "operations" ? "true" : undefined}
    >
    <Suspense
      fallback={(
        <main className="auth-gate" data-theme={normalizeSkin(props.skin)} aria-labelledby="platform-console-loading">
          <section className="auth-gate-card" aria-live="polite">
            <span className="view-kicker">平台运营</span>
            <h1 id="platform-console-loading">正在打开运营控制台</h1>
            <p>正在按当前管理员权限加载可访问的经营、权益与安全模块。</p>
          </section>
        </main>
      )}
    >
      <LazyAdminOperationsContainer
        {...props}
        operationsVisible={view === "operations"}
        operationsContext={operationsContext}
        onOperationsContextChange={setOperationsContext}
        onOpenBasicConfig={({ identity, section, operationsContext: latestOperationsContext }) => {
          const operationsMain = document.querySelector(".ops-console > main");
          const currentDocumentScrollTop = document.scrollingElement?.scrollTop || 0;
          const scrollSnapshot = operationsScrollSnapshotRef.current;
          const recentUserScroll = performance.now() - scrollSnapshot.lastUserScrollAt < 500;
          operationsRestoreRef.current = {
            pending: false,
            documentScrollTop: recentUserScroll || !scrollSnapshot.initialized
              ? currentDocumentScrollTop
              : scrollSnapshot.documentScrollTop,
            mainScrollTop: operationsMain?.scrollTop || 0,
          };
          if (latestOperationsContext) setOperationsContext(latestOperationsContext);
          setBasicConfigurationContext({ identity, section });
          setView("basic-configuration");
        }}
      />
      </Suspense>
    </div>
  );

  return (
    <>
      {operationsSurface}
      {view === "basic-configuration" ? (
        <ManagementConfigurationConsole
          {...props}
          initialPlatformIdentity={basicConfigurationContext.identity}
          initialPlatformSection={basicConfigurationContext.section}
          onOpenOperationsConsole={returnToOperations}
        />
      ) : null}
    </>
  );
}

export function ManagementConsole(props) {
  const shouldLoadDemoFixtures = !import.meta.env.PROD && props.demoMode;
  const [managementDemoFixtures, setManagementDemoFixtures] = useState(null);
  const [demoFixtureError, setDemoFixtureError] = useState("");

  useEffect(() => {
    if (!shouldLoadDemoFixtures || managementDemoFixtures || demoFixtureError) return undefined;
    let active = true;
    import("./demo/managementFixtures.js")
      .then((fixtures) => {
        if (active) setManagementDemoFixtures(fixtures);
      })
      .catch(() => {
        if (active) setDemoFixtureError("演示数据加载失败，请刷新后重试。");
      });
    return () => {
      active = false;
    };
  }, [demoFixtureError, managementDemoFixtures, shouldLoadDemoFixtures]);

  if (shouldLoadDemoFixtures && !managementDemoFixtures) {
    return (
      <main className="auth-gate" data-theme={normalizeSkin(props.skin)} aria-labelledby="management-demo-loading">
        <section className="auth-gate-card" aria-live="polite">
          <span className="view-kicker">管理控制台</span>
          <h1 id="management-demo-loading">{demoFixtureError || "正在加载演示工作区"}</h1>
          <p>{demoFixtureError || "正在准备与生产数据隔离的开发演示数据。"}</p>
        </section>
      </main>
    );
  }

  const resolvedProps = { ...props, managementDemoFixtures };
  if (props.mode === "platform") return <PlatformManagementRouter {...resolvedProps} />;
  return <ManagementConfigurationConsole {...resolvedProps} />;
}
