import assert from "node:assert/strict";
import test from "node:test";
import {
  PlatformApiError,
  clearPlatformCsrfToken,
  createPlatformClient,
  parseArtifactDownloadUrl,
  readRuntimePlatformConfig,
  setPlatformCsrfToken,
} from "../src/api/platformClient.js";

test("rejects live requests when the API or company is not configured", async () => {
  const noApi = createPlatformClient({
    baseUrl: "",
    companyId: "company-1",
    accessToken: "token-1",
    fetcher: async () => {
      throw new Error("fetch should not be called");
    },
  });
  const noCompany = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "",
    accessToken: "token-1",
    fetcher: async () => {
      throw new Error("fetch should not be called");
    },
  });

  await assert.rejects(
    () => noApi.listModels(),
    (error) =>
      error instanceof PlatformApiError &&
      error.code === "PLATFORM_API_NOT_CONFIGURED",
  );
  assert.throws(
    () => noCompany.listTasks(),
    (error) =>
      error instanceof PlatformApiError &&
      error.code === "COMPANY_ID_NOT_CONFIGURED",
  );
});

test("uses company-scoped paths with an explicit Bearer identity", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example/",
    companyId: "company/a",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  await client.listModels();
  await client.listTasks();
  await client.getTask("task/a");

  assert.deepEqual(
    captured.map(({ url }) => url),
    [
      "https://platform.example/api/v1/companies/company%2Fa/models",
      "https://platform.example/api/v1/companies/company%2Fa/tasks",
      "https://platform.example/api/v1/companies/company%2Fa/tasks/task%2Fa",
    ],
  );
  for (const { options } of captured) {
    assert.equal(options.headers["X-Company-ID"], "company/a");
    assert.equal(options.headers.Authorization, "Bearer token-1");
    assert.equal(options.headers["X-User-ID"], undefined);
    assert.ok(options.headers["X-Request-Id"]);
    assert.equal(options.credentials, "omit");
    assert.equal(options.redirect, "error");
  }
});

test("creates a company task using the backend contract and idempotency identifiers", async () => {
  let captured;
  const client = createPlatformClient({
    baseUrl: "https://platform.example/",
    companyId: "company-1",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      captured = { url, options };
      return new Response(JSON.stringify({ id: "task-1", status: "queued" }), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    },
  });

  const result = await client.createTask(
    {
      modelId: "model-1",
      expectedCapabilityVersion: 7,
      expectedQuoteRevision: `sha256:${"a".repeat(64)}`,
      requestPayload: {
        prompt: "test prompt",
        duration_seconds: 5,
        aspect_ratio: "16:9",
        resolution: "1080p",
        face_enabled: true,
      },
    },
    { idempotencyKey: "idem-task-1" },
  );

  assert.equal(
    captured.url,
    "https://platform.example/api/v1/companies/company-1/tasks",
  );
  assert.equal(captured.options.method, "POST");
  assert.equal(captured.options.headers["Idempotency-Key"], "idem-task-1");
  assert.equal(captured.options.headers["X-Company-ID"], "company-1");
  assert.equal(captured.options.headers.Authorization, "Bearer token-1");
  assert.equal(captured.options.headers["X-User-ID"], undefined);
  assert.deepEqual(JSON.parse(captured.options.body), {
    model_id: "model-1",
    idempotency_key: "idem-task-1",
    expected_capability_version: 7,
    expected_quote_revision: `sha256:${"a".repeat(64)}`,
    request_payload: {
      prompt: "test prompt",
      duration_seconds: 5,
      aspect_ratio: "16:9",
      resolution: "1080p",
      face_enabled: true,
    },
  });
  assert.deepEqual(result, { id: "task-1", status: "queued" });
});

