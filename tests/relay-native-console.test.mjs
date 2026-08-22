import assert from "node:assert/strict";
import test from "node:test";

import {
  RelayNativeConsoleGrantError,
  deferRelayNativeConsoleGrantConsumption,
  relayNativeConsoleAuthorizationAllowed,
  relayNativeConsoleAuthorizationDisabledReason,
  relayNativeConsoleBlockReason,
  relayNativeConsoleErrorMessage,
  requestRelayNativeConsoleGrant,
  validateRelayNativeConsoleGrant,
} from "../src/admin/relayNativeConsole.js";

test("only a real platform owner with Relay manage permission can authorize", () => {
  assert.equal(relayNativeConsoleAuthorizationAllowed({
    isPlatformOwner: true,
    canManageRelayHealth: true,
  }), true);
  assert.equal(relayNativeConsoleAuthorizationAllowed({
    isPlatformOwner: false,
    canManageRelayHealth: true,
    environment: "development",
  }), false);
  assert.equal(relayNativeConsoleAuthorizationAllowed({
    isPlatformOwner: true,
    canManageRelayHealth: true,
    demoMode: true,
  }), false);
  assert.match(
    relayNativeConsoleAuthorizationDisabledReason({
      isPlatformOwner: false,
      canManageRelayHealth: true,
    }),
    /仅平台所有者/,
  );
});

test("accepts only the strict native break-glass response and fixed channels path", () => {
  assert.deepEqual(
    validateRelayNativeConsoleGrant({
      url: "https://relay-admin.example/channels",
      mode: "native_break_glass",
    }),
    {
      url: "https://relay-admin.example/channels",
      mode: "native_break_glass",
    },
  );
  assert.deepEqual(
    validateRelayNativeConsoleGrant(
      {
        url: "http://127.0.0.1:3000/channels",
        mode: "native_break_glass",
      },
      { allowLocalHttp: true },
    ),
    {
      url: "http://127.0.0.1:3000/channels",
      mode: "native_break_glass",
    },
  );
});

test("rejects credentials, transport parameters, fragments, insecure hosts, and other paths", () => {
  const invalidPayloads = [
    { url: "https://relay-admin.example/channels", mode: "native_break_glass", token: "secret" },
    { url: "https://relay-admin.example/channels", mode: "normal" },
    { url: "https://root:password@relay-admin.example/channels", mode: "native_break_glass" },
    { url: "https://relay-admin.example/channels?access_token=secret", mode: "native_break_glass" },
    { url: "https://relay-admin.example/channels?", mode: "native_break_glass" },
    { url: "https://relay-admin.example/channels#token=secret", mode: "native_break_glass" },
    { url: "https://relay-admin.example/channels#", mode: "native_break_glass" },
    { url: "http://relay-admin.example/channels", mode: "native_break_glass" },
    { url: "https://relay-admin.example/console/channel", mode: "native_break_glass" },
    { url: "https://relay-admin.example/channels/", mode: "native_break_glass" },
  ];

  for (const payload of invalidPayloads) {
    assert.throws(
      () => validateRelayNativeConsoleGrant(payload, { allowLocalHttp: true }),
      (error) => error instanceof RelayNativeConsoleGrantError,
      JSON.stringify(payload),
    );
  }
});

test("requests one Platform authorization and validates before exposing the URL", async () => {
  let calls = 0;
  const grant = await requestRelayNativeConsoleGrant({
    requestAccess: async () => {
      calls += 1;
      return {
        url: "https://relay-admin.example/channels",
        mode: "native_break_glass",
      };
    },
  });

  assert.equal(calls, 1);
  assert.equal(grant.url, "https://relay-admin.example/channels");
  assert.equal(Object.isFrozen(grant), true);
});

test("defers grant cleanup so the anchor default navigation can read its href", () => {
  const order = [];
  let scheduled;
  deferRelayNativeConsoleGrantConsumption(
    () => order.push("consumed"),
    (callback, delay) => {
      order.push(`scheduled:${delay}`);
      scheduled = callback;
    },
  );

  assert.deepEqual(order, ["scheduled:0"]);
  scheduled();
  assert.deepEqual(order, ["scheduled:0", "consumed"]);
});

test("classifies only durable configuration and permission failures as disabled", () => {
  assert.equal(relayNativeConsoleBlockReason({ status: 503 }), "unconfigured");
  assert.equal(relayNativeConsoleBlockReason({ status: 403 }), "forbidden");
  assert.equal(
    relayNativeConsoleBlockReason({ status: 401, code: "STEP_UP_REQUIRED" }),
    "",
  );
  assert.match(
    relayNativeConsoleErrorMessage({ status: 401, code: "STEP_UP_REQUIRED" }),
    /重新完成平台管理员强认证/,
  );
});
