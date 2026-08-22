import assert from "node:assert/strict";
import test from "node:test";

import { createPlatformClient } from "../src/api/platformClient.js";


function jsonResponse(value = {}) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}


function capturingClient() {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company/current",
    accessToken: "platform-admin-token",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return jsonResponse();
    },
  });
  return { captured, client };
}


test("platform administrator analytics and entitlement reads preserve every query contract", async () => {
  const { captured, client } = capturingClient();

  await client.getAdminOperatingSeries({
    start_time: "2026-07-01T00:00:00.000Z",
    end_time: "2026-08-01T00:00:00.000Z",
    granularity: "week",
    ignored_empty: "",
  });
  await client.getAdminTaskOperations({
    start_time: "2026-08-01T00:00:00.000Z",
    end_time: "2026-08-08T00:00:00.000Z",
  });
  await client.getAdminModelProfitability({
    start_time: "2026-07-08T00:00:00.000Z",
    end_time: "2026-08-08T00:00:00.000Z",
    include_inactive: false,
  });
  await client.getAdminCompanyHealth({
    page: 3,
    page_size: 40,
    low_balance_threshold_cents: 12000,
    inactivity_days: 21,
    stale_reservation_hours: 12,
    failure_rate_threshold: 0.35,
    minimum_terminal_tasks: 8,
    abnormal_spend_ratio: 4.5,
  });
  await client.getAdminChannelHealth({
    start_time: "2026-08-01T00:00:00.000Z",
    end_time: "2026-08-08T00:00:00.000Z",
  });
  await client.getAdminDataReadiness();
  await client.getAdminExceptionCenter({ limit_per_category: 75 });
  await client.getAdminEntitlementMatrix({
    company_page: 2,
    company_page_size: 25,
    catalog_page: 3,
    catalog_page_size: 50,
    company_query: "华东/视频",
    catalog_query: "Kling Pro",
    catalog_kind: "model",
    include_retired: false,
  });
  await client.getAdminEntitlementCoverage({ include_retired: false });

  assert.deepEqual(captured.map(({ url }) => url), [
    "https://platform.example/api/v1/platform-admin/analytics/operating-series?start_time=2026-07-01T00%3A00%3A00.000Z&end_time=2026-08-01T00%3A00%3A00.000Z&granularity=week",
    "https://platform.example/api/v1/platform-admin/analytics/task-operations?start_time=2026-08-01T00%3A00%3A00.000Z&end_time=2026-08-08T00%3A00%3A00.000Z",
    "https://platform.example/api/v1/platform-admin/analytics/model-profitability?start_time=2026-07-08T00%3A00%3A00.000Z&end_time=2026-08-08T00%3A00%3A00.000Z&include_inactive=false",
    "https://platform.example/api/v1/platform-admin/analytics/company-health?page=3&page_size=40&low_balance_threshold_cents=12000&inactivity_days=21&stale_reservation_hours=12&failure_rate_threshold=0.35&minimum_terminal_tasks=8&abnormal_spend_ratio=4.5",
    "https://platform.example/api/v1/platform-admin/analytics/channel-health?start_time=2026-08-01T00%3A00%3A00.000Z&end_time=2026-08-08T00%3A00%3A00.000Z",
    "https://platform.example/api/v1/platform-admin/analytics/data-readiness",
    "https://platform.example/api/v1/platform-admin/analytics/exceptions?limit_per_category=75",
    "https://platform.example/api/v1/platform-admin/entitlements/matrix?company_page=2&company_page_size=25&catalog_page=3&catalog_page_size=50&company_query=%E5%8D%8E%E4%B8%9C%2F%E8%A7%86%E9%A2%91&catalog_query=Kling+Pro&catalog_kind=model&include_retired=false",
    "https://platform.example/api/v1/platform-admin/entitlements/coverage?include_retired=false",
  ]);
  for (const { options } of captured) {
    assert.equal(options.method, "GET");
    assert.equal(options.body, undefined);
    assert.equal(options.headers.Authorization, "Bearer platform-admin-token");
    assert.equal(options.headers["X-Company-ID"], "company/current");
  }
});


