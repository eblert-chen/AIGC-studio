function positiveFinite(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

function safeInteger(value) {
  if (
    value === null
    || value === undefined
    || typeof value === "boolean"
    || (typeof value === "string" && value.trim() === "")
  ) {
    return null;
  }
  const number = Number(value);
  return Number.isSafeInteger(number) ? number : null;
}

const POINT_FORMATTER = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 0,
});

const CNY_FORMATTER = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export const BILLING_UNIT_POINT = "POINT";
export const BILLING_UNIT_CNY_CENT = "CNY_CENT";
export const BILLING_UNIT_MIXED = "MIXED";
export const POINT_BILLING_VERSION = 2;

function normalizedUnit(value) {
  const unit = String(value ?? "").trim().toUpperCase();
  return [BILLING_UNIT_POINT, BILLING_UNIT_CNY_CENT, BILLING_UNIT_MIXED].includes(unit)
    ? unit
    : "";
}

function normalizedScope(value) {
  return String(value ?? "").trim().toLowerCase();
}

export function billingPresentationState(source = {}) {
  const evidence = source && typeof source === "object" ? source : {};
  const snakeUnitDeclared = Object.hasOwn(evidence, "billing_unit");
  const camelUnitDeclared = Object.hasOwn(evidence, "billingUnit");
  const snakeVersionDeclared = Object.hasOwn(evidence, "billing_version");
  const camelVersionDeclared = Object.hasOwn(evidence, "billingVersion");
  const unitAliasesAgree = !snakeUnitDeclared || !camelUnitDeclared
    || normalizedUnit(evidence.billing_unit) === normalizedUnit(evidence.billingUnit);
  const snakeVersion = safeInteger(evidence.billing_version);
  const camelVersion = safeInteger(evidence.billingVersion);
  const versionAliasesAgree = !snakeVersionDeclared || !camelVersionDeclared
    || (
      evidence.billing_version === null && evidence.billingVersion === null
      || snakeVersion !== null && snakeVersion === camelVersion
    );
  const rawBillingUnit = snakeUnitDeclared ? evidence.billing_unit : evidence.billingUnit;
  const rawBillingVersion = snakeVersionDeclared
    ? evidence.billing_version
    : evidence.billingVersion;
  const billingUnit = normalizedUnit(rawBillingUnit);
  const billingVersion = safeInteger(rawBillingVersion);
  const billingScope = normalizedScope(evidence.billing_scope ?? evidence.billingScope);
  const internalTest = ["internal_test", "platform_test"].includes(billingScope);

  if (!unitAliasesAgree || !versionAliasesAgree) {
    return {
      kind: "unverified",
      billingUnit,
      billingVersion,
      billingScope,
      available: false,
    };
  }

  if (billingUnit === BILLING_UNIT_POINT && billingVersion === POINT_BILLING_VERSION) {
    return {
      kind: internalTest ? "internal_test" : "points",
      billingUnit,
      billingVersion,
      billingScope,
      available: true,
    };
  }
  if (billingUnit === BILLING_UNIT_CNY_CENT && billingVersion === 1) {
    return {
      kind: "legacy_cents",
      billingUnit,
      billingVersion,
      billingScope,
      available: true,
    };
  }
  const mixedVersionIsExplicitlyNull = (snakeVersionDeclared || camelVersionDeclared)
    && (!snakeVersionDeclared || evidence.billing_version === null)
    && (!camelVersionDeclared || evidence.billingVersion === null);
  if (billingUnit === BILLING_UNIT_MIXED && mixedVersionIsExplicitlyNull) {
    return {
      kind: "mixed",
      billingUnit,
      billingVersion,
      billingScope,
      // MIXED is valid only for a collection with separately evidenced totals.
      // It can never authorize a task quote or format one scalar amount.
      available: false,
    };
  }
  return {
    kind: "unverified",
    billingUnit,
    billingVersion,
    billingScope,
    available: false,
  };
}

export function formatPointAmount(value, { noun = "积分", signed = false } = {}) {
  const amount = safeInteger(value);
  if (amount === null) return "积分数据不可用";
  const sign = signed && amount > 0 ? "+" : "";
  return `${sign}${POINT_FORMATTER.format(amount)} ${noun}`;
}

