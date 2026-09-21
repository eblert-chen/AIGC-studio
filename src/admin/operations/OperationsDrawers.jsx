import { useEffect, useRef, useState } from "react";
import {
  ArrowClockwise,
  ArrowRight,
  Check,
  CheckCircle,
  Eye,
  Funnel,
  Lightning,
  LinkBreak,
  LockKey,
  MagnifyingGlass,
  Receipt,
  ShieldCheck,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  entitlementStateLabel,
  exceptionStatusLabel,
  filterExceptions,
  formatDateTime,
  formatInteger,
  formatTime,
} from "../adminConsoleUtils.js";
import { toggleSetValue } from "./BusinessEntitlementViews.jsx";
import {
  auditResultMeta,
  AuditDiff,
  relayChannelTypeLabel,
  RELAY_CHANNEL_STATUS_META,
} from "./ChannelAuditViews.jsx";
import {
  CHANNEL_CLASS_LABELS,
  compactIdentifier,
  cx,
  EmptyState,
  GENERATION_MODE_LABELS,
  KIND_LABELS,
  Priority,
  StatusPill,
} from "./operationsShared.jsx";
import { relayChannelTestErrorLabel } from "../relayChannelOperations.js";

function Drawer({ title, detail, onClose, children, footer, wide = false, tone = "evidence" }) {
  const dialogRef = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    const focusableSelector = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const getFocusable = () => Array.from(dialog?.querySelectorAll(focusableSelector) || []).filter((element) => !element.hasAttribute("hidden") && element.getAttribute("aria-hidden") !== "true");
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = getFocusable();
      if (!focusable.length) {
        event.preventDefault();
        dialog?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialog?.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialog?.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKeyDown);
    (getFocusable()[0] || dialog)?.focus();
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);
  return (
    <div className="ops-overlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section ref={dialogRef} className={cx("ops-drawer", wide && "is-wide", `is-${tone}`)} role="dialog" aria-modal="true" aria-label={title} tabIndex="-1">
        <header><div><h2>{title}</h2>{detail ? <p>{detail}</p> : null}</div><button className="ops-drawer-close" data-icon-only="true" type="button" onClick={onClose} aria-label="关闭"><X size={19} /></button></header>
        <div className="ops-drawer-body">{children}</div>
        {footer ? <footer>{footer}</footer> : null}
      </section>
    </div>
  );
}

