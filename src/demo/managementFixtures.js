import { ACTIVE_PERMISSION_CATALOG } from "../permissionCatalog.js";
import { withMemberPermissionState } from "../components/management/managementAccess.js";
import { MANAGEMENT_PAGE_SIZE } from "../components/management/legacy/managementSnapshot.js";

const PERMISSIONS = ACTIVE_PERMISSION_CATALOG.map(
  ({ code, description }) => [code, description],
);

const DEVELOPMENT_DEMO_FIXTURES = !import.meta.env.PROD;
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
    billing_unit: "POINT",
    billing_version: 2,
    quote_points: 450,
    actual_cost_points: 420,
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
    billing_unit: "POINT",
    billing_version: 2,
    quote_points: 180,
    actual_cost_points: null,
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
    billing_unit: "POINT",
    billing_version: 2,
    quote_points: 300,
    actual_cost_points: 0,
    created_at: "2026-07-31T12:06:00Z",
  },
] : [];

const DEMO_LEDGER = DEVELOPMENT_DEMO_FIXTURES ? [
  {
    id: "led-1",
    kind: "recharge",
    billing_unit: "POINT",
    billing_version: 2,
    amount_points: 10000,
    available_delta_points: 10000,
    reserved_delta_points: 0,
    note: "平台管理员人工入账",
    created_at: "2026-07-28T03:20:00Z",
  },
  {
    id: "led-2",
    kind: "settle",
    billing_unit: "POINT",
    billing_version: 2,
    amount_points: -420,
    available_delta_points: 30,
    reserved_delta_points: -450,
    task_id: "tsk-9d21c7c4",
    note: "任务完成结算",
    created_at: "2026-08-01T07:56:00Z",
  },
  {
    id: "led-3",
    kind: "release",
    billing_unit: "POINT",
    billing_version: 2,
    amount_points: 0,
    available_delta_points: 300,
    reserved_delta_points: -300,
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
    required_resource_keys: [],
    ...(supportsFace
      ? {
          conditional_required_resource_keys: {
            face_enabled: ["face.library"],
          },
        }
      : {}),
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
  schema_version: 2,
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
    billing_unit: "POINT",
    billing_version: 2,
    unit_price_points: 30,
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
    billing_unit: "POINT",
    billing_version: 2,
    unit_price_points: 180,
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
    billing_unit: "POINT",
    billing_version: 2,
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

export const DEMO_COMPANIES = DEVELOPMENT_DEMO_FIXTURES ? [
  { id: "co-yuanchuang", name: "远创电商", status: "active", created_at: "2026-05-11T08:00:00Z" },
  { id: "co-hailan", name: "海岚文旅", status: "active", created_at: "2026-06-03T08:00:00Z" },
  { id: "co-beichen", name: "北辰教育", status: "suspended", created_at: "2026-06-21T08:00:00Z" },
] : [];

const DEMO_DASHBOARD = DEVELOPMENT_DEMO_FIXTURES ? {
  page: 1,
  page_size: MANAGEMENT_PAGE_SIZE,
  billing_unit: "MIXED",
  billing_version: null,
  platform_recharge_cents: 310000,
  platform_income_cents: 186400,
  platform_recharge_points: 31000,
  platform_consumption_points: 18640,
  channel_cost_cents: 109600,
  known_gross_profit_cents: 76800,
  gross_profit_cents: null,
  channel_cost_status: "incomplete",
  unreconciled_succeeded_count: 2,
  revenue_reconciliation_status: "incomplete",
  unattributed_point_settlement_count: 247,
  finance_status: "incomplete",
  active_company_count: 2,
  total_task_count: 273,
  succeeded_task_count: 247,
  failed_task_count: 15,
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
      billing_unit: "POINT",
      billing_version: 2,
      recharge_cents: 0,
      consumption_cents: 0,
      available_cents: 0,
      reserved_cents: 0,
      recharge_points: 16000,
      consumption_points: 8640,
      available_points: 6640,
      reserved_points: 720,
      task_count: 128,
      succeeded_count: 117,
      failed_count: 6,
    },
    {
      company_id: "co-hailan",
      company_name: "海岚文旅",
      company_status: "active",
      billing_unit: "POINT",
      billing_version: 2,
      recharge_cents: 0,
      consumption_cents: 0,
      available_cents: 0,
      reserved_cents: 0,
      recharge_points: 10000,
      consumption_points: 6200,
      available_points: 3440,
      reserved_points: 360,
      task_count: 94,
      succeeded_count: 86,
      failed_count: 4,
    },
    {
      company_id: "co-beichen",
      company_name: "北辰教育",
      company_status: "suspended",
      billing_unit: "POINT",
      billing_version: 2,
      recharge_cents: 0,
      consumption_cents: 0,
      available_cents: 0,
      reserved_cents: 0,
      recharge_points: 5000,
      consumption_points: 3800,
      available_points: 1200,
      reserved_points: 0,
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

export function demoCompanyEntitlements(companyId) {
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
        billing_unit: model.billing_unit,
        billing_version: model.billing_version,
        grant_id: enabled ? `demo-grant-${companyId}-${model.id}` : null,
        grant_updated_at: enabled ? "2026-08-28T00:00:00.000Z" : null,
        enabled,
        price_per_second_points: enabled && model.billing_mode === "per_second"
          ? model.unit_price_points
          : null,
        price_per_item_points: enabled && model.billing_mode === "per_item"
          ? model.unit_price_points
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

export function demoSnapshot(demoIdentity) {
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
    is_platform_owner: resolvedIdentity.is_platform_owner === true,
    permission_codes: resolvedIdentity.permission_codes || [],
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
    wallet: {
      billing_unit: "POINT",
      billing_version: 2,
      available_points: 8870,
      reserved_points: 180,
    },
    ledger: structuredClone(DEMO_LEDGER),
    recharges: {
      page: 1,
      page_size: 100,
      total: 1,
      billing_unit: "POINT",
      billing_version: 2,
      total_amount_points: 10000,
      items: [structuredClone(DEMO_LEDGER[0])],
    },
    models: structuredClone(DEMO_MODELS.filter((item) => item.status === "published")),
    grants: [
      { id: "grant-1", model_id: "model-cinemox", enabled: true, billing_unit: "POINT", billing_version: 2, price_per_second_points: 30 },
      { id: "grant-2", model_id: "model-rush", enabled: true, billing_unit: "POINT", billing_version: 2, price_per_item_points: 180 },
    ],
    resources: structuredClone(DEMO_RESOURCES),
    taskReport: {
      page: 1,
      page_size: MANAGEMENT_PAGE_SIZE,
      total: DEMO_TASKS.length,
      billing_unit: "POINT",
      billing_version: 2,
      total_actual_cost_points: 420,
      items: structuredClone(DEMO_TASKS),
    },
    succeededTaskReport: {
      page: 1,
      page_size: 1,
      total: DEMO_TASKS.filter((task) => task.status === "succeeded").length,
      billing_unit: "POINT",
      billing_version: 2,
      total_actual_cost_points: 420,
      items: structuredClone(DEMO_TASKS.filter((task) => task.status === "succeeded").slice(0, 1)),
    },
    consumption: {
      page: 1,
      page_size: MANAGEMENT_PAGE_SIZE,
      total: 1,
      billing_unit: "POINT",
      billing_version: 2,
      total_amount_points: 420,
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
          billing_unit: "POINT",
          billing_version: 2,
          unit_price_points: 30,
          quantity: 14,
          amount_points: 420,
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
    relayModelReconcile: {
      status: "skipped",
      error: "",
      error_status: 0,
    },
    personalModelGrants: { items: [], error: "" },
    adminResources: structuredClone(DEMO_RESOURCES),
    channelCosts: structuredClone(DEMO_CHANNEL_COSTS),
    adminConsumption: {
      page: 1,
      page_size: 100,
      total: 1,
      billing_unit: "POINT",
      billing_version: 2,
      total_amount_points: 420,
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
          billing_unit: "POINT",
          billing_version: 2,
          unit_price_points: 30,
          quantity: 14,
          amount_points: 420,
          consumed_at: "2026-08-01T07:56:00Z",
        },
      ],
    },
    audit: { page: 1, page_size: 100, total: DEMO_AUDIT.length, items: structuredClone(DEMO_AUDIT) },
  };
}
