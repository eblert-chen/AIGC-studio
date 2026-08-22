import {
  ArrowClockwise,
  CalendarBlank,
  CaretDown,
  CaretLeft,
  CaretRight,
  CheckCircle,
  CloudArrowUp,
  Database,
  Hourglass,
  LockKey,
  Minus,
  PlayCircle,
  Pulse,
  Receipt,
  TrendDown,
  TrendUp,
  WarningCircle,
} from "@phosphor-icons/react";
import { formatDateTime, formatInteger } from "../adminConsoleUtils.js";

export const NAV_ITEMS = [
  { id: "cockpit", label: "经营总览" },
  { id: "task-operations", label: "任务运营" },
  { id: "model-profit", label: "模型利润" },
  { id: "company-health", label: "企业健康" },
  { id: "entitlements", label: "权益分发" },
  { id: "channels", label: "Relay 控制面" },
  { id: "publishing-assets", label: "发布与资产" },
  { id: "showcase", label: "首页内容" },
  { id: "access-audit", label: "权限与审计" },
];

export const ACCESS_AUDIT_TABS = [
  {
    id: "audit",
    label: "操作审计",
    icon: Receipt,
    tabId: "ops-access-audit-tab-audit",
    panelId: "ops-access-audit-panel-audit",
  },
  {
    id: "access",
    label: "平台管理员权限",
    icon: LockKey,
    tabId: "ops-access-audit-tab-access",
    panelId: "ops-access-audit-panel-access",
  },
];

const RANGE_OPTIONS = [
  { value: "24h", label: "最近 24 小时" },
  { value: "7d", label: "最近 7 天" },
  { value: "30d", label: "最近 30 天" },
  { value: "month", label: "本月" },
];

export const KIND_LABELS = {
  model: "模型",
  feature: "功能",
  agent: "智能体",
  external_api: "外部 API",
};

export const CHANNEL_CLASS_LABELS = {
  official: "官方渠道",
  third_party_api: "第三方 API",
  reverse: "逆向渠道",
};

export const GENERATION_MODE_LABELS = {
  text_to_image: "文生图",
  text_to_video: "文生视频",
  image_to_video: "图生视频",
  video_to_video: "视频生视频",
};

export const FAILURE_COLORS = {
  modelError: "var(--ops-chart-danger)",
  resourceShortage: "var(--ops-chart-blue)",
  timeout: "var(--ops-chart-cyan)",
  contentReview: "var(--ops-chart-warning)",
  transferFailure: "var(--ops-chart-info)",
  other: "var(--ops-chart-violet)",
};
export const FAILURE_PALETTE = Object.values(FAILURE_COLORS);

export const CHART_COLORS = {
  primary: "var(--ops-chart-primary)",
  blue: "var(--ops-chart-blue)",
  cyan: "var(--ops-chart-cyan)",
  red: "var(--ops-chart-danger)",
  orange: "var(--ops-chart-warning)",
  ink: "var(--ops-chart-ink)",
  muted: "var(--ops-chart-muted)",
};

export const SOURCE_KEYS = [
  "operating",
  "profitability",
  "readiness",
  "taskOps",
  "dashboard",
  "companyHealth",
  "relayChannels",
  "channelHealth",
  "relayUnknownSubmissions",
  "relayCallbackDeadLetters",
  "exceptions",
  "matrix",
  "coverage",
  "relayModels",
  "audits",
  "permissionCatalog",
  "administrators",
];

export const TIME_SCOPED_SECTIONS = new Set([
  "cockpit",
  "task-operations",
  "model-profit",
  "company-health",
]);