test("cancels through the company-scoped generation task boundary", async () => {
  let captured;
  const client = createPlatformClient({
    baseUrl: "https://platform.example/",
    companyId: "company/a",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      captured = { url, options };
      return new Response(JSON.stringify({ id: "task/a", status: "cancelled" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  const result = await client.cancelTask("task/a");
  assert.equal(
    captured.url,
    "https://platform.example/api/v1/companies/company%2Fa/tasks/task%2Fa/cancel",
  );
  assert.equal(captured.options.method, "POST");
  assert.equal(captured.options.credentials, "omit");
  assert.equal(captured.options.redirect, "error");
  assert.deepEqual(result, { id: "task/a", status: "cancelled" });
});

test("requests a short-lived artifact URL through the company task boundary", async () => {
  let captured;
  const client = createPlatformClient({
    baseUrl: "https://platform.example/",
    companyId: "company/a",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      captured = { url, options };
      return new Response(
        JSON.stringify({
          url: "https://obs.example/signed-output",
          expires_seconds: 300,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    },
  });

  const result = await client.getArtifactDownload("task/a", "asset/a");

  assert.equal(
    captured.url,
    "https://platform.example/api/v1/companies/company%2Fa/tasks/task%2Fa/artifacts/asset%2Fa/download",
  );
  assert.equal(captured.options.method, "GET");
  assert.equal(captured.options.body, undefined);
  assert.equal(captured.options.headers["X-Company-ID"], "company/a");
  assert.equal(captured.options.headers.Authorization, "Bearer token-1");
  assert.equal(captured.options.headers["X-User-ID"], undefined);
  assert.deepEqual(result, {
    url: "https://obs.example/signed-output",
    expires_seconds: 300,
  });
});

test("requests a non-auditing artifact preview through the company task boundary", async () => {
  let captured;
  const client = createPlatformClient({
    baseUrl: "https://platform.example/",
    companyId: "company/a",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      captured = { url, options };
      return new Response(
        JSON.stringify({
          url: "https://obs.example/signed-output",
          expires_seconds: 300,
          media_type: "video",
          content_type: "video/mp4",
          preview_status: "issued",
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    },
  });

  const result = await client.getArtifactPreview("task/a", "asset/a", {
    scope: "company",
  });

  assert.equal(
    captured.url,
    "https://platform.example/api/v1/companies/company%2Fa/tasks/task%2Fa/artifacts/asset%2Fa/preview?scope=company",
  );
  assert.equal(captured.options.method, "GET");
  assert.equal(captured.options.body, undefined);
  assert.deepEqual(result, {
    url: "https://obs.example/signed-output",
    expires_seconds: 300,
    media_type: "video",
    content_type: "video/mp4",
    preview_status: "issued",
  });
});

test("promotes a canonical artifact with one stable idempotency key after an uncertain response", async () => {
  const attempts = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example/",
    companyId: "company/a",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      attempts.push({ url, options });
      if (attempts.length === 1) throw new TypeError("connection reset");
      return new Response(
        JSON.stringify({
          id: "input-asset-1",
          media_type: "video",
          status: "active",
          source_task_artifact_id: "canonical-artifact-1",
        }),
        { status: 201, headers: { "content-type": "application/json" } },
      );
    },
  });

  const result = await client.promoteArtifactToInputAsset(
    "task/a",
    "relay-asset/a",
    { idempotencyKey: "stable-promotion-key", scope: "company" },
  );

  assert.equal(result.id, "input-asset-1");
  assert.equal(attempts.length, 2);
  for (const { url, options } of attempts) {
    assert.equal(
      url,
      "https://platform.example/api/v1/companies/company%2Fa/tasks/task%2Fa/artifacts/relay-asset%2Fa/input-asset?scope=company",
    );
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Idempotency-Key"], "stable-promotion-key");
    assert.deepEqual(JSON.parse(options.body), {
      idempotency_key: "stable-promotion-key",
    });
  }
});

test("loads paged task history and archived artworks with explicit visibility scope", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example/",
    companyId: "company/a",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response(JSON.stringify({ page: 2, page_size: 24, total: 40, items: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  await client.listTaskHistory({
    page: 2,
    page_size: 24,
    scope: "company",
    employee_user_id: "user/a",
    model_id: "model/a",
    status: "succeeded",
    media_type: "image",
    query: "雨夜 产品",
  });
  await client.listArtworks({
    page: 2,
    page_size: 24,
    scope: "mine",
    media_type: "video",
    downloaded: false,
  });
  await client.getTask("task/a", { scope: "company" });
  await client.getArtifactDownload("task/a", "asset/a", { scope: "company" });

  assert.deepEqual(
    captured.map(({ url }) => url),
    [
      "https://platform.example/api/v1/companies/company%2Fa/task-history?page=2&page_size=24&scope=company&employee_user_id=user%2Fa&model_id=model%2Fa&status=succeeded&media_type=image&query=%E9%9B%A8%E5%A4%9C+%E4%BA%A7%E5%93%81",
      "https://platform.example/api/v1/companies/company%2Fa/artworks?page=2&page_size=24&scope=mine&media_type=video&downloaded=false",
      "https://platform.example/api/v1/companies/company%2Fa/tasks/task%2Fa?scope=company",
      "https://platform.example/api/v1/companies/company%2Fa/tasks/task%2Fa/artifacts/asset%2Fa/download?scope=company",
    ],
  );
  for (const { options } of captured) {
    assert.equal(options.headers.Authorization, "Bearer token-1");
    assert.equal(options.headers["X-Company-ID"], "company/a");
  }
});

test("normalizes the platform's direct structured API errors", async () => {
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-1",
    fetcher: async () =>
      new Response(
        JSON.stringify({
          detail: "公司可用余额不足",
          code: "insufficient_balance",
        }),
        {
          status: 409,
          headers: {
            "content-type": "application/json",
            "x-request-id": "req-server",
          },
        },
      ),
  });

  await assert.rejects(
    () =>
      client.createTask(
        { modelId: "model-1", requestPayload: {} },
        { idempotencyKey: "idem-task-2" },
      ),
    (error) =>
      error instanceof PlatformApiError &&
      error.status === 409 &&
      error.code === "insufficient_balance" &&
      error.message === "公司可用余额不足" &&
      error.requestId === "req-server",
  );
});

test("reads public runtime hints without trusting browser-stored bearer identity", () => {
  const values = new Map([
    ["ai-video.platform-api-url", "https://attacker.example"],
    ["ai-video.access-token", "token-session"],
  ]);
  const config = readRuntimePlatformConfig({
    __AI_VIDEO_RUNTIME_CONFIG__: {
      platformApiUrl: "https://platform.example/",
      companyId: "company-hint",
      accessToken: "must-not-be-public",
    },
    sessionStorage: { getItem: (key) => values.get(key) ?? null },
  });
  assert.deepEqual(config, {
    baseUrl: "https://platform.example/",
    companyId: "company-hint",
    accessToken: "",
  });
});

test("legacy bearer identity requires an explicit non-production runtime opt-in", () => {
  const config = readRuntimePlatformConfig({
    __AI_VIDEO_RUNTIME_CONFIG__: {
      platformApiUrl: "https://platform.example/",
      legacyBearerEnabled: true,
    },
    sessionStorage: {
      getItem: (key) => key === "ai-video.access-token" ? "legacy-test-token" : null,
    },
  });
  assert.equal(config.accessToken, "legacy-test-token");
});

test("uses the build-time public endpoint but never a build-time company identity", () => {
  const config = readRuntimePlatformConfig(
    {
      location: { origin: "https://page.example" },
      sessionStorage: { getItem: (key) => key === "ai-video.access-token" ? "token-session" : null },
    },
    {
      platformApiUrl: "https://platform.example",
      companyId: "company-build-must-be-ignored",
    },
  );

  assert.deepEqual(config, {
    baseUrl: "https://platform.example",
    companyId: "",
    accessToken: "",
  });
});

test("controlled runtime injection overrides public build hints without accepting a public token", () => {
  const config = readRuntimePlatformConfig(
    {
      __AI_VIDEO_RUNTIME_CONFIG__: {
        platformApiUrl: "https://runtime-platform.example",
        companyId: "company-runtime",
        accessToken: "public-token-must-be-ignored",
      },
      location: { origin: "https://page.example" },
      sessionStorage: { getItem: (key) => key === "ai-video.access-token" ? "session-token" : null },
    },
    {
      platformApiUrl: "https://build-platform.example",
    },
  );

  assert.deepEqual(config, {
    baseUrl: "https://runtime-platform.example",
    companyId: "company-runtime",
    accessToken: "",
  });
});

test("ignores a user-writable API origin and keeps cookie-BFF calls on the page origin", async () => {
  const values = new Map([
    ["ai-video.platform-api-url", "https://attacker.example"],
    ["ai-video.company-id", "company-session"],
    ["ai-video.access-token", "token-session"],
  ]);
  const runtimeConfig = readRuntimePlatformConfig({
    location: { origin: "http://127.0.0.1:5174" },
    sessionStorage: { getItem: (key) => values.get(key) ?? null },
  });
  assert.deepEqual(runtimeConfig, {
    baseUrl: "http://127.0.0.1:5174",
    companyId: "company-session",
    accessToken: "",
  });

  let captured;
  const client = createPlatformClient({
    ...runtimeConfig,
    fetcher: async (url, options) => {
      captured = { url, options };
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  await client.listTasks();

  assert.equal(
    captured.url,
    "http://127.0.0.1:5174/api/v1/companies/company-session/tasks",
  );
  assert.equal(captured.options.headers.Authorization, undefined);
  assert.equal(captured.options.credentials, "include");
  assert.equal(captured.options.redirect, "error");
});

test("retries uncertain task submission with one stable idempotency key", async () => {
  const attempts = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-1",
    fetcher: async (_url, options) => {
      attempts.push(options);
      if (attempts.length === 1) throw new TypeError("socket reset");
      return new Response(JSON.stringify({ id: "task-1", status: "queued" }), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    },
  });
  await client.createTask(
    { modelId: "model-1", requestPayload: {} },
    { idempotencyKey: "stable-key" },
  );
  assert.equal(attempts.length, 2);
  for (const attempt of attempts) {
    assert.equal(attempt.headers["Idempotency-Key"], "stable-key");
    assert.equal(JSON.parse(attempt.body).idempotency_key, "stable-key");
  }
});

test("retries a 5xx task submission with the same idempotency key", async () => {
  const attempts = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-1",
    fetcher: async (_url, options) => {
      attempts.push(options);
      if (attempts.length === 1) {
        return new Response(
          JSON.stringify({ detail: "upstream response was lost" }),
          {
            status: 502,
            headers: { "content-type": "application/json" },
          },
        );
      }
      return new Response(JSON.stringify({ id: "task-502", status: "queued" }), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    },
  });

  const result = await client.createTask(
    { modelId: "model-1", requestPayload: { prompt: "stable payload" } },
    { idempotencyKey: "stable-502-key" },
  );

  assert.equal(result.id, "task-502");
  assert.equal(attempts.length, 2);
  for (const attempt of attempts) {
    assert.equal(attempt.headers["Idempotency-Key"], "stable-502-key");
    assert.equal(JSON.parse(attempt.body).idempotency_key, "stable-502-key");
  }
});

test("retries a truncated JSON success response with the same idempotency key", async () => {
  const attempts = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-1",
    fetcher: async (_url, options) => {
      attempts.push(options);
      if (attempts.length === 1) {
        return new Response('{"id":"task-truncated"', {
          status: 201,
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(
        JSON.stringify({ id: "task-truncated", status: "queued" }),
        {
          status: 201,
          headers: { "content-type": "application/json" },
        },
      );
    },
  });

  const result = await client.createTask(
    { modelId: "model-1", requestPayload: { prompt: "stable payload" } },
    { idempotencyKey: "stable-json-key" },
  );

  assert.equal(result.id, "task-truncated");
  assert.equal(attempts.length, 2);
  for (const attempt of attempts) {
    assert.equal(attempt.headers["Idempotency-Key"], "stable-json-key");
    assert.equal(JSON.parse(attempt.body).idempotency_key, "stable-json-key");
  }
});

test("rejects unsafe API identities and download URLs", () => {
  for (const baseUrl of [
    "http://platform.example",
    "https://user:secret@platform.example",
    "https://platform.example/api",
    "https://platform.example?redirect=evil",
  ]) {
    assert.throws(
      () =>
        createPlatformClient({
          baseUrl,
          companyId: "company-1",
          accessToken: "token-1",
        }),
      PlatformApiError,
    );
  }
  assert.throws(
    () =>
      createPlatformClient({
        baseUrl: "https://platform.example",
        companyId: "company-1",
        accessToken: "token with spaces",
      }),
    (error) => error.code === "INVALID_ACCESS_TOKEN",
  );
  assert.equal(parseArtifactDownloadUrl("https://obs.example/out").protocol, "https:");
  assert.throws(
    () => parseArtifactDownloadUrl("https://user:secret@obs.example/out"),
    PlatformApiError,
  );
  assert.throws(
    () => parseArtifactDownloadUrl("http://obs.example/out"),
    PlatformApiError,
  );
});

test("uploads and manages private input assets without forcing a multipart content type", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company/a",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      if (options.method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      return new Response(
        JSON.stringify(
          url.endsWith("/preview")
            ? { url: "https://assets.example/preview", expires_seconds: 300 }
            : url.includes("/assets?")
              ? []
              : { id: "asset-1", media_type: "image" },
        ),
        { status: options.method === "POST" ? 201 : 200, headers: { "content-type": "application/json" } },
      );
    },
  });

  const file = new Blob(["image-bytes"], { type: "image/png" });
  await client.uploadAsset(file, "image");
  await client.listAssets({ status: "active", media_type: "image" });
  await client.getAssetPreview("asset/a");
  await client.deleteAsset("asset/a");

  assert.equal(
    captured[0].url,
    "https://platform.example/api/v1/companies/company%2Fa/assets",
  );
  assert.equal(captured[0].options.method, "POST");
  assert.ok(captured[0].options.body instanceof FormData);
  assert.equal(captured[0].options.headers["Content-Type"], undefined);
  assert.ok(captured[0].options.headers["Idempotency-Key"]);
  assert.equal(captured[0].options.body.get("media_type"), "image");
  assert.equal(captured[0].options.body.get("file").type, "image/png");
  assert.equal(
    captured[1].url,
    "https://platform.example/api/v1/companies/company%2Fa/assets?status=active&media_type=image",
  );
  assert.equal(
    captured[2].url,
    "https://platform.example/api/v1/companies/company%2Fa/assets/asset%2Fa/preview",
  );
  assert.equal(captured[3].options.method, "DELETE");
});

test("retries an uncertain asset upload with the same idempotency key and form body", async () => {
  const attempts = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-1",
    fetcher: async (_url, options) => {
      attempts.push(options);
      if (attempts.length === 1) throw new TypeError("connection reset");
      return new Response(
        JSON.stringify({ id: "asset-1", media_type: "image" }),
        { status: 201, headers: { "content-type": "application/json" } },
      );
    },
  });

  const result = await client.uploadAsset(
    new Blob(["same-image"], { type: "image/png" }),
    "image",
    { idempotencyKey: "stable-asset-key" },
  );

  assert.equal(result.id, "asset-1");
  assert.equal(attempts.length, 2);
  assert.equal(attempts[0].headers["Idempotency-Key"], "stable-asset-key");
  assert.equal(attempts[1].headers["Idempotency-Key"], "stable-asset-key");
  assert.equal(attempts[0].body, attempts[1].body);
});

test("uses the lifecycle and reporting contracts for company management", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company/a",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      if (url.endsWith("export.csv?status=succeeded")) {
        return new Response("task_id,status\ntask-1,succeeded", {
          status: 200,
          headers: { "content-type": "text/csv; charset=utf-8" },
        });
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  await client.getCompanyMe();
  await client.setMemberStatus("membership/a", "disabled");
  await client.replaceMemberRoles("membership/a", {
    roleIds: ["role-1", "role-2"],
    expectedRoleIds: ["role-1"],
  });
  await client.getTaskReport({ page_size: 25, status: "succeeded", ignored: "" });
  const csv = await client.exportTaskReport({ status: "succeeded" });

  assert.equal(csv, "task_id,status\ntask-1,succeeded");
  assert.deepEqual(
    captured.map(({ url }) => url),
    [
      "https://platform.example/api/v1/companies/company%2Fa/me",
      "https://platform.example/api/v1/companies/company%2Fa/members/membership%2Fa/status",
      "https://platform.example/api/v1/companies/company%2Fa/members/membership%2Fa/roles",
      "https://platform.example/api/v1/companies/company%2Fa/reports/tasks?page_size=25&status=succeeded",
      "https://platform.example/api/v1/companies/company%2Fa/reports/tasks/export.csv?status=succeeded",
    ],
  );
  assert.deepEqual(JSON.parse(captured[1].options.body), { status: "disabled" });
  assert.deepEqual(JSON.parse(captured[2].options.body), {
    role_ids: ["role-1", "role-2"],
    expected_role_ids: ["role-1"],
  });
  assert.equal(captured[4].options.headers.Accept, "text/csv, text/plain");
});

