import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createPlatformClient } from "../src/api/platformClient.js";
import { visibleAdminSections } from "../src/admin/adminApiAdapter.js";
import {
  adaptTaskContentPage,
  buildTaskContentListFilters,
} from "../src/admin/taskContentCollection.js";

const COMPANY_PROMPT = "  雨夜站台\n固定长焦，路面积水反射暖光 🎬  ";
const COMPANY_ITEM = {
  task_id: "task-company-1",
  workspace_type: "company",
  company_id: "company-1",
  company_name: "远创电商",
  personal_workspace_id: null,
  user_id: "user-1",
  user_display_name: "李娜",
  model_id: "model-1",
  model_display_name: "Kling 2.1",
  status: "failed",
  prompt: COMPANY_PROMPT,
  prompt_length: [...COMPANY_PROMPT].length,
  created_at: "2026-08-28T01:00:00Z",
  updated_at: "2026-08-28T01:03:00Z",
};

test("task-content collection keeps the submitted prompt exact and rejects task payload leakage", () => {
  const page = adaptTaskContentPage({
    page: 1,
    page_size: 25,
    total: 1,
    items: [COMPANY_ITEM],
  });
  assert.equal(page.total, 1);
  assert.equal(page.items[0].taskId, "task-company-1");
  assert.equal(page.items[0].prompt, COMPANY_PROMPT);
  assert.equal(page.items[0].status, "failed");

  assert.throws(
    () => adaptTaskContentPage({ page: 1, page_size: 25, total: 0 }),
    /items 缺失/,
  );
  assert.throws(
    () => adaptTaskContentPage({
      page: 1,
      page_size: 25,
      total: 1,
      items: [{ ...COMPANY_ITEM, request_payload: { prompt: COMPANY_PROMPT } }],
    }),
    /任务载荷字段 request_payload/,
  );
  assert.throws(
    () => adaptTaskContentPage({
      page: 1,
      page_size: 25,
      total: 1,
      items: [{ ...COMPANY_ITEM, relay_payload: { prompt: COMPANY_PROMPT } }],
    }),
    /任务载荷字段 relay_payload/,
  );
  assert.throws(
    () => adaptTaskContentPage({
      page: 1,
      page_size: 25,
      total: 1,
      items: [{ ...COMPANY_ITEM, prompt_length: 1 }],
    }),
    /原文与长度证据不一致/,
  );
  assert.throws(
    () => adaptTaskContentPage({
      page: 1,
      page_size: 25,
      total: 1,
      items: [{ ...COMPANY_ITEM, company_id: null }],
    }),
    /工作区范围不一致/,
  );
});

test("task-content collection accepts an unavailable legacy prompt without inventing content", () => {
  const page = adaptTaskContentPage({
    page: 1,
    page_size: 25,
    total: 1,
    items: [{ ...COMPANY_ITEM, prompt: null, prompt_length: 0 }],
  });
  assert.equal(page.items[0].prompt, null);
  assert.equal(page.items[0].promptLength, 0);
});

test("task-content filters never put prompt content into the URL", () => {
  const filters = buildTaskContentListFilters({
    workspaceType: "company",
    status: "cancelled",
    userId: " user-9 ",
    taskId: " task-9 ",
    createdFrom: "2026-08-01T00:00:00Z",
    createdBefore: "2026-09-01T00:00:00Z",
    page: 2,
    pageSize: 25,
    promptQuery: "must be ignored",
  });
  assert.deepEqual(filters, {
    page: 2,
    page_size: 25,
    workspace_type: "company",
    status: "cancelled",
    user_id: "user-9",
    task_id: "task-9",
    created_from: "2026-08-01T00:00:00Z",
    created_before: "2026-09-01T00:00:00Z",
  });
  assert.equal(JSON.stringify(filters).includes("must be ignored"), false);
});

test("only the dedicated task-content permission exposes the collection module", () => {
  assert.deepEqual(
    visibleAdminSections({ permission_codes: ["platform.task_content.read"] }),
    ["prompt-collection"],
  );
  assert.deepEqual(
    visibleAdminSections({ permission_codes: ["platform.analytics.read"] }),
    ["task-operations"],
  );
});

test("platform client reads the collection with one GET and no company header", async () => {
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: "company/current",
    accessToken: "admin-token",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response(JSON.stringify({ page: 1, page_size: 25, total: 0, items: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });
  await client.listAdminTaskContent({ workspace_type: "personal", page: 1, page_size: 25 });

  assert.equal(captured.length, 1);
  assert.equal(captured[0].url, "https://platform.example/api/v1/platform-admin/task-content?workspace_type=personal&page=1&page_size=25");
  assert.equal(captured[0].options.method, "GET");
  assert.equal(captured[0].options.headers["X-Company-ID"], undefined);
  assert.equal("revealAdminTaskContent" in client, false);
});

test("prompt collection UI is a direct read-only ledger without a review workflow", async () => {
  const [promptContainer, adminContainer, drawers, operations, management, operationsCss] = await Promise.all([
    readFile(new URL("../src/admin/promptCollection/PromptCollectionContainer.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/admin/AdminOperationsContainer.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/admin/operations/OperationsDrawers.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/admin/OperationsConsole.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/ManagementConsole.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/design-system/operations-routes.css", import.meta.url), "utf8"),
  ]);
  assert.match(adminContainer, /active=\{operationsVisible && activeSection === "prompt-collection"\}/);
  assert.match(promptContainer, /client\.listAdminTaskContent/);
  assert.match(promptContainer, /setPage\(EMPTY_PAGE\)/);
  assert.match(promptContainer, /new AbortController\(\)/);
  assert.match(promptContainer, /<details className="ops-prompt-entry is-expandable">/);
  assert.match(promptContainer, /<th>提示词<\/th>/);
  assert.doesNotMatch(promptContainer, /review_reason|revealAdminTaskContent|localStorage|sessionStorage|审阅原因|审阅原文|强认证/);
  assert.doesNotMatch(drawers, /PromptRevealDrawer|提示词原文审阅|审阅原因/);
  assert.match(operations, /"prompt-collection": \{ title: "提示词收集"/);
  assert.match(management, /operationsVisible=\{view === "operations"\}/);
  assert.match(operationsCss, /\.ops-console \.ops-prompt-entry summary:focus-visible/);
  assert.doesNotMatch(operationsCss, /ops-prompt-reveal-receipt|ops-prompt-original|ops-prompt-policy-strip/);
});
