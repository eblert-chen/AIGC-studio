import {
  ARK_TEST_COMPANY_ID,
  ARK_TEST_USER_ID,
  arkDiscoveryResponses,
} from "../fixtures/ark-video-discovery.mjs";

// This fixture intercepts the real App's Platform transport. It never calls a
// provider, writes a production grant, or starts a paid generation.
export async function installArkPlatformFixture(page, {
  models = arkDiscoveryResponses(),
  uncertainSubmission = false,
  permissions = ["models.read", "assets.read", "assets.manage", "tasks.read", "tasks.create"],
  fixtureLabel = "Ark",
  beforeUpload = null,
} = {}) {
  const state = { requests: [], unexpected: [], submissions: [], assets: [], tasks: [] };
  const companyPrefix = `/api/v1/companies/${ARK_TEST_COMPANY_ID}`;
  const user = {
    id: ARK_TEST_USER_ID,
    email: "ark-contract-test@example.invalid",
    display_name: `${fixtureLabel} 接入回归账号`,
    status: "active",
  };
  await page.addInitScript(({ companyId }) => {
    window.__AI_VIDEO_RUNTIME_CONFIG__ = {
      platformApiUrl: window.location.origin,
      companyId,
    };
  }, { companyId: ARK_TEST_COMPANY_ID });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    state.requests.push({ method, path });
    const respond = (body, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      headers: { "cache-control": "no-store" },
      body: JSON.stringify(body),
    });
    if (method === "GET" && path === "/api/v1/auth/session") {
      return respond({
        authenticated: true,
        login_available: true,
        csrf_token: "ark-browser-fixture-csrf-not-a-real-token",
        user,
        account_type: "company",
        active_product_context: "company",
        available_product_contexts: ["company"],
      });
    }
    if (method === "GET" && path === "/api/v1/session/surfaces") {
      return respond({
        user,
        account_type: "company",
        active_product_context: "company",
        available_product_contexts: ["company"],
        companies: [{ company_id: ARK_TEST_COMPANY_ID, name: `${fixtureLabel} 合同测试企业`, status: "active" }],
        personal: null,
        platform_admin: false,
      });
    }
    if (method === "GET" && path === `${companyPrefix}/me`) {
      return respond({
        ...user,
        user_id: ARK_TEST_USER_ID,
        company_id: ARK_TEST_COMPANY_ID,
        company_name: `${fixtureLabel} 合同测试企业`,
        permission_codes: permissions,
        roles: [{ system_key: "operator", name: "操作员" }],
        is_platform_admin: false,
      });
    }
    if (method === "GET" && path === `${companyPrefix}/models`) return respond(models);
    if (method === "GET" && path === `${companyPrefix}/assets`) return respond(state.assets);
    if (method === "POST" && path === `${companyPrefix}/assets`) {
      const multipart = request.postDataBuffer()?.toString("utf8") || "";
      const kind = multipart.match(/name="media_type"\r?\n\r?\n(image|video|audio)/)?.[1];
      const name = multipart.match(/filename="([^"]+)"/)?.[1];
      if (!kind || !name) {
        state.unexpected.push("Malformed synthetic upload");
        return respond({ error: { code: "FIXTURE_UPLOAD_INVALID", message: "测试上传格式错误" } }, 400);
      }
      if (beforeUpload) await beforeUpload({ kind, name });
      const asset = {
        id: `dddddddd-0000-4000-8000-${String(state.assets.length + 1).padStart(12, "0")}`,
        media_type: kind,
        original_filename: name,
        status: "active",
        size_bytes: request.postDataBuffer()?.length || 0,
      };
      state.assets.push(asset);
      return respond(asset, 201);
    }
    if (method === "GET" && path === `${companyPrefix}/task-history`) {
      const filtered = state.tasks.filter((task) => !url.searchParams.get("status")
        || url.searchParams.get("status") === task.status);
      return respond({ items: filtered, total: filtered.length, page: 1, page_size: 24 });
    }
    if (method === "GET" && path === `${companyPrefix}/artworks`) {
      return respond({ items: [], total: 0, page: 1, page_size: 24 });
    }
    if (method === "GET" && path === `${companyPrefix}/tasks`) return respond(state.tasks);
    if (method === "POST" && path === `${companyPrefix}/tasks`) {
      const body = request.postDataJSON();
      state.submissions.push({ body, idempotencyKey: request.headers()["idempotency-key"] });
      // A successful HTTP response without a task id is uncertain. Returning
      // it twice exercises explicit confirmation and persisted exact replay.
      if (uncertainSubmission && state.submissions.length <= 2) return respond({});
      const task = {
        id: "eeeeeeee-0000-4000-8000-000000000001",
        status: "accepted",
        model_id: body.model_id,
        request_payload: body.request_payload,
        created_at: new Date().toISOString(),
        billing_unit: "POINT",
        billing_version: 2,
      };
      state.tasks = [task];
      return respond(task, 201);
    }
    if (method === "GET" && path.startsWith(`${companyPrefix}/tasks/`)) {
      const task = state.tasks.find((item) => path === `${companyPrefix}/tasks/${item.id}`);
      if (task) return respond(task);
    }
    if (method === "GET" && path === "/api/v1/showcase/home") {
      return respond({ hero: null, items: [], publication_version: 0 });
    }
    state.unexpected.push(`${method} ${path}`);
    return respond({ error: { code: "UNEXPECTED_FIXTURE_REQUEST", message: "测试未允许此接口" } }, 501);
  });
  return state;
}