test("uses recharge history and the complete company consumption filters", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company/a",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      if (url.includes("export.csv")) {
        return new Response("ledger_entry_id,amount_cents\nledger-1,4200", {
          status: 200,
          headers: { "content-type": "text/csv; charset=utf-8" },
        });
      }
      return new Response(JSON.stringify({ total: 0, items: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });
  const filters = {
    employee_user_id: "user/a",
    model_id: "model/a",
    status: "succeeded",
    start_time: "2026-08-01T00:00:00.000Z",
    end_time: "2026-08-02T00:00:00.000Z",
  };

  await client.listRecharges({ page: 2, page_size: 100 });
  await client.getConsumptionReport({ page_size: 50, ...filters });
  const csv = await client.exportConsumptionReport(filters);

  assert.equal(csv, "ledger_entry_id,amount_cents\nledger-1,4200");
  assert.deepEqual(
    captured.map(({ url }) => url),
    [
      "https://platform.example/api/v1/companies/company%2Fa/wallet/recharges?page=2&page_size=100",
      "https://platform.example/api/v1/companies/company%2Fa/reports/consumption?page_size=50&employee_user_id=user%2Fa&model_id=model%2Fa&status=succeeded&start_time=2026-08-01T00%3A00%3A00.000Z&end_time=2026-08-02T00%3A00%3A00.000Z",
      "https://platform.example/api/v1/companies/company%2Fa/reports/consumption/export.csv?employee_user_id=user%2Fa&model_id=model%2Fa&status=succeeded&start_time=2026-08-01T00%3A00%3A00.000Z&end_time=2026-08-02T00%3A00%3A00.000Z",
    ],
  );
  assert.equal(captured[2].options.headers.Accept, "text/csv, text/plain");
});

