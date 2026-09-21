import assert from "node:assert/strict";
import test from "node:test";

import {
  availableProductContexts,
  isActiveCompanyContext,
  isCompanyMembershipContext,
  normalizeSessionSurfaces,
  personalCapability,
  personalIdentityFromSession,
  preferredCompanyId,
  sessionAccountKind,
} from "../src/personalWorkspace.js";

test("linked product contexts are explicit metadata and never union active data surfaces", () => {
  const platform = normalizeSessionSurfaces({
    account_type: "platform_admin",
    active_product_context: "platform",
    available_account_kinds: ["platform_admin", "personal"],
    user: { id: "owner-1" },
    platform_admin: { is_platform_owner: true },
    personal: { workspace_id: "must-remain-hidden" },
  });
  assert.deepEqual(availableProductContexts(platform), ["platform", "personal"]);
  assert.equal(platform.personal, null);
  assert.deepEqual(platform.companies, []);
  assert.equal(sessionAccountKind(platform), "platform");

  const personal = normalizeSessionSurfaces({
    account_type: "personal",
    active_product_context: "personal",
    available_product_contexts: ["personal", "platform"],
    user: { id: "owner-1" },
    personal: { workspace_id: "personal-owner-1" },
    platform_admin: { is_platform_owner: true },
  });
  assert.deepEqual(availableProductContexts(personal), ["personal", "platform"]);
  assert.equal(personal.platform_admin, false);
  assert.equal(sessionAccountKind(personal), "personal");
});

test("explicit personal accounts discard company contexts instead of exposing a workspace switch", () => {
  const session = normalizeSessionSurfaces({
    account_type: "personal",
    user: { id: "user-1", email: "user@example.cn", display_name: "林瑶" },
    personal: {
      kind: "personal",
      workspace_id: "personal-user-1",
      label: "林瑶的空间",
      capabilities: {
        generation: true,
        models: true,
        tasks: true,
        artworks: true,
        assets: false,
        artifact_access: true,
        publishing: false,
        task_cancel: false,
      },
    },
    companies: [
      { company_id: "company-1", name: "远创电商", status: "active" },
      { company_id: "company-2", name: "第二家公司", status: "active" },
    ],
    platform_admin: false,
  });
  const identity = personalIdentityFromSession(session);

  assert.equal(session.account_type, "personal");
  assert.equal(sessionAccountKind(session), "personal");
  assert.deepEqual(session.companies, []);
  assert.equal(identity.company_id, null);
  assert.equal(identity.workspace_id, "personal-user-1");
  assert.equal(identity.workspace_kind, "personal");
  assert.deepEqual(identity.available_surfaces, ["personal"]);
  assert.equal(personalCapability(identity, "generation"), true);
  assert.equal(personalCapability(identity, "artifact_access"), true);
  assert.equal(personalCapability(identity, "assets"), false);
  assert.equal(personalCapability(identity, "publishing"), false);
  assert.equal(preferredCompanyId(session, "company-2"), "");
  assert.equal(session.platform_admin, false);
});

test("explicit company accounts discard personal context while preserving company-only switching", () => {
  const session = normalizeSessionSurfaces({
    account_type: "company",
    user: { id: "user-2", email: "member@example.cn", display_name: "周宁" },
    personal: {
      workspace_id: "must-not-be-exposed",
      capabilities: { generation: true },
    },
    companies: [
      { company_id: "company-1", name: "远创电商", status: "active" },
      { company_id: "company-2", name: "第二家公司", status: "active" },
      { company_id: "company-paused", name: "暂停企业", status: "suspended" },
    ],
    platform_admin: false,
  });

  assert.equal(session.account_type, "company");
  assert.equal(sessionAccountKind(session), "company");
  assert.equal(session.personal, null);
  assert.deepEqual(
    session.companies.map((company) => company.company_id),
    ["company-1", "company-2", "company-paused"],
  );
  assert.equal(personalIdentityFromSession(session), null);
  assert.equal(preferredCompanyId(session, "company-2"), "company-2");
  assert.equal(preferredCompanyId(session, "company-paused"), "company-1");
});

test("unknown personal capabilities fail closed", () => {
  const session = normalizeSessionSurfaces({
    account_type: "personal",
    user: { id: "user-1" },
    personal: { workspace_id: "personal-user-1", capabilities: { generation: 1 } },
  });
  const identity = personalIdentityFromSession(session);
  assert.equal(personalCapability(identity, "generation"), false);
  assert.equal(personalCapability(identity, "artifact_access"), false);
  assert.equal(personalCapability(identity, "unknown"), false);
});

test("legacy mixed responses resolve platform over company over personal", () => {
  const platform = normalizeSessionSurfaces({
    user: { id: "legacy-platform" },
    personal: { workspace_id: "legacy-personal" },
    companies: [{ company_id: "legacy-company", status: "active" }],
    platform_admin: true,
  });
  assert.equal(platform.account_type, "platform_admin");
  assert.equal(sessionAccountKind(platform), "platform");
  assert.equal(platform.personal, null);
  assert.deepEqual(platform.companies, []);

  const company = normalizeSessionSurfaces({
    user: { id: "legacy-company-user" },
    personal: { workspace_id: "legacy-personal" },
    companies: [{ company_id: "legacy-company", status: "active" }],
  });
  assert.equal(company.account_type, "company");
  assert.equal(sessionAccountKind(company), "company");
  assert.equal(company.personal, null);
  assert.deepEqual(
    company.companies.map((item) => item.company_id),
    ["legacy-company"],
  );

  const personal = normalizeSessionSurfaces({
    user: { id: "legacy-personal-user" },
    personal: { workspace_id: "legacy-personal" },
  });
  assert.equal(personal.account_type, "personal");
  assert.equal(sessionAccountKind(personal), "personal");
});

test("company membership never falls back to personal when every company is suspended", () => {
  const session = normalizeSessionSurfaces({
    account_type: "company",
    personal: { workspace_id: "must-not-be-restored" },
    companies: [
      { company_id: "company-suspended", name: "停用企业", status: "suspended" },
    ],
  });

  assert.equal(session.personal, null);
  assert.equal(sessionAccountKind(session), "company_unavailable");
  assert.equal(isActiveCompanyContext(session.companies[0]), false);
  assert.equal(isCompanyMembershipContext(session.companies[0]), true);
  assert.equal(preferredCompanyId(session, "company-suspended"), "");
});

test("explicit unavailable accounts discard every forged workspace", () => {
  const session = normalizeSessionSurfaces({
    account_type: "unavailable",
    personal: { workspace_id: "forged-personal" },
    companies: [{ company_id: "forged-company", status: "active" }],
    platform_admin: true,
  });

  assert.equal(session.account_type, "unavailable");
  assert.equal(session.platform_admin, false);
  assert.equal(session.personal, null);
  assert.deepEqual(session.companies, []);
  assert.equal(sessionAccountKind(session), "unavailable");
});
