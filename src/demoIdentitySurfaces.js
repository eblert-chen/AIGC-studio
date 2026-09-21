const PRODUCTION_BUILD = import.meta.env?.PROD === true;

export const DEMO_PERSONAS = PRODUCTION_BUILD ? {} : {
  personal_creator: {
    id: "personal_creator",
    label: "个人创作者 · 林瑶",
    identity: {
      company_id: null,
      user_id: "usr-linyao",
      workspace_id: "personal-linyao",
      workspace_kind: "personal",
      workspace_label: "个人创作",
      display_name: "林瑶",
      email: "linyao@example.cn",
      is_personal: true,
      is_platform_admin: false,
      status: "active",
      personal_capabilities: {
        generation: true,
        models: true,
        tasks: true,
        artworks: true,
        assets: false,
        artifact_access: true,
        publishing: false,
        task_cancel: false,
      },
      permission_codes: [],
      roles: [],
    },
  },
  operator: {
    id: "operator",
    label: "运营 · 陈默",
    identity: {
      company_id: "co-yuanchuang",
      company_name: "远创电商",
      user_id: "usr-chenmo",
      membership_id: "mem-operator",
      display_name: "陈默",
      email: "chenmo@example.cn",
      is_platform_admin: false,
      status: "active",
      permission_codes: [
        "assets.read", "assets.manage", "models.read", "resources.read",
        "tasks.read", "tasks.create", "publish.accounts.read",
        "publish.jobs.read", "publish.jobs.manage",
      ],
      roles: [{ id: "role-operator", name: "运营", system_key: "operator" }],
    },
  },
  owner: {
    id: "owner",
    label: "老板 · 张帆",
    identity: {
      company_id: "co-yuanchuang",
      company_name: "远创电商",
      user_id: "usr-zhangfan",
      membership_id: "mem-owner",
      display_name: "张帆",
      email: "zhangfan@example.cn",
      is_platform_admin: false,
      status: "active",
      permission_codes: [
        "assets.read", "assets.manage", "users.read", "users.manage",
        "models.read", "resources.read", "billing.read", "billing.manage",
        "tasks.read", "tasks.create", "reports.read", "reports.export",
        "publish.accounts.read", "publish.accounts.manage",
        "publish.jobs.read", "publish.jobs.manage",
      ],
      roles: [{ id: "role-owner", name: "老板", system_key: "owner" }],
    },
  },
  platform_admin: {
    id: "platform_admin",
    label: "平台所有者 · 周宁",
    identity: {
      company_id: null,
      user_id: "admin-zhou",
      display_name: "周宁",
      email: "admin@example.cn",
      is_platform_admin: true,
      is_platform_owner: true,
      active_product_context: "platform",
      available_product_contexts: ["platform", "personal"],
      available_account_kinds: ["platform_admin", "personal"],
      status: "active",
      permission_codes: [],
      roles: [],
    },
  },
};

const PLATFORM_OWNER_PERSONAL_CAPABILITIES = Object.freeze({
  generation: true,
  models: true,
  tasks: true,
  artworks: true,
  assets: false,
  artifact_access: true,
  publishing: false,
  task_cancel: false,
});

export function demoIdentityForProductContext(persona, productContext = "") {
  if (!persona?.identity || persona.id !== "platform_admin") {
    return persona?.identity || null;
  }
  if (productContext !== "personal") {
    return {
      ...persona.identity,
      active_product_context: "platform",
    };
  }
  return {
    ...persona.identity,
    company_id: null,
    workspace_id: "personal-admin-zhou",
    workspace_kind: "personal",
    workspace_label: "周宁的个人空间",
    personal_capabilities: PLATFORM_OWNER_PERSONAL_CAPABILITIES,
    permission_codes: [],
    roles: [],
    is_personal: true,
    is_platform_admin: false,
    is_platform_owner: false,
    linked_platform_owner: true,
    active_product_context: "personal",
    available_product_contexts: ["platform", "personal"],
    available_account_kinds: ["platform_admin", "personal"],
  };
}

export const DEMO_PERSONA_OPTIONS = Object.values(DEMO_PERSONAS).map(({ id, label }) => ({
  id,
  label,
}));

export function demoPersona(personaId) {
  return DEMO_PERSONAS[personaId] || DEMO_PERSONAS.operator || null;
}
