import assert from "node:assert/strict";
import test from "node:test";

import {
  clearAuthSessionState,
  clearInvitationToken,
  clearSessionUiState,
  captureInvitationToken,
  createAuthClient,
  establishInvitationHandoff,
  safeAccountManagementUrl,
  safeReturnTo,
} from "../src/auth/authClient.js";
import { createAuthSessionSync } from "../src/auth/sessionSync.js";
import {
  clearPlatformCsrfToken,
  getPlatformCsrfToken,
  setPlatformCsrfToken,
} from "../src/api/platformClient.js";

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    get length() { return values.size; },
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
    key: (index) => [...values.keys()][index] ?? null,
    snapshot: () => Object.fromEntries(values),
  };
}

function runtime(overrides = {}) {
  return {
    location: {
      origin: "https://app.example",
      pathname: "/works/task-1",
      search: "?panel=result",
      hash: "",
      ...overrides.location,
    },
    sessionStorage: overrides.sessionStorage ?? memoryStorage(),
    localStorage: overrides.localStorage ?? memoryStorage(),
    history: overrides.history ?? { replaceState() {} },
  };
}

test("login return targets stay same-origin and invitation capabilities never enter OIDC URLs", () => {
  const browser = runtime();
  const client = createAuthClient({
    baseUrl: "https://platform.example",
    runtime: browser,
    fetcher: async () => new Response("{}", { headers: { "content-type": "application/json" } }),
  });

  assert.equal(safeReturnTo("https://evil.example/steal", browser), "/");
  assert.equal(safeReturnTo("/auth/callback?code=secret", browser), "/");
  assert.equal(safeReturnTo("/works/task-2?panel=result", browser), "/works/task-2?panel=result");

  const normal = new URL(client.loginUrl({ returnTo: "/works/task-2", prompt: "step_up" }));
  assert.equal(normal.pathname, "/api/v1/auth/login");
  assert.equal(normal.searchParams.get("return_to"), "/works/task-2");
  assert.equal(normal.searchParams.get("prompt"), "step_up");
  const accountPicker = new URL(client.loginUrl({ returnTo: "/invite", prompt: "select_account" }));
  assert.equal(accountPicker.searchParams.get("prompt"), "select_account");

  const invitation = new URL(client.loginUrl({
    returnTo: "/invite?token=query-secret#token=fragment-secret",
    prompt: "invalid-prompt",
  }));
  assert.equal(invitation.searchParams.get("return_to"), "/invite");
  assert.equal(invitation.searchParams.get("prompt"), "login");
  assert.doesNotMatch(invitation.href, /query-secret|fragment-secret/);
});

test("invitation token stays only in memory until preview establishes the HttpOnly handoff", () => {
  const storage = memoryStorage({ "ai-video.pending-invitation-token": "legacy-secret" });
  const localStorage = memoryStorage({ "ai-video.pending-invitation-token": "legacy-local-secret" });
  const replacements = [];
  const browser = runtime({
    location: { pathname: "/invite", search: "", hash: "#token=invite-secret" },
    sessionStorage: storage,
    localStorage,
    history: { replaceState: (...args) => replacements.push(args) },
  });

  assert.equal(captureInvitationToken(browser), "invite-secret");
  assert.equal(storage.getItem("ai-video.pending-invitation-token"), null);
  assert.equal(localStorage.getItem("ai-video.pending-invitation-token"), null);
  assert.deepEqual(replacements, []);

  establishInvitationHandoff(browser);
  assert.deepEqual(replacements, [[{}, "", "/invite"]]);
  browser.location.hash = "";
  assert.equal(captureInvitationToken(browser), "");
  clearInvitationToken(browser);
  assert.equal(storage.getItem("ai-video.pending-invitation-token"), null);
});

