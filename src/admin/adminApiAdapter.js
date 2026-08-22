import { buildEntitlementKey } from "./adminConsoleUtils.js";
import { adaptRelayUnknownPage } from "./relayUnknownOperations.js";
import { adaptRelayCallbackDeadLetterPage } from "./relayCallbackDeadLetters.js";

const DAY_MS = 24 * 60 * 60 * 1000;

const ALERT_LABELS = {
  LOW_BALANCE: "余额不足",
  STALE_RESERVED_BALANCE: "预留余额长期未释放",
  INACTIVE_COMPANY: "长期未活跃",
  HIGH_FAILURE_RATE: "失败率过高",
  ABNORMAL_SPEND: "消费异常增长",
  ENTITLEMENT_EXPIRED: "权益已过期",
  ENTITLEMENT_EXPIRING: "权益即将到期",
};

const EXCEPTION_LABELS = {
  PUBLICATION_PENDING_APPROVAL: "发布任务待审批",
  PUBLICATION_FAILED: "发布失败待处理",
  PUBLICATION_SUBMISSION_UNKNOWN: "发布结果未知待核对",
  PUBLISHER_REAUTH_REQUIRED: "发布账号需要重新授权",
  PUBLISHER_OAUTH_EXPIRING: "OAuth 即将过期",
  RELAY_SUBMISSION_UNKNOWN: "Relay 提交结果未知",
  RELAY_SUBMISSION_FAILED: "Relay 提交失败",
  ARTIFACT_STORAGE_TRANSFER_FAILED: "OBS 产物转存失败",
  DOWNLOAD_REGISTRATION_UNKNOWN: "下载登记结果未知",
  DOWNLOAD_REGISTRATION_FAILED: "下载登记失败",
  RELAY_MODEL_UNMAPPED: "Relay 模型未映射",
};

const EXCEPTION_KINDS = {
  PUBLICATION_PENDING_APPROVAL: "approval",
  PUBLICATION_FAILED: "publish_failed",
  PUBLICATION_SUBMISSION_UNKNOWN: "submission_unknown",
  PUBLISHER_REAUTH_REQUIRED: "oauth_expiry",
  PUBLISHER_OAUTH_EXPIRING: "oauth_expiry",
  RELAY_SUBMISSION_UNKNOWN: "relay_submission_unknown",
  RELAY_SUBMISSION_FAILED: "relay_submission_failed",
  ARTIFACT_STORAGE_TRANSFER_FAILED: "obs_transfer",
  DOWNLOAD_REGISTRATION_UNKNOWN: "download_registration",
  DOWNLOAD_REGISTRATION_FAILED: "download_registration",
  RELAY_MODEL_UNMAPPED: "relay_unmapped",
};

const MANUAL_EXCEPTION_GUIDANCE = {
  PUBLICATION_SUBMISSION_UNKNOWN: "先到发布平台控制台核对是否已产生外部帖子。确认未发布后，再由有权限的管理员选择明确核销结果；系统不会自动重试。",
  RELAY_SUBMISSION_UNKNOWN: "保留原渠道和账号路由，按中转站对账流程确认提交结果；禁止跨渠道重试。",
  DOWNLOAD_REGISTRATION_UNKNOWN: "重新查询下载登记服务的持久化结果；只有可信完成回执才能把产物标记为已下载。",
  DOWNLOAD_REGISTRATION_FAILED: "安全重试下载登记服务；按钮点击和签发下载地址本身都不代表下载完成。",
  ARTIFACT_STORAGE_TRANSFER_FAILED: "检查 OBS 转存记录与校验结果，临时渠道 URL 不能作为长期产物地址。",
};

const PERMISSION_GROUPS = {
  analytics: "经营分析",
  companies: "企业管理",
  entitlements: "企业权益",
  models: "模型目录",
  resources: "资源目录",
  finance: "财务",
  provider_costs: "渠道成本",
  publishing_exceptions: "发布异常",
  asset_exceptions: "资产异常",
  audit: "安全审计",
  relay_health: "Relay 健康",
  admin_access: "管理员权限",
};

