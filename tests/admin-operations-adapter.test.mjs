import assert from "node:assert/strict";
import test from "node:test";

import {
  adaptAdminOperationsData,
  adaptRelayControlChannel,
  adaptRelayControlChannelPage,
  adminRangeWindow,
  visibleAdminSections,
} from "../src/admin/adminApiAdapter.js";
import { buildEntitlementKey } from "../src/admin/adminConsoleUtils.js";
import {
  adaptRelayProviderResultReconciliationPage,
  relayProviderResultReconciliationPresentation,
} from "../src/admin/relayProviderResultReconciliations.js";
import { operationsSource } from "./operations-source.mjs";

const DAY_MS = 24 * 60 * 60 * 1000;

function liveResponseFixture() {
  const now = Date.now();
  return {
    loadedAt: "2026-08-07T12:30:00+08:00",
    environment: "staging",
    operating: {
      start_time: "2026-08-01T00:00:00+00:00",
      end_time: "2026-08-08T00:00:00+00:00",
      granularity: "day",
      point_bucket_time_basis: "task_updated_at",
      totals: {
        recharge_cents: 120_000,
        settled_revenue_cents: 80_000,
        settled_points: 0,
        point_succeeded_task_count: 0,
        point_settlement_count: 0,
        unattributed_point_settlement_count: 0,
        point_settlement_missing_task_count: 0,
        point_settlement_duplicate_task_count: 0,
        provider_cost_cents: 30_000,
        known_gross_profit_cents: 50_000,
        gross_profit_cents: 50_000,
        gross_margin: 0.625,
        cost_missing_task_count: 0,
        cost_reconciliation_status: "complete",
        revenue_reconciliation_status: "complete",
        finance_status: "complete",
      },
      points: [
        {
          bucket_start: "2026-08-07",
          recharge_cents: 12_345,
          settled_revenue_cents: 8_000,
          settled_points: 0,
          point_succeeded_task_count: 0,
          point_settlement_count: 0,
          unattributed_point_settlement_count: 0,
          point_settlement_missing_task_count: 0,
          point_settlement_duplicate_task_count: 0,
          provider_cost_cents: 3_000,
          known_gross_profit_cents: 5_000,
          gross_profit_cents: 5_000,
          gross_margin: 0.625,
          cost_missing_task_count: 0,
          cost_reconciliation_status: "complete",
          revenue_reconciliation_status: "complete",
          finance_status: "complete",
        },
      ],
      comparisons: {
        period_over_period: {
          status: "available",
          baseline_data_status: "available",
          metrics: {
            recharge_cents: { current: 120_000, baseline: 100_000, absolute_change: 20_000, change_rate: 0.2 },
            settled_revenue_cents: { current: 80_000, baseline: 64_000, absolute_change: 16_000, change_rate: 0.25 },
            settled_points: { current: 0, baseline: 0, absolute_change: 0, change_rate: null },
            point_settlement_count: { current: 0, baseline: 0, absolute_change: 0, change_rate: null },
            provider_cost_cents: { current: 30_000, baseline: 20_000, absolute_change: 10_000, change_rate: 0.5 },
            known_gross_profit_cents: { current: 50_000, baseline: 44_000, absolute_change: 6_000, change_rate: 0.136364 },
            gross_profit_cents: { current: 50_000, baseline: 44_000, absolute_change: 6_000, change_rate: 0.136364 },
            gross_margin: { current: 0.625, baseline: 0.6875, absolute_change: -0.0625, change_rate: -0.090909 },
          },
        },
        year_over_year: {
          status: "unavailable",
          baseline_data_status: "empty",
          metrics: {
            recharge_cents: { current: 120_000, baseline: null, absolute_change: null, change_rate: null },
            settled_revenue_cents: { current: 80_000, baseline: null, absolute_change: null, change_rate: null },
            settled_points: { current: 0, baseline: null, absolute_change: null, change_rate: null },
            point_settlement_count: { current: 0, baseline: null, absolute_change: null, change_rate: null },
            provider_cost_cents: { current: 30_000, baseline: null, absolute_change: null, change_rate: null },
            known_gross_profit_cents: { current: 50_000, baseline: null, absolute_change: null, change_rate: null },
            gross_profit_cents: { current: 50_000, baseline: null, absolute_change: null, change_rate: null },
            gross_margin: { current: 0.625, baseline: null, absolute_change: null, change_rate: null },
          },
        },
      },
    },
    taskOps: {
      status_counts: {
        draft: 1,
        queued: 2,
        processing: 3,
        succeeded: 8,
        failed: 2,
        cancelled: 1,
      },
      total_task_count: 17,
      terminal_task_count: 11,
      success_rate: 8 / 11,
      timeout_count: 1,
      submission_unknown_count: 2,
      latency_seconds: {
        terminal_p50: 5.5,
        terminal_p95: 12,
        succeeded_p50: 4.5,
        succeeded_p95: 9,
        failed_p50: null,
        failed_p95: null,
      },
      failure_reasons: [
        { error_code: "PROVIDER_RATE_LIMITED", count: 2 },
        { error_code: "PROVIDER_TIMEOUT", count: 1 },
      ],
      trend_data_status: "available",
      trend_granularity: "day",
      trend_points: [
        {
          bucket_start: "2026-08-06",
          status_counts: { draft: 0, queued: 1, processing: 1, succeeded: 3, failed: 2, cancelled: 0 },
          total_task_count: 7,
          terminal_task_count: 5,
          success_rate: 0.6,
          timeout_count: 1,
          submission_unknown_count: 0,
          terminal_latency_p50_seconds: 5,
          terminal_latency_p95_seconds: 10,
          failure_reasons: [
            { error_code: "PROVIDER_RATE_LIMITED", count: 1 },
            { error_code: "PROVIDER_TIMEOUT", count: 1 },
          ],
        },
        {
          bucket_start: "2026-08-07",
          status_counts: { draft: 1, queued: 1, processing: 2, succeeded: 5, failed: 1, cancelled: 0 },
          total_task_count: 10,
          terminal_task_count: 6,
          success_rate: 5 / 6,
          timeout_count: 0,
          submission_unknown_count: 2,
          terminal_latency_p50_seconds: 6,
          terminal_latency_p95_seconds: 12,
          failure_reasons: [
            { error_code: "PROVIDER_RATE_LIMITED", count: 1 },
          ],
        },
      ],
      terminal_latency_distribution_seconds: {
        sample_count: 11,
        bins: [
          { range: "[0,10]", count: 2 },
          { range: "(10,30]", count: 4 },
          { range: "(30,60]", count: 3 },
          { range: "(60,120]", count: 1 },
          { range: "(120,300]", count: 1 },
          { range: "(300,600]", count: 0 },
          { range: "(600,+inf)", count: 0 },
        ],
      },
      relay_stage_source_status: "available",
      relay_stage_task_counts: {
        queued: 15,
        submitting: 17,
        submission_unknown: 2,
        provider_processing: 12,
        artifact_transferring: 8,
        artifact_stored: 7,
        failed: 2,
        cancelled: 1,
      },
      artifact_pipeline: {
        source_status: "available",
        transferring_task_count: 8,
        stored_task_count: 7,
      },
      relay_callbacks: {
        source_status: "available",
        event_count: 8,
        terminal_event_count: 6,
      },
    },
    profitability: {
      unattributed_provider_cost_cents: 2_500,
      revenue_unavailable_task_count: 0,
      revenue_missing_task_count: 0,
      revenue_reconciliation_status: "complete",
      items: [
        {
          model_id: "model-complete",
          display_name: "完整成本模型",
          task_count: 10,
          settled_revenue_cents: 20_000,
          provider_cost_cents: 8_000,
          known_gross_profit_cents: 12_000,
          gross_profit_cents: 12_000,
          gross_margin: 0.6,
          revenue_unavailable_task_count: 0,
          revenue_missing_task_count: 0,
          revenue_reconciliation_status: "complete",
          cost_missing_task_count: 0,
          cost_reconciliation_status: "complete",
          success_rate: 0.8,
          average_terminal_latency_seconds: 6.25,
          latency_p95_seconds: 11,
        },
        {
          model_id: "model-incomplete",
          display_name: "待对账模型",
          task_count: 4,
          settled_revenue_cents: 10_000,
          provider_cost_cents: 3_000,
          known_gross_profit_cents: 7_000,
          gross_profit_cents: null,
          gross_margin: null,
          revenue_unavailable_task_count: 0,
          revenue_missing_task_count: 0,
          revenue_reconciliation_status: "complete",
          cost_missing_task_count: 2,
          cost_reconciliation_status: "incomplete",
          success_rate: 0.95,
          average_terminal_latency_seconds: 7.5,
          latency_p95_seconds: 14,
        },
      ],
    },
    companyHealth: {
      low_balance_threshold_cents: 500,
      low_balance_threshold_points: 50,
      items: [
        {
          company_id: "company-risk",
          company_name: "风险企业",
          company_status: "active",
          billing_unit: "CNY_CENT",
          billing_version: 1,
          available_cents: 100,
          reserved_cents: 5_000,
          available_points: null,
          reserved_points: null,
          spend_24h_cents: 70_000,
          spend_24h_points: null,
          last_task_at: new Date(now - 3 * DAY_MS).toISOString(),
          task_count_30d: 20,
          failure_rate_30d: 0.42,
          alerts: [
            { code: "LOW_BALANCE", severity: "critical", details: { available_cents: 100, threshold_cents: 500 } },
            { code: "STALE_RESERVED_BALANCE", severity: "critical", details: { reserved_cents: 5_000, stale_task_count: 1, threshold_hours: 72 } },
            { code: "ABNORMAL_SPEND", severity: "warning", details: { spend_24h_cents: 70_000, baseline_daily_cents: 20_000, ratio: 3.5 } },
            { code: "ENTITLEMENT_EXPIRING", severity: "warning", details: { count: 2 } },
          ],
        },
        {
          company_id: "company-suspended",
          company_name: "已停用企业",
          company_status: "suspended",
          billing_unit: "POINT",
          billing_version: 2,
          available_cents: null,
          reserved_cents: null,
          spend_24h_cents: null,
          available_points: 0,
          reserved_points: 0,
          spend_24h_points: 0,
          last_task_at: null,
          task_count_30d: 0,
          failure_rate_30d: null,
          alerts: [],
        },
      ],
    },
    dashboard: {
      companies: [
        {
          company_id: "company-risk",
          company_name: "风险企业",
          billing_unit: "CNY_CENT",
          billing_version: 1,
          consumption_cents: 70_000,
          available_cents: 100,
          reserved_cents: 5_000,
          consumption_points: 0,
          available_points: 0,
          reserved_points: 0,
          task_count: 20,
          succeeded_count: 15,
        },
      ],
    },
    channelHealth: {
      relay_control_plane_data_status: "available",
      account_pool_metrics: {
        active_account_count: 7,
        cooling_account_count: 1,
        invalid_account_count: 2,
        rate_limit_count: 4,
        failover_count: 5,
        pending_alert_count: 6,
      },
      channels: [
        {
          channel_key: "official-a",
          channel_type: "official",
          source_status: "available",
          route_count: 1,
          observed_success_rate: 0.95,
          provider_cost_cents: 12_000,
          provider_cost_data_status: "available",
          routes: [
            {
              route_id: 41,
              channel_key: "official-a",
              channel_type: "official",
              provider_name: "Official Provider A",
              model: "video-complete",
              mode: "text_to_video",
              enabled: true,
              production_ready: true,
              health_status: "healthy",
              cooling_account_count: 1,
              invalid_account_count: 2,
              rate_limited_account_count: 4,
              successful_task_count: 19,
              failed_task_count: 1,
              observed_success_rate: 0.95,
              latency_p95_ms: 12_345,
            },
          ],
        },
      ],
    },
    readiness: {
      generated_at: "2026-08-07T12:29:00+08:00",
      production_data_ready: false,
      blocking_sources: { channel_costs: "incomplete" },
      sources: {
        platform_db: { source_status: "available", freshness: "live", gaps: [] },
        relay_telemetry: { source_status: "available", freshness: "fresh", gaps: [] },
        channel_costs: { source_status: "incomplete", freshness: "event_driven", gaps: ["successful_tasks_missing_provider_cost"] },
        task_stages: { source_status: "available", freshness: "event_driven", gaps: [] },
      },
    },
    exceptions: {
      generated_at: "2026-08-07T12:25:00+08:00",
      items: [
        {
          category: "PUBLICATION_PENDING_APPROVAL",
          severity: "warning",
          company_id: "company-risk",
          company_name: "风险企业",
          target_type: "publication_job",
          target_id: "publication-1",
          status: "pending_approval",
          occurred_at: "2026-08-07T11:00:00+08:00",
        },
        {
          category: "PUBLISHER_OAUTH_EXPIRING",
          severity: "warning",
          company_id: "company-risk",
          target_type: "publisher_connection",
          target_id: "publisher-1",
          status: "active",
          occurred_at: "2026-08-07T10:00:00+08:00",
        },
        {
          category: "ARTIFACT_STORAGE_TRANSFER_FAILED",
          severity: "critical",
          company_id: "company-risk",
          target_type: "task_artifact",
          target_id: "artifact-1",
          status: "failed",
          error_code: "OBS_CHECKSUM_MISMATCH",
          occurred_at: "2026-08-07T09:00:00+08:00",
        },
        {
          category: "DOWNLOAD_REGISTRATION_FAILED",
          severity: "warning",
          company_id: "company-risk",
          target_type: "download_gateway_registration_attempt",
          target_id: "download-1",
          status: "resolved",
          occurred_at: "2026-08-07T08:00:00+08:00",
        },
        {
          category: "RELAY_SUBMISSION_UNKNOWN",
          severity: "critical",
          company_id: "company-risk",
          target_type: "relay_submission_outbox",
          target_id: "relay-1",
          status: "reconciliation_required",
          occurred_at: "2026-08-07T07:00:00+08:00",
        },
      ],
    },
    relayModels: {
      items: [
        {
          relay_model_id: "relay-model-with-a-name-that-remains-readable-at-production-length",
          candidate_revision: `sha256:${"a".repeat(64)}`,
          status: "mapped",
          routes: [{
            route_id: "volcengine-seedance-primary-01",
            channel_id: 17,
            upstream_model: "doubao-seedance-1-5-pro-250528",
            adapter_profile_id: "volcengine-seedance-v1",
            adapter_profile_revision: "sha256:profile-revision-001",
            enabled: true,
            accepted: true,
            fresh: true,
            required_test_modes: ["text_to_video", "image_to_video"],
            fresh_test_modes: ["text_to_video", "image_to_video"],
          }],
        },
        { model_id: "relay-unmapped", status: "unmapped" },
      ],
      platform_only_model_ids: ["platform-only"],
    },
    matrix: {
      generated_at: "2026-08-07T12:20:00+08:00",
      columns: [
        {
          item_kind: "model",
          item_id: "model-complete",
          catalog_key: "video.complete",
          display_name: "完整成本模型",
          resource_kind: null,
          catalog_active: true,
          lifecycle: "active",
          billing_mode: "per_second",
        },
        {
          item_kind: "resource",
          item_id: "feature-auto-publish",
          catalog_key: "feature.auto_publish",
          display_name: "自动发布",
          resource_kind: "feature",
          catalog_active: true,
          lifecycle: "active",
          billing_mode: null,
        },
        {
          item_kind: "resource",
          item_id: "agent-retired",
          catalog_key: "agent.retired",
          display_name: "已下线智能体",
          resource_kind: "agent",
          catalog_active: false,
          lifecycle: "retired",
          billing_mode: null,
        },
        {
          item_kind: "model",
          item_id: "model-unconfigured",
          catalog_key: "video.unconfigured",
          display_name: "未配置模型",
          resource_kind: null,
          catalog_active: true,
          lifecycle: "active",
          billing_mode: "per_item",
        },
      ],
      rows: [
        {
          company_id: "company-risk",
          company_name: "风险企业",
          company_status: "active",
          billing_unit: "POINT",
          billing_version: 2,
          cells: [
            {
              item_kind: "model",
              item_id: "model-complete",
              state: "enabled",
              configured: true,
              enabled: true,
              billing_unit: "POINT",
              billing_version: 2,
              price_per_second_points: 42,
              price_per_item_points: null,
              price_per_second_cents: null,
              price_per_item_cents: null,
              config_override: { limits: { max_images: 4 } },
              call_quota: 1_000,
              concurrency_limit: 3,
              effective_at: "2026-08-01T00:00:00+00:00",
              expires_at: new Date(now + 7 * DAY_MS).toISOString(),
            },
            {
              item_kind: "resource",
              item_id: "feature-auto-publish",
              state: "scheduled",
              configured: true,
              enabled: true,
              config_override: {},
              call_quota: 200,
              concurrency_limit: 1,
              effective_at: new Date(now + DAY_MS).toISOString(),
              expires_at: null,
            },
            {
              item_kind: "resource",
              item_id: "agent-retired",
              state: "retired",
              configured: false,
            },
            {
              item_kind: "model",
              item_id: "model-unconfigured",
              state: "unconfigured",
              configured: false,
              billing_unit: "POINT",
              billing_version: 2,
              price_per_second_points: null,
              price_per_item_points: null,
              price_per_second_cents: null,
              price_per_item_cents: null,
            },
          ],
        },
      ],
    },
    coverage: {
      total_companies: 10,
      items: [
        {
          item_kind: "model",
          item_id: "model-complete",
          display_name: "完整成本模型",
          resource_kind: null,
          catalog_active: true,
          configured_company_count: 8,
          enabled_company_count: 4,
          disabled_company_count: 2,
          scheduled_company_count: 1,
          expired_company_count: 1,
          coverage_rate: 0.4,
        },
      ],
    },
    audits: {
      items: [
        {
          id: "audit-1",
          created_at: "2026-08-07T12:00:00+08:00",
          actor_user_id: "admin-1",
          action: "entitlement.batch.execute",
          target_type: "company",
          target_id: "company-risk",
          result: "failed",
          before_summary: { enabled: false },
          after_summary: { enabled: true, change_reason: "合同生效" },
        },
        {
          id: "audit-system-1",
          created_at: "2026-08-07T12:01:00+08:00",
          actor_user_id: null,
          actor_kind: "system",
          actor_key: "relay-catalog-sync",
          action: "model.relay_catalog.reconcile",
          target_type: "relay_model_catalog",
          target_id: "sha256:catalog",
          outcome: "SUCCEEDED",
          before_summary: {},
          after_summary: { trigger: "periodic_worker" },
        },
      ],
    },
    permissionCatalog: [
      {
        code: "platform.analytics.read",
        domain: "analytics",
        action: "read",
        description: "查看经营分析",
      },
    ],
    administrators: [
      {
        user_id: "admin-owner",
        email: "owner@example.com",
        display_name: "平台所有者",
        access: {
          is_platform_owner: true,
          effective_permissions: [],
        },
      },
      {
        user_id: "admin-ops",
        email: "ops@example.com",
        display_name: "运营管理员",
        status: "suspended",
        last_active_at: "2026-08-06T08:30:00+08:00",
        access: {
          is_platform_owner: false,
          effective_permissions: ["platform.analytics.read"],
        },
      },
    ],
  };
}

