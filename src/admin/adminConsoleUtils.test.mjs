import test from "node:test";
import assert from "node:assert/strict";
import {
  buildEntitlementKey,
  createEmptyOperationsData,
  filterExceptions,
  formatDurationSeconds,
  mergeOperationsData,
  resolveEntitlementState,
  summarizeBatchImpact,
  toAuditCsv,
} from "./adminConsoleUtils.js";

test("live data merge keeps empty arrays instead of inventing demo metrics", () => {
  const data = mergeOperationsData({ summary: { pending: 2 }, channels: undefined, business: { trend: undefined } });
  assert.equal(data.summary.pending, 2);
  assert.deepEqual(data.trends, []);
  assert.deepEqual(data.companies, []);
  assert.deepEqual(data.business.trend, []);
  assert.deepEqual(data.channels, []);
  assert.deepEqual(createEmptyOperationsData().exceptions, []);
});

test("entitlement state defaults to unconfigured", () => {
  assert.equal(resolveEntitlementState({}, "company-1", "model-1"), "unconfigured");
  assert.equal(buildEntitlementKey("company-1", "model-1"), "company-1::model-1");
});

test("batch impact excludes cells already in the target state", () => {
  const companies = [{ id: "c1", name: "甲" }, { id: "c2", name: "乙" }];
  const products = [{ id: "p1", name: "模型一" }];
  const grants = {
    [buildEntitlementKey("c1", "p1")]: { state: "enabled" },
    [buildEntitlementKey("c2", "p1")]: { state: "disabled" },
  };
  const impact = summarizeBatchImpact({ companies, products, grants, companyIds: ["c1", "c2"], productIds: ["p1"], nextState: "enabled" });
  assert.equal(impact.cellCount, 2);
  assert.equal(impact.changedCount, 1);
  assert.equal(impact.unchangedCount, 1);
  assert.equal(impact.changes[0].companyId, "c2");
});

test("exception filters combine priority, state and search text", () => {
  const items = [
    { title: "Relay 模型未映射", priority: "P1", status: "open", owner: "张伟" },
    { title: "OAuth 即将到期", priority: "P3", status: "open", owner: "系统" },
  ];
  assert.equal(filterExceptions(items, { query: "relay", priority: "P1", status: "open" }).length, 1);
  assert.equal(filterExceptions(items, { query: "系统", priority: "P1", status: "open" }).length, 0);
});

test("audit csv quotes values and duration formatting is stable", () => {
  const csv = toAuditCsv([{ occurredAt: "2026-08-07", actorName: "周宁", actionLabel: "批量开通", targetLabel: "甲,乙", reason: '合同"更新', result: "success" }]);
  assert.match(csv, /"甲,乙"/);
  assert.match(csv, /"合同""更新"/);
  assert.equal(formatDurationSeconds(0.48), "480ms");
  assert.equal(formatDurationSeconds(134), "02:14");
});
