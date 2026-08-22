import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AdminOperationsConsole } from "./OperationsConsole.jsx";
import { ShowcaseOperationsContainer } from "./showcase/ShowcaseOperationsContainer.jsx";
import {
  adaptAdminOperationsData,
  adaptRelayControlChannel,
  adminRangeWindow,
  visibleAdminSections,
} from "./adminApiAdapter.js";
import {
  buildEntitlementKey,
  downloadTextFile,
  toAuditCsv,
} from "./adminConsoleUtils.js";
import {
  adaptRelayUnknownSubmission,
  buildRelayUnknownResolution,
  refreshRelayUnknownWithReadback as refreshRelayUnknownOperation,
  resolveRelayUnknownWithReadback as resolveRelayUnknownOperation,
} from "./relayUnknownOperations.js";
import {
  adaptRelayCallbackDeadLetter,
  buildRelayCallbackRedrive,
  redriveRelayCallbackWithReadback,
} from "./relayCallbackDeadLetters.js";
import {
  buildRelayChannelOperationRequest,
  readRelayChannelOperation as readRelayChannelOperationReceipt,
  runRelayChannelOperationWithReadback,
} from "./relayChannelOperations.js";
import {
  relayNativeConsoleAuthorizationAllowed,
  relayNativeConsoleAuthorizationDisabledReason,
  requestRelayNativeConsoleGrant,
} from "./relayNativeConsole.js";

const MATRIX_PAGE_SIZE = 100;
const MAX_BATCH_CELLS = 500;
const OPERATIONS_SECTIONS = new Set([
  "cockpit",
  "task-operations",
  "model-profit",
  "company-health",
  "entitlements",
  "channels",
  "publishing-assets",
  "showcase",
  "access-audit",
]);
const OPERATIONS_RANGES = new Set(["24h", "7d", "30d", "month"]);
const OPERATIONS_SOURCE_KEYS = [
  "operating",
  "profitability",
  "readiness",
  "taskOps",
  "dashboard",
  "companyHealth",
  "relayChannels",
  "channelHealth",
  "relayUnknownSubmissions",
  "relayCallbackDeadLetters",
  "exceptions",
  "matrix",
  "coverage",
  "relayModels",
  "audits",
  "permissionCatalog",
  "administrators",
];