test("requires a stable recharge key and uses the platform-wide consumption report", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-admin",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      if (url.includes("export.csv")) {
        return new Response("company_id,amount_cents\ncompany-1,4200", {
          status: 200,
          headers: { "content-type": "text/csv; charset=utf-8" },
        });
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  assert.throws(
    () => client.rechargeAdminCompany("company-1", { amountCents: 1000 }),
    (error) => error instanceof PlatformApiError && error.code === "IDEMPOTENCY_KEY_REQUIRED",
  );
  await client.rechargeAdminCompany("company/a", {
    amountCents: 1000,
    note: "合同充值",
    idempotencyKey: "recharge-stable-1",
  });
  await client.getAdminConsumptionReport({
    company_id: "company/a",
    employee_query: "operator@example.cn",
    model_id: "model/a",
    start_time: "2026-08-01T00:00:00.000Z",
    end_time: "2026-08-02T00:00:00.000Z",
  });
  const csv = await client.exportAdminConsumptionReport({ company_id: "company/a" });

  assert.deepEqual(JSON.parse(captured[0].options.body), {
    amount_cents: 1000,
    note: "合同充值",
    idempotency_key: "recharge-stable-1",
  });
  assert.equal(captured[0].options.headers["Idempotency-Key"], "recharge-stable-1");
  assert.equal(
    captured[1].url,
    "https://platform.example/api/v1/platform-admin/reports/consumption?company_id=company%2Fa&employee_query=operator%40example.cn&model_id=model%2Fa&start_time=2026-08-01T00%3A00%3A00.000Z&end_time=2026-08-02T00%3A00%3A00.000Z",
  );
  assert.equal(
    captured[2].url,
    "https://platform.example/api/v1/platform-admin/reports/consumption/export.csv?company_id=company%2Fa",
  );
  assert.equal(csv, "company_id,amount_cents\ncompany-1,4200");
  assert.equal(captured[2].options.headers.Accept, "text/csv, text/plain");
});

