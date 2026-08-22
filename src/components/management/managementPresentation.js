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

export function money(value) {
  const cents = Number(value) || 0;
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