test("cookie BFF auth requests include credentials and memory CSRF without Authorization", async () => {
  clearPlatformCsrfToken();
  const requests = [];
  const client = createAuthClient({
    baseUrl: "https://platform.example",
    runtime: runtime(),
    fetcher: async (url, options) => {
      requests.push({ url, options });
      if (url.endsWith("/api/v1/auth/session")) {
        return Response.json({
          authenticated: true,
          csrf_token: "csrf-from-session",
          user: { id: "user-1", email: "owner@example.com", display_name: "Owner" },
        });
      }
      return Response.json({ ok: true });
    },
  });

  const session = await client.getSession();
  assert.equal(session.authenticated, true);
  assert.equal(getPlatformCsrfToken(), "csrf-from-session");
  await client.updateAccount({
    displayName: "New Name",
    expectedAuthVersion: 7,
    expectedUpdatedAt: "2026-08-20T09:00:00Z",
  });
  await client.logout();
  await client.deactivateAccount({ expectedAuthVersion: 7 });

  for (const { options } of requests) {
    assert.equal(options.credentials, "include");
    assert.equal(options.redirect, "error");
    assert.equal(options.headers.Authorization, undefined);
  }
  assert.equal(requests[0].options.headers["X-CSRF-Token"], undefined);
  assert.equal(requests[1].options.headers["X-CSRF-Token"], "csrf-from-session");
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    display_name: "New Name",
    expected_auth_version: 7,
    expected_updated_at: "2026-08-20T09:00:00Z",
  });
  assert.deepEqual(JSON.parse(requests[2].options.body), {
    preserve_invitation: false,
  });
  assert.deepEqual(JSON.parse(requests[3].options.body), {
    expected_auth_version: 7,
    confirmation: "DEACTIVATE",
  });
});

test("account switching is the only logout flow that preserves the HttpOnly invitation handoff", async () => {
  setPlatformCsrfToken("csrf-switch");
  const requests = [];
  const client = createAuthClient({
    baseUrl: "https://platform.example",
    runtime: runtime(),
    fetcher: async (url, options) => {
      requests.push({ url, options });
      return new Response(null, { status: 204 });
    },
  });

  await client.logout({ preserveInvitation: true });

  assert.deepEqual(JSON.parse(requests[0].options.body), {
    preserve_invitation: true,
  });
  assert.equal(requests[0].options.headers["X-CSRF-Token"], "csrf-switch");
  clearPlatformCsrfToken();
});

test("invitation preview exchanges the fragment capability for a cookie handoff", async () => {
  setPlatformCsrfToken("csrf-memory");
  const requests = [];
  const client = createAuthClient({
    baseUrl: "https://platform.example",
    runtime: runtime(),
    fetcher: async (url, options) => {
      requests.push({ url, options });
      return Response.json(url.endsWith("/preview")
        ? { id: "invite-1", status: "pending", email: "new@example.com" }
        : { accepted: true });
    },
  });

  await client.getInvitation("invite-secret");
  await client.getInvitation();
  await client.acceptInvitation();

  assert.deepEqual(requests.map(({ url }) => url), [
    "https://platform.example/api/v1/invitations/preview",
    "https://platform.example/api/v1/invitations/preview",
    "https://platform.example/api/v1/invitations/accept",
  ]);
  for (const request of requests) {
    assert.equal(request.options.method, "POST");
    assert.equal(request.options.headers["X-CSRF-Token"], "csrf-memory");
    assert.doesNotMatch(request.url, /invite-secret|token=/);
  }
  assert.deepEqual(JSON.parse(requests[0].options.body), { token: "invite-secret" });
  assert.deepEqual(JSON.parse(requests[1].options.body), {});
  assert.deepEqual(JSON.parse(requests[2].options.body), {});
  assert.doesNotMatch(requests.slice(1).map(({ options }) => options.body).join(""), /invite-secret/);
});