export function cloneData(value) {
  if (!value) return null;
  if (typeof structuredClone === "function") return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

export function cx(...values) {
  return values.filter(Boolean).join(" ");
}

export function compactIdentifier(value, head = 8, tail = 6) {
  const normalized = String(value || "");
  if (normalized.length <= head + tail + 1) return normalized || "—";
  return `${normalized.slice(0, head)}…${normalized.slice(-tail)}`;
}

export function evidenceCount(value, fallback = "—") {
  const normalized = Number(value);
  return Number.isFinite(normalized) && normalized >= 0
    ? formatInteger(normalized)
    : fallback;
}

export function createRelayChannelOperationId(kind) {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `relay-channel-${kind}-${suffix}`.slice(0, 128);
}

const MODEL_AXIS_ALIASES = {
  "MiniMax H3": "MiniMax",
  "Kling 2.1": "Kling",
  "Runway Gen-3": "Runway",
  "Stable Diffusion XL": "SDXL",
  "PixVerse V6": "PixVerse",
};

export function formatModelAxisTick(value) {
  const normalized = String(value || "");
  return MODEL_AXIS_ALIASES[normalized]
    || (normalized.length > 9 ? `${normalized.slice(0, 8)}…` : normalized);
}

export function changeIcon(value) {
  if (Number(value) > 0) return TrendUp;
  if (Number(value) < 0) return TrendDown;
  return Minus;
}

export function FlowIcon({ flowKey, size = 21 }) {
  const Icon = {
    submitted: CloudArrowUp,
    queued: Hourglass,
    generating: PlayCircle,
    transferring: Database,
    reconciled: CheckCircle,
  }[flowKey] || Pulse;
  return <Icon size={size} weight="regular" />;
}

export function EmptyState({ title = "暂无数据", detail = "当前筛选范围内没有可显示的记录。" }) {
  return (
    <div className="ops-empty" role="status">
      <Database size={24} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

export function DatasetState({ status = "unavailable", label, detail, onRetry, compact = false }) {
  if (status === "available") return null;
  const meta = {
    loading: {
      icon: ArrowClockwise,
      title: `${label}正在加载`,
      detail: detail || "正在读取服务端数据，请稍候。",
      tone: "loading",
    },
    failed: {
      icon: WarningCircle,
      title: `${label}加载失败`,
      detail: detail || "本区域不是零数据，请重试或检查服务状态。",
      tone: "failed",
    },
    unauthorized: {
      icon: LockKey,
      title: `无权读取${label}`,
      detail: detail || "当前管理员未获得该数据域的读取权限。",
      tone: "unauthorized",
    },
    unavailable: {
      icon: Database,
      title: `${label}不可用`,
      detail: detail || "服务端没有返回可核验数据，不能据此判断为零。",
      tone: "unavailable",
    },
  }[status] || {
    icon: Database,
    title: `${label}状态未知`,
    detail: detail || "无法确认当前数据状态。",
    tone: "unavailable",
  };
  const Icon = meta.icon;
  return (
    <div className={cx("ops-dataset-state", `is-${meta.tone}`, compact && "is-compact")} role={status === "failed" ? "alert" : "status"}>
      <Icon size={compact ? 18 : 22} className={status === "loading" ? "is-spinning" : ""} />
      <span><strong>{meta.title}</strong><small>{meta.detail}</small></span>
      {status === "failed" && onRetry ? <button className="ops-secondary-button" type="button" onClick={onRetry}><ArrowClockwise size={15} />重试</button> : null}
    </div>
  );
}

export function combineSourceStatuses(...values) {
  const statuses = values.filter(Boolean);
  if (statuses.length && statuses.every((value) => value === "available")) return "available";
  if (statuses.includes("failed")) return "failed";
  if (statuses.includes("loading")) return "loading";
  if (statuses.includes("unauthorized")) return "unauthorized";
  return "unavailable";
}

export function StatusPill({ value, label }) {
  return (
    <span className={cx("ops-status-pill", `is-${value || "unknown"}`)}>
      <span aria-hidden="true" />
      {label || value || "未知"}
    </span>
  );
}

export function Priority({ value }) {
  return (
    <span className={cx("ops-priority", `is-${String(value || "P3").toLowerCase()}`)}>
      <span aria-hidden="true" />
      {value || "P3"}
    </span>
  );
}

export function ChartTooltip({ active, payload, label, valueFormatter = formatInteger }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="ops-chart-tooltip">
      <strong>{label}</strong>
      {payload.map((entry) => (
        <span key={entry.dataKey}>
          <i style={{ backgroundColor: entry.color }} />
          {entry.name}: {valueFormatter(entry.value)}
        </span>
      ))}
    </div>
  );
}

export function ChartDataTable({
  caption = "图表数值",
  rows = [],
  columns = [],
  rowKey = (row, index) => row.id || row.time || row.date || index,
}) {
  if (!rows.length || !columns.length) return null;
  return (
    <details className="ops-chart-data">
      <summary>查看图表数值</summary>
      <TableScroller label={caption}>
        <table className="ops-table is-compact-data">
          <caption>{caption}</caption>
          <thead>
            <tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={rowKey(row, index)}>
                {columns.map((column) => (
                  <td key={column.key}>
                    {column.format ? column.format(row[column.key], row) : row[column.key] ?? "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroller>
    </details>
  );
}

export function PanelHeader({ title, detail, action, compact = false }) {
  return (
    <div className={cx("ops-panel-header", compact && "is-compact")}>
      <div>
        <h2>{title}</h2>
        {detail ? <p>{detail}</p> : null}
      </div>
      {action ? <div className="ops-panel-action">{action}</div> : null}
    </div>
  );
}

export function TableScroller({ children, className = "", hasActions = false, label = "数据表格" }) {
  return (
    <div
      className={cx("ops-table-wrap", hasActions && "has-sticky-actions", className)}
      role="region"
      aria-label={hasActions ? `${label}，操作列固定在右侧，可横向滚动查看完整字段` : label}
      tabIndex="0"
    >
      {hasActions ? <span className="ops-table-scroll-hint" aria-hidden="true"><CaretLeft size={13} />左右滑动查看字段<CaretRight size={13} /></span> : null}
      {children}
    </div>
  );
}

export function PageTitle({ title, detail, controls }) {
  return (
    <div className="ops-page-title">
      <div>
        <h1 id="ops-page-title">{title}</h1>
        {detail ? <p>{detail}</p> : null}
      </div>
      {controls ? <div className="ops-page-controls">{controls}</div> : null}
    </div>
  );
}

const ENVIRONMENT_LABELS = {
  production: "生产环境",
  staging: "预发布环境",
  development: "开发环境",
};

export function RangeControls({ range, environment, environmentOptions, lastRefreshed, onRange, onEnvironment, onRefresh, loading, showRange = true }) {
  const options = environmentOptions?.length
    ? environmentOptions
    : ["production", "staging", "development"];
  return (
    <div className={cx("ops-range-controls", showRange && "has-time-range")}>
      {showRange ? <label className="ops-select-control">
        <CalendarBlank size={16} />
        <span className="sr-only">时间范围</span>
        <select value={range} onChange={(event) => onRange(event.target.value)}>
          {RANGE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <CaretDown size={14} />
      </label> : null}
      {options.length > 1 ? (
        <label className="ops-select-control">
          <span className="sr-only">环境</span>
          <select value={environment} onChange={(event) => onEnvironment(event.target.value)}>
            {options.map((value) => <option value={value} key={value}>{ENVIRONMENT_LABELS[value] || value}</option>)}
          </select>
          <CaretDown size={14} />
        </label>
      ) : <span className="ops-environment-label">{ENVIRONMENT_LABELS[environment] || environment}</span>}
      <span className="ops-last-refresh">最后刷新：{formatDateTime(lastRefreshed)}</span>
      <button className="ops-icon-button" type="button" onClick={onRefresh} disabled={loading} aria-label="刷新数据">
        <ArrowClockwise size={17} className={loading ? "is-spinning" : ""} />
      </button>
    </div>
  );
}

export function PageStatus({ error, loading, toast }) {
  if (error) {
    return <div className="ops-message is-error" role="alert"><WarningCircle size={18} />{error}</div>;
  }
  if (toast) {
    return <div className="ops-message is-success" role="status"><CheckCircle size={18} />{toast}</div>;
  }
  if (loading) {
    return <div className="ops-message" role="status"><ArrowClockwise size={18} className="is-spinning" />正在更新平台数据…</div>;
  }
  return null;
}

const READINESS_SOURCE_LABELS = {
  relay_telemetry: "Relay 签名遥测",
  channel_costs: "渠道成本证据",
  task_stages: "Relay 任务阶段",
  relay_callbacks: "Relay 终态回调",
  publishing: "发布任务",
  artifact_and_download_evidence: "产物与下载证据",
  platform_db: "Platform 数据库",
};

export function DataReadinessStatus({ readiness }) {
  const known = Boolean(readiness?.productionDataReadyKnown);
  const ready = known && readiness.productionDataReady;
  const blockers = Object.entries(readiness?.blockingSources || {});
  const sourceCount = Object.keys(readiness?.sources || {}).length;
  const label = known
    ? `production_data_ready=${ready ? "true" : "false"}`
    : "production_data_ready=unavailable";
  const blockerText = blockers.length
    ? blockers.map(([source, status]) => `${READINESS_SOURCE_LABELS[source] || source}：${status}`).join("；")
    : ready
      ? sourceCount
        ? `服务端核验的 ${sourceCount} 个生产数据源均已通过。`
        : "服务端核验的生产数据源均已通过。"
      : "数据就绪接口未返回可核验的阻断源，当前按未就绪处理。";
  return (
    <section
      className={cx("ops-readiness-status", ready ? "is-ready" : "is-blocked")}
      role={ready ? "status" : "alert"}
      aria-label="生产数据就绪状态"
    >
      {ready ? <CheckCircle size={19} weight="fill" /> : <WarningCircle size={19} weight="fill" />}
      <div>
        <strong>{ready ? "生产数据已就绪" : "生产数据未就绪"}</strong>
        <code>{label}</code>
        <span>{blockerText}</span>
      </div>
      <small>{readiness?.generatedAt ? `核验时间 ${formatDateTime(readiness.generatedAt)}` : "未取得后端核验时间"}</small>
    </section>
  );
}