test("platform administrator access client matches permission, role, user, and status routes", async () => {
  const { captured, client } = capturingClient();
  const roleCreate = {
    key: "finance-reviewer",
    display_name: "财务复核",
    description: "只读财务与审计数据",
    permission_codes: ["platform.analytics.read", "platform.audit.read"],
    change_reason: "建立最小权限角色",
  };
  const roleReplace = {
    display_name: "财务复核（含导出）",
    description: "财务与审计复核",
    active: true,
    permission_codes: [
      "platform.analytics.read",
      "platform.audit.read",
      "platform.audit.export",
    ],
    expected_lock_version: 3,
    change_reason: "增加审计导出",
  };
  const userAccess = {
    role_ids: ["role/finance"],
    permission_overrides: {
      "platform.company.read": "allow",
      "platform.company.manage": "deny",
    },
    expected_lock_version: 6,
    change_reason: "按岗位微调公司权限",
  };
  const userStatus = {
    enabled: false,
    expected_is_platform_admin: true,
    change_reason: "人员离岗停用",
  };

  await client.listPlatformAdminPermissionCatalog();
  await client.listPlatformAdminRoles();
  await client.createPlatformAdminRole(roleCreate);
  await client.replacePlatformAdminRole("role/finance", roleReplace);
  await client.listPlatformAdministrators();
  await client.getPlatformAdministratorAccess("user/admin@example.com");
  await client.replacePlatformAdministratorAccess(
    "user/admin@example.com",
    userAccess,
  );
  await client.setPlatformAdministratorStatus(
    "user/admin@example.com",
    userStatus,
  );

  assert.deepEqual(captured.map(({ url }) => url), [
    "https://platform.example/api/v1/platform-admin/access/permissions",
    "https://platform.example/api/v1/platform-admin/access/roles",
    "https://platform.example/api/v1/platform-admin/access/roles",
    "https://platform.example/api/v1/platform-admin/access/roles/role%2Ffinance",
    "https://platform.example/api/v1/platform-admin/access/users",
    "https://platform.example/api/v1/platform-admin/access/users/user%2Fadmin%40example.com",
    "https://platform.example/api/v1/platform-admin/access/users/user%2Fadmin%40example.com",
    "https://platform.example/api/v1/platform-admin/access/users/user%2Fadmin%40example.com/status",
  ]);
  assert.deepEqual(
    captured.map(({ options }) => options.method),
    ["GET", "GET", "POST", "PUT", "GET", "GET", "PUT", "PUT"],
  );
  assert.deepEqual(JSON.parse(captured[2].options.body), roleCreate);
  assert.deepEqual(JSON.parse(captured[3].options.body), roleReplace);
  assert.deepEqual(JSON.parse(captured[6].options.body), userAccess);
  assert.deepEqual(JSON.parse(captured[7].options.body), userStatus);
});


test("entitlement batch, copy, and template commands keep preview pure and execute idempotent", async () => {
  const { captured, client } = capturingClient();
  const batchPreview = {
    changes: [
      {
        company_id: "company/a",
        item_kind: "model",
        item_id: "model/kling",
        enabled: true,
        price_per_item_cents: 75,
      },
    ],
  };
  const batchExecute = {
    ...batchPreview,
    expected_snapshot: "a".repeat(64),
    reason: "批量开通已确认",
    idempotency_key: "entitlement-batch-20260808-1",
  };
  const copyPreview = {
    source_company_id: "company/source",
    target_company_ids: ["company/a", "company/b"],
    mode: "replace",
    include_models: true,
    include_resources: false,
  };
  const copyExecute = {
    ...copyPreview,
    expected_snapshot: "b".repeat(64),
    reason: "复制签约公司的模型权益",
    idempotency_key: "entitlement-copy-20260808-1",
  };
  const templatePreview = {
    template_name: "标准视频套餐",
    template_version: 4,
    target_company_ids: ["company/a", "company/b"],
    mode: "merge",
    cells: [
      {
        item_kind: "resource",
        item_id: "feature/auto-publish",
        enabled: true,
        call_quota: 500,
        concurrency_limit: 4,
      },
    ],
  };
  const templateExecute = {
    ...templatePreview,
    expected_snapshot: "c".repeat(64),
    reason: "应用标准视频套餐",
    idempotency_key: "entitlement-template-20260808-1",
  };

  await client.previewAdminEntitlementBatch(batchPreview);
  await client.executeAdminEntitlementBatch(batchExecute);
  await client.previewAdminEntitlementCopy(copyPreview);
  await client.executeAdminEntitlementCopy(copyExecute);
  await client.previewAdminEntitlementTemplate(templatePreview);
  await client.executeAdminEntitlementTemplate(templateExecute);

  assert.deepEqual(captured.map(({ url }) => url), [
    "https://platform.example/api/v1/platform-admin/entitlements/batch/preview",
    "https://platform.example/api/v1/platform-admin/entitlements/batch/execute",
    "https://platform.example/api/v1/platform-admin/entitlements/copy/preview",
    "https://platform.example/api/v1/platform-admin/entitlements/copy/execute",
    "https://platform.example/api/v1/platform-admin/entitlements/templates/preview",
    "https://platform.example/api/v1/platform-admin/entitlements/templates/execute",
  ]);
  assert.deepEqual(
    captured.map(({ options }) => options.method),
    ["POST", "POST", "POST", "POST", "POST", "POST"],
  );
  assert.deepEqual(
    captured.map(({ options }) => JSON.parse(options.body)),
    [
      batchPreview,
      batchExecute,
      copyPreview,
      copyExecute,
      templatePreview,
      templateExecute,
    ],
  );
  assert.deepEqual(
    captured.map(({ options }) => options.headers["Idempotency-Key"]),
    [
      undefined,
      "entitlement-batch-20260808-1",
      undefined,
      "entitlement-copy-20260808-1",
      undefined,
      "entitlement-template-20260808-1",
    ],
  );
});


