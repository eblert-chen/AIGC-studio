import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { managementSource } from "./management-source.mjs";

import { createPlatformClient } from "../src/api/platformClient.js";


test("platform-admin model grant PUT forwards the exact optimistic version token", async () => {
  let captured;
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-context",
    accessToken: "platform-admin-token",
    fetcher: async (url, options) => {
      captured = { url, options };
      return new Response(JSON.stringify({
        id: "grant-1",
        updated_at: "2026-08-28T03:10:00Z",
      }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  await client.upsertAdminModelGrant("company/a", {
    model_id: "model-1",
    enabled: true,
    price_per_item_cents: 120,
    price_per_second_cents: null,
    config_override: {},
    expected_updated_at: "2026-08-28T03:00:00Z",
  });

  assert.equal(
    captured.url,
    "https://platform.example/api/v1/platform-admin/companies/company%2Fa/model-grants",
  );
  assert.equal(captured.options.method, "PUT");
  assert.equal(
    JSON.parse(captured.options.body).expected_updated_at,
    "2026-08-28T03:00:00Z",
  );
});

test("company entitlement editor fails closed on missing or stale model-grant versions", () => {
  assert.match(
    managementSource,
    /if \(item\.grant_id && !item\.grant_updated_at\)[\s\S]*?本次修改尚未提交/,
  );
  assert.match(
    managementSource,
    /expected_updated_at: item\.grant_id \? item\.grant_updated_at : null/,
  );
  assert.match(
    managementSource,
    /grant_updated_at: result\?\.updated_at \|\| result\?\.grant_updated_at \|\| item\.grant_updated_at/,
  );
  assert.match(
    managementSource,
    /mutationError\?\.status === 409[\s\S]*?本次修改没有覆盖服务端数据/,
  );
  assert.doesNotMatch(
    managementSource,
    /mutationError\?\.status === 409[\s\S]{0,300}upsertAdminModelGrant/,
  );
});