test("admin range windows are deterministic and use the backend query contract", () => {
  const now = new Date("2026-08-07T12:00:00.000Z");
  assert.deepEqual(adminRangeWindow("24h", now), {
    start_time: "2026-08-06T12:00:00.000Z",
    end_time: "2026-08-07T12:00:00.000Z",
    granularity: "day",
  });
  assert.equal(adminRangeWindow("7d", now).start_time, "2026-07-31T12:00:00.000Z");
  assert.equal(adminRangeWindow("30d", now).start_time, "2026-07-08T12:00:00.000Z");
  assert.equal(adminRangeWindow("month", now).start_time, "2026-08-01T00:00:00.000Z");
});

test("platform admin section visibility is fail-closed and honors all-of permission gates", () => {
  assert.deepEqual(visibleAdminSections(null), []);
  assert.deepEqual(visibleAdminSections({ permission_codes: [] }), []);
  assert.equal(visibleAdminSections({ is_platform_owner: true }), undefined);

  assert.deepEqual(
    visibleAdminSections({
      permission_codes: [
        "platform.analytics.read",
        "platform.finance.read",
        "platform.provider_costs.read",
      ],
    }),
    ["cockpit", "task-operations", "model-profit", "company-health"],
  );
  assert.deepEqual(
    visibleAdminSections({
      permission_codes: [
        "platform.entitlements.read",
        "platform.relay_health.read",
        "platform.publishing_exceptions.read",
        "platform.asset_exceptions.read",
        "platform.audit.read",
      ],
    }),
    ["entitlements", "channels", "publishing-assets", "access-audit"],
  );
  assert.deepEqual(
    visibleAdminSections({ permission_codes: ["platform.admin_access.read"] }),
    ["access-audit"],
  );
  assert.deepEqual(
    visibleAdminSections({ permission_codes: ["platform.analytics.manage"] }),
    [],
  );
});

