import { useRef } from "react";
import {
  Alarm,
  ArrowClockwise,
  ArrowRight,
  ArrowSquareOut,
  Database,
  DownloadSimple,
  Funnel,
  Key,
  LinkBreak,
  LockKey,
  MagnifyingGlass,
  PlayCircle,
  Pulse,
  Queue,
  SealCheck,
  ShieldCheck,
  SlidersHorizontal,
  Timer,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  exceptionStatusLabel,
  formatDateTime,
  formatInteger,
  formatPercent,
  formatTime,
} from "../adminConsoleUtils.js";
import { deferRelayNativeConsoleGrantConsumption } from "../relayNativeConsole.js";
import {
  ACCESS_AUDIT_TABS,
  CHANNEL_CLASS_LABELS,
  combineSourceStatuses,
  compactIdentifier,
  cx,
  DatasetState,
  evidenceCount,
  EmptyState,
  GENERATION_MODE_LABELS,
  PanelHeader,
  Priority,
  StatusPill,
  TableScroller,
} from "./operationsShared.jsx";

export const RELAY_CHANNEL_STATUS_META = {
  enabled: { label: "已启用", tone: "healthy" },
  manually_disabled: { label: "手动停用", tone: "disabled" },
  auto_disabled: { label: "自动停用", tone: "critical" },
  unavailable: { label: "状态不可用", tone: "unknown" },
};

export function relayChannelTypeLabel(channel) {
  if (channel.typeLabel) return channel.typeLabel;
  return channel.type == null ? "类型未提供" : `Relay 类型 #${channel.type}`;
}

function relayTestFreshness(value) {
  if (!value) return { label: "无测试记录", detail: "新鲜度未知" };
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return { label: "时间无效", detail: "新鲜度未知" };
  const hours = Math.max(0, Math.floor((Date.now() - timestamp) / 3_600_000));
  if (hours < 1) return { label: "1 小时内", detail: formatDateTime(value) };
  if (hours < 24) return { label: `${hours} 小时前`, detail: formatDateTime(value) };
  return { label: `${Math.floor(hours / 24)} 天前`, detail: formatDateTime(value) };
}