test("creates company members with one atomic primary level", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-1",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response(JSON.stringify({ membership_id: "membership-1" }), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    },
  });

  await client.createMember({
    email: "lead@example.cn",
    displayName: "新组长",
    primaryRole: "team_lead",
  });
  await client.createMember({
    email: "operator@example.cn",
    displayName: "新运营",
  });

  assert.equal(
    captured[0].url,
    "https://platform.example/api/v1/companies/company-1/members",
  );
  assert.deepEqual(JSON.parse(captured[0].options.body), {
    email: "lead@example.cn",
    display_name: "新组长",
    primary_role: "team_lead",
  });
  assert.deepEqual(JSON.parse(captured[1].options.body), {
    email: "operator@example.cn",
    display_name: "新运营",
    primary_role: "operator",
  });
});

test("uses invitation, owner-transfer and global account lifecycle contracts", async () => {
  setPlatformCsrfToken("csrf-lifecycle");
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company/1",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return Response.json([]);
    },
  });

  await client.listInvitations({ page: 2, page_size: 40 });
  await client.createInvitation({
    email: "invitee@example.com",
    displayName: "Invitee",
    primaryRole: "team_lead",
    idempotencyKey: "invite-idempotency-1",
    expiresInHours: 72,
  });
  await client.reissueInvitation("invite/1");
  await client.revokeInvitation("invite/1");
  await client.transferCompanyOwner({
    targetMembershipId: "membership-new",
    expectedCurrentOwnerMembershipId: "membership-owner",
    expectedCurrentOwnerUserId: "user-owner",
    formerOwnerPrimaryRole: "operator",
  });
  await client.listPlatformUsers();
  await client.setPlatformUserStatus("user/1", {
    expectedStatus: "active",
    expectedAuthVersion: 4,
    targetStatus: "suspended",
  });
  await client.reissueAdminCompanyOwnerInvitation("company/1", {
    expectedOwnerMembershipId: "membership-owner",
    expectedOwnerUserId: "user-owner",
    replacementEmail: "new-owner@example.com",
    replacementDisplayName: "New Owner",
  });

  assert.deepEqual(captured.map(({ url }) => url), [
    "https://platform.example/api/v1/companies/company%2F1/invitations?page=2&page_size=40",
    "https://platform.example/api/v1/companies/company%2F1/invitations",
    "https://platform.example/api/v1/companies/company%2F1/invitations/invite%2F1/reissue",
    "https://platform.example/api/v1/companies/company%2F1/invitations/invite%2F1/revoke",
    "https://platform.example/api/v1/companies/company%2F1/owner-transfer",
    "https://platform.example/api/v1/platform-admin/users",
    "https://platform.example/api/v1/platform-admin/users/user%2F1/status",
    "https://platform.example/api/v1/platform-admin/companies/company%2F1/owner-invitation/reissue",
  ]);
  assert.deepEqual(JSON.parse(captured[1].options.body), {
    email: "invitee@example.com",
    display_name: "Invitee",
    primary_role: "team_lead",
    idempotency_key: "invite-idempotency-1",
    expires_in_hours: 72,
  });
  assert.equal(captured[1].options.headers["Idempotency-Key"], "invite-idempotency-1");
  assert.deepEqual(JSON.parse(captured[4].options.body), {
    target_membership_id: "membership-new",
    expected_current_owner_membership_id: "membership-owner",
    expected_current_owner_user_id: "user-owner",
    former_owner_primary_role: "operator",
  });
  assert.deepEqual(JSON.parse(captured[6].options.body), {
    expected_status: "active",
    expected_auth_version: 4,
    target_status: "suspended",
  });
  assert.deepEqual(JSON.parse(captured[7].options.body), {
    expected_owner_membership_id: "membership-owner",
    expected_owner_user_id: "user-owner",
    replacement_email: "new-owner@example.com",
    replacement_display_name: "New Owner",
  });
  for (const index of [1, 2, 3, 4, 6, 7]) {
    assert.equal(captured[index].options.headers["X-CSRF-Token"], "csrf-lifecycle");
    assert.equal(captured[index].options.credentials, "include");
    assert.equal(captured[index].options.headers.Authorization, undefined);
  }
  assert.equal(captured[5].options.headers["X-Company-ID"], undefined);
  assert.equal(captured[6].options.headers["X-Company-ID"], undefined);
  assert.equal(captured[7].options.headers["X-Company-ID"], undefined);
  clearPlatformCsrfToken();
});