test("download registration reconciliation uses the encoded platform-admin mutation route", async () => {
  const { captured, client } = capturingClient();

  await client.reconcileAdminDownloadGatewayAttempt("attempt/OBS 回执");

  assert.equal(
    captured[0].url,
    "https://platform.example/api/v1/platform-admin/download-gateway-registration-attempts/attempt%2FOBS%20%E5%9B%9E%E6%89%A7/reconcile",
  );
  assert.equal(captured[0].options.method, "POST");
  assert.equal(captured[0].options.body, undefined);
  assert.equal(captured[0].options.headers["Idempotency-Key"], undefined);
});


test("Relay unknown-submission operations use fenced reads and a non-retrying resolve contract", async () => {
  const { captured, client } = capturingClient();
  const jobId = "91c5cd71-bde2-4cf9-b6ed-b264b0841f51";
  const body = {
    outcome: "created",
    upstream_task_id: "provider-task-8842",
    expected_route_id: 208,
    expected_submission_attempt: 1,
    expected_reconciliation_token: `sha256:${"a".repeat(64)}`,
    verification_reference: "provider-console-event-7781",
    reason: "值班负责人已核对 Provider 控制台",
  };

  await client.listAdminRelayUnknownSubmissions({ page: 2, page_size: 50 });
  await client.getAdminRelayUnknownSubmission(jobId);
  await client.getAdminRelayUnknownSubmissionResult(jobId);
  await client.getAdminRelayUnknownSubmissionResult(jobId, {
    operationId: "relay-reconcile-op-20260811",
  });
  await client.resolveAdminRelayUnknownSubmission(jobId, body);

  assert.deepEqual(captured.map(({ url }) => url), [
    "https://platform.example/api/v1/platform-admin/relay/submission-unknown?page=2&page_size=50",
    `https://platform.example/api/v1/platform-admin/relay/submission-unknown/${jobId}`,
    `https://platform.example/api/v1/platform-admin/relay/submission-unknown/${jobId}/result`,
    `https://platform.example/api/v1/platform-admin/relay/submission-unknown/${jobId}/result?operation_id=relay-reconcile-op-20260811`,
    `https://platform.example/api/v1/platform-admin/relay/submission-unknown/${jobId}/resolve`,
  ]);
  assert.deepEqual(
    captured.map(({ options }) => options.method),
    ["GET", "GET", "GET", "GET", "POST"],
  );
  assert.deepEqual(JSON.parse(captured[4].options.body), body);
  assert.equal(captured[4].options.headers["Idempotency-Key"], undefined);
});

test("Relay callback dead-letter operations stay behind Platform and never transport-retry redrive", async () => {
  const { captured, client } = capturingClient();
  const eventId = "b9b2537e-258c-4a98-af8a-6d23bdb135a4";
  const operationId = "callback-redrive-op-20260813";

  await client.listAdminRelayCallbackDeadLetters({ page: 1, page_size: 50 });
  await client.getAdminRelayCallbackDeadLetter(eventId);
  await client.getAdminRelayCallbackRedriveResult(eventId, { operationId });
  await client.redriveAdminRelayCallbackDeadLetter(eventId, {
    operation_id: operationId,
    actor: "oncall-a",
    reason: "Destination incident is resolved",
    approved: true,
  });

  assert.deepEqual(captured.map(({ url }) => url), [
    "https://platform.example/api/v1/platform-admin/relay/callback-dead-letters?page=1&page_size=50",
    `https://platform.example/api/v1/platform-admin/relay/callback-dead-letters/${eventId}`,
    `https://platform.example/api/v1/platform-admin/relay/callback-dead-letters/${eventId}/result?operation_id=${operationId}`,
    `https://platform.example/api/v1/platform-admin/relay/callback-dead-letters/${eventId}/redrive`,
  ]);
  assert.deepEqual(captured.map(({ options }) => options.method), ["GET", "GET", "GET", "POST"]);
  assert.deepEqual(JSON.parse(captured[3].options.body), {
    operation_id: operationId,
    actor: "oncall-a",
    reason: "Destination incident is resolved",
    approved: true,
  });
});

