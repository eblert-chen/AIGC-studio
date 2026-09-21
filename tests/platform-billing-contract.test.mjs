import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { managementSource as management } from "./management-source.mjs";

const app = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
const adminAdapter = readFileSync(
  new URL("../src/admin/adminApiAdapter.js", import.meta.url),
  "utf8",
);
const businessViews = readFileSync(
  new URL("../src/admin/operations/BusinessEntitlementViews.jsx", import.meta.url),
  "utf8",
);

test("LIVE generation pins the server model quote revision", () => {
  assert.match(
    app,
    /quoteRevision:\s*[\s\S]*?typeof source\.quote_revision === "string"/,
  );
  assert.match(app, /version:\s*6,\s*workspaceKey: submissionStorageKey,\s*creationContext: submissionContext/);
  assert.match(app, /const submissionStorageKey = submissionContext\.scopeKey/);
  assert.match(app, /const fingerprint = JSON\.stringify\([\s\S]*?quoteRevision: targetQuoteRevision,[\s\S]*?requestPayload,[\s\S]*?creationContext: submissionContext/);
  assert.match(app, /if \(storedPending && storedPending\.fingerprint !== requestFingerprint\)\s*\{\s*restorePendingCreate\(storedPending\);\s*return;/);
  assert.match(app, /const pendingReceipt = rememberPendingCreate\(submissionStorageKey, pendingCreate\);\s*if \(!pendingReceipt\.ok\)\s*\{[\s\S]*?return;\s*\}[\s\S]*?studioClient\.createTask\(/);
  assert.match(app, /expectedCapabilityVersion:\s*pendingCreate\.capabilityVersion/);
  assert.match(app, /expectedQuoteRevision:\s*pendingCreate\.quoteRevision/);
  assert.match(app, /当前模型的价格信息无效，暂不能提交。请刷新后重试。/);
});

test("operations dashboard labels incomplete finance as known, not final", () => {
  assert.match(
    adminAdapter,
    /costIncomplete[\s\S]*?known_gross_profit_cents[\s\S]*?gross_profit_cents/,
  );
  assert.match(
    adminAdapter,
    /const grossMargin = hasOperating && !financeIncomplete/,
  );
  assert.match(
    adminAdapter,
    /grossProfit: point\.finance_status === "complete"[\s\S]*?knownGrossProfit: point\.revenue_reconciliation_status === "complete"/,
  );
  assert.match(
    businessViews,
    /function modelProfitLabel\(row\)[\s\S]*?revenueReconciliationStatus !== "complete"[\s\S]*?"毛利待收入归因"/,
  );
  assert.match(
    businessViews,
    /最终毛利只在收入归因与渠道成本都完整时成立；积分不反推现金收入。/,
  );
  assert.match(
    businessViews,
    /revenueReconciliationStatus === "unavailable"[\s\S]*?"积分收入待归因"/,
  );
});

test("legacy finance and task cost surfaces fail closed when evidence is missing", () => {
  const emptySnapshotSource = management.match(
    /function emptySnapshot\(\) \{[\s\S]*?\n\}/,
  )?.[0] || "";
  [
    "wallet",
    "ledger",
    "recharges",
    "taskReport",
    "succeededTaskReport",
    "consumption",
    "downloads",
    "dashboard",
    "channelCosts",
    "adminConsumption",
  ].forEach((field) => assert.match(emptySnapshotSource, new RegExp(`${field}: null`)));
  assert.match(management, /commitData\(\{ dashboard: null, channelCosts: null \}\)/);
  assert.match(management, /hasPlatformOverviewEvidence\(data\)/);
  assert.match(management, /setLoading\(true\);[\s\S]*?commitData\(\{ dashboard: null, channelCosts: null \}\);[\s\S]*?try \{/);
  assert.match(management, /平台财务证据不可用/);
  assert.match(management, /公司账务证据不可用/);
  assert.match(management, /余额流水证据不可用/);
  assert.match(management, /使用报表证据不可用/);
  assert.match(management, /平台消费报表证据不可用/);
  assert.match(management, /taskCostLabel\(task\)/);
  assert.doesNotMatch(
    management,
    /task\.actual_cost_cents == null \? "待结算"/,
  );
});