export function RelayChannelDrawer({
  channel,
  routeOptions = [],
  intent,
  targetStatus,
  operationId,
  canManage,
  demoMode,
  busy,
  error,
  requiresReadback,
  receipt,
  onClose,
  onSubmit,
  onReadback,
}) {
  const [reason, setReason] = useState("");
  const [approved, setApproved] = useState(false);
  const [selectedRouteKey, setSelectedRouteKey] = useState("");
  const [selectedMode, setSelectedMode] = useState("");
  const exactRouteOptions = (Array.isArray(routeOptions) ? routeOptions : [])
    .filter((route) => String(route.channelId) === String(channel.id))
    .map((route) => ({
      ...route,
      selectionKey: `${encodeURIComponent(route.publicModelId)}|${encodeURIComponent(route.routeId)}`,
    }));
  const selectedRoute = exactRouteOptions.find(
    (route) => route.selectionKey === selectedRouteKey,
  ) || null;
  const routeTestModes = Array.isArray(selectedRoute?.requiredTestModes)
    ? selectedRoute.requiredTestModes
    : [];
  const effectiveMode = routeTestModes.includes(selectedMode)
    ? selectedMode
    : routeTestModes.find((mode) => !selectedRoute?.freshTestModes?.includes(mode)) || routeTestModes[0] || "";
  const isOperation = intent === "test" || intent === "status";
  const state = RELAY_CHANNEL_STATUS_META[channel.status] || RELAY_CHANNEL_STATUS_META.unavailable;
  const ready = canManage
    && isOperation
    && reason.trim().length >= 3
    && reason.trim().length <= 240
    && (intent !== "test" || selectedRoute)
    && approved
    && !receipt
    && !requiresReadback;
  const submit = () => onSubmit({
    channel,
    kind: intent,
    values: {
      operationId,
      reason: reason.trim(),
      approved,
      expectedRevision: channel.revision,
      targetStatus,
      publicModelId: selectedRoute?.publicModelId || "",
      routeId: selectedRoute?.routeId || "",
      mode: effectiveMode,
    },
  });
  const resultLabel = receipt?.kind === "test"
    ? receipt.result?.success
      ? `测试成功，${formatInteger(receipt.result.responseTimeMs)} ms`
      : receipt.result
        ? `测试失败：${relayChannelTestErrorLabel(receipt.result.errorCode)}`
        : "等待 Relay 完成测试"
    : receipt?.kind === "status"
      ? receipt.result?.errorCode === "CHANNEL_REVISION_CONFLICT"
        ? "revision 已变化，本次状态操作未生效"
        : receipt.result
          ? `${RELAY_CHANNEL_STATUS_META[receipt.result.previousStatus]?.label || receipt.result.previousStatus} → ${RELAY_CHANNEL_STATUS_META[receipt.result.currentStatus]?.label || receipt.result.currentStatus}`
          : "等待 Relay 完成状态操作"
      : "";
  return (
    <Drawer
      title={`${channel.name} · Relay 渠道`}
      detail={`channel #${channel.id} · ${relayChannelTypeLabel(channel)}`}
      onClose={onClose}
      wide
      tone="guarded"
      footer={(
        <>
          <button className="ops-secondary-button" type="button" onClick={onClose}>{requiresReadback ? "结果核对前不可关闭" : "关闭"}</button>
          {isOperation && (requiresReadback || receipt?.state === "pending") ? <button className="ops-secondary-button" type="button" onClick={onReadback} disabled={busy}><ArrowClockwise size={16} className={busy ? "is-spinning" : ""} />只读核对结果</button> : null}
          {isOperation && canManage ? <button className="ops-primary-button" type="button" onClick={submit} disabled={busy || !ready}><ShieldCheck size={16} />{intent === "test" ? "批准一次测试" : targetStatus === "enabled" ? "批准启用" : "批准停用"}</button> : null}
        </>
      )}
    >
      {demoMode ? <div className="ops-callout"><ShieldCheck size={21} /><div><strong>演示模式</strong><span>这里的操作只更新浏览器演示数据，不会调用 Platform 或 Relay。</span></div></div> : null}
      {error ? <div className="ops-message is-error" role="alert">{error}</div> : null}
      {requiresReadback ? <div className="ops-callout is-warning"><WarningCircle size={21} /><div><strong>本次 operation_id 已锁定</strong><span>上一次提交结果尚未确认。页面只会读取同一个 operation_id，不会再次 POST，也不会生成新 ID。</span></div></div> : null}
      <div className="ops-detail-grid ops-relay-channel-detail">
        <span><small>渠道 ID</small><strong>{channel.id}</strong></span>
        <span><small>适配类型</small><strong>{relayChannelTypeLabel(channel)}</strong></span>
        <span><small>状态</small><strong>{state.label}</strong></span>
        <span><small>凭据状态</small><strong>{channel.credentialConfigured ? "已配置" : "未配置"}</strong></span>
        <span><small>模型数量</small><strong>{channel.modelCount}</strong></span>
        <span><small>测试模型</small><strong>{channel.testModel || "未指定"}</strong></span>
        <span><small>通用连通测试</small><strong>{channel.testSupported ? "支持" : "不支持，需 staging canary"}</strong></span>
        <span><small>权重 / 优先级</small><strong>{channel.weight ?? "未提供"} / {channel.priority ?? "未提供"}</strong></span>
        <span><small>自动禁用</small><strong>{channel.autoBan ? "开启" : "关闭"}</strong></span>
        <span><small>标签</small><strong>{channel.tag || "无"}</strong></span>
        <span><small>创建时间</small><strong>{formatDateTime(channel.createdAt)}</strong></span>
        <span><small>上次测试</small><strong>{formatDateTime(channel.lastTestedAt)}</strong></span>
        <span><small>响应延迟</small><strong>{channel.responseTimeMs == null ? "待核验" : `${formatInteger(channel.responseTimeMs)} ms`}</strong></span>
        <span><small>路由数量</small><strong>Platform 当前未提供</strong></span>
      </div>
      <div className="ops-drawer-section">
        <span className="ops-field-label">已配置模型</span>
        <p>{channel.configuredModels.length ? channel.configuredModels.join("、") : "未提供已配置模型"}</p>
      </div>
      <div className="ops-drawer-section ops-relay-revision">
        <span className="ops-field-label">并发控制 revision</span>
        <code>{channel.revision || "未提供"}</code>
      </div>
      {intent === "status" ? <div className="ops-callout is-warning"><LockKey size={21} /><div><strong>停用只阻止新准入</strong><span>已接受并绑定原渠道的任务继续完成；本操作不会取消在途任务，也不会切换未知提交的渠道。</span></div></div> : null}
      {receipt ? <div className={cx("ops-callout", receipt.state === "failed" && "is-warning")}><Receipt size={21} /><div><strong>操作回执：{receipt.state === "pending" ? "处理中" : receipt.state === "succeeded" ? "已完成" : "未生效"}</strong><span>{resultLabel}</span><code>{receipt.operationId}</code></div></div> : null}
      {isOperation && !receipt ? (
        canManage ? (
          <>
            <div className="ops-drawer-section ops-operation-identity"><span className="ops-field-label">稳定 operation_id</span><code>{operationId}</code></div>
            {intent === "test" ? (
              <div className="ops-drawer-section ops-exact-route-test">
                <div className="ops-section-heading">
                  <div>
                    <span className="ops-field-label">精确发布路由</span>
                    <p>从 Relay 当前签名发布中选择精确路由。Platform 不接受手填 route_id，也不会把渠道密钥、凭据指纹或上游地址发送到浏览器。</p>
                  </div>
                </div>
                {exactRouteOptions.length ? (
                  <>
                    <label className="ops-form-field">
                      <span>Relay 发布路由 <b>必选</b></span>
                      <select value={selectedRouteKey} onChange={(event) => {
                        const nextKey = event.target.value;
                        const nextRoute = exactRouteOptions.find((route) => route.selectionKey === nextKey);
                        setSelectedRouteKey(nextKey);
                        setSelectedMode(
                          nextRoute?.requiredTestModes?.find((mode) => !nextRoute?.freshTestModes?.includes(mode))
                          || nextRoute?.requiredTestModes?.[0]
                          || "",
                        );
                      }} disabled={busy || requiresReadback}>
                        <option value="">选择 public model 与精确 route</option>
                        {exactRouteOptions.map((route) => (
                          <option key={route.selectionKey} value={route.selectionKey}>
                            {route.publicModelId} → {route.upstreamModel} · {route.routeId}
                          </option>
                        ))}
                      </select>
                    </label>
                    {selectedRoute && routeTestModes.length ? (
                      <label className="ops-form-field">
                        <span>验收模式 <b>必选</b></span>
                        <select value={effectiveMode} onChange={(event) => setSelectedMode(event.target.value)} disabled={busy || requiresReadback}>
                          {routeTestModes.map((mode) => (
                            <option key={mode} value={mode}>
                              {mode} · {selectedRoute.freshTestModes?.includes(mode) ? "已有新鲜证据" : "需要真实测试"}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : null}
                    {selectedRoute ? (
                      <div className="ops-route-selection-evidence" aria-live="polite">
                        <span><small>public model</small><strong>{selectedRoute.publicModelId}</strong></span>
                        <span><small>route</small><strong>{selectedRoute.routeId}</strong></span>
                        <span><small>upstream</small><strong>{selectedRoute.upstreamModel}</strong></span>
                        <span><small>Adapter Profile</small><strong>{selectedRoute.adapterProfileId} · {selectedRoute.adapterProfileRevision}</strong></span>
                        <span><small>当前证据</small><strong>{selectedRoute.fresh ? "测试新鲜" : selectedRoute.accepted ? "验收过但需重测" : "尚未验收"}</strong></span>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <div className="ops-message is-error" role="alert">当前渠道没有来自 Relay 发布证据的可选路由。请先完成路由声明并刷新本页；为避免测试错误账号，页面不提供手填 route_id。</div>
                )}
                <div className="ops-callout is-warning"><WarningCircle size={21} /><div><strong>这不是免费连通探测</strong><span>批准后将发起一次真实低配生成，可能产生供应商费用；失败不会解锁模型发布。</span></div></div>
              </div>
            ) : null}
            <label className="ops-form-field"><span>操作原因 <b>必填</b></span><textarea rows="4" minLength="3" maxLength="240" value={reason} onChange={(event) => setReason(event.target.value)} disabled={busy || requiresReadback} placeholder="至少 3 个字符，说明测试或状态变更的工单依据" /></label>
            <label className="ops-approval-check"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} disabled={busy || requiresReadback} /><span><strong>明确批准本次操作</strong><small>我确认使用当前 revision 和上方 operation_id；网络结果不明时只做只读核对。</small></span></label>
          </>
        ) : <div className="ops-callout"><LockKey size={21} /><div><strong>当前为只读详情</strong><span>需要 platform.relay_health.manage 权限才能测试、启用或手动停用渠道。</span></div></div>
      ) : null}
    </Drawer>
  );
}

export function ExceptionDrawer({
  item,
  onClose,
  onResolve,
  onOpenRelayReconciliation,
  canResolve = true,
  busy,
}) {
  const [note, setNote] = useState("");
  const isRelayUnknown = item.category === "RELAY_SUBMISSION_UNKNOWN"
    || item.kind === "relay_submission_unknown";
  return (
    <Drawer
      title={item.title}
      detail={`${item.priority || "P3"} · ${exceptionStatusLabel(item.status)} · ${formatDateTime(item.occurredAt)}`}
      onClose={onClose}
      tone={isRelayUnknown ? "guarded" : "action"}
      footer={<><button className="ops-secondary-button" type="button" onClick={onClose}>关闭</button>{isRelayUnknown ? <button className="ops-primary-button" type="button" onClick={onOpenRelayReconciliation}><ShieldCheck size={16} />打开人工对账列表</button> : <button className="ops-primary-button" type="button" onClick={() => onResolve(item, note)} disabled={!canResolve || busy || !note.trim()}><CheckCircle size={16} />{canResolve ? "执行安全处置" : "需按处理指引操作"}</button>}</>}
    >
      <div className="ops-drawer-section"><span className="ops-field-label">异常说明</span><p>{item.description}</p></div>
      {item.nextAction ? <div className="ops-drawer-section"><span className="ops-field-label">处理指引</span><p>{item.nextAction}</p></div> : null}
      {item.nextAction ? <div className="ops-callout"><Lightning size={20} /><div><strong>建议处置</strong><span>{item.nextAction}</span></div></div> : null}
      <div className="ops-detail-grid"><span><small>负责人</small><strong>{item.owner || "未分派"}</strong></span><span><small>持续时间</small><strong>{item.duration || "未提供"}</strong></span><span><small>异常类型</small><strong>{item.kind || "未提供"}</strong></span><span><small>当前状态</small><strong>{exceptionStatusLabel(item.status)}</strong></span></div>
      {isRelayUnknown ? <div className="ops-callout is-warning"><WarningCircle size={20} /><div><strong>异常摘要不能直接 resolve</strong><span>进入 Relay 人工对账列表并重新读取详情，核对 route、attempt 与 token fencing 证明。</span></div></div> : <label className="ops-form-field"><span>处理记录 <b>必填</b></span><textarea value={note} onChange={(event) => setNote(event.target.value)} rows="5" maxLength="500" placeholder="说明核对结果、处置动作和后续观察项" /></label>}
    </Drawer>
  );
}

export function RelayUnknownDrawer({
  item,
  onClose,
  onRefresh,
  onResolve,
  canManage = false,
  busy = false,
  refreshing = false,
  error = "",
  fieldErrors = {},
  requiresRefresh = false,
}) {
  const [outcome, setOutcome] = useState("");
  const [upstreamTaskId, setUpstreamTaskId] = useState("");
  const [synchronousResult, setSynchronousResult] = useState({
    providerModelId: item.providerUpstreamModel || "",
    providerResponseSha256: "",
    artifactUrl: "",
    providerCreatedAt: "",
    generatedImages: "1",
    outputTokens: "0",
    totalTokens: "0",
  });
  const [verificationReference, setVerificationReference] = useState("");
  const [reason, setReason] = useState("");
  const [approved, setApproved] = useState(false);
  const synchronousImageCreated = item.mode === "text_to_image" && outcome === "created";
  const synchronousReady = !synchronousImageCreated || (
    synchronousResult.providerModelId.trim()
    && /^[0-9a-f]{64}$/.test(synchronousResult.providerResponseSha256.trim())
    && /^https:\/\//.test(synchronousResult.artifactUrl.trim())
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(synchronousResult.providerCreatedAt.trim())
    && Number.isInteger(Number(synchronousResult.generatedImages))
    && Number(synchronousResult.generatedImages) >= 1
    && Number(synchronousResult.generatedImages) <= 16
    && Number.isInteger(Number(synchronousResult.outputTokens))
    && Number(synchronousResult.outputTokens) >= 0
    && Number.isInteger(Number(synchronousResult.totalTokens))
    && Number(synchronousResult.totalTokens) >= Number(synchronousResult.outputTokens)
  );
  const ready = approved
    && ["created", "not_created"].includes(outcome)
    && verificationReference.trim()
    && reason.trim().length >= 3
    && (outcome !== "created" || synchronousImageCreated || upstreamTaskId.trim())
    && synchronousReady;
  const submit = () => onResolve(item, {
    outcome,
    upstreamTaskId,
    verificationReference,
    reason,
    approved,
    synchronousResult,
  });
  const updateSynchronousResult = (field) => (event) => {
    setSynchronousResult((current) => ({ ...current, [field]: event.target.value }));
  };
  const fieldError = (field) => fieldErrors[field]
    ? <small className="ops-field-error" role="alert">{fieldErrors[field]}</small>
    : null;
  return (
    <Drawer
      title="核实 Relay 未知提交"
      detail={`${compactIdentifier(item.jobId, 12, 8)} · ${formatDateTime(item.unknownAt)}`}
      onClose={onClose}
      wide
      tone="guarded"
      footer={(
        <>
          <button className="ops-secondary-button" type="button" onClick={onClose}>关闭</button>
          <button className="ops-secondary-button" type="button" onClick={onRefresh} disabled={busy || refreshing}><ArrowClockwise size={16} className={refreshing ? "is-spinning" : ""} />刷新详情</button>
          {canManage ? <button className="ops-primary-button" type="button" onClick={submit} disabled={busy || refreshing || requiresRefresh || !ready}><ShieldCheck size={16} />{outcome === "created" ? "批准并确认已创建" : outcome === "not_created" ? "批准并确认未创建" : "选择核实结果"}</button> : null}
        </>
      )}
    >
      {error ? <div className="ops-message is-error ops-reconciliation-error" role="alert">{error}</div> : null}
      {requiresRefresh ? <div className="ops-callout is-warning"><WarningCircle size={21} /><div><strong>已锁定再次提交</strong><span>上一次 resolve 的结果需要重新确认。点“刷新详情”只会读取 pending 详情或 Relay receipt；页面不会再次 POST resolve。</span></div></div> : null}
      <div className="ops-detail-grid ops-reconciliation-identity">
        <span><small>Relay job ID</small><strong>{item.jobId}</strong></span>
        <span><small>业务引用</small><strong>{item.clientReferenceId || "未提供"}</strong></span>
        <span><small>模型 / 模式</small><strong>{item.model} · {GENERATION_MODE_LABELS[item.mode] || item.mode}</strong></span>
        <span><small>错误证据</small><strong>{item.errorCode || "未提供"} · {item.errorMessage || "未提供"}</strong></span>
        <span><small>固定 Provider</small><strong>{item.providerName} · {CHANNEL_CLASS_LABELS[item.providerChannelClass] || item.providerChannelClass}</strong></span>
        <span><small>上游模型</small><strong>{item.providerUpstreamModel || "未提供"}</strong></span>
        <span><small>固定 route</small><strong>{item.providerRouteId} · {item.providerRouteKey}</strong></span>
        <span><small>固定账号</small><strong>{item.providerAccountId} · channel {item.providerChannelId} / key {item.providerKeyIndex}</strong></span>
        <span><small>提交 attempt fencing</small><strong>{item.providerSubmissionAttempt}</strong></span>
        <span><small>详情更新时间</small><strong>{formatDateTime(item.updatedAt)}</strong></span>
      </div>
      <div className="ops-drawer-section ops-fencing-proof">
        <span className="ops-field-label">Reconciliation token fencing</span>
        <code>{item.reconciliationToken}</code>
      </div>
      <div className="ops-callout is-warning">
        <LinkBreak size={21} />
        <div><strong>这里只确认 Provider 已发生的事实，不会重新提交生成请求</strong><span>“已创建”会继续绑定原渠道任务；“未创建”会结束该次未知提交并释放原账号槽位。两种结果都不会切换渠道。</span></div>
      </div>
      {canManage ? (
        <>
          <fieldset className="ops-state-options ops-reconciliation-outcomes">
            <legend>Provider 核实结果 <b>必选</b></legend>
            <label className={outcome === "created" ? "is-active" : ""}><input type="radio" name="relay-unknown-outcome" value="created" checked={outcome === "created"} onChange={() => setOutcome("created")} /><span><strong>已创建</strong><small>{item.mode === "text_to_image" ? "补齐同步图片结果证据，继续原路由恢复" : "填写真实上游任务 ID，继续原路由跟踪"}</small></span></label>
            <label className={outcome === "not_created" ? "is-active" : ""}><input type="radio" name="relay-unknown-outcome" value="not_created" checked={outcome === "not_created"} onChange={() => { setOutcome("not_created"); setUpstreamTaskId(""); }} /><span><strong>未创建</strong><small>确认 Provider 侧没有对应任务，结束本次提交</small></span></label>
          </fieldset>
          {outcome === "created" && !synchronousImageCreated ? <label className="ops-form-field"><span>Provider 上游任务 ID <b>必填</b></span><input value={upstreamTaskId} onChange={(event) => setUpstreamTaskId(event.target.value)} maxLength="191" autoComplete="off" aria-invalid={Boolean(fieldErrors.upstream_task_id)} placeholder="从 Provider 控制台复制，不要填写本平台任务 ID" />{fieldError("upstream_task_id")}</label> : null}
          {synchronousImageCreated ? (
            <div className="ops-drawer-section ops-synchronous-evidence">
              <div className="ops-callout is-warning">
                <WarningCircle size={21} />
                <div><strong>同步图片结果需要补齐一次性结果证据</strong><span>临时 Artifact URL 提交后只供 Platform → Relay 内部恢复结果使用；审计和结果回执只保留 URL 的 SHA-256，不会回显原 URL。不要把 URL 复制到日志、截图或工单。</span></div>
              </div>
              <div className="ops-form-grid">
                <label className="ops-form-field"><span>Provider 模型 <b>必填</b></span><input value={synchronousResult.providerModelId} onChange={updateSynchronousResult("providerModelId")} maxLength="128" autoComplete="off" aria-invalid={Boolean(fieldErrors.provider_model_id)} placeholder="例如 doubao-seedream-5-0-260128" />{fieldError("provider_model_id")}</label>
                <label className="ops-form-field"><span>Provider 响应 SHA-256 <b>必填</b></span><input value={synchronousResult.providerResponseSha256} onChange={updateSynchronousResult("providerResponseSha256")} maxLength="64" autoComplete="off" spellCheck="false" aria-invalid={Boolean(fieldErrors.provider_response_sha256)} placeholder="64 位小写十六进制；系统据此绑定任务 ID" />{fieldError("provider_response_sha256")}</label>
              </div>
              <label className="ops-form-field"><span>临时 Artifact URL <b>敏感 · 必填</b></span><input type="url" value={synchronousResult.artifactUrl} onChange={updateSynchronousResult("artifactUrl")} maxLength="8192" autoComplete="off" spellCheck="false" aria-invalid={Boolean(fieldErrors.artifact_url)} placeholder="https://provider.example/result.png?..." />{fieldError("artifact_url")}</label>
              <div className="ops-form-grid">
                <label className="ops-form-field"><span>Provider 创建时间（UTC） <b>必填</b></span><input value={synchronousResult.providerCreatedAt} onChange={updateSynchronousResult("providerCreatedAt")} autoComplete="off" spellCheck="false" aria-invalid={Boolean(fieldErrors.provider_created_at)} placeholder="2026-08-28T01:02:03Z" />{fieldError("provider_created_at")}</label>
                <label className="ops-form-field"><span>生成图片数 <b>必填</b></span><input type="number" min="1" max="16" step="1" value={synchronousResult.generatedImages} onChange={updateSynchronousResult("generatedImages")} aria-invalid={Boolean(fieldErrors.generated_images)} />{fieldError("generated_images")}</label>
              </div>
              <div className="ops-form-grid">
                <label className="ops-form-field"><span>输出 token 数 <b>必填</b></span><input type="number" min="0" step="1" value={synchronousResult.outputTokens} onChange={updateSynchronousResult("outputTokens")} aria-invalid={Boolean(fieldErrors.output_tokens)} />{fieldError("output_tokens")}</label>
                <label className="ops-form-field"><span>总 token 数 <b>必填</b></span><input type="number" min="0" step="1" value={synchronousResult.totalTokens} onChange={updateSynchronousResult("totalTokens")} aria-invalid={Boolean(fieldErrors.total_tokens)} />{fieldError("total_tokens")}</label>
              </div>
            </div>
          ) : null}
          <label className="ops-form-field"><span>核实凭证 <b>必填</b></span><input value={verificationReference} onChange={(event) => setVerificationReference(event.target.value)} maxLength="191" autoComplete="off" aria-invalid={Boolean(fieldErrors.verification_reference)} placeholder="工单号、Provider 查询记录或内部事件编号" />{fieldError("verification_reference")}</label>
          <label className="ops-form-field"><span>审批原因 <b>必填</b></span><textarea value={reason} onChange={(event) => setReason(event.target.value)} rows="4" maxLength="240" aria-invalid={Boolean(fieldErrors.reason)} placeholder="至少 3 个字符；说明核实时间、控制台结果与审批依据" />{fieldError("reason")}</label>
          <label className="ops-approval-check"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>明确审批确认</strong><small>我已在 Provider 控制台核实，并理解此次动作不会创建新的 Provider 请求，也不能跨渠道重试。</small></span></label>
        </>
      ) : <div className="ops-callout"><LockKey size={21} /><div><strong>当前为只读核实</strong><span>需要 platform.relay_health.manage 权限才能作出明确审批并提交 resolve。</span></div></div>}
    </Drawer>
  );
}

export function RelayCallbackDeadLetterDrawer({ item, onClose, onRedrive, canManage, busy, error, requiresReadback }) {
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  const [approved, setApproved] = useState(false);
  const ready = approved && actor.trim() && reason.trim().length >= 3;
  return (
    <Drawer
      title="核对 Callback 死信"
      detail={`${compactIdentifier(item.eventId, 12, 8)} · ${formatDateTime(item.deadLetteredAt)}`}
      onClose={onClose}
      wide
      tone="guarded"
      footer={<><button className="ops-secondary-button" type="button" onClick={onClose}>关闭并刷新队列</button>{canManage ? <button className="ops-primary-button" type="button" onClick={() => onRedrive(item, { actor, reason, approved })} disabled={busy || requiresReadback || !ready}><ArrowClockwise size={16} />{requiresReadback ? "等待只读核对" : "批准重新投递"}</button> : null}</>}
    >
      {error ? <div className="ops-message is-error" role="alert">{error}</div> : null}
      {requiresReadback ? <div className="ops-callout is-warning"><WarningCircle size={21} /><div><strong>当前事件已锁定再次提交</strong><span>上一次 POST 的结果不明。关闭窗口并刷新队列只会读取 Relay 状态；本窗口不会再次 POST，也不会生成新的 operation_id。</span></div></div> : null}
      <div className="ops-detail-grid">
        <span><small>事件 ID</small><strong>{item.eventId}</strong></span>
        <span><small>任务 ID</small><strong>{item.jobId}</strong></span>
        <span><small>状态 / 尝试</small><strong>{item.state} · {item.attempts}/{item.maxAttempts}</strong></span>
        <span><small>最后 HTTP 响应</small><strong>{item.responseStatus || "无"}</strong></span>
        <span><small>Payload SHA-256</small><strong>{item.payloadSha256}</strong></span>
        <span><small>目的端 SHA-256</small><strong>{item.callbackUrlSha256}</strong></span>
      </div>
      <div className="ops-callout is-warning"><WarningCircle size={21} /><div><strong>重新投递复用原事件，不创建新生成任务</strong><span>Platform 会先审计审批证据，再向 Relay 发送一次 POST。网络结果不明时，页面只读取 redrive 回执，绝不重复 POST。</span></div></div>
      {canManage ? <>
        <label className="ops-form-field"><span>处置人 <b>必填</b></span><input value={actor} onChange={(event) => setActor(event.target.value)} maxLength="128" autoComplete="off" placeholder="值班人或工单处理人" /></label>
        <label className="ops-form-field"><span>重新投递原因 <b>必填</b></span><textarea value={reason} onChange={(event) => setReason(event.target.value)} rows="4" maxLength="240" placeholder="说明已核对目的端可用性、失败原因和重新投递依据" /></label>
        <label className="ops-approval-check"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>明确批准重新投递</strong><small>我确认这是原 callback 事件的安全重投，不会创建新的生成任务。</small></span></label>
      </> : <div className="ops-callout"><LockKey size={21} /><div><strong>当前为只读核对</strong><span>需要 platform.relay_health.manage 权限才能批准 redrive。</span></div></div>}
    </Drawer>
  );
}

export function EntitlementDrawer({ company, product, grant, onClose, onSave, busy, readOnly = false }) {
  const [state, setState] = useState(grant?.state === "disabled" ? "disabled" : "enabled");
  const pointBilling = company?.billingUnit === "POINT" && Number(company?.billingVersion) === 2;
  const [priceAmount, setPriceAmount] = useState(
    pointBilling ? (grant?.pricePoints ?? "") : (grant?.priceCents ?? ""),
  );
  const [quota, setQuota] = useState(grant?.quota ?? "");
  const [concurrency, setConcurrency] = useState(grant?.concurrency ?? "");
  const [effectiveAt, setEffectiveAt] = useState(grant?.effectiveAt || "");
  const [expiresAt, setExpiresAt] = useState(grant?.expiresAt || "");
  const [capabilityLimit, setCapabilityLimit] = useState(grant?.capabilityLimit || "");
  const [reason, setReason] = useState("");
  const submit = () => {
    if (readOnly) return;
    onSave({ company, product, grant: { ...grant, companyId: company.id, productId: product.id, state, priceAmount: priceAmount === "" ? null : Number(priceAmount), billingUnit: company.billingUnit, billingVersion: company.billingVersion, quota: quota === "" ? null : Number(quota), concurrency: concurrency === "" ? null : Number(concurrency), effectiveAt, expiresAt, capabilityLimit }, reason });
  };
  return (
    <Drawer title={`${company.name} · ${product.name}`} detail={`${KIND_LABELS[product.kind] || product.kind}权益${readOnly ? "详情" : "配置"}`} onClose={onClose} footer={readOnly ? <button className="ops-secondary-button" type="button" onClick={onClose}>关闭</button> : <><button className="ops-secondary-button" type="button" onClick={onClose}>取消</button><button className="ops-primary-button" type="button" onClick={submit} disabled={busy || !reason.trim()}><Check size={16} />保存变更</button></>}>
      {readOnly ? <div className="ops-callout"><Eye size={20} /><div><strong>只读详情</strong><span>当前权限允许核对价格、配额、并发与有效期，不允许提交任何变更。</span></div></div> : null}
      <fieldset className="ops-state-options"><legend>授权状态</legend>{["enabled", "disabled"].map((value) => <label className={state === value ? "is-active" : ""} key={value}><input type="radio" name="state" value={value} checked={state === value} onChange={() => setState(value)} disabled={readOnly} /><span>{entitlementStateLabel(value)}</span></label>)}</fieldset>
      {product.kind === "model" ? <label className="ops-form-field"><span>{pointBilling ? "企业单价（积分）" : "历史企业单价（分）"}</span><input type="number" min="1" step="1" value={priceAmount} onChange={(event) => setPriceAmount(event.target.value)} placeholder="只填写模型当前计费方式的整数单价" disabled={readOnly} /></label> : null}
      <div className="ops-form-grid"><label className="ops-form-field"><span>调用额度</span><input type="number" min="0" value={quota} onChange={(event) => setQuota(event.target.value)} placeholder="留空表示不单独限制" disabled={readOnly} /></label><label className="ops-form-field"><span>并发数</span><input type="number" min="1" value={concurrency} onChange={(event) => setConcurrency(event.target.value)} placeholder="留空使用平台默认" disabled={readOnly} /></label></div>
      <div className="ops-form-grid"><label className="ops-form-field"><span>生效时间</span><input type="datetime-local" value={effectiveAt} onChange={(event) => setEffectiveAt(event.target.value)} disabled={readOnly} /></label><label className="ops-form-field"><span>到期时间</span><input type="datetime-local" min={effectiveAt || undefined} value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} disabled={readOnly} /></label></div>
      {product.kind === "model" ? <label className="ops-form-field"><span>能力限制（JSON，可选）</span><textarea rows="4" value={capabilityLimit} onChange={(event) => setCapabilityLimit(event.target.value)} placeholder={'例如 {"max_images": 4, "resolutions": ["720p"]}；只能收窄目录能力'} disabled={readOnly} /></label> : null}
      {!readOnly ? <label className="ops-form-field"><span>变更原因 <b>必填</b></span><textarea rows="4" maxLength="300" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明合同、套餐或运营依据；原因会写入审计日志" /></label> : null}
    </Drawer>
  );
}

export function BatchPreviewDrawer({ preview, companies, products, onClose, onConfirm, busy }) {
  const [reason, setReason] = useState("");
  const operationLabel = { enable: "批量开通", disable: "批量停用", copy: "复制企业配置", template: "套用套餐模板" }[preview.mode] || preview.mode;
  return (
    <Drawer title="确认批量权益变更" detail={`${operationLabel} · 提交前核对影响范围`} onClose={onClose} wide footer={<><button className="ops-secondary-button" type="button" onClick={onClose}>返回修改</button><button className="ops-primary-button" type="button" onClick={() => onConfirm({ ...preview, reason })} disabled={busy || !reason.trim() || !preview.changedCount}><Check size={16} />确认提交 {preview.changedCount} 项变更</button></>}>
      <div className="ops-impact-summary"><span><small>企业</small><strong>{preview.companyCount}</strong></span><span><small>权益项</small><strong>{preview.productCount}</strong></span><span><small>预计变更</small><strong>{preview.changedCount}</strong></span><span><small>保持不变</small><strong>{preview.unchangedCount}</strong></span></div>
      <div className="ops-callout is-warning"><WarningCircle size={21} /><div><strong>本次操作会写入不可变审计记录</strong><span>历史记录不删除；需要恢复时使用审计差异创建反向变更。</span></div></div>
      <div className="ops-impact-list">
        {(preview.changes || []).slice(0, 80).map((change, index) => <div key={`${change.companyId}-${change.productId}-${index}`}><strong>{change.companyName || companies.find((item) => item.id === change.companyId)?.name}</strong><span>{change.productName || products.find((item) => item.id === change.productId)?.name}</span><em>{entitlementStateLabel(change.previousState)} <ArrowRight size={13} /> {entitlementStateLabel(change.nextState)}</em></div>)}
        {!preview.changes?.length ? <EmptyState title="没有需要提交的变化" detail="所选企业当前配置与目标状态一致。" /> : null}
      </div>
      <label className="ops-form-field"><span>变更原因 <b>必填</b></span><textarea rows="4" maxLength="300" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="填写套餐、合同或审批单依据" /></label>
    </Drawer>
  );
}

export function AuditDrawer({ event, onClose, onRollback, busy }) {
  const [reason, setReason] = useState("");
  const result = auditResultMeta(event.result);
  return (
    <Drawer title={event.actionLabel} detail={`${formatDateTime(event.occurredAt)} · ${event.actorName}`} onClose={onClose} wide footer={<><button className="ops-secondary-button" type="button" onClick={onClose}>关闭</button><button className="ops-secondary-button" type="button" onClick={() => onRollback(event, reason)} disabled={busy || !reason.trim() || !onRollback}><ArrowClockwise size={16} />创建反向变更</button></>}>
      <div className="ops-detail-grid"><span><small>目标</small><strong>{event.targetLabel}</strong></span><span><small>结果</small><strong>{result.label}</strong></span><span><small>动作代码</small><strong>{event.action}</strong></span><span><small>审计 ID</small><strong>{event.id}</strong></span></div>
      <div className="ops-drawer-section"><span className="ops-field-label">变更原因</span><p>{event.reason || "未填写"}</p></div>
      <div className="ops-drawer-section"><span className="ops-field-label">字段差异</span><AuditDiff before={event.before} after={event.after} /></div>
      <div className="ops-callout"><Receipt size={21} /><div><strong>回滚线索</strong><span>{event.rollbackHint || "根据变更前快照创建新的反向操作。"}</span></div></div>
      {onRollback ? <label className="ops-form-field"><span>反向变更原因 <b>必填</b></span><textarea rows="3" value={reason} onChange={(event_) => setReason(event_.target.value)} placeholder="说明为什么需要恢复，并关联原审计 ID" /></label> : null}
    </Drawer>
  );
}

export function AdminAccessDrawer({ admin, catalog, onClose, onSave, busy }) {
  const [selected, setSelected] = useState(new Set(admin.permissions || []));
  const [reason, setReason] = useState("");
  const groups = catalog.reduce((result, permission) => ({ ...result, [permission.group]: [...(result[permission.group] || []), permission] }), {});
  return (
    <Drawer title={`配置权限 · ${admin.name}`} detail={`${admin.email} · ${admin.roleLabel}`} onClose={onClose} wide footer={<><button className="ops-secondary-button" type="button" onClick={onClose}>取消</button><button className="ops-primary-button" type="button" onClick={() => onSave(admin, Array.from(selected), reason)} disabled={busy || !reason.trim()}><Check size={16} />保存权限</button></>}>
      <div className="ops-permission-groups">
        {Object.entries(groups).map(([group, permissions]) => <fieldset key={group}><legend>{group}</legend>{permissions.map((permission) => <label key={permission.key}><input type="checkbox" checked={selected.has(permission.key)} onChange={(event) => setSelected(toggleSetValue(selected, permission.key, event.target.checked))} /><span><strong>{permission.label}</strong><small>{permission.key}</small></span></label>)}</fieldset>)}
      </div>
      <label className="ops-form-field"><span>变更原因 <b>必填</b></span><textarea rows="4" maxLength="300" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明职责变化、审批依据和有效范围" /></label>
    </Drawer>
  );
}

export function ExceptionCenterDrawer({ items, onClose, onSelect }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [priority, setPriority] = useState("all");
  const filtered = filterExceptions(items, { query, status, priority });
  return (
    <Drawer title="异常处理中心" detail="按优先级、状态和责任人统一处理平台异常。" onClose={onClose} wide>
      <div className="ops-filterbar">
        <label className="ops-search"><span className="sr-only">搜索异常</span><MagnifyingGlass size={16} aria-hidden="true" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索异常、负责人或类型" /></label>
        <label><span className="sr-only">筛选异常优先级</span><Funnel size={15} aria-hidden="true" /><select value={priority} onChange={(event) => setPriority(event.target.value)}><option value="all">全部优先级</option><option value="P1">P1</option><option value="P2">P2</option><option value="P3">P3</option></select></label>
        <label><span className="sr-only">筛选异常状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">全部状态</option><option value="open">待处理</option><option value="investigating">处理中</option><option value="resolved">已解决</option></select></label>
        <span>{filtered.length} 项</span>
      </div>
      <div className="ops-exception-center-list">
        {filtered.map((item) => <button type="button" key={item.id} onClick={() => onSelect(item)}><Priority value={item.priority} /><span><strong>{item.title}</strong><small>{item.description}</small><em>{item.owner || "未分派"} · {item.duration || formatTime(item.occurredAt)}</em></span><StatusPill value={item.status} label={exceptionStatusLabel(item.status)} /><ArrowRight size={16} /></button>)}
        {!filtered.length ? <EmptyState title="没有匹配的异常" /> : null}
      </div>
    </Drawer>
  );
}