test("uses the complete member permission catalog and atomic access contract", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company/a",
    accessToken: "token-owner",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  await client.listPermissionCatalog();
  await client.getMemberPermissions("membership/a");
  await client.replaceMemberAccess("membership/a", {
    roleIds: ["role-operator"],
    permissionOverrides: {
      "billing.read": "allow",
      "tasks.read": "deny",
    },
    expectedRoleIds: ["role-operator"],
    expectedPermissionOverrides: { "tasks.read": "allow" },
  });
  await client.replaceMemberPermissionOverrides("membership/a", {
    permissionOverrides: { "assets.manage": "deny" },
    expectedPermissionOverrides: { "tasks.read": "allow" },
  });

  assert.deepEqual(
    captured.map(({ url }) => url),
    [
      "https://platform.example/api/v1/companies/company%2Fa/permissions",
      "https://platform.example/api/v1/companies/company%2Fa/members/membership%2Fa/permissions",
      "https://platform.example/api/v1/companies/company%2Fa/members/membership%2Fa/access",
      "https://platform.example/api/v1/companies/company%2Fa/members/membership%2Fa/permissions",
    ],
  );
  assert.deepEqual(JSON.parse(captured[2].options.body), {
    role_ids: ["role-operator"],
    permission_overrides: {
      "billing.read": "allow",
      "tasks.read": "deny",
    },
    expected_role_ids: ["role-operator"],
    expected_permission_overrides: { "tasks.read": "allow" },
  });
  assert.deepEqual(JSON.parse(captured[3].options.body), {
    overrides: { "assets.manage": "deny" },
    expected_overrides: { "tasks.read": "allow" },
  });
});