test("real operating, task and model responses map without changing cents or percentages", () => {
  const data = adaptAdminOperationsData(liveResponseFixture());

  assert.deepEqual(data.summary, {
    pending: 6,
    alertBacklog: 6,
    unreconciledCosts: 0,
    unattributedPointSettlements: 0,
    lastRefreshed: "2026-08-07T12:30:00+08:00",
    environment: "staging",
  });
  assert.deepEqual(data.relayRouteOptions, [{
    publicModelId: "relay-model-with-a-name-that-remains-readable-at-production-length",
    capabilityRevision: `sha256:${"a".repeat(64)}`,
    routeId: "volcengine-seedance-primary-01",
    channelId: 17,
    upstreamModel: "doubao-seedance-1-5-pro-250528",
    adapterProfileId: "volcengine-seedance-v1",
    adapterProfileRevision: "sha256:profile-revision-001",
    enabled: true,
    accepted: true,
    fresh: true,
    requiredTestModes: ["text_to_video", "image_to_video"],
    freshTestModes: ["text_to_video", "image_to_video"],
  }]);
  assert.deepEqual(
    data.taskFlow.map(({ key, total, dropoff }) => ({ key, total, dropoff })),
    [
      { key: "submitted", total: 17, dropoff: 3 },
      { key: "queued", total: 15, dropoff: 1 },
      { key: "generating", total: 12, dropoff: 2 },
      { key: "transferring", total: 8, dropoff: undefined },
      { key: "reconciled", total: 6, dropoff: undefined },
    ],
  );
  assert.equal(data.taskFlow[1].rate, 15 / 17 * 100);
  assert.deepEqual(data.timings.map((item) => item.key), ["terminal", "succeeded"]);
  assert.deepEqual(data.failureReasons, [
    { key: "PROVIDER_RATE_LIMITED", label: "PROVIDER_RATE_LIMITED", count: 2, share: 2 / 3 * 100, change: 0 },
    { key: "PROVIDER_TIMEOUT", label: "PROVIDER_TIMEOUT", count: 1, share: 1 / 3 * 100, change: -100 },
  ]);
  assert.deepEqual(data.trends, [
    {
      time: "08-06",
      submitted: 7,
      queued: 1,
      generating: 1,
      completed: 3,
      failed: 2,
      timeout: 1,
      submissionUnknown: 0,
      successRate: 60,
      latencyP50: 5,
      latencyP95: 10,
    },
    {
      time: "08-07",
      submitted: 10,
      queued: 2,
      generating: 2,
      completed: 5,
      failed: 1,
      timeout: 0,
      submissionUnknown: 2,
      successRate: 5 / 6 * 100,
      latencyP50: 6,
      latencyP95: 12,
    },
  ]);
  assert.deepEqual(data.failureTrends, [
    { time: "08-06", PROVIDER_RATE_LIMITED: 1, PROVIDER_TIMEOUT: 1 },
    { time: "08-07", PROVIDER_RATE_LIMITED: 1, PROVIDER_TIMEOUT: 0 },
  ]);
  assert.deepEqual(data.latencyDistribution, [
    { range: "[0,10]", count: 2, share: 2 / 11 * 100, cumulative: 2 / 11 * 100 },
    { range: "(10,30]", count: 4, share: 4 / 11 * 100, cumulative: 6 / 11 * 100 },
    { range: "(30,60]", count: 3, share: 3 / 11 * 100, cumulative: 9 / 11 * 100 },
    { range: "(60,120]", count: 1, share: 1 / 11 * 100, cumulative: 10 / 11 * 100 },
    { range: "(120,300]", count: 1, share: 1 / 11 * 100, cumulative: 100 },
    { range: "(300,600]", count: 0, share: 0, cumulative: 100 },
    { range: "(600,+inf)", count: 0, share: 0, cumulative: 100 },
  ]);

  const revenueMetric = data.business.metrics.find((item) => item.key === "revenue");
  assert.equal(revenueMetric.valueCents, 80_000);
  assert.equal(revenueMetric.change, 25);
  assert.equal(revenueMetric.comparisonStatus, "available");
  assert.equal(revenueMetric.yearOverYearChange, null);
  assert.equal(revenueMetric.yearOverYearStatus, "unavailable");
  assert.deepEqual(revenueMetric.comparisons.periodOverPeriod, {
    status: "available",
    current: 80_000,
    baseline: 64_000,
    absoluteChange: 16_000,
    changeRate: 25,
  });
  assert.deepEqual(data.business.trend, [
    {
      date: "08-07",
      recharge: 123.45,
      revenue: 80,
      settledPoints: 0,
      pointSucceededTaskCount: 0,
      pointSettlementCount: 0,
      pointSettlementMissingTaskCount: 0,
      pointSettlementDuplicateTaskCount: 0,
      cost: 30,
      grossProfit: 50,
      knownGrossProfit: null,
      revenueReconciliationStatus: "complete",
      financeStatus: "complete",
    },
  ]);
  assert.deepEqual(data.modelProfitabilitySummary, {
    unattributedProviderCostCents: 2_500,
    revenueUnavailableTaskCount: 0,
    revenueMissingTaskCount: 0,
    revenueReconciliationStatus: "complete",
  });
  assert.deepEqual(data.modelProfitability[0], {
    id: "model-complete",
    model: "完整成本模型",
    calls: 10,
    revenueCents: 20_000,
    costCents: 8_000,
    knownGrossProfitCents: 12_000,
    grossProfitCents: 12_000,
    costReconciliationStatus: "complete",
    revenueReconciliationStatus: "complete",
    revenueUnavailableTaskCount: 0,
    revenueMissingTaskCount: 0,
    financeStatus: "complete",
    grossMargin: 60,
    successRate: 80,
    avgSeconds: 6.25,
    missingCostCount: 0,
    missingCostRate: 0,
  });
  assert.deepEqual(data.reliability, [
    {
      id: "official-a:41",
      model: "video-complete",
      mode: "text_to_video",
      channel: "Official Provider A",
      channelKey: "official-a",
      channelClass: "official",
      calls: 20,
      successRate: 95,
      p95: 12.345,
      rateLimited: 4,
      failoverCount: 5,
      failoverScope: "account_pool",
      costDataStatus: "available",
      providerCostCents: 12_000,
      evidenceStatus: "available",
      healthStatus: "healthy",
      status: "healthy",
    },
  ]);
  assert.deepEqual(data.dataReadiness, {
    generatedAt: "2026-08-07T12:29:00+08:00",
    productionDataReady: false,
    productionDataReadyKnown: true,
    blockingSources: { channel_costs: "incomplete" },
    sources: liveResponseFixture().readiness.sources,
  });
});

