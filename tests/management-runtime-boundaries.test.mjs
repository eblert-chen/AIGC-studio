import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import test, { after } from "node:test";
import { createServer } from "vite";

const vite = await createServer({
  appType: "custom",
  server: {
    hmr: false,
    middlewareMode: true,
    warmup: { clientFiles: [] },
  },
});
after(async () => {
  await vite.close();
});
const { ManagementConfigurationConsole } = await vite.ssrLoadModule("/src/ManagementConsole.jsx");
const managementDemoFixtures = await vite.ssrLoadModule("/src/demo/managementFixtures.js");

const noop = () => {};
const client = new Proxy({}, {
  get() {
    return async () => ({});
  },
});

function renderManagement(mode, section) {
  const identity = mode === "platform"
    ? {
        user_id: "platform-owner",
        display_name: "平台所有者",
        email: "owner@example.cn",
        is_platform_admin: true,
        is_platform_owner: true,
        permission_codes: [],
      }
    : {
        company_id: "co-yuanchuang",
        user_id: "company-owner",
        membership_id: "membership-owner",
        display_name: "企业所有者",
        email: "company-owner@example.cn",
        is_platform_admin: false,
        status: "active",
        permission_codes: [
          "billing.read",
          "models.read",
          "reports.export",
          "reports.read",
          "resources.read",
          "users.manage",
          "users.read",
        ],
        roles: [{ id: "role-owner", name: "老板", system_key: "owner" }],
      };
  const location = new URL(
    `http://localhost/?${mode === "company" ? "company_section" : "platform_section"}=${section}`,
  );
  globalThis.location = location;
  return renderToStaticMarkup(React.createElement(ManagementConfigurationConsole, {
    mode,
    client,
    demoMode: true,
    demoIdentity: identity,
    demoPersonaId: mode === "platform" ? "platform_admin" : "operator",
    managementDemoFixtures,
    allowedSurfaces: ["studio", "company", "platform"],
    onDemoPersonaChange: noop,
    onSurfaceChange: noop,
    onLogout: noop,
    onSessionError: noop,
    onSkinChange: noop,
    initialPlatformIdentity: identity,
    initialPlatformSection: section,
    onOpenOperationsConsole: noop,
    companyContexts: [{ company_id: "co-yuanchuang", name: "远创电商" }],
    activeCompanyId: "co-yuanchuang",
    onCompanyChange: noop,
  }));
}

test("every company management section renders through its extracted boundary", () => {
  for (const [section, heading] of [
    ["overview", "经营概览"],
    ["members", "成员与角色"],
    ["models", "模型与功能"],
    ["reports", "使用报表"],
    ["wallet", "权益流水"],
  ]) {
    assert.match(renderManagement("company", section), new RegExp(heading));
  }
});

test("every platform basic section renders through its extracted boundary", () => {
  for (const [section, heading] of [
    ["overview", "平台总览"],
    ["users", "账号生命周期"],
    ["companies", "企业管理"],
    ["reports", "消费报表"],
    ["models", "模型目录"],
    ["resources", "功能资源"],
    ["audit", "操作审计"],
  ]) {
    assert.match(renderManagement("platform", section), new RegExp(heading));
  }
});