test("account sessions use the bounded production pagination contract", async () => {
  const requests = [];
  const client = createAuthClient({
    baseUrl: "https://platform.example",
    runtime: runtime(),
    fetcher: async (url, options) => {
      requests.push({ url, options });
      return Response.json({ items: [], page: 3, page_size: 50, total: 0 });
    },
  });

  await client.listSessions({ page: 3, pageSize: 50 });

  assert.equal(
    requests[0].url,
    "https://platform.example/api/v1/account/sessions?page=3&page_size=50",
  );
  assert.equal(requests[0].options.credentials, "include");
  assert.equal(requests[0].options.headers.Authorization, undefined);
});

test("logout cleanup removes invitation, legacy identity and sensitive drafts", () => {
  setPlatformCsrfToken("csrf-memory");
  const storage = memoryStorage({
    "ai-video.access-token": "legacy-token",
    "ai-video.company-id": "company-1",
    "ai-video.surface": "studio",
    "ai-video.pending-invitation-token": "invite-secret",
    "ai-video.pending-create:company-1": "sensitive-draft",
    "ai-video.skin": "warm",
  });
  clearSessionUiState(runtime({ sessionStorage: storage }));

  assert.deepEqual(storage.snapshot(), { "ai-video.skin": "warm" });
  assert.equal(getPlatformCsrfToken(), "");
});

test("auth-only invalidation removes legacy raw invitations but preserves unsent drafts", () => {
  setPlatformCsrfToken("csrf-memory");
  const storage = memoryStorage({
    "ai-video.access-token": "legacy-token",
    "ai-video.company-id": "company-1",
    "ai-video.surface": "studio",
    "ai-video.pending-invitation-token": "invite-secret",
    "ai-video.pending-create:company-1": "unsent-draft",
  });
  clearAuthSessionState(runtime({ sessionStorage: storage }));

  assert.deepEqual(storage.snapshot(), {
    "ai-video.pending-create:company-1": "unsent-draft",
  });
  assert.equal(getPlatformCsrfToken(), "");
});

test("cross-tab session events are secret-free, ignore self, and stop after close", () => {
  const channels = [];
  class FakeBroadcastChannel {
    constructor() {
      this.listeners = new Set();
      channels.push(this);
    }
    addEventListener(_type, listener) { this.listeners.add(listener); }
    removeEventListener(_type, listener) { this.listeners.delete(listener); }
    postMessage(data) {
      for (const channel of channels) {
        if (channel === this) continue;
        for (const listener of channel.listeners) listener({ data });
      }
    }
    close() { this.listeners.clear(); }
  }
  const receivedA = [];
  const receivedB = [];
  const base = {
    BroadcastChannel: FakeBroadcastChannel,
    localStorage: { setItem() {}, removeItem() {} },
    addEventListener() {},
    removeEventListener() {},
  };
  const syncA = createAuthSessionSync({
    runtime: { ...base, crypto: { randomUUID: () => "tab-a" } },
    onEvent: (event) => receivedA.push(event),
  });
  const syncB = createAuthSessionSync({
    runtime: { ...base, crypto: { randomUUID: () => "tab-b" } },
    onEvent: (event) => receivedB.push(event),
  });

  const event = syncA.publish("logout", { fullClear: true, preserveInvitation: true });
  assert.equal(receivedA.length, 0);
  assert.equal(receivedB.length, 1);
  assert.deepEqual(receivedB[0], event);
  assert.equal(event.preserve_invitation, true);
  assert.doesNotMatch(JSON.stringify(event), /token|email|user_id|company_id/i);

  syncB.close();
  syncA.publish("deactivated", { fullClear: true });
  assert.equal(receivedB.length, 1);
  syncA.close();
});

test("external account-management links fail closed unless they use HTTPS", () => {
  assert.equal(safeAccountManagementUrl("javascript:alert(1)"), "");
  assert.equal(safeAccountManagementUrl("http://id.example/account"), "");
  assert.equal(
    safeAccountManagementUrl("https://id.example/account"),
    "https://id.example/account",
  );
});