test("Relay channel control and native-console authorization use only Platform facade routes", async () => {
  const { captured, client } = capturingClient();
  const channelId = 17;
  const testOperationId = "relay-channel-test-20260814";
  const statusOperationId = "relay-channel-status-20260814";
  const revision = `sha256:${"d".repeat(64)}`;

  await client.listAdminRelayChannels({ page: 1, page_size: 50, status: "enabled" });
  await client.openAdminRelayNativeConsole();
  await client.getAdminRelayChannel(channelId);
  await client.testAdminRelayChannel(channelId, {
    operationId: testOperationId,
    reason: "Run approved channel connectivity test",
    approved: true,
  });
  await client.setAdminRelayChannelStatus(channelId, {
    operationId: statusOperationId,
    reason: "Disable new admission during credential review",
    approved: true,
    expectedRevision: revision,
    targetStatus: "manually_disabled",
  });
  await client.getAdminRelayChannelOperation(channelId, statusOperationId);

  assert.deepEqual(captured.map(({ url }) => url), [
    "https://platform.example/api/v1/platform-admin/relay/channels?page=1&page_size=50&status=enabled",
    "https://platform.example/api/v1/platform-admin/relay/native-console/open",
    `https://platform.example/api/v1/platform-admin/relay/channels/${channelId}`,
    `https://platform.example/api/v1/platform-admin/relay/channels/${channelId}/test`,
    `https://platform.example/api/v1/platform-admin/relay/channels/${channelId}/status`,
    `https://platform.example/api/v1/platform-admin/relay/channels/${channelId}/operations/${statusOperationId}`,
  ]);
  assert.deepEqual(captured.map(({ options }) => options.method), ["GET", "POST", "GET", "POST", "POST", "GET"]);
  assert.deepEqual(JSON.parse(captured[1].options.body), {});
  assert.deepEqual(JSON.parse(captured[3].options.body), {
    operation_id: testOperationId,
    reason: "Run approved channel connectivity test",
    approved: true,
  });
  assert.deepEqual(JSON.parse(captured[4].options.body), {
    operation_id: statusOperationId,
    reason: "Disable new admission during credential review",
    approved: true,
    expected_revision: revision,
    target_status: "manually_disabled",
  });
  assert.equal(captured[1].options.headers["Idempotency-Key"], undefined);
  assert.equal(captured[3].options.headers["Idempotency-Key"], testOperationId);
  assert.equal(captured[4].options.headers["Idempotency-Key"], statusOperationId);
  assert.equal(captured[5].options.body, undefined);
});

test("native-console authorization preserves FastAPI detail errors and elevates step-up headers", async () => {
  const makeClient = (headers = {}) => createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company/current",
    accessToken: "platform-admin-token",
    fetcher: async () => new Response(JSON.stringify({
      detail: {
        code: "RELAY_NATIVE_CONSOLE_OWNER_REQUIRED",
        message: "Platform owner authentication is required",
      },
    }), {
      status: 403,
      headers: { "content-type": "application/json", ...headers },
    }),
  });

  await assert.rejects(
    () => makeClient().openAdminRelayNativeConsole(),
    (error) => error.status === 403
      && error.code === "RELAY_NATIVE_CONSOLE_OWNER_REQUIRED"
      && error.message === "Platform owner authentication is required",
  );
  await assert.rejects(
    () => makeClient({ "x-auth-required": "step-up" }).openAdminRelayNativeConsole(),
    (error) => error.status === 403
      && error.code === "STEP_UP_REQUIRED"
      && error.details?.authRequired === "step-up",
  );
});


test("a lost Relay resolve response is surfaced after exactly one HTTP attempt", async () => {
  let attempts = 0;
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company/current",
    accessToken: "platform-admin-token",
    fetcher: async () => {
      attempts += 1;
      throw new Error("connection reset after request write");
    },
  });

  await assert.rejects(
    client.resolveAdminRelayUnknownSubmission(
      "91c5cd71-bde2-4cf9-b6ed-b264b0841f51",
      {
        outcome: "not_created",
        upstream_task_id: "",
        expected_route_id: 208,
        expected_submission_attempt: 1,
        expected_reconciliation_token: `sha256:${"a".repeat(64)}`,
        verification_reference: "provider-console-event-7781",
        reason: "Provider 控制台确认没有任务",
      },
    ),
    /无法连接客户平台 API/,
  );
  assert.equal(attempts, 1);
});
