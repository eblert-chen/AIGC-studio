const INTEGER_FORMATTER = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 0,
});

const DECIMAL_FORMATTER = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const MONEY_FORMATTER = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function finiteNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function formatInteger(value) {
  return INTEGER_FORMATTER.format(finiteNumber(value));
}

export function formatCompactInteger(value) {
  const numeric = finiteNumber(value);
  if (Math.abs(numeric) >= 100_000_000) {
    return `${DECIMAL_FORMATTER.format(numeric / 100_000_000)}亿`;
  }
  if (Math.abs(numeric) >= 10_000) {
    return `${DECIMAL_FORMATTER.format(numeric / 10_000)}万`;
  }
  return formatInteger(numeric);
}

export function formatMoneyFromCents(value) {
  return MONEY_FORMATTER.format(finiteNumber(value) / 100);
}

export function formatMoneyYuan(value) {
  return MONEY_FORMATTER.format(finiteNumber(value));
}

export function formatPercent(value, digits = 1) {
  const numeric = finiteNumber(value);
  return `${numeric.toFixed(digits)}%`;
}

export function formatDurationSeconds(value) {
  const seconds = Math.max(0, finiteNumber(value));
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds >= 10 ? 1 : 2)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

export function formatTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

export function formatDateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

export function changeTone(value) {
  const numeric = finiteNumber(value);
  if (numeric > 0) return "up";
  if (numeric < 0) return "down";
  return "flat";
}

export function entitlementStateLabel(value) {
  return {
    enabled: "已开通",
    disabled: "已停用",
    unconfigured: "未配置",
    scheduled: "待生效",
    expiring: "即将到期",
    expired: "已过期",
    retired: "目录下线",
  }[value] || value || "未配置";
}

export function exceptionStatusLabel(value) {
  return {
    open: "待处理",
    investigating: "处理中",
    acknowledged: "已确认",
    resolved: "已解决",
  }[value] || value || "待处理";
}

export function riskLabel(value) {
  return {
    critical: "高风险",
    warning: "需关注",
    healthy: "健康",
    inactive: "不活跃",
  }[value] || value || "未知";
}

export function buildEntitlementKey(companyId, productId) {
  return `${String(companyId)}::${String(productId)}`;
}

export function resolveEntitlementState(grants, companyId, productId) {
  const grant = grants?.[buildEntitlementKey(companyId, productId)];
  return grant?.state || "unconfigured";
}

export function summarizeBatchImpact({
  companies = [],
  products = [],
  grants = {},
  companyIds = [],
  productIds = [],
  nextState = "enabled",
}) {
  const companyIdSet = new Set(companyIds.map(String));
  const productIdSet = new Set(productIds.map(String));
  const selectedCompanies = companies.filter((item) => companyIdSet.has(String(item.id)));
  const selectedProducts = products.filter((item) => productIdSet.has(String(item.id)));
  const changes = [];

  selectedCompanies.forEach((company) => {
    selectedProducts.forEach((product) => {
      const previousState = resolveEntitlementState(grants, company.id, product.id);
      if (previousState !== nextState) {
        changes.push({
          companyId: company.id,
          companyName: company.name,
          productId: product.id,
          productName: product.name,
          previousState,
          nextState,
        });
      }
    });
  });

  return {
    companyCount: selectedCompanies.length,
    productCount: selectedProducts.length,
    cellCount: selectedCompanies.length * selectedProducts.length,
    changedCount: changes.length,
    unchangedCount: selectedCompanies.length * selectedProducts.length - changes.length,
    changes,
  };
}

export function filterExceptions(items, { query = "", status = "all", priority = "all" } = {}) {
  const normalizedQuery = query.trim().toLocaleLowerCase("zh-CN");
  return (items || []).filter((item) => {
    if (status !== "all" && item.status !== status) return false;
    if (priority !== "all" && item.priority !== priority) return false;
    if (!normalizedQuery) return true;
    return [item.title, item.description, item.owner, item.kind]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase("zh-CN").includes(normalizedQuery));
  });
}

export function toAuditCsv(items = []) {
  const quote = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const header = ["时间", "操作者", "动作", "目标", "变更原因", "结果"];
  const rows = items.map((item) => [
    item.occurredAt,
    item.actorName,
    item.actionLabel,
    item.targetLabel,
    item.reason,
    item.result,
  ]);
  return [header, ...rows].map((row) => row.map(quote).join(",")).join("\n");
}

export function downloadTextFile(filename, text, mime = "text/plain;charset=utf-8") {
  if (typeof document === "undefined" || typeof URL === "undefined") return false;
  const blob = new Blob([text], { type: mime });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(href);
  return true;
}

export function createEmptyOperationsData() {
  return {
    summary: {},
    taskFlow: [],
    timings: [],
    trends: [],
    failureTrends: [],
    failureReasons: [],
    latencyDistribution: [],
    exceptions: [],
    reliability: [],
    business: { metrics: [], trend: [], companyRanking: [] },
    modelProfitability: [],
    companyHealth: [],
    channels: [],
    relayChannels: [],
    relayChannelSourceStatus: "unavailable",
    relayChannelTotal: null,
    channelSummary: null,
    relayUnknownSubmissions: [],
    relayUnknownSubmissionSourceStatus: "unavailable",
    relayUnknownSubmissionPage: 1,
    relayUnknownSubmissionPageSize: 0,
    relayUnknownSubmissionTotal: null,
    relayCallbackDeadLetters: [],
    relayCallbackDeadLetterSourceStatus: "unavailable",
    relayCallbackDeadLetterTotal: null,
    dataReadiness: null,
    publishingExceptions: [],
    assetExceptions: [],
    companies: [],
    entitlementProducts: [],
    entitlementTemplates: [],
    entitlementCoverage: [],
    entitlementGrants: {},
    auditEvents: [],
    platformAdmins: [],
    adminPermissionCatalog: [],
  };
}

export function mergeOperationsData(data) {
  const empty = createEmptyOperationsData();
  const merged = {
    ...empty,
    ...(data || {}),
    summary: { ...empty.summary, ...(data?.summary || {}) },
    business: { ...empty.business, ...(data?.business || {}) },
  };
  [
    "taskFlow",
    "timings",
    "trends",
    "failureTrends",
    "failureReasons",
    "latencyDistribution",
    "exceptions",
    "reliability",
    "modelProfitability",
    "companyHealth",
    "channels",
    "relayChannels",
    "relayUnknownSubmissions",
    "relayCallbackDeadLetters",
    "publishingExceptions",
    "assetExceptions",
    "companies",
    "entitlementProducts",
    "entitlementTemplates",
    "entitlementCoverage",
    "auditEvents",
    "platformAdmins",
    "adminPermissionCatalog",
  ].forEach((key) => {
    if (!Array.isArray(merged[key])) merged[key] = [];
  });
  ["metrics", "trend", "companyRanking"].forEach((key) => {
    if (!Array.isArray(merged.business[key])) merged.business[key] = [];
  });
  if (!merged.entitlementGrants || typeof merged.entitlementGrants !== "object" || Array.isArray(merged.entitlementGrants)) {
    merged.entitlementGrants = {};
  }
  return merged;
}