export function formatLegacyCents(value, { signed = false } = {}) {
  const cents = safeInteger(value);
  if (cents === null) return "历史计费数据不可用";
  const sign = signed && cents > 0 ? "+" : "";
  return `${sign}${CNY_FORMATTER.format(cents / 100)}（历史人民币计费）`;
}

export function billingAmountLabel(
  source,
  {
    pointsField = "amount_points",
    centsField = "amount_cents",
    signed = false,
    pointNoun,
    unavailableLabel = "计费单位待确认",
  } = {},
) {
  const state = billingPresentationState(source);
  if (state.kind === "legacy_cents") {
    return formatLegacyCents(source?.[centsField], { signed });
  }
  if (state.kind === "points" || state.kind === "internal_test") {
    const noun = pointNoun || (state.kind === "internal_test" ? "测试积分" : "积分");
    return formatPointAmount(source?.[pointsField], { noun, signed });
  }
  return unavailableLabel;
}

export function billingUnitLabel(source = {}) {
  const state = billingPresentationState(source);
  if (state.kind === "points") return "积分计费 v2";
  if (state.kind === "internal_test") return "内部测试预算 · 不形成客户收入";
  if (state.kind === "legacy_cents") return "历史人民币计费 · 迁移锁定";
  if (state.kind === "mixed") return "迁移期分单位汇总";
  return "计费单位待确认";
}

export function billingTotalsLabel(
  source,
  {
    pointsField = "total_amount_points",
    centsField = "total_amount_cents",
    unavailableLabel = "计费汇总待确认",
  } = {},
) {
  const state = billingPresentationState(source);
  if (state.kind === "points" || state.kind === "internal_test") {
    const points = safeInteger(source?.[pointsField]);
    if (points === null || points < 0) return unavailableLabel;
    return formatPointAmount(points, {
      noun: state.kind === "internal_test" ? "测试积分" : "积分",
    });
  }
  if (state.kind === "legacy_cents") {
    const cents = safeInteger(source?.[centsField]);
    return cents !== null && cents >= 0 ? formatLegacyCents(cents) : unavailableLabel;
  }
  if (state.kind === "mixed") {
    const points = safeInteger(source?.[pointsField]);
    const cents = safeInteger(source?.[centsField]);
    if (points === null || points < 0 || cents === null || cents < 0) {
      return unavailableLabel;
    }
    return `${formatPointAmount(points)} + ${formatLegacyCents(cents)}`;
  }
  return unavailableLabel;
}

export function normalizePositivePrice(value) {
  return positiveFinite(value);
}

export function generationCostPreview({
  historicalRequestLocked = false,
  liveMode = false,
  model,
  duration,
  outputCount,
} = {}) {
  if (historicalRequestLocked) return { available: false, label: "原提交待确认", value: null };
  if (!model?.id) return { available: false, label: "待选择可用模型", value: null };

  const quantity = positiveFinite(
    model.pricingMode === "per_second" ? duration : outputCount,
  );
  const billing = billingPresentationState(model);
  if (liveMode && billing.kind === "legacy_cents") {
    return { available: false, label: "历史人民币计费 · 请先迁移", value: null };
  }
  if (liveMode && !billing.available) {
    return { available: false, label: "计费单位待确认", value: null };
  }

  const unitPrice = positiveFinite(liveMode ? model.unitPricePoints : model.rate);
  if (quantity === null || unitPrice === null) {
    return { available: false, label: "价格待核验", value: null };
  }

  const value = unitPrice * quantity;
  if (!Number.isSafeInteger(value) || value <= 0) {
    return { available: false, label: "价格待核验", value: null };
  }
  return {
    available: true,
    label: formatPointAmount(value, {
      noun: liveMode && billing.kind === "internal_test" ? "影子积分" : "积分",
    }),
    value,
  };
}

export function pointBalanceLabel(value) {
  const number = safeInteger(value);
  return number !== null && number >= 0
    ? formatPointAmount(number)
    : "积分数据不可用";
}
