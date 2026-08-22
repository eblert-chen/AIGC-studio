import assert from "node:assert/strict";
import test from "node:test";

import {
  PlatformApiError,
  createPlatformClient,
} from "../src/api/platformClient.js";


function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}


test("uses the section 5 entitlement, resource, and channel-cost contracts", async () => {
  const captured = [];
  const responses = [
    {
      company_id: "company/a",
      models: [
        {
          model_id: "model/a",
          grant_id: null,
          enabled: false,
          price_per_item_cents: null,
        },
      ],
      resources: [
        {
          resource_id: "resource/a",
          key: "feature.video",
          grant_id: "grant/a",
          enabled: true,
        },
      ],
    },
    {
      id: "resource/a",
      key: "feature.video",
      kind: "feature",
      display_name: "视频功能（维护）",
      description: "暂停新开通",
      active: false,
    },
    {
      page: 2,
      page_size: 25,
      total: 1,
      total_amount_cents: -35,
      items: [
        {
          id: "cost/a",
          amount_cents: -35,
          channel_key: "kling/official",
          channel_type: "official",
        },
      ],
    },
    {
      id: "cost/new",
      amount_cents: -35,
      idempotency_key: "channel-cost-stable-1",
      source: "platform_admin",
      recorded_by_user_id: "admin-1",
    },
  ];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "current-company",
    accessToken: "token-admin",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return jsonResponse(responses[captured.length - 1]);
    },
  });

  const entitlements = await client.getAdminCompanyEntitlements("company/a");
  const resource = await client.updateAdminResource("resource/a", {
    displayName: "视频功能（维护）",
    description: "暂停新开通",
    active: false,
  });
  const costs = await client.listAdminChannelCosts({
    page: 2,
    page_size: 25,
    company_id: "company/a",
    channel_key: "kling/official",
    channel_type: "official",
    start_time: "2026-08-05T00:00:00.000Z",
    end_time: "2026-08-06T00:00:00.000Z",
  });
  const createdCost = await client.createAdminChannelCost({
    amountCents: -35,
    idempotencyKey: "channel-cost-stable-1",
    channelKey: "kling/official",
    channelType: "official",
    occurredAt: "2026-08-05T01:02:03.000Z",
    externalReference: "invoice/a",
    companyId: "company/a",
    taskId: "task/a",
    relayJobId: "relay/a",
    note: "渠道退款调整",
  });

  assert.equal(entitlements.company_id, "company/a");
  assert.equal(entitlements.models[0].enabled, false);
  assert.equal(entitlements.resources[0].key, "feature.video");
  assert.equal(resource.active, false);
  assert.equal(costs.total_amount_cents, -35);
  assert.equal(createdCost.source, "platform_admin");

  assert.deepEqual(
    captured.map(({ url }) => url),
    [
      "https://platform.example/api/v1/platform-admin/companies/company%2Fa/entitlements",
      "https://platform.example/api/v1/platform-admin/resources/resource%2Fa",
      "https://platform.example/api/v1/platform-admin/channel-costs?page=2&page_size=25&company_id=company%2Fa&channel_key=kling%2Fofficial&channel_type=official&start_time=2026-08-05T00%3A00%3A00.000Z&end_time=2026-08-06T00%3A00%3A00.000Z",
      "https://platform.example/api/v1/platform-admin/channel-costs",
    ],
  );
  assert.equal(captured[0].options.method, "GET");
  assert.equal(captured[1].options.method, "PUT");
  assert.deepEqual(JSON.parse(captured[1].options.body), {
    display_name: "视频功能（维护）",
    description: "暂停新开通",
    active: false,
  });
  assert.equal(captured[2].options.method, "GET");
  assert.equal(captured[3].options.method, "POST");
  assert.deepEqual(JSON.parse(captured[3].options.body), {
    amount_cents: -35,
    idempotency_key: "channel-cost-stable-1",
    channel_key: "kling/official",
    channel_type: "official",
    occurred_at: "2026-08-05T01:02:03.000Z",
    external_reference: "invoice/a",
    company_id: "company/a",
    task_id: "task/a",
    relay_job_id: "relay/a",
    note: "渠道退款调整",
  });
  assert.equal(
    captured[3].options.headers["Idempotency-Key"],
    "channel-cost-stable-1",
  );
  for (const { options } of captured) {
    assert.equal(options.headers.Authorization, "Bearer token-admin");
    assert.equal(options.headers["X-User-ID"], undefined);
  }
});


test("channel-cost client requires a caller-stable idempotency key", () => {
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-admin",
    fetcher: async () => {
      throw new Error("request must not be sent");
    },
  });

  assert.throws(
    () =>
      client.createAdminChannelCost({
        amountCents: 0,
        channelKey: "wan.official",
        channelType: "official",
        occurredAt: "2026-08-05T01:02:03.000Z",
        externalReference: "invoice-zero",
      }),
    (error) =>
      error instanceof PlatformApiError &&
      error.code === "IDEMPOTENCY_KEY_REQUIRED",
  );
});