test("incomplete provider costs are labelled as known-only profit instead of exact profit", () => {
  const raw = liveResponseFixture();
  raw.operating.totals = {
    ...raw.operating.totals,
    settled_revenue_cents: 100_000,
    provider_cost_cents: 60_000,
    known_gross_profit_cents: 40_000,
    gross_profit_cents: null,
    gross_margin: null,
    cost_missing_task_count: 2,
    cost_reconciliation_status: "incomplete",
    finance_status: "incomplete",
  };
  raw.operating.comparisons.period_over_period = {
    ...raw.operating.comparisons.period_over_period,
    status: "partial",
    metrics: {
      ...raw.operating.comparisons.period_over_period.metrics,
      known_gross_profit_cents: {
        current: 40_000,
        baseline: 36_000,
        absolute_change: 4_000,
        change_rate: 1 / 9,
      },
      gross_margin: {
        current: null,
        baseline: 0.5,
        absolute_change: null,
        change_rate: null,
      },
    },
  };

  const data = adaptAdminOperationsData(raw);
  const grossProfit = data.business.metrics.find((item) => item.key === "grossProfit");
  const grossMargin = data.business.metrics.find((item) => item.key === "grossMargin");
  assert.equal(data.summary.unreconciledCosts, 2);
  assert.equal(grossProfit.label, "已知毛利（成本未完整）");
  assert.equal(grossProfit.valueCents, 40_000);
  assert.equal(grossProfit.comparisonStatus, "partial");
  assert.equal(grossProfit.change, 1 / 9 * 100);
  assert.equal(grossMargin.label, "最终毛利率（待成本完整）");
  assert.equal(grossMargin.valuePercent, null);
  assert.equal(grossMargin.comparisonStatus, "partial");
  assert.equal(grossMargin.change, null);
  assert.equal(data.modelProfitability[1].knownGrossProfitCents, 7_000);
  assert.equal(data.modelProfitability[1].grossProfitCents, null);
  assert.equal(data.modelProfitability[1].costReconciliationStatus, "incomplete");
  assert.equal(data.modelProfitability[1].grossMargin, null);
  assert.equal(data.modelProfitability[1].missingCostRate, 50);
  assert.match(operationsSource, /已知毛利（成本未完整）/);
  assert.match(operationsSource, /成本未完整/);
  assert.match(operationsSource, /未归因渠道成本/);
});

