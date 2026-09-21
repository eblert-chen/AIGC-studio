import assert from "node:assert/strict";
import test from "node:test";

import {
  billingAmountLabel,
  billingPresentationState,
  billingTotalsLabel,
  billingUnitLabel,
  formatLegacyCents,
  formatPointAmount,
  generationCostPreview,
  normalizePositivePrice,
  pointBalanceLabel,
} from "../src/billingPresentation.js";
import {
  adaptPersonalPointCollection,
  adaptPersonalPointRecord,
} from "../src/api/sessionPersonalApi.js";

test("generation cost preview never turns missing model or price evidence into zero", () => {
  assert.deepEqual(generationCostPreview({ model: null }), {
    available: false,
    label: "待选择可用模型",
    value: null,
  });
  assert.equal(generationCostPreview({ model: { id: "m", pricingMode: "per_item", rate: null }, outputCount: 1 }).label, "价格待核验");
  assert.equal(normalizePositivePrice(undefined), null);
  assert.equal(normalizePositivePrice(0), null);
  assert.equal(normalizePositivePrice(1.5), null);
  assert.equal(normalizePositivePrice(Number.MAX_SAFE_INTEGER + 1), null);
  assert.equal(generationCostPreview({
    model: { id: "m", pricingMode: "per_item", rate: 2 },
    outputCount: 1.5,
  }).available, false);
});

test("generation cost preview uses explicit v2 point evidence for personal and company workspaces", () => {
  assert.equal(generationCostPreview({
    liveMode: true,
    model: {
      id: "personal",
      pricingMode: "per_item",
      unitPricePoints: 12,
      billingUnit: "POINT",
      billingVersion: 2,
    },
    outputCount: 2,
  }).label, "24 积分");
  assert.equal(generationCostPreview({
    liveMode: true,
    model: {
      id: "company",
      pricingMode: "per_second",
      unitPricePoints: 13,
      billingUnit: "POINT",
      billingVersion: 2,
    },
    duration: 4,
  }).label, "52 积分");
});

test("legacy cents and incomplete metadata never masquerade as points", () => {
  assert.deepEqual(generationCostPreview({
    liveMode: true,
    model: {
      id: "legacy-company",
      pricingMode: "per_item",
      unitPriceCents: 125,
      billingUnit: "CNY_CENT",
      billingVersion: 1,
    },
    outputCount: 2,
  }), {
    available: false,
    label: "历史人民币计费 · 请先迁移",
    value: null,
  });
  assert.equal(generationCostPreview({
    liveMode: true,
    model: { id: "ambiguous", pricingMode: "per_item", unitPricePoints: 12 },
    outputCount: 2,
  }).label, "计费单位待确认");
  assert.equal(generationCostPreview({
    liveMode: true,
    model: {
      id: "mismatch",
      pricingMode: "per_item",
      unitPricePoints: 12,
      billingUnit: "POINT",
      billingVersion: 1,
    },
    outputCount: 2,
  }).available, false);
});

test("billing presentation keeps customer points, historical cents and internal test budgets distinct", () => {
  const points = { billing_unit: "POINT", billing_version: 2, amount_points: 1234 };
  const legacy = { billing_unit: "CNY_CENT", billing_version: 1, amount_cents: 1234 };
  const internal = {
    billing_unit: "POINT",
    billing_version: 2,
    billing_scope: "internal_test",
    amount_points: 50,
  };

  assert.equal(billingPresentationState(points).kind, "points");
  assert.equal(billingPresentationState(legacy).kind, "legacy_cents");
  assert.equal(billingPresentationState(internal).kind, "internal_test");
  assert.equal(billingAmountLabel(points), "1,234 积分");
  assert.equal(billingAmountLabel(legacy), "¥12.34（历史人民币计费）");
  assert.equal(billingAmountLabel(internal), "50 测试积分");
  assert.equal(billingAmountLabel({ amount_points: 999 }), "计费单位待确认");
  assert.equal(billingUnitLabel(internal), "内部测试预算 · 不形成客户收入");
  assert.equal(billingUnitLabel(legacy), "历史人民币计费 · 迁移锁定");
  assert.equal(formatPointAmount(1000000), "1,000,000 积分");
  assert.equal(formatLegacyCents(5), "¥0.05（历史人民币计费）");
});

