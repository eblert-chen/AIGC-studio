import { useState } from "react";
import { Check, Copy, Key, Plus, WarningCircle } from "@phosphor-icons/react";
import {
  AuditChangeSummary,
  EmptyRows,
  ModelCapabilitySummary,
  PageHeader,
  PageLoadMore,
  PrimaryButton,
  StatusPill,
} from "./ManagementPrimitives.jsx";
import {
  RELAY_CAPABILITY_STATUS_LABELS,
  RESOURCE_KIND_LABELS,
  money,
  pricingModeLabel,
  shortDate,
} from "./managementPresentation.js";
import { safeId } from "./managementAccess.js";

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
    <>
      <PageHeader eyebrow="客户生命周期" title="企业管理" detail="创建企业、启停访问、充值，以及下发模型和功能权益。">
        <PrimaryButton onClick={() => setDrawer({ type: "company" })} disabled={!canUsePlatformPermission("platform.companies.manage")}><Plus size={16} /> 新建企业</PrimaryButton>
      </PageHeader>
      <section className="control-section">
        <div className="control-section-title"><div><h2>企业目录</h2><p>{data.companies?.total || 0} 家企业，所有变更都会进入平台审计日志。</p></div></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-companies-table">
            <thead><tr><th>企业</th><th>状态</th><th>老板账号</th><th>累计充值</th><th>可用余额</th><th>实际消费</th><th>创建时间</th><th className="is-actions">操作</th></tr></thead>
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
                    <td data-label="累计充值">{summary.recharge_cents == null ? "—" : money(summary.recharge_cents)}</td>
                    <td data-label="可用余额">{summary.available_cents == null ? "—" : money(summary.available_cents)}</td>
                    <td data-label="实际消费">{summary.consumption_cents == null ? "—" : money(summary.consumption_cents)}</td>
                    <td data-label="创建时间">{shortDate(company.created_at)}</td>
                    <td data-label="操作" className="is-actions">
                      {ownerInvitationLinks[company.id] ? <button type="button" onClick={() => copyOwnerInvitationLink(company)} disabled={busy}>复制老板邀请</button> : null}
                      {company.owner_activation_required ? <button type="button" onClick={() => setDrawer({ type: "ownerInvitation", company })} disabled={busy || !canUsePlatformPermission("platform.companies.manage") || !company.owner_membership_id || !company.owner_user_id} title={!company.owner_membership_id || !company.owner_user_id ? "需要服务端返回老板成员快照" : undefined}>重新签发老板邀请</button> : null}
                      <button type="button" onClick={() => openCompanyControl(company)} disabled={!canUsePlatformPermission("platform.entitlements.read") && !canUsePlatformPermission("platform.finance.read")} title={!canUsePlatformPermission("platform.entitlements.read") && !canUsePlatformPermission("platform.finance.read") ? "需要企业权益读取或财务读取权限" : undefined}>管理</button>
                      <button type="button" onClick={() => setDrawer({ type: "recharge", company, idempotencyKey: makeOperationKey("recharge") })} disabled={!canUsePlatformPermission("platform.finance.manage")}>充值</button>
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
    </>
  );
}