test("uses platform-admin model lifecycle without inventing a browser identity", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-admin",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response(JSON.stringify({ id: "model-1" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  await client.getPlatformAdminMe();
  await client.listAdminRelayModels();
  await client.approveAdminRelayCapability("model/a", {
    expectedCapabilityVersion: 7,
  });
  const canonicalCapability = {
    schema_version: 1,
    modes: {
      image_to_video: {
        input_media_types: ["image", "audio"],
        supports_face: true,
        required_resource_keys: [],
        limits: {
          max_prompt_length: 2000,
          max_images: 4,
          max_videos: 0,
          max_audio: 3,
          duration_seconds: [5, 10],
          aspect_ratios: ["16:9", "9:16"],
          resolutions: ["720p", "1080p"],
          output_counts: [1, 2],
        },
      },
      text_to_video: {
        input_media_types: ["image", "video", "audio"],
        supports_face: false,
        required_resource_keys: ["feature.video-generation"],
        limits: {
          max_prompt_length: 2000,
          max_images: 9,
          max_videos: 3,
          max_audio: 3,
          duration_seconds: [5, 10, 15],
          aspect_ratios: ["16:9", "9:16"],
          resolutions: ["720p", "1080p"],
          output_counts: [1, 2, 3],
        },
      },
    },
  };
  const capabilities = [{ key: "generation", config: canonicalCapability }];
  await client.createAdminModel({
    slug: "video-v1",
    displayName: "Video V1",
    providerKey: "provider-a",
    billingMode: "per_item",
    capabilities,
  });
  await client.updateAdminModel("model/a", {
    displayName: "Video V1 revised",
    providerKey: "provider-a",
    billingMode: "per_item",
    expectedCapabilityVersion: 7,
    capabilities,
  });
  await client.publishAdminModel("model/a");
  await client.disableAdminModel("model/a");

  assert.deepEqual(
    captured.map(({ url }) => url),
    [
      "https://platform.example/api/v1/platform-admin/me",
      "https://platform.example/api/v1/platform-admin/relay-models",
      "https://platform.example/api/v1/platform-admin/models/model%2Fa/relay-capability",
      "https://platform.example/api/v1/platform-admin/models",
      "https://platform.example/api/v1/platform-admin/models/model%2Fa",
      "https://platform.example/api/v1/platform-admin/models/model%2Fa/publish",
      "https://platform.example/api/v1/platform-admin/models/model%2Fa/disable",
    ],
  );
  assert.deepEqual(JSON.parse(captured[2].options.body), {
    expected_capability_version: 7,
  });
  assert.deepEqual(JSON.parse(captured[3].options.body), {
    slug: "video-v1",
    display_name: "Video V1",
    provider_key: "provider-a",
    billing_mode: "per_item",
    capabilities,
  });
  assert.deepEqual(JSON.parse(captured[4].options.body), {
    display_name: "Video V1 revised",
    provider_key: "provider-a",
    billing_mode: "per_item",
    expected_capability_version: 7,
    capabilities,
  });
  for (const { options } of captured) {
    assert.equal(options.headers.Authorization, "Bearer token-admin");
    assert.equal(options.headers["X-User-ID"], undefined);
  }
});

test("accepts successful empty responses for destructive lifecycle operations", async () => {
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-1",
    fetcher: async () => new Response(null, { status: 204 }),
  });

  assert.equal(await client.deleteRole("role-1"), null);
  assert.equal(await client.deleteAdminModel("model-draft-1"), null);
});

