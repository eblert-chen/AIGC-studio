import assert from "node:assert/strict";
import test from "node:test";

import {
  hasCompanyOverviewEvidence,
  hasCompanyReportsEvidence,
  hasCompanyWalletEvidence,
  hasPlatformConsumptionEvidence,
  hasPlatformFinanceEvidence,
  hasPlatformOverviewEvidence,
  money,
} from "../src/components/management/managementPresentation.js";

function dashboard(overrides = {}) {
  return {
    billing_unit: "CNY_CENT",
    billing_version: 1,
    platform_recharge_cents: 0,
    platform_income_cents: 0,
    platform_recharge_points: 0,
    platform_consumption_points: 0,
    channel_cost_cents: 0,
    known_gross_profit_cents: 0,
    gross_profit_cents: 0,
    channel_cost_status: "complete",
    unreconciled_succeeded_count: 0,
    revenue_reconciliation_status: "complete",
    unattributed_point_settlement_count: 0,
    finance_status: "complete",
    active_company_count: 0,
    total_task_count: 0,
    succeeded_task_count: 0,
    failed_task_count: 0,
    total_companies: 0,
    page: 1,
    page_size: 50,
    channel_costs: [],
    companies: [],
    ...overrides,
  };
}

test("a complete server-owned zero snapshot remains valid evidence", () => {
  assert.equal(hasPlatformFinanceEvidence(dashboard()), true);
});

test("missing or contradictory finance evidence never becomes a zero dashboard", () => {
  assert.equal(hasPlatformFinanceEvidence(null), false);
  assert.equal(hasPlatformFinanceEvidence(dashboard({ platform_income_cents: undefined })), false);
  assert.equal(hasPlatformFinanceEvidence(dashboard({ platform_consumption_points: undefined })), false);
  assert.equal(hasPlatformFinanceEvidence(dashboard({ channel_cost_status: "complete", gross_profit_cents: null })), false);
  assert.equal(hasPlatformFinanceEvidence(dashboard({ channel_cost_status: "unknown" })), false);
  assert.equal(hasPlatformFinanceEvidence(dashboard({ revenue_reconciliation_status: "unknown" })), false);
  assert.equal(hasPlatformFinanceEvidence(dashboard({ known_gross_profit_cents: 1 })), false);
  assert.equal(hasPlatformFinanceEvidence(dashboard({ unreconciled_succeeded_count: 1 })), false);
  assert.equal(hasPlatformFinanceEvidence(dashboard({ active_company_count: 2, total_companies: 1 })), false);
});

test("incomplete reconciliation accepts a deliberately absent final gross profit", () => {
  assert.equal(hasPlatformFinanceEvidence(dashboard({
    channel_cost_status: "incomplete",
    finance_status: "incomplete",
    gross_profit_cents: null,
    unreconciled_succeeded_count: 2,
  })), true);
  assert.equal(hasPlatformFinanceEvidence(dashboard({
    billing_unit: "POINT",
    billing_version: 2,
    platform_consumption_points: 12,
    revenue_reconciliation_status: "incomplete",
    unattributed_point_settlement_count: 1,
    finance_status: "incomplete",
    gross_profit_cents: null,
  })), true);
  assert.equal(hasPlatformFinanceEvidence(dashboard({
    revenue_reconciliation_status: "incomplete",
    unattributed_point_settlement_count: 1,
    gross_profit_cents: null,
  })), false);
});