test("migration reports keep point and historical-cents totals separate", () => {
  const mixed = {
    billing_unit: "MIXED",
    billing_version: null,
    total_amount_points: 123,
    total_amount_cents: 456,
  };

  assert.equal(billingPresentationState(mixed).kind, "mixed");
  assert.equal(billingPresentationState(mixed).available, false);
  assert.equal(billingPresentationState({ ...mixed, billing_version: 2 }).kind, "unverified");
  assert.equal(
    billingPresentationState({ billing_unit: "MIXED", total_amount_points: 123 }).kind,
    "unverified",
  );
  assert.equal(
    billingTotalsLabel(mixed),
    "123 积分 + ¥4.56（历史人民币计费）",
  );
  assert.equal(
    billingTotalsLabel({ ...mixed, total_amount_cents: undefined }),
    "计费汇总待确认",
  );
  assert.equal(
    billingTotalsLabel({ ...mixed, total_amount_cents: null }),
    "计费汇总待确认",
  );
  assert.equal(
    billingAmountLabel({ ...mixed, amount_points: 123, amount_cents: 456 }),
    "计费单位待确认",
  );
  assert.equal(
    billingTotalsLabel({
      billing_unit: "POINT",
      billing_version: 2,
      total_amount_points: 0,
    }),
    "0 积分",
  );
  assert.equal(
    billingTotalsLabel({
      billing_unit: "MIXED",
      billing_version: null,
      total_amount_points: 0,
      total_amount_cents: 0,
    }),
    "0 积分 + ¥0.00（历史人民币计费）",
  );
  assert.equal(formatPointAmount(null), "积分数据不可用");
  assert.equal(formatLegacyCents(""), "历史计费数据不可用");
});

test("historical requests keep their non-charge confirmation label", () => {
  assert.equal(generationCostPreview({ historicalRequestLocked: true }).label, "原提交待确认");
});

test("personal point balance distinguishes explicit zero from missing evidence", () => {
  assert.equal(pointBalanceLabel(0), "0 积分");
  assert.equal(pointBalanceLabel("12"), "12 积分");
  assert.equal(pointBalanceLabel(undefined), "积分数据不可用");
  assert.equal(pointBalanceLabel(-1), "积分数据不可用");
});

test("legacy personal endpoints materialize their route-owned POINT v2 contract", () => {
  assert.deepEqual(adaptPersonalPointRecord({
    workspace_id: "personal-1",
    available_points: 40,
    reserved_points: 2,
  }), {
    workspace_id: "personal-1",
    available_points: 40,
    reserved_points: 2,
    billing_unit: "POINT",
    billing_version: 2,
    billing_scope: "personal",
  });

  const page = adaptPersonalPointCollection({
    page: 1,
    page_size: 20,
    total: 1,
    items: [{ id: "task-1", quote_points: 12 }],
  });
  assert.equal(page.billing_unit, "POINT");
  assert.equal(page.billing_version, 2);
  assert.equal(page.items[0].billing_unit, "POINT");
  assert.equal(page.items[0].billing_version, 2);

  const models = adaptPersonalPointCollection([{ id: "model-1", unit_price_points: 6 }]);
  assert.equal(models[0].billing_unit, "POINT");
  assert.equal(models[0].billing_version, 2);
});

test("personal endpoint compatibility never overwrites conflicting billing evidence", () => {
  assert.deepEqual(adaptPersonalPointRecord({
    id: "conflict-cents",
    billing_unit: "CNY_CENT",
    billing_version: 1,
    amount_cents: 100,
  }), {
    id: "conflict-cents",
    billing_unit: "CNY_CENT",
    billing_version: 1,
    amount_cents: 100,
  });
  assert.deepEqual(adaptPersonalPointRecord({
    id: "conflict-version",
    billing_unit: "POINT",
    billing_version: 1,
    amount_points: 10,
  }), {
    id: "conflict-version",
    billing_unit: "POINT",
    billing_version: 1,
    amount_points: 10,
  });
});
