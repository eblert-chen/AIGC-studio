import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeSessionSurfaces,
  personalCapability,
  personalIdentityFromSession,
  preferredCompanyId,
} from "../src/personalWorkspace.js";

test("session discovery keeps personal and company contexts separate", () => {
  const session = normalizeSessionSurfaces({
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
    platform_admin: true,
  });
  const identity = personalIdentityFromSession(session, ["personal", "studio", "company"]);

  assert.equal(identity.company_id, null);
  assert.equal(identity.workspace_id, "personal-user-1");
  assert.equal(identity.workspace_kind, "personal");
  assert.equal(personalCapability(identity, "generation"), true);
  assert.equal(personalCapability(identity, "artifact_access"), true);
  assert.equal(personalCapability(identity, "assets"), false);
  assert.equal(personalCapability(identity, "publishing"), false);
  assert.equal(preferredCompanyId(session, "company-2"), "company-2");
  assert.equal(preferredCompanyId(session, "missing"), "company-1");
  assert.equal(session.platform_admin, true);
});

test("unknown personal capabilities fail closed", () => {
  const session = normalizeSessionSurfaces({
    user: { id: "user-1" },
    personal: { workspace_id: "personal-user-1", capabilities: { generation: 1 } },
  });
  const identity = personalIdentityFromSession(session);
  assert.equal(personalCapability(identity, "generation"), false);
  assert.equal(personalCapability(identity, "artifact_access"), false);
  assert.equal(personalCapability(identity, "unknown"), false);
});