test("company finance surfaces require complete server evidence but accept genuine zeros", () => {
  const metadata = { billing_unit: "POINT", billing_version: 2 };
  const wallet = { ...metadata, available_points: 0, reserved_points: 0 };
  const taskPage = { ...metadata, page: 1, page_size: 50, total: 0, total_actual_cost_points: 0, items: [] };
  const rechargePage = { ...metadata, page: 1, page_size: 50, total: 0, total_amount_points: 0, items: [] };
  assert.equal(hasCompanyOverviewEvidence({
    wallet,
    taskReport: taskPage,
    succeededTaskReport: { ...taskPage, page_size: 1 },
  }), true);
  assert.equal(hasCompanyOverviewEvidence({ wallet: null, taskReport: taskPage, succeededTaskReport: taskPage }), false);
  assert.equal(hasCompanyWalletEvidence({ wallet, ledger: [], recharges: rechargePage }), true);
  assert.equal(hasCompanyWalletEvidence({ wallet, ledger: null, recharges: rechargePage }), false);
  const downloads = { page: 1, page_size: 50, total: 0, items: [] };
  assert.equal(hasCompanyReportsEvidence({ taskReport: taskPage, consumption: rechargePage, downloads }), true);
  assert.equal(hasCompanyReportsEvidence({ taskReport: taskPage, consumption: null, downloads }), false);
  assert.equal(hasPlatformConsumptionEvidence({ adminConsumption: rechargePage }), true);
  const channelCosts = { page: 1, page_size: 50, total: 0, total_amount_cents: 0, items: [] };
  assert.equal(hasPlatformOverviewEvidence({ dashboard: dashboard(), channelCosts }), true);
  assert.equal(hasPlatformOverviewEvidence({ dashboard: dashboard(), channelCosts: null }), false);
  assert.equal(hasCompanyWalletEvidence({
    wallet: { available_points: 0, reserved_points: 0 },
    ledger: [],
    recharges: rechargePage,
  }), false);
});

test("legacy company finance evidence remains readable only when explicitly versioned", () => {
  const legacy = { billing_unit: "CNY_CENT", billing_version: 1 };
  const wallet = { ...legacy, available_cents: 0, reserved_cents: 0 };
  const page = { ...legacy, page: 1, page_size: 50, total: 0, total_amount_cents: 0, items: [] };
  const tasks = { ...legacy, page: 1, page_size: 50, total: 0, total_actual_cost_cents: 0, items: [] };
  assert.equal(hasCompanyWalletEvidence({ wallet, ledger: [], recharges: page }), true);
  assert.equal(hasCompanyOverviewEvidence({
    wallet,
    taskReport: tasks,
    succeededTaskReport: { ...tasks, page_size: 1 },
  }), true);
});

test("migration-period reports require split totals and explicit billing evidence on every row", () => {
  const pointRow = { billing_unit: "POINT", billing_version: 2, amount_points: 12 };
  const legacyRow = { billing_unit: "CNY_CENT", billing_version: 1, amount_cents: 250 };
  const mixedTasks = {
    billing_unit: "MIXED",
    billing_version: null,
    page: 1,
    page_size: 50,
    total: 2,
    total_actual_cost_points: 12,
    total_actual_cost_cents: 250,
    items: [pointRow, legacyRow],
  };
  const mixedConsumption = {
    billing_unit: "MIXED",
    billing_version: null,
    page: 1,
    page_size: 50,
    total: 2,
    total_amount_points: 12,
    total_amount_cents: 250,
    items: [pointRow, legacyRow],
  };
  const downloads = { page: 1, page_size: 50, total: 0, items: [] };

  assert.equal(hasCompanyReportsEvidence({
    taskReport: mixedTasks,
    consumption: mixedConsumption,
    downloads,
  }), true);
  assert.equal(hasPlatformConsumptionEvidence({ adminConsumption: mixedConsumption }), true);
  assert.equal(hasPlatformConsumptionEvidence({
    adminConsumption: { ...mixedConsumption, billing_version: 2 },
  }), false);
  assert.equal(hasPlatformConsumptionEvidence({
    adminConsumption: { ...mixedConsumption, total_amount_cents: undefined },
  }), false);
  assert.equal(hasPlatformConsumptionEvidence({
    adminConsumption: {
      ...mixedConsumption,
      items: [pointRow, { amount_cents: 250 }],
    },
  }), false);
  assert.equal(hasPlatformConsumptionEvidence({
    adminConsumption: {
      ...mixedConsumption,
      billing_unit: "POINT",
      billing_version: 2,
    },
  }), false);
});

test("the money formatter distinguishes missing evidence from a real zero", () => {
  assert.equal(money(0), "¥0.00");
  assert.equal(money(null), "金额未提供");
  assert.equal(money(undefined), "金额未提供");
  assert.equal(money(Number.NaN), "金额不可用");
});
