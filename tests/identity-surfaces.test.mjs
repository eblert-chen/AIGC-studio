import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  allowedSurfacesForIdentity,
  canUseSurface,
  defaultSurfaceForIdentity,
  identityRoleLabel,
  resolveSurfaceForIdentity,
} from "../src/identitySurfaces.js";
import { DEMO_PERSONAS, demoPersona } from "../src/demoIdentitySurfaces.js";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const managementSource = await readFile(
  new URL("../src/ManagementConsole.jsx", import.meta.url),
  "utf8",
);

test("演示账号只获得其权限目录对应的工作区入口", () => {
  const operator = DEMO_PERSONAS.operator.identity;
  const owner = DEMO_PERSONAS.owner.identity;
  const platformAdmin = DEMO_PERSONAS.platform_admin.identity;

  assert.deepEqual(allowedSurfacesForIdentity(operator), ["studio", "company"]);
  assert.deepEqual(allowedSurfacesForIdentity(owner), ["studio", "company"]);
  assert.deepEqual(allowedSurfacesForIdentity(platformAdmin), ["platform"]);

  assert.equal(defaultSurfaceForIdentity(operator), "studio");
  assert.equal(defaultSurfaceForIdentity(owner), "studio");
  assert.equal(defaultSurfaceForIdentity(platformAdmin), "platform");

  assert.equal(canUseSurface(operator, "company"), true);
  assert.equal(canUseSurface(operator, "platform"), false);
  assert.equal(canUseSurface(owner, "company"), true);
  assert.equal(canUseSurface(owner, "platform"), false);
  assert.equal(canUseSurface(platformAdmin, "studio"), false);
  assert.equal(canUseSurface(platformAdmin, "company"), false);
});

test("live 身份尚未解析时不预授予任何工作区", () => {
  assert.deepEqual(allowedSurfacesForIdentity(null), []);
  assert.equal(canUseSurface(null, "studio"), false);
  assert.equal(canUseSurface(null, "company"), false);
  assert.equal(canUseSurface(null, "platform"), false);
});

test("personal identity is an explicit isolated Studio context", () => {
  const personal = {
    workspace_kind: "personal",
    workspace_id: "personal-user-1",
    user_id: "user-1",
    display_name: "林瑶",
    is_personal: true,
    available_surfaces: ["personal", "studio", "company"],
  };

  assert.equal(identityRoleLabel(personal), "个人用户");
  assert.deepEqual(allowedSurfacesForIdentity(personal), ["personal", "studio", "company"]);
  assert.equal(defaultSurfaceForIdentity(personal), "personal");
  assert.equal(resolveSurfaceForIdentity(personal, "personal"), "personal");
  assert.equal(resolveSurfaceForIdentity(personal, "platform"), "personal");
  assert.equal(canUseSurface(personal, "personal"), true);
});

test("陈旧或伪造的工作区记录会回落到当前身份的默认页面", () => {
  const operator = DEMO_PERSONAS.operator.identity;
  const owner = DEMO_PERSONAS.owner.identity;
  const platformAdmin = DEMO_PERSONAS.platform_admin.identity;

  assert.equal(resolveSurfaceForIdentity(operator, "company"), "company");
  assert.equal(resolveSurfaceForIdentity(operator, "platform"), "studio");
  assert.equal(resolveSurfaceForIdentity(owner, "platform"), "studio");
  assert.equal(resolveSurfaceForIdentity(owner, "unknown-surface"), "studio");
  assert.equal(resolveSurfaceForIdentity(platformAdmin, "studio"), "platform");
  assert.equal(resolveSurfaceForIdentity(platformAdmin, "company"), "platform");

  assert.equal(resolveSurfaceForIdentity(owner, "company"), "company");
  assert.equal(resolveSurfaceForIdentity(platformAdmin, "platform"), "platform");
});

