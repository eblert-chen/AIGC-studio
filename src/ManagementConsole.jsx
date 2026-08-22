import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  Buildings,
  ChartLineUp,
  Check,
  DownloadSimple,
  FilmSlate,
  Gauge,
  Key,
  ListBullets,
  Plus,
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
  GENERATION_MODES,
  defaultEditorCapability,
  resolveEffectiveCapabilities,
  toCanonicalGenerationConfig,
} from "./modelCapabilities.js";
import { downloadRecordState } from "./taskArtifacts.js";
import {
  ACTIVE_PERMISSION_CATALOG,
  requirePermissionCatalog,
} from "./permissionCatalog.js";
import { DemoAccountSwitcher } from "./DemoAccountSwitcher.jsx";
import { normalizeSkin, SkinSwitcher } from "./SkinSwitcher.jsx";
import { BrandLogo, BRAND_NAME } from "./BrandLogo.jsx";
import { canOpenCompanyConsoleSection } from "./companyConsolePermissions.js";
import { MemberAccessFields } from "./components/management/MemberAccessFields.jsx";
import {
  CollectionState,
  EmptyRows,
  ManagementAccessState,
  ModelCapabilitySummary,
  PageHeader,
  PageLoadMore,
  PermissionNotice,
  PrimaryButton,
  QuietButton,
  STATUS_LABELS,
  StatusPill,
  SummaryStrip,
} from "./components/management/ManagementPrimitives.jsx";
import {
  memberAccessStateKey,
  permissionOverrideMap,
  roleList,
  safeId,
  withMemberPermissionState,
} from "./components/management/managementAccess.js";
import {
  CopyIdentifier,
  PlatformAuditView,
  PlatformCompaniesView,
  PlatformModelsView,
  PlatformResourcesView,
} from "./components/management/PlatformCatalogViews.jsx";
import {
  CHANNEL_TYPE_LABELS,
  RESOURCE_KIND_LABELS,
  money,
  pricingModeLabel,
  shortDate,
} from "./components/management/managementPresentation.js";

const LazyAdminOperationsContainer = React.lazy(
  () => import("./admin/AdminOperationsContainer.jsx"),
);

const PERMISSIONS = ACTIVE_PERMISSION_CATALOG.map(
  ({ code, description }) => [code, description],
);

const DEVELOPMENT_DEMO_FIXTURES = !import.meta.env.PROD;
const MANAGEMENT_PAGE_SIZE = 50;

const DEMO_MEMBERS = DEVELOPMENT_DEMO_FIXTURES ? [
  {
    user_id: "usr-zhangfan",
    membership_id: "mem-owner",
    display_name: "张帆",
    email: "zhangfan@example.cn",
    status: "active",
    roles: [{ id: "role-owner", name: "老板", system_key: "owner" }],
  },
  {
    user_id: "usr-linyao",
    membership_id: "mem-lead",
    display_name: "林瑶",
    email: "linyao@example.cn",
    status: "active",
    roles: [{ id: "role-lead", name: "组长", system_key: "team_lead" }],
  },
  {
    user_id: "usr-chenmo",
    membership_id: "mem-operator",
    display_name: "陈默",
    email: "chenmo@example.cn",
    status: "active",
    roles: [{ id: "role-operator", name: "运营", system_key: "operator" }],
    permission_overrides: [
      { permission_code: "assets.manage", effect: "deny" },
      { permission_code: "reports.read", effect: "allow" },
    ],
  },
  {
    user_id: "usr-songyu",
    membership_id: "mem-review",
    display_name: "宋宇",
    email: "songyu@example.cn",
    status: "disabled",
    roles: [
      { id: "role-operator", name: "运营", system_key: "operator" },
      { id: "role-review", name: "审阅者" },
    ],
  },
] : [];

const DEMO_ROLES = DEVELOPMENT_DEMO_FIXTURES ? [
  {
    id: "role-owner",
    name: "老板",
    description: "拥有公司范围内的全部权限",
    is_system: true,
    system_key: "owner",
    permission_codes: PERMISSIONS.map(([code]) => code),
  },
  {
    id: "role-lead",
    name: "组长",
    description: "管理制作任务与成员协作",
    is_system: true,
    system_key: "team_lead",
    permission_codes: [
      "users.read",
      "assets.read",
      "assets.manage",
      "models.read",
      "resources.read",
      "tasks.read",
      "tasks.create",
      "publish.accounts.read",
      "publish.accounts.manage",
      "publish.jobs.read",
      "publish.jobs.manage",
    ],
  },
  {
    id: "role-operator",
    name: "运营",
    description: "使用已授权模型创建与查看任务",
    is_system: true,
    system_key: "operator",
    permission_codes: [
      "assets.read",
      "assets.manage",
      "models.read",
      "resources.read",
      "tasks.read",
      "tasks.create",
      "publish.accounts.read",
      "publish.jobs.read",
      "publish.jobs.manage",
    ],
  },
  {
    id: "role-review",
    name: "审阅者",
    description: "只查看任务与报表",
    is_system: false,
    permission_codes: ["tasks.read", "reports.read"],
  },
] : [];

const DEMO_TASKS = DEVELOPMENT_DEMO_FIXTURES ? [
  {
    task_id: "tsk-9d21c7c4",
    employee_user_id: "usr-chenmo",
    employee_display_name: "陈默",
    employee_email: "chenmo@example.cn",
    model_id: "model-cinemox",
    model_display_name: "CinemoX Pro 2.1",
    status: "succeeded",
    quote_cents: 4500,
    actual_cost_cents: 4200,
    created_at: "2026-08-01T07:42:00Z",
  },
  {
    task_id: "tsk-2ab7e541",
    employee_user_id: "usr-linyao",
    employee_display_name: "林瑶",
    employee_email: "linyao@example.cn",
    model_id: "model-rush",
    model_display_name: "Rush Video 1.6",
    status: "processing",
    quote_cents: 1800,
    actual_cost_cents: null,
    created_at: "2026-08-01T08:18:00Z",
  },
  {
    task_id: "tsk-8f1c4e10",
    employee_user_id: "usr-chenmo",
    employee_display_name: "陈默",
    employee_email: "chenmo@example.cn",
    model_id: "model-cinemox",
    model_display_name: "CinemoX Pro 2.1",
    status: "failed",
    quote_cents: 3000,
    actual_cost_cents: 0,
    created_at: "2026-07-31T12:06:00Z",
  },
] : [];

const DEMO_LEDGER = DEVELOPMENT_DEMO_FIXTURES ? [
  {
    id: "led-1",
    kind: "recharge",
    amount_cents: 100000,
    available_delta_cents: 100000,
    reserved_delta_cents: 0,
    note: "平台管理员充值",
    created_at: "2026-07-28T03:20:00Z",
  },
  {
    id: "led-2",
    kind: "settle",
    amount_cents: -4200,
    available_delta_cents: 300,
    reserved_delta_cents: -4500,
    task_id: "tsk-9d21c7c4",
    note: "任务完成结算",
    created_at: "2026-08-01T07:56:00Z",
  },
  {
    id: "led-3",
    kind: "release",
    amount_cents: 0,
    available_delta_cents: 3000,
    reserved_delta_cents: -3000,
    task_id: "tsk-8f1c4e10",
    note: "任务失败，全额释放预留",
    created_at: "2026-07-31T12:09:00Z",
  },
] : [];

function demoModeCapability({
  maxImages = 0,
  maxVideos = 0,
  maxAudio = 0,
  supportsFace = false,
  durations = [5],
  resolutions = ["720p"],
  outputCounts = [1],
}) {
  return {
    input_media_types: [
      maxImages > 0 ? "image" : "",
      maxVideos > 0 ? "video" : "",
      maxAudio > 0 ? "audio" : "",
    ].filter(Boolean),
    supports_face: supportsFace,
    required_resource_keys: supportsFace ? ["face.library"] : [],
    limits: {
      max_prompt_length: 1000,
      max_images: maxImages,
      max_videos: maxVideos,
      max_audio: maxAudio,
      duration_seconds: durations,
      aspect_ratios: ["16:9", "9:16", "1:1"],
      resolutions,
      output_counts: outputCounts,
    },
  };
}

const DEMO_CINEMOX_CAPABILITY = DEVELOPMENT_DEMO_FIXTURES ? {
  schema_version: 1,
  modes: {
    text_to_video: demoModeCapability({
      maxImages: 9,
      maxVideos: 3,
      maxAudio: 3,
      supportsFace: true,
      durations: [10, 15, 20],
      resolutions: ["720p", "1080p"],
      outputCounts: [1, 2, 3, 4],
    }),
    image_to_video: demoModeCapability({
      maxImages: 9,
      maxVideos: 3,
      maxAudio: 3,
      supportsFace: true,
      durations: [10, 15, 20],
      resolutions: ["720p", "1080p"],
      outputCounts: [1, 2, 3, 4],
    }),
    video_to_video: demoModeCapability({
      maxImages: 9,
      maxVideos: 3,
      maxAudio: 3,
      supportsFace: true,
      durations: [10, 15, 20],
      resolutions: ["720p", "1080p"],
      outputCounts: [1, 2, 3, 4],
    }),
  },
} : {};

const DEMO_RUSH_CAPABILITY = DEVELOPMENT_DEMO_FIXTURES ? {
  schema_version: 1,
  modes: {
    image_to_video: demoModeCapability({
      maxImages: 4,
      maxVideos: 3,
      maxAudio: 3,
      durations: [5, 10],
      outputCounts: [1, 2],
    }),
    text_to_video: demoModeCapability({
      maxImages: 4,
      maxVideos: 3,
      maxAudio: 3,
      durations: [5, 10],
      outputCounts: [1, 2],
    }),
  },
} : {};

const DEMO_STORYBOARD_CAPABILITY = DEVELOPMENT_DEMO_FIXTURES ? {
  schema_version: 1,
  modes: {
    text_to_image: demoModeCapability({
      maxImages: 1,
      resolutions: ["720p", "1080p"],
      outputCounts: [1, 2, 3, 4],
    }),
  },
} : {};

const DEMO_MODELS = DEVELOPMENT_DEMO_FIXTURES ? [
  {
    id: "model-cinemox",
    slug: "cinemox-v2",
    display_name: "CinemoX Pro 2.1",
    provider_key: "channel-a",
    capability_version: 4,
    status: "published",
    active: true,
    billing_mode: "per_second",
    pricing_mode: "per_second",
    unit_price_cents: 300,
    capabilities: { generation: DEMO_CINEMOX_CAPABILITY },
    effective_capabilities: DEMO_CINEMOX_CAPABILITY,
  },
  {
    id: "model-rush",
    slug: "rush-video-1.6",
    display_name: "Rush Video 1.6",
    provider_key: "channel-b",
    capability_version: 2,
    status: "published",
    active: true,
    billing_mode: "per_item",
    pricing_mode: "per_item",
    unit_price_cents: 1800,
    capabilities: { generation: DEMO_RUSH_CAPABILITY },
    effective_capabilities: DEMO_RUSH_CAPABILITY,
  },
  {
    id: "model-storyboard",
    slug: "storyboard-beta",
    display_name: "Storyboard Beta",
    provider_key: "channel-a",
    capability_version: 1,
    status: "draft",
    active: false,
    billing_mode: "per_second",
    capabilities: { generation: DEMO_STORYBOARD_CAPABILITY },
    effective_capabilities: DEMO_STORYBOARD_CAPABILITY,
  },
] : [];

const DEMO_RESOURCES = DEVELOPMENT_DEMO_FIXTURES ? [
  {
    id: "res-face",
    key: "face.library",
    kind: "feature",
    display_name: "数字人脸库",
    description: "企业专属人脸素材管理与生成引用",
    active: true,
  },
  {
    id: "res-obs",
    key: "storage.private",
    kind: "external_api",
    display_name: "私有产物存储",
    description: "生成产物转存与短时签名下载",
    active: true,
  },
] : [];

const DEMO_COMPANIES = DEVELOPMENT_DEMO_FIXTURES ? [
  { id: "co-yuanchuang", name: "远创电商", status: "active", created_at: "2026-05-11T08:00:00Z" },
  { id: "co-hailan", name: "海岚文旅", status: "active", created_at: "2026-06-03T08:00:00Z" },
  { id: "co-beichen", name: "北辰教育", status: "suspended", created_at: "2026-06-21T08:00:00Z" },
] : [];

const DEMO_DASHBOARD = DEVELOPMENT_DEMO_FIXTURES ? {
  page: 1,
  page_size: MANAGEMENT_PAGE_SIZE,
  platform_recharge_cents: 310000,
  platform_income_cents: 186400,
  channel_cost_cents: 109600,
  known_gross_profit_cents: 76800,
  gross_profit_cents: null,
  channel_cost_status: "incomplete",
  unreconciled_succeeded_count: 2,
  channel_costs: [
    { channel_key: "channel-a", channel_type: "official", amount_cents: 78200 },
    { channel_key: "channel-b", channel_type: "third_party_api", amount_cents: 31400 },
  ],
  total_companies: 3,
  companies: [
    {
      company_id: "co-yuanchuang",
      company_name: "远创电商",
      company_status: "active",
      recharge_cents: 160000,
      consumption_cents: 86400,
      reserved_cents: 7200,
      task_count: 128,
      succeeded_count: 117,
      failed_count: 6,
    },
    {
      company_id: "co-hailan",
      company_name: "海岚文旅",
      company_status: "active",
      recharge_cents: 100000,
      consumption_cents: 62000,
      reserved_cents: 3600,
      task_count: 94,
      succeeded_count: 86,
      failed_count: 4,
    },
    {
      company_id: "co-beichen",
      company_name: "北辰教育",
      company_status: "suspended",
      recharge_cents: 50000,
      consumption_cents: 38000,
      reserved_cents: 0,
      task_count: 51,
      succeeded_count: 44,
      failed_count: 5,
    },
  ],
} : {};

const DEMO_CHANNEL_COSTS = DEVELOPMENT_DEMO_FIXTURES ? {
  page: 1,
  page_size: 50,
  total: 2,
  total_amount_cents: 109600,
  items: [
    {
      id: "cost-channel-a",
      amount_cents: 78200,
      channel_key: "channel-a",
      channel_type: "official",
      occurred_at: "2026-08-01T08:00:00Z",
      external_reference: "official-2026-08-01",
      note: "官方渠道日账单",
      source: "manual",
    },
    {
      id: "cost-channel-b",
      amount_cents: 31400,
      channel_key: "channel-b",
      channel_type: "third_party_api",
      occurred_at: "2026-08-01T08:00:00Z",
      external_reference: "partner-2026-08-01",
      note: "第三方渠道日账单",
      source: "manual",
    },
  ],
} : {};

const DEMO_DOWNLOADS = DEVELOPMENT_DEMO_FIXTURES ? [
  {
    id: "dl-1",
    task_id: "tsk-9d21c7c4",
    asset_id: "asset-final-01",
    requested_by_user_id: "usr-linyao",
    requested_by_display_name: "林瑶",
    expires_seconds: 300,
    status: "completed",
    downloaded: true,
    completed_at: "2026-08-01T08:03:12Z",
    bytes_sent: 4372373,
    completion_source: "object_storage",
    created_at: "2026-08-01T08:02:00Z",
  },
] : [];

const DEMO_AUDIT = DEVELOPMENT_DEMO_FIXTURES ? [
  {
    id: "audit-1",
    actor_user_id: "admin-zhou",
    action: "company.wallet.recharge",
    target_type: "company",
    target_id: "co-yuanchuang",
    before_summary: { available_cents: 38700 },
    after_summary: { available_cents: 88700, amount_cents: 50000 },
    request_id: "req-5a8f",
    created_at: "2026-08-01T06:30:00Z",
  },
  {
    id: "audit-2",
    actor_user_id: "admin-zhou",
    action: "model.publish",
    target_type: "model_definition",
    target_id: "model-rush",
    before_summary: { status: "draft", capability_version: 2 },
    after_summary: { status: "published", capability_version: 3 },
    request_id: "req-24c1",
    created_at: "2026-07-31T09:10:00Z",
  },
] : [];

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


