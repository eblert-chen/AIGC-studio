const PRODUCTION_BUILD = import.meta.env?.PROD === true;

export const DEMO_PERSONAS = PRODUCTION_BUILD ? {} : {
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
      status: "active",
      permission_codes: [],
      roles: [],
    },
  },
};

export const DEMO_PERSONA_OPTIONS = Object.values(DEMO_PERSONAS).map(({ id, label }) => ({
  id,
  label,
}));

export function demoPersona(personaId) {
  return DEMO_PERSONAS[personaId] || DEMO_PERSONAS.operator || null;
}