test("uses the company publishing contract without exposing provider credentials", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company/a",
    accessToken: "token-publisher",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response(JSON.stringify({ id: "result-1", items: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  await client.listPublisherConnections();
  await client.listPublisherOAuthProviders();
  await client.startPublisherOAuth({ provider: "douyin" });
  await client.deletePublisherConnection("connection/a");
  await client.listPublicationJobs({ status: "pending_approval", page: 2, page_size: 50 });
  await client.createPublicationJob({
    artifactId: "artifact/a",
    connectionId: "connection/a",
    idempotencyKey: "publication-stable-1",
    title: "防水音箱",
    caption: "户外防水演示",
    scheduledAt: "2026-08-08T10:00:00+08:00",
    timezone: "Asia/Shanghai",
  });
  await client.getPublicationJob("job/a");
  await client.approvePublicationJob("job/a");
  await client.cancelPublicationJob("job/a");
  await client.retryPublicationJob("job/a");
  await client.reconcilePublicationJob("job/a", {
    outcome: "published",
    externalPostId: "douyin-post-1",
    externalPostUrl: "https://www.douyin.com/video/1",
  });
  await client.reconcilePublicationJob("job/b", {
    outcome: "failed",
    errorCode: "CHANNEL_CONFIRMED_MISSING",
    errorMessage: "渠道后台未找到作品",
  });

  assert.deepEqual(captured.map(({ url }) => url), [
    "https://platform.example/api/v1/companies/company%2Fa/publishing/connections",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/connections/oauth/providers",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/connections/oauth/start",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/connections/connection%2Fa",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/jobs?status=pending_approval&page=2&page_size=50",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/jobs",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/jobs/job%2Fa",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/jobs/job%2Fa/approve",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/jobs/job%2Fa/cancel",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/jobs/job%2Fa/retry",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/jobs/job%2Fa/reconcile",
    "https://platform.example/api/v1/companies/company%2Fa/publishing/jobs/job%2Fb/reconcile",
  ]);
  assert.deepEqual(JSON.parse(captured[2].options.body), { provider: "douyin" });
  assert.deepEqual(JSON.parse(captured[5].options.body), {
    artifact_id: "artifact/a",
    connection_id: "connection/a",
    idempotency_key: "publication-stable-1",
    title: "防水音箱",
    caption: "户外防水演示",
    scheduled_at: "2026-08-08T10:00:00+08:00",
    timezone: "Asia/Shanghai",
  });
  assert.equal(captured[5].options.headers["Idempotency-Key"], "publication-stable-1");
  assert.deepEqual(JSON.parse(captured[10].options.body), {
    outcome: "published",
    external_post_id: "douyin-post-1",
    external_post_url: "https://www.douyin.com/video/1",
  });
  assert.deepEqual(JSON.parse(captured[11].options.body), {
    outcome: "failed",
    error_code: "CHANNEL_CONFIRMED_MISSING",
    error_message: "渠道后台未找到作品",
  });
  assert.deepEqual(captured.slice(7).map(({ options }) => options.method), ["POST", "POST", "POST", "POST", "POST"]);
  assert.deepEqual(captured.slice(7, 10).map(({ options }) => options.body), [undefined, undefined, undefined]);
  assert.equal(captured.some(({ options }) => JSON.stringify(options).includes("access_token")), false);
});

test("non-development clients fail closed before creating a mock publisher connection", () => {
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-publisher",
    fetcher: async () => {
      throw new Error("fetch should not be called");
    },
  });

  assert.throws(
    () => client.createPublisherConnection({ provider: "mock", displayName: "测试账号" }),
    (error) => error instanceof PlatformApiError
      && error.code === "MOCK_PUBLISHER_CONNECTIONS_DEVELOPMENT_ONLY",
  );
});

test("browser publishing client refuses to invent a non-mock OAuth connection", () => {
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-publisher",
    fetcher: async () => {
      throw new Error("fetch should not be called");
    },
  });

  assert.throws(
    () => client.createPublisherConnection({ provider: "douyin", displayName: "真实账号" }),
    (error) => error instanceof PlatformApiError && error.code === "UNSUPPORTED_PUBLISHER_CONNECTION_PROVIDER",
  );
});

test("manual reconciliation rejects an invalid outcome and requires a published post id", () => {
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company-1",
    accessToken: "token-publisher",
    fetcher: async () => {
      throw new Error("fetch should not be called");
    },
  });

  assert.throws(
    () => client.reconcilePublicationJob("job-1", { outcome: "unknown" }),
    (error) => error instanceof PlatformApiError && error.code === "INVALID_RECONCILIATION_OUTCOME",
  );
  assert.throws(
    () => client.reconcilePublicationJob("job-1", { outcome: "published" }),
    (error) => error instanceof PlatformApiError && error.code === "EXTERNAL_POST_ID_REQUIRED",
  );
});