function copyableInvitationUrl(value) {
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

function byteCount(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "未回传";
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function localDateTimeInput(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function grossMarginLabel(incomeCents, grossProfitCents) {
  const income = Number(incomeCents) || 0;
  if (income <= 0) return "0.00%";
  return `${((Number(grossProfitCents || 0) / income) * 100).toFixed(2)}%`;
}

function makeOperationKey(prefix) {
  const unique = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${unique}`;
}

function reportApiFilters(filters) {
  const normalized = { ...filters };
  for (const key of ["start_time", "end_time"]) {
    if (!normalized[key]) continue;
    const value = new Date(normalized[key]);
    normalized[key] = Number.isNaN(value.getTime()) ? "" : value.toISOString();
  }
  return normalized;
}

function dateMatches(value, startTime, endTime) {
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
    !items.length ||
    items.some((item) => !Number.isInteger(item) || item < min || item > max)
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

function readCapabilityEditor(form) {
  const selectedModes = form.getAll("capabilityModes").map(String);
  if (!selectedModes.length) {
    throw new Error("请至少启用一种生成模式。" );
  }
  if (selectedModes.some((mode) => !GENERATION_MODES.some((item) => item.id === mode))) {
    throw new Error("生成模式不在平台支持范围内。" );
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
      throw new Error("素材数量和提示词上限必须填写整数。" );
    }
    if (maxPromptLength < 1 || maxPromptLength > 10_000) {
      throw new Error("提示词上限需要在 1-10000 之间。" );
    }
    if (maxImages + maxVideos + maxAudio > 15) {
      throw new Error("单个模式的图片、视频和音频输入合计不能超过 15 个。" );
    }
    if (mode === "image_to_video" && maxImages < 1) {
      throw new Error("图生视频至少需要支持 1 张图片。" );
    }
    if (mode === "video_to_video" && maxVideos < 1) {
      throw new Error("视频重绘至少需要支持 1 个视频。" );
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
      !aspectRatios.length ||
      !resolutions.length ||
      !outputCounts.length ||
      outputCounts.some((value) => value < 1 || value > 16)
    ) {
      throw new Error("每种模式都要选择比例、分辨率和 1-16 个产物数。" );
    }
    if (
      form.get("billingMode") === "per_second" &&
      (outputCounts.length !== 1 || outputCounts[0] !== 1)
    ) {
      throw new Error("按秒计费模型的每种模式只能选择 1 个产物。" );
    }
    const inputMediaTypes = [
      maxImages > 0 ? "image" : "",
      maxVideos > 0 ? "video" : "",
      maxAudio > 0 ? "audio" : "",
    ].filter(Boolean);
    modes[mode] = {
      inputMediaTypes,
      supportsFace: form.get(`${prefix}.supportsFace`) === "on",
      requiredResourceKeys: parseResourceKeys(form.get(`${prefix}.requiredResourceKeys`)),
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

function pricingQuantityLabel(item) {
  if (item.quantity == null) return "-";
  return `${item.quantity}${item.pricing_mode === "per_second" ? " 秒" : " 条"}`;
}

function canOpenCompanySection(identity, section) {
  return canOpenCompanyConsoleSection(identity, section);
}

function mergePageRecords(currentPage, nextPage, identityKeys) {
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

function normalizePageCollection(payload, fallbackPageSize = MANAGEMENT_PAGE_SIZE) {
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

function managementSectionParam(mode) {
  return mode === "platform" ? "platform_config" : "company_section";
}

function sectionFromLocation(mode, fallback) {
  try {
    const value = new URLSearchParams(globalThis.location?.search || "")
      .get(managementSectionParam(mode));
    return value || fallback;
  } catch {
    return fallback;
  }
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

function demoCompanyEntitlements(companyId) {
  const isPrimaryCompany = companyId === DEMO_COMPANIES[0]?.id;
  return {
    company_id: companyId,
    models: DEMO_MODELS.map((model) => {
      const enabled = isPrimaryCompany && model.status === "published";
      return {
        model_id: model.id,
        slug: model.slug,
        display_name: model.display_name,
        status: model.status,
        billing_mode: model.billing_mode,
        grant_id: enabled ? `demo-grant-${companyId}-${model.id}` : null,
        enabled,
        price_per_second_cents: enabled && model.billing_mode === "per_second"
          ? model.unit_price_cents
          : null,
        price_per_item_cents: enabled && model.billing_mode === "per_item"
          ? model.unit_price_cents
          : null,
        config_override: {},
      };
    }),
    resources: DEMO_RESOURCES.map((resource) => ({
      resource_id: resource.id,
      key: resource.key,
      kind: resource.kind,
      display_name: resource.display_name,
      active: resource.active,
      grant_id: isPrimaryCompany ? `demo-grant-${companyId}-${resource.id}` : null,
      enabled: isPrimaryCompany,
      config_override: {},
    })),
  };
}

function demoSnapshot(demoIdentity) {
  const roles = structuredClone(DEMO_ROLES);
  const members = structuredClone(DEMO_MEMBERS).map((member) => (
    withMemberPermissionState(member, roles)
  ));
  const fallbackIdentity = {
    company_id: "co-yuanchuang",
    user_id: "usr-chenmo",
    membership_id: "mem-operator",
    display_name: "陈默",
    email: "chenmo@example.cn",
    is_platform_admin: false,
    status: "active",
    permission_codes: [
      "assets.read",
      "assets.manage",
      "models.read",
      "resources.read",
      "tasks.read",
      "tasks.create",
    ],
    roles: [DEMO_ROLES.find((role) => role.system_key === "operator")].filter(Boolean),
  };
  const resolvedIdentity = structuredClone(demoIdentity || fallbackIdentity);
  const companyMe = resolvedIdentity.is_platform_admin ? null : {
    ...resolvedIdentity,
    company_id: resolvedIdentity.company_id || "co-yuanchuang",
  };
  const adminMe = resolvedIdentity.is_platform_admin ? {
    user_id: resolvedIdentity.user_id,
    display_name: resolvedIdentity.display_name,
    email: resolvedIdentity.email,
    is_platform_admin: true,
  } : null;
  return {
    me: companyMe,
    members,
    invitations: {
      page: 1,
      page_size: MANAGEMENT_PAGE_SIZE,
      total: 1,
      items: [{
        id: "invite-demo-pending",
        company_id: "co-yuanchuang",
        email: "new.member@example.cn",
        display_name: "新成员",
        primary_role: "operator",
        status: "pending",
        expires_at: "2026-08-24T12:00:00Z",
        created_at: "2026-08-20T08:00:00Z",
        invitation_url: null,
      }],
    },
    roles,
    permissions: PERMISSIONS.map(([code, description]) => ({ code, description })),
    wallet: { available_cents: 88700, reserved_cents: 1800 },
    ledger: structuredClone(DEMO_LEDGER),
    recharges: {
      page: 1,
      page_size: 100,
      total: 1,
      total_amount_cents: 100000,
      items: [structuredClone(DEMO_LEDGER[0])],
    },
    models: structuredClone(DEMO_MODELS.filter((item) => item.status === "published")),
    grants: [
      { id: "grant-1", model_id: "model-cinemox", enabled: true, price_per_second_cents: 300 },
      { id: "grant-2", model_id: "model-rush", enabled: true, price_per_item_cents: 1800 },
    ],
    resources: structuredClone(DEMO_RESOURCES),
    taskReport: {
      page: 1,
      page_size: MANAGEMENT_PAGE_SIZE,
      total: DEMO_TASKS.length,
      total_actual_cost_cents: 4200,
      items: structuredClone(DEMO_TASKS),
    },
    succeededTaskReport: {
      page: 1,
      page_size: 1,
      total: DEMO_TASKS.filter((task) => task.status === "succeeded").length,
      total_actual_cost_cents: 4200,
      items: structuredClone(DEMO_TASKS.filter((task) => task.status === "succeeded").slice(0, 1)),
    },
    consumption: {
      page: 1,
      page_size: MANAGEMENT_PAGE_SIZE,
      total: 1,
      total_amount_cents: 4200,
      items: [
        {
          ledger_entry_id: "led-2",
          company_id: "co-yuanchuang",
          company_name: "远创电商",
          task_id: "tsk-9d21c7c4",
          employee_user_id: "usr-chenmo",
          employee_display_name: "陈默",
          employee_email: "chenmo@example.cn",
          model_id: "model-cinemox",
          model_display_name: "CinemoX Pro 2.1",
          task_status: "succeeded",
          pricing_mode: "per_second",
          unit_price_cents: 300,
          quantity: 14,
          amount_cents: 4200,
          consumed_at: "2026-08-01T07:56:00Z",
        },
      ],
    },
    downloads: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: DEMO_DOWNLOADS.length, items: structuredClone(DEMO_DOWNLOADS) },
    adminMe,
    adminUsers: {
      page: 1,
      page_size: MANAGEMENT_PAGE_SIZE,
      total: 3,
      items: [
      {
        id: "usr-platform-owner",
        email: "owner@example.cn",
        display_name: "平台所有者",
        status: "active",
        email_verified_at: "2026-08-01T01:00:00Z",
        auth_version: 4,
        last_login_at: "2026-08-20T08:00:00Z",
        deactivated_at: null,
        updated_at: "2026-08-20T08:00:00Z",
      },
      {
        id: "usr-suspended",
        email: "suspended@example.cn",
        display_name: "已停用账号",
        status: "suspended",
        email_verified_at: "2026-08-02T01:00:00Z",
        auth_version: 3,
        last_login_at: "2026-08-17T08:00:00Z",
        deactivated_at: null,
        updated_at: "2026-08-18T08:00:00Z",
      },
      {
        id: "usr-deactivated",
        email: "deactivated@example.cn",
        display_name: "已注销账号",
        status: "deactivated",
        email_verified_at: "2026-07-01T01:00:00Z",
        auth_version: 6,
        last_login_at: "2026-08-01T08:00:00Z",
        deactivated_at: "2026-08-10T08:00:00Z",
        updated_at: "2026-08-10T08:00:00Z",
      },
      ],
    },
    companies: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: DEMO_COMPANIES.length, items: structuredClone(DEMO_COMPANIES) },
    dashboard: structuredClone(DEMO_DASHBOARD),
    adminModels: structuredClone(DEMO_MODELS),
    relayModelAudit: {
      catalog_revision: "",
      items: [],
      platform_only_model_ids: [],
      error: "演示模式未连接中转站模型目录",
    },
    adminResources: structuredClone(DEMO_RESOURCES),
    channelCosts: structuredClone(DEMO_CHANNEL_COSTS),
    adminConsumption: {
      page: 1,
      page_size: 100,
      total: 1,
      total_amount_cents: 4200,
      items: [
        {
          ledger_entry_id: "led-2",
          company_id: "co-yuanchuang",
          company_name: "远创电商",
          task_id: "tsk-9d21c7c4",
          employee_user_id: "usr-chenmo",
          employee_display_name: "陈默",
          employee_email: "chenmo@example.cn",
          model_id: "model-cinemox",
          model_display_name: "CinemoX Pro 2.1",
          task_status: "succeeded",
          pricing_mode: "per_second",
          unit_price_cents: 300,
          quantity: 14,
          amount_cents: 4200,
          consumed_at: "2026-08-01T07:56:00Z",
        },
      ],
    },
    audit: { page: 1, page_size: 100, total: DEMO_AUDIT.length, items: structuredClone(DEMO_AUDIT) },
  };
}

function emptySnapshot() {
  return {
    me: null,
    members: [],
    invitations: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, items: [] },
    roles: [],
    permissions: [],
    wallet: { available_cents: 0, reserved_cents: 0 },
    ledger: [],
    recharges: { page: 1, page_size: 100, total: 0, total_amount_cents: 0, items: [] },
    models: [],
    grants: [],
    resources: [],
    taskReport: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, total_actual_cost_cents: 0, items: [] },
    succeededTaskReport: { page: 1, page_size: 1, total: 0, total_actual_cost_cents: 0, items: [] },
    consumption: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, total_amount_cents: 0, items: [] },
    downloads: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, items: [] },
    adminMe: null,
    adminUsers: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, items: [] },
    companies: { page: 1, page_size: MANAGEMENT_PAGE_SIZE, total: 0, items: [] },
    dashboard: {
      platform_recharge_cents: 0,
      platform_income_cents: 0,
      channel_cost_cents: 0,
      known_gross_profit_cents: 0,
      gross_profit_cents: 0,
      channel_cost_status: "complete",
      unreconciled_succeeded_count: 0,
      channel_costs: [],
      total_companies: 0,
      page: 1,
      page_size: MANAGEMENT_PAGE_SIZE,
      companies: [],
    },
    adminModels: [],
    relayModelAudit: {
      catalog_revision: "",
      items: [],
      platform_only_model_ids: [],
      error: "",
    },
    adminResources: [],
    channelCosts: { page: 1, page_size: 50, total: 0, total_amount_cents: 0, items: [] },
    adminConsumption: { page: 1, page_size: 100, total: 0, total_amount_cents: 0, items: [] },
    audit: { page: 1, page_size: 100, total: 0, items: [] },
  };
}

function LegacyManagementConsole({
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
  companyContexts = [],
  activeCompanyId = "",
  onCompanyChange,
}) {
  const demoMode = !import.meta.env.PROD && requestedDemoMode;
  const activeSkin = normalizeSkin(skin);
  const [section, setSection] = useState(() => sectionFromLocation(
    mode,
    mode === "platform" ? initialPlatformSection : "overview",
  ));
  const [data, setData] = useState(() => {
    const initial = demoMode ? demoSnapshot(demoIdentity) : emptySnapshot();
    if (mode === "platform" && initialPlatformIdentity) {
      initial.adminMe = initialPlatformIdentity;
    }
    return initial;
  });
  const [loading, setLoading] = useState(!demoMode);
  const [busy, setBusy] = useState(false);
  const [memberAccessLoading, setMemberAccessLoading] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [drawer, setDrawerState] = useState(null);
  const [drawerError, setDrawerError] = useState("");
  const [entitlementBusyKey, setEntitlementBusyKey] = useState("");
  const [paginationBusyKey, setPaginationBusyKey] = useState("");
  const [accessInvalidated, setAccessInvalidated] = useState(false);
  const [ownerInvitationLinks, setOwnerInvitationLinks] = useState({});
  const memberAccessRequestRef = useRef(0);
  const companyControlRequestRef = useRef(0);
  const loadRequestGenerationRef = useRef(0);
  const loadAbortControllerRef = useRef(null);
  const controlMainRef = useRef(null);
  const controlNavRef = useRef(null);
  const activeNavItemRef = useRef(null);
  const [reportFilters, setReportFilters] = useState(EMPTY_COMPANY_REPORT_FILTERS);
  const [appliedReportFilters, setAppliedReportFilters] = useState(EMPTY_COMPANY_REPORT_FILTERS);
  const [adminReportFilters, setAdminReportFilters] = useState(EMPTY_ADMIN_REPORT_FILTERS);
  const [appliedAdminReportFilters, setAppliedAdminReportFilters] = useState(EMPTY_ADMIN_REPORT_FILTERS);
  const companyPermissions = useMemo(
    () => new Set(data.me?.permission_codes || []),
    [data.me?.permission_codes],
  );
  const isCurrentOwner = Boolean(
    data.me?.roles?.some((role) => role.system_key === "owner"),
  );
  const canManageUsers = companyPermissions.has("users.manage");
  const canExportReports = companyPermissions.has("reports.export");
  const canAccessPlatform = allowedSurfaces.includes("platform");
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

  useEffect(() => {
    memberAccessRequestRef.current += 1;
    companyControlRequestRef.current += 1;
    setMemberAccessLoading(false);
    setEntitlementBusyKey("");
    setPaginationBusyKey("");
  }, [mode, section]);

  useEffect(() => () => {
    memberAccessRequestRef.current += 1;
    companyControlRequestRef.current += 1;
    loadRequestGenerationRef.current += 1;
    loadAbortControllerRef.current?.abort();
    loadAbortControllerRef.current = null;
  }, []);

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
            const [adminModels, relayModelAudit] = await Promise.all([
              client.listAdminModels({ signal }),
              client.listAdminRelayModels({ signal }).catch((relayError) => {
                if (signal.aborted) throw relayError;
                return {
                  catalog_revision: "",
                  items: [],
                  platform_only_model_ids: [],
                  error: relayError?.message || "中转站模型目录暂时不可用",
                };
              }),
            ]);
            commitData({ adminModels, relayModelAudit });
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
        demoWork?.();
      } else {
        await work();
        await load(section);
      }
      setDrawer(null);
      setToast(successMessage);
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

  const openMemberAccess = async (member) => {
    const returnFocusElement = globalThis.document?.activeElement ?? null;
    if (demoMode) {
      setDrawer({ type: "roles", member, returnFocusElement });
      return;
    }
    const requestId = memberAccessRequestRef.current + 1;
    memberAccessRequestRef.current = requestId;
    setMemberAccessLoading(true);
    setError("");
    try {
      const [members, roles, permissions] = await Promise.all([
        client.listMembers(),
        client.listRoles(),
        client.listPermissionCatalog(),
      ]);
      requirePermissionCatalog(permissions);
      const freshMember = members.find(
        (item) => item.membership_id === member.membership_id,
      );
      if (!freshMember) throw new Error("成员已不存在，请刷新后重试。");
      if (requestId !== memberAccessRequestRef.current) return;
      mergeData({ members, roles, permissions });
      setDrawer({
        type: "roles",
        member: withMemberPermissionState(freshMember, roles),
        returnFocusElement,
      });
    } catch (refreshError) {
      if (requestId === memberAccessRequestRef.current) {
        setError(refreshError?.message || "读取成员最新权限失败，请稍后重试。");
      }
    } finally {
      if (requestId === memberAccessRequestRef.current) setMemberAccessLoading(false);
    }
  };

  const openRoleEditor = (role = null) => {
    try {
      requirePermissionCatalog(data.permissions);
    } catch (catalogError) {
      setError(catalogError.message);
      return;
    }
    setDrawer(role ? { type: "role", role } : { type: "role" });
  };

  const setMemberStatus = (member) => {
    const nextStatus = member.status === "active" ? "disabled" : "active";
    if (nextStatus === "disabled" && !globalThis.confirm?.(`确认停用成员“${member.display_name}”？`)) return;
    mutate(
      () => client.setMemberStatus(member.membership_id, nextStatus),
      () =>
        mergeData({
          members: data.members.map((item) =>
            item.membership_id === member.membership_id ? { ...item, status: nextStatus } : item,
          ),
        }),
      nextStatus === "active" ? "成员已恢复" : "成员已停用，现有会话权限将被拒绝",
    );
  };

  const createCompanyInvitation = async (payload) => {
    setBusy(true);
    setDrawerError("");
    try {
      const result = demoMode
        ? {
            id: `invite-demo-${Date.now()}`,
            company_id: data.me?.company_id || "demo-company",
            email: payload.email,
            display_name: payload.displayName,
            primary_role: payload.primaryRole,
            status: "pending",
            expires_at: new Date(Date.now() + payload.expiresInHours * 3_600_000).toISOString(),
            created_at: new Date().toISOString(),
            invitation_url: null,
          }
        : await client.createInvitation(payload);
      setData((current) => ({
        ...current,
        invitations: {
          ...current.invitations,
          total: Math.max(current.invitations.total, current.invitations.items.length + 1),
          items: [
            result,
            ...current.invitations.items.filter((item) => item.id !== result.id),
          ],
        },
      }));
      setDrawer(null);
      setToast(demoMode
        ? "演示邀请已加入列表；演示模式不会生成真实链接"
        : result.invitation_url
          ? "邀请已创建；请复制仅显示一次的邀请链接"
          : "邀请已存在；如需新链接请重新发送");
    } catch (invitationError) {
      if (!onSessionError?.(invitationError)) {
        setDrawerError(invitationError?.message || "邀请创建失败，请稍后重试。");
      }
    } finally {
      setBusy(false);
    }
  };

  const copyInvitationLink = async (invitation) => {
    const link = copyableInvitationUrl(invitation.invitation_url);
    if (!link) {
      setError("该一次性链接未保留或已不可用；请重新发送邀请以生成新链接。");
      return;
    }
    try {
      await globalThis.navigator?.clipboard?.writeText(link);
      setToast("一次性邀请链接已复制；请通过可信渠道发送");
    } catch {
      setError("浏览器未允许复制。请重新发送后再次尝试，页面不会直接展示邀请凭据。");
    }
  };

  const reissueCompanyInvitation = async (invitation) => {
    if (demoMode) {
      setToast("演示模式不会签发真实邀请链接");
      return;
    }
    if (!globalThis.confirm?.("重新发送会立即使旧邀请链接失效，是否继续？")) return;
    setBusy(true);
    setError("");
    try {
      const result = await client.reissueInvitation(invitation.id);
      setData((current) => ({
        ...current,
        invitations: {
          ...current.invitations,
          items: current.invitations.items.map((item) => item.id === result.id ? result : item),
        },
      }));
      setToast("新邀请已签发；请复制仅显示一次的新链接");
    } catch (invitationError) {
      if (!onSessionError?.(invitationError)) {
        setError(invitationError?.message || "邀请重新发送失败，请稍后重试。");
      }
    } finally {
      setBusy(false);
    }
  };

  const revokeCompanyInvitation = async (invitation) => {
    if (!globalThis.confirm?.(`确认撤销发给 ${invitation.email} 的邀请？`)) return;
    setBusy(true);
    setError("");
    try {
      const result = demoMode
        ? { ...invitation, status: "revoked", invitation_url: null }
        : await client.revokeInvitation(invitation.id);
      setData((current) => ({
        ...current,
        invitations: {
          ...current.invitations,
          items: current.invitations.items.map((item) => item.id === result.id ? result : item),
        },
      }));
      setToast("邀请已撤销，旧链接不能再使用");
    } catch (invitationError) {
      if (!onSessionError?.(invitationError)) {
        setError(invitationError?.message || "邀请撤销失败，请稍后重试。");
      }
    } finally {
      setBusy(false);
    }
  };

  const createPlatformCompany = async (payload) => {
    setBusy(true);
    setDrawerError("");
    try {
      const result = demoMode
        ? {
            id: `co-demo-${Date.now()}`,
            name: payload.name,
            status: "active",
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            owner_activation_required: false,
            owner_user_id: `usr-demo-${Date.now()}`,
            owner_membership_id: `mem-demo-${Date.now()}`,
          }
        : await client.createAdminCompany(payload);
      const invitationUrl = copyableInvitationUrl(result.owner_invitation_url);
      setData((current) => ({
        ...current,
        companies: {
          ...current.companies,
          total: current.companies.total + 1,
          items: [result, ...current.companies.items.filter((item) => item.id !== result.id)],
        },
      }));
      if (invitationUrl) {
        setOwnerInvitationLinks((current) => ({ ...current, [result.id]: invitationUrl }));
      }
      setDrawer(null);
      setToast(demoMode
        ? "演示企业已加入目录，不会创建真实老板账号"
        : result.owner_activation_required
          ? "企业已创建；老板账号需接受邀请后才会激活，请复制一次性链接"
          : "企业与老板账号已创建并可使用");
    } catch (companyError) {
      if (!onSessionError?.(companyError)) {
        setDrawerError(companyError?.message || "企业创建失败，请稍后重试。");
      }
    } finally {
      setBusy(false);
    }
  };

  const copyOwnerInvitationLink = async (company) => {
    const link = copyableInvitationUrl(ownerInvitationLinks[company.id]);
    if (!link) {
      setError("该老板邀请链接未保留或已不可用；请重新签发后再复制。");
      return;
    }
    try {
      await globalThis.navigator?.clipboard?.writeText(link);
      setToast("老板一次性邀请链接已复制；请通过可信渠道发送");
    } catch {
      setError("浏览器未允许复制。可重新签发邀请，页面不会直接展示邀请凭据。");
    }
  };

  const reissueOwnerInvitation = async (company, {
    replacementEmail = "",
    replacementDisplayName = "",
  } = {}) => {
    if (demoMode) {
      setToast("演示模式不会签发真实老板邀请链接");
      return;
    }
    if (!company.owner_membership_id || !company.owner_user_id) {
      setDrawerError("服务端尚未返回老板成员快照，请关闭窗口并刷新企业目录后重试。");
      return;
    }
    setBusy(true);
    setDrawerError("");
    try {
      const result = await client.reissueAdminCompanyOwnerInvitation(company.id, {
        expectedOwnerMembershipId: company.owner_membership_id,
        expectedOwnerUserId: company.owner_user_id,
        replacementEmail,
        replacementDisplayName,
      });
      const invitationUrl = copyableInvitationUrl(result.invitation_url);
      if (!invitationUrl) throw new Error("服务端未返回可安全复制的老板邀请链接。");
      setOwnerInvitationLinks((current) => ({ ...current, [company.id]: invitationUrl }));
      setData((current) => ({
        ...current,
        companies: {
          ...current.companies,
          items: current.companies.items.map((item) => item.id === company.id
            ? {
                ...item,
                owner_activation_required: true,
                owner_membership_id: result.owner_membership_id,
                owner_user_id: result.owner_user_id,
                owner_invitation_expires_at: result.expires_at,
              }
            : item),
        },
      }));
      setDrawer(null);
      setToast("老板邀请已重新签发；请复制仅返回一次的新链接");
    } catch (invitationError) {
      if (onSessionError?.(invitationError)) return;
      if (invitationError?.status === 409) {
        await load("companies");
        setDrawerError("老板账号状态已变化，已刷新企业目录；请关闭窗口并确认最新状态后再操作。");
      } else {
        setDrawerError(invitationError?.message || "老板邀请重新签发失败，请稍后重试。");
      }
    } finally {
      setBusy(false);
    }
  };

  const setGlobalUserStatus = (user) => {
    const targetStatus = user.status === "active" ? "suspended" : "active";
    if (!globalThis.confirm?.(
      targetStatus === "suspended"
        ? `确认暂停账号“${user.display_name}”？该账号的全部会话会立即撤销。`
        : `确认恢复账号“${user.display_name}”？恢复后仍需重新登录。`,
    )) return;
    mutate(
      () => client.setPlatformUserStatus(user.id, {
        expectedStatus: user.status,
        expectedAuthVersion: user.auth_version,
        targetStatus,
      }),
      () => mergeData({
        adminUsers: {
          ...data.adminUsers,
          items: data.adminUsers.items.map((item) => item.id === user.id
            ? { ...item, status: targetStatus, auth_version: item.auth_version + 1 }
            : item),
        },
      }),
      targetStatus === "active" ? "账号已恢复，可重新登录" : "账号已暂停，全部会话已撤销",
    );
  };

  const deleteCompanyRole = (role) => {
    if (!globalThis.confirm?.(`确认删除自定义角色“${role.name}”？已分配成员会同时失去这个附加角色。`)) return;
    const nextRoles = data.roles.filter((item) => item.id !== role.id);
    mutate(
      () => client.deleteRole(role.id),
      () => mergeData({
        roles: nextRoles,
        members: data.members.map((member) => withMemberPermissionState({
          ...member,
          roles: roleList(member).filter((assigned) => assigned.id !== role.id),
        }, nextRoles)),
      }),
      "自定义角色已删除",
    );
  };

  const setCompanyStatus = (company) => {
    const nextStatus = company.status === "active" ? "suspended" : "active";
    if (nextStatus === "suspended" && !globalThis.confirm?.(`确认停用企业“${company.name}”？`)) return;
    mutate(
      () => client.setAdminCompanyStatus(company.id, nextStatus),
      () =>
        mergeData({
          companies: {
            ...data.companies,
            items: data.companies.items.map((item) =>
              item.id === company.id ? { ...item, status: nextStatus } : item,
            ),
          },
        }),
      nextStatus === "active" ? "企业已恢复" : "企业已停用",
    );
  };

  const companyDashboardRow = (companyId) => (
    (data.dashboard?.companies || []).find((item) => item.company_id === companyId)
  );

  const openCompanyControl = async (company, { preserveContent = false } = {}) => {
    const access = {
      entitlements: canUsePlatformPermission("platform.entitlements.read"),
      finance: canUsePlatformPermission("platform.finance.read"),
    };
    if (!access.entitlements && !access.finance) {
      setError("当前账号没有企业权益或财务读取权限。");
      return;
    }
    const requestId = companyControlRequestRef.current + 1;
    companyControlRequestRef.current = requestId;
    const previous = preserveContent && drawer?.type === "companyControl"
      && drawer.company?.id === company.id
      ? drawer
      : null;
    const returnFocusElement = previous?.returnFocusElement
      ?? globalThis.document?.activeElement
      ?? null;
    setDrawerError("");
    setDrawerState({
      type: "companyControl",
      company,
      returnFocusElement,
      access,
      loadingDomains: {
        entitlements: access.entitlements,
        finance: access.finance,
      },
      domainErrors: { entitlements: "", finance: "" },
      entitlements: previous?.entitlements || null,
      recharges: previous?.recharges || null,
      consumption: previous?.consumption || null,
      summary: companyDashboardRow(company.id) || previous?.summary || null,
    });

    if (demoMode) {
      const allConsumption = data.adminConsumption?.items || [];
      const items = allConsumption.filter((item) => item.company_id === company.id);
      const rechargeItems = company.id === DEMO_COMPANIES[0]?.id
        ? (data.recharges?.items || [])
        : [];
      setDrawerState((current) => current?.type === "companyControl"
        && current.company?.id === company.id
        ? {
            ...current,
            loadingDomains: { entitlements: false, finance: false },
            entitlements: access.entitlements ? demoCompanyEntitlements(company.id) : null,
            recharges: access.finance ? {
              page: 1,
              page_size: 50,
              total: rechargeItems.length,
              total_amount_cents: rechargeItems.reduce(
                (sum, item) => sum + Math.max(0, Number(item.amount_cents || item.available_delta_cents || 0)),
                0,
              ),
              items: rechargeItems,
            } : null,
            consumption: access.finance ? {
              page: 1,
              page_size: 50,
              total: items.length,
              total_amount_cents: items.reduce((sum, item) => sum + Number(item.amount_cents || 0), 0),
              items,
            } : null,
          }
        : current);
      return;
    }

    const updateDomain = (domain, patch) => {
      if (requestId !== companyControlRequestRef.current) return;
      setDrawerState((current) => {
        if (current?.type !== "companyControl" || current.company?.id !== company.id) return current;
        const { domainError = "", ...domainPatch } = patch;
        return {
          ...current,
          ...domainPatch,
          loadingDomains: { ...current.loadingDomains, [domain]: false },
          domainErrors: { ...current.domainErrors, [domain]: domainError },
          summary: companyDashboardRow(company.id) || current.summary,
        };
      });
    };
    const handleDomainError = (domain, loadError, fallback) => {
      if (loadError?.status === 401) {
        companyControlRequestRef.current += 1;
        invalidateSensitiveData("登录状态已失效，请通过正式身份系统重新登录。");
        return;
      }
      if (loadError?.status === 403) {
        companyControlRequestRef.current += 1;
        invalidateSensitiveData("权限已变化，现有管理数据已清除。请重新核验身份后再试。");
        return;
      }
      updateDomain(domain, { domainError: loadError?.message || fallback });
    };

    const jobs = [];
    if (access.entitlements) {
      jobs.push(
        client.getAdminCompanyEntitlements(company.id)
          .then((entitlements) => updateDomain("entitlements", {
            entitlements,
            domainError: "",
          }))
          .catch((loadError) => handleDomainError(
            "entitlements",
            loadError,
            "企业权益读取失败，请稍后重试。",
          )),
      );
    }
    if (access.finance) {
      jobs.push(
        Promise.all([
          client.listAdminCompanyRecharges(company.id, { page: 1, page_size: MANAGEMENT_PAGE_SIZE }),
          client.getAdminConsumptionReport({ company_id: company.id, page: 1, page_size: MANAGEMENT_PAGE_SIZE }),
        ])
          .then(([recharges, consumption]) => updateDomain("finance", {
            recharges,
            consumption,
            domainError: "",
          }))
          .catch((loadError) => handleDomainError(
            "finance",
            loadError,
            "企业账务读取失败，请稍后重试。",
          )),
      );
    }
    await Promise.all(jobs);
  };

  const loadMoreCompanyControl = async (kind) => {
    if (demoMode || drawer?.type !== "companyControl" || paginationBusyKey) return;
    const companyId = drawer.company.id;
    const busyKey = `company-control-${kind}`;
    const currentPage = kind === "recharges" ? drawer.recharges : drawer.consumption;
    const nextPage = Number(currentPage?.page || 1) + 1;
    setPaginationBusyKey(busyKey);
    setDrawerError("");
    try {
      const result = kind === "recharges"
        ? await client.listAdminCompanyRecharges(companyId, { page: nextPage, page_size: MANAGEMENT_PAGE_SIZE })
        : await client.getAdminConsumptionReport({ company_id: companyId, page: nextPage, page_size: MANAGEMENT_PAGE_SIZE });
      setDrawerState((current) => {
        if (current?.type !== "companyControl" || current.company.id !== companyId) return current;
        return {
          ...current,
          [kind]: mergePageRecords(
            current[kind],
            result,
            kind === "recharges" ? "id" : "ledger_entry_id",
          ),
        };
      });
    } catch (pageError) {
      if (pageError?.status === 401) {
        invalidateSensitiveData("登录状态已失效，请通过正式身份系统重新登录。");
      } else if (pageError?.status === 403) {
        invalidateSensitiveData("权限已变化，现有管理数据已清除。请重新核验身份后再试。");
      } else {
        setDrawerError(pageError?.message || "企业账务下一页加载失败，请稍后重试。");
      }
    } finally {
      setPaginationBusyKey("");
    }
  };

  const updateEntitlementRow = (collection, identityKey, identity, patch) => {
    setDrawerState((current) => {
      if (current?.type !== "companyControl" || !current.entitlements) return current;
      return {
        ...current,
        entitlements: {
          ...current.entitlements,
          [collection]: (current.entitlements[collection] || []).map((item) => (
            item[identityKey] === identity ? { ...item, ...patch } : item
          )),
        },
      };
    });
  };

  const saveCompanyModelEntitlement = async (item, enabled, unitPriceCents) => {
    const price = Math.round(Number(unitPriceCents));
    if (!Number.isFinite(price) || price <= 0) {
      setDrawerError("模型单价必须大于 0 元。" );
      return;
    }
    const busyKey = `model:${item.model_id}`;
    setEntitlementBusyKey(busyKey);
    setDrawerError("");
    const payload = {
      model_id: item.model_id,
      enabled,
      price_per_second_cents: item.billing_mode === "per_second" ? price : null,
      price_per_item_cents: item.billing_mode === "per_item" ? price : null,
      config_override: item.config_override || {},
    };
    try {
      const result = demoMode
        ? { ...payload, grant_id: item.grant_id || `demo-grant-${Date.now()}` }
        : await client.upsertAdminModelGrant(drawer.company.id, payload);
      updateEntitlementRow("models", "model_id", item.model_id, {
        ...result,
        enabled,
        grant_id: result?.id || result?.grant_id || item.grant_id,
      });
      setToast(enabled
        ? `${item.display_name} 的企业单价已保存并开通`
        : `${item.display_name} 已对该企业停用`);
    } catch (mutationError) {
      setDrawerError(mutationError?.message || "模型权益更新失败，请稍后重试。" );
    } finally {
      setEntitlementBusyKey("");
    }
  };

  const saveCompanyResourceEntitlement = async (item, enabled) => {
    const busyKey = `resource:${item.resource_id}`;
    setEntitlementBusyKey(busyKey);
    setDrawerError("");
    try {
      const payload = { enabled, config_override: item.config_override || {} };
      const result = demoMode
        ? { ...payload, grant_id: item.grant_id || `demo-grant-${Date.now()}` }
        : await client.upsertAdminResourceGrant(
            drawer.company.id,
            item.resource_id,
            payload,
          );
      updateEntitlementRow("resources", "resource_id", item.resource_id, {
        ...result,
        enabled,
        grant_id: result?.id || result?.grant_id || item.grant_id,
      });
      setToast(enabled
        ? `${item.display_name} 已对该企业开通`
        : `${item.display_name} 已对该企业停用`);
    } catch (mutationError) {
      setDrawerError(mutationError?.message || "功能权益更新失败，请稍后重试。" );
    } finally {
      setEntitlementBusyKey("");
    }
  };

  const setModelState = (model, action) => {
    if (action === "disable" && !globalThis.confirm?.(`确认下线模型“${model.display_name}”？`)) return;
    mutate(
      () =>
        action === "publish"
          ? client.publishAdminModel(model.id)
          : client.disableAdminModel(model.id),
      () =>
        mergeData({
          adminModels: data.adminModels.map((item) =>
            item.id === model.id
              ? { ...item, status: action === "publish" ? "published" : "disabled", active: action === "publish" }
              : item,
          ),
        }),
      action === "publish" ? "模型已发布，可用于企业授权" : "模型已下线，新任务将不可使用",
    );
  };

  const approveRelayModelRevision = (model) => {
    mutate(
      () => client.approveAdminRelayCapability(model.id, {
        expectedCapabilityVersion: model.capability_version,
      }),
      undefined,
      "中转站能力版本已确认，后续任务会固定使用该版本校验",
    );
  };

  const deleteDraftModel = (model) => {
    if (!globalThis.confirm?.(`确认删除模型草稿“${model.display_name}”？`)) return;
    mutate(
      () => client.deleteAdminModel(model.id),
      () => mergeData({ adminModels: data.adminModels.filter((item) => item.id !== model.id) }),
      "模型草稿已删除",
    );
  };

  const submitDrawer = (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    let permissionCatalog = null;
    if (drawer.type === "roles" || drawer.type === "role") {
      try {
        permissionCatalog = requirePermissionCatalog(data.permissions);
      } catch (catalogError) {
        setDrawerError(catalogError.message);
        return;
      }
    }
    if (drawer.type === "invitation") {
      const payload = {
        email: form.get("email"),
        displayName: form.get("displayName"),
        primaryRole: form.get("primaryRole"),
        expiresInHours: Number(form.get("expiresInHours")),
        idempotencyKey: drawer.idempotencyKey,
      };
      void createCompanyInvitation(payload);
    } else if (drawer.type === "ownerTransfer") {
      const targetMembershipId = String(form.get("targetMembershipId") || "");
      const formerOwnerPrimaryRole = String(form.get("formerOwnerPrimaryRole") || "operator");
      const target = data.members.find((member) => member.membership_id === targetMembershipId);
      if (!target || target.status !== "active") {
        setDrawerError("请选择一位仍在职的组长或运营作为新老板。");
        return;
      }
      mutate(
        async () => {
          await client.transferCompanyOwner({
            targetMembershipId,
            expectedCurrentOwnerMembershipId: data.me.membership_id,
            expectedCurrentOwnerUserId: data.me.user_id,
            formerOwnerPrimaryRole,
          });
          mergeData({ me: await client.getCompanyMe() });
        },
        () => {
          const ownerRole = data.roles.find((role) => role.system_key === "owner");
          const formerRole = data.roles.find((role) => role.system_key === formerOwnerPrimaryRole);
          mergeData({
            me: { ...data.me, roles: [formerRole].filter(Boolean) },
            members: data.members.map((member) => {
              if (member.membership_id === targetMembershipId) return { ...member, roles: [ownerRole].filter(Boolean) };
              if (member.membership_id === data.me.membership_id) return { ...member, roles: [formerRole].filter(Boolean) };
              return member;
            }),
          });
        },
        "老板职责已交接；原老板已切换为所选公司级别",
      );
    } else if (drawer.type === "ownerInvitation") {
      const replacementEmail = String(form.get("replacementEmail") || "").trim();
      const replacementDisplayName = String(form.get("replacementDisplayName") || "").trim();
      if (!replacementEmail && replacementDisplayName) {
        setDrawerError("只有同时填写新的老板邮箱时才能修改老板姓名。");
        return;
      }
      void reissueOwnerInvitation(drawer.company, {
        replacementEmail,
        replacementDisplayName,
      });
    } else if (drawer.type === "roles") {
      const roleIds = [form.get("primaryRoleId"), ...form.getAll("customRoleIds")].filter(Boolean);
      const permissionOverrides = {};
      permissionCatalog.forEach(({ code }) => {
        const effect = form.get(`permission:${code}`);
        if (effect === "allow" || effect === "deny") permissionOverrides[code] = effect;
      });
      const member = drawer.member;
      mutate(
        () => client.replaceMemberAccess(member.membership_id, {
          roleIds,
          permissionOverrides,
          expectedRoleIds: roleList(member).map((role) => role.id),
          expectedPermissionOverrides: permissionOverrideMap(member),
        }),
        () =>
          mergeData({
            members: data.members.map((item) =>
              item.membership_id === member.membership_id
                ? withMemberPermissionState({
                    ...item,
                    roles: data.roles.filter((role) => roleIds.includes(role.id)),
                    permission_overrides: Object.entries(permissionOverrides).map(
                      ([permission_code, effect]) => ({ permission_code, effect }),
                    ),
                  }, data.roles)
                : item,
            ),
          }),
        "成员级别与个人权限已更新",
      );
    } else if (drawer.type === "role") {
      const payload = {
        name: form.get("name"),
        description: form.get("description"),
        permissionCodes: form.getAll("permissionCodes"),
      };
      const existing = drawer.role;
      const nextRoles = existing
        ? data.roles.map((role) => role.id === existing.id
          ? { ...role, name: payload.name, description: payload.description, permission_codes: payload.permissionCodes }
          : role)
        : [
            ...data.roles,
            { id: `role-${Date.now()}`, is_system: false, name: payload.name, description: payload.description, permission_codes: payload.permissionCodes },
          ];
      mutate(
        () => existing ? client.updateRole(existing.id, payload) : client.createRole(payload),
        () =>
          mergeData({
            roles: nextRoles,
            members: data.members.map((member) => withMemberPermissionState(member, nextRoles)),
          }),
        existing
          ? (existing.is_system ? "公司级别权限模板已更新" : "自定义角色已更新")
          : "自定义角色已创建",
      );
    } else if (drawer.type === "company") {
      const payload = {
        name: form.get("name"),
        ownerEmail: form.get("ownerEmail"),
        ownerDisplayName: form.get("ownerDisplayName"),
      };
      void createPlatformCompany(payload);
    } else if (drawer.type === "recharge") {
      const amountCents = Math.round(Number(form.get("amountYuan")) * 100);
      mutate(
        () =>
          client.rechargeAdminCompany(drawer.company.id, {
            amountCents,
            note: form.get("note"),
            idempotencyKey: drawer.idempotencyKey,
          }),
        null,
        `已为 ${drawer.company.name} 充值 ${money(amountCents)}`,
      );
    } else if (drawer.type === "model") {
      let generationConfig;
      try {
        generationConfig = readCapabilityEditor(form);
      } catch (capabilityError) {
        setDrawerError(capabilityError?.message || "模型能力配置不完整。" );
        return;
      }
      const payload = {
        slug: form.get("slug"),
        displayName: form.get("displayName"),
        providerKey: form.get("providerKey"),
        billingMode: form.get("billingMode"),
        capabilities: [{ key: "generation", config: generationConfig }],
      };
      const existing = drawer.model;
      mutate(
        () => existing
          ? client.updateAdminModel(existing.id, {
              displayName: payload.displayName,
              providerKey: payload.providerKey,
              billingMode: payload.billingMode,
              expectedCapabilityVersion: existing.capability_version,
              capabilities: payload.capabilities,
            })
          : client.createAdminModel(payload),
        () =>
          mergeData({
            adminModels: existing
              ? data.adminModels.map((model) => model.id === existing.id
                ? {
                    ...model,
                    display_name: payload.displayName,
                    provider_key: payload.providerKey,
                    billing_mode: payload.billingMode,
                    capability_version: Number(model.capability_version) + 1,
                    capabilities: { generation: generationConfig },
                    effective_capabilities: generationConfig,
                  }
                : model)
              : [
                  ...data.adminModels,
                  {
                    id: `model-${Date.now()}`,
                    slug: payload.slug,
                    display_name: payload.displayName,
                    provider_key: payload.providerKey,
                    billing_mode: payload.billingMode,
                    capability_version: 1,
                    status: "draft",
                    active: false,
                    capabilities: { generation: generationConfig },
                    effective_capabilities: generationConfig,
                  },
                ],
          }),
        existing ? "模型能力版本已更新" : "模型草稿已创建；发布后才可授权给企业",
      );
    } else if (drawer.type === "resource") {
      const existing = drawer.resource;
      const payload = {
        key: existing?.key || form.get("key"),
        kind: existing?.kind || form.get("kind"),
        displayName: form.get("displayName"),
        description: form.get("description"),
        active: form.get("active") === "on",
      };
      mutate(
        () => existing
          ? client.updateAdminResource(existing.id, payload)
          : client.createAdminResource(payload),
        () =>
          mergeData({
            adminResources: existing
              ? data.adminResources.map((resource) => resource.id === existing.id
                ? {
                    ...resource,
                    display_name: payload.displayName,
                    description: payload.description,
                    active: payload.active,
                  }
                : resource)
              : [
                  ...data.adminResources,
                  { id: `res-${Date.now()}`, ...payload, display_name: payload.displayName },
                ],
          }),
        existing ? "功能资源已更新" : "功能资源已创建",
      );
    } else if (drawer.type === "channelCost") {
      const amountCents = Math.round(Number(form.get("amountYuan")) * 100);
      const occurredAtValue = new Date(form.get("occurredAt"));
      if (!Number.isFinite(amountCents)) {
        setDrawerError("请输入有效的渠道成本金额。" );
        return;
      }
      if (Number.isNaN(occurredAtValue.getTime())) {
        setDrawerError("请选择有效的成本发生时间。" );
        return;
      }
      const payload = {
        amountCents,
        idempotencyKey: drawer.idempotencyKey,
        channelKey: form.get("channelKey"),
        channelType: form.get("channelType"),
        occurredAt: occurredAtValue.toISOString(),
        externalReference: form.get("externalReference"),
        companyId: form.get("companyId"),
        taskId: form.get("taskId"),
        relayJobId: form.get("relayJobId"),
        note: form.get("note"),
      };
      mutate(
        () => client.createAdminChannelCost(payload),
        () => {
          const previousCosts = data.channelCosts || { total: 0, total_amount_cents: 0, items: [] };
          const channelCosts = [...(data.dashboard?.channel_costs || [])];
          const channelIndex = channelCosts.findIndex((item) => (
            item.channel_key === payload.channelKey && item.channel_type === payload.channelType
          ));
          if (channelIndex >= 0) {
            channelCosts[channelIndex] = {
              ...channelCosts[channelIndex],
              amount_cents: Number(channelCosts[channelIndex].amount_cents || 0) + amountCents,
            };
          } else {
            channelCosts.push({
              channel_key: payload.channelKey,
              channel_type: payload.channelType,
              amount_cents: amountCents,
            });
          }
          const nextCost = Number(data.dashboard?.channel_cost_cents || 0) + amountCents;
          const nextKnownGrossProfit =
            Number(data.dashboard?.platform_income_cents || 0) - nextCost;
          const costComplete = data.dashboard?.channel_cost_status === "complete";
          mergeData({
            channelCosts: {
              ...previousCosts,
              total: Number(previousCosts.total || 0) + 1,
              total_amount_cents: Number(previousCosts.total_amount_cents || 0) + amountCents,
              items: [
                {
                  id: `cost-${Date.now()}`,
                  amount_cents: amountCents,
                  channel_key: payload.channelKey,
                  channel_type: payload.channelType,
                  occurred_at: payload.occurredAt,
                  external_reference: payload.externalReference,
                  company_id: payload.companyId || null,
                  task_id: payload.taskId || null,
                  relay_job_id: payload.relayJobId || null,
                  note: payload.note,
                  source: "manual",
                },
                ...(previousCosts.items || []),
              ],
            },
            dashboard: {
              ...data.dashboard,
              channel_cost_cents: nextCost,
              known_gross_profit_cents: nextKnownGrossProfit,
              gross_profit_cents: costComplete ? nextKnownGrossProfit : null,
              channel_costs: channelCosts,
            },
          });
        },
        `已录入 ${payload.channelKey} 渠道成本 ${money(amountCents)}`,
      );
    }
  };

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
          ? `任务ID,员工,模型,状态,实际消费\n${filteredDemoTasks
              .map((task) => `${task.task_id},${task.employee_display_name},${task.model_display_name},${task.status},${task.actual_cost_cents ?? ""}`)
              .join("\n")}`
          : `流水ID,任务ID,员工,模型,金额\n${filteredDemoConsumption
              .map((item) => `${item.ledger_entry_id},${item.task_id},${item.employee_display_name},${item.model_display_name},${item.amount_cents}`)
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
        ? `企业,员工,邮箱,模型,计费方式,数量,金额,时间\n${filteredDemoAdminConsumption
            .map((item) => `${item.company_name},${item.employee_display_name},${item.employee_email},${item.model_display_name},${pricingModeLabel(item.pricing_mode)},${item.quantity ?? ""},${item.amount_cents},${item.consumed_at}`)
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

  const renderCompanyOverview = () => {
    const succeeded = data.succeededTaskReport?.total || 0;
    const total = data.taskReport?.total || 0;
    return (
      <>
        <PageHeader eyebrow="公司控制台" title="经营概览" detail="余额、任务与近期资金变动都来自同一个公司账本。">
          <QuietButton onClick={() => load("overview")} disabled={loading}>
            <ArrowClockwise size={16} /> 刷新
          </QuietButton>
        </PageHeader>
        <SummaryStrip
          items={[
            { label: "可用余额", value: money(data.wallet?.available_cents), note: "可用于新任务预留" },
            { label: "任务预留", value: money(data.wallet?.reserved_cents), note: "完成结算，失败释放" },
            { label: "累计任务", value: total, note: `${succeeded} 条已成功` },
            { label: "实际消费", value: money(data.taskReport?.total_actual_cost_cents), note: "按最终结算口径" },
          ]}
        />
        <section className="control-section">
          <div className="control-section-title">
            <div><h2>最近任务</h2><p>只显示服务器持久化的任务记录。</p></div>
            <button type="button" onClick={() => setSection("reports")}>查看完整报表</button>
          </div>
          <div className="control-table-wrap is-mobile-records">
            <table className="control-table is-recent-tasks-table">
              <thead><tr><th>任务</th><th>创建人</th><th>模型</th><th>状态</th><th>结算</th><th>时间</th></tr></thead>
              <tbody>
                {(data.taskReport?.items || []).slice(0, 8).map((task) => (
                  <tr key={task.task_id}>
                    <td className="is-mono">{safeId(task.task_id)}</td>
                    <td>{task.employee_display_name}</td>
                    <td>{task.model_display_name}</td>
                    <td><StatusPill value={task.status} /></td>
                    <td>{task.actual_cost_cents == null ? "待结算" : money(task.actual_cost_cents)}</td>
                    <td>{shortDate(task.created_at)}</td>
                  </tr>
                ))}
                {!(data.taskReport?.items || []).length && <EmptyRows colSpan={6} message="还没有生成任务" />}
              </tbody>
            </table>
          </div>
        </section>
      </>
    );
  };

  const renderMembers = () => (
    <>
      <PageHeader eyebrow="访问控制" title="成员与角色" detail="角色提供默认权限模板；老板还能对每位员工的每一项权限单独允许、禁止或恢复为跟随模板。">
        {canManageUsers && <QuietButton onClick={() => openRoleEditor()} disabled={actionBusy}><ShieldCheck size={16} /> 新建权限角色</QuietButton>}
        {isCurrentOwner && data.members.some((member) => member.membership_id !== data.me?.membership_id && member.status === "active") ? <QuietButton onClick={() => setDrawer({ type: "ownerTransfer" })} disabled={actionBusy}>交接老板职责</QuietButton> : null}
        {canManageUsers && <PrimaryButton onClick={() => setDrawer({ type: "invitation", idempotencyKey: makeOperationKey("company-invitation") })} disabled={actionBusy}><Plus size={16} /> 发邀请</PrimaryButton>}
      </PageHeader>
      <section className="control-section">
        <div className="control-section-title">
          <div><h2>公司成员</h2><p>{data.members.length} 人 · 当前账号不能调整自己或老板的级别，也不能停用自己或老板。</p></div>
        </div>
        {loading && !data.members.length ? (
          <CollectionState state="loading" title="正在读取公司成员" detail="成员、级别和个人权限会一起核验。" />
        ) : error && !data.members.length ? (
          <CollectionState state="error" title="公司成员读取失败" detail={error} onRetry={() => load("members")} />
        ) : (
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-members-table">
            <thead><tr><th>成员</th><th>角色</th><th>状态</th><th>账号 ID</th><th className="is-actions">操作</th></tr></thead>
            <tbody>
              {data.members.map((member) => {
                const roles = roleList(member);
                const overrideCount = Object.keys(permissionOverrideMap(member)).length;
                const isSelf = member.membership_id === data.me?.membership_id;
                const isOwner = roles.some((role) => role.system_key === "owner");
                return (
                  <tr key={member.membership_id}>
                    <td><strong>{member.display_name}</strong><small>{member.email}</small></td>
                    <td>
                      <div className="control-chip-row">{roles.length ? roles.map((role) => <span key={role.id}>{role.name}</span>) : <em>未分配</em>}</div>
                      <small className={overrideCount ? "control-access-note is-adjusted" : "control-access-note"}>
                        {overrideCount ? `个人调整 ${overrideCount} 项` : "跟随角色模板"}
                      </small>
                    </td>
                    <td><StatusPill value={member.status} label={member.status === "disabled" ? "已停用" : undefined} /></td>
                    <td><CopyIdentifier value={member.user_id} label={`${member.display_name}账号 ID`} /></td>
                    <td className="is-actions">
                       <button type="button" onClick={() => openMemberAccess(member)} disabled={!isCurrentOwner || isSelf || isOwner || member.status !== "active" || actionBusy}>级别与权限</button>
                       <button type="button" onClick={() => setMemberStatus(member)} disabled={!canManageUsers || isSelf || isOwner || actionBusy}>{member.status === "active" ? "停用" : "恢复"}</button>
                    </td>
                  </tr>
                );
              })}
              {!data.members.length && <EmptyRows colSpan={5} message="暂无公司成员" />}
            </tbody>
          </table>
        </div>
        )}
      </section>
      {canManageUsers ? (
        <section className="control-section">
          <div className="control-section-title">
            <div><h2>成员邀请</h2><p>账号只有在受邀邮箱完成登录并接受邀请后才会成为正式成员。新链接只在创建或重新发送后显示一次。</p></div>
            <span>{data.invitations.total} 份</span>
          </div>
          <div className="control-table-wrap is-mobile-records">
            <table className="control-table is-invitations-table">
              <thead><tr><th>受邀人</th><th>初始级别</th><th>状态</th><th>有效期</th><th className="is-actions">操作</th></tr></thead>
              <tbody>
                {data.invitations.items.map((invitation) => (
                  <tr key={invitation.id}>
                    <td><strong>{invitation.display_name}</strong><small>{invitation.email}</small></td>
                    <td>{invitation.primary_role === "team_lead" ? "组长" : "运营"}</td>
                    <td><StatusPill value={invitation.status} /></td>
                    <td>{shortDate(invitation.expires_at)}</td>
                    <td className="is-actions">
                      {invitation.invitation_url ? <button type="button" onClick={() => copyInvitationLink(invitation)} disabled={actionBusy}>复制一次性链接</button> : null}
                      <button type="button" onClick={() => reissueCompanyInvitation(invitation)} disabled={actionBusy || invitation.status === "accepted"}>重新发送</button>
                      <button type="button" onClick={() => revokeCompanyInvitation(invitation)} disabled={actionBusy || ["accepted", "revoked"].includes(invitation.status)}>撤销</button>
                    </td>
                  </tr>
                ))}
                {!data.invitations.items.length && <EmptyRows colSpan={5} message="暂无成员邀请" />}
              </tbody>
            </table>
          </div>
          <PageLoadMore loaded={data.invitations.items.length} total={data.invitations.total} busy={paginationBusyKey === "company-invitations"} onLoadMore={() => loadMore("company-invitations")} noun="份邀请" />
        </section>
      ) : null}
      <section className="control-section">
        <div className="control-section-title"><div><h2>级别与权限模板</h2><p>老板可配置组长、运营的默认模板；成员个人设置会在模板之上逐项覆盖。</p></div></div>
        {loading && !data.roles.length ? (
          <CollectionState state="loading" title="正在读取权限模板" detail="完成前不会将空列表误认为没有角色。" />
        ) : error && !data.roles.length ? (
          <CollectionState state="error" title="权限模板读取失败" detail={error} onRetry={() => load("members")} />
        ) : data.roles.length ? (
        <div className="control-role-grid">
          {data.roles.map((role) => {
            const canEditRole = canManageUsers && (
              !role.is_system || (isCurrentOwner && ["team_lead", "operator"].includes(role.system_key))
            );
            return (
              <article key={role.id}>
                <header>
                  <div><strong>{role.name}</strong><small>{role.is_system ? "公司固定级别" : "附加权限角色"}</small></div>
                  <div className="control-role-actions">
                    <span>{(role.permission_codes || []).length} 项</span>
                    {canEditRole && <button type="button" onClick={() => openRoleEditor(role)} disabled={actionBusy}>{role.is_system ? "配置权限" : "编辑"}</button>}
                    {!role.is_system && canManageUsers && <button type="button" onClick={() => deleteCompanyRole(role)} disabled={actionBusy}>删除</button>}
                  </div>
                </header>
                <p>{role.description || "暂无说明"}</p>
                <div className="control-chip-row">{(role.permission_codes || []).map((code) => <span key={code}>{code}</span>)}</div>
              </article>
            );
          })}
        </div>
        ) : (
          <CollectionState title="还没有权限模板" detail="固定公司级别尚未返回，请刷新后再试；拥有成员管理权限时也可以新建附加权限角色。" />
        )}
      </section>
    </>
  );

  const renderCompanyModels = () => {
    const canReadModels = companyPermissions.has("models.read");
    const canReadResources = companyPermissions.has("resources.read");
    return (
    <>
      <PageHeader eyebrow="企业权益" title="模型与功能" detail="这里仅展示平台已发布并授权给当前公司的模型与功能。" />
      <section className="control-section">
        <div className="control-section-title"><div><h2>已授权模型</h2><p>价格由公司授权快照决定，任务提交时再次由服务器校验。</p></div></div>
        {canReadModels ? <div className="control-table-wrap is-model-capability-table is-mobile-records">
          <table className="control-table is-company-models-table">
            <thead><tr><th>模型</th><th>能力版本</th><th>计费方式</th><th>公司单价</th><th>能力</th></tr></thead>
            <tbody>
              {data.models.map((model) => (
                <tr key={model.id}>
                  <td><strong>{model.display_name}</strong><small>{model.slug}</small></td>
                  <td>v{model.capability_version}</td>
                  <td>{model.pricing_mode === "per_second" ? "按秒" : "按条"}</td>
                  <td>{money(model.unit_price_cents)}{model.pricing_mode === "per_second" ? "/秒" : "/条"}</td>
                  <td><ModelCapabilitySummary model={model} compact /></td>
                </tr>
              ))}
              {!data.models.length && <EmptyRows colSpan={5} message="当前公司还没有可用模型" />}
            </tbody>
          </table>
        </div> : <PermissionNotice title="没有模型目录读取权限" detail="此区域没有加载模型数据。请联系公司老板授予 models.read。" />}
      </section>
      <section className="control-section">
        <div className="control-section-title"><div><h2>已开通功能</h2><p>功能与资源由平台管理员统一开通。</p></div></div>
        {canReadResources ? <div className="control-resource-list">
          {data.resources.map((resource) => (
            <article key={resource.id}><span><Key size={20} /></span><div><strong>{resource.display_name}</strong><small>{resource.description}</small></div><code>{resource.key}</code></article>
          ))}
          {!data.resources.length && <p className="control-empty-block">暂无已开通功能</p>}
        </div> : <PermissionNotice title="没有功能资源读取权限" detail="此区域没有加载功能数据。请联系公司老板授予 resources.read。" />}
      </section>
    </>
    );
  };

  const renderReports = () => (
    <>
      <PageHeader eyebrow="用量与审计" title="使用报表" detail="按员工、模型、状态与时间核对任务、消费和产物下载行为。">
        {canExportReports && <QuietButton onClick={() => exportReport("consumption")} disabled={busy}><DownloadSimple size={16} /> 消费 CSV</QuietButton>}
        {canExportReports && <PrimaryButton onClick={() => exportReport("tasks")} disabled={busy}><DownloadSimple size={16} /> 任务 CSV</PrimaryButton>}
      </PageHeader>
      <details className="control-filter-disclosure">
        <summary>
          <SlidersHorizontal size={18} aria-hidden="true" />
          <span>
            <strong>筛选条件</strong>
            <small>
              <span className="is-collapsed-copy">{Object.values(reportFilters).filter(Boolean).length} 项已设置 · 点击展开</span>
              <span className="is-expanded-copy">{Object.values(reportFilters).filter(Boolean).length} 项已设置 · 完成后可收起</span>
            </small>
          </span>
        </summary>
        <form
          className="control-filterbar"
          onSubmit={(event) => {
            event.preventDefault();
            const applied = { ...reportFilters };
            setAppliedReportFilters(applied);
            load("reports", applied);
          }}
        >
        {companyPermissions.has("users.read") ? (
          <label><span>员工</span><select aria-label="按员工筛选使用报表" value={reportFilters.employee_user_id} onChange={(event) => setReportFilters((current) => ({ ...current, employee_user_id: event.target.value }))}><option value="">全部员工</option>{companyReportMembers.map((member) => <option key={member.id} value={member.id}>{member.display_name}</option>)}</select></label>
        ) : (
          <label><span>员工 ID</span><input aria-label="按员工 ID 筛选使用报表" type="search" list="company-report-member-ids" value={reportFilters.employee_user_id} onChange={(event) => setReportFilters((current) => ({ ...current, employee_user_id: event.target.value }))} placeholder="输入员工用户 ID" /><small>未授予成员目录权限，可按已知 ID 精确筛选。</small><datalist id="company-report-member-ids">{companyReportMembers.map((member) => <option key={member.id} value={member.id}>{member.display_name}</option>)}</datalist></label>
        )}
        <label><span>模型</span><select aria-label="按模型筛选使用报表" value={reportFilters.model_id} onChange={(event) => setReportFilters((current) => ({ ...current, model_id: event.target.value }))}><option value="">全部模型</option>{companyReportModels.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</select></label>
        <label><span>任务状态</span><select aria-label="按任务状态筛选使用报表" value={reportFilters.status} onChange={(event) => setReportFilters((current) => ({ ...current, status: event.target.value }))}><option value="">全部状态</option><option value="queued">排队中</option><option value="processing">处理中</option><option value="succeeded">成功</option><option value="failed">失败</option><option value="cancelled">已取消</option></select></label>
        <label><span>开始时间</span><input aria-label="使用报表开始时间" type="datetime-local" value={reportFilters.start_time} max={reportFilters.end_time || undefined} onChange={(event) => setReportFilters((current) => ({ ...current, start_time: event.target.value }))} /></label>
        <label><span>结束时间</span><input aria-label="使用报表结束时间" type="datetime-local" value={reportFilters.end_time} min={reportFilters.start_time || undefined} onChange={(event) => setReportFilters((current) => ({ ...current, end_time: event.target.value }))} /></label>
        <button type="submit">应用筛选</button>
          <span>{demoMode ? filteredDemoTasks.length : data.taskReport?.total || 0} 条任务 · {demoMode ? filteredDemoConsumption.length : data.consumption?.total || 0} 笔消费 · 共 {money(demoMode ? filteredDemoConsumption.reduce((sum, item) => sum + Number(item.amount_cents || 0), 0) : data.consumption?.total_amount_cents)}</span>
        </form>
      </details>
      <section className="control-section">
        <div className="control-section-title"><div><h2>任务明细</h2><p>报价与实际结算分列，失败任务不计费。</p></div></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-report-tasks-table">
            <thead><tr><th>任务</th><th>员工</th><th>模型</th><th>状态</th><th>报价</th><th>实际消费</th><th>创建时间</th></tr></thead>
            <tbody>
              {filteredDemoTasks.map((task) => (
                <tr key={task.task_id}><td className="is-mono">{safeId(task.task_id)}</td><td><strong>{task.employee_display_name}</strong><small>{task.employee_email}</small></td><td>{task.model_display_name}</td><td><StatusPill value={task.status} /></td><td>{money(task.quote_cents)}</td><td>{task.actual_cost_cents == null ? "待结算" : money(task.actual_cost_cents)}</td><td>{shortDate(task.created_at)}</td></tr>
              ))}
              {!filteredDemoTasks.length && <EmptyRows colSpan={7} message="当前筛选条件下没有任务" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.taskReport?.items?.length} total={data.taskReport?.total} busy={paginationBusyKey === "company-tasks"} onLoadMore={() => loadMore("company-tasks")} noun="条任务" />
      </section>
      <section className="control-section">
        <div className="control-section-title"><div><h2>消费明细</h2><p>每笔成功结算记录保留员工、模型、计费方式和价格快照。</p></div><span>{demoMode ? filteredDemoConsumption.length : data.consumption?.total || 0} 笔</span></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-report-consumption-table">
            <thead><tr><th>任务</th><th>员工</th><th>模型</th><th>计费方式</th><th>单价</th><th>数量</th><th>金额</th><th>结算时间</th></tr></thead>
            <tbody>
              {filteredDemoConsumption.map((item) => (
                <tr key={item.ledger_entry_id}><td className="is-mono">{safeId(item.task_id)}</td><td><strong>{item.employee_display_name}</strong><small>{item.employee_email}</small></td><td>{item.model_display_name}</td><td>{pricingModeLabel(item.pricing_mode)}</td><td>{item.unit_price_cents == null ? "-" : `${money(item.unit_price_cents)}/${item.pricing_mode === "per_second" ? "秒" : "条"}`}</td><td>{pricingQuantityLabel(item)}</td><td>{money(item.amount_cents)}</td><td>{shortDate(item.consumed_at)}</td></tr>
              ))}
              {!filteredDemoConsumption.length && <EmptyRows colSpan={8} message="当前筛选条件下没有消费记录" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.consumption?.items?.length} total={data.consumption?.total} busy={paginationBusyKey === "company-consumption"} onLoadMore={() => loadMore("company-consumption")} noun="笔消费" />
      </section>
      <section className="control-section">
        <div className="control-section-title"><div><h2>产物下载审计</h2><p>按公司范围读取。员工与时间筛选适用；模型和任务状态只作用于上方两张表。</p></div><span>{data.downloads?.total || 0} 条</span></div>
        {(appliedReportFilters.model_id || appliedReportFilters.status) && <div className="control-filter-scope-note" role="note">下载记录接口不包含模型和任务状态字段，因此没有套用这两项筛选，避免把未过滤的数据伪装成筛选结果。</div>}
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-download-audit-table">
            <thead><tr><th>任务</th><th>产物</th><th>下载人</th><th>状态</th><th>签发时间</th><th>完成时间</th><th>已传输</th></tr></thead>
            <tbody>
              {(data.downloads?.items || []).map((record) => {
                const state = downloadRecordState(record);
                return (
                  <tr key={record.id}><td className="is-mono">{safeId(record.task_id)}</td><td className="is-mono">{safeId(record.asset_id)}</td><td>{record.requested_by_display_name}</td><td><span className={`download-state is-${state.tone}`} title={state.detail}>{state.label}</span></td><td><strong>{shortDate(record.created_at)}</strong><small>地址有效 {record.expires_seconds} 秒</small></td><td>{record.completed_at ? shortDate(record.completed_at) : "等待存储确认"}</td><td>{record.completed_at ? byteCount(record.bytes_sent) : "-"}</td></tr>
                );
              })}
              {!(data.downloads?.items || []).length && <EmptyRows colSpan={7} message="暂无下载记录" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.downloads?.items?.length} total={data.downloads?.total} busy={paginationBusyKey === "company-downloads"} onLoadMore={() => loadMore("company-downloads")} noun="条下载记录" />
      </section>
    </>
  );

  const renderWallet = () => (
    <>
      <PageHeader eyebrow="财务账本" title="余额流水" detail="所有金额使用人民币分存储；预留、结算和释放构成完整闭环。" />
      <SummaryStrip items={[{ label: "可用余额", value: money(data.wallet?.available_cents), note: "不含任务预留" }, { label: "预留金额", value: money(data.wallet?.reserved_cents), note: "失败自动释放" }, { label: "累计充值", value: money(data.recharges?.total_amount_cents), note: `${data.recharges?.total || 0} 笔充值` }, { label: "账本记录", value: data.ledger.length, note: "服务器持久化" }]} />
      <section className="control-section">
        <div className="control-section-title"><div><h2>充值明细</h2><p>独立列出每笔充值记录，便于与合同和付款凭证逐笔核对。</p></div><span>{data.recharges?.total || 0} 笔</span></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-recharges-table">
            <thead><tr><th>充值金额</th><th>账本备注</th><th>记录 ID</th><th>充值时间</th></tr></thead>
            <tbody>
              {(data.recharges?.items || []).map((entry) => (
                <tr key={entry.id}><td className="is-positive">+{money(entry.amount_cents ?? entry.available_delta_cents)}</td><td>{entry.note || "-"}</td><td className="is-mono">{safeId(entry.id)}</td><td>{shortDate(entry.created_at)}</td></tr>
              ))}
              {!(data.recharges?.items || []).length && <EmptyRows colSpan={4} message="暂无充值记录" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.recharges?.items?.length} total={data.recharges?.total} busy={paginationBusyKey === "company-recharges"} onLoadMore={() => loadMore("company-recharges")} noun="笔充值" />
      </section>
      <section className="control-section">
        <div className="control-section-title"><div><h2>资金流水</h2><p>按时间倒序展示不可变账本记录。</p></div></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-wallet-ledger-table">
            <thead><tr><th>类型</th><th>可用变动</th><th>预留变动</th><th>关联任务</th><th>说明</th><th>时间</th></tr></thead>
            <tbody>
              {data.ledger.map((entry) => (
                <tr key={entry.id}><td><StatusPill value={entry.kind} /></td><td className={Number(entry.available_delta_cents) < 0 ? "is-negative" : "is-positive"}>{Number(entry.available_delta_cents) > 0 ? "+" : ""}{money(entry.available_delta_cents)}</td><td>{Number(entry.reserved_delta_cents) > 0 ? "+" : ""}{money(entry.reserved_delta_cents)}</td><td className="is-mono">{safeId(entry.task_id)}</td><td>{entry.note || "-"}</td><td>{shortDate(entry.created_at)}</td></tr>
              ))}
              {!data.ledger.length && <EmptyRows colSpan={6} message="暂无资金流水" />}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );

  const renderGlobalUsers = () => (
    <>
      <PageHeader
        eyebrow="生产身份边界"
        title="账号生命周期"
        detail="仅平台所有者可查看全局自然人账号并在 ACTIVE 与 SUSPENDED 之间切换；DEACTIVATED 为终态，不能从这里恢复。"
      />
      <section className="control-section">
        <div className="control-section-title">
          <div><h2>全局账号</h2><p>暂停会递增账号安全版本并撤销全部设备会话；写操作在生产环境要求近期强认证。</p></div>
          <span>{data.adminUsers.total} 个</span>
        </div>
        {loading && !data.adminUsers.items.length ? (
          <CollectionState state="loading" title="正在读取账号生命周期" detail="服务端状态确认完成前不会开放停用或恢复操作。" />
        ) : error && !data.adminUsers.items.length ? (
          <CollectionState state="error" title="全局账号读取失败" detail={error} onRetry={() => load("users")} />
        ) : (
          <div className="control-table-wrap is-mobile-records">
            <table className="control-table is-global-users-table">
              <thead><tr><th>账号</th><th>状态</th><th>安全版本</th><th>最近登录</th><th>更新时间</th><th className="is-actions">操作</th></tr></thead>
              <tbody>
                {data.adminUsers.items.map((user) => {
                  const isCurrent = user.id === platformIdentity?.user_id;
                  const mutable = ["active", "suspended"].includes(user.status) && !isCurrent;
                  return (
                    <tr key={user.id}>
                      <td><strong>{user.display_name}</strong><small>{user.email}</small><CopyIdentifier value={user.id} label={`${user.display_name}账号 ID`} /></td>
                      <td><StatusPill value={user.status} /></td>
                      <td className="is-mono">v{user.auth_version}</td>
                      <td>{shortDate(user.last_login_at)}</td>
                      <td>{shortDate(user.updated_at)}</td>
                      <td className="is-actions">
                        <button type="button" disabled={!mutable || actionBusy} onClick={() => setGlobalUserStatus(user)}>
                          {user.status === "active" ? "暂停账号" : user.status === "suspended" ? "恢复账号" : "终态不可恢复"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {!data.adminUsers.items.length && <EmptyRows colSpan={6} message="暂无全局账号" />}
              </tbody>
            </table>
          </div>
        )}
        <PageLoadMore loaded={data.adminUsers.items.length} total={data.adminUsers.total} busy={paginationBusyKey === "platform-users"} onLoadMore={() => loadMore("platform-users")} noun="个账号" />
      </section>
    </>
  );

  const renderPlatformOverview = () => {
    const dashboard = data.dashboard || {};
    const companyRows = dashboard.companies || [];
    const totalTasks = dashboard.total_task_count
      ?? companyRows.reduce((sum, item) => sum + Number(item.task_count || 0), 0);
    const succeededTasks = dashboard.succeeded_task_count
      ?? companyRows.reduce((sum, item) => sum + Number(item.succeeded_count || 0), 0);
    const failedTasks = dashboard.failed_task_count
      ?? companyRows.reduce((sum, item) => sum + Number(item.failed_count || 0), 0);
    const reconciliationComplete = dashboard.channel_cost_status === "complete";
    const displayedGrossProfit = reconciliationComplete
      ? dashboard.gross_profit_cents
      : dashboard.known_gross_profit_cents;
    return (
      <>
        <PageHeader eyebrow="平台运营" title="平台总览" detail="以成功结算为收入，以渠道账单为成本，直接查看平台毛利与企业经营情况。">
          <QuietButton onClick={() => load("overview")} disabled={loading}><ArrowClockwise size={16} /> 刷新</QuietButton>
          <PrimaryButton onClick={() => setDrawer({ type: "channelCost", idempotencyKey: makeOperationKey("channel-cost") })} disabled={!canUsePlatformPermission("platform.provider_costs.manage")}><Plus size={16} /> 录入渠道成本</PrimaryButton>
        </PageHeader>
        <SummaryStrip items={[
          { label: "平台收入", value: money(dashboard.platform_income_cents), note: "企业成功任务实际结算" },
          { label: "渠道成本", value: money(dashboard.channel_cost_cents), note: reconciliationComplete ? "任务成本已完整对账" : "仍有成本等待对账" },
          { label: reconciliationComplete ? "平台毛利" : "已知毛利", value: money(displayedGrossProfit), note: reconciliationComplete ? "收入减已完整对账渠道成本" : "仅扣除已入账渠道成本，最终毛利待对账" },
          { label: reconciliationComplete ? "毛利率" : "已知毛利率", value: grossMarginLabel(dashboard.platform_income_cents, displayedGrossProfit), note: reconciliationComplete ? "毛利占平台收入" : "已知毛利占平台收入，非最终毛利率" },
        ]} />

        <div className="control-finance-status" aria-label="平台经营补充指标">
          <span><strong>{money(dashboard.platform_recharge_cents)}</strong><small>累计充值</small></span>
          <span><strong>{dashboard.active_company_count ?? companyRows.filter((item) => item.company_status === "active").length}</strong><small>正常企业</small></span>
          <span><strong>{totalTasks}</strong><small>任务总数</small></span>
          <span><strong>{succeededTasks}</strong><small>成功任务</small></span>
          <span><strong>{failedTasks}</strong><small>失败任务</small></span>
        </div>

        {!reconciliationComplete && (
          <div className="control-finance-warning" role="status">
            <WarningCircle size={18} weight="fill" />
            <span>渠道成本对账不完整，仍有 {dashboard.unreconciled_succeeded_count || 0} 个成功任务缺少任务级成本。当前仅展示已知毛利与已知毛利率，不能视为最终经营结果。</span>
          </div>
        )}

        <section className="control-section">
          <div className="control-section-title"><div><h2>渠道成本构成</h2><p>按中转站渠道键和渠道类型聚合真实成本。</p></div><span>{(dashboard.channel_costs || []).length} 个渠道</span></div>
          <div className="control-table-wrap">
            <table className="control-table">
              <thead><tr><th>渠道键</th><th>渠道类型</th><th>成本金额</th></tr></thead>
              <tbody>
                {(dashboard.channel_costs || []).map((channel) => (
                  <tr key={`${channel.channel_type}:${channel.channel_key}`}><td><code>{channel.channel_key}</code></td><td>{CHANNEL_TYPE_LABELS[channel.channel_type] || channel.channel_type}</td><td>{money(channel.amount_cents)}</td></tr>
                ))}
                {!(dashboard.channel_costs || []).length && <EmptyRows colSpan={3} message="暂无渠道成本汇总" />}
              </tbody>
            </table>
          </div>
        </section>

        <section className="control-section">
          <div className="control-section-title"><div><h2>成本账单明细</h2><p>每条记录保留外部凭证、发生时间和关联任务，便于财务复核。</p></div><span>{data.channelCosts?.total || 0} 条</span></div>
          <div className="control-table-wrap">
            <table className="control-table">
              <thead><tr><th>渠道</th><th>类型</th><th>金额</th><th>外部凭证</th><th>关联对象</th><th>来源</th><th>发生时间</th></tr></thead>
              <tbody>
                {(data.channelCosts?.items || []).map((item) => (
                  <tr key={item.id}><td><code>{item.channel_key}</code></td><td>{CHANNEL_TYPE_LABELS[item.channel_type] || item.channel_type}</td><td className={Number(item.amount_cents) < 0 ? "is-positive" : "is-negative"}>{money(item.amount_cents)}</td><td className="is-mono">{safeId(item.external_reference)}</td><td><small>{item.company_id ? `企业 ${safeId(item.company_id)}` : ""}{item.task_id ? ` 任务 ${safeId(item.task_id)}` : ""}{item.relay_job_id ? ` Relay ${safeId(item.relay_job_id)}` : ""}{!item.company_id && !item.task_id && !item.relay_job_id ? "未关联" : ""}</small></td><td>{item.source || "manual"}</td><td>{shortDate(item.occurred_at)}</td></tr>
                ))}
                {!(data.channelCosts?.items || []).length && <EmptyRows colSpan={7} message="暂无成本账单" />}
              </tbody>
            </table>
          </div>
          <PageLoadMore loaded={data.channelCosts?.items?.length} total={data.channelCosts?.total} busy={paginationBusyKey === "platform-costs"} onLoadMore={() => loadMore("platform-costs")} noun="条成本记录" />
        </section>

        <section className="control-section">
          <div className="control-section-title"><div><h2>企业经营明细</h2><p>充值、可用余额、结算、预留与任务结果按企业汇总。</p></div><span>{dashboard.total_companies || 0} 家</span></div>
          <div className="control-table-wrap">
            <table className="control-table">
              <thead><tr><th>企业</th><th>状态</th><th>充值</th><th>可用余额</th><th>消费</th><th>预留</th><th>任务</th><th>任务结果</th></tr></thead>
              <tbody>
                {companyRows.map((company) => {
                  const rate = company.task_count ? Math.round((company.succeeded_count / company.task_count) * 100) : 0;
                  return <tr key={company.company_id}><td><strong>{company.company_name}</strong><small>{safeId(company.company_id)}</small></td><td><StatusPill value={company.company_status} /></td><td>{money(company.recharge_cents)}</td><td>{money(company.available_cents)}</td><td>{money(company.consumption_cents)}</td><td>{money(company.reserved_cents)}</td><td>{company.task_count}</td><td><strong>{rate}%</strong><small>{company.succeeded_count} 成功，{company.failed_count} 失败</small></td></tr>;
                })}
                {!companyRows.length && <EmptyRows colSpan={8} message="暂无企业数据" />}
              </tbody>
            </table>
          </div>
          <PageLoadMore loaded={companyRows.length} total={dashboard.total_companies} busy={paginationBusyKey === "platform-dashboard"} onLoadMore={() => loadMore("platform-dashboard")} noun="家企业" />
        </section>
      </>
    );
  };

  const renderAdminReports = () => (
    <>
      <PageHeader eyebrow="平台财务" title="跨公司消费报表" detail="按公司、员工、模型与结算时间核对全平台实际消费；仅成功结算进入本表。">
        <PrimaryButton onClick={exportAdminReport} disabled={busy}><DownloadSimple size={16} /> 导出 CSV</PrimaryButton>
      </PageHeader>
      <form
        className="control-filterbar"
        onSubmit={(event) => {
          event.preventDefault();
          const applied = { ...adminReportFilters };
          setAppliedAdminReportFilters(applied);
          load("reports", applied);
        }}
      >
        {canUsePlatformPermission("platform.companies.read") ? (
          <label><span>公司</span><select aria-label="按公司筛选平台消费报表" value={adminReportFilters.company_id} onChange={(event) => setAdminReportFilters((current) => ({ ...current, company_id: event.target.value }))}><option value="">全部公司</option>{(data.companies?.items || []).map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</select></label>
        ) : (
          <label><span>公司 ID</span><input aria-label="按公司 ID 筛选平台消费报表" type="search" value={adminReportFilters.company_id} onChange={(event) => setAdminReportFilters((current) => ({ ...current, company_id: event.target.value }))} placeholder="输入企业 ID" /><small>未授予企业目录权限，可按已知 ID 精确筛选。</small></label>
        )}
        <label><span>员工</span><input aria-label="按员工姓名或邮箱筛选平台消费报表" type="search" value={adminReportFilters.employee_query} onChange={(event) => setAdminReportFilters((current) => ({ ...current, employee_query: event.target.value }))} placeholder="姓名或邮箱" /></label>
        {canUsePlatformPermission("platform.models.read") ? (
          <label><span>模型</span><select aria-label="按模型筛选平台消费报表" value={adminReportFilters.model_id} onChange={(event) => setAdminReportFilters((current) => ({ ...current, model_id: event.target.value }))}><option value="">全部模型</option>{data.adminModels.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</select></label>
        ) : (
          <label><span>模型 ID</span><input aria-label="按模型 ID 筛选平台消费报表" type="search" value={adminReportFilters.model_id} onChange={(event) => setAdminReportFilters((current) => ({ ...current, model_id: event.target.value }))} placeholder="输入模型 ID" /><small>未授予模型目录权限，可按已知 ID 精确筛选。</small></label>
        )}
        <label><span>开始时间</span><input aria-label="平台消费报表开始时间" type="datetime-local" value={adminReportFilters.start_time} max={adminReportFilters.end_time || undefined} onChange={(event) => setAdminReportFilters((current) => ({ ...current, start_time: event.target.value }))} /></label>
        <label><span>结束时间</span><input aria-label="平台消费报表结束时间" type="datetime-local" value={adminReportFilters.end_time} min={adminReportFilters.start_time || undefined} onChange={(event) => setAdminReportFilters((current) => ({ ...current, end_time: event.target.value }))} /></label>
        <button type="submit">应用筛选</button>
        <span>{demoMode ? filteredDemoAdminConsumption.length : data.adminConsumption?.total || 0} 笔 · 共 {money(demoMode ? filteredDemoAdminConsumption.reduce((sum, item) => sum + Number(item.amount_cents || 0), 0) : data.adminConsumption?.total_amount_cents)}</span>
      </form>
      <section className="control-section">
        <div className="control-section-title"><div><h2>消费明细</h2><p>金额来自公司钱包的成功结算，价格与数量使用任务提交时的快照。</p></div><span>{demoMode ? filteredDemoAdminConsumption.length : data.adminConsumption?.total || 0} 笔</span></div>
        <div className="control-table-wrap">
          <table className="control-table">
            <thead><tr><th>公司</th><th>员工</th><th>模型</th><th>计费方式</th><th>单价</th><th>数量</th><th>金额</th><th>结算时间</th></tr></thead>
            <tbody>
              {filteredDemoAdminConsumption.map((item) => (
                <tr key={item.ledger_entry_id}><td><strong>{item.company_name}</strong><small>{safeId(item.company_id)}</small></td><td><strong>{item.employee_display_name}</strong><small>{item.employee_email}</small></td><td>{item.model_display_name}</td><td>{pricingModeLabel(item.pricing_mode)}</td><td>{item.unit_price_cents == null ? "-" : `${money(item.unit_price_cents)}/${item.pricing_mode === "per_second" ? "秒" : "条"}`}</td><td>{pricingQuantityLabel(item)}</td><td>{money(item.amount_cents)}</td><td>{shortDate(item.consumed_at)}</td></tr>
              ))}
              {!filteredDemoAdminConsumption.length && <EmptyRows colSpan={8} message="当前筛选条件下没有消费记录" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.adminConsumption?.items?.length} total={data.adminConsumption?.total} busy={paginationBusyKey === "platform-consumption"} onLoadMore={() => loadMore("platform-consumption")} noun="笔消费" />
      </section>
    </>
  );

  const renderCompanies = () => (
    <PlatformCompaniesView
      data={data}
      busy={busy}
      ownerInvitationLinks={ownerInvitationLinks}
      paginationBusyKey={paginationBusyKey}
      canUsePlatformPermission={canUsePlatformPermission}
      companyDashboardRow={companyDashboardRow}
      copyOwnerInvitationLink={copyOwnerInvitationLink}
      loadMore={loadMore}
      makeOperationKey={makeOperationKey}
      openCompanyControl={openCompanyControl}
      setCompanyStatus={setCompanyStatus}
      setDrawer={setDrawer}
    />
  );

  const renderAdminModels = () => (
    <PlatformModelsView
      data={data}
      busy={busy}
      canUsePlatformPermission={canUsePlatformPermission}
      approveRelayModelRevision={approveRelayModelRevision}
      deleteDraftModel={deleteDraftModel}
      setDrawer={setDrawer}
      setModelState={setModelState}
    />
  );

  const renderResources = () => (
    <PlatformResourcesView
      data={data}
      canUsePlatformPermission={canUsePlatformPermission}
      setDrawer={setDrawer}
    />
  );

  const renderAudit = () => (
    <PlatformAuditView
      data={data}
      paginationBusyKey={paginationBusyKey}
      loadMore={loadMore}
    />
  );

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
    ? { overview: renderCompanyOverview, members: renderMembers, models: renderCompanyModels, reports: renderReports, wallet: renderWallet }
    : { overview: renderPlatformOverview, users: renderGlobalUsers, companies: renderCompanies, reports: renderAdminReports, models: renderAdminModels, resources: renderResources, audit: renderAudit };
  const content = !nav.length
    ? <ManagementAccessState mode={mode} pending={isResolvingIdentity} />
    : sectionIsAccessible
      ? sectionRenderers[section]?.()
      : <ManagementAccessState mode={mode} pending />;
  const canReturnToStudio = allowedSurfaces.includes("studio");
  const activeSectionLabel = nav.find(([id]) => id === section)?.[1] || "公司管理";

  useEffect(() => {
    const previousTitle = globalThis.document?.title || "旭天 AI VIDEO";
    if (globalThis.document) {
      globalThis.document.title = `${activeSectionLabel} · ${mode === "platform" ? "平台基础配置" : "公司管理"} · 旭天 AI VIDEO`;
    }
    return () => {
      if (globalThis.document) globalThis.document.title = previousTitle;
    };
  }, [activeSectionLabel, mode]);

  return (
    <div
      className={`control-shell is-${mode}-management`}
      data-theme={activeSkin}
      data-section={sectionIsAccessible ? section : "access"}
    >
      <header className="control-topbar">
        {(
          <div className="control-mobile-commandbar">
            {canReturnToStudio ? (
              <button className="control-mobile-home" type="button" onClick={() => onSurfaceChange("studio")} aria-label="返回制作工作区">
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
                {allowedSurfaces.length > 1 && (
                  <section>
                    <span>工作区</span>
                    <div className="surface-switch" aria-label="工作区切换">
                      {allowedSurfaces.includes("studio") && <button type="button" aria-pressed="false" onClick={() => onSurfaceChange("studio")}>制作</button>}
                      {allowedSurfaces.includes("company") && <button className={mode === "company" ? "is-active" : ""} type="button" aria-pressed={mode === "company"} onClick={() => mode !== "company" && onSurfaceChange("company")}>公司</button>}
                      {canAccessPlatform && <button className={mode === "platform" ? "is-active" : ""} type="button" aria-pressed={mode === "platform"} onClick={() => mode !== "platform" && onSurfaceChange("platform")}>平台</button>}
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
                {mode === "company" && !demoMode && companyContexts.length > 0 && (
                  <section className="control-mobile-company-context">
                    <span>当前企业</span>
                    <label className="control-company-context-switcher">
                      <span className="visually-hidden">切换当前企业</span>
                      <select
                        value={activeCompanyId || activeCompanyContext?.company_id || companyContexts[0]?.company_id || ""}
                        disabled={companyContexts.length < 2 || !onCompanyChange}
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
                    <DemoAccountSwitcher value={demoPersonaId} onChange={onDemoPersonaChange} />
                  ) : (
                    <div>
                      <strong>{identity?.display_name || "公司成员"}</strong>
                      <small>{identity?.email || identity?.roles?.map((role) => role.name).join(" · ") || "企业账号"}</small>
                      <button type="button" onClick={onLogout}>退出登录</button>
                    </div>
                  )}
                </section>
                <span className={`mode-badge ${demoMode ? "" : "is-live"}`}>{demoMode ? "演示数据" : "真实 API"}</span>
              </div>
            </details>
          </div>
        )}
        {canReturnToStudio ? (
          <button className="control-brand" type="button" onClick={() => onSurfaceChange("studio")} aria-label="返回旭天制作工作区">
            <BrandLogo variant="wordmark" />
            <strong>视频工作台</strong>
          </button>
        ) : (
          <div className="control-brand is-static" aria-label={`${BRAND_NAME} 平台控制台`}>
            <BrandLogo variant="wordmark" />
            <strong>平台控制台</strong>
          </div>
        )}
        {allowedSurfaces.length > 1 && (
          <div className="surface-switch" aria-label="工作区切换">
            {allowedSurfaces.includes("studio") && <button type="button" aria-pressed="false" onClick={() => onSurfaceChange("studio")}>制作</button>}
            {allowedSurfaces.includes("company") && <button className={mode === "company" ? "is-active" : ""} type="button" aria-pressed={mode === "company"} onClick={() => onSurfaceChange("company")}>公司</button>}
            {canAccessPlatform && <button className={mode === "platform" ? "is-active" : ""} type="button" aria-pressed={mode === "platform"} onClick={() => onSurfaceChange("platform")}>平台</button>}
          </div>
        )}
        {mode === "company" && !demoMode && companyContexts.length > 0 && (
          <label className="control-company-context-switcher is-desktop">
            <span>当前企业</span>
            <select
              value={activeCompanyId || activeCompanyContext?.company_id || companyContexts[0]?.company_id || ""}
              disabled={companyContexts.length < 2 || !onCompanyChange}
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
        <SkinSwitcher value={activeSkin} onChange={onSkinChange} />
        {mode === "platform" && onOpenOperationsConsole ? (
          <button className="control-platform-view-switch" type="button" onClick={onOpenOperationsConsole}>
            <Gauge size={15} />运营指挥台
          </button>
        ) : null}
        <span className={`mode-badge ${demoMode ? "" : "is-live"}`}>{demoMode ? "演示数据" : "真实 API"}</span>
        {demoMode && <DemoAccountSwitcher value={demoPersonaId} onChange={onDemoPersonaChange} />}
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
      </header>

      <aside className="control-sidebar">
        <div className="control-context"><BrandLogo variant="symbol" /><div><strong>{mode === "platform" ? "运营管理平台" : (demoMode ? "远创电商" : (activeCompanyContext?.name || "公司控制台"))}</strong><small title={mode === "platform" ? undefined : (data.me?.company_id || activeCompanyId || undefined)}>{mode === "platform" ? "全局控制面" : safeId(data.me?.company_id || activeCompanyId)}</small></div></div>
        <nav ref={controlNavRef} aria-label={mode === "platform" ? "平台管理导航" : "公司管理导航"}>
          {nav.map(([id, label, Icon]) => (
            <button ref={section === id ? activeNavItemRef : null} key={id} className={section === id ? "is-active" : ""} type="button" onClick={() => setSection(id)} aria-label={label} title={label} aria-current={section === id ? "page" : undefined}><Icon size={19} aria-hidden="true" /><span>{label}</span></button>
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
          title={{ invitation: "邀请公司成员", ownerTransfer: "交接老板职责", ownerInvitation: `重新签发老板邀请 · ${drawer.company?.name || ""}`, roles: `成员访问 · ${drawer.member?.display_name || ""}`, role: drawer.role ? `配置角色 · ${drawer.role.name}` : "新建附加权限角色", company: "新建企业", companyControl: `企业管理 · ${drawer.company?.name || ""}`, recharge: `企业充值 · ${drawer.company?.name || ""}`, model: drawer.model ? `编辑模型 · ${drawer.model.display_name}` : "新建模型草稿", resource: drawer.resource ? `编辑资源 · ${drawer.resource.display_name}` : "新建功能资源", channelCost: "录入渠道成本" }[drawer.type]}
          detail={{ invitation: "先创建待接受邀请；受邀邮箱完成正式登录和接受后，账号才成为运营或组长。", ownerTransfer: "所有权会原子转移给所选在职成员；当前老板同时降为运营或组长。", ownerInvitation: "旧链接会立即失效。留空替换资料会向当前老板重新签发；若最初邮箱有误，可原子换绑尚未激活的老板账号。", roles: "先选唯一的公司级别，再逐项设置个人权限；个人允许或禁止优先于角色模板。", role: drawer.role?.is_system ? "只有老板可配置组长和运营的权限模板，固定级别名称不能修改。" : (drawer.role ? "更新会重新校验操作者权限并写入审计日志。" : "只授予完成工作所需的最小权限。"), company: "原子创建企业、老板成员与独立钱包；生产环境中的老板需通过一次性邀请完成激活。", companyControl: "查看真实权益状态，逐项配置模型价格、功能开关和公司账务。", recharge: "金额将写入不可变账本；请确认企业和数额。", model: drawer.model ? "保存时使用当前能力版本做并发校验。" : "模型必须先发布，随后才能授权给企业。", resource: drawer.resource ? "目录状态会影响后续企业开通，不会伪造已有企业权益。" : "创建后可在企业管理中逐家开通。", channelCost: "正数记录成本，负数记录退款或调整，0 表示已确认零成本。" }[drawer.type]}
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
              onRecharge={() => setDrawer({ type: "recharge", company: drawer.company, idempotencyKey: makeOperationKey("recharge") })}
              onClose={() => setDrawer(null)}
              canManageEntitlements={canUsePlatformPermission("platform.entitlements.manage")}
              canRecharge={canUsePlatformPermission("platform.finance.manage")}
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
            {drawer.type === "recharge" && <><label><span>充值金额（元）</span><input name="amountYuan" type="number" min="0.01" step="0.01" required autoFocus /></label><label><span>账本备注</span><textarea name="note" maxLength={240} placeholder="例如：合同 2026-08 首次充值" /></label><div className="control-form-warning"><Receipt size={18} /><span>充值会立即增加企业可用余额并写入审计日志。</span></div></>}
            {drawer.type === "model" && (
              <>
                <label><span>模型标识</span><input name="slug" required={!drawer.model} disabled={Boolean(drawer.model)} pattern="[a-z0-9]+([.-][a-z0-9]+)*" placeholder="video-model-v1" defaultValue={drawer.model?.slug || ""} /></label>
                <label><span>显示名称</span><input name="displayName" required maxLength={120} defaultValue={drawer.model?.display_name || ""} /></label>
                <label><span>Relay 提供方键</span><input name="providerKey" required pattern="[a-z0-9][a-z0-9._-]*" placeholder="provider-key" defaultValue={drawer.model?.provider_key || ""} /></label>
                <label><span>固定计费方式</span><select name="billingMode" defaultValue={drawer.model?.billing_mode || "per_second"} required><option value="per_second">按秒计费</option><option value="per_item">按条计费</option></select></label>
                <small className="control-form-help">计费方式属于模型目录；发布并授权后不能切换。按秒计费模式单次只允许 1 个产物。</small>
                <CapabilityEditorFields
                  key={drawer.model?.id || "new-model"}
                  model={drawer.model}
                />
              </>
            )}
            {drawer.type === "resource" && <><label><span>资源标识</span><input name="key" required={!drawer.resource} readOnly={Boolean(drawer.resource)} pattern="[a-z0-9][a-z0-9._-]+[a-z0-9]" placeholder="face.library" defaultValue={drawer.resource?.key || ""} /></label><label><span>显示名称</span><input name="displayName" required defaultValue={drawer.resource?.display_name || ""} /></label><label><span>类型</span><select name="kind" defaultValue={drawer.resource?.kind || "feature"} disabled={Boolean(drawer.resource)}><option value="feature">平台功能</option><option value="agent">智能体</option><option value="external_api">外部 API</option></select></label><label><span>说明</span><textarea name="description" maxLength={500} defaultValue={drawer.resource?.description || ""} /></label><label className="control-check"><input name="active" type="checkbox" defaultChecked={drawer.resource ? drawer.resource.active : true} /><span><strong>目录启用</strong><small>关闭后不能再向企业开通此资源</small></span></label></>}
            {drawer.type === "channelCost" && <><div className="control-form-warning"><Receipt size={18} /><span>正数表示成本，负数表示有凭证的退款或调整，0 表示明确的零成本确认。</span></div><div className="control-form-grid"><label><span>渠道键</span><input name="channelKey" required pattern="[a-z0-9][a-z0-9._-]*" placeholder="provider-key" autoFocus /></label><label><span>渠道类型</span><select name="channelType" defaultValue="official" required><option value="reverse">逆向渠道</option><option value="third_party_api">第三方 API</option><option value="official">官方渠道</option></select></label></div><div className="control-form-grid"><label><span>成本金额（元）</span><input name="amountYuan" type="number" step="0.01" required placeholder="正数、负数或 0" /></label><label><span>发生时间</span><input name="occurredAt" type="datetime-local" required defaultValue={localDateTimeInput()} /></label></div><label><span>外部凭证号</span><input name="externalReference" required maxLength={160} placeholder="账单号、流水号或发票号" /></label><label><span>关联企业（可选）</span><select name="companyId" defaultValue=""><option value="">不关联企业</option>{(data.companies?.items || []).map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</select></label><div className="control-form-grid"><label><span>关联任务 ID（可选）</span><input name="taskId" /></label><label><span>Relay 任务 ID（可选）</span><input name="relayJobId" /></label></div><label><span>备注（可选）</span><textarea name="note" maxLength={240} placeholder="说明账单口径或调整原因" /></label></>}
            <footer><QuietButton onClick={() => setDrawer(null)} disabled={busy}>取消</QuietButton><PrimaryButton type="submit" disabled={busy}>{busy ? <><SpinnerGap size={16} className="spin" /> 正在提交</> : <><Check size={16} /> 确认提交</>}</PrimaryButton></footer>
          </form>
          )}
        </Drawer>
      )}

      {toast && <div className="control-toast" role="status"><Check size={17} weight="bold" />{toast}</div>}
    </div>
  );
}

function CompanyEntitlementsPanel({
  drawer,
  drawerError,
  busyKey,
  onReload,
  onLoadMore,
  onSaveModel,
  onToggleResource,
  onRecharge,
  onClose,
  canManageEntitlements = true,
  canRecharge = true,
  paginationBusyKey = "",
}) {
  const access = drawer.access || { entitlements: true, finance: true };
  const availableTabs = [
    ...(access.entitlements ? [
      { id: "models", label: "模型授权" },
      { id: "resources", label: "功能权益" },
    ] : []),
    ...(access.finance ? [{ id: "finance", label: "用量与账务" }] : []),
  ];
  const [tab, setTab] = useState(availableTabs[0]?.id || "models");
  const tabRefs = useRef([]);
  const models = drawer.entitlements?.models || [];
  const resources = drawer.entitlements?.resources || [];
  const [priceDrafts, setPriceDrafts] = useState({});

  useEffect(() => {
    const next = {};
    models.forEach((item) => {
      const cents = item.billing_mode === "per_item"
        ? item.price_per_item_cents
        : item.price_per_second_cents;
      next[item.model_id] = cents == null ? "" : (Number(cents) / 100).toFixed(2);
    });
    setPriceDrafts(next);
  }, [drawer.company?.id, drawer.entitlements]);

  const resourceGroups = useMemo(() => resources.reduce((result, resource) => {
    const kind = resource.kind || "feature";
    if (!result[kind]) result[kind] = [];
    result[kind].push(resource);
    return result;
  }, {}), [resources]);
  const resourceKinds = Object.keys(resourceGroups).sort((left, right) => (
    (RESOURCE_KIND_LABELS[left] || left).localeCompare(RESOURCE_KIND_LABELS[right] || right, "zh-CN")
  ));
  const enabledModels = models.filter((item) => (
    item.enabled && item.status === "published"
  )).length;
  const enabledResources = resources.filter((item) => (
    item.enabled && item.active
  )).length;
  const summary = drawer.summary || {};

  useEffect(() => {
    if (availableTabs.some((item) => item.id === tab)) return;
    setTab(availableTabs[0]?.id || "models");
  }, [access.entitlements, access.finance, tab]);

  const selectTabByKeyboard = (event, index) => {
    let nextIndex = index;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % availableTabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (index - 1 + availableTabs.length) % availableTabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = availableTabs.length - 1;
    else return;
    event.preventDefault();
    const nextTab = availableTabs[nextIndex];
    setTab(nextTab.id);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <div className="control-company-panel">
      <div className="control-company-panel-tabs" role="tablist" aria-label="企业配置分类">
        {availableTabs.map((item, index) => {
          const count = item.id === "models"
            ? `${enabledModels}/${models.length}`
            : item.id === "resources"
              ? `${enabledResources}/${resources.length}`
              : drawer.consumption?.total || 0;
          return (
            <button
              key={item.id}
              ref={(element) => { tabRefs.current[index] = element; }}
              id={`company-control-tab-${item.id}`}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              aria-controls={`company-control-panel-${item.id}`}
              tabIndex={tab === item.id ? 0 : -1}
              className={tab === item.id ? "is-active" : ""}
              onClick={() => setTab(item.id)}
              onKeyDown={(event) => selectTabByKeyboard(event, index)}
            >
              {item.label} <span>{count}</span>
            </button>
          );
        })}
      </div>

      <div className="control-company-panel-body">
        {drawerError && <div className="control-drawer-error" role="alert"><WarningCircle size={18} weight="fill" /><span>{drawerError}</span></div>}

        {tab === "models" && (
          <section className="control-entitlement-section" id="company-control-panel-models" role="tabpanel" aria-labelledby="company-control-tab-models">
            <header><div><h3>模型与企业单价</h3><p>目录计费方式固定，企业只配置对应的单价和开关。</p></div><span>{models.length} 个模型</span></header>
            {drawer.loadingDomains?.entitlements ? (
              <CollectionState state="loading" title="正在读取模型授权" detail="财务页可独立使用，不会等待此请求。" />
            ) : drawer.domainErrors?.entitlements ? (
              <CollectionState state="error" title="模型授权读取失败" detail={drawer.domainErrors.entitlements} onRetry={onReload} />
            ) : <div className="control-entitlement-list is-models">
              {models.map((item) => {
                const cents = item.billing_mode === "per_item"
                  ? item.price_per_item_cents
                  : item.price_per_second_cents;
                const draft = priceDrafts[item.model_id] ?? "";
                const draftCents = Math.round(Number(draft) * 100);
                const priceValid = draft !== "" && Number.isFinite(draftCents) && draftCents > 0;
                const priceChanged = priceValid && draftCents !== Number(cents || 0);
                const pending = busyKey === `model:${item.model_id}`;
                const canEnable = item.status === "published" && priceValid;
                return (
                  <article key={item.model_id}>
                    <div className="control-entitlement-copy"><strong>{item.display_name}</strong><small>{item.slug}</small><span>{pricingModeLabel(item.billing_mode)}计费 · 目录{STATUS_LABELS[item.status] || item.status}</span></div>
                    <label><span>{item.billing_mode === "per_item" ? "每条单价（元）" : "每秒单价（元）"}</span><input type="number" min="0.01" step="0.01" value={draft} onChange={(event) => setPriceDrafts((current) => ({ ...current, [item.model_id]: event.target.value }))} disabled={!canManageEntitlements || pending || (item.status !== "published" && !item.enabled)} placeholder="输入企业单价" /></label>
                    <StatusPill
                      value={item.enabled && item.status === "published" ? "active" : "disabled"}
                      label={item.enabled
                        ? (item.status === "published" ? "已开通" : "历史授权")
                        : "未开通"}
                    />
                    <div className="control-entitlement-actions">
                      {item.enabled ? (
                        <>
                          <button type="button" disabled={!canManageEntitlements || pending || item.status !== "published" || !priceChanged} onClick={() => onSaveModel(item, true, draftCents)}>{pending ? <SpinnerGap size={14} className="spin" /> : null}保存单价</button>
                          <button type="button" className="is-danger" disabled={!canManageEntitlements || pending || !priceValid} onClick={() => onSaveModel(item, false, draftCents)}>停用</button>
                        </>
                      ) : (
                        <button type="button" disabled={!canManageEntitlements || pending || !canEnable} onClick={() => onSaveModel(item, true, draftCents)}>{pending ? <SpinnerGap size={14} className="spin" /> : null}开通</button>
                      )}
                    </div>
                  </article>
                );
              })}
              {!models.length && <p className="control-empty-block">模型目录为空，请先在模型目录创建并发布模型。</p>}
            </div>}
          </section>
        )}

        {tab === "resources" && (
          <section className="control-entitlement-section" id="company-control-panel-resources" role="tabpanel" aria-labelledby="company-control-tab-resources">
            <header><div><h3>功能、智能体与外部 API</h3><p>列表完全来自服务端资源目录，可逐项为当前企业启停。</p></div><span>{resources.length} 项资源</span></header>
            {drawer.loadingDomains?.entitlements ? (
              <CollectionState state="loading" title="正在读取功能权益" detail="财务页可独立使用，不会等待此请求。" />
            ) : drawer.domainErrors?.entitlements ? (
              <CollectionState state="error" title="功能权益读取失败" detail={drawer.domainErrors.entitlements} onRetry={onReload} />
            ) : <>{resourceKinds.map((kind) => (
              <div className="control-entitlement-resource-group" key={kind}>
                <header><strong>{RESOURCE_KIND_LABELS[kind] || kind}</strong><span>{resourceGroups[kind].filter((item) => item.enabled && item.active).length}/{resourceGroups[kind].length} 已开通</span></header>
                <div className="control-entitlement-list is-resources">
                  {resourceGroups[kind].map((item) => {
                    const pending = busyKey === `resource:${item.resource_id}`;
                    return (
                      <article key={item.resource_id}>
                        <div className="control-entitlement-copy"><strong>{item.display_name}</strong><small>{item.key}</small>{!item.active && <span>目录已停用，只能关闭现有权益</span>}</div>
                        <StatusPill
                          value={item.enabled && item.active ? "active" : "disabled"}
                          label={item.enabled
                            ? (item.active ? "已开通" : "历史授权")
                            : "未开通"}
                        />
                        <button type="button" className={item.enabled ? "is-danger" : ""} disabled={!canManageEntitlements || pending || (!item.active && !item.enabled)} onClick={() => onToggleResource(item, !item.enabled)}>{pending ? <SpinnerGap size={14} className="spin" /> : null}{item.enabled ? "停用" : "开通"}</button>
                      </article>
                    );
                  })}
                </div>
              </div>
            ))}
            {!resourceKinds.length && <p className="control-empty-block">资源目录为空，请先在功能资源页创建目录项。</p>}
            </>}
          </section>
        )}

        {tab === "finance" && (
          <section className="control-entitlement-section" id="company-control-panel-finance" role="tabpanel" aria-labelledby="company-control-tab-finance">
            <header><div><h3>企业用量与账务</h3><p>充值和消费分别来自钱包账本及成功任务结算。</p></div><button type="button" onClick={onRecharge} disabled={!canRecharge}><Plus size={15} /> 企业充值</button></header>
            {drawer.loadingDomains?.finance ? (
              <CollectionState state="loading" title="正在读取用量与账务" detail="权益页可独立使用，不会等待此请求。" />
            ) : drawer.domainErrors?.finance ? (
              <CollectionState state="error" title="用量与账务读取失败" detail={drawer.domainErrors.finance} onRetry={onReload} />
            ) : <>
            <dl className="control-company-finance-strip">
              <div><dt>累计充值</dt><dd>{drawer.recharges?.total_amount_cents == null && summary.recharge_cents == null ? "—" : money(drawer.recharges?.total_amount_cents ?? summary.recharge_cents)}</dd></div>
              <div><dt>可用余额</dt><dd>{summary.available_cents == null ? "—" : money(summary.available_cents)}</dd></div>
              <div><dt>已预留</dt><dd>{summary.reserved_cents == null ? "—" : money(summary.reserved_cents)}</dd></div>
              <div><dt>实际消费</dt><dd>{drawer.consumption?.total_amount_cents == null && summary.consumption_cents == null ? "—" : money(drawer.consumption?.total_amount_cents ?? summary.consumption_cents)}</dd></div>
              <div><dt>任务数量</dt><dd>{summary.task_count ?? "—"}</dd></div>
            </dl>

            <div className="control-company-ledger-section">
              <header><strong>充值记录</strong><span>{drawer.recharges?.total || 0} 笔</span></header>
              <div className="control-table-wrap"><table className="control-table"><thead><tr><th>金额</th><th>说明</th><th>时间</th></tr></thead><tbody>{(drawer.recharges?.items || []).map((item) => <tr key={item.id}><td className="is-positive">{money(item.amount_cents ?? item.available_delta_cents)}</td><td>{item.note || "平台充值"}</td><td>{shortDate(item.created_at)}</td></tr>)}{!(drawer.recharges?.items || []).length && <EmptyRows colSpan={3} message="暂无充值记录" />}</tbody></table></div>
              <PageLoadMore loaded={drawer.recharges?.items?.length} total={drawer.recharges?.total} busy={paginationBusyKey === "company-control-recharges"} onLoadMore={() => onLoadMore("recharges")} noun="笔充值" />
            </div>

            <div className="control-company-ledger-section">
              <header><strong>消费记录</strong><span>{drawer.consumption?.total || 0} 笔</span></header>
              <div className="control-table-wrap"><table className="control-table"><thead><tr><th>员工</th><th>模型</th><th>数量</th><th>金额</th><th>时间</th></tr></thead><tbody>{(drawer.consumption?.items || []).map((item) => <tr key={item.ledger_entry_id}><td><strong>{item.employee_display_name}</strong><small>{item.employee_email}</small></td><td>{item.model_display_name}</td><td>{pricingQuantityLabel(item)}</td><td>{money(item.amount_cents)}</td><td>{shortDate(item.consumed_at)}</td></tr>)}{!(drawer.consumption?.items || []).length && <EmptyRows colSpan={5} message="暂无成功结算消费" />}</tbody></table></div>
              <PageLoadMore loaded={drawer.consumption?.items?.length} total={drawer.consumption?.total} busy={paginationBusyKey === "company-control-consumption"} onLoadMore={() => onLoadMore("consumption")} noun="笔消费" />
            </div>
            </>}
          </section>
        )}
      </div>

      <footer className="control-company-panel-footer"><QuietButton onClick={onClose} disabled={Boolean(busyKey)}>关闭</QuietButton><QuietButton onClick={onReload} disabled={Boolean(busyKey) || Object.values(drawer.loadingDomains || {}).some(Boolean)}><ArrowClockwise size={16} /> 刷新可访问数据</QuietButton></footer>
    </div>
  );
}

function CapabilityEditorFields({ model }) {
  const initial = useMemo(() => resolveEffectiveCapabilities(model ?? {}), [model]);
  const initialModeIds = Object.keys(initial.modes);
  const [selectedModes, setSelectedModes] = useState(
    initialModeIds.length ? initialModeIds : ["text_to_video"],
  );
  const values = useMemo(() => {
    const result = {};
    GENERATION_MODES.forEach(({ id }) => {
      result[id] = initial.modes[id] ?? defaultEditorCapability(id);
    });
    return result;
  }, [initial]);

  const toggleMode = (id, checked) => {
    setSelectedModes((current) => (
      checked
        ? [...new Set([...current, id])]
        : current.filter((item) => item !== id)
    ));
  };

  return (
    <fieldset className="capability-editor">
      <legend>生成能力</legend>
      <p className="control-form-help capability-editor-intro">
        每种模式独立声明输入数量和可选参数。保存会写入一个版本化的标准能力配置。
      </p>
      <div className="capability-mode-picker" role="group" aria-label="启用的生成模式">
        {GENERATION_MODES.map(({ id, label }) => (
          <label className="control-check is-compact" key={id}>
            <input
              name="capabilityModes"
              type="checkbox"
              value={id}
              checked={selectedModes.includes(id)}
              onChange={(event) => toggleMode(id, event.target.checked)}
            />
            <span><strong>{label}</strong><small>{id}</small></span>
          </label>
        ))}
      </div>

      <div className="capability-mode-list">
        {GENERATION_MODES.filter(({ id }) => selectedModes.includes(id)).map(({ id, label, requiredMedia }) => {
          const capability = values[id];
          const prefix = `cap.${id}`;
          return (
            <section className="capability-mode-panel" key={id} aria-labelledby={`capability-${id}`}>
              <header>
                <div>
                  <strong id={`capability-${id}`}>{label}</strong>
                  <small>{requiredMedia === "image" ? "至少 1 张图片" : requiredMedia === "video" ? "至少 1 个视频" : "可使用纯文本"}</small>
                </div>
                <code>{id}</code>
              </header>

              <div className="control-form-grid capability-limit-grid">
                <label><span>图片上限</span><input name={`${prefix}.maxImages`} type="number" min={requiredMedia === "image" ? 1 : 0} max="15" required defaultValue={capability.limits.maxImages} /></label>
                <label><span>视频上限</span><input name={`${prefix}.maxVideos`} type="number" min={requiredMedia === "video" ? 1 : 0} max="15" required defaultValue={capability.limits.maxVideos} /></label>
                <label><span>音频上限</span><input name={`${prefix}.maxAudio`} type="number" min="0" max="15" required defaultValue={capability.limits.maxAudio} /></label>
              </div>

              <div className="control-form-grid">
                <label><span>时长选项（秒，最大 3600）</span><input name={`${prefix}.durations`} required placeholder="5, 10" defaultValue={capability.limits.durations.join(", ")} /></label>
                <label><span>提示词上限</span><input name={`${prefix}.maxPromptLength`} type="number" min="1" max="10000" required defaultValue={capability.limits.maxPromptLength} /></label>
              </div>

              <div className="control-form-grid">
                <label><span>画面比例</span><input name={`${prefix}.aspectRatios`} required placeholder="16:9, 9:16, 1:1" defaultValue={capability.limits.aspectRatios.join(", ")} /></label>
                <label><span>分辨率</span><input name={`${prefix}.resolutions`} required placeholder="720p, 1080p, 4K" defaultValue={capability.limits.resolutions.join(", ")} /></label>
              </div>

              <fieldset className="capability-option-group">
                <legend>单次产物数（1-16）</legend>
                <div className="capability-option-row">
                  {Array.from({ length: 16 }, (_, index) => index + 1).map((value) => (
                    <label className="control-check is-compact" key={value}>
                      <input name={`${prefix}.outputCounts`} type="checkbox" value={value} defaultChecked={capability.limits.outputCounts.includes(value)} />
                      <span><strong>{value} 个</strong></span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <label className="capability-face-toggle">
                <input name={`${prefix}.supportsFace`} type="checkbox" defaultChecked={capability.supportsFace} />
                <span><strong>支持人脸能力</strong><small>制作台会在此模式下显示人脸开关。</small></span>
              </label>
              <label>
                <span>所需功能资源 Key</span>
                <input name={`${prefix}.requiredResourceKeys`} placeholder="例如：face.library" defaultValue={capability.requiredResourceKeys.join(", ")} />
              </label>
            </section>
          );
        })}
      </div>
      {!selectedModes.length && (
        <p className="control-drawer-error" role="alert">请至少启用一种生成模式。</p>
      )}
    </fieldset>
  );
}

function PlatformManagementRouter(props) {
  const [view, setView] = useState("operations");
  const [legacyContext, setLegacyContext] = useState({
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
          setLegacyContext({ identity, section });
          setView("legacy");
        }}
      />
      </Suspense>
    </div>
  );

  return (
    <>
      {operationsSurface}
      {view === "legacy" ? (
        <LegacyManagementConsole
          {...props}
          initialPlatformIdentity={legacyContext.identity}
          initialPlatformSection={legacyContext.section}
          onOpenOperationsConsole={returnToOperations}
        />
      ) : null}
    </>
  );
}

export function ManagementConsole(props) {
  if (props.mode === "platform") return <PlatformManagementRouter {...props} />;
  return <LegacyManagementConsole {...props} />;
}