test("三个演示 persona 是三个账号，未知 persona 安全回落到运营账号", () => {
  const personas = Object.values(DEMO_PERSONAS);
  const userIds = personas.map(({ identity }) => identity.user_id);

  assert.equal(new Set(userIds).size, personas.length);
  assert.deepEqual(userIds.sort(), ["admin-zhou", "usr-chenmo", "usr-zhangfan"]);

  assert.equal(DEMO_PERSONAS.operator.identity.roles[0].system_key, "operator");
  assert.equal(DEMO_PERSONAS.owner.identity.roles[0].system_key, "owner");
  assert.equal(DEMO_PERSONAS.platform_admin.identity.is_platform_admin, true);
  assert.equal(DEMO_PERSONAS.platform_admin.identity.is_platform_owner, true);
  assert.equal(DEMO_PERSONAS.platform_admin.label, "平台所有者 · 周宁");
  assert.equal(DEMO_PERSONAS.platform_admin.identity.company_id, null);
  assert.deepEqual(DEMO_PERSONAS.platform_admin.identity.roles, []);

  assert.equal(demoPersona("missing-persona").id, "operator");
});

test("老板进入制作与公司页面时保持同一姓名、账号和角色", () => {
  const owner = demoPersona("owner").identity;
  const identityOn = (requestedSurface) => ({
    surface: resolveSurfaceForIdentity(owner, requestedSurface),
    userId: owner.user_id,
    membershipId: owner.membership_id,
    displayName: owner.display_name,
    role: identityRoleLabel(owner),
  });

  const studio = identityOn("studio");
  const company = identityOn("company");

  assert.equal(studio.surface, "studio");
  assert.equal(company.surface, "company");
  assert.deepEqual(
    { ...studio, surface: undefined },
    { ...company, surface: undefined },
  );
  assert.deepEqual(
    {
      userId: owner.user_id,
      displayName: owner.display_name,
      role: identityRoleLabel(owner),
    },
    { userId: "usr-zhangfan", displayName: "张帆", role: "老板" },
  );
});

test("应用只从统一身份策略开放页面，不再保留演示或管理员宽松放行", () => {
  assert.match(
    appSource,
    /const sessionSurfaces = allowedSurfacesForIdentity\(sessionIdentity\);/,
  );
  assert.match(
    appSource,
    /if \(!sessionSurfaces\.includes\(nextSurface\)\) \{[\s\S]*?没有这个工作区的访问权限/,
  );
  assert.match(
    managementSource,
    /const canAccessPlatform = allowedSurfaces\.includes\("platform"\);/,
  );

  assert.doesNotMatch(
    appSource,
    /DEMO_MODE\s*\|\|\s*companyIdentity\?\.is_platform_admin/,
  );
  assert.doesNotMatch(
    managementSource,
    /demoMode\s*\|\|\s*mode\s*===\s*["']platform["']\s*\|\|/,
  );
  assert.doesNotMatch(
    managementSource,
    /demoMode\s*\|\|\s*Boolean\([^)]*is_platform_admin/,
  );
});

test("身份解析完成后才纠正陈旧页面，切换 persona 会重建管理台身份快照", () => {
  assert.match(
    appSource,
    /useEffect\(\(\) => \{\s*if \(!identityResolved\) return;\s*if \(sessionSurfaces\.includes\(surface\)\) return;/,
  );
  assert.match(
    appSource,
    /key=\{DEMO_MODE \? demoPersonaId : effectiveSurface\}/,
  );
  assert.match(
    appSource,
    /demoIdentity=\{DEMO_MODE \? sessionIdentity : null\}/,
  );
  assert.match(
    managementSource,
    /demoMode \? demoSnapshot\(demoIdentity\) : emptySnapshot\(\)/,
  );
});

test("live 身份加载中或解析失败时由专用门禁阻止工作区渲染", () => {
  assert.match(
    appSource,
    /if \(LIVE_MODE && !identityResolved\) \{[\s\S]*?identity-loading/,
  );
  assert.match(
    appSource,
    /if \(LIVE_MODE && identityResolved && \(!sessionIdentity \|\| identityError\)\) \{[\s\S]*?identity-error/,
  );
});
