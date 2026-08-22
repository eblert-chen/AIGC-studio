import assert from "node:assert/strict";
import test from "node:test";

import { createPlatformClient } from "../src/api/platformClient.js";

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

test("personal and session endpoints never carry a company context header", async () => {
  const requests = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-must-not-leak",
    accessToken: "bearer-session",
    fetcher: async (url, options) => {
      requests.push({ url, options });
      return jsonResponse({ items: [], total: 0, page: 1, page_size: 24 });
    },
  });

  await client.getSessionSurfaces();
  await client.getPersonalMe();
  await client.getPersonalWallet();
  await client.listPersonalModels();
  await client.listPersonalTasks({ page: 2, page_size: 24, status: "succeeded" });
  await client.getPersonalTask("task/a");
  await client.getPersonalArtifactPreview("task/a", "asset/b");
  await client.getPersonalArtifactDownload("task/a", "asset/b");
  await client.listPersonalArtworks({ page: 1, page_size: 24, media_type: "video" });

  assert.deepEqual(requests.map(({ url }) => url), [
    "https://platform.example/api/v1/session/surfaces",
    "https://platform.example/api/v1/personal/me",
    "https://platform.example/api/v1/personal/wallet",
    "https://platform.example/api/v1/personal/models",
    "https://platform.example/api/v1/personal/tasks?page=2&page_size=24&status=succeeded",
    "https://platform.example/api/v1/personal/tasks/task%2Fa",
    "https://platform.example/api/v1/personal/tasks/task%2Fa/artifacts/asset%2Fb/preview",
    "https://platform.example/api/v1/personal/tasks/task%2Fa/artifacts/asset%2Fb/download",
    "https://platform.example/api/v1/personal/artworks?page=1&page_size=24&media_type=video",
  ]);
  for (const { options } of requests) {
    assert.equal(options.headers["X-Company-ID"], undefined);
    assert.equal(options.headers.Authorization, "Bearer bearer-session");
    assert.equal(options.headers["X-User-ID"], undefined);
  }
});

test("personal task submission keeps the capability, quote and idempotency contract", async () => {
  const requests = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-must-not-leak",
    accessToken: "bearer-session",
    fetcher: async (url, options) => {
      requests.push({ url, options });
      return jsonResponse({ id: "personal-task-1", status: "accepted" }, 201);
    },
  });

  const payload = {
    modelId: "retail-model-1",
    requestPayload: {
      mode: "text_to_video",
      prompt: "个人创作",
      duration_seconds: 5,
      aspect_ratio: "16:9",
      resolution: "720p",
      output_count: 1,
    },
    expectedCapabilityVersion: 3,
    expectedQuoteRevision: `sha256:${"a".repeat(64)}`,
  };
  await client.createPersonalTask(payload, { idempotencyKey: "personal-idem-1" });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "https://platform.example/api/v1/personal/tasks");
  assert.equal(requests[0].options.headers["X-Company-ID"], undefined);
  assert.equal(requests[0].options.headers["Idempotency-Key"], "personal-idem-1");
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    model_id: "retail-model-1",
    idempotency_key: "personal-idem-1",
    request_payload: payload.requestPayload,
    expected_capability_version: 3,
    expected_quote_revision: payload.expectedQuoteRevision,
  });
});