function requestKey(prefix) {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`.slice(0, 120);
}

function readOperationsLocation() {
  if (!globalThis.location) return {};
  const search = new URLSearchParams(globalThis.location.search || "");
  const activeSection = search.get("ops_module");
  const range = search.get("ops_range");
  return {
    activeSection: OPERATIONS_SECTIONS.has(activeSection) ? activeSection : "",
    range: OPERATIONS_RANGES.has(range) ? range : "",
  };
}

function writeOperationsLocation({ activeSection, range }, { push = false } = {}) {
  if (!globalThis.history || !globalThis.location) return;
  const url = new URL(globalThis.location.href);
  if (OPERATIONS_SECTIONS.has(activeSection)) url.searchParams.set("ops_module", activeSection);
  if (OPERATIONS_RANGES.has(range)) url.searchParams.set("ops_range", range);
  if (activeSection !== "access-audit") {
    url.searchParams.delete("ops_audit_tab");
    url.searchParams.delete("ops_audit_query");
    url.searchParams.delete("ops_audit_result");
  }
  globalThis.history[push ? "pushState" : "replaceState"](
    globalThis.history.state,
    "",
    `${url.pathname}${url.search}${url.hash}`,
  );
}

function currentEnvironment() {
  const configured = globalThis.__AI_VIDEO_RUNTIME_CONFIG__?.environment;
  if (["production", "staging", "development"].includes(configured)) {
    return configured;
  }
  const host = globalThis.location?.hostname;
  return ["localhost", "127.0.0.1", "::1"].includes(host)
    ? "development"
    : "production";
}

function hasPermissions(identity, ...codes) {
  if (identity?.is_platform_owner) return true;
  const granted = new Set(identity?.permission_codes || []);
  return codes.every((code) => granted.has(code));
}

function basicConfigurationSection(identity, demoMode) {
  if (demoMode || identity?.is_platform_owner) return "users";
  const can = (...codes) => hasPermissions(identity, ...codes);
  if (can("platform.companies.read", "platform.models.read", "platform.resources.read", "platform.analytics.read")) return "companies";
  if (can("platform.companies.read", "platform.models.read", "platform.finance.read")) return "reports";
  if (can("platform.models.read")) return "models";
  if (can("platform.resources.read")) return "resources";
  if (can("platform.analytics.read", "platform.companies.read", "platform.provider_costs.read")) return "overview";
  if (can("platform.audit.read")) return "audit";
  return "";
}

function errorMessage(error) {
  if (error?.status === 401) return "登录状态已失效，请重新登录。";
  if (error?.status === 403) return "当前平台管理员没有访问该模块的权限。";
  return error?.message || "平台运营数据加载失败，请稍后重试。";
}

async function runWithConcurrency(tasks, limit = 4) {
  const results = new Array(tasks.length);
  let cursor = 0;
  const worker = async () => {
    while (cursor < tasks.length) {
      const index = cursor;
      cursor += 1;
      try {
        results[index] = { status: "fulfilled", value: await tasks[index]() };
      } catch (reason) {
        results[index] = { status: "rejected", reason };
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, tasks.length) }, worker));
  return results;
}

export async function loadCompleteEntitlementMatrix(client, { signal } = {}) {
  const filters = {
    company_page: 1,
    company_page_size: MATRIX_PAGE_SIZE,
    catalog_page: 1,
    catalog_page_size: MATRIX_PAGE_SIZE,
    include_retired: true,
  };
  const first = await client.getAdminEntitlementMatrix(filters, { signal });
  const companyPages = Math.max(
    1,
    Math.ceil(Number(first.total_companies || 0) / MATRIX_PAGE_SIZE),
  );
  const catalogPages = Math.max(
    1,
    Math.ceil(Number(first.total_catalog_items || 0) / MATRIX_PAGE_SIZE),
  );
  const requests = [];
  for (let companyPage = 1; companyPage <= companyPages; companyPage += 1) {
    for (let catalogPage = 1; catalogPage <= catalogPages; catalogPage += 1) {
      if (companyPage === 1 && catalogPage === 1) continue;
      requests.push(() => client.getAdminEntitlementMatrix({
        ...filters,
        company_page: companyPage,
        catalog_page: catalogPage,
      }, { signal }));
    }
  }
  const pages = [first];
  if (requests.length) {
    const results = await runWithConcurrency(requests);
    const rejected = results.find((item) => item.status === "rejected");
    if (rejected) throw rejected.reason;
    pages.push(...results.map((item) => item.value));
  }

  const columnMap = new Map();
  const rowMap = new Map();
  for (const page of pages) {
    for (const column of page.columns || []) {
      columnMap.set(`${column.item_kind}:${column.item_id}`, column);
    }
    for (const row of page.rows || []) {
      const current = rowMap.get(row.company_id) || { ...row, cells: [] };
      const cells = new Map(
        current.cells.map((cell) => [`${cell.item_kind}:${cell.item_id}`, cell]),
      );
      for (const cell of row.cells || []) {
        cells.set(`${cell.item_kind}:${cell.item_id}`, cell);
      }
      current.cells = Array.from(cells.values());
      rowMap.set(row.company_id, current);
    }
  }
  return {
    ...first,
    company_page: 1,
    company_page_size: Number(first.total_companies || 0),
    catalog_page: 1,
    catalog_page_size: Number(first.total_catalog_items || 0),
    columns: Array.from(columnMap.values()),
    rows: Array.from(rowMap.values()),
  };
}

export async function loadCompleteRelayChannels(client, { signal } = {}) {
  const pageSize = 100;
  const first = await client.listAdminRelayChannels(
    { page: 1, page_size: pageSize },
    { signal },
  );
  const total = Number(first?.total || 0);
  const pageCount = Math.ceil(total / pageSize);
  const requests = [];
  for (let page = 2; page <= pageCount; page += 1) {
    requests.push(() => client.listAdminRelayChannels(
      { page, page_size: pageSize },
      { signal },
    ));
  }
  const pages = [first];
  if (requests.length) {
    const results = await runWithConcurrency(requests);
    const rejected = results.find((item) => item.status === "rejected");
    if (rejected) throw rejected.reason;
    pages.push(...results.map((item) => item.value));
  }
  return {
    ...first,
    page: 1,
    page_size: pages.reduce((count, page) => count + (page?.data?.length || 0), 0),
    total,
    data: pages.flatMap((page) => Array.isArray(page?.data) ? page.data : []),
  };
}

function toUtcIso(value) {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error("权益生效或到期时间格式无效。");
  }
  return parsed.toISOString();
}

function parseCapabilityLimit(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return {};
  let parsed;
  try {
    parsed = JSON.parse(normalized);
  } catch {
    throw new Error("能力限制必须是有效的 JSON 对象。");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("能力限制必须是 JSON 对象。");
  }
  return parsed;
}

function positiveOrNull(value, label) {
  if (value === "" || value == null) return null;
  const number = Number(value);
  if (!Number.isInteger(number) || number <= 0) {
    throw new Error(`${label}必须是大于 0 的整数。`);
  }
  return number;
}

export function entitlementMutation({ companyId, product, grant, enabled }) {
  if (!product) throw new Error("未找到要变更的权益目录项，请刷新后重试。");
  const itemKind = product.kind === "model" ? "model" : "resource";
  const result = {
    company_id: companyId,
    item_kind: itemKind,
    item_id: product.id,
    enabled: Boolean(enabled),
    config_override: parseCapabilityLimit(grant?.capabilityLimit),
    call_quota: positiveOrNull(grant?.quota, "调用额度"),
    concurrency_limit: positiveOrNull(grant?.concurrency, "并发数"),
    effective_at: toUtcIso(grant?.effectiveAt),
    expires_at: toUtcIso(grant?.expiresAt),
  };
  if (result.effective_at && result.expires_at
    && new Date(result.effective_at) >= new Date(result.expires_at)) {
    throw new Error("权益到期时间必须晚于生效时间。");
  }
  if (itemKind === "model") {
    const price = positiveOrNull(grant?.priceCents, "企业单价");
    if (result.enabled && price == null) {
      throw new Error(`模型“${product.name}”尚未配置企业单价，请先单独配置。`);
    }
    if (price != null) {
      if (product.billingMode === "per_second") {
        result.price_per_second_cents = price;
      } else if (product.billingMode === "per_item") {
        result.price_per_item_cents = price;
      } else {
        throw new Error(`模型“${product.name}”的计费方式未配置，不能开通。`);
      }
    }
  }
  return result;
}

function requireReason(value) {
  const reason = String(value || "").trim();
  if (reason.length < 3) throw new Error("变更原因至少填写 3 个字符。");
  return reason;
}

function previewChangedCount(preview) {
  return Number(preview?.changed_cells || 0);
}

export function exactPermissionOverrides(admin, selectedPermissions, catalog) {
  const selected = new Set(selectedPermissions || []);
  const inherited = new Set(admin?.access?.inherited_permissions || []);
  const overrides = {};
  for (const permission of catalog || []) {
    if (selected.has(permission.key) && !inherited.has(permission.key)) {
      overrides[permission.key] = "allow";
    } else if (!selected.has(permission.key) && inherited.has(permission.key)) {
      overrides[permission.key] = "deny";
    }
  }
  return overrides;
}

async function loadLiveData(client, range, signal) {
  const me = await client.getPlatformAdminMe({ signal });
  const window = adminRangeWindow(range);
  const requests = [];
  const sourceStatuses = Object.fromEntries(
    OPERATIONS_SOURCE_KEYS.map((key) => [key, "unauthorized"]),
  );
  const sourceErrors = {};
  const add = (key, label, work) => {
    sourceStatuses[key] = "loading";
    requests.push({ key, label, work });
  };

  if (hasPermissions(me, "platform.analytics.read", "platform.finance.read", "platform.provider_costs.read")) {
    add("operating", "经营趋势", () => client.getAdminOperatingSeries(window, { signal }));
    add("profitability", "模型盈利", () => client.getAdminModelProfitability(window, { signal }));
  }
  if (hasPermissions(me, "platform.analytics.read")) {
    // This is the server-owned fail-closed verdict. It shares the analytics
    // permission boundary so audit-only/catalog-only administrators do not get
    // a predictable 403 and a misleading partial-load warning.
    add("readiness", "生产数据就绪状态", () => client.getAdminDataReadiness({ signal }));
    add("taskOps", "任务运营", () => client.getAdminTaskOperations(window, { signal }));
    add("dashboard", "企业排名", () => client.getPlatformDashboard({ page_size: 100 }, { signal }));
  }
  if (hasPermissions(me, "platform.analytics.read", "platform.finance.read")) {
    add("companyHealth", "企业健康", () => client.getAdminCompanyHealth({ page: 1, page_size: 100 }, { signal }));
  }
  if (hasPermissions(me, "platform.relay_health.read")) {
    add("relayChannels", "Relay 渠道控制面", () => loadCompleteRelayChannels(client, { signal }));
    add("channelHealth", "渠道健康", () => client.getAdminChannelHealth(window, { signal }));
    add("relayUnknownSubmissions", "Relay 未知提交", () => (
      client.listAdminRelayUnknownSubmissions({ page: 1, page_size: 100 }, { signal })
    ));
    add("relayCallbackDeadLetters", "Relay callback dead letters", () => (
      client.listAdminRelayCallbackDeadLetters({ page: 1, page_size: 100 }, { signal })
    ));
  }
  if (hasPermissions(me, "platform.publishing_exceptions.read", "platform.asset_exceptions.read", "platform.relay_health.read")) {
    add("exceptions", "异常中心", () => client.getAdminExceptionCenter({ limit_per_category: 100 }, { signal }));
  }
  if (hasPermissions(me, "platform.entitlements.read")) {
    add("matrix", "企业权益矩阵", () => loadCompleteEntitlementMatrix(client, { signal }));
    add("coverage", "权益覆盖率", () => client.getAdminEntitlementCoverage({ include_retired: true }, { signal }));
  }
  if (hasPermissions(me, "platform.models.read")) {
    add("relayModels", "Relay 模型映射", () => client.listAdminRelayModels({ signal }));
  }
  if (hasPermissions(me, "platform.audit.read")) {
    add("audits", "操作审计", () => client.listAdminAuditLogs({ page: 1, page_size: 100 }, { signal }));
  }
  if (hasPermissions(me, "platform.admin_access.read")) {
    add("permissionCatalog", "管理员权限目录", () => client.listPlatformAdminPermissionCatalog({ signal }));
    add("administrators", "平台管理员", () => client.listPlatformAdministrators({ signal }));
  }

  const settled = await Promise.allSettled(requests.map((item) => item.work()));
  const raw = {
    loadedAt: new Date().toISOString(),
    environment: currentEnvironment(),
    sourceStatuses,
    sourceErrors,
  };
  const failures = [];
  settled.forEach((result, index) => {
    const request = requests[index];
    if (result.status === "fulfilled") {
      raw[request.key] = result.value;
      sourceStatuses[request.key] = "available";
    } else {
      const message = errorMessage(result.reason);
      sourceStatuses[request.key] = result.reason?.status === 401
        || result.reason?.status === 403
        ? "unauthorized"
        : "failed";
      sourceErrors[request.key] = message;
      failures.push(`${request.label}：${message}`);
    }
  });
  return { me, raw, failures };
}

export function AdminOperationsContainer({
  client,
  demoMode: requestedDemoMode = false,
  demoIdentity,
  demoPersonaId,
  onDemoPersonaChange,
  onLogout,
  onSessionError,
  onOpenBasicConfig,
  operationsContext = {},
  onOperationsContextChange,
  skin = "paper",
  onSkinChange,
}) {
  const demoMode = !import.meta.env.PROD && requestedDemoMode;
  const [initialLocation] = useState(readOperationsLocation);
  const [internalRange, setInternalRange] = useState(initialLocation.range || operationsContext.range || "24h");
  const [internalSection, setInternalSection] = useState(initialLocation.activeSection || operationsContext.activeSection || "task-operations");
  const range = internalRange;
  const activeSection = internalSection;
  const [data, setData] = useState(null);
  const [identity, setIdentity] = useState(demoMode ? demoIdentity : null);
  const [loading, setLoading] = useState(!demoMode);
  const [error, setError] = useState("");
  const loadSequence = useRef(0);

  const reload = useCallback(async (nextRange = range, externalSignal) => {
    if (demoMode) return;
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setError("");
    try {
      const result = await loadLiveData(client, nextRange, externalSignal);
      if (sequence !== loadSequence.current || externalSignal?.aborted) return;
      setIdentity(result.me);
      setData(adaptAdminOperationsData(result.raw));
      setError(result.failures.length
        ? `部分模块未能加载：${result.failures.join("；")}`
        : "");
    } catch (loadError) {
      if (externalSignal?.aborted || loadError?.name === "AbortError") return;
      if (sequence === loadSequence.current) {
        if (loadError?.status === 401 || loadError?.status === 403) {
          setIdentity(null);
          setData(null);
        }
        setError(errorMessage(loadError));
      }
    } finally {
      if (sequence === loadSequence.current && !externalSignal?.aborted) setLoading(false);
    }
  }, [client, demoMode, range]);

  useEffect(() => {
    if (demoMode) {
      if (import.meta.env.PROD) {
        setIdentity(null);
        setData(null);
        setError("生产构建已禁用演示数据。");
        setLoading(false);
        return undefined;
      }
      setIdentity(demoIdentity);
      setLoading(true);
      let cancelled = false;
      import("./adminDemoData.js")
        .then((module) => {
          if (!cancelled) {
            setData(module.default);
            setError("");
          }
        })
        .catch(() => {
          if (!cancelled) setError("演示数据模块加载失败。");
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
      return () => {
        cancelled = true;
      };
    }
    const controller = new AbortController();
    reload(range, controller.signal);
    return () => controller.abort();
  }, [demoIdentity, demoMode, range]); // eslint-disable-line react-hooks/exhaustive-deps

  const visibleSections = useMemo(
    () => demoMode ? undefined : visibleAdminSections(identity),
    [demoMode, identity],
  );
  const permissionCatalog = data?.adminPermissionCatalog || [];
  const canManageEntitlements = demoMode
    || hasPermissions(identity, "platform.entitlements.manage");
  const canManageAdminAccess = demoMode
    || hasPermissions(identity, "platform.admin_access.manage");
  const canManageAssetExceptions = demoMode
    || hasPermissions(identity, "platform.asset_exceptions.manage");
  const canReadAudit = demoMode || hasPermissions(identity, "platform.audit.read");
  const canReadAdminAccess = demoMode
    || hasPermissions(identity, "platform.admin_access.read");
  const canReadEntitlements = demoMode
    || hasPermissions(identity, "platform.entitlements.read");
  const canReadRelayHealth = demoMode
    || hasPermissions(identity, "platform.relay_health.read");
  const canManageRelayHealth = demoMode
    || hasPermissions(identity, "platform.relay_health.manage");
  const isPlatformOwner = identity?.is_platform_owner === true;
  const nativeConsoleEnvironment = data?.summary?.environment || currentEnvironment();
  const nativeConsoleAccess = {
    demoMode,
    isPlatformOwner: identity?.is_platform_owner === true,
    canManageRelayHealth,
  };
  const canAuthorizeRelayNativeConsole = relayNativeConsoleAuthorizationAllowed(
    nativeConsoleAccess,
  );
  const relayNativeConsoleDisabledReason = relayNativeConsoleAuthorizationDisabledReason(
    nativeConsoleAccess,
  );
  const basicSection = basicConfigurationSection(identity, demoMode);

  useEffect(() => {
    writeOperationsLocation({ activeSection, range });
  }, []); // Persist the initial route without creating a duplicate history entry.

  useEffect(() => {
    const restoreFromHistory = () => {
      const restored = readOperationsLocation();
      const nextSection = restored.activeSection || operationsContext.activeSection || "task-operations";
      const nextRange = restored.range || operationsContext.range || "24h";
      setInternalSection(nextSection);
      setInternalRange(nextRange);
      onOperationsContextChange?.({ activeSection: nextSection, range: nextRange });
    };
    globalThis.addEventListener?.("popstate", restoreFromHistory);
    return () => globalThis.removeEventListener?.("popstate", restoreFromHistory);
  }, [onOperationsContextChange, operationsContext.activeSection, operationsContext.range]);

  const updateActiveSection = useCallback((nextSection) => {
    setInternalSection(nextSection);
    writeOperationsLocation({ activeSection: nextSection, range }, { push: true });
    onOperationsContextChange?.({ activeSection: nextSection, range });
  }, [onOperationsContextChange, range]);
  const updateRange = useCallback((nextRange) => {
    setInternalRange(nextRange);
    writeOperationsLocation({ activeSection, range: nextRange });
    onOperationsContextChange?.({ activeSection, range: nextRange });
  }, [activeSection, onOperationsContextChange]);

  const saveEntitlement = useCallback(async ({ companyId, productId, grant, reason }) => {
    const product = data?.entitlementProducts?.find((item) => item.id === productId);
    const change = entitlementMutation({
      companyId,
      product,
      grant,
      enabled: grant.state === "enabled",
    });
    const preview = await client.previewAdminEntitlementBatch({ changes: [change] });
    if (!previewChangedCount(preview)) {
      await reload();
      throw new Error("服务端核对后没有需要提交的变化，矩阵已刷新。");
    }
    await client.executeAdminEntitlementBatch({
      changes: [change],
      expected_snapshot: preview.snapshot,
      reason: requireReason(reason),
      idempotency_key: requestKey("entitlement-cell"),
    });
    await reload();
  }, [client, data, reload]);

  const commitBatch = useCallback(async (preview) => {
    const reason = requireReason(preview.reason);
    if (preview.mode === "enable" || preview.mode === "disable") {
      if ((preview.changes || []).length > MAX_BATCH_CELLS) {
        throw new Error(`单次最多变更 ${MAX_BATCH_CELLS} 项权益，请缩小范围。`);
      }
      const changes = (preview.changes || []).map((item) => {
        const product = data?.entitlementProducts?.find((entry) => entry.id === item.productId);
        const grant = data?.entitlementGrants?.[
          buildEntitlementKey(item.companyId, item.productId)
        ];
        return entitlementMutation({
          companyId: item.companyId,
          product,
          grant,
          enabled: preview.mode === "enable",
        });
      });
      if (!changes.length) throw new Error("没有需要提交的权益变化。");
      const serverPreview = await client.previewAdminEntitlementBatch({ changes });
      if (!previewChangedCount(serverPreview)) {
        await reload();
        throw new Error("服务端核对后没有需要提交的变化，矩阵已刷新。");
      }
      await client.executeAdminEntitlementBatch({
        changes,
        expected_snapshot: serverPreview.snapshot,
        reason,
        idempotency_key: requestKey("entitlement-batch"),
      });
    } else if (preview.mode === "copy") {
      const targets = [...new Set(preview.companyIds || [])];
      if (targets.includes(preview.copySourceId)) {
        throw new Error("配置来源企业不能同时作为复制目标。");
      }
      const body = {
        source_company_id: preview.copySourceId,
        target_company_ids: targets,
        mode: "replace",
        include_models: true,
        include_resources: true,
      };
      const serverPreview = await client.previewAdminEntitlementCopy(body);
      if (!previewChangedCount(serverPreview)) {
        await reload();
        throw new Error("服务端核对后没有需要复制的变化，矩阵已刷新。");
      }
      await client.executeAdminEntitlementCopy({
        ...body,
        expected_snapshot: serverPreview.snapshot,
        reason,
        idempotency_key: requestKey("entitlement-copy"),
      });
    } else if (preview.mode === "template") {
      const template = data?.entitlementTemplates?.find((item) => item.id === preview.templateId);
      if (!template?.cells?.length) {
        throw new Error("该套餐模板没有服务端可执行配置，请刷新模板目录。");
      }
      const body = {
        template_name: template.name,
        template_version: Number(template.version || 1),
        target_company_ids: [...new Set(preview.companyIds || [])],
        mode: template.mode || "replace",
        cells: template.cells,
      };
      const serverPreview = await client.previewAdminEntitlementTemplate(body);
      if (!previewChangedCount(serverPreview)) {
        await reload();
        throw new Error("服务端核对后没有需要套用的变化，矩阵已刷新。");
      }
      await client.executeAdminEntitlementTemplate({
        ...body,
        expected_snapshot: serverPreview.snapshot,
        reason,
        idempotency_key: requestKey("entitlement-template"),
      });
    } else {
      throw new Error("不支持的批量权益操作。");
    }
    await reload();
  }, [client, data, reload]);

  const saveAdminAccess = useCallback(async ({ adminId, permissions, reason }) => {
    const admin = data?.platformAdmins?.find((item) => item.id === adminId);
    if (!admin || admin.owner) throw new Error("平台所有者权限不可在此页面修改。");
    await client.replacePlatformAdministratorAccess(adminId, {
      role_ids: admin.access?.role_ids || [],
      permission_overrides: exactPermissionOverrides(admin, permissions, permissionCatalog),
      expected_lock_version: Number(admin.access?.lock_version || 0),
      change_reason: requireReason(reason),
    });
    await reload();
  }, [client, data, permissionCatalog, reload]);

  const exportAudit = useCallback(async ({ items }) => {
    const completed = downloadTextFile(
      `platform-audit-${new Date().toISOString().slice(0, 10)}.csv`,
      `\uFEFF${toAuditCsv(items)}`,
      "text/csv;charset=utf-8",
    );
    if (!completed) throw new Error("当前浏览器无法生成审计导出文件。");
  }, []);

  const canResolveException = useCallback((exception) => (
    canManageAssetExceptions
    && ["DOWNLOAD_REGISTRATION_UNKNOWN", "DOWNLOAD_REGISTRATION_FAILED"]
      .includes(exception?.category || exception?.raw?.category)
    && exception?.targetType === "download_gateway_registration_attempt"
    && Boolean(exception?.targetId)
  ), [canManageAssetExceptions]);

  const resolveException = useCallback(async ({ exception }) => {
    if (!canResolveException(exception)) {
      throw new Error("该异常必须按处理指引人工核对，系统不会自动核销或重试。");
    }
    await client.reconcileAdminDownloadGatewayAttempt(exception.targetId);
    await reload();
  }, [canResolveException, client, reload]);

  const loadRelayUnknownDetail = useCallback(async (item) => {
    if (!canReadRelayHealth || !item?.jobId) {
      throw new Error("当前平台管理员没有读取 Relay 未知提交详情的权限。");
    }
    const detail = await client.getAdminRelayUnknownSubmission(item.jobId);
    return adaptRelayUnknownSubmission(detail);
  }, [canReadRelayHealth, client]);

  const refreshRelayUnknown = useCallback(async ({ item, form }) => {
    if (!canReadRelayHealth || !item?.jobId) {
      throw new Error("当前平台管理员没有读取 Relay 未知提交处置结果的权限。");
    }
    const resolution = form ? buildRelayUnknownResolution(item, form) : null;
    const result = await refreshRelayUnknownOperation({
      item,
      resolution,
      getDetail: (jobId) => client.getAdminRelayUnknownSubmission(jobId),
      getResult: (jobId, operationId) => (
        client.getAdminRelayUnknownSubmissionResult(jobId, { operationId })
      ),
      expectedApprovedBy: resolution ? (identity?.user_id || "") : "",
    });
    if (result.state === "resolved") await reload();
    return result;
  }, [canReadRelayHealth, client, identity?.user_id, reload]);

  const resolveRelayUnknown = useCallback(async ({ item, form }) => {
    if (!canManageRelayHealth) {
      throw new Error("当前平台管理员没有审批 Relay 未知提交的权限。");
    }
    const body = buildRelayUnknownResolution(item, form);
    const result = await resolveRelayUnknownOperation({
      item,
      resolution: body,
      resolve: (jobId, resolution) => (
        client.resolveAdminRelayUnknownSubmission(jobId, resolution)
      ),
      getResult: (jobId, operationId) => (
        client.getAdminRelayUnknownSubmissionResult(jobId, { operationId })
      ),
      expectedApprovedBy: identity?.user_id || "",
    });
    await reload();
    return result;
  }, [canManageRelayHealth, client, identity?.user_id, reload]);

  const loadRelayCallbackDeadLetter = useCallback(async (item) => {
    if (!canReadRelayHealth || !item?.eventId) {
      throw new Error("当前平台管理员没有读取 callback 死信详情的权限。");
    }
    return adaptRelayCallbackDeadLetter(
      await client.getAdminRelayCallbackDeadLetter(item.eventId),
    );
  }, [canReadRelayHealth, client]);

  const redriveRelayCallbackDeadLetter = useCallback(async ({ item, form }) => {
    if (!canManageRelayHealth) {
      throw new Error("当前平台管理员没有重新投递 callback 的权限。");
    }
    const request = {
      ...buildRelayCallbackRedrive(item, form),
      // Kept in browser state so an ambiguous POST can be reconciled with GET
      // /result without submitting the side effect a second time.
      operation_id: requestKey("relay-callback-redrive-op"),
    };
    const result = await redriveRelayCallbackWithReadback({
      item,
      request,
      redrive: (eventId, body) => (
        client.redriveAdminRelayCallbackDeadLetter(eventId, body)
      ),
      getResult: (eventId, operationId) => (
        client.getAdminRelayCallbackRedriveResult(eventId, { operationId })
      ),
    });
    await reload();
    return result;
  }, [canManageRelayHealth, client, reload]);

  const loadRelayChannelDetail = useCallback(async (channel) => {
    if (!canReadRelayHealth || !channel?.id) {
      throw new Error("当前平台管理员没有读取 Relay 渠道详情的权限。");
    }
    const detail = adaptRelayControlChannel(
      await client.getAdminRelayChannel(channel.id),
    );
    if (!detail) throw new Error("Relay 渠道详情结构无效，请刷新列表后重试。");
    return detail;
  }, [canReadRelayHealth, client]);

  const operateRelayChannel = useCallback(async ({ channel, kind, values }) => {
    if (!canManageRelayHealth || !channel?.id) {
      throw new Error("当前平台管理员没有管理 Relay 渠道的权限。");
    }
    const request = buildRelayChannelOperationRequest(kind, values);
    const result = await runRelayChannelOperationWithReadback({
      channelId: channel.id,
      kind,
      request,
      submit: kind === "test"
        ? (channelId, body) => client.testAdminRelayChannel(channelId, body)
        : (channelId, body) => client.setAdminRelayChannelStatus(channelId, body),
      getResult: (channelId, operationId) => (
        client.getAdminRelayChannelOperation(channelId, operationId)
      ),
    });
    if (result.receipt.state !== "pending") await reload();
    return result;
  }, [canManageRelayHealth, client, reload]);

  const readRelayChannelOperation = useCallback(async ({ channel, kind, values }) => {
    if (!canReadRelayHealth || !channel?.id) {
      throw new Error("当前平台管理员没有读取 Relay 渠道操作结果的权限。");
    }
    const request = buildRelayChannelOperationRequest(kind, values);
    const receipt = await readRelayChannelOperationReceipt({
      channelId: channel.id,
      kind,
      request,
      getResult: (channelId, operationId) => (
        client.getAdminRelayChannelOperation(channelId, operationId)
      ),
    });
    if (receipt.state !== "pending") await reload();
    return receipt;
  }, [canReadRelayHealth, client, reload]);

  const authorizeRelayNativeConsole = useCallback(async () => {
    if (!canAuthorizeRelayNativeConsole) {
      throw new Error(relayNativeConsoleDisabledReason || "当前会话不能授权 Relay 高风险运维入口。");
    }
    return requestRelayNativeConsoleGrant({
      requestAccess: () => client.openAdminRelayNativeConsole(),
      allowLocalHttp: nativeConsoleEnvironment === "development",
    });
  }, [
    canAuthorizeRelayNativeConsole,
    client,
    nativeConsoleEnvironment,
    relayNativeConsoleDisabledReason,
  ]);

  return (
    <AdminOperationsConsole
      data={data}
      demoMode={demoMode}
      loading={loading}
      accessPending={!demoMode && loading && !identity}
      error={error}
      administrator={{
        name: identity?.display_name || "平台管理员",
        roleLabel: identity?.is_platform_owner ? "平台所有者" : "平台管理员",
      }}
      activeSection={activeSection}
      range={range}
      onSectionChange={updateActiveSection}
      visibleSections={visibleSections}
      canManageEntitlements={canManageEntitlements}
      canManageAdminAccess={canManageAdminAccess}
      canReadAudit={canReadAudit}
      canReadAdminAccess={canReadAdminAccess}
      environmentOptions={[
        demoMode ? "production" : (data?.summary?.environment || currentEnvironment()),
      ]}
      demoPersonaId={demoPersonaId}
      onDemoPersonaChange={onDemoPersonaChange}
      onLogout={onLogout}
      skin={skin}
      onSkinChange={onSkinChange}
      onOpenBasicConfig={basicSection && onOpenBasicConfig
        ? () => onOpenBasicConfig({ identity, section: basicSection, operationsContext: { activeSection, range } })
        : undefined}
      onRangeChange={updateRange}
      onRefresh={({ range: nextRange }) => reload(nextRange)}
      onEntitlementSave={!demoMode && canManageEntitlements ? saveEntitlement : undefined}
      onBatchEntitlementCommit={!demoMode && canManageEntitlements ? commitBatch : undefined}
      onAdminAccessSave={!demoMode && canManageAdminAccess ? saveAdminAccess : undefined}
      onAuditExport={!demoMode && canReadAudit ? exportAudit : undefined}
      onExceptionAction={demoMode ? undefined : resolveException}
      canResolveException={demoMode ? undefined : canResolveException}
      onModelOpen={canReadEntitlements ? () => updateActiveSection("entitlements") : undefined}
      onReliabilityAction={canReadRelayHealth ? () => updateActiveSection("channels") : undefined}
      onRelayChannelDetail={demoMode ? undefined : loadRelayChannelDetail}
      onRelayChannelOperation={demoMode ? undefined : operateRelayChannel}
      onRelayChannelOperationRead={demoMode ? undefined : readRelayChannelOperation}
      canManageRelayChannels={canManageRelayHealth}
      onRelayNativeConsoleAuthorize={canAuthorizeRelayNativeConsole
        ? authorizeRelayNativeConsole
        : undefined}
      canAuthorizeRelayNativeConsole={canAuthorizeRelayNativeConsole}
      relayNativeConsoleDisabledReason={relayNativeConsoleDisabledReason}
      relayNativeConsoleAccessScope={identity?.user_id || ""}
      onRelayUnknownDetail={demoMode ? undefined : loadRelayUnknownDetail}
      onRelayUnknownRefresh={demoMode ? undefined : refreshRelayUnknown}
      onRelayUnknownResolve={demoMode ? undefined : resolveRelayUnknown}
      canManageRelayUnknown={canManageRelayHealth}
      onRelayCallbackDeadLetterDetail={demoMode ? undefined : loadRelayCallbackDeadLetter}
      onRelayCallbackDeadLetterRedrive={demoMode ? undefined : redriveRelayCallbackDeadLetter}
      canManageRelayCallbackDeadLetters={canManageRelayHealth}
      isPlatformOwner={isPlatformOwner}
      showcaseContent={isPlatformOwner ? (
        <ShowcaseOperationsContainer
          active={activeSection === "showcase"}
          client={client}
          demoMode={demoMode}
          onAuthenticationError={onSessionError}
        />
      ) : null}
    />
  );
}

export default AdminOperationsContainer;