export function PlatformModelsView({
  data,
  busy,
  canUsePlatformPermission,
  approveRelayModelRevision,
  deleteDraftModel,
  setDrawer,
  setModelState,
}) {
  return (
    <>
      <PageHeader eyebrow="生产目录" title="模型目录" detail="草稿、发布、下线有明确生命周期；已下线模型不能再创建新任务。">
        <PrimaryButton onClick={() => setDrawer({ type: "model" })} disabled={!canUsePlatformPermission("platform.models.manage")}><Plus size={16} /> 新建模型</PrimaryButton>
      </PageHeader>
      <section className="control-section">
        <div className="control-section-title"><div><h2>统一模型定义</h2><p>{data.relayModelAudit?.error || (data.relayModelAudit?.catalog_revision ? `已读取中转站目录 ${data.relayModelAudit.catalog_revision.slice(0, 12)}，确认后任务会固定能力版本。` : "能力版本变更使用乐观并发控制，避免覆盖他人修改。")}</p></div></div>
        <div className="control-table-wrap is-model-capability-table">
          <table className="control-table">
            <thead><tr><th>模型</th><th>提供方</th><th>计费方式</th><th>版本</th><th>状态</th><th>中转能力</th><th>能力</th><th className="is-actions">操作</th></tr></thead>
            <tbody>
              {data.adminModels.map((model) => {
                const relayAudit = data.relayModelAudit?.items?.find(
                  (item) => item.platform_model_id === model.id,
                );
                const relayApproved = Boolean(
                  relayAudit
                    && relayAudit.approved_revision === relayAudit.capability_revision,
                );
                const relayCompatible = relayAudit?.status === "identical"
                  || relayAudit?.status === "compatible_restriction";
                return (
                  <tr key={model.id}>
                    <td><strong>{model.display_name}</strong><small>{model.slug}</small></td>
                    <td><code>{model.provider_key}</code></td>
                    <td>{pricingModeLabel(model.billing_mode)}</td>
                    <td>v{model.capability_version}</td>
                    <td><StatusPill value={model.status || (model.active ? "published" : "disabled")} /></td>
                    <td>
                      <StatusPill
                        value={relayApproved ? "active" : relayCompatible ? "draft" : relayAudit ? "failed" : "disabled"}
                        label={relayApproved ? "版本已固定" : relayAudit ? RELAY_CAPABILITY_STATUS_LABELS[relayAudit.status] : "中转站无映射"}
                      />
                      {relayAudit?.capability_revision && <small>{relayAudit.capability_revision.slice(0, 12)}</small>}
                    </td>
                    <td><ModelCapabilitySummary model={model} compact /></td>
                    <td className="is-actions">
                      {relayCompatible && !relayApproved && <button type="button" onClick={() => approveRelayModelRevision(model)} disabled={busy || !canUsePlatformPermission("platform.models.manage")}>确认能力</button>}
                      {model.status !== "published" && <button type="button" onClick={() => setDrawer({ type: "model", model })} disabled={!canUsePlatformPermission("platform.models.manage")}>编辑</button>}
                      {model.status === "draft" || model.status === "disabled"
                        ? <button type="button" onClick={() => setModelState(model, "publish")} disabled={busy || !canUsePlatformPermission("platform.models.manage")}>发布</button>
                        : <button type="button" onClick={() => setModelState(model, "disable")} disabled={busy || !canUsePlatformPermission("platform.models.manage")}>下线</button>}
                      {model.status === "draft" && <button type="button" onClick={() => deleteDraftModel(model)} disabled={busy || !canUsePlatformPermission("platform.models.manage")}>删除</button>}
                    </td>
                  </tr>
                );
              })}
              {!data.adminModels.length && <EmptyRows colSpan={8} message="模型目录为空" />}
            </tbody>
          </table>
        </div>
      </section>
    </>
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
    <>
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
                <article key={resource.id}><span><Key size={20} /></span><div><strong>{resource.display_name}</strong><small>{resource.description || "暂无说明"}</small></div><code>{resource.key}</code><StatusPill value={resource.active ? "active" : "disabled"} /><button className="control-resource-edit" type="button" onClick={() => setDrawer({ type: "resource", resource })} disabled={!canUsePlatformPermission("platform.resources.manage")}>编辑</button></article>
              ))}
            </div>
          </section>
        ))}
        {!kinds.length && <p className="control-empty-block">暂无资源定义</p>}
      </section>
    </>
  );
}

export function PlatformAuditView({ data, paginationBusyKey, loadMore }) {
  return (
    <>
      <PageHeader eyebrow="安全与追溯" title="操作审计" detail="平台级高风险操作记录执行人、对象、请求编号与变更摘要。" />
      <section className="control-section">
        <div className="control-section-title"><div><h2>审计事件</h2><p>{data.audit?.total || 0} 条记录 · 按时间倒序。</p></div></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-audit-table">
            <thead><tr><th>动作</th><th>执行人</th><th>对象</th><th>对象 ID</th><th>变更摘要</th><th>请求 ID</th><th>时间</th></tr></thead>
            <tbody>
              {(data.audit?.items || []).map((event) => (
                <tr key={event.id}><td data-label="动作"><code>{event.action}</code></td><td data-label="执行人"><CopyIdentifier value={event.actor_user_id} label="执行人 ID" /></td><td data-label="对象">{event.target_type}</td><td data-label="对象 ID"><CopyIdentifier value={event.target_id} label="对象 ID" /></td><td data-label="变更摘要"><AuditChangeSummary before={event.before_summary} after={event.after_summary} /></td><td data-label="请求 ID"><CopyIdentifier value={event.request_id} label="请求 ID" /></td><td data-label="时间">{shortDate(event.created_at)}</td></tr>
              ))}
              {!(data.audit?.items || []).length && <EmptyRows colSpan={7} message="暂无审计记录" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.audit?.items?.length} total={data.audit?.total} busy={paginationBusyKey === "platform-audit"} onLoadMore={() => loadMore("platform-audit")} noun="条审计记录" />
      </section>
    </>
  );
}
