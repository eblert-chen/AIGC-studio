import { billingPresentationState } from "../../billingPresentation.js";

export const RELAY_CAPABILITY_STATUS_LABELS = {
  identical: "能力一致",
  compatible_restriction: "平台安全收紧",
  unsafe_expansion: "超出中转能力",
  platform_unconfigured: "平台能力未配置",
  unmapped: "平台未映射",
};

export const RESOURCE_KIND_LABELS = {
  feature: "平台功能",
  agent: "智能体",
  external_api: "外部 API",
};

export const CHANNEL_TYPE_LABELS = {
  reverse: "逆向渠道",
  third_party_api: "第三方 API",
  official: "官方渠道",
};

const PLATFORM_FINANCE_AMOUNT_FIELDS = [
  "platform_recharge_cents",
  "platform_income_cents",
  "channel_cost_cents",
  "known_gross_profit_cents",
];

const PLATFORM_POINT_AMOUNT_FIELDS = [
  "platform_recharge_points",
  "platform_consumption_points",
];

const PLATFORM_FINANCE_COUNT_FIELDS = [
  "unreconciled_succeeded_count",
  "unattributed_point_settlement_count",
  "active_company_count",
  "total_task_count",
  "succeeded_task_count",
  "failed_task_count",
  "total_companies",
  "page",
  "page_size",
];

function hasFiniteNumber(value) {
  return value !== null
    && value !== undefined
    && value !== ""
    && Number.isFinite(Number(value));
}

function hasNonNegativeInteger(value) {
  const number = Number(value);
  return hasFiniteNumber(value) && Number.isSafeInteger(number) && number >= 0;
}

function hasPositiveInteger(value) {
  return hasNonNegativeInteger(value) && Number(value) > 0;
}

function hasWalletEvidence(wallet) {
  const billing = billingPresentationState(wallet);
  if (billing.kind === "points" || billing.kind === "internal_test") {
    return hasNonNegativeInteger(wallet?.available_points)
      && hasNonNegativeInteger(wallet?.reserved_points);
  }
  if (billing.kind === "legacy_cents") {
    return hasNonNegativeInteger(wallet?.available_cents)
      && hasNonNegativeInteger(wallet?.reserved_cents);
  }
  return false;
}

function hasPageEvidence(page, totalField) {
  return Boolean(page)
    && hasPositiveInteger(page.page)
    && hasPositiveInteger(page.page_size)
    && hasNonNegativeInteger(page.total)
    && (!totalField || hasNonNegativeInteger(page[totalField]))
    && Array.isArray(page.items);
}

function hasBillingPageEvidence(page, pointTotalField, legacyTotalField) {
  const billing = billingPresentationState(page);
  const itemKinds = Array.isArray(page?.items)
    ? page.items.map((item) => billingPresentationState(item).kind)
    : [];
  if (billing.kind === "points" || billing.kind === "internal_test") {
    return hasPageEvidence(page, pointTotalField)
      && itemKinds.every((kind) => kind === "points" || kind === "internal_test");
  }
  if (billing.kind === "legacy_cents") {
    return hasPageEvidence(page, legacyTotalField)
      && itemKinds.every((kind) => kind === "legacy_cents");
  }
  if (billing.kind === "mixed") {
    return hasPageEvidence(page)
      && hasNonNegativeInteger(page?.[pointTotalField])
      && hasNonNegativeInteger(page?.[legacyTotalField])
      && itemKinds.every((kind) => (
        kind === "points" || kind === "internal_test" || kind === "legacy_cents"
      ));
  }
  return false;
}