test("point settlements remain points and make cash profit fail closed", () => {
  const raw = liveResponseFixture();
  raw.operating.totals = {
    ...raw.operating.totals,
    settled_points: 25,
    point_succeeded_task_count: 1,
    point_settlement_count: 1,
    unattributed_point_settlement_count: 1,
    point_settlement_missing_task_count: 0,
    point_settlement_duplicate_task_count: 0,
    known_gross_profit_cents: null,
    gross_profit_cents: null,
    gross_margin: null,
    revenue_reconciliation_status: "incomplete",
    finance_status: "incomplete",
  };
  raw.operating.points[0] = {
    ...raw.operating.points[0],
    settled_points: 25,
    point_succeeded_task_count: 1,
    point_settlement_count: 1,
    unattributed_point_settlement_count: 1,
    point_settlement_missing_task_count: 0,
    point_settlement_duplicate_task_count: 0,
    known_gross_profit_cents: null,
    gross_profit_cents: null,
    gross_margin: null,
    revenue_reconciliation_status: "incomplete",
    finance_status: "incomplete",
  };
  raw.operating.comparisons.period_over_period = {
    ...raw.operating.comparisons.period_over_period,
    status: "partial",
    metrics: {
      ...raw.operating.comparisons.period_over_period.metrics,
      settled_points: {
        current: 25,
        baseline: 0,
        absolute_change: 25,
        change_rate: null,
      },
      gross_profit_cents: {
        current: null,
        baseline: 44_000,
        absolute_change: null,
        change_rate: null,
      },
      gross_margin: {
        current: null,
        baseline: 0.6875,
        absolute_change: null,
        change_rate: null,
      },
    },
  };

  const data = adaptAdminOperationsData(raw);
  const points = data.business.metrics.find((item) => item.key === "points");
  const revenue = data.business.metrics.find((item) => item.key === "revenue");
  const grossProfit = data.business.metrics.find((item) => item.key === "grossProfit");
  const grossMargin = data.business.metrics.find((item) => item.key === "grossMargin");

  assert.equal(data.sourceStatus.operating, "available");
  assert.equal(points.valuePoints, 25);
  assert.equal(points.settlementCount, 1);
  assert.equal(revenue.valueCents, 80_000);
  assert.equal(grossProfit.label, "最终毛利（积分收入待归因）");
  assert.equal(grossProfit.valueCents, null);
  assert.equal(grossProfit.unavailableLabel, "待积分收入归因");
  assert.equal(grossMargin.valuePercent, null);
  assert.equal(grossMargin.unavailableLabel, "待积分收入归因");
  assert.deepEqual(data.business.financeEvidence, {
    settledPoints: 25,
    pointSucceededTaskCount: 1,
    pointSettlementCount: 1,
    unattributedPointSettlementCount: 1,
    pointSettlementMissingTaskCount: 0,
    pointSettlementDuplicateTaskCount: 0,
    costReconciliationStatus: "complete",
    revenueReconciliationStatus: "incomplete",
    financeStatus: "incomplete",
  });
  assert.equal(data.business.trend[0].revenue, 80);
  assert.equal(data.business.trend[0].settledPoints, 25);
  assert.equal(data.business.trend[0].grossProfit, null);
  assert.equal(data.business.trend[0].knownGrossProfit, null);
  assert.equal(data.summary.unattributedPointSettlements, 1);
  assert.match(operationsSource, /积分只作为消费权益展示，不按兑换锚点折算为现金收入/);
  assert.match(operationsSource, /已归因法币收入/);
});

test("operating evidence rejects point revenue mixed into a final cash profit", () => {
  const raw = liveResponseFixture();
  raw.operating.totals = {
    ...raw.operating.totals,
    settled_points: 10,
    point_succeeded_task_count: 1,
    point_settlement_count: 1,
    unattributed_point_settlement_count: 1,
    point_settlement_missing_task_count: 0,
    point_settlement_duplicate_task_count: 0,
    revenue_reconciliation_status: "incomplete",
    finance_status: "incomplete",
  };

  const data = adaptAdminOperationsData(raw);
  assert.equal(data.sourceStatus.operating, "unavailable");
  assert.deepEqual(data.business.metrics, []);
  assert.deepEqual(data.business.trend, []);
});

test("malformed successful finance responses fail closed instead of becoming zero evidence", () => {
  const malformedOperating = liveResponseFixture();
  malformedOperating.sourceStatuses = { operating: "available", profitability: "available" };
  delete malformedOperating.operating.totals.provider_cost_cents;

  const operatingData = adaptAdminOperationsData(malformedOperating);
  assert.equal(operatingData.sourceStatus.operating, "unavailable");
  assert.match(operatingData.sourceErrors.operating, /完整、可核验/);
  assert.deepEqual(operatingData.business.metrics, []);
  assert.deepEqual(operatingData.business.trend, []);
  assert.equal(operatingData.summary.unreconciledCosts, null);

  const malformedProfitability = liveResponseFixture();
  malformedProfitability.sourceStatuses = { operating: "available", profitability: "available" };
  delete malformedProfitability.profitability.items[0].provider_cost_cents;

  const profitabilityData = adaptAdminOperationsData(malformedProfitability);
  assert.equal(profitabilityData.sourceStatus.profitability, "unavailable");
  assert.match(profitabilityData.sourceErrors.profitability, /未归因渠道成本证据/);
  assert.deepEqual(profitabilityData.modelProfitability, []);
  assert.deepEqual(profitabilityData.modelProfitabilitySummary, {
    unattributedProviderCostCents: null,
    revenueUnavailableTaskCount: null,
    revenueMissingTaskCount: null,
    revenueReconciliationStatus: "unavailable",
  });
});

test("missing model success and latency evidence remains unavailable rather than zero", () => {
  const raw = liveResponseFixture();
  delete raw.profitability.items[0].success_rate;
  delete raw.profitability.items[0].average_terminal_latency_seconds;

  const data = adaptAdminOperationsData(raw);
  assert.equal(data.sourceStatus.profitability, "available");
  assert.equal(data.modelProfitability[0].successRate, null);
  assert.equal(data.modelProfitability[0].avgSeconds, null);
  assert.match(operationsSource, /row\.successRate == null/);
  assert.match(operationsSource, /row\.avgSeconds == null/);
  assert.match(operationsSource, /row\.avgSeconds == null \? "待核验"/);
});

test("company health preserves server alerts and does not let a suspended company look healthy", () => {
  const data = adaptAdminOperationsData(liveResponseFixture());
  const risk = data.companyHealth.find((item) => item.id === "company-risk");
  const suspended = data.companyHealth.find((item) => item.id === "company-suspended");

  assert.equal(risk.risk, "critical");
  assert.equal(risk.billingUnit, "CNY_CENT");
  assert.equal(risk.balanceCents, 100);
  assert.equal(risk.balancePoints, null);
  assert.equal(risk.spend24hCents, 70_000);
  assert.equal(risk.spend24hPoints, null);
  assert.equal(risk.failureRate, 42);
  assert.equal(risk.consumptionChange, 250);
  assert.equal(risk.reservationAgeHours, 72);
  assert.equal(risk.entitlementsExpiring, 2);
  assert.deepEqual(risk.reasons, ["余额不足", "预留余额长期未释放", "消费异常增长", "权益即将到期"]);
  assert.equal(risk.dashboard.name, "风险企业");
  assert.equal(suspended.risk, "inactive");
  assert.equal(suspended.billingUnit, "POINT");
  assert.equal(suspended.balanceCents, null);
  assert.equal(suspended.balancePoints, 0);
  assert.equal(suspended.daysInactive, null);

  assert.deepEqual(data.business.companyRanking, [
    {
      id: "company-risk",
      name: "风险企业",
      billingUnit: "CNY_CENT",
      billingVersion: 1,
      consumptionCents: 70_000,
      consumptionPoints: null,
      taskCount: 20,
      successRate: 75,
      balanceCents: 100,
      balancePoints: null,
      reservedCents: 5_000,
      reservedPoints: null,
    },
  ]);
});

test("company health and ranking keep point companies in points without cash conversion", () => {
  const raw = liveResponseFixture();
  raw.dashboard.companies.push({
    company_id: "company-points",
    company_name: "积分企业",
    billing_unit: "POINT",
    billing_version: 2,
    consumption_cents: 99_999,
    available_cents: 88_888,
    reserved_cents: 77_777,
    consumption_points: 320,
    available_points: 90,
    reserved_points: 10,
    task_count: 30,
    succeeded_count: 24,
  });

  const data = adaptAdminOperationsData(raw);
  assert.equal(data.sourceStatus.dashboard, "available");
  assert.equal(data.sourceStatus.companyHealth, "available");
  assert.deepEqual(data.business.companyRanking[0], {
    id: "company-points",
    name: "积分企业",
    billingUnit: "POINT",
    billingVersion: 2,
    consumptionCents: null,
    consumptionPoints: 320,
    taskCount: 30,
    successRate: 80,
    balanceCents: null,
    balancePoints: 90,
    reservedCents: null,
    reservedPoints: 10,
  });
  assert.match(operationsSource, /消费权益按企业当前计费单位展示，不把积分换算为现金收入/);
  assert.match(operationsSource, /积分不折算为现金/);
});

