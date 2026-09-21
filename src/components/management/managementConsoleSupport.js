import {
  billingPresentationState,
} from "../../billingPresentation.js";
import {
  GENERATION_MODES,
  toCanonicalGenerationConfig,
} from "../../modelCapabilities.js";
import { MANAGEMENT_PAGE_SIZE } from "./legacy/managementSnapshot.js";

const POINT_FORMATTER = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });

export function copyableInvitationUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const url = new URL(raw, globalThis.location?.origin);
    if (
      url.origin !== globalThis.location?.origin
      || url.pathname !== "/invite"
      || !url.hash.startsWith("#token=")
      || url.search
    ) return "";
    return url.href;
  } catch {
    return "";
  }
}

export function byteCount(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "未回传";
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function localDateTimeInput(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

export function grossMarginLabel(incomeCents, grossProfitCents) {
  const income = Number(incomeCents) || 0;
  if (income <= 0) return "0.00%";
  return `${((Number(grossProfitCents || 0) / income) * 100).toFixed(2)}%`;
}

export function makeOperationKey(prefix) {
  const unique = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${unique}`;
}

export function personalPointBalanceLabel(value) {
  if (value === null || value === undefined || value === "") return "积分未确认";
  const amount = Number(value);
  return Number.isSafeInteger(amount) && amount >= 0
    ? `${POINT_FORMATTER.format(amount)} 积分`
    : "积分未确认";
}

export function billingNumericValue(source, pointsField, centsField) {
  const billing = billingPresentationState(source);
  const value = billing.kind === "legacy_cents"
    ? source?.[centsField]
    : billing.available
      ? source?.[pointsField]
      : null;
  const amount = Number(value);
  return Number.isSafeInteger(amount) ? amount : null;
}

export function personalPointGrantEligibilityError(user) {
  if (user?.account_type === "platform_admin") {
    return "平台管理员账号只进入 Platform，不能领取个人创作积分";
  }
  if (user?.account_type === "company") {
    return "企业账号使用企业共享钱包，不能领取个人创作积分";
  }
  if (user?.account_type === "unavailable") {
    return "该账号类型当前不可用，不能领取个人创作积分";
  }
  if (!user?.personal_workspace_id) {
    return "该账号尚未完成个人空间初始化，不能赠送积分";
  }
  if (user.personal_workspace_active !== true) {
    return "该账号的个人空间已停用，不能赠送积分";
  }
  if (user.status !== "active") {
    return "该账号当前未启用，恢复账号后才能赠送积分";
  }
  if (
    user.available_points === null
    || user.available_points === undefined
    || user.reserved_points === null
    || user.reserved_points === undefined
    || !Number.isSafeInteger(Number(user.available_points))
    || !Number.isSafeInteger(Number(user.reserved_points))
  ) {
    return "该账号的个人积分钱包状态尚未确认，请刷新后重试";
  }
  return "";
}

export function reportApiFilters(filters) {
  const normalized = { ...filters };
  for (const key of ["start_time", "end_time"]) {
    if (!normalized[key]) continue;
    const value = new Date(normalized[key]);
    normalized[key] = Number.isNaN(value.getTime()) ? "" : value.toISOString();
  }
  return normalized;
}

export function dateMatches(value, startTime, endTime) {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return !startTime && !endTime;
  const start = startTime ? new Date(startTime).getTime() : null;
  const end = endTime ? new Date(endTime).getTime() : null;
  return (start == null || timestamp >= start) && (end == null || timestamp <= end);
}

function parseIntegerList(value, label, { min = 1, max = 3600 } = {}) {
  const items = String(value ?? "")
    .split(/[，,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map(Number);
  if (
    !items.length
    || items.some((item) => !Number.isInteger(item) || item < min || item > max)
  ) {
    throw new Error(`${label}需要填写 ${min}-${max} 之间的整数，可用逗号分隔。`);
  }
  return [...new Set(items)].sort((left, right) => left - right);
}

function parseResourceKeys(value) {
  const keys = String(value ?? "")
    .split(/[，,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const invalid = keys.find(
    (item) => !/^[a-z0-9][a-z0-9._-]{1,118}[a-z0-9]$/.test(item),
  );
  if (invalid) {
    throw new Error(`资源 Key “${invalid}”格式不正确。`);
  }
  return [...new Set(keys)];
}

function parseCapabilityStringList(value, label, pattern) {
  const items = String(value ?? "")
    .split(/[，,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const invalid = items.find((item) => !pattern.test(item));
  if (!items.length || invalid) {
    throw new Error(`${label}格式不正确，请使用逗号分隔有效值。`);
  }
  return [...new Set(items)];
}

export function readCapabilityEditor(form) {
  const selectedModes = form.getAll("capabilityModes").map(String);
  if (!selectedModes.length) {
    throw new Error("请至少启用一种生成模式。");
  }
  if (selectedModes.some((mode) => !GENERATION_MODES.some((item) => item.id === mode))) {
    throw new Error("生成模式不在平台支持范围内。");
  }
  const modes = {};
  for (const mode of selectedModes) {
    const prefix = `cap.${mode}`;
    const maxImages = Number(form.get(`${prefix}.maxImages`));
    const maxVideos = Number(form.get(`${prefix}.maxVideos`));
    const maxAudio = Number(form.get(`${prefix}.maxAudio`));
    const maxPromptLength = Number(form.get(`${prefix}.maxPromptLength`));
    const integerLimits = [maxImages, maxVideos, maxAudio, maxPromptLength];
    if (integerLimits.some((value) => !Number.isInteger(value) || value < 0)) {
      throw new Error("素材数量和提示词上限必须填写整数。");
    }
    if (maxPromptLength < 1 || maxPromptLength > 10_000) {
      throw new Error("提示词上限需要在 1-10000 之间。");
    }
    if (maxImages + maxVideos + maxAudio > 15) {
      throw new Error("单个模式的图片、视频和音频输入合计不能超过 15 个。");
    }
    if (mode === "image_to_video" && maxImages < 1) {
      throw new Error("图生视频至少需要支持 1 张图片。");
    }
    if (mode === "video_to_video" && maxVideos < 1) {
      throw new Error("视频参考至少需要支持 1 个视频。");
    }
    const aspectRatios = parseCapabilityStringList(
      form.get(`${prefix}.aspectRatios`),
      "画面比例",
      /^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$/,
    );
    const resolutions = parseCapabilityStringList(
      form.get(`${prefix}.resolutions`),
      "分辨率",
      /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/,
    );
    const outputCounts = form
      .getAll(`${prefix}.outputCounts`)
      .map(Number)
      .filter(Number.isInteger);
    if (
      !aspectRatios.length
      || !resolutions.length
      || !outputCounts.length
      || outputCounts.some((value) => value < 1 || value > 16)
    ) {
      throw new Error("每种模式都要选择比例、分辨率和 1-16 个产物数。");
    }
    if (
      form.get("billingMode") === "per_second"
      && (outputCounts.length !== 1 || outputCounts[0] !== 1)
    ) {
      throw new Error("按秒计费模型的每种模式只能选择 1 个产物。");
    }
    const inputMediaTypes = [
      maxImages > 0 ? "image" : "",
      maxVideos > 0 ? "video" : "",
      maxAudio > 0 ? "audio" : "",
    ].filter(Boolean);
    const supportsFace = form.get(`${prefix}.supportsFace`) === "on";
    const faceRequiredResourceKeys = supportsFace
      ? parseResourceKeys(form.get(`${prefix}.faceRequiredResourceKeys`))
      : [];
    modes[mode] = {
      inputMediaTypes,
      supportsFace,
      requiredResourceKeys: parseResourceKeys(form.get(`${prefix}.requiredResourceKeys`)),
      conditionalRequiredResourceKeys: {
        faceEnabled: supportsFace ? faceRequiredResourceKeys : [],
      },
      limits: {
        maxPromptLength,
        maxImages,
        maxVideos,
        maxAudio,
        durations: parseIntegerList(form.get(`${prefix}.durations`), "时长"),
        aspectRatios,
        resolutions,
        outputCounts: [...new Set(outputCounts)].sort((left, right) => left - right),
      },
    };
  }
  return toCanonicalGenerationConfig(modes);
}

export function pricingQuantityLabel(item) {
  if (item.quantity == null) return "-";
  return `${item.quantity}${item.pricing_mode === "per_second" ? " 秒" : " 条"}`;
}

export function mergePageRecords(currentPage, nextPage, identityKeys) {
  const currentItems = currentPage?.items || [];
  const nextItems = nextPage?.items || [];
  const keys = Array.isArray(identityKeys) ? identityKeys : [identityKeys];
  const itemKey = (item) => {
    for (const key of keys) {
      if (item?.[key] != null) return `${key}:${item[key]}`;
    }
    return JSON.stringify(item);
  };
  const merged = new Map(currentItems.map((item) => [itemKey(item), item]));
  nextItems.forEach((item) => merged.set(itemKey(item), item));
  return {
    ...currentPage,
    ...nextPage,
    items: [...merged.values()],
  };
}

export function normalizePageCollection(payload, fallbackPageSize = MANAGEMENT_PAGE_SIZE) {
  const items = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.items) ? payload.items : [];
  return {
    ...(payload && !Array.isArray(payload) ? payload : {}),
    page: Number(payload?.page || 1),
    page_size: Number(payload?.page_size || Math.max(items.length, fallbackPageSize)),
    total: Number(payload?.total ?? items.length),
    items,
  };
}

export function managementSectionParam(mode) {
  return mode === "platform" ? "platform_config" : "company_section";
}

export function sectionFromLocation(mode, fallback) {
  try {
    const value = new URLSearchParams(globalThis.location?.search || "")
      .get(managementSectionParam(mode));
    return value || fallback;
  } catch {
    return fallback;
  }
}