export function hasPlatformFinanceEvidence(dashboard) {
  if (!dashboard || typeof dashboard !== "object" || Array.isArray(dashboard)) return false;
  const billing = billingPresentationState(dashboard);
  if (!["points", "legacy_cents", "mixed"].includes(billing.kind)) return false;
  if (!["complete", "incomplete"].includes(dashboard.channel_cost_status)) return false;
  if (!["complete", "incomplete"].includes(dashboard.revenue_reconciliation_status)) return false;
  if (!["complete", "incomplete"].includes(dashboard.finance_status)) return false;
  if (!PLATFORM_FINANCE_AMOUNT_FIELDS.every((field) => hasFiniteNumber(dashboard[field]))) return false;
  if (!PLATFORM_POINT_AMOUNT_FIELDS.every((field) => hasNonNegativeInteger(dashboard[field]))) return false;
  if (!PLATFORM_FINANCE_COUNT_FIELDS.every((field) => hasNonNegativeInteger(dashboard[field]))) return false;
  if (!hasPositiveInteger(dashboard.page) || !hasPositiveInteger(dashboard.page_size)) return false;
  if (!Array.isArray(dashboard.channel_costs) || !Array.isArray(dashboard.companies)) return false;
  if (!dashboard.companies.every((company) => {
    const companyBilling = billingPresentationState(company);
    if (!["points", "legacy_cents"].includes(companyBilling.kind)) return false;
    return [
      "recharge_cents",
      "consumption_cents",
      "available_cents",
      "reserved_cents",
      "recharge_points",
      "consumption_points",
      "available_points",
      "reserved_points",
      "task_count",
      "succeeded_count",
      "failed_count",
    ].every((field) => hasNonNegativeInteger(company[field]));
  })) return false;
  const income = Number(dashboard.platform_income_cents);
  const cost = Number(dashboard.channel_cost_cents);
  const knownGrossProfit = Number(dashboard.known_gross_profit_cents);
  const unreconciled = Number(dashboard.unreconciled_succeeded_count);
  const unattributedPointSettlements = Number(
    dashboard.unattributed_point_settlement_count,
  );
  if (knownGrossProfit !== income - cost) return false;
  if (Number(dashboard.active_company_count) > Number(dashboard.total_companies)) return false;
  if (Number(dashboard.succeeded_task_count) + Number(dashboard.failed_task_count) > Number(dashboard.total_task_count)) return false;
  const costComplete = dashboard.channel_cost_status === "complete";
  const revenueComplete = dashboard.revenue_reconciliation_status === "complete";
  const financeComplete = dashboard.finance_status === "complete";
  if ((costComplete && unreconciled !== 0) || (!costComplete && unreconciled === 0)) return false;
  if (
    (revenueComplete && unattributedPointSettlements !== 0)
    || (!revenueComplete && unattributedPointSettlements === 0)
  ) return false;
  if (financeComplete !== (costComplete && revenueComplete)) return false;
  if (financeComplete) {
    if (!hasFiniteNumber(dashboard.gross_profit_cents)) return false;
    if (Number(dashboard.gross_profit_cents) !== knownGrossProfit) return false;
  } else if (dashboard.gross_profit_cents !== null) {
    return false;
  }
  return true;
}

export function hasCompanyOverviewEvidence({ wallet, taskReport, succeededTaskReport } = {}) {
  return hasWalletEvidence(wallet)
    && hasBillingPageEvidence(taskReport, "total_actual_cost_points", "total_actual_cost_cents")
    && hasBillingPageEvidence(succeededTaskReport, "total_actual_cost_points", "total_actual_cost_cents");
}

export function hasCompanyWalletEvidence({ wallet, ledger, recharges } = {}) {
  return hasWalletEvidence(wallet)
    && Array.isArray(ledger)
    && hasBillingPageEvidence(recharges, "total_amount_points", "total_amount_cents");
}

export function hasCompanyReportsEvidence({ taskReport, consumption, downloads } = {}) {
  return hasBillingPageEvidence(taskReport, "total_actual_cost_points", "total_actual_cost_cents")
    && hasBillingPageEvidence(consumption, "total_amount_points", "total_amount_cents")
    && hasPageEvidence(downloads);
}

export function hasPlatformConsumptionEvidence({ adminConsumption } = {}) {
  return hasBillingPageEvidence(adminConsumption, "total_amount_points", "total_amount_cents");
}

export function hasPlatformOverviewEvidence({ dashboard, channelCosts } = {}) {
  return hasPlatformFinanceEvidence(dashboard)
    && hasPageEvidence(channelCosts, "total_amount_cents");
}

export function money(value) {
  if (value === null || value === undefined || value === "") return "金额未提供";
  const cents = Number(value);
  if (!Number.isFinite(cents)) return "金额不可用";
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
  }).format(cents / 100);
}

export function shortDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function pricingModeLabel(mode) {
  if (mode === "per_second") return "按秒";
  if (mode === "per_item") return "按条";
  return "-";
}