test("company health and ranking fail closed when billing metadata or unit evidence conflicts", () => {
  const malformedHealth = liveResponseFixture();
  malformedHealth.companyHealth.items[1].available_cents = 0;
  const healthData = adaptAdminOperationsData(malformedHealth);
  assert.equal(healthData.sourceStatus.companyHealth, "unavailable");
  assert.deepEqual(healthData.companyHealth, []);
  assert.match(healthData.sourceErrors.companyHealth, /匹配计费单位/);

  const malformedRanking = liveResponseFixture();
  malformedRanking.dashboard.companies[0].billing_unit = "POINT";
  malformedRanking.dashboard.companies[0].billing_version = 1;
  const rankingData = adaptAdminOperationsData(malformedRanking);
  assert.equal(rankingData.sourceStatus.dashboard, "unavailable");
  assert.deepEqual(rankingData.business.companyRanking, []);
  assert.match(rankingData.sourceErrors.dashboard, /匹配计费单位/);
});

test("successful point tasks with missing settlement evidence keep operating finance incomplete", () => {
  const raw = liveResponseFixture();
  for (const record of [raw.operating.totals, raw.operating.points[0]]) {
    record.point_succeeded_task_count = 1;
    record.point_settlement_count = 0;
    record.unattributed_point_settlement_count = 0;
    record.point_settlement_missing_task_count = 1;
    record.point_settlement_duplicate_task_count = 0;
    record.known_gross_profit_cents = null;
    record.gross_profit_cents = null;
    record.gross_margin = null;
    record.revenue_reconciliation_status = "incomplete";
    record.finance_status = "incomplete";
  }

  const data = adaptAdminOperationsData(raw);
  assert.equal(data.sourceStatus.operating, "available");
  assert.equal(data.business.financeEvidence.pointSucceededTaskCount, 1);
  assert.equal(data.business.financeEvidence.pointSettlementCount, 0);
  assert.equal(data.business.financeEvidence.pointSettlementMissingTaskCount, 1);
  assert.equal(data.business.financeEvidence.financeStatus, "incomplete");
  assert.equal(data.business.metrics.find((item) => item.key === "grossProfit").valueCents, null);
  assert.match(operationsSource, /成功积分任务缺少结算分录/);
});

test("model profitability keeps point revenue unavailable instead of rendering a zero cash income", () => {
  const raw = liveResponseFixture();
  raw.profitability.revenue_unavailable_task_count = 1;
  raw.profitability.revenue_reconciliation_status = "unavailable";
  raw.profitability.items[0] = {
    ...raw.profitability.items[0],
    settled_revenue_cents: 0,
    known_gross_profit_cents: null,
    gross_profit_cents: null,
    gross_margin: null,
    revenue_unavailable_task_count: 1,
    revenue_missing_task_count: 0,
    revenue_reconciliation_status: "unavailable",
  };

  const data = adaptAdminOperationsData(raw);
  const row = data.modelProfitability.find((item) => item.id === "model-complete");
  assert.equal(data.sourceStatus.profitability, "available");
  assert.equal(row.revenueCents, null);
  assert.equal(row.revenueReconciliationStatus, "unavailable");
  assert.equal(row.revenueUnavailableTaskCount, 1);
  assert.equal(row.grossProfitCents, null);
  assert.equal(row.grossMargin, null);
  assert.equal(row.financeStatus, "incomplete");
  assert.match(operationsSource, /积分收入待归因/);
  assert.match(operationsSource, /不会按积分兑换锚点反推收入/);

  raw.profitability.items[0].gross_profit_cents = 0;
  const malformed = adaptAdminOperationsData(raw);
  assert.equal(malformed.sourceStatus.profitability, "unavailable");
  assert.deepEqual(malformed.modelProfitability, []);
});

test("exception center keeps publishing, assets and operational failures in separate queues", () => {
  const data = adaptAdminOperationsData(liveResponseFixture());

  assert.deepEqual(data.publishingExceptions.map((item) => item.targetId), ["publication-1", "publisher-1"]);
  assert.deepEqual(data.assetExceptions.map((item) => item.targetId), ["artifact-1", "download-1"]);
  assert.deepEqual(data.exceptions.map((item) => item.targetId), ["relay-unmapped", "platform-only", "relay-1"]);
  assert.equal(data.publishingExceptions[0].title, "发布任务待审批");
  assert.equal(data.assetExceptions[0].priority, "P1");
  assert.equal(data.assetExceptions[0].errorCode, "OBS_CHECKSUM_MISMATCH");
  assert.equal(data.assetExceptions[1].status, "resolved");
  assert.equal(data.exceptions[2].title, "Relay 提交结果未知");
  assert.equal(data.exceptions[2].raw.target_id, "relay-1");
});

test("Relay unknown submissions remain a separate fenced operations queue", () => {
  const token = `sha256:${"b".repeat(64)}`;
  const data = adaptAdminOperationsData({
    relayUnknownSubmissions: {
      page: 1,
      page_size: 50,
      total: 1,
      data: [{
        job_id: "91c5cd71-bde2-4cf9-b6ed-b264b0841f51",
        tenant_id: "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
        model: "kling-video-v2.1",
        mode: "image_to_video",
        status: "reconciliation_required",
        provider_route_id: 208,
        provider_route_key: "kling-official-prod-01",
        provider_name: "Kling Official",
        provider_account_id: "kling-account-07",
        provider_channel_id: 31,
        provider_key_index: 0,
        provider_channel_class: "official",
        provider_upstream_model: "kling-v2-1-master",
        provider_submission_attempt: 2,
        unknown_at: "2026-08-07T03:31:00Z",
        reconciliation_token: token,
        error_code: "PROVIDER_RESPONSE_LOSS",
        error_message: "Provider response was lost",
      }],
    },
  });

  assert.equal(data.relayUnknownSubmissionTotal, 1);
  assert.equal(data.relayUnknownSubmissions[0].jobId, "91c5cd71-bde2-4cf9-b6ed-b264b0841f51");
  assert.equal(data.relayUnknownSubmissions[0].providerRouteId, 208);
  assert.equal(data.relayUnknownSubmissions[0].providerSubmissionAttempt, 2);
  assert.equal(data.relayUnknownSubmissions[0].reconciliationToken, token);
  assert.deepEqual(data.exceptions, []);
});

test("provider-result reconciliation is an independent secret-free read-only queue", () => {
  const providerUrl = "https://provider.example.test/result.mp4?token=must-not-render";
  const base = {
    api_version: "v1",
    schema_version: 1,
    object: "generation.provider_result_reconciliation",
    tenant_id: "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
    client_reference_id: "https://campaign.example/reference/42",
    model: "seedance-2.0",
    mode: "text_to_video",
    progress: 95,
    provider_route_id: 41,
    provider_channel_id: 7,
    provider_submission_attempt: 2,
    upstream_task_id: "provider-task-42",
    resolution_supported: false,
    evidence_retained: true,
    error_code: "PROVIDER_POLL_RECONCILIATION_REQUIRED",
    error_message: "server-owned summary",
    created_at: "2026-08-28T01:02:03Z",
    updated_at: "2026-08-28T01:04:05Z",
    result_url: providerUrl,
    temporary_result_json: JSON.stringify({ provider_url: providerUrl }),
    fail_reason: providerUrl,
    credential: "must-not-render",
  };
  const rawPage = {
    api_version: "v1",
    schema_version: 1,
    object: "list",
    page: 1,
    page_size: 100,
    total: 4,
    data: [
      {
        ...base,
        job_id: "91c5cd71-bde2-4cf9-b6ed-b264b0841f51",
        status: "reconciliation_required",
        reconciliation_kind: "missing_native_task",
      },
      {
        ...base,
        job_id: "a1c5cd71-bde2-4cf9-b6ed-b264b0841f52",
        status: "reconciliation_required",
        reconciliation_kind: "provider_result_proof",
      },
      {
        ...base,
        job_id: "b1c5cd71-bde2-4cf9-b6ed-b264b0841f53",
        status: "succeeded",
        progress: 100,
        error_code: "",
        reconciliation_kind: "provider_material",
      },
      {
        ...base,
        job_id: "c1c5cd71-bde2-4cf9-b6ed-b264b0841f54",
        status: "reconciliation_required",
        reconciliation_kind: "provider_material",
      },
    ],
  };

  const page = adaptRelayProviderResultReconciliationPage(rawPage);
  assert.equal(page.sourceStatus, "available");
  assert.equal(page.total, 4);
  assert.deepEqual(
    Object.keys(page.items[0]).sort(),
    [
      "clientReferenceId",
      "createdAt",
      "errorCode",
      "evidenceRetained",
      "jobId",
      "mode",
      "model",
      "progress",
      "providerChannelId",
      "providerRouteId",
      "providerSubmissionAttempt",
      "reconciliationKind",
      "status",
      "updatedAt",
      "upstreamTaskId",
    ].sort(),
  );
  const serialized = JSON.stringify(page);
  assert.equal(serialized.includes(providerUrl), false);
  assert.equal(serialized.includes("must-not-render"), false);
  assert.equal(serialized.includes("temporary_result_json"), false);
  assert.equal(serialized.includes("fail_reason"), false);
  assert.equal(serialized.includes("tenant_id"), false);
  assert.equal(serialized.includes("https://campaign.example/reference/42"), true);

  assert.equal(
    relayProviderResultReconciliationPresentation(page.items[0]).label,
    "原生任务证据缺失",
  );
  assert.equal(
    relayProviderResultReconciliationPresentation(page.items[1]).label,
    "终态证明冲突",
  );
  const published = relayProviderResultReconciliationPresentation(page.items[2]);
  assert.equal(published.label, "临时材料待核对");
  assert.match(published.guidance, /长期作品已发布/);
  assert.match(published.guidance, /不要重生成或删除作品/);
  const unpublished = relayProviderResultReconciliationPresentation(page.items[3]);
  assert.match(unpublished.guidance, /作品尚未发布/);
  assert.match(unpublished.guidance, /禁止重试或重新生成/);

  const data = adaptAdminOperationsData({ relayProviderResultReconciliations: rawPage });
  assert.equal(data.relayProviderResultReconciliationSourceStatus, "available");
  assert.equal(data.relayProviderResultReconciliationTotal, 4);
  assert.equal(data.relayProviderResultReconciliations.length, 4);
});

