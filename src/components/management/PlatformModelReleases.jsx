import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  CaretDown,
  CheckCircle,
  ClockCountdown,
  Coins,
  LockKey,
  ShieldCheck,
  UserCircle,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  buildCapabilityApprovalPayload,
  buildModelCapabilityReleaseRows,
  buildPersonalModelGrantBatchChange,
  buildPersonalModelGrantPayload,
  capabilityChangeLabel,
  capabilityReleasePresentation,
  normalizePersonalModelGrantCatalog,
  normalizeCapabilityHistory,
  serializeCapabilityValue,
  shortRevision,
} from "../../admin/modelCapabilityReleases.js";
import {
  ModelCapabilitySummary,
  PrimaryButton,
  StatusPill,
} from "./ManagementPrimitives.jsx";
import { shortDate } from "./managementPresentation.js";

function ErrorState({ status, message, onRetry }) {
  const forbidden = Number(status) === 403;
  return (
    <div className="model-release-state is-error" role="alert">
      {forbidden ? <LockKey size={22} aria-hidden="true" /> : <WarningCircle size={22} aria-hidden="true" />}
      <div>
        <strong>{forbidden ? "没有 Relay 能力目录读取权限" : "Relay 能力目录不可用"}</strong>
        <p>{message || (forbidden ? "请由平台所有者授予模型读取权限后重试。" : "请检查 Platform 到 Relay 的服务连接。")}</p>
      </div>
      {onRetry && <button type="button" onClick={onRetry}><ArrowClockwise size={15} /> 重新读取</button>}
    </div>
  );
}

const ROUTE_EVIDENCE_LABELS = Object.freeze({
  ready: "测试证据就绪",
  blocked: "路由仍被阻塞",
  missing: "尚无路由证据",
  revision_drift: "证据版本已漂移",
  unavailable: "证据服务不可用",
});

