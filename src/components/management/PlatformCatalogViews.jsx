import { useState } from "react";
import { Check, Copy, Key, Plus, WarningCircle } from "@phosphor-icons/react";
import {
  AuditChangeSummary,
  EmptyRows,
  PageHeader,
  PageLoadMore,
  PrimaryButton,
  StatusPill,
} from "./ManagementPrimitives.jsx";
import {
  RESOURCE_KIND_LABELS,
  shortDate,
} from "./managementPresentation.js";
import { safeId } from "./managementAccess.js";
import { PlatformModelReleases } from "./PlatformModelReleases.jsx";
import {
  billingAmountLabel,
  billingPresentationState,
  billingUnitLabel,
} from "../../billingPresentation.js";

export function CopyIdentifier({ value, label }) {
  const [state, setState] = useState("idle");
  const copy = async () => {
    try {
      if (!value || !globalThis.navigator?.clipboard?.writeText) {
        throw new Error("clipboard unavailable");
      }
      await globalThis.navigator.clipboard.writeText(String(value));
      setState("copied");
    } catch {
      setState("error");
    }
    globalThis.setTimeout?.(() => setState("idle"), 1800);
  };
  return (
    <span className={`control-copy-id is-${state}`}>
      <code title={String(value || "")}>{safeId(value)}</code>
      <button type="button" onClick={copy} aria-label={`复制${label}`}>
        {state === "copied" ? <Check size={14} aria-hidden="true" /> : state === "error" ? <WarningCircle size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
        <span aria-live="polite">{state === "copied" ? "已复制" : state === "error" ? "复制失败" : "复制"}</span>
      </button>
    </span>
  );
}

export function PlatformCompaniesView({
  data,
  busy,
  demoMode = false,
  ownerInvitationLinks,
  paginationBusyKey,
  canUsePlatformPermission,
  companyDashboardRow,
  copyOwnerInvitationLink,
  loadMore,
  makeOperationKey,
  openCompanyControl,
  setCompanyStatus,
  setDrawer,
}) {
  return (
    <div className="control-route is-platform-companies">
      <PageHeader eyebrow="客户生命周期" title="企业管理" detail="创建企业、启停访问、查看共享积分钱包，以及下发模型和功能权益。">
        <PrimaryButton onClick={() => setDrawer({ type: "company" })} disabled={!canUsePlatformPermission("platform.companies.manage")}><Plus size={16} /> 新建企业</PrimaryButton>
      </PageHeader>
      <section className="control-section">
        <div className="control-section-title"><div><h2>企业目录</h2><p>{data.companies?.total || 0} 家企业，所有变更都会进入平台审计日志。</p></div></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-companies-table">
            <thead><tr><th>企业</th><th>状态</th><th>老板账号</th><th>权益入账</th><th>可用权益</th><th>实际消费</th><th>创建时间</th><th className="is-actions">操作</th></tr></thead>
            <tbody>
              {(data.companies?.items || []).map((company) => {
                const summary = companyDashboardRow(company.id) || {};
                return (
                  <tr key={company.id}>
                    <td data-label="企业"><strong>{company.name}</strong><CopyIdentifier value={company.id} label={`${company.name}企业 ID`} /></td>
                    <td data-label="状态"><StatusPill value={company.status} /></td>
                    <td data-label="老板账号">
                      <StatusPill value={company.owner_activation_required ? "pending" : "active"} label={company.owner_activation_required ? "等待接受邀请" : "已激活"} />
                      {company.owner_activation_required && company.owner_invitation_expires_at ? <small>链接有效期至 {shortDate(company.owner_invitation_expires_at)}</small> : null}
                    </td>
                    <td data-label="权益入账"><strong>{billingAmountLabel(summary, { pointsField: "recharge_points", centsField: "recharge_cents" })}</strong><small>{billingUnitLabel(summary)}</small></td>
                    <td data-label="可用权益">{billingAmountLabel(summary, { pointsField: "available_points", centsField: "available_cents" })}</td>
                    <td data-label="实际消费">{billingAmountLabel(summary, { pointsField: "consumption_points", centsField: "consumption_cents" })}</td>
                    <td data-label="创建时间">{shortDate(company.created_at)}</td>
                    <td data-label="操作" className="is-actions">
                      {ownerInvitationLinks[company.id] ? <button type="button" onClick={() => copyOwnerInvitationLink(company)} disabled={busy}>复制老板邀请</button> : null}
                      {company.owner_activation_required ? <button type="button" onClick={() => setDrawer({ type: "ownerInvitation", company })} disabled={busy || !canUsePlatformPermission("platform.companies.manage") || !company.owner_membership_id || !company.owner_user_id} title={!company.owner_membership_id || !company.owner_user_id ? "需要服务端返回老板成员快照" : undefined}>重新签发老板邀请</button> : null}
                      <button type="button" onClick={() => openCompanyControl(company)} disabled={!canUsePlatformPermission("platform.entitlements.read") && !canUsePlatformPermission("platform.finance.read")} title={!canUsePlatformPermission("platform.entitlements.read") && !canUsePlatformPermission("platform.finance.read") ? "需要企业权益读取或财务读取权限" : undefined}>管理</button>
                      <button type="button" onClick={() => setDrawer({ type: "recharge", company, summary, idempotencyKey: makeOperationKey("recharge") })} disabled={demoMode || !canUsePlatformPermission("platform.finance.manage") || billingPresentationState(summary).kind !== "legacy_cents"} title={demoMode ? "演示模式不执行钱包入账" : billingPresentationState(summary).kind !== "legacy_cents" ? "积分入账必须关联可信支付或受控授权来源，本入口暂不可用" : "历史人民币账人工入账"}>{billingPresentationState(summary).kind === "legacy_cents" ? "历史人工入账" : "积分入账暂不可用"}</button>
                      <button type="button" onClick={() => setCompanyStatus(company)} disabled={busy || !canUsePlatformPermission("platform.companies.manage")}>{company.status === "active" ? "停用" : "恢复"}</button>
                    </td>
                  </tr>
                );
              })}
              {!(data.companies?.items || []).length && <EmptyRows colSpan={8} message="暂无企业" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.companies?.items?.length} total={data.companies?.total} busy={paginationBusyKey === "platform-companies"} onLoadMore={() => loadMore("platform-companies")} noun="家企业" />
      </section>
    </div>
  );
}

export function PlatformModelsView({
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
  return (
    <div className="control-route is-platform-models">
      <PageHeader eyebrow="生产目录" title="模型接入与发布" detail="Relay 新公共模型自动生成未发布草稿；你只需核验证据、设置价格并决定何时发布和分发。" />
      <PlatformModelReleases
        data={data}
        loading={loading}
        busy={busy}
        canUsePlatformPermission={canUsePlatformPermission}
        demoMode={demoMode}
        approveRelayModelRevision={approveRelayModelRevision}
        loadRelayCapabilityHistory={loadRelayCapabilityHistory}
        refreshModels={refreshModels}
        reconcileRelayModels={reconcileRelayModels}
        syncRelayModelCandidate={syncRelayModelCandidate}
        setDrawer={setDrawer}
        setModelState={setModelState}
        savePersonalModelGrant={savePersonalModelGrant}
        previewPersonalModelGrantBatch={previewPersonalModelGrantBatch}
        savePersonalModelGrantBatch={savePersonalModelGrantBatch}
      />
    </div>
  );
}

export function PlatformResourcesView({ data, canUsePlatformPermission, setDrawer }) {
  const groups = data.adminResources.reduce((result, resource) => {
    const kind = resource.kind || "feature";
    if (!result[kind]) result[kind] = [];
    result[kind].push(resource);
    return result;
  }, {});
  const kinds = Object.keys(groups).sort((left, right) => (
    (RESOURCE_KIND_LABELS[left] || left).localeCompare(RESOURCE_KIND_LABELS[right] || right, "zh-CN")
  ));
  return (
    <div className="control-route is-platform-resources">
      <PageHeader eyebrow="平台权益" title="功能资源" detail="资源目录由服务端动态返回，平台功能、智能体和外部 API 都可逐家企业开通。">
        <PrimaryButton onClick={() => setDrawer({ type: "resource" })} disabled={!canUsePlatformPermission("platform.resources.manage")}><Plus size={16} /> 新建资源</PrimaryButton>
      </PageHeader>
      <section className="control-section">
        <div className="control-section-title"><div><h2>资源目录</h2><p>停用目录项后不能再向企业开通，已有权益仍可在企业管理中关闭。</p></div><span>{data.adminResources.length} 项</span></div>
        {kinds.map((kind) => (
          <section className="control-resource-group" key={kind}>
            <header><strong>{RESOURCE_KIND_LABELS[kind] || kind}</strong><span>{groups[kind].length} 项</span></header>
            <div className="control-resource-list is-admin">
              {groups[kind].map((resource) => (
                <article key={resource.id} data-resource-kind={kind}><span><Key size={20} /></span><div><strong>{resource.display_name}</strong><small>{resource.description || "暂无说明"}</small></div><code>{resource.key}</code><StatusPill value={resource.active ? "active" : "disabled"} /><button className="control-resource-edit" type="button" onClick={() => setDrawer({ type: "resource", resource })} disabled={!canUsePlatformPermission("platform.resources.manage")}>编辑</button></article>
              ))}
            </div>
          </section>
        ))}
        {!kinds.length && <p className="control-empty-block">暂无资源定义</p>}
      </section>
    </div>
  );
}

export function PlatformAuditView({ data, paginationBusyKey, loadMore }) {
  return (
    <div className="control-route is-platform-audit">
      <PageHeader eyebrow="安全与追溯" title="操作审计" detail="平台级高风险操作记录执行人、对象、请求编号与变更摘要。" />
      <section className="control-section">
        <div className="control-section-title"><div><h2>审计事件</h2><p>{data.audit?.total || 0} 条记录 · 按时间倒序。</p></div></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-audit-table">
            <thead><tr><th>动作</th><th>执行人</th><th>对象</th><th>对象 ID</th><th>变更摘要</th><th>请求 ID</th><th>时间</th></tr></thead>
            <tbody>
              {(data.audit?.items || []).map((event) => (
                <tr key={event.id}><td data-label="动作"><code>{event.action}</code></td><td data-label="执行人">{event.actor_kind === "system" ? <span><strong>系统任务</strong><small>{event.actor_key || "Platform"}</small></span> : <CopyIdentifier value={event.actor_user_id} label="执行人 ID" />}</td><td data-label="对象">{event.target_type}</td><td data-label="对象 ID"><CopyIdentifier value={event.target_id} label="对象 ID" /></td><td data-label="变更摘要"><AuditChangeSummary before={event.before_summary} after={event.after_summary} /></td><td data-label="请求 ID"><CopyIdentifier value={event.request_id} label="请求 ID" /></td><td data-label="时间">{shortDate(event.created_at)}</td></tr>
              ))}
              {!(data.audit?.items || []).length && <EmptyRows colSpan={7} message="暂无审计记录" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.audit?.items?.length} total={data.audit?.total} busy={paginationBusyKey === "platform-audit"} onLoadMore={() => loadMore("platform-audit")} noun="条审计记录" />
      </section>
    </div>
  );
}