test("provider-result reconciliation rejects an unsafe or actionable facade as unavailable", () => {
  const item = {
    api_version: "v1",
    schema_version: 1,
    object: "generation.provider_result_reconciliation",
    job_id: "91c5cd71-bde2-4cf9-b6ed-b264b0841f51",
    client_reference_id: null,
    model: "seedance-2.0",
    mode: "text_to_video",
    status: "reconciliation_required",
    progress: 95,
    provider_route_id: 41,
    provider_channel_id: 7,
    provider_submission_attempt: 2,
    upstream_task_id: "provider-task-42",
    reconciliation_kind: "provider_result_proof",
    resolution_supported: true,
    evidence_retained: true,
    error_code: "PROVIDER_POLL_RECONCILIATION_REQUIRED",
    created_at: "2026-08-28T01:02:03Z",
    updated_at: "2026-08-28T01:04:05Z",
  };
  const page = {
    api_version: "v1",
    schema_version: 1,
    object: "list",
    page: 1,
    page_size: 100,
    total: 1,
  };
  const actionable = adaptRelayProviderResultReconciliationPage({ ...page, data: [item] });
  assert.equal(actionable.sourceStatus, "unavailable");
  assert.equal(actionable.total, null);
  assert.deepEqual(actionable.items, []);

  const urlBearing = adaptRelayProviderResultReconciliationPage({
    ...page,
    data: [{ ...item, resolution_supported: false, upstream_task_id: "https://provider.test/private" }],
  });
  assert.equal(urlBearing.sourceStatus, "unavailable");
  assert.equal(urlBearing.total, null);
  assert.deepEqual(urlBearing.items, []);

  for (const [label, candidate] of [
    ["wrong page schema", { ...page, schema_version: 2, data: [{ ...item, resolution_supported: false }] }],
    ["wrong item object", { ...page, data: [{ ...item, resolution_supported: false, object: "task" }] }],
    ["timestamp without offset", { ...page, data: [{ ...item, resolution_supported: false, created_at: "2026-08-28T01:02:03" }] }],
    ["time regression", { ...page, data: [{ ...item, resolution_supported: false, updated_at: "2026-08-28T01:00:00Z" }] }],
    ["terminal status on proof conflict", { ...page, data: [{ ...item, resolution_supported: false, status: "succeeded" }] }],
  ]) {
    const result = adaptRelayProviderResultReconciliationPage(candidate);
    assert.equal(result.sourceStatus, "unavailable", label);
    assert.equal(result.total, null, label);
    assert.deepEqual(result.items, [], label);
  }
});

test("Relay channel facade is projected through a strict secret-free UI whitelist", () => {
  const raw = {
    id: 17,
    name: "Official video primary",
    type: 1,
    type_label: "OpenAI",
    status: "enabled",
    configured_models: ["video-v1", "", 42],
    test_model: "video-v1",
    test_supported: true,
    weight: 100,
    priority: 10,
    auto_ban: true,
    tag: "official",
    created_at: "2026-08-01T00:00:00Z",
    last_tested_at: "2026-08-14T00:00:00Z",
    response_time_ms: 413,
    credential: { configured: true, key_count: 9, key: "SECRET_CANARY" },
    revision: `sha256:${"d".repeat(64)}`,
    base_url: "https://secret-provider.example",
    headers: { Authorization: "SECRET_CANARY" },
    proxy: "http://secret-proxy.example",
    fingerprint: "SECRET_CANARY",
    error: "SECRET_CANARY",
  };
  const channel = adaptRelayControlChannel(raw);

  assert.deepEqual(channel, {
    id: 17,
    name: "Official video primary",
    type: 1,
    typeLabel: "OpenAI",
    status: "enabled",
    configuredModels: ["video-v1"],
    modelCount: 1,
    testModel: "video-v1",
    testSupported: true,
    weight: 100,
    priority: 10,
    autoBan: true,
    tag: "official",
    createdAt: "2026-08-01T00:00:00Z",
    lastTestedAt: "2026-08-14T00:00:00Z",
    responseTimeMs: 413,
    credentialConfigured: true,
    revision: `sha256:${"d".repeat(64)}`,
  });
  const serialized = JSON.stringify(channel);
  for (const forbidden of ["SECRET_CANARY", "base_url", "headers", "proxy", "fingerprint", "key_count"]) {
    assert.doesNotMatch(serialized, new RegExp(forbidden));
  }

  assert.deepEqual(adaptRelayControlChannelPage(null), {
    items: [],
    total: null,
    sourceStatus: "unavailable",
  });
  assert.equal(adaptRelayControlChannelPage({ data: [raw], total: 1 }).sourceStatus, "available");
});

test("entitlement matrix maps model pricing, schedules, limits and retired catalog states", () => {
  const data = adaptAdminOperationsData(liveResponseFixture());
  assert.deepEqual(data.companies, [
    { id: "company-risk", name: "风险企业", status: "active", plan: "独立合同", billingUnit: "POINT", billingVersion: 2 },
  ]);
  assert.deepEqual(
    data.entitlementProducts.map(({ id, kind, billingMode, status }) => ({ id, kind, billingMode, status })),
    [
      { id: "model-complete", kind: "model", billingMode: "per_second", status: "active" },
      { id: "feature-auto-publish", kind: "feature", billingMode: null, status: "active" },
      { id: "agent-retired", kind: "agent", billingMode: null, status: "retired" },
      { id: "model-unconfigured", kind: "model", billingMode: "per_item", status: "active" },
    ],
  );

  const modelGrant = data.entitlementGrants[buildEntitlementKey("company-risk", "model-complete")];
  const scheduledGrant = data.entitlementGrants[buildEntitlementKey("company-risk", "feature-auto-publish")];
  const retiredGrant = data.entitlementGrants[buildEntitlementKey("company-risk", "agent-retired")];
  assert.equal(modelGrant.state, "expiring");
  assert.equal(modelGrant.serverState, "enabled");
  assert.equal(modelGrant.billingUnit, "POINT");
  assert.equal(modelGrant.billingVersion, 2);
  assert.equal(modelGrant.pricePoints, 42);
  assert.equal(modelGrant.priceCents, null);
  assert.equal(modelGrant.quota, 1_000);
  assert.equal(modelGrant.concurrency, 3);
  assert.match(modelGrant.capabilityLimit, /"max_images": 4/);
  assert.equal(scheduledGrant.state, "scheduled");
  assert.equal(scheduledGrant.quota, 200);
  assert.equal(retiredGrant.state, "retired");
  assert.equal(data.entitlementGrants[buildEntitlementKey("company-risk", "model-unconfigured")], undefined);
  assert.deepEqual(data.entitlementTemplates, []);
  assert.deepEqual(data.entitlementCoverage, [
    {
      id: "model-complete",
      name: "完整成本模型",
      kind: "model",
      active: true,
      totalCompanies: 10,
      configuredCompanies: 8,
      enabledCompanies: 4,
      disabledCompanies: 2,
      scheduledCompanies: 1,
      expiredCompanies: 1,
      coverageRate: 40,
    },
  ]);
});