export function ChannelOperationsScreen({
  data,
  onRelayChannelOpen,
  canManageRelayChannels,
  onRelayNativeConsoleAuthorize,
  onRelayNativeConsoleConsume,
  onRelayNativeConsoleDismiss,
  relayNativeConsoleGrant,
  relayNativeConsoleBusy,
  relayNativeConsoleDisabledReason,
  onRelayUnknownOpen,
  openingRelayUnknownId,
  onRelayCallbackDeadLetterOpen,
  openingRelayCallbackDeadLetterId,
  onRetry,
}) {
  const source = data.sourceStatus || {};
  const sumEvidence = (key) => {
    const values = data.channels
      .map((channel) => Number(channel[key]))
      .filter(Number.isFinite);
    return values.length ? values.reduce((total, value) => total + value, 0) : "—";
  };
  const derivedSummary = {
    active: sumEvidence("activeAccounts"),
    cooling: sumEvidence("coolingAccounts"),
    invalid: sumEvidence("invalidAccounts"),
    limits: sumEvidence("rateLimits"),
    failovers: sumEvidence("failovers"),
    alerts: sumEvidence("alertBacklog"),
  };
  const summary = data.channelSummary || derivedSummary;
  const relayControlStatus = source.relayChannels || data.relayChannelSourceStatus || "unavailable";
  const reconciliationStatus = source.relayUnknownSubmissions || data.relayUnknownSubmissionSourceStatus || "unavailable";
  const callbackStatus = source.relayCallbackDeadLetters || data.relayCallbackDeadLetterSourceStatus || "unavailable";
  const channelHealthStatus = source.channelHealth || "unavailable";
  const reconciliationAvailable = reconciliationStatus === "available" && data.relayUnknownSubmissionSourceStatus === "available";
  const relayControlAvailable = relayControlStatus === "available" && data.relayChannelSourceStatus === "available";
  const relayChannels = data.relayChannels;
  const relayControlSummary = {
    enabled: relayChannels.filter((item) => item.status === "enabled").length,
    manuallyDisabled: relayChannels.filter((item) => item.status === "manually_disabled").length,
    autoDisabled: relayChannels.filter((item) => item.status === "auto_disabled").length,
    tested: relayChannels.filter((item) => Boolean(item.lastTestedAt)).length,
    missingCredential: relayChannels.filter((item) => !item.credentialConfigured).length,
  };
  const unknownSubmissionCount = evidenceCount(data.relayUnknownSubmissionTotal, "待核验");
  const callbackDeadLetterCount = evidenceCount(data.relayCallbackDeadLetterTotal, "待核验");
  return (
    <>
      <section className="ops-panel ops-relay-control-panel">
        <PanelHeader
          title="Relay 渠道控制面"
          detail="通过 Platform 安全门面读取、测试和启停渠道。凭据、上游地址、请求头和原始错误不会进入浏览器。"
          action={<span className={cx("ops-queue-count", !relayControlAvailable && "is-unavailable")}>{relayControlAvailable ? `${data.relayChannelTotal ?? relayChannels.length} 个渠道` : "数据不可用"}</span>}
        />
        {relayControlStatus !== "available" ? <DatasetState status={relayControlStatus} label="Relay 渠道控制面" detail={data.sourceErrors?.relayChannels} onRetry={onRetry} compact /> : null}
        <div className="ops-native-console-entry" aria-labelledby="ops-native-console-title">
          <div className="ops-native-console-copy">
            <span className="ops-native-console-kicker"><WarningCircle size={15} />高风险运维</span>
            <div>
              <strong id="ops-native-console-title">new-api 原生控制台</strong>
              <p>仅用于新增渠道、密钥轮换和模型映射等尚未迁入 Platform 的操作；目标系统独立登录，并在新标签页打开。</p>
            </div>
          </div>
          <div className="ops-native-console-actions">
            <div className="ops-native-console-badges" aria-hidden="true"><span>独立登录</span><span>新标签页</span></div>
            {relayNativeConsoleGrant ? (
              <div className="ops-native-console-granted-actions">
                <a
                  className="ops-native-console-link"
                  href={relayNativeConsoleGrant.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  referrerPolicy="no-referrer"
                  onClick={() => deferRelayNativeConsoleGrantConsumption(
                    onRelayNativeConsoleConsume,
                  )}
                  aria-describedby="ops-native-console-status"
                >
                  <ArrowSquareOut size={16} />打开 new-api 原生控制台
                </a>
                <button className="ops-text-button" type="button" onClick={onRelayNativeConsoleDismiss}>取消</button>
              </div>
            ) : (
              <button
                className="ops-secondary-button ops-native-console-authorize"
                type="button"
                onClick={onRelayNativeConsoleAuthorize}
                disabled={!onRelayNativeConsoleAuthorize || relayNativeConsoleBusy || Boolean(relayNativeConsoleDisabledReason)}
                aria-describedby="ops-native-console-status"
                title={relayNativeConsoleDisabledReason || "经 Platform 授权后短时展示固定渠道入口"}
              >
                <ShieldCheck size={16} />{relayNativeConsoleBusy ? "授权中…" : "授权高风险运维"}
              </button>
            )}
            <small id="ops-native-console-status" aria-live="polite">
              {relayNativeConsoleGrant
                ? "入口已授权，但尚不能证明新标签页已打开或 new-api 已登录；临时入口将在 60 秒内自动清除。"
                : relayNativeConsoleDisabledReason || "Platform 只返回无凭据的固定渠道页地址；不会把登录令牌写入 URL 或浏览器存储。"}
            </small>
          </div>
        </div>
        <div className="ops-health-summary is-channel ops-relay-control-summary">
          <span><PlayCircle size={20} /><small>已启用</small><strong>{relayControlAvailable ? relayControlSummary.enabled : "—"}</strong></span>
          <span className="is-warning"><LockKey size={20} /><small>手动停用</small><strong>{relayControlAvailable ? relayControlSummary.manuallyDisabled : "—"}</strong></span>
          <span className="is-critical"><LinkBreak size={20} /><small>自动停用</small><strong>{relayControlAvailable ? relayControlSummary.autoDisabled : "—"}</strong></span>
          <span><Pulse size={20} /><small>有测试证据</small><strong>{relayControlAvailable ? relayControlSummary.tested : "—"}</strong></span>
          <span className={relayControlSummary.missingCredential ? "is-critical" : ""}><Key size={20} /><small>凭据未配置</small><strong>{relayControlAvailable ? relayControlSummary.missingCredential : "—"}</strong></span>
          <span className={Number(data.relayUnknownSubmissionTotal) > 0 ? "is-critical" : ""}><Alarm size={20} /><small>未知提交</small><strong>{reconciliationAvailable ? unknownSubmissionCount : "—"}</strong></span>
        </div>
        <TableScroller hasActions label="Relay 渠道控制面">
          <table className="ops-table ops-relay-channel-table">
            <thead><tr><th>渠道</th><th>适配类型</th><th>状态</th><th>测试证据 / 新鲜度</th><th>路由 / 模型</th><th>上次测试</th><th>延迟</th><th>操作</th></tr></thead>
            <tbody>
              {relayChannels.map((row) => {
                const state = RELAY_CHANNEL_STATUS_META[row.status] || RELAY_CHANNEL_STATUS_META.unavailable;
                const freshness = relayTestFreshness(row.lastTestedAt);
                const targetStatus = row.status === "enabled" ? "manually_disabled" : "enabled";
                return (
                  <tr key={row.id}>
                    <td><strong>{row.name}</strong><small>channel #{row.id}</small></td>
                    <td><strong>{relayChannelTypeLabel(row)}</strong><small>{row.tag || "无标签"} · {row.autoBan ? "自动禁用开启" : "自动禁用关闭"}</small></td>
                    <td><StatusPill value={state.tone} label={state.label} /></td>
                    <td><strong>{row.testSupported ? (row.lastTestedAt ? "已有测试证据" : "暂无测试证据") : "不支持通用测试"}</strong><small>{row.testSupported ? freshness.label : "需 staging 真实 canary"}</small></td>
                    <td><strong>路由证据未接入</strong><small>{evidenceCount(row.modelCount)} 个模型</small></td>
                    <td><strong>{formatDateTime(row.lastTestedAt)}</strong><small>{freshness.detail}</small></td>
                    <td>{row.responseTimeMs == null ? "—" : `${formatInteger(row.responseTimeMs)} ms`}</td>
                    <td>
                      <div className="ops-table-actions">
                        <button className="ops-table-link" type="button" onClick={() => onRelayChannelOpen?.(row, "detail")} disabled={!onRelayChannelOpen}>详情</button>
                        <button className="ops-table-link" type="button" onClick={() => onRelayChannelOpen?.(row, "test")} disabled={!onRelayChannelOpen || !canManageRelayChannels || !row.testSupported} title={row.testSupported ? "执行一次受审计的连通测试" : "该渠道不支持通用测试，需 staging 真实 canary"}>{row.testSupported ? "测试" : "需 canary"}</button>
                        <button className="ops-table-link" type="button" onClick={() => onRelayChannelOpen?.(row, "status", targetStatus)} disabled={!onRelayChannelOpen || !canManageRelayChannels || row.status === "unavailable"}>{canManageRelayChannels ? (targetStatus === "enabled" ? "启用" : "停用") : "只读"}</button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!relayChannels.length ? <tr><td colSpan="8"><EmptyState title={relayControlAvailable ? "当前没有 Relay 渠道" : "Relay 渠道控制面暂不可用"} detail={relayControlAvailable ? "Platform 已明确返回空渠道列表。" : "无法确认渠道是否为空，请检查页面错误并刷新。"} /></td></tr> : null}
            </tbody>
          </table>
        </TableScroller>
      </section>
      <section className="ops-panel ops-relay-callback-dlq-panel">
        <PanelHeader
          title="Callback 死信队列"
          detail="仅展示 Relay 已耗尽自动重试预算的签名回调。重新投递必须读取最新详情、记录审批证据，并复用原事件。"
          action={<span className={cx("ops-queue-count", callbackStatus !== "available" && "is-unavailable")}>{callbackStatus === "available" ? `${callbackDeadLetterCount} 项待处理` : callbackStatus === "failed" ? "加载失败" : "数据不可用"}</span>}
        />
        {callbackStatus !== "available" ? <DatasetState status={callbackStatus} label="Callback 死信队列" detail={data.sourceErrors?.relayCallbackDeadLetters} onRetry={onRetry} compact /> : null}
        <TableScroller hasActions label="Callback 死信队列">
          <table className="ops-table ops-relay-callback-dlq-table">
            <thead><tr><th>死信时间</th><th>事件 / 任务</th><th>尝试</th><th>最后响应</th><th>错误</th><th>操作</th></tr></thead>
            <tbody>
              {data.relayCallbackDeadLetters.map((item) => (
                <tr key={item.eventId}>
                  <td>{formatDateTime(item.deadLetteredAt)}</td>
                  <td><strong title={item.eventId}>{compactIdentifier(item.eventId)}</strong><small title={item.jobId}>任务 {compactIdentifier(item.jobId)}</small></td>
                  <td>{item.attempts} / {item.maxAttempts}</td>
                  <td>{item.responseStatus || "无 HTTP 响应"}</td>
                  <td className="is-negative"><strong>{item.lastError || "delivery_failed"}</strong><small>目的端摘要 {compactIdentifier(item.callbackUrlSha256, 10, 6)}</small></td>
                  <td><button className="ops-table-link" type="button" onClick={() => onRelayCallbackDeadLetterOpen?.(item)} disabled={!onRelayCallbackDeadLetterOpen || openingRelayCallbackDeadLetterId === item.eventId}>{openingRelayCallbackDeadLetterId === item.eventId ? "读取详情中…" : "核对并重新投递"}</button></td>
                </tr>
              ))}
              {!data.relayCallbackDeadLetters.length ? <tr><td colSpan="6"><EmptyState title={callbackStatus === "available" ? "当前没有 callback 死信" : "Callback 死信队列暂不可用"} detail="队列为空只在 Relay 明确返回空列表时成立。" /></td></tr> : null}
            </tbody>
          </table>
        </TableScroller>
      </section>
      <section className="ops-panel ops-callout is-warning">
        <WarningCircle size={22} />
        <div>
          <strong>未知提交只允许人工对账，严禁自动重试或跨渠道切换</strong>
          <span>每次处置都必须重新读取详情，在 Provider 控制台核实，并使用当前 route、attempt 与 token fencing 证明提交。</span>
        </div>
      </section>
      <section className="ops-panel ops-relay-reconciliation-panel">
        <PanelHeader
          title="未知提交人工对账"
          detail="列表来自 Relay 实时待对账资源；打开记录时会再次读取详情，不使用异常摘要直接处置。"
          action={<span className={cx("ops-queue-count", !reconciliationAvailable && "is-unavailable")}>{reconciliationAvailable ? `${unknownSubmissionCount} 项待核实` : "数据不可用"}</span>}
        />
        {reconciliationStatus !== "available" ? <DatasetState status={reconciliationStatus} label="未知提交人工对账" detail={data.sourceErrors?.relayUnknownSubmissions} onRetry={onRetry} compact /> : null}
        <TableScroller hasActions label="未知提交人工对账">
          <table className="ops-table ops-relay-reconciliation-table">
            <thead><tr><th>发现时间</th><th>任务</th><th>模型 / 模式</th><th>固定渠道</th><th>固定账号</th><th>Attempt</th><th>错误证据</th><th>操作</th></tr></thead>
            <tbody>
              {data.relayUnknownSubmissions.map((item) => (
                <tr key={item.jobId}>
                  <td>{formatDateTime(item.unknownAt)}</td>
                  <td><strong title={item.jobId}>{compactIdentifier(item.jobId)}</strong><small>{item.clientReferenceId || "无业务引用"}</small></td>
                  <td><strong>{item.model || "—"}</strong><small>{GENERATION_MODE_LABELS[item.mode] || item.mode || "—"}</small></td>
                  <td><strong>{item.providerName || "—"}</strong><small>{CHANNEL_CLASS_LABELS[item.providerChannelClass] || item.providerChannelClass || "—"} · route {item.providerRouteId ?? "—"}</small></td>
                  <td><strong title={item.providerAccountId}>{compactIdentifier(item.providerAccountId, 10, 4)}</strong><small>channel {item.providerChannelId ?? "—"} · key {item.providerKeyIndex ?? "—"}</small></td>
                  <td>{item.providerSubmissionAttempt ?? "—"}</td>
                  <td className="is-negative"><strong>{item.errorCode || "PROVIDER_RESPONSE_LOSS"}</strong><small>{item.errorMessage || "Provider 响应丢失"}</small></td>
                  <td><button className="ops-table-link" type="button" onClick={() => onRelayUnknownOpen?.(item)} disabled={!onRelayUnknownOpen || openingRelayUnknownId === item.jobId} aria-label={`核实 Relay 未知提交 ${item.jobId}`}>{openingRelayUnknownId === item.jobId ? "读取详情中…" : "核实并审批"}</button></td>
                </tr>
              ))}
              {!data.relayUnknownSubmissions.length ? <tr><td colSpan="8"><EmptyState title={reconciliationAvailable ? "当前没有待对账的未知提交" : "未知提交队列暂不可用"} detail={reconciliationAvailable ? "Relay 已明确返回空的 reconciliation_required 列表。" : "无法确认队列是否为空，请检查页面错误提示并刷新；不要据此判断没有未知提交。"} /></td></tr> : null}
            </tbody>
          </table>
        </TableScroller>
        {reconciliationAvailable && data.relayUnknownSubmissionTotal > data.relayUnknownSubmissions.length ? <div className="ops-table-footer"><span>当前显示前 {data.relayUnknownSubmissions.length} 项，共 {data.relayUnknownSubmissionTotal} 项；处置后刷新获取下一批。</span></div> : null}
      </section>
      {channelHealthStatus === "available" ? <><section className="ops-panel">
        <div className="ops-health-summary is-channel">
          <span><PlayCircle size={20} /><small>可用账号证据</small><strong>{evidenceCount(summary.active)}</strong></span>
          <span className="is-warning"><Timer size={20} /><small>冷却账号</small><strong>{evidenceCount(summary.cooling)}</strong></span>
          <span className="is-critical"><LinkBreak size={20} /><small>失效账号</small><strong>{evidenceCount(summary.invalid)}</strong></span>
          <span className="is-warning"><SlidersHorizontal size={20} /><small>限流事件</small><strong>{evidenceCount(summary.limits)}</strong></span>
          <span><ArrowClockwise size={20} /><small>故障切换</small><strong>{evidenceCount(summary.failovers)}</strong></span>
          <span className="is-critical"><Alarm size={20} /><small>告警积压</small><strong>{evidenceCount(summary.alerts)}</strong></span>
        </div>
      </section>
      <section className="ops-panel ops-callout">
        <ShieldCheck size={22} />
        <div><strong>渠道控制与签名遥测保持分层</strong><span>上表是 Platform 门面的可审计控制事实；下表是签名遥测摘要。遥测缺失不会被解释为渠道健康。</span></div>
      </section>
      <section className="ops-panel">
        <PanelHeader title="签名遥测摘要" detail="成功率、路由指标、限流、故障切换和告警积压；不可用字段保留为未知。" />
        <TableScroller label="签名遥测摘要">
          <table className="ops-table">
            <thead><tr><th>渠道</th><th>类型</th><th>成功率</th><th>可用签名路由</th><th>冷却账号</th><th>失效账号</th><th>限流账号</th><th>全局故障切换</th><th>全局告警积压</th><th>状态</th></tr></thead>
            <tbody>
              {data.channels.map((row) => <tr key={row.id}><td><strong>{row.name}</strong></td><td>{CHANNEL_CLASS_LABELS[row.channelClass] || row.channelClass || "类型未提供"}</td><td className={row.successRate == null ? "" : row.successRate < 90 ? "is-negative" : row.successRate < 96 ? "is-warning" : "is-positive"}>{row.successRate == null ? "—" : formatPercent(row.successRate, 2)}</td><td>{evidenceCount(row.activeAccounts)}</td><td>{evidenceCount(row.coolingAccounts)}</td><td className={Number.isFinite(Number(row.invalidAccounts)) && Number(row.invalidAccounts) > 0 ? "is-negative" : ""}>{evidenceCount(row.invalidAccounts)}</td><td>{evidenceCount(row.rateLimits)}</td><td>{evidenceCount(row.failovers)}</td><td className={Number.isFinite(Number(row.alertBacklog)) && Number(row.alertBacklog) > 0 ? "is-negative" : ""}>{evidenceCount(row.alertBacklog)}</td><td><StatusPill value={row.status} label={row.successRate == null ? "数据未接入" : ({ healthy: "正常", warning: "预警", critical: "异常" }[row.status])} /></td></tr>)}
              {!data.channels.length ? <tr><td colSpan="10"><EmptyState title="没有签名遥测数据" /></td></tr> : null}
            </tbody>
          </table>
        </TableScroller>
      </section></> : <section className="ops-panel"><DatasetState status={channelHealthStatus} label="Relay 签名遥测" detail={data.sourceErrors?.channelHealth} onRetry={onRetry} /></section>}
    </>
  );
}

function ExceptionWorklist({ title, detail, items, onSelect }) {
  return (
    <section className="ops-panel ops-worklist">
      <PanelHeader title={title} detail={detail} />
      <div>
        {items.map((item) => (
          <button key={item.id} type="button" onClick={() => onSelect(item)}>
            <Priority value={item.priority} />
            <span><strong>{item.title}</strong><small>{item.description}</small><em>{formatTime(item.occurredAt)} · {item.account || item.company || item.provider || "平台"}</em></span>
            <StatusPill value={item.status} label={exceptionStatusLabel(item.status)} />
            <ArrowRight size={16} />
          </button>
        ))}
        {!items.length ? <EmptyState title="当前没有异常" /> : null}
      </div>
    </section>
  );
}

export function PublishingAssetsScreen({ data, onExceptionSelect, onRetry }) {
  const sourceStatus = data.sourceStatus?.exceptions || "unavailable";
  if (sourceStatus !== "available") {
    return <DatasetState status={sourceStatus} label="发布与资产异常" detail={data.sourceErrors?.exceptions} onRetry={onRetry} />;
  }
  const pendingApproval = data.publishingExceptions.filter((item) => item.kind === "approval").length;
  const submissionUnknown = data.publishingExceptions.filter((item) => item.kind === "submission_unknown").length;
  const obsFailures = data.assetExceptions.filter((item) => item.kind === "obs_transfer").length;
  return (
    <>
      <section className="ops-panel">
        <div className="ops-health-summary">
          <span><Queue size={20} /><small>待审批</small><strong>{pendingApproval}</strong></span>
          <span className="is-critical"><LinkBreak size={20} /><small>结果未知</small><strong>{submissionUnknown}</strong></span>
          <span className="is-warning"><Key size={20} /><small>OAuth 到期</small><strong>{data.publishingExceptions.filter((item) => item.kind === "oauth_expiry").length}</strong></span>
          <span className="is-critical"><Database size={20} /><small>OBS 转存失败</small><strong>{obsFailures}</strong></span>
        </div>
      </section>
      <div className="ops-worklist-grid">
        <ExceptionWorklist title="发布异常" detail="人工审批、发布失败、结果未知与授权过期。" items={data.publishingExceptions} onSelect={onExceptionSelect} />
        <ExceptionWorklist title="产物与下载异常" detail="OBS 转存、存储绑定和可信下载完成记录。" items={data.assetExceptions} onSelect={onExceptionSelect} />
      </div>
      <section className="ops-panel ops-callout is-warning">
        <WarningCircle size={22} />
        <div><strong>结果未知的发布任务禁止自动重试</strong><span>只有授权管理员在提供方控制台确认没有外部发布后，才可以人工核销并重新提交。</span></div>
      </section>
    </>
  );
}

function formatAuditValue(value) {
  if (value == null) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return "[无法序列化的结构]";
  }
}

export function auditResultMeta(value) {
  const normalized = String(value || "recorded").toLocaleLowerCase("en-US");
  if (["success", "succeeded", "completed", "ok"].includes(normalized)) {
    return { tone: "healthy", label: "成功" };
  }
  if (["failed", "failure", "error", "rejected"].includes(normalized)) {
    return { tone: "critical", label: "失败" };
  }
  return { tone: "unknown", label: normalized === "recorded" ? "已记录" : "结果未提供" };
}

export function AuditDiff({ before, after, compact = false }) {
  const keys = Array.from(new Set([...Object.keys(before || {}), ...Object.keys(after || {})]));
  if (!keys.length) return <span className="ops-muted">没有字段差异</span>;
  return (
    <div className={cx("ops-audit-diff", compact && "is-compact")}>
      {keys.map((key) => {
        const previous = before?.[key];
        const next = after?.[key];
        const changed = JSON.stringify(previous) !== JSON.stringify(next);
        return (
          <div key={key} className={changed ? "is-changed" : ""}>
            <span>{key}</span>
            <del>{formatAuditValue(previous)}</del>
            <ArrowRight size={13} />
            <ins>{formatAuditValue(next)}</ins>
          </div>
        );
      })}
    </div>
  );
}

export function AuditAccessScreen({
  data,
  tab,
  onTab,
  auditQuery,
  onAuditQuery,
  auditResult,
  onAuditResult,
  onAuditOpen,
  onAuditExport,
  canExport,
  onAdminOpen,
  canManageAdmins = false,
  canReadAudit = true,
  canReadAdminAccess = true,
  onRetry,
}) {
  const tabRefs = useRef(new Map());
  const availableTabs = ACCESS_AUDIT_TABS.filter((item) => (
    item.id === "audit" ? canReadAudit : canReadAdminAccess
  ));
  const activeTab = availableTabs.some((item) => item.id === tab)
    ? tab
    : availableTabs[0]?.id;
  const activeTabDefinition = availableTabs.find((item) => item.id === activeTab);
  const auditSourceStatus = data.sourceStatus?.audits || "unavailable";
  const administratorSourceStatus = combineSourceStatuses(
    data.sourceStatus?.administrators,
    data.sourceStatus?.permissionCatalog,
  );
  const filteredAudits = data.auditEvents.filter((event) => {
    const queryMatch = !auditQuery.trim() || [event.actorName, event.actionLabel, event.targetLabel, event.reason].some((value) => String(value || "").toLocaleLowerCase("zh-CN").includes(auditQuery.trim().toLocaleLowerCase("zh-CN")));
    return queryMatch && (auditResult === "all" || event.result === auditResult);
  });

  const handleTabKeyDown = (event, currentTabId) => {
    if (!availableTabs.length) return;
    const currentIndex = availableTabs.findIndex((item) => item.id === currentTabId);
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % availableTabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + availableTabs.length) % availableTabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = availableTabs.length - 1;
    else return;

    event.preventDefault();
    const nextTab = availableTabs[nextIndex];
    onTab(nextTab.id);
    tabRefs.current.get(nextTab.id)?.focus();
  };

  if (!activeTabDefinition) {
    return <EmptyState title="当前管理员没有权限与审计模块的读取权限" />;
  }

  return (
    <>
      <div className="ops-segmented-tabs" role="tablist" aria-label="权限与审计模块">
        {availableTabs.map((item) => {
          const Icon = item.icon;
          const selected = activeTab === item.id;
          return (
            <button
              key={item.id}
              ref={(node) => {
                if (node) tabRefs.current.set(item.id, node);
                else tabRefs.current.delete(item.id);
              }}
              id={item.tabId}
              role="tab"
              aria-selected={selected}
              aria-controls={item.panelId}
              tabIndex={selected ? 0 : -1}
              className={selected ? "is-active" : ""}
              type="button"
              onClick={() => onTab(item.id)}
              onKeyDown={(event) => handleTabKeyDown(event, item.id)}
            >
              <Icon size={16} aria-hidden="true" />
              {item.label}
            </button>
          );
        })}
      </div>
      {activeTab === "audit" ? (
        <section
          className="ops-panel"
          id="ops-access-audit-panel-audit"
          role="tabpanel"
          aria-labelledby="ops-access-audit-tab-audit"
        >
          {auditSourceStatus === "available" ? <><div className="ops-filterbar">
            <label className="ops-search"><span className="sr-only">搜索审计事件</span><MagnifyingGlass size={16} aria-hidden="true" /><input value={auditQuery} onChange={(event) => onAuditQuery(event.target.value)} placeholder="搜索操作者、动作、目标或原因" /></label>
            <label><span className="sr-only">筛选审计结果</span><Funnel size={15} aria-hidden="true" /><select value={auditResult} onChange={(event) => onAuditResult(event.target.value)}><option value="all">全部结果</option><option value="success">成功</option><option value="failed">失败</option></select></label>
            <span>{filteredAudits.length} 条记录</span>
            <button className="ops-secondary-button" type="button" onClick={() => onAuditExport(filteredAudits)} disabled={!canExport}><DownloadSimple size={16} />导出 CSV</button>
          </div>
          <TableScroller hasActions label="操作审计">
            <table className="ops-table ops-audit-table">
              <thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>目标</th><th>变更前后</th><th>原因</th><th>结果</th><th>操作</th></tr></thead>
              <tbody>
                {filteredAudits.map((event) => (
                  <tr key={event.id}>
                    <td>{formatDateTime(event.occurredAt)}</td><td><strong>{event.actorName}</strong><small>{event.actorId}</small></td><td><strong>{event.actionLabel}</strong><small>{event.action}</small></td><td>{event.targetLabel}</td><td><AuditDiff before={event.before} after={event.after} compact /></td><td><span className="ops-reason-summary">{event.reason || "—"}</span></td><td>{(() => { const result = auditResultMeta(event.result); return <StatusPill value={result.tone} label={result.label} />; })()}</td><td><button className="ops-table-link" type="button" onClick={() => onAuditOpen(event)}>查看差异</button></td>
                  </tr>
                ))}
                {!filteredAudits.length ? <tr><td colSpan="8"><EmptyState title="没有匹配的审计事件" /></td></tr> : null}
              </tbody>
            </table>
          </TableScroller></> : <DatasetState status={auditSourceStatus} label="操作审计" detail={data.sourceErrors?.audits} onRetry={onRetry} />}
        </section>
      ) : (
        <div
          className="ops-audit-tabpanel"
          id="ops-access-audit-panel-access"
          role="tabpanel"
          aria-labelledby="ops-access-audit-tab-access"
        >
          <section className="ops-panel ops-callout">
            <ShieldCheck size={22} />
            <div><strong>平台所有者是受保护的生产安全边界</strong><span>所有者权限来自服务端 allowlist 与强认证；其他平台管理员只获得明确分配的最小权限，不继承完整权限。</span></div>
          </section>
          <section className="ops-panel">
            <PanelHeader title="平台管理员" detail="这是平台管理权限域，不复用企业员工的 16 项权限，也不等同于 new-api 渠道管理员。" />
            {administratorSourceStatus === "available" ? <TableScroller hasActions label="平台管理员">
              <table className="ops-table">
                <thead><tr><th>管理员</th><th>职责</th><th>状态</th><th>权限数量</th><th>最近活动</th><th>安全边界</th><th>操作</th></tr></thead>
                <tbody>
                  {data.platformAdmins.map((admin) => {
                    const status = admin.status === "active"
                      ? { tone: "healthy", label: "正常" }
                      : ["disabled", "inactive", "suspended"].includes(admin.status)
                        ? { tone: "disabled", label: "已停用" }
                        : { tone: "unknown", label: "状态未提供" };
                    return <tr key={admin.id}><td><strong>{admin.name}</strong><small>{admin.email}</small></td><td>{admin.roleLabel}</td><td><StatusPill value={status.tone} label={status.label} /></td><td>{admin.permissions.includes("*") ? "全部" : `${admin.permissions.length} 项`}</td><td>{admin.lastActiveAt ? formatDateTime(admin.lastActiveAt) : "未提供"}</td><td>{admin.owner ? <span className="ops-owner-mark"><SealCheck size={15} />所有者保护</span> : "最小权限"}</td><td><button className="ops-table-link" type="button" onClick={() => onAdminOpen(admin)} disabled={admin.owner || !canManageAdmins}>{admin.owner ? "不可编辑" : canManageAdmins ? "配置权限" : "只读"}</button></td></tr>;
                  })}
                  {!data.platformAdmins.length ? <tr><td colSpan="7"><EmptyState title="没有平台管理员数据" /></td></tr> : null}
                </tbody>
              </table>
            </TableScroller> : <DatasetState status={administratorSourceStatus} label="平台管理员" detail={data.sourceErrors?.administrators} onRetry={onRetry} />}
          </section>
        </div>
      )}
    </>
  );
}
