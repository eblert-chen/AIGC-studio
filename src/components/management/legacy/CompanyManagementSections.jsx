import React from "react";
import {
  ArrowClockwise,
  DownloadSimple,
  Key,
  Plus,
  ShieldCheck,
  SlidersHorizontal,
} from "@phosphor-icons/react";
import { downloadRecordState, taskCostLabel } from "../../../taskArtifacts.js";
import {
  billingAmountLabel,
  billingTotalsLabel,
  billingUnitLabel,
  formatPointAmount,
} from "../../../billingPresentation.js";
import {
  CollectionState,
  EmptyRows,
  ModelCapabilitySummary,
  PageHeader,
  PageLoadMore,
  PermissionNotice,
  PrimaryButton,
  QuietButton,
  StatusPill,
  SummaryStrip,
} from "../ManagementPrimitives.jsx";
import {
  permissionOverrideMap,
  roleList,
  safeId,
} from "../managementAccess.js";
import { CopyIdentifier } from "../PlatformCatalogViews.jsx";
import {
  hasCompanyOverviewEvidence,
  hasCompanyReportsEvidence,
  hasCompanyWalletEvidence,
  pricingModeLabel,
  shortDate,
} from "../managementPresentation.js";

export function createCompanyManagementSectionRenderers(workspace) {
  const {
    data,
    error,
    loading,
    busy,
    demoMode,
    paginationBusyKey,
  } = workspace.state;
  const {
    companyPermissions,
    canManageUsers,
    canExportReports,
    isCurrentOwner,
    actionBusy,
  } = workspace.access;
  const {
    load,
    loadMore,
    setSection,
    setDrawer,
    openMemberAccess,
    openRoleEditor,
    setMemberStatus,
    copyInvitationLink,
    reissueCompanyInvitation,
    revokeCompanyInvitation,
    deleteCompanyRole,
    exportReport,
  } = workspace.actions;
  const {
    reportFilters,
    setReportFilters,
    setAppliedReportFilters,
    appliedReportFilters,
    companyReportMembers,
    companyReportModels,
    filteredDemoTasks,
    filteredDemoConsumption,
    EMPTY_COMPANY_REPORT_FILTERS,
  } = workspace.reporting;
  const {
    byteCount,
    makeOperationKey,
    pricingQuantityLabel,
    billingNumericValue,
  } = workspace.formatters;

  const renderCompanyOverview = () => {
    if (!hasCompanyOverviewEvidence(data)) {
      const isPending = loading && !error;
      return (
        <div className="control-route is-company-overview">
          <PageHeader eyebrow="公司控制台" title="经营概览" detail="余额、任务与近期资金变动都来自同一个公司账本。">
            <QuietButton onClick={() => load("overview")} disabled={loading}><ArrowClockwise size={16} /> 刷新</QuietButton>
          </PageHeader>
          <CollectionState
            state={isPending ? "loading" : "error"}
            title={isPending ? "正在核验公司账务" : "公司账务证据不可用"}
            detail={isPending
              ? "钱包和任务汇总返回前不会展示零余额或空任务。"
              : error || "服务端没有返回完整的钱包和任务汇总；当前结果不能按零值处理。"}
            onRetry={isPending ? undefined : () => load("overview")}
          />
        </div>
      );
    }
    const succeeded = data.succeededTaskReport?.total || 0;
    const total = data.taskReport?.total || 0;
    return (
      <div className="control-route is-company-overview">
        <PageHeader eyebrow="公司控制台" title="经营概览" detail="企业共享计费钱包、任务与近期变动都来自同一个公司账本。">
          <QuietButton onClick={() => load("overview")} disabled={loading}>
            <ArrowClockwise size={16} /> 刷新
          </QuietButton>
        </PageHeader>
        <SummaryStrip
          items={[
            { label: "可用权益", value: billingAmountLabel(data.wallet, { pointsField: "available_points", centsField: "available_cents" }), note: billingUnitLabel(data.wallet) },
            { label: "任务预留", value: billingAmountLabel(data.wallet, { pointsField: "reserved_points", centsField: "reserved_cents" }), note: "完成结算，失败释放" },
            { label: "累计任务", value: total, note: `${succeeded} 条已成功` },
            { label: "实际消费", value: billingTotalsLabel(data.taskReport, { pointsField: "total_actual_cost_points", centsField: "total_actual_cost_cents" }), note: "按计费单位分别汇总；不代表人民币收入" },
          ]}
        />
        <section className="control-section is-evidence-ledger">
          <div className="control-section-title">
            <div><h2>最近任务</h2><p>只显示服务器持久化的任务记录。</p></div>
            <button type="button" onClick={() => setSection("reports")}>查看完整报表</button>
          </div>
          <div className="control-table-wrap is-mobile-records">
            <table className="control-table is-recent-tasks-table">
              <thead><tr><th>任务</th><th>创建人</th><th>模型</th><th>状态</th><th>结算</th><th>时间</th></tr></thead>
              <tbody>
                {(data.taskReport?.items || []).slice(0, 8).map((task) => (
                  <tr key={task.task_id}>
                    <td data-label="任务" className="is-mono">{safeId(task.task_id)}</td>
                    <td data-label="创建人">{task.employee_display_name}</td>
                    <td data-label="模型">{task.model_display_name}</td>
                    <td data-label="状态"><StatusPill value={task.status} /></td>
                    <td data-label="结算">{taskCostLabel(task)}</td>
                    <td data-label="时间">{shortDate(task.created_at)}</td>
                  </tr>
                ))}
                {!(data.taskReport?.items || []).length && <EmptyRows colSpan={6} message="还没有生成任务" />}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    );
  };

  const renderMembers = () => (
    <div className="control-route is-company-members">
      <PageHeader eyebrow="访问控制" title="成员与角色" detail="角色提供默认权限模板；老板还能对每位员工的每一项权限单独允许、禁止或恢复为跟随模板。">
        {canManageUsers && <QuietButton onClick={() => openRoleEditor()} disabled={actionBusy}><ShieldCheck size={16} /> 新建权限角色</QuietButton>}
        {isCurrentOwner && data.members.some((member) => member.membership_id !== data.me?.membership_id && member.status === "active") ? <QuietButton onClick={() => setDrawer({ type: "ownerTransfer" })} disabled={actionBusy}>交接老板职责</QuietButton> : null}
        {canManageUsers && <PrimaryButton onClick={() => setDrawer({ type: "invitation", idempotencyKey: makeOperationKey("company-invitation") })} disabled={actionBusy}><Plus size={16} /> 发邀请</PrimaryButton>}
      </PageHeader>
      <section className="control-section is-evidence-ledger">
        <div className="control-section-title">
          <div><h2>公司成员</h2><p>{data.members.length} 人 · 当前账号不能调整自己或老板的级别，也不能停用自己或老板。</p></div>
        </div>
        {loading && !data.members.length ? (
          <CollectionState state="loading" title="正在读取公司成员" detail="成员、级别和个人权限会一起核验。" />
        ) : error && !data.members.length ? (
          <CollectionState state="error" title="公司成员读取失败" detail={error} onRetry={() => load("members")} />
        ) : (
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-members-table">
            <thead><tr><th>成员</th><th>角色</th><th>状态</th><th>账号 ID</th><th className="is-actions">操作</th></tr></thead>
            <tbody>
              {data.members.map((member) => {
                const roles = roleList(member);
                const overrideCount = Object.keys(permissionOverrideMap(member)).length;
                const isSelf = member.membership_id === data.me?.membership_id;
                const isOwner = roles.some((role) => role.system_key === "owner");
                return (
                  <tr key={member.membership_id}>
                    <td data-label="成员"><strong>{member.display_name}</strong><small>{member.email}</small></td>
                    <td data-label="角色">
                      <div className="control-chip-row">{roles.length ? roles.map((role) => <span key={role.id}>{role.name}</span>) : <em>未分配</em>}</div>
                      <small className={overrideCount ? "control-access-note is-adjusted" : "control-access-note"}>
                        {overrideCount ? `个人调整 ${overrideCount} 项` : "跟随角色模板"}
                      </small>
                    </td>
                    <td data-label="状态"><StatusPill value={member.status} label={member.status === "disabled" ? "已停用" : undefined} /></td>
                    <td data-label="账号 ID"><CopyIdentifier value={member.user_id} label={`${member.display_name}账号 ID`} /></td>
                    <td data-label="操作" className="is-actions">
                       <button type="button" onClick={() => openMemberAccess(member)} disabled={!isCurrentOwner || isSelf || isOwner || member.status !== "active" || actionBusy}>级别与权限</button>
                       <button type="button" onClick={() => setMemberStatus(member)} disabled={!canManageUsers || isSelf || isOwner || actionBusy}>{member.status === "active" ? "停用" : "恢复"}</button>
                    </td>
                  </tr>
                );
              })}
              {!data.members.length && <EmptyRows colSpan={5} message="暂无公司成员" />}
            </tbody>
          </table>
        </div>
        )}
      </section>
      {canManageUsers ? (
        <section className="control-section is-evidence-ledger">
          <div className="control-section-title">
            <div><h2>成员邀请</h2><p>账号只有在受邀邮箱完成登录并接受邀请后才会成为正式成员。新链接只在创建或重新发送后显示一次。</p></div>
            <span>{data.invitations.total} 份</span>
          </div>
          <div className="control-table-wrap is-mobile-records">
            <table className="control-table is-invitations-table">
              <thead><tr><th>受邀人</th><th>初始级别</th><th>状态</th><th>有效期</th><th className="is-actions">操作</th></tr></thead>
              <tbody>
                {data.invitations.items.map((invitation) => (
                  <tr key={invitation.id}>
                    <td data-label="受邀人"><strong>{invitation.display_name}</strong><small>{invitation.email}</small></td>
                    <td data-label="初始级别">{invitation.primary_role === "team_lead" ? "组长" : "运营"}</td>
                    <td data-label="状态"><StatusPill value={invitation.status} /></td>
                    <td data-label="有效期">{shortDate(invitation.expires_at)}</td>
                    <td data-label="操作" className="is-actions">
                      {invitation.invitation_url ? <button type="button" onClick={() => copyInvitationLink(invitation)} disabled={actionBusy}>复制一次性链接</button> : null}
                      <button type="button" onClick={() => reissueCompanyInvitation(invitation)} disabled={actionBusy || invitation.status === "accepted"}>重新发送</button>
                      <button type="button" onClick={() => revokeCompanyInvitation(invitation)} disabled={actionBusy || ["accepted", "revoked"].includes(invitation.status)}>撤销</button>
                    </td>
                  </tr>
                ))}
                {!data.invitations.items.length && <EmptyRows colSpan={5} message="暂无成员邀请" />}
              </tbody>
            </table>
          </div>
          <PageLoadMore loaded={data.invitations.items.length} total={data.invitations.total} busy={paginationBusyKey === "company-invitations"} onLoadMore={() => loadMore("company-invitations")} noun="份邀请" />
        </section>
      ) : null}
      <section className="control-section is-role-ledger">
        <div className="control-section-title"><div><h2>级别与权限模板</h2><p>老板可配置组长、运营的默认模板；成员个人设置会在模板之上逐项覆盖。</p></div></div>
        {loading && !data.roles.length ? (
          <CollectionState state="loading" title="正在读取权限模板" detail="完成前不会将空列表误认为没有角色。" />
        ) : error && !data.roles.length ? (
          <CollectionState state="error" title="权限模板读取失败" detail={error} onRetry={() => load("members")} />
        ) : data.roles.length ? (
        <div className="control-role-grid" role="list" aria-label="公司级别与权限模板">
          {data.roles.map((role) => {
            const canEditRole = canManageUsers && (
              !role.is_system || (isCurrentOwner && ["team_lead", "operator"].includes(role.system_key))
            );
            return (
              <article key={role.id} role="listitem" data-role-kind={role.is_system ? "system" : "custom"}>
                <header>
                  <div><strong>{role.name}</strong><small>{role.is_system ? "公司固定级别" : "附加权限角色"}</small></div>
                  <div className="control-role-actions">
                    <span>{(role.permission_codes || []).length} 项</span>
                    {canEditRole && <button type="button" onClick={() => openRoleEditor(role)} disabled={actionBusy}>{role.is_system ? "配置权限" : "编辑"}</button>}
                    {!role.is_system && canManageUsers && <button type="button" onClick={() => deleteCompanyRole(role)} disabled={actionBusy}>删除</button>}
                  </div>
                </header>
                <p>{role.description || "暂无说明"}</p>
                <div className="control-chip-row">{(role.permission_codes || []).map((code) => <span key={code}>{code}</span>)}</div>
              </article>
            );
          })}
        </div>
        ) : (
          <CollectionState title="还没有权限模板" detail="固定公司级别尚未返回，请刷新后再试；拥有成员管理权限时也可以新建附加权限角色。" />
        )}
      </section>
    </div>
  );

  const renderCompanyModels = () => {
    const canReadModels = companyPermissions.has("models.read");
    const canReadResources = companyPermissions.has("resources.read");
    return (
    <div className="control-route is-company-entitlements">
      <PageHeader eyebrow="企业权益" title="模型与功能" detail="这里仅展示平台已发布并授权给当前公司的模型与功能。" />
      <section className="control-section is-evidence-ledger">
        <div className="control-section-title"><div><h2>已授权模型</h2><p>价格由公司授权快照决定，任务提交时再次由服务器校验。</p></div></div>
        {canReadModels ? <div className="control-table-wrap is-model-capability-table is-mobile-records">
          <table className="control-table is-company-models-table">
            <thead><tr><th>模型</th><th>能力版本</th><th>计费方式</th><th>公司单价</th><th>能力</th></tr></thead>
            <tbody>
              {data.models.map((model) => (
                <tr key={model.id}>
                  <td data-label="模型"><strong>{model.display_name}</strong><small>{model.slug}</small></td>
                  <td data-label="能力版本">v{model.capability_version}</td>
                  <td data-label="计费方式">{model.pricing_mode === "per_second" ? "按秒" : "按条"}</td>
                  <td data-label="公司单价">{billingAmountLabel(model, { pointsField: "unit_price_points", centsField: "unit_price_cents" })}{model.pricing_mode === "per_second" ? "/秒" : "/条"}</td>
                  <td data-label="能力"><ModelCapabilitySummary model={model} compact /></td>
                </tr>
              ))}
              {!data.models.length && <EmptyRows colSpan={5} message="当前公司还没有可用模型" />}
            </tbody>
          </table>
        </div> : <PermissionNotice title="没有模型目录读取权限" detail="此区域没有加载模型数据。请联系公司老板授予 models.read。" />}
      </section>
      <section className="control-section is-resource-ledger">
        <div className="control-section-title"><div><h2>已开通功能</h2><p>功能与资源由平台管理员统一开通。</p></div></div>
        {canReadResources ? <div className="control-resource-list is-company-resources">
          {data.resources.map((resource) => (
            <article key={resource.id} data-resource-kind={resource.kind || "feature"}><span><Key size={20} /></span><div><strong>{resource.display_name}</strong><small>{resource.description}</small></div><code>{resource.key}</code></article>
          ))}
          {!data.resources.length && <p className="control-empty-block">暂无已开通功能</p>}
        </div> : <PermissionNotice title="没有功能资源读取权限" detail="此区域没有加载功能数据。请联系公司老板授予 resources.read。" />}
      </section>
    </div>
    );
  };

  const renderReports = () => {
    if (!hasCompanyReportsEvidence(data)) {
      const isPending = loading && !error;
      return (
        <div className="control-route is-company-reports">
          <PageHeader eyebrow="用量与审计" title="使用报表" detail="按员工、模型、状态与时间核对任务、消费和产物下载行为。">
            {!isPending && <QuietButton onClick={() => {
              const cleared = { ...EMPTY_COMPANY_REPORT_FILTERS };
              setReportFilters(cleared);
              setAppliedReportFilters(cleared);
              load("reports", cleared);
            }}>清除筛选并重试</QuietButton>}
          </PageHeader>
          <CollectionState
            state={isPending ? "loading" : "error"}
            title={isPending ? "正在核验使用报表" : "使用报表证据不可用"}
            detail={isPending
              ? "任务、消费和下载记录返回前不会展示零笔或空结果。"
              : error || "当前筛选没有获得完整的任务、消费和下载记录；不会继续展示上一组筛选结果。"}
            onRetry={isPending ? undefined : () => load("reports")}
          />
        </div>
      );
    }
    return (
    <div className="control-route is-company-reports">
      <PageHeader eyebrow="用量与审计" title="使用报表" detail="按员工、模型、状态与时间核对任务、消费和产物下载行为。">
        {canExportReports && <QuietButton onClick={() => exportReport("consumption")} disabled={busy}><DownloadSimple size={16} /> 消费 CSV</QuietButton>}
        {canExportReports && <PrimaryButton onClick={() => exportReport("tasks")} disabled={busy}><DownloadSimple size={16} /> 任务 CSV</PrimaryButton>}
      </PageHeader>
      <details className="control-filter-disclosure">
        <summary>
          <SlidersHorizontal size={18} aria-hidden="true" />
          <span>
            <strong>筛选条件</strong>
            <small>
              <span className="is-collapsed-copy">{Object.values(reportFilters).filter(Boolean).length} 项已设置 · 点击展开</span>
              <span className="is-expanded-copy">{Object.values(reportFilters).filter(Boolean).length} 项已设置 · 完成后可收起</span>
            </small>
          </span>
        </summary>
        <form
          className="control-filterbar"
          onSubmit={(event) => {
            event.preventDefault();
            const applied = { ...reportFilters };
            setAppliedReportFilters(applied);
            load("reports", applied);
          }}
        >
        {companyPermissions.has("users.read") ? (
          <label><span>员工</span><select aria-label="按员工筛选使用报表" value={reportFilters.employee_user_id} onChange={(event) => setReportFilters((current) => ({ ...current, employee_user_id: event.target.value }))}><option value="">全部员工</option>{companyReportMembers.map((member) => <option key={member.id} value={member.id}>{member.display_name}</option>)}</select></label>
        ) : (
          <label><span>员工 ID</span><input aria-label="按员工 ID 筛选使用报表" type="search" list="company-report-member-ids" value={reportFilters.employee_user_id} onChange={(event) => setReportFilters((current) => ({ ...current, employee_user_id: event.target.value }))} placeholder="输入员工用户 ID" /><small>未授予成员目录权限，可按已知 ID 精确筛选。</small><datalist id="company-report-member-ids">{companyReportMembers.map((member) => <option key={member.id} value={member.id}>{member.display_name}</option>)}</datalist></label>
        )}
        <label><span>模型</span><select aria-label="按模型筛选使用报表" value={reportFilters.model_id} onChange={(event) => setReportFilters((current) => ({ ...current, model_id: event.target.value }))}><option value="">全部模型</option>{companyReportModels.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</select></label>
        <label><span>任务状态</span><select aria-label="按任务状态筛选使用报表" value={reportFilters.status} onChange={(event) => setReportFilters((current) => ({ ...current, status: event.target.value }))}><option value="">全部状态</option><option value="queued">排队中</option><option value="processing">处理中</option><option value="succeeded">成功</option><option value="failed">失败</option><option value="cancelled">已取消</option></select></label>
        <label><span>开始时间</span><input aria-label="使用报表开始时间" type="datetime-local" value={reportFilters.start_time} max={reportFilters.end_time || undefined} onChange={(event) => setReportFilters((current) => ({ ...current, start_time: event.target.value }))} /></label>
        <label><span>结束时间</span><input aria-label="使用报表结束时间" type="datetime-local" value={reportFilters.end_time} min={reportFilters.start_time || undefined} onChange={(event) => setReportFilters((current) => ({ ...current, end_time: event.target.value }))} /></label>
        <button type="submit">应用筛选</button>
          <span>{demoMode ? filteredDemoTasks.length : data.taskReport?.total || 0} 条任务 · {demoMode ? filteredDemoConsumption.length : data.consumption?.total || 0} 笔消费 · 共 {demoMode
            ? formatPointAmount(filteredDemoConsumption.reduce((sum, item) => sum + Number(item.amount_points || 0), 0))
            : billingTotalsLabel(data.consumption, { pointsField: "total_amount_points", centsField: "total_amount_cents" })}</span>
        </form>
      </details>
      <section className="control-section is-evidence-ledger">
        <div className="control-section-title"><div><h2>任务明细</h2><p>报价与实际结算分列，失败任务不计费。</p></div></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-report-tasks-table">
            <thead><tr><th>任务</th><th>员工</th><th>模型</th><th>状态</th><th>报价</th><th>实际消费</th><th>创建时间</th></tr></thead>
            <tbody>
              {filteredDemoTasks.map((task) => (
                <tr key={task.task_id}><td data-label="任务" className="is-mono">{safeId(task.task_id)}</td><td data-label="员工"><strong>{task.employee_display_name}</strong><small>{task.employee_email}</small></td><td data-label="模型">{task.model_display_name}</td><td data-label="状态"><StatusPill value={task.status} /></td><td data-label="报价">{billingAmountLabel(task, { pointsField: "quote_points", centsField: "quote_cents" })}</td><td data-label="实际消费">{taskCostLabel(task)}</td><td data-label="创建时间">{shortDate(task.created_at)}</td></tr>
              ))}
              {!filteredDemoTasks.length && <EmptyRows colSpan={7} message="当前筛选条件下没有任务" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.taskReport?.items?.length} total={data.taskReport?.total} busy={paginationBusyKey === "company-tasks"} onLoadMore={() => loadMore("company-tasks")} noun="条任务" />
      </section>
      <section className="control-section is-evidence-ledger">
        <div className="control-section-title"><div><h2>消费明细</h2><p>每笔成功结算记录保留员工、模型、计费方式和价格快照。</p></div><span>{demoMode ? filteredDemoConsumption.length : data.consumption?.total || 0} 笔</span></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-report-consumption-table">
            <thead><tr><th>任务</th><th>员工</th><th>模型</th><th>计费方式</th><th>单价</th><th>数量</th><th>金额</th><th>结算时间</th></tr></thead>
            <tbody>
              {filteredDemoConsumption.map((item) => (
                <tr key={item.ledger_entry_id}><td data-label="任务" className="is-mono">{safeId(item.task_id)}</td><td data-label="员工"><strong>{item.employee_display_name}</strong><small>{item.employee_email}</small></td><td data-label="模型">{item.model_display_name}</td><td data-label="计费方式">{pricingModeLabel(item.pricing_mode)}</td><td data-label="单价">{billingAmountLabel(item, { pointsField: "unit_price_points", centsField: "unit_price_cents" })}/{item.pricing_mode === "per_second" ? "秒" : "条"}</td><td data-label="数量">{pricingQuantityLabel(item)}</td><td data-label="实际消费">{billingAmountLabel(item, { pointsField: "amount_points", centsField: "amount_cents" })}</td><td data-label="结算时间">{shortDate(item.consumed_at)}</td></tr>
              ))}
              {!filteredDemoConsumption.length && <EmptyRows colSpan={8} message="当前筛选条件下没有消费记录" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.consumption?.items?.length} total={data.consumption?.total} busy={paginationBusyKey === "company-consumption"} onLoadMore={() => loadMore("company-consumption")} noun="笔消费" />
      </section>
      <section className="control-section is-evidence-ledger">
        <div className="control-section-title"><div><h2>产物下载审计</h2><p>按公司范围读取。员工与时间筛选适用；模型和任务状态只作用于上方两张表。</p></div><span>{data.downloads?.total || 0} 条</span></div>
        {(appliedReportFilters.model_id || appliedReportFilters.status) && <div className="control-filter-scope-note" role="note">下载记录接口不包含模型和任务状态字段，因此没有套用这两项筛选，避免把未过滤的数据伪装成筛选结果。</div>}
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-download-audit-table">
            <thead><tr><th>任务</th><th>产物</th><th>下载人</th><th>状态</th><th>签发时间</th><th>完成时间</th><th>已传输</th></tr></thead>
            <tbody>
              {(data.downloads?.items || []).map((record) => {
                const state = downloadRecordState(record);
                return (
                  <tr key={record.id}><td data-label="任务" className="is-mono">{safeId(record.task_id)}</td><td data-label="产物" className="is-mono">{safeId(record.asset_id)}</td><td data-label="下载人">{record.requested_by_display_name}</td><td data-label="状态"><span className={`download-state is-${state.tone}`} title={state.detail}>{state.label}</span></td><td data-label="签发时间"><strong>{shortDate(record.created_at)}</strong><small>地址有效 {record.expires_seconds} 秒</small></td><td data-label="完成时间">{record.completed_at ? shortDate(record.completed_at) : "等待存储确认"}</td><td data-label="已传输">{record.completed_at ? byteCount(record.bytes_sent) : "-"}</td></tr>
                );
              })}
              {!(data.downloads?.items || []).length && <EmptyRows colSpan={7} message="暂无下载记录" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.downloads?.items?.length} total={data.downloads?.total} busy={paginationBusyKey === "company-downloads"} onLoadMore={() => loadMore("company-downloads")} noun="条下载记录" />
      </section>
    </div>
    );
  };

  const renderWallet = () => {
    if (!hasCompanyWalletEvidence(data)) {
      const isPending = loading && !error;
      return (
        <div className="control-route is-company-wallet">
          <PageHeader eyebrow="计费账本" title="权益流水" detail="v2 使用整数积分；历史人民币分币账仅作只读迁移证据。" />
          <CollectionState
            state={isPending ? "loading" : "error"}
            title={isPending ? "正在核验余额流水" : "余额流水证据不可用"}
            detail={isPending
              ? "钱包、入账记录和不可变账本返回前不会展示零值。"
              : error || "服务端没有返回完整的钱包、入账记录或账本；当前结果不能按空账本处理。"}
            onRetry={isPending ? undefined : () => load("wallet")}
          />
        </div>
      );
    }
    return (
    <div className="control-route is-company-wallet">
      <PageHeader eyebrow="计费账本" title="权益流水" detail="v2 使用整数积分；历史人民币分币账仅作只读迁移证据。" />
      <SummaryStrip items={[{ label: "可用权益", value: billingAmountLabel(data.wallet, { pointsField: "available_points", centsField: "available_cents" }), note: billingUnitLabel(data.wallet) }, { label: "任务预留", value: billingAmountLabel(data.wallet, { pointsField: "reserved_points", centsField: "reserved_cents" }), note: "失败自动释放" }, { label: "累计入账", value: billingTotalsLabel(data.recharges, { pointsField: "total_amount_points", centsField: "total_amount_cents" }), note: `${data.recharges?.total || 0} 笔账本入账` }, { label: "账本记录", value: data.ledger?.length || 0, note: "服务器持久化" }]} />
      <section className="control-section is-evidence-ledger">
        <div className="control-section-title"><div><h2>权益入账明细</h2><p>来源与批次来自服务端计费账本；积分入账本身不代表支付渠道已经收款。</p></div><span>{data.recharges?.total || 0} 笔</span></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-recharges-table">
            <thead><tr><th>入账权益</th><th>账本备注</th><th>记录 ID</th><th>入账时间</th></tr></thead>
            <tbody>
              {(data.recharges?.items || []).map((entry) => (
                <tr key={entry.id}><td data-label="入账权益" className="is-positive">{billingAmountLabel(entry, { pointsField: entry.amount_points == null ? "available_delta_points" : "amount_points", centsField: entry.amount_cents == null ? "available_delta_cents" : "amount_cents", signed: true })}</td><td data-label="账本备注">{entry.note || "-"}</td><td data-label="记录 ID" className="is-mono">{safeId(entry.id)}</td><td data-label="入账时间">{shortDate(entry.created_at)}</td></tr>
              ))}
              {!(data.recharges?.items || []).length && <EmptyRows colSpan={4} message="暂无人工入账记录" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.recharges?.items?.length} total={data.recharges?.total} busy={paginationBusyKey === "company-recharges"} onLoadMore={() => loadMore("company-recharges")} noun="笔人工入账" />
      </section>
      <section className="control-section is-evidence-ledger">
        <div className="control-section-title"><div><h2>不可变流水</h2><p>按时间倒序展示计费账本记录；个人与企业账本始终隔离。</p></div></div>
        <div className="control-table-wrap is-mobile-records">
          <table className="control-table is-wallet-ledger-table">
            <thead><tr><th>类型</th><th>可用变动</th><th>预留变动</th><th>关联任务</th><th>说明</th><th>时间</th></tr></thead>
            <tbody>
              {(data.ledger || []).map((entry) => (
                <tr key={entry.id}><td data-label="类型"><StatusPill value={entry.kind} /></td><td data-label="可用变动" className={(billingNumericValue(entry, "available_delta_points", "available_delta_cents") || 0) < 0 ? "is-negative" : "is-positive"}>{billingAmountLabel(entry, { pointsField: "available_delta_points", centsField: "available_delta_cents", signed: true })}</td><td data-label="预留变动">{billingAmountLabel(entry, { pointsField: "reserved_delta_points", centsField: "reserved_delta_cents", signed: true })}</td><td data-label="关联任务" className="is-mono">{safeId(entry.task_id)}</td><td data-label="说明">{entry.note || "-"}</td><td data-label="时间">{shortDate(entry.created_at)}</td></tr>
              ))}
              {!(data.ledger || []).length && <EmptyRows colSpan={6} message="暂无资金流水" />}
            </tbody>
          </table>
        </div>
      </section>
    </div>
    );
  };


  return {
    overview: renderCompanyOverview,
    members: renderMembers,
    models: renderCompanyModels,
    reports: renderReports,
    wallet: renderWallet,
  };
}