test("channel, audit and administrator responses preserve unavailable evidence and access detail", () => {
  const data = adaptAdminOperationsData(liveResponseFixture());
  assert.deepEqual(data.channels, [
    {
      id: "official-a",
      name: "official-a",
      channelClass: "official",
      successRate: 95,
      activeAccounts: "—",
      coolingAccounts: 1,
      invalidAccounts: 2,
      rateLimits: 4,
      failovers: 5,
      alertBacklog: 6,
      status: "healthy",
      evidenceStatus: "available",
      routeCount: 1,
    },
  ]);
  assert.equal(data.auditEvents[0].reason, "合同生效");
  assert.equal(data.auditEvents[0].result, "failed");
  assert.deepEqual(data.auditEvents[0].before, { enabled: false });
  assert.deepEqual(data.auditEvents[0].after, { enabled: true, change_reason: "合同生效" });
  assert.equal(data.auditEvents[1].actorName, "系统任务 · relay-catalog-sync");
  assert.equal(data.auditEvents[1].actorId, "relay-catalog-sync");
  assert.deepEqual(data.adminPermissionCatalog, [
    { key: "platform.analytics.read", label: "查看经营分析", group: "经营分析" },
  ]);
  assert.deepEqual(data.platformAdmins.map(({ id, owner, permissions }) => ({ id, owner, permissions })), [
    { id: "admin-owner", owner: true, permissions: ["*"] },
    { id: "admin-ops", owner: false, permissions: ["platform.analytics.read"] },
  ]);
  assert.equal(data.platformAdmins[1].status, "suspended");
  assert.equal(data.platformAdmins[1].lastActiveAt, "2026-08-06T08:30:00+08:00");
});

test("missing live datasets stay empty and never become demo-like zero dashboards", () => {
  const data = adaptAdminOperationsData({
    environment: "production",
    sourceStatuses: { operating: "failed", taskOps: "unauthorized" },
    sourceErrors: { operating: "upstream timeout" },
  });
  assert.deepEqual(data.taskFlow, []);
  assert.deepEqual(data.timings, []);
  assert.deepEqual(data.failureReasons, []);
  assert.deepEqual(data.business.metrics, []);
  assert.deepEqual(data.business.trend, []);
  assert.deepEqual(data.modelProfitability, []);
  assert.deepEqual(data.companyHealth, []);
  assert.deepEqual(data.channels, []);
  assert.equal(data.relayUnknownSubmissionSourceStatus, "unavailable");
  assert.equal(data.relayUnknownSubmissionTotal, null);
  assert.deepEqual(data.relayUnknownSubmissions, []);
  assert.deepEqual(data.exceptions, []);
  assert.deepEqual(data.entitlementGrants, {});
  assert.equal(data.summary.lastRefreshed, null);
  assert.equal(data.summary.alertBacklog, null);
  assert.equal(data.dataReadiness, null);
  assert.equal(data.sourceStatus.operating, "failed");
  assert.equal(data.sourceStatus.taskOps, "unauthorized");
  assert.equal(data.sourceErrors.operating, "upstream timeout");

  const confirmedEmpty = adaptAdminOperationsData({
    operating: {
      point_bucket_time_basis: "task_updated_at",
      totals: {
        recharge_cents: 0,
        settled_revenue_cents: 0,
        settled_points: 0,
        point_succeeded_task_count: 0,
        point_settlement_count: 0,
        unattributed_point_settlement_count: 0,
        point_settlement_missing_task_count: 0,
        point_settlement_duplicate_task_count: 0,
        provider_cost_cents: 0,
        known_gross_profit_cents: 0,
        gross_profit_cents: 0,
        gross_margin: null,
        cost_missing_task_count: 0,
        cost_reconciliation_status: "complete",
        revenue_reconciliation_status: "complete",
        finance_status: "complete",
      },
      points: [],
      end_time: "2026-08-07T00:00:00Z",
    },
    taskOps: { status_counts: {}, total_task_count: 0, latency_seconds: {}, failure_reasons: [] },
  });
  assert.equal(confirmedEmpty.business.metrics.length, 6);
  assert.equal(confirmedEmpty.sourceStatus.operating, "available");
  assert.equal(confirmedEmpty.business.metrics[0].valueCents, 0);
  assert.equal(confirmedEmpty.business.metrics[5].valuePercent, null);
  assert.equal(confirmedEmpty.business.metrics[0].change, null);
  assert.equal(confirmedEmpty.business.metrics[0].comparisonStatus, "unavailable");
  assert.equal(confirmedEmpty.business.metrics[0].yearOverYearChange, null);
  assert.equal(confirmedEmpty.taskFlow.length, 5);
  assert.deepEqual(confirmedEmpty.taskFlow.map((item) => item.total), [0, null, null, null, null]);
  assert.deepEqual(confirmedEmpty.timings, []);
  assert.equal(confirmedEmpty.summary.lastRefreshed, "2026-08-07T00:00:00Z");
});

test("missing signed Relay stage, route, and account-pool evidence remains unavailable", () => {
  const raw = liveResponseFixture();
  raw.taskOps.relay_stage_source_status = "unavailable";
  raw.taskOps.relay_stage_task_counts = {
    queued: null,
    provider_processing: null,
    submission_unknown: null,
    failed: null,
    cancelled: null,
  };
  raw.taskOps.artifact_pipeline = { source_status: "unavailable", transferring_task_count: null };
  raw.taskOps.relay_callbacks = { source_status: "unavailable", terminal_event_count: null };
  raw.channelHealth.account_pool_metrics = {
    active_account_count: null,
    cooling_account_count: null,
    invalid_account_count: null,
    rate_limit_count: null,
    failover_count: null,
    pending_alert_count: null,
  };
  raw.channelHealth.channels[0] = {
    channel_key: "official-a",
    channel_type: "official",
    source_status: "unavailable",
    route_count: null,
    observed_success_rate: null,
    provider_cost_data_status: "unavailable",
    routes: [],
  };

  const data = adaptAdminOperationsData(raw);
  assert.deepEqual(data.taskFlow.map((item) => item.total), [17, null, null, null, null]);
  assert.equal(data.taskFlow[0].dropoff, null);
  assert.equal(data.taskFlow[2].dropoff, null);
  assert.equal(data.reliability[0].calls, null);
  assert.equal(data.reliability[0].successRate, null);
  assert.equal(data.reliability[0].p95, null);
  assert.equal(data.reliability[0].rateLimited, null);
  assert.equal(data.reliability[0].failoverCount, null);
  assert.equal(data.channels[0].activeAccounts, "—");
  assert.equal(data.channels[0].rateLimits, "—");
  assert.equal(data.summary.alertBacklog, null);
});

test("metric comparison UI renders null as unavailable rather than a fabricated zero", () => {
  assert.match(operationsSource, /value == null/);
  assert.match(operationsSource, /暂无对比/);
  assert.doesNotMatch(operationsSource, /Math\.abs\(item\.change \|\| 0\)/);
});