function number(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function nullableNumber(value) {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function nullablePercent(value) {
  const parsed = nullableNumber(value);
  return parsed == null ? null : parsed * 100;
}

function nullableSum(values) {
  const parsed = values.map(nullableNumber);
  return parsed.some((value) => value == null)
    ? null
    : parsed.reduce((sum, value) => sum + value, 0);
}

function percent(value) {
  return value == null ? 0 : number(value) * 100;
}

function comparisonMetric(operating, comparisonName, metricName) {
  const comparison = operating?.comparisons?.[comparisonName];
  const metric = comparison?.metrics?.[metricName];
  const status = comparison?.status || "unavailable";
  return {
    status,
    current: metric?.current ?? null,
    baseline: status === "unavailable" ? null : metric?.baseline ?? null,
    absoluteChange: status === "unavailable" ? null : metric?.absolute_change ?? null,
    changeRate: status === "unavailable" || metric?.change_rate == null
      ? null
      : percent(metric.change_rate),
  };
}

function metricComparisons(operating, metricName) {
  const periodOverPeriod = comparisonMetric(operating, "period_over_period", metricName);
  const yearOverYear = comparisonMetric(operating, "year_over_year", metricName);
  return {
    change: periodOverPeriod.changeRate,
    comparisonStatus: periodOverPeriod.status,
    yearOverYearChange: yearOverYear.changeRate,
    yearOverYearStatus: yearOverYear.status,
    comparisons: { periodOverPeriod, yearOverYear },
  };
}

function dateLabel(value) {
  if (!value) return "—";
  return String(value).slice(5, 10);
}

function priorityFor(severity) {
  if (severity === "critical") return "P1";
  if (severity === "warning") return "P2";
  return "P3";
}

function normalizeExceptionStatus(value) {
  if (["investigating", "processing", "retry"].includes(value)) return "investigating";
  if (["resolved", "succeeded", "completed"].includes(value)) return "resolved";
  return "open";
}

function exceptionItem(item) {
  const category = item.category || item.kind || "UNKNOWN_EXCEPTION";
  const company = item.company_name ? `${item.company_name} · ` : "";
  const code = item.error_code ? ` · ${item.error_code}` : "";
  return {
    id: `${category}:${item.target_id || item.id}`,
    category,
    priority: priorityFor(item.severity),
    category,
    kind: EXCEPTION_KINDS[category] || category.toLocaleLowerCase("en-US"),
    title: EXCEPTION_LABELS[category] || category,
    description: `${company}${item.target_type || "平台异常"}${code}`,
    nextAction: item.next_action || item.recommended_action || null,
    owner: item.owner || item.owner_label || null,
    occurredAt: item.occurred_at || item.occurredAt,
    status: normalizeExceptionStatus(item.status),
    companyId: item.company_id,
    targetType: item.target_type,
    targetId: item.target_id,
    errorCode: item.error_code,
    nextAction: MANUAL_EXCEPTION_GUIDANCE[category] || item.next_action,
    raw: item,
  };
}

function localDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function riskFromCompany(row) {
  if (row.company_status !== "active") return "inactive";
  if ((row.alerts || []).some((alert) => alert.severity === "critical")) return "critical";
  if ((row.alerts || []).some((alert) => alert.severity === "warning")) return "warning";
  return "healthy";
}

function entitlementState(cell) {
  if (cell.state !== "enabled" || !cell.expires_at) return cell.state;
  const expiresAt = new Date(cell.expires_at).getTime();
  return Number.isFinite(expiresAt) && expiresAt - Date.now() <= 14 * DAY_MS
    ? "expiring"
    : cell.state;
}

export function adminRangeWindow(range, now = new Date()) {
  if (range === "month") {
    return {
      start_time: new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString(),
      end_time: now.toISOString(),
      granularity: "day",
    };
  }
  const durationDays = range === "30d" ? 30 : range === "7d" ? 7 : 1;
  return {
    start_time: new Date(now.getTime() - durationDays * DAY_MS).toISOString(),
    end_time: now.toISOString(),
    granularity: "day",
  };
}

export function visibleAdminSections(me) {
  // An absent identity is not an owner identity. Keeping this fail-closed is
  // important while the live console is still loading (or if /me fails).
  if (!me) return [];
  if (me.is_platform_owner) return undefined;
  const permissions = new Set(me.permission_codes || []);
  const hasAll = (...codes) => codes.every((code) => permissions.has(code));
  const sections = [];
  if (hasAll("platform.analytics.read", "platform.finance.read", "platform.provider_costs.read")) sections.push("cockpit");
  if (hasAll("platform.analytics.read")) sections.push("task-operations");
  if (hasAll("platform.analytics.read", "platform.finance.read", "platform.provider_costs.read")) sections.push("model-profit");
  if (hasAll("platform.analytics.read", "platform.finance.read")) sections.push("company-health");
  if (hasAll("platform.entitlements.read")) sections.push("entitlements");
  if (hasAll("platform.relay_health.read")) sections.push("channels");
  if (hasAll("platform.publishing_exceptions.read", "platform.asset_exceptions.read", "platform.relay_health.read")) sections.push("publishing-assets");
  if (permissions.has("platform.audit.read") || permissions.has("platform.admin_access.read")) sections.push("access-audit");
  return sections;
}

const RELAY_CHANNEL_STATUSES = new Set([
  "enabled",
  "manually_disabled",
  "auto_disabled",
]);

export function adaptRelayControlChannel(item = {}) {
  const id = Number(item.id);
  if (!Number.isInteger(id) || id <= 0) return null;
  const configuredModels = Array.isArray(item.configured_models)
    ? item.configured_models.filter((model) => typeof model === "string" && model.trim())
    : [];
  const responseTimeMs = nullableNumber(item.response_time_ms);
  return {
    id,
    name: String(item.name || `Relay 渠道 ${id}`),
    type: Number.isInteger(Number(item.type)) ? Number(item.type) : null,
    typeLabel: String(item.type_label || "").trim(),
    status: RELAY_CHANNEL_STATUSES.has(item.status) ? item.status : "unavailable",
    configuredModels,
    modelCount: configuredModels.length,
    testModel: typeof item.test_model === "string" ? item.test_model : null,
    testSupported: item.test_supported === true,
    weight: nullableNumber(item.weight),
    priority: nullableNumber(item.priority),
    autoBan: item.auto_ban === true,
    tag: typeof item.tag === "string" ? item.tag : null,
    createdAt: item.created_at || null,
    lastTestedAt: item.last_tested_at || null,
    responseTimeMs: responseTimeMs != null && responseTimeMs >= 0
      ? responseTimeMs
      : null,
    credentialConfigured: item.credential?.configured === true,
    revision: typeof item.revision === "string" ? item.revision : "",
  };
}

export function adaptRelayControlChannelPage(page) {
  if (!page || !Array.isArray(page.data)) {
    return { items: [], total: null, sourceStatus: "unavailable" };
  }
  return {
    items: page.data.map(adaptRelayControlChannel).filter(Boolean),
    total: Number.isInteger(Number(page.total)) ? Number(page.total) : null,
    sourceStatus: "available",
  };
}

export function adaptAdminOperationsData(raw = {}) {
  const hasOperating = Boolean(raw.operating && typeof raw.operating === "object");
  const hasTaskOps = Boolean(raw.taskOps && typeof raw.taskOps === "object");
  const operating = raw.operating || {};
  const taskOps = raw.taskOps || {};
  const profitability = raw.profitability?.items || [];
  const companyHealthRows = raw.companyHealth?.items || [];
  const channelRows = raw.channelHealth?.channels || [];
  const relayControlPage = adaptRelayControlChannelPage(raw.relayChannels);
  const relayUnknownPage = adaptRelayUnknownPage(raw.relayUnknownSubmissions);
  const relayCallbackDeadLetters = adaptRelayCallbackDeadLetterPage(raw.relayCallbackDeadLetters);
  const exceptionRows = (raw.exceptions?.items || []).map(exceptionItem);
  const relayUnmapped = [
    ...(raw.relayModels?.items || []).filter((item) => (
      item.status === "unmapped" || item.reconciliation_status === "unmapped"
    )),
    ...(raw.relayModels?.platform_only_model_ids || []).map((id) => ({ model_id: id })),
  ].map((item) => exceptionItem({
    category: "RELAY_MODEL_UNMAPPED",
    severity: "warning",
    target_type: "model_definition",
    target_id: item.model_id || item.id || item.slug,
    status: "open",
    occurred_at: raw.loadedAt,
  }));
  const allExceptions = [...relayUnmapped, ...exceptionRows];
  const publishingExceptions = allExceptions.filter((item) => item.category.startsWith("PUBLICATION_") || item.category.startsWith("PUBLISHER_"));
  const assetExceptions = allExceptions.filter((item) => item.category.startsWith("ARTIFACT_") || item.category.startsWith("DOWNLOAD_"));
  const operationalExceptions = allExceptions.filter((item) => !publishingExceptions.includes(item) && !assetExceptions.includes(item));
  const latency = taskOps.latency_seconds || {};
  const taskTrendPoints = hasTaskOps ? taskOps.trend_points || [] : [];
  const overallFailureCounts = new Map(
    (taskOps.failure_reasons || []).map((item) => [item.error_code, number(item.count)]),
  );
  const failureCodes = [...new Set([
    ...overallFailureCounts.keys(),
    ...taskTrendPoints.flatMap((point) => (point.failure_reasons || []).map((item) => item.error_code)),
  ])];
  const trendFailureCount = (point, code) => number(
    (point?.failure_reasons || []).find((item) => item.error_code === code)?.count,
  );
  const failureCount = (code) => overallFailureCounts.has(code)
    ? overallFailureCounts.get(code)
    : taskTrendPoints.reduce((sum, point) => sum + trendFailureCount(point, code), 0);
  const failureTotal = failureCodes.reduce((sum, code) => sum + failureCount(code), 0);
  const operatingTotals = operating.totals || {};
  const costIncomplete = number(operatingTotals.cost_missing_task_count) > 0;
  const grossProfit = number(operatingTotals.known_gross_profit_cents);
  const grossMargin = operatingTotals.gross_margin == null
    ? (number(operatingTotals.settled_revenue_cents) ? grossProfit / number(operatingTotals.settled_revenue_cents) : 0)
    : number(operatingTotals.gross_margin);

  const matrix = raw.matrix || {};
  const entitlementProducts = (matrix.columns || []).map((column) => ({
    id: column.item_id,
    name: column.display_name,
    key: column.catalog_key,
    kind: column.item_kind === "model" ? "model" : column.resource_kind,
    billingMode: column.billing_mode,
    status: column.catalog_active ? "active" : "retired",
    lifecycle: column.lifecycle,
  }));
  const companies = (matrix.rows || []).map((row) => ({
    id: row.company_id,
    name: row.company_name,
    status: row.company_status,
    plan: "独立合同",
  }));
  const entitlementGrants = {};
  for (const row of matrix.rows || []) {
    for (const cell of row.cells || []) {
      if (!cell.configured && cell.state === "unconfigured") continue;
      const product = entitlementProducts.find((item) => item.id === cell.item_id);
      const price = product?.billingMode === "per_second"
        ? cell.price_per_second_cents
        : cell.price_per_item_cents;
      entitlementGrants[buildEntitlementKey(row.company_id, cell.item_id)] = {
        companyId: row.company_id,
        productId: cell.item_id,
        state: entitlementState(cell),
        serverState: cell.state,
        enabled: cell.enabled,
        priceCents: price,
        quota: cell.call_quota,
        concurrency: cell.concurrency_limit,
        effectiveAt: localDateTime(cell.effective_at),
        expiresAt: localDateTime(cell.expires_at),
        capabilityLimit: Object.keys(cell.config_override || {}).length ? JSON.stringify(cell.config_override, null, 2) : "",
      };
    }
  }

  const dashboardCompanies = raw.dashboard?.companies || [];
  const dashboardCompanyById = new Map(dashboardCompanies.map((item) => [item.company_id, item]));
  const businessTrend = (operating.points || []).map((point) => ({
    date: dateLabel(point.bucket_start),
    // The chart contract is yuan while every backend ledger field is integer
    // cents. Keeping the conversion here prevents a 100x visual overstatement.
    recharge: number(point.recharge_cents) / 100,
    revenue: number(point.settled_revenue_cents) / 100,
    cost: number(point.provider_cost_cents) / 100,
    grossProfit: number(point.known_gross_profit_cents) / 100,
  }));

  const relayStageCounts = taskOps.relay_stage_task_counts || {};
  const relayStageAvailable = taskOps.relay_stage_source_status === "available";
  const artifactPipeline = taskOps.artifact_pipeline || {};
  const artifactPipelineAvailable = artifactPipeline.source_status === "available";
  const relayCallbacks = taskOps.relay_callbacks || {};
  const relayCallbacksAvailable = relayCallbacks.source_status === "available";
  const platformSubmittedCount = nullableNumber(taskOps.total_task_count);
  const taskFlowRate = (value) => {
    if (value == null || platformSubmittedCount == null) return null;
    return platformSubmittedCount > 0 ? (value / platformSubmittedCount) * 100 : 0;
  };
  const failedStageCount = relayStageAvailable
    ? nullableSum([relayStageCounts.failed, relayStageCounts.cancelled])
    : null;
  const timeoutCount = nullableNumber(taskOps.timeout_count);
  const submissionUnknownStageCount = relayStageAvailable
    ? nullableNumber(relayStageCounts.submission_unknown)
    : null;
  const queuedStageCount = relayStageAvailable
    ? nullableNumber(relayStageCounts.queued)
    : null;
  const processingStageCount = relayStageAvailable
    ? nullableNumber(relayStageCounts.provider_processing)
    : null;
  const transferringStageCount = artifactPipelineAvailable
    ? nullableNumber(artifactPipeline.transferring_task_count)
    : null;
  const callbackTerminalCount = relayCallbacksAvailable
    ? nullableNumber(relayCallbacks.terminal_event_count)
    : null;

  const taskFlow = hasTaskOps ? [
    { key: "submitted", label: "平台提交", total: platformSubmittedCount, rate: taskFlowRate(platformSubmittedCount), sourceStatus: platformSubmittedCount == null ? "unavailable" : "available", dropoffLabel: "失败 / 取消", dropoff: failedStageCount, dropoffRate: taskFlowRate(failedStageCount) },
    { key: "queued", label: "Relay 排队", total: queuedStageCount, rate: taskFlowRate(queuedStageCount), sourceStatus: taskOps.relay_stage_source_status || "unavailable", dropoffLabel: "平台超时", dropoff: timeoutCount, dropoffRate: taskFlowRate(timeoutCount) },
    { key: "generating", label: "渠道生成中", total: processingStageCount, rate: taskFlowRate(processingStageCount), sourceStatus: taskOps.relay_stage_source_status || "unavailable", dropoffLabel: "提交结果未知", dropoff: submissionUnknownStageCount, dropoffRate: taskFlowRate(submissionUnknownStageCount) },
    { key: "transferring", label: "产物转存", total: transferringStageCount, rate: taskFlowRate(transferringStageCount), sourceStatus: artifactPipeline.source_status || "unavailable" },
    { key: "reconciled", label: "终态回调", total: callbackTerminalCount, rate: taskFlowRate(callbackTerminalCount), sourceStatus: relayCallbacks.source_status || "unavailable" },
  ] : [];
  const timings = hasTaskOps ? [
    { key: "terminal", label: "任务终态耗时", p50: latency.terminal_p50, p95: latency.terminal_p95 },
    { key: "succeeded", label: "成功任务耗时", p50: latency.succeeded_p50, p95: latency.succeeded_p95 },
    { key: "failed", label: "失败任务耗时", p50: latency.failed_p50, p95: latency.failed_p95 },
  ].filter((item) => item.p50 != null || item.p95 != null) : [];
  const trends = taskTrendPoints.map((point) => {
    const counts = point.status_counts || {};
    return {
      time: dateLabel(point.bucket_start),
      submitted: number(point.total_task_count),
      queued: number(counts.draft) + number(counts.queued),
      generating: number(counts.processing),
      completed: number(counts.succeeded),
      failed: number(counts.failed) + number(counts.cancelled),
      timeout: number(point.timeout_count),
      submissionUnknown: number(point.submission_unknown_count),
      successRate: point.success_rate == null ? null : percent(point.success_rate),
      latencyP50: point.terminal_latency_p50_seconds ?? null,
      latencyP95: point.terminal_latency_p95_seconds ?? null,
    };
  });
  const failureTrends = failureCodes.length ? taskTrendPoints.map((point) => ({
    time: dateLabel(point.bucket_start),
    ...Object.fromEntries(failureCodes.map((code) => [code, trendFailureCount(point, code)])),
  })) : [];
  const failureReasons = failureCodes.map((code) => {
    const count = failureCount(code);
    const previous = taskTrendPoints.length > 1
      ? trendFailureCount(taskTrendPoints.at(-2), code)
      : null;
    const current = taskTrendPoints.length
      ? trendFailureCount(taskTrendPoints.at(-1), code)
      : null;
    return {
      key: code,
      label: code,
      count,
      share: failureTotal ? count / failureTotal * 100 : 0,
      change: previous == null || current == null || previous === 0
        ? null
        : (current - previous) / previous * 100,
    };
  });
  const latencyDistributionSource = taskOps.terminal_latency_distribution_seconds || {};
  const latencySampleCount = number(latencyDistributionSource.sample_count);
  let cumulativeLatencyCount = 0;
  const latencyDistribution = latencySampleCount > 0
    ? (latencyDistributionSource.bins || []).map((bin) => {
      const count = number(bin.count);
      cumulativeLatencyCount += count;
      return {
        range: bin.range,
        count,
        share: count / latencySampleCount * 100,
        cumulative: Math.min(100, cumulativeLatencyCount / latencySampleCount * 100),
      };
    })
    : [];

  const accountPoolMetrics = raw.channelHealth?.account_pool_metrics || null;
  const globalFailoverCount = nullableNumber(accountPoolMetrics?.failover_count);
  const reliability = channelRows.flatMap((channel) => {
    const routes = Array.isArray(channel.routes) ? channel.routes : [];
    const routeRows = routes.length ? routes : [null];
    return routeRows.map((route, index) => {
      const successfulCalls = nullableNumber(route?.successful_task_count);
      const failedCalls = nullableNumber(route?.failed_task_count);
      const calls = route ? nullableSum([successfulCalls, failedCalls]) : null;
      const healthStatus = route?.health_status;
      const status = channel.source_status !== "available" || !route
        ? "warning"
        : ["failed", "invalidated"].includes(healthStatus)
          ? "critical"
          : healthStatus === "healthy"
            ? "healthy"
            : "warning";
      return {
        id: `${channel.channel_key}:${route?.route_id ?? `unavailable-${index}`}`,
        model: route?.model || "—",
        mode: route?.mode || null,
        channel: route?.provider_name || channel.channel_key,
        channelKey: channel.channel_key,
        channelClass: route?.channel_type || channel.channel_type || "—",
        calls,
        successRate: route?.observed_success_rate == null
          ? null
          : nullablePercent(route.observed_success_rate),
        p95: route?.latency_p95_ms == null
          ? null
          : nullableNumber(route.latency_p95_ms) / 1000,
        rateLimited: nullableNumber(route?.rate_limited_account_count),
        failoverCount: globalFailoverCount,
        failoverScope: globalFailoverCount == null ? null : "account_pool",
        costDataStatus: channel.provider_cost_data_status || "unavailable",
        providerCostCents: nullableNumber(channel.provider_cost_cents),
        evidenceStatus: channel.source_status || "unavailable",
        healthStatus: healthStatus || "unavailable",
        status,
      };
    });
  });

  const channelOperations = channelRows.map((item) => {
    const routes = Array.isArray(item.routes) ? item.routes : [];
    const routeMetricsAvailable = item.source_status === "available" && routes.length > 0;
    const sumRouteMetric = (key) => routeMetricsAvailable
      ? nullableSum(routes.map((route) => route[key]))
      : null;
    const routeStates = routes.map((route) => route.health_status);
    return {
      id: item.channel_key,
      name: item.channel_key,
      channelClass: item.channel_type,
      successRate: item.observed_success_rate == null
        ? null
        : nullablePercent(item.observed_success_rate),
      // A signed route is not a unique account: one account may expose several
      // model/mode routes. Per-channel account cardinality is therefore unknown
      // until the backend provides a deduplicated account identifier/count.
      activeAccounts: "—",
      coolingAccounts: sumRouteMetric("cooling_account_count") ?? "—",
      invalidAccounts: sumRouteMetric("invalid_account_count") ?? "—",
      rateLimits: sumRouteMetric("rate_limited_account_count") ?? "—",
      failovers: globalFailoverCount ?? "—",
      alertBacklog: nullableNumber(accountPoolMetrics?.pending_alert_count) ?? "—",
      status: !routeMetricsAvailable
        ? "warning"
        : routeStates.some((value) => ["failed", "invalidated"].includes(value))
          ? "critical"
          : routeStates.every((value) => value === "healthy")
            ? "healthy"
            : "warning",
      evidenceStatus: item.source_status || raw.channelHealth?.relay_control_plane_data_status || "unavailable",
      routeCount: nullableNumber(item.route_count),
    };
  });

  return {
    sourceStatus: { ...(raw.sourceStatuses || {}) },
    sourceErrors: { ...(raw.sourceErrors || {}) },
    summary: {
      pending: allExceptions.filter((item) => item.status !== "resolved").length,
      alertBacklog: nullableNumber(accountPoolMetrics?.pending_alert_count),
      unreconciledCosts: number(operatingTotals.cost_missing_task_count),
      lastRefreshed: raw.loadedAt || raw.exceptions?.generated_at || raw.operating?.end_time || raw.matrix?.generated_at || null,
      environment: raw.environment || "development",
    },
    dataReadiness: raw.readiness && typeof raw.readiness === "object"
      ? {
        generatedAt: raw.readiness.generated_at || null,
        productionDataReady: raw.readiness.production_data_ready === true,
        productionDataReadyKnown: typeof raw.readiness.production_data_ready === "boolean",
        blockingSources: raw.readiness.blocking_sources || {},
        sources: raw.readiness.sources || {},
      }
      : null,
    taskFlow,
    timings,
    trends,
    failureTrends,
    failureReasons,
    latencyDistribution,
    exceptions: operationalExceptions,
    reliability,
    business: {
      metrics: hasOperating ? [
        { key: "recharge", label: "充值现金流", valueCents: number(operatingTotals.recharge_cents), ...metricComparisons(operating, "recharge_cents") },
        { key: "revenue", label: "结算收入", valueCents: number(operatingTotals.settled_revenue_cents), ...metricComparisons(operating, "settled_revenue_cents") },
        { key: "cost", label: "渠道成本", valueCents: number(operatingTotals.provider_cost_cents), ...metricComparisons(operating, "provider_cost_cents") },
        { key: "grossProfit", label: costIncomplete ? "已知毛利（成本未完整）" : "毛利", valueCents: grossProfit, ...metricComparisons(operating, "known_gross_profit_cents") },
        { key: "grossMargin", label: costIncomplete ? "已知毛利率" : "毛利率", valuePercent: grossMargin * 100, ...metricComparisons(operating, "gross_margin") },
      ] : [],
      trend: hasOperating ? businessTrend : [],
      companyRanking: dashboardCompanies.map((item) => ({
        id: item.company_id,
        name: item.company_name,
        revenueCents: number(item.consumption_cents),
        taskCount: number(item.task_count),
        successRate: number(item.task_count) ? number(item.succeeded_count) / number(item.task_count) * 100 : 0,
        balanceCents: number(item.available_cents),
      })),
    },
    modelProfitability: profitability.map((item) => ({
      id: item.model_id,
      model: item.display_name,
      calls: number(item.task_count),
      revenueCents: number(item.settled_revenue_cents),
      costCents: number(item.provider_cost_cents),
      grossProfitCents: number(item.known_gross_profit_cents),
      grossMargin: (item.gross_margin == null ? (number(item.settled_revenue_cents) ? number(item.known_gross_profit_cents) / number(item.settled_revenue_cents) : 0) : number(item.gross_margin)) * 100,
      successRate: percent(item.success_rate),
      avgSeconds: number(item.average_terminal_latency_seconds),
      missingCostRate: number(item.task_count) ? number(item.cost_missing_task_count) / number(item.task_count) * 100 : 0,
    })),
    companyHealth: companyHealthRows.map((item) => {
      const abnormal = (item.alerts || []).find((alert) => alert.code === "ABNORMAL_SPEND");
      const reservation = (item.alerts || []).find((alert) => alert.code === "STALE_RESERVED_BALANCE");
      const expiry = (item.alerts || []).filter((alert) => alert.code === "ENTITLEMENT_EXPIRING" || alert.code === "ENTITLEMENT_EXPIRED").reduce((sum, alert) => sum + number(alert.details?.count), 0);
      return {
        id: item.company_id,
        name: item.company_name,
        risk: riskFromCompany(item),
        balanceCents: number(item.available_cents),
        daysInactive: item.last_task_at ? Math.max(0, Math.floor((Date.now() - new Date(item.last_task_at).getTime()) / DAY_MS)) : null,
        consumptionChange: abnormal ? (number(abnormal.details?.ratio, 1) - 1) * 100 : 0,
        reservationAgeHours: reservation ? number(reservation.details?.threshold_hours) : 0,
        failureRate: percent(item.failure_rate_30d),
        entitlementsExpiring: expiry,
        reasons: (item.alerts || []).map((alert) => ALERT_LABELS[alert.code] || alert.code),
        dashboard: dashboardCompanyById.get(item.company_id),
      };
    }),
    channels: channelOperations,
    relayChannels: relayControlPage.items,
    relayChannelSourceStatus: relayControlPage.sourceStatus,
    relayChannelTotal: relayControlPage.total,
    relayUnknownSubmissions: relayUnknownPage.items,
    relayUnknownSubmissionSourceStatus: relayUnknownPage.sourceStatus,
    relayUnknownSubmissionPage: relayUnknownPage.page,
    relayUnknownSubmissionPageSize: relayUnknownPage.pageSize,
    relayUnknownSubmissionTotal: relayUnknownPage.total,
    relayCallbackDeadLetters: relayCallbackDeadLetters.items,
    relayCallbackDeadLetterSourceStatus: relayCallbackDeadLetters.sourceStatus,
    relayCallbackDeadLetterTotal: relayCallbackDeadLetters.total,
    channelSummary: accountPoolMetrics ? {
      active: accountPoolMetrics.active_account_count ?? "—",
      cooling: accountPoolMetrics.cooling_account_count ?? "—",
      invalid: accountPoolMetrics.invalid_account_count ?? "—",
      limits: accountPoolMetrics.rate_limit_count ?? "—",
      failovers: accountPoolMetrics.failover_count ?? "—",
      alerts: accountPoolMetrics.pending_alert_count ?? "—",
    } : null,
    publishingExceptions,
    assetExceptions,
    companies,
    entitlementProducts,
    entitlementTemplates: (raw.entitlementTemplates || matrix.templates || []).map((template) => ({
      id: template.id || template.key || template.name,
      name: template.display_name || template.name,
      version: Number(template.version || 1),
      mode: template.mode || "replace",
      cells: template.cells || [],
      productIds: (template.cells || []).map((cell) => cell.item_id),
    })),
    entitlementCoverage: (raw.coverage?.items || []).map((item) => ({
      id: item.item_id,
      name: item.display_name,
      kind: item.item_kind === "model" ? "model" : item.resource_kind,
      active: Boolean(item.catalog_active),
      totalCompanies: number(raw.coverage?.total_companies),
      configuredCompanies: number(item.configured_company_count),
      enabledCompanies: number(item.enabled_company_count),
      disabledCompanies: number(item.disabled_company_count),
      scheduledCompanies: number(item.scheduled_company_count),
      expiredCompanies: number(item.expired_company_count),
      coverageRate: item.coverage_rate == null ? null : percent(item.coverage_rate),
    })),
    entitlementGrants,
    auditEvents: (raw.audits?.items || []).map((item) => ({
      id: item.id,
      occurredAt: item.created_at,
      actorName: item.actor_display_name || item.actor_user_id,
      actorId: item.actor_user_id,
      actionLabel: item.action,
      action: item.action,
      targetLabel: `${item.target_type} · ${item.target_id}`,
      reason: item.after_summary?.change_reason || item.after_summary?.reason || "—",
      // Older audit responses do not expose an execution result. Preserve a
      // server value when present, otherwise describe the row as recorded
      // instead of silently presenting every entry as a successful action.
      result: item.result || item.outcome || item.status || "recorded",
      before: item.before_summary || {},
      after: item.after_summary || {},
      rollbackHint: item.action?.startsWith("channel.cost") ? "追加负数调整项，不删除原成本记录" : "根据变更前快照创建新的反向变更，并关联原审计 ID",
    })),
    adminPermissionCatalog: (raw.permissionCatalog || []).map((item) => ({
      key: item.code,
      label: item.description,
      group: PERMISSION_GROUPS[item.domain] || item.domain,
    })),
    platformAdmins: (raw.administrators || []).map((item) => ({
      id: item.user_id,
      name: item.display_name,
      email: item.email,
      roleLabel: item.access?.is_platform_owner ? "平台所有者" : "平台管理员",
      owner: Boolean(item.access?.is_platform_owner),
      status: item.status || item.account_status || "unknown",
      lastActiveAt: item.last_active_at || item.lastActiveAt || null,
      permissions: item.access?.is_platform_owner ? ["*"] : (item.access?.effective_permissions || []),
      access: item.access,
    })),
  };
}