function RelayRouteEvidence({ relay, compact = false }) {
  const status = relay?.routeEvidenceStatus || "unavailable";
  const ready = status === "ready";
  const blockers = relay?.routeEvidenceBlockers || [];
  return (
    <div className={`relay-route-evidence is-${status}${compact ? " is-compact" : ""}`}>
      <div className="relay-route-evidence-heading">
        {ready ? <CheckCircle size={18} aria-hidden="true" /> : <WarningCircle size={18} aria-hidden="true" />}
        <div>
          <strong>{ROUTE_EVIDENCE_LABELS[status] || ROUTE_EVIDENCE_LABELS.unavailable}</strong>
          <small>{ready ? "精确绑定当前能力、路由、渠道与凭据版本" : "未就绪时审批、发布和权益开通全部拒绝"}</small>
        </div>
      </div>
      <dl>
        <div><dt>路由</dt><dd>{relay?.routeCount ?? "—"}</dd></div>
        <div><dt>已启用</dt><dd>{relay?.enabledRouteCount ?? "—"}</dd></div>
        <div><dt>已验收</dt><dd>{relay?.acceptedRouteCount ?? "—"}</dd></div>
        <div><dt>新鲜成功</dt><dd>{relay?.freshTestCount ?? "—"}</dd></div>
      </dl>
      {!compact && (
        <div className="relay-route-evidence-revisions">
          <span><small>路由发布</small><code title={relay?.routingReleaseSha256 || ""}>{shortRevision(relay?.routingReleaseSha256)}</code></span>
          <span><small>模型发布</small><code title={relay?.modelReleaseId || ""}>{relay?.modelReleaseId || "—"}</code></span>
          <span><small>最近成功</small><strong>{relay?.latestSuccessfulTestAt ? shortDate(relay.latestSuccessfulTestAt) : "—"}</strong></span>
        </div>
      )}
      {blockers.length ? <ul>{blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul> : null}
      {!ready ? <a href="/platform?ops_module=channels">前往 Relay 控制面执行精确路由测试</a> : null}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="model-release-state is-loading" role="status" aria-live="polite">
      <span className="model-release-spinner" aria-hidden="true" />
      <div><strong>正在读取 Relay 模型与发布证据</strong><p>后台对账负责生成未发布草稿；打开本页面不会触发任何写入。</p></div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="model-release-state is-empty">
      <ShieldCheck size={24} aria-hidden="true" />
      <div><strong>Relay 尚未声明公共模型</strong><p>在 Relay 新增公共模型后，Platform 会自动生成未发布草稿；这里不需要手工注册映射。</p></div>
    </div>
  );
}

function ReconcileNotice({ state, onRetry, canRetry }) {
  if (state?.status !== "error") return null;
  return (
    <div className="model-release-state is-error" role="alert">
      <WarningCircle size={22} aria-hidden="true" />
      <div>
        <strong>模型目录对账未完成</strong>
        <p>{state.error || "Platform 暂时无法把 Relay 新模型同步为未发布草稿。"} 现有目录仍可查看；手动重新同步不会重复创建草稿，也不会自动发布或开放给客户。</p>
      </div>
      {canRetry ? <button type="button" onClick={onRetry}><ArrowClockwise size={15} /> 重新同步</button> : null}
    </div>
  );
}

function ReleaseStatus({ row }) {
  const presentation = capabilityReleasePresentation(row.releaseState);
  return <StatusPill value={presentation.tone} label={presentation.label} />;
}

function CapabilityDiff({ diff }) {
  if (!diff.changes.length) {
    return (
      <div className={`model-capability-diff-empty is-${diff.classification}`}>
        <strong>{diff.label}</strong>
        <p>{diff.classification === "unchanged"
          ? "公共能力声明没有变化；仅更换账号、Key、渠道或路由时不需要重新审批。"
          : "当前后端没有返回逐字段差异。请按完整能力声明复核，不要把空差异当作已验收。"}</p>
      </div>
    );
  }
  return (
    <div className="model-capability-diff">
      <header><strong>{diff.label}</strong><span>{diff.changes.length} 项</span></header>
      <ul>
        {diff.changes.map((change) => (
          <li key={change.id} data-change-kind={change.kind}>
            <span>{capabilityChangeLabel(change)}</span>
            <code>{change.path}</code>
            <p><del>{serializeCapabilityValue(change.before)}</del><b aria-hidden="true">→</b><ins>{serializeCapabilityValue(change.after)}</ins></p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ApprovalHistory({ state, onLoad }) {
  if (state.status === "idle") {
    return <button className="model-release-history-trigger" type="button" onClick={onLoad}>读取审批历史</button>;
  }
  if (state.status === "loading") {
    return <p className="model-release-history-note" role="status">正在读取不可变审批记录…</p>;
  }
  if (state.status === "error") {
    return (
      <div className="model-release-history-error" role="alert">
        <p>{state.statusCode === 403 ? "没有审批历史读取权限。" : state.message}</p>
        <button type="button" onClick={onLoad}>重试</button>
      </div>
    );
  }
  if (!state.items.length) {
    return <p className="model-release-history-note">尚无审批记录。首次审批后会在这里保留原因、操作者与请求编号。</p>;
  }
  return (
    <ol className="model-release-history">
      {state.items.map((entry) => (
        <li key={entry.id}>
          <span aria-hidden="true" />
          <div>
            <header><strong>{entry.eventType === "candidate_sync" ? "候选同步" : "能力审批"}</strong><time dateTime={entry.createdAt || undefined}>{shortDate(entry.createdAt)}</time></header>
            <p>{entry.reason || "未记录原因（历史兼容记录）"}</p>
            <small>操作者 {entry.actorKind === "system" ? `系统任务 · ${entry.actorKey || "Platform"}` : entry.actorUserId || "未知账号"} · {shortRevision(entry.beforeRevision)} → {shortRevision(entry.afterRevision)}</small>
            {entry.requestId && <code title={entry.requestId}>请求 {shortRevision(entry.requestId)}</code>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function CapabilityReviewDrawer({ row, catalogRevision, busy, canManage, onApprove, onClose, onRefresh, onSyncCandidate }) {
  const helpId = useId();
  const drawerRef = useRef(null);
  const [reason, setReason] = useState("");
  const [state, setState] = useState({ status: "idle", message: "" });
  const pending = state.status === "submitting";

  useEffect(() => {
    const previous = globalThis.document?.activeElement;
    drawerRef.current?.querySelector("textarea")?.focus();
    return () => {
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  const handleKeyDown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = drawerRef.current?.querySelectorAll(
      'button:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    if (pending) return;
    const action = event.nativeEvent?.submitter?.value || "approve";
    let payload;
    try {
      payload = buildCapabilityApprovalPayload({
        model: row.model,
        relay: row.relay,
        catalogRevision,
        reason,
        requireRouteEvidence: action === "approve",
      });
    } catch (error) {
      setState({ status: "error", message: error.message });
      return;
    }
    setState({ status: "submitting", message: "" });
    try {
      const result = action === "sync"
        ? await onSyncCandidate(row.model, payload)
        : await onApprove(row.model, payload);
      setState({
        status: result?.changed === false ? "duplicate" : "success",
        message: result?.changed === false
          ? "该能力版本已由另一会话记录，无需重复提交。"
          : action === "sync"
            ? "候选版本已记录。请关闭后重新打开，复核刷新后的差异再审批。"
            : "审批已写入，目录刷新后才会允许下一步发布。",
      });
      setReason("");
    } catch (error) {
      setState({
        status: error?.status === 409 ? "conflict" : error?.status === 403 ? "forbidden" : "error",
        message: error?.status === 409
          ? "候选版本或 Platform 能力版本已经变化。审批原因已保留，请刷新证据后重新核对。"
          : error?.message || "审批失败，请稍后重试。",
      });
    }
  };

  return (
    <div className="control-drawer-layer model-release-review-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside
        ref={drawerRef}
        className="control-drawer is-wide model-release-review-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${helpId}-title`}
        aria-describedby={helpId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <header>
          <div><small>模型能力发布审阅</small><h2 id={`${helpId}-title`}>{row.model?.display_name || row.relay?.relayModelId}</h2><p id={helpId}>固定的是公共能力修订，不是某个 new-api 账号、Key 或渠道。</p></div>
          <button className="control-drawer-close" data-icon-only="true" type="button" onClick={onClose} aria-label="关闭能力审阅">×</button>
        </header>
        <div className="model-release-review-content">
          <section className="model-release-review-summary">
            <div><span>公共模型名</span><code>{row.model?.slug || row.relay?.relayModelId}</code></div>
            <div><span>Relay 候选</span><code title={row.relay?.candidateRevision}>{shortRevision(row.relay?.candidateRevision)}</code></div>
            <div><span>当前批准</span><code title={row.relay?.approvedRevision}>{shortRevision(row.relay?.approvedRevision)}</code></div>
            <div><span>Platform 能力</span><strong>v{row.model?.capability_version}</strong></div>
          </section>
          <RelayRouteEvidence relay={row.relay} />
          <section className="model-release-review-diff">
            <header><div><strong>逐字段能力差异</strong><p>扩展项必须由真实供应商证据支撑；Platform 公开能力不得超出 Relay 上限。</p></div><span>{row.relay?.capabilityDiff?.label}</span></header>
            <CapabilityDiff diff={row.relay.capabilityDiff} />
          </section>
          <section className="model-release-review-capabilities">
            <div><strong>Platform 将公开的能力</strong><ModelCapabilitySummary model={row.model} compact /></div>
            <div><strong>Relay 当前物理上限</strong><ModelCapabilitySummary model={{ effective_capabilities: row.relay.capabilities }} compact /></div>
          </section>
          <form className="model-release-approval" onSubmit={submit}>
            <label>
              <span>变更原因与验收证据</span>
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                minLength={3}
                maxLength={500}
                required
                aria-describedby={helpId}
                placeholder="例如：已按官方文档与真实 canary 验证文生图输入、2048×2048 上限及单张输出。"
              />
              <small>{Array.from(reason).length}/500 · 同步候选和审批都会写入不可变历史</small>
            </label>
            {state.status !== "idle" && state.status !== "submitting" && (
              <div className={`model-release-feedback is-${state.status}`} role={state.status === "success" || state.status === "duplicate" ? "status" : "alert"}>
                {state.status === "success" || state.status === "duplicate" ? <CheckCircle size={17} /> : <WarningCircle size={17} />}
                <span>{state.message}</span>
                {state.status === "conflict" && <button type="button" onClick={onRefresh}>刷新证据</button>}
              </div>
            )}
            <footer>
              <button type="button" onClick={onClose} disabled={pending}>取消</button>
              <button type="submit" name="reviewAction" value="sync" disabled={!canManage || busy || pending || Array.from(reason.trim()).length < 3}>
                {pending ? "正在提交…" : "先记录候选"}
              </button>
              <button className="is-primary" type="submit" name="reviewAction" value="approve" disabled={!row.canApprove || !canManage || busy || pending || Array.from(reason.trim()).length < 3} title={!row.canApprove ? "当前候选尚不满足审批条件" : undefined}>
                {pending ? "正在提交…" : "批准候选版本"}
              </button>
            </footer>
          </form>
        </div>
      </aside>
    </div>
  );
}

function PersonalGrantEditor({ row, busy, canManage, onClose, onRefresh, onSave }) {
  const titleId = useId();
  const drawerRef = useRef(null);
  const [enabled, setEnabled] = useState(Boolean(row.enabled));
  const [pricePoints, setPricePoints] = useState(String(row.pricePoints || 1));
  const [reason, setReason] = useState("");
  const [feedback, setFeedback] = useState({ status: "idle", message: "" });
  const pending = feedback.status === "submitting";

  useEffect(() => {
    const previous = globalThis.document?.activeElement;
    drawerRef.current?.querySelector("input[type='number']")?.focus();
    return () => {
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  const handleKeyDown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = drawerRef.current?.querySelectorAll(
      'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    if (pending) return;
    if (enabled && !row.canEnable) {
      setFeedback({ status: "validation", message: `暂不能开通：${row.blockerLabels.filter((label) => label !== "积分价格尚未配置").join("、") || "发布证据不完整"}。` });
      return;
    }
    let payload;
    try {
      payload = buildPersonalModelGrantPayload({ grant: row, enabled, pricePoints, reason });
    } catch (error) {
      setFeedback({ status: "validation", message: error.message });
      return;
    }
    setFeedback({ status: "submitting", message: "" });
    try {
      await onSave(row, payload);
      setFeedback({ status: "success", message: enabled ? "已开通个人零售分发。" : "已停用个人零售分发。" });
      onClose();
    } catch (error) {
      const status = Number(error?.status || 0);
      setFeedback({
        status: status === 403 ? "forbidden" : status === 409 ? "conflict" : status === 422 ? "validation" : "error",
        message: status === 403
          ? "没有个人零售分发管理权限，请由平台所有者授权。"
          : status === 409
            ? "模型能力、发布状态或价格快照已经变化。原因已保留，请刷新后复核。"
            : status === 422
              ? "积分价格、能力限制或变更原因未通过服务端校验。"
              : error?.message || "个人零售分发保存失败。",
      });
    }
  };

  const priceUnit = row.billingMode === "per_second" ? "积分 / 秒" : "积分 / 张（条）";
  return (
    <div className="control-drawer-layer personal-grant-editor-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside
        ref={drawerRef}
        className="control-drawer personal-grant-editor"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <header>
          <div><small>个人工作区模型分发</small><h2 id={titleId}>{row.model_display_name}</h2><p>个人积分价格与企业合同价完全隔离；保存时固定当前能力版本并写入平台审计。</p></div>
          <button className="control-drawer-close" data-icon-only="true" type="button" onClick={onClose} aria-label="关闭个人分发编辑">×</button>
        </header>
        <form className="control-form personal-grant-form" onSubmit={submit}>
          <section className="personal-grant-editor-evidence" aria-label="模型分发证据">
            <div><span>公共模型名</span><code>{row.model_slug}</code></div>
            <div><span>能力版本</span><strong>v{row.capability_version}</strong></div>
            <div><span>能力来源</span><code title={row.relay_capability_revision || ""}>{row.capabilitySource} · {shortRevision(row.relay_capability_revision)}</code></div>
            <div><span>目录状态</span><StatusPill value={row.model_status} /></div>
          </section>
          {!!row.blockerLabels.length && (
            <div className="personal-grant-blockers" role={row.enabled ? "alert" : "status"}>
              <WarningCircle size={18} aria-hidden="true" />
              <div><strong>{row.enabled ? "现有分发存在阻塞" : "开通前仍需处理"}</strong><p>{row.blockerLabels.join("、")}</p></div>
            </div>
          )}
          <label className="personal-grant-toggle">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
              disabled={!canManage || (!row.enabled && !row.canEnable)}
            />
            <span><strong>允许个人工作区使用</strong><small>关闭只阻止新任务，不改写历史任务、积分账本或作品。</small></span>
          </label>
          <label>
            <span>个人积分价格</span>
            <div className="personal-grant-price-control"><input type="number" min="1" step="1" inputMode="numeric" value={pricePoints} onChange={(event) => setPricePoints(event.target.value)} required /><span>{priceUnit}</span></div>
            <small>只允许正整数；系统按模型目录的 {row.billingMode === "per_second" ? "按秒" : "按条"} 方式扣除个人积分。</small>
          </label>
          <section className="personal-grant-effective-capability">
            <header><strong>个人工作区有效能力</strong><span>服务端计算，只可等于或窄于已批准上限</span></header>
            <ModelCapabilitySummary model={{ effective_capabilities: row.effective_capabilities }} compact />
          </section>
          <label>
            <span>变更原因</span>
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} minLength={3} maxLength={500} required placeholder="例如：首批个人用户灰度开放，按真实供应商成本和目标毛利设置 3 积分/张。" />
            <small>{Array.from(reason).length}/500 · 必填，提交后进入操作审计</small>
          </label>
          {feedback.status !== "idle" && feedback.status !== "submitting" && (
            <div className={`control-drawer-error personal-grant-feedback is-${feedback.status}`} role={feedback.status === "success" ? "status" : "alert"}>
              <span>{feedback.message}</span>
              {feedback.status === "conflict" && <button type="button" onClick={onRefresh}>刷新</button>}
            </div>
          )}
          <footer>
            <button type="button" onClick={onClose} disabled={pending}>取消</button>
            <button className="is-primary" type="submit" disabled={!canManage || busy || pending || Array.from(reason.trim()).length < 3}>{pending ? "正在保存…" : "保存分发设置"}</button>
          </footer>
        </form>
      </aside>
    </div>
  );
}

function PersonalGrantBatchEditor({ rows, busy, canManage, onClose, onExecute, onPreview, onRefresh }) {
  const titleId = useId();
  const drawerRef = useRef(null);
  const [enabled, setEnabled] = useState(true);
  const [pricePoints, setPricePoints] = useState(String(rows[0]?.pricePoints || 1));
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState(null);
  const [pending, setPending] = useState("");
  const [feedback, setFeedback] = useState({ status: "idle", message: "" });
  const batchIdempotencyKeyRef = useRef("");
  const canEnable = rows.every((row) => row.enabled || row.canEnable);

  useEffect(() => {
    const previous = globalThis.document?.activeElement;
    drawerRef.current?.focus();
    return () => {
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  useEffect(() => {
    setPreview(null);
    batchIdempotencyKeyRef.current = "";
    setFeedback({ status: "idle", message: "" });
  }, [enabled, pricePoints, reason]);

  const changes = () => rows.map((grant) => buildPersonalModelGrantBatchChange({
    grant,
    enabled,
    pricePoints,
  }));

  const previewBatch = async () => {
    setPending("preview");
    setFeedback({ status: "idle", message: "" });
    try {
      const result = await onPreview(changes());
      batchIdempotencyKeyRef.current = globalThis.crypto?.randomUUID?.()
        || `personal-batch-${Date.now()}`;
      setPreview(result);
      setFeedback({
        status: "success",
        message: `影响 ${result.changed_cells || 0}/${result.total_cells || rows.length} 个模型；确认后作为一个原子批次提交。`,
      });
    } catch (error) {
      setFeedback({
        status: error?.status === 409 ? "conflict" : "error",
        message: error?.message || "批量分发预览失败。",
      });
    } finally {
      setPending("");
    }
  };

  const executeBatch = async () => {
    if (!preview?.snapshot || !batchIdempotencyKeyRef.current) return;
    setPending("execute");
    setFeedback({ status: "idle", message: "" });
    try {
      await onExecute({
        changes: changes(),
        expectedSnapshot: preview.snapshot,
        reason,
        idempotencyKey: batchIdempotencyKeyRef.current,
      });
      onClose();
    } catch (error) {
      if (error?.status === 409) {
        setPreview(null);
        batchIdempotencyKeyRef.current = "";
      }
      setFeedback({
        status: error?.status === 409 ? "conflict" : "error",
        message: error?.message || "批量分发提交失败。",
      });
    } finally {
      setPending("");
    }
  };

  const handleKeyDown = (event) => {
    if (event.key === "Escape" && !pending) {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = drawerRef.current?.querySelectorAll(
      'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="control-drawer-layer model-release-review-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}>
      <aside
        ref={drawerRef}
        className="control-drawer personal-grant-editor personal-grant-batch-editor"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <header>
          <div><small>受控个人零售发布</small><h2 id={titleId}>批量分发 {rows.length} 个模型</h2><p>先生成服务端影响预览，再以同一快照、原因和幂等键原子提交；任何一个模型版本冲突都会整批拒绝。</p></div>
          <button className="control-drawer-close" data-icon-only="true" type="button" onClick={onClose} aria-label="关闭批量个人分发">×</button>
        </header>
        <div className="control-form personal-grant-form">
          <section className="personal-grant-batch-selection" aria-label="已选择模型">
            {rows.map((row) => <div key={row.model_id}><strong title={row.model_display_name}>{row.model_display_name}</strong><code title={row.model_slug}>{row.model_slug}</code><span>v{row.capability_version}</span></div>)}
          </section>
          <label className="personal-grant-toggle">
            <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} disabled={!canManage || (!canEnable && !enabled)} />
            <span><strong>{enabled ? "批量开通个人工作区" : "批量停止个人新任务"}</strong><small>关闭不删除历史作品和积分账；开通仍要求每个模型具备发布、审批与路由证据。</small></span>
          </label>
          <label>
            <span>统一积分数字</span>
            <div className="personal-grant-price-control"><input type="number" min="1" step="1" inputMode="numeric" value={pricePoints} onChange={(event) => setPricePoints(event.target.value)} required /><span>按各模型目录单位</span></div>
            <small>按秒模型解释为积分/秒，按条模型解释为积分/张（条）；不会混用企业人民币合同价。</small>
          </label>
          <label>
            <span>批量变更原因</span>
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} minLength={3} maxLength={500} required placeholder="例如：Seedream 首批个人用户灰度开放，统一按验证后的供应商成本设置零售价。" />
            <small>{Array.from(reason).length}/500 · 将同时写入批次审计和每个模型的变更审计</small>
          </label>
          {preview && (
            <section className="personal-grant-batch-preview" aria-label="批量影响预览">
              <strong>快照已生成</strong><span>{preview.changed_cells || 0} 项变更 · {preview.created_cells || 0} 项新建 · {preview.updated_cells || 0} 项更新</span><code title={preview.snapshot}>{shortRevision(preview.snapshot)}</code>
            </section>
          )}
          {feedback.status !== "idle" && (
            <div className={`control-drawer-error personal-grant-feedback is-${feedback.status}`} role={feedback.status === "success" ? "status" : "alert"}>
              <span>{feedback.message}</span>
              {feedback.status === "conflict" && <button type="button" onClick={onRefresh}>刷新目录</button>}
            </div>
          )}
          <footer>
            <button type="button" onClick={onClose} disabled={!!pending}>取消</button>
            <button type="button" onClick={previewBatch} disabled={!canManage || busy || !!pending || !Number.isInteger(Number(pricePoints)) || Number(pricePoints) < 1}>{pending === "preview" ? "正在预览…" : preview ? "重新预览" : "预览影响"}</button>
            <button className="is-primary" type="button" onClick={executeBatch} disabled={!preview?.snapshot || !canManage || busy || !!pending || Array.from(reason.trim()).length < 3}>{pending === "execute" ? "正在提交…" : "确认原子分发"}</button>
          </footer>
        </div>
      </aside>
    </div>
  );
}

function PersonalModelDistribution({ data, busy, canManage, demoMode, onRefresh, onSave, onPreviewBatch, onSaveBatch }) {
  const [editingModelId, setEditingModelId] = useState("");
  const [selectedModelIds, setSelectedModelIds] = useState([]);
  const [batchOpen, setBatchOpen] = useState(false);
  const catalog = useMemo(
    () => normalizePersonalModelGrantCatalog(
      data.personalModelGrants,
      data.adminModels,
      data.relayModelAudit,
    ),
    [data.personalModelGrants, data.adminModels, data.relayModelAudit],
  );
  const enabledCount = catalog.items.filter((item) => item.enabled).length;
  const editing = catalog.items.find((item) => item.model_id === editingModelId) || null;
  const selectedRows = catalog.items.filter((item) => selectedModelIds.includes(item.model_id));
  const toggleSelection = (modelId) => setSelectedModelIds((current) => (
    current.includes(modelId)
      ? current.filter((item) => item !== modelId)
      : [...current, modelId]
  ));

  return (
    <section className="control-section personal-model-distribution" aria-labelledby="personal-model-distribution-title">
      <div className="control-section-title">
        <div><h2 id="personal-model-distribution-title">个人零售分发</h2><p>为个人工作区逐模型配置开关与积分价。企业授权和公司钱包价格不受影响。</p></div>
        <div className="personal-model-distribution-toolbar"><span>{enabledCount}/{catalog.items.length} 个已开通</span><a href="/platform?ops_module=entitlements">企业批量分发</a><button type="button" disabled={!canManage || !selectedRows.length} onClick={() => setBatchOpen(true)}>批量个人分发 ({selectedRows.length})</button></div>
      </div>
      {demoMode ? (
        <div className="model-release-state is-empty"><UserCircle size={23} /><div><strong>演示模式不展示虚构个人分发</strong><p>切换到正式平台所有者账号后，页面会读取生产 personal-model-grants 目录。</p></div></div>
      ) : catalog.error ? (
        <ErrorState status={catalog.errorStatus} message={catalog.error} onRetry={onRefresh} />
      ) : !catalog.items.length ? (
        <div className="model-release-state is-empty"><UserCircle size={23} /><div><strong>没有可配置的模型</strong><p>Relay 新公共模型会自动生成草稿并出现在这里；审核和发布完成前不会向个人用户开放。</p></div></div>
      ) : (
        <div className="personal-model-distribution-list">
          {catalog.items.map((item) => (
            <article key={item.model_id} className={item.enabled ? "is-enabled" : "is-disabled"}>
              <label className="personal-model-distribution-select"><input type="checkbox" checked={selectedModelIds.includes(item.model_id)} onChange={() => toggleSelection(item.model_id)} disabled={!canManage} /><span className="sr-only">选择 {item.model_display_name}</span></label>
              <div className="personal-model-distribution-identity"><strong>{item.model_display_name}</strong><code>{item.model_slug}</code><small>能力 v{item.capability_version}</small></div>
              <div className="personal-model-distribution-state"><StatusPill value={item.enabled ? "active" : "disabled"} label={item.enabled ? "个人已开通" : "个人未开通"} /><small>{item.capabilitySource}</small></div>
              <div className="personal-model-distribution-price"><Coins size={18} aria-hidden="true" /><span><strong>{item.pricePoints ?? "未定价"}</strong><small>{item.billingMode === "per_second" ? "积分 / 秒" : "积分 / 张（条）"}</small></span></div>
              <div className="personal-model-distribution-capability"><ModelCapabilitySummary model={{ effective_capabilities: item.effective_capabilities }} compact /></div>
              <div className="personal-model-distribution-blocker">{item.blockerLabels.length ? <><WarningCircle size={16} /><span>{item.blockerLabels.join("、")}</span></> : <><CheckCircle size={16} /><span>发布证据完整</span></>}</div>
              <button type="button" onClick={() => setEditingModelId(item.model_id)} disabled={!canManage}>配置分发</button>
            </article>
          ))}
        </div>
      )}
      {editing && <PersonalGrantEditor row={editing} busy={busy} canManage={canManage} onClose={() => setEditingModelId("")} onRefresh={onRefresh} onSave={onSave} />}
      {batchOpen && selectedRows.length > 0 && <PersonalGrantBatchEditor rows={selectedRows} busy={busy} canManage={canManage} onClose={() => setBatchOpen(false)} onRefresh={onRefresh} onPreview={onPreviewBatch} onExecute={onSaveBatch} />}
    </section>
  );
}

function CapabilityReleaseRecord({
  row,
  busy,
  canManage,
  onLoadHistory,
  onOpenReview,
  setDrawer,
  setModelState,
}) {
  const initialHistory = row.relay?.approvalHistory || [];
  const [history, setHistory] = useState({
    status: initialHistory.length ? "success" : "idle",
    items: initialHistory,
    message: "",
    statusCode: 0,
  });
  const modelName = row.model?.display_name || row.relay?.relayModelId || "未命名模型";
  const publicId = row.model?.slug || row.relay?.relayModelId || "—";

  const loadHistory = async () => {
    if (!row.model || history.status === "loading") return;
    setHistory((current) => ({ ...current, status: "loading", message: "" }));
    try {
      const response = await onLoadHistory(row.model);
      setHistory({ status: "success", items: normalizeCapabilityHistory(response), message: "", statusCode: 0 });
    } catch (error) {
      setHistory({
        status: "error",
        items: [],
        message: error?.status === 404 ? "审批历史接口尚未部署；当前页面不会伪造历史记录。" : error?.message || "读取审批历史失败。",
        statusCode: Number(error?.status || 0),
      });
    }
  };

  return (
    <article className={`model-release-record is-${row.releaseState}`}>
      <header className="model-release-record-header">
        <div className="model-release-identity">
          <strong>{modelName}</strong>
          <code>{publicId}</code>
          <small>{row.model ? `Platform v${row.model.capability_version}` : "自动草稿尚未生成"}</small>
        </div>
        <div className="model-release-revisions" aria-label="能力修订">
          <span><small>候选</small><code title={row.relay?.candidateRevision || ""}>{shortRevision(row.relay?.candidateRevision)}</code></span>
          <b aria-hidden="true">→</b>
          <span><small>已批准</small><code title={row.relay?.approvedRevision || ""}>{shortRevision(row.relay?.approvedRevision)}</code></span>
        </div>
        <div className="model-release-status">
          <ReleaseStatus row={row} />
          {row.model && <StatusPill value={row.model.status || (row.model.active ? "published" : "disabled")} />}
          <small>{row.productionReady ? "已具备目录发布证据" : "尚未形成完整客户可用链路"}</small>
        </div>
        <div className="model-release-actions">
          {row.model && row.model.status !== "published" && (
            <button type="button" onClick={() => setDrawer({ type: "model", model: row.model })} disabled={!canManage}>编辑限制</button>
          )}
          {row.model && row.relay && (
            <button type="button" onClick={() => onOpenReview(row)} disabled={!canManage}>审阅候选</button>
          )}
          {row.model && (row.model.status === "draft" || row.model.status === "disabled") && (
            <button type="button" onClick={() => setModelState(row.model, "publish")} disabled={busy || !canManage || !row.canPublish} title={!row.canPublish ? "需先通过 Relay 能力验收" : undefined}>发布</button>
          )}
          {row.model?.status === "published" && (
            <button type="button" onClick={() => setModelState(row.model, "disable")} disabled={busy || !canManage}>下线</button>
          )}
        </div>
      </header>

      <details className="model-release-evidence">
        <summary><span>能力差异与发布证据</span><CaretDown size={16} aria-hidden="true" /></summary>
        <div className="model-release-evidence-body">
          <section>
            <header><strong>候选差异</strong><span>{row.relay?.capabilityDiff?.label || "无 Relay 证据"}</span></header>
            {row.relay ? <CapabilityDiff diff={row.relay.capabilityDiff} /> : <p className="model-release-evidence-note">Relay 公共目录没有这个模型，不能确认真实物理能力。</p>}
          </section>
          <section>
            <header><strong>当前公开能力</strong><span>Platform 只可收紧</span></header>
            {row.model
              ? <ModelCapabilitySummary model={row.model} compact />
              : row.relay
                ? <ModelCapabilitySummary model={{ effective_capabilities: row.relay.capabilities }} compact />
                : <p className="model-release-evidence-note">尚无能力声明。</p>}
            <p className="model-release-evidence-note">new-api 连通测试只证明凭据和协议可请求；它不等于能力边界、成本、产物转存和故障语义已经通过生产验收。</p>
          </section>
          <section>
            <header><strong>精确路由证据</strong><span>发布与分发门禁</span></header>
            <RelayRouteEvidence relay={row.relay} compact />
          </section>
          <section>
            <header><strong>审批记录</strong><span>原因与操作者</span></header>
            {row.model
              ? <ApprovalHistory state={history} onLoad={loadHistory} />
              : <p className="model-release-history-note">自动草稿同步完成后才能形成审批历史。</p>}
          </section>
        </div>
      </details>
    </article>
  );
}

export function PlatformModelReleases({
  data,
  loading,
  busy,
  canUsePlatformPermission,
  demoMode,
  approveRelayModelRevision,
  loadRelayCapabilityHistory,
  refreshModels,
  reconcileRelayModels,
  syncRelayModelCandidate,
  setDrawer,
  setModelState,
  savePersonalModelGrant,
  previewPersonalModelGrantBatch,
  savePersonalModelGrantBatch,
}) {
  const [reviewRow, setReviewRow] = useState(null);
  const release = useMemo(
    () => buildModelCapabilityReleaseRows(data.adminModels, data.relayModelAudit),
    [data.adminModels, data.relayModelAudit],
  );
  const canManage = canUsePlatformPermission("platform.models.manage");

  return (
    <>
      <section className="model-release-guide" aria-labelledby="model-release-guide-title">
        <header>
          <div><span>上线顺序</span><h2 id="model-release-guide-title">测试成功，不等于客户可用</h2></div>
          <p>Relay 新公共模型会自动生成未发布草稿；管理员只审核能力与路由证据、设置价格并发布。new-api 渠道、账号、Key、权重与故障切换始终留在 Relay 管理边界。</p>
        </header>
        <ol>
          <li><span>01</span><div><strong>渠道连通</strong><small>new-api 验证凭据与供应商协议</small></div></li>
          <li><span>02</span><div><strong>自动草稿</strong><small>Relay 公共别名自动进入 Platform 待审目录</small></div></li>
          <li><span>03</span><div><strong>差异审批</strong><small>Platform 复核收紧项并记录原因</small></div></li>
          <li><span>04</span><div><strong>定价发布</strong><small>审核通过后再配置价格与客户分发</small></div></li>
        </ol>
      </section>

      <section className="control-section model-release-ledger" aria-labelledby="model-release-ledger-title">
        <div className="control-section-title">
          <div><h2 id="model-release-ledger-title">模型发布账</h2><p>{release.catalogRevision ? `Relay 目录 ${shortRevision(release.catalogRevision)} · ` : ""}{release.counts.productionReady} 个已形成完整发布证据，{release.counts.pending || 0} 个等待审批。</p></div>
          <span>{release.counts.total} 个公共模型</span>
          <button
            type="button"
            onClick={reconcileRelayModels}
            disabled={busy || demoMode || !canManage}
            title={demoMode
              ? "演示模式不写入模型目录"
              : !canManage
                ? "需要 platform.models.manage 权限"
                : "立即执行一次幂等目录对账"}
          ><ArrowClockwise size={15} /> 手动重新同步</button>
        </div>
        {!loading && <ReconcileNotice state={data.relayModelReconcile} onRetry={reconcileRelayModels} canRetry={canManage && !demoMode && !busy} />}
        {loading ? <LoadingState /> : release.error ? <ErrorState status={release.errorStatus} message={release.error} onRetry={refreshModels} /> : !release.rows.length ? <EmptyState /> : (
          <div className="model-release-records">
            {release.rows.map((row) => (
              <CapabilityReleaseRecord
                key={row.key}
                row={row}
                busy={busy}
                canManage={canManage}
                onLoadHistory={loadRelayCapabilityHistory}
                onOpenReview={setReviewRow}
                setDrawer={setDrawer}
                setModelState={setModelState}
              />
            ))}
          </div>
        )}
      </section>
      <PersonalModelDistribution
        data={data}
        busy={busy}
        canManage={canManage}
        demoMode={demoMode}
        onRefresh={refreshModels}
        onSave={savePersonalModelGrant}
        onPreviewBatch={previewPersonalModelGrantBatch}
        onSaveBatch={savePersonalModelGrantBatch}
      />
      {reviewRow && (
        <CapabilityReviewDrawer
          key={`${reviewRow.key}:${reviewRow.relay?.candidateRevision || "candidate"}`}
          row={reviewRow}
          catalogRevision={release.catalogRevision}
          busy={busy}
          canManage={canManage}
          onApprove={approveRelayModelRevision}
          onClose={() => setReviewRow(null)}
          onRefresh={refreshModels}
          onSyncCandidate={syncRelayModelCandidate}
        />
      )}
    </>
  );
}
