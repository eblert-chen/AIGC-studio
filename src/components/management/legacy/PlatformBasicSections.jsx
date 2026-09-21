import React from "react";
import {
  ArrowClockwise,
  DownloadSimple,
  Gift,
  Plus,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  billingAmountLabel,
  billingTotalsLabel,
  formatPointAmount,
} from "../../../billingPresentation.js";
import {
  CollectionState,
  EmptyRows,
  PageHeader,
  PageLoadMore,
  PrimaryButton,
  QuietButton,
  StatusPill,
  SummaryStrip,
} from "../ManagementPrimitives.jsx";
import {
  PlatformAuditView,
  PlatformCompaniesView,
  PlatformModelsView,
  PlatformResourcesView,
  CopyIdentifier,
} from "../PlatformCatalogViews.jsx";
import {
  CHANNEL_TYPE_LABELS,
  hasPlatformConsumptionEvidence,
  hasPlatformOverviewEvidence,
  money,
  pricingModeLabel,
  shortDate,
} from "../managementPresentation.js";
import { safeId } from "../managementAccess.js";

export function createPlatformBasicSectionRenderers(workspace) {
  const {
    data,
    error,
    loading,
    busy,
    demoMode,
    paginationBusyKey,
  } = workspace.state;
  const {
    platformIdentity,
    actionBusy,
    canUsePlatformPermission,
  } = workspace.access;
  const {
    load,
    loadMore,
    setDrawer,
    setGlobalUserStatus,
    openPersonalPointGrant,
    setCompanyStatus,
    openCompanyControl,
    copyOwnerInvitationLink,
    setModelState,
    approveRelayModelRevision,
    syncRelayModelCandidate,
    reconcileRelayModels,
    loadRelayCapabilityHistory,
    savePersonalModelGrant,
    previewPersonalModelGrantBatch,
    savePersonalModelGrantBatch,
  } = workspace.actions;
  const {
    adminReportFilters,
    setAdminReportFilters,
    setAppliedAdminReportFilters,
    filteredDemoAdminConsumption,
    exportAdminReport,
    EMPTY_ADMIN_REPORT_FILTERS,
  } = workspace.reporting;
  const {
    ownerInvitationLinks,
    companyDashboardRow,
    makeOperationKey,
    personalPointBalanceLabel,
    personalPointGrantEligibilityError,
    grossMarginLabel,
    pricingQuantityLabel,
  } = workspace.presentation;

  const renderGlobalUsers = () => (
    <>
      <PageHeader
        eyebrow="生产身份边界"
        title="账号生命周期"
        detail="仅平台所有者可管理全局自然人账号，并向已启用的个人空间赠送整数积分；个人积分与企业钱包始终隔离。"
      />
      <section className="control-section">
        <div className="control-section-title">
          <div><h2>全局账号</h2><p>暂停会递增账号安全版本并撤销全部设备会话；写操作在生产环境要求近期强认证。</p></div>
          <span>{data.adminUsers.total} 个</span>
        </div>
        {loading && !data.adminUsers.items.length ? (
          <CollectionState state="loading" title="正在读取账号生命周期" detail="服务端状态确认完成前不会开放停用或恢复操作。" />
        ) : error && !data.adminUsers.items.length ? (
          <CollectionState state="error" title="全局账号读取失败" detail={error} onRetry={() => load("users")} />
        ) : (
          <div className="control-table-wrap is-mobile-records">
            <table className="control-table is-global-users-table">
              <thead><tr><th>账号</th><th>状态</th><th>个人积分</th><th>安全版本</th><th>最近登录</th><th>更新时间</th><th className="is-actions">操作</th></tr></thead>
              <tbody>
                {data.adminUsers.items.map((user) => {
                  const isCurrent = user.id === platformIdentity?.user_id;
                  const mutable = ["active", "suspended"].includes(user.status) && !isCurrent;
                  const grantUnavailableReason = demoMode
                    ? "演示模式不会写入真实积分账本"
                    : !platformIdentity?.is_platform_owner
                      ? "只有平台所有者可以赠送积分"
                      : personalPointGrantEligibilityError(user);
                  return (
                    <tr key={user.id}>
                      <td><strong>{user.display_name}</strong><small>{user.email}</small><CopyIdentifier value={user.id} label={`${user.display_name}账号 ID`} /></td>
                      <td><StatusPill value={user.status} /></td>
                      <td className="personal-points-cell">
                        {user.account_type === "company" ? (
                          <><strong>企业账号</strong><small>使用企业钱包，不能赠送个人积分</small></>
                        ) : user.account_type === "platform_admin" ? (
                          <><strong>平台管理员</strong><small>只进入 Platform，不能赠送个人积分</small></>
                        ) : user.account_type === "unavailable" ? (
                          <><strong>账号不可用</strong><small>当前不能赠送个人积分</small></>
                        ) : user.personal_workspace_id ? (
                          <>
                            <strong>{personalPointBalanceLabel(user.available_points)}</strong>
                            <small>{user.personal_workspace_active === true ? `预留 ${personalPointBalanceLabel(user.reserved_points)}` : "个人空间已停用"}</small>
                          </>
                        ) : (
                          <><strong>未开通</strong><small>完成正式登录后初始化个人空间；仅个人消费者账号可领取积分</small></>
                        )}
                      </td>
                      <td className="is-mono">v{user.auth_version}</td>
                      <td>{shortDate(user.last_login_at)}</td>
                      <td>{shortDate(user.updated_at)}</td>
                      <td className="is-actions">
                        <button
                          type="button"
                          disabled={Boolean(grantUnavailableReason) || actionBusy}
                          title={grantUnavailableReason || `向 ${user.display_name} 赠送积分`}
                          aria-label={grantUnavailableReason || `向 ${user.display_name} 赠送积分`}
                          onClick={() => openPersonalPointGrant(user)}
                        >
                          <Gift size={15} aria-hidden="true" />赠送积分
                        </button>
                        <button type="button" disabled={!mutable || actionBusy} onClick={() => setGlobalUserStatus(user)}>
                          {user.status === "active" ? "暂停账号" : user.status === "suspended" ? "恢复账号" : "终态不可恢复"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {!data.adminUsers.items.length && <EmptyRows colSpan={7} message="暂无全局账号" />}
              </tbody>
            </table>
          </div>
        )}
        <PageLoadMore loaded={data.adminUsers.items.length} total={data.adminUsers.total} busy={paginationBusyKey === "platform-users"} onLoadMore={() => loadMore("platform-users")} noun="个账号" />
      </section>
    </>
  );

  const renderPlatformOverview = () => {
    const dashboard = data.dashboard;
    if (!hasPlatformOverviewEvidence(data)) {
      const isPending = loading && !error;
      return (
        <>
          <PageHeader eyebrow="平台运营" title="平台总览" detail="以成功结算为收入，以渠道账单为成本，直接查看平台毛利与企业经营情况。">
            <QuietButton onClick={() => load("overview")} disabled={loading}><ArrowClockwise size={16} /> 刷新</QuietButton>
          </PageHeader>
          <CollectionState
            state={isPending ? "loading" : "error"}
            title={isPending ? "正在核验平台财务" : "平台财务证据不可用"}
            detail={isPending
              ? "收入、渠道成本和对账状态返回前不会展示零值。"
              : error || "服务端没有返回完整的金额、任务计数或渠道对账状态；当前结果不能按零值处理。"}
            onRetry={isPending ? undefined : () => load("overview")}
          />
        </>
      );
    }
    const companyRows = dashboard.companies || [];
    const totalTasks = dashboard.total_task_count
      ?? companyRows.reduce((sum, item) => sum + Number(item.task_count || 0), 0);
    const succeededTasks = dashboard.succeeded_task_count
      ?? companyRows.reduce((sum, item) => sum + Number(item.succeeded_count || 0), 0);
    const failedTasks = dashboard.failed_task_count
      ?? companyRows.reduce((sum, item) => sum + Number(item.failed_count || 0), 0);
    const costReconciliationComplete = dashboard.channel_cost_status === "complete";
    const revenueReconciliationComplete = dashboard.revenue_reconciliation_status === "complete";
    const reconciliationComplete = dashboard.finance_status === "complete";
    const displayedGrossProfit = reconciliationComplete
      ? dashboard.gross_profit_cents
      : dashboard.known_gross_profit_cents;
    return (
      <>
        <PageHeader eyebrow="平台运营" title="平台总览" detail="以成功结算为收入，以渠道账单为成本，直接查看平台毛利与企业经营情况。">
          <QuietButton onClick={() => load("overview")} disabled={loading}><ArrowClockwise size={16} /> 刷新</QuietButton>
          <PrimaryButton onClick={() => setDrawer({ type: "channelCost", idempotencyKey: makeOperationKey("channel-cost") })} disabled={!canUsePlatformPermission("platform.provider_costs.manage")}><Plus size={16} /> 录入渠道成本</PrimaryButton>
        </PageHeader>
        <SummaryStrip items={[
          { label: revenueReconciliationComplete ? "已归因法币收入" : "已知法币收入", value: money(dashboard.platform_income_cents), note: revenueReconciliationComplete ? "成功结算已完成现金基础归因" : "积分消费未换算为人民币收入" },
          { label: "渠道成本", value: money(dashboard.channel_cost_cents), note: costReconciliationComplete ? "任务成本已完整对账" : "仍有成本等待对账" },
          { label: reconciliationComplete ? "平台毛利" : "已知净额", value: money(displayedGrossProfit), note: reconciliationComplete ? "已归因收入减已完整对账渠道成本" : "仅基于已归因收入和已入账成本，非最终毛利" },
          { label: reconciliationComplete ? "毛利率" : "毛利率", value: revenueReconciliationComplete ? grossMarginLabel(dashboard.platform_income_cents, displayedGrossProfit) : "待收入归因", note: reconciliationComplete ? "毛利占已归因收入" : "积分批次现金基础归因完成后计算" },
        ]} />

        <div className="control-finance-status" aria-label="平台经营补充指标">
          <span><strong>{formatPointAmount(dashboard.platform_recharge_points)}</strong><small>积分权益入账</small></span>
          <span><strong>{formatPointAmount(dashboard.platform_consumption_points)}</strong><small>积分实际消费</small></span>
          <span><strong>{money(dashboard.platform_recharge_cents)}</strong><small>历史人民币人工入账</small></span>
          <span><strong>{dashboard.active_company_count ?? companyRows.filter((item) => item.company_status === "active").length}</strong><small>正常企业</small></span>
          <span><strong>{totalTasks}</strong><small>任务总数</small></span>
          <span><strong>{succeededTasks}</strong><small>成功任务</small></span>
          <span><strong>{failedTasks}</strong><small>失败任务</small></span>
        </div>

        {!reconciliationComplete && (
          <div className="control-finance-warning" role="status">
            <WarningCircle size={18} weight="fill" />
            <span>{!costReconciliationComplete ? `仍有 ${dashboard.unreconciled_succeeded_count || 0} 个成功任务缺少任务级渠道成本。` : "任务级渠道成本已对账。"}{!revenueReconciliationComplete ? ` 另有 ${dashboard.unattributed_point_settlement_count || 0} 个积分结算尚未完成现金基础归因。` : " 收入现金基础已归因。"} 当前仅展示已知法币净额，不能视为最终毛利。</span>
          </div>
        )}

        <section className="control-section">
          <div className="control-section-title"><div><h2>渠道成本构成</h2><p>按中转站渠道键和渠道类型聚合真实成本。</p></div><span>{(dashboard.channel_costs || []).length} 个渠道</span></div>
          <div className="control-table-wrap">
            <table className="control-table">
              <thead><tr><th>渠道键</th><th>渠道类型</th><th>成本金额</th></tr></thead>
              <tbody>
                {(dashboard.channel_costs || []).map((channel) => (
                  <tr key={`${channel.channel_type}:${channel.channel_key}`}><td><code>{channel.channel_key}</code></td><td>{CHANNEL_TYPE_LABELS[channel.channel_type] || channel.channel_type}</td><td>{money(channel.amount_cents)}</td></tr>
                ))}
                {!(dashboard.channel_costs || []).length && <EmptyRows colSpan={3} message="暂无渠道成本汇总" />}
              </tbody>
            </table>
          </div>
        </section>

        <section className="control-section">
          <div className="control-section-title"><div><h2>成本账单明细</h2><p>每条记录保留外部凭证、发生时间和关联任务，便于财务复核。</p></div><span>{data.channelCosts?.total || 0} 条</span></div>
          <div className="control-table-wrap">
            <table className="control-table">
              <thead><tr><th>渠道</th><th>类型</th><th>金额</th><th>外部凭证</th><th>关联对象</th><th>来源</th><th>发生时间</th></tr></thead>
              <tbody>
                {(data.channelCosts?.items || []).map((item) => (
                  <tr key={item.id}><td><code>{item.channel_key}</code></td><td>{CHANNEL_TYPE_LABELS[item.channel_type] || item.channel_type}</td><td className={Number(item.amount_cents) < 0 ? "is-positive" : "is-negative"}>{money(item.amount_cents)}</td><td className="is-mono">{safeId(item.external_reference)}</td><td><small>{item.company_id ? `企业 ${safeId(item.company_id)}` : ""}{item.task_id ? ` 任务 ${safeId(item.task_id)}` : ""}{item.relay_job_id ? ` Relay ${safeId(item.relay_job_id)}` : ""}{!item.company_id && !item.task_id && !item.relay_job_id ? "未关联" : ""}</small></td><td>{item.source || "manual"}</td><td>{shortDate(item.occurred_at)}</td></tr>
                ))}
                {!(data.channelCosts?.items || []).length && <EmptyRows colSpan={7} message="暂无成本账单" />}
              </tbody>
            </table>
          </div>
          <PageLoadMore loaded={data.channelCosts?.items?.length} total={data.channelCosts?.total} busy={paginationBusyKey === "platform-costs"} onLoadMore={() => loadMore("platform-costs")} noun="条成本记录" />
        </section>

        <section className="control-section">
          <div className="control-section-title"><div><h2>企业积分用量</h2><p>入账、可用积分、消费与预留按企业共享积分钱包汇总，不用于反推人民币收入。</p></div><span>{dashboard.total_companies || 0} 家</span></div>
          <div className="control-table-wrap">
            <table className="control-table">
              <thead><tr><th>企业</th><th>状态</th><th>权益入账</th><th>可用权益</th><th>实际消费</th><th>已预留权益</th><th>任务</th><th>任务结果</th></tr></thead>
              <tbody>
                {companyRows.map((company) => {
                  const rate = company.task_count ? Math.round((company.succeeded_count / company.task_count) * 100) : 0;
                  return <tr key={company.company_id}><td><strong>{company.company_name}</strong><small>{safeId(company.company_id)}</small></td><td><StatusPill value={company.company_status} /></td><td>{billingAmountLabel(company, { pointsField: "recharge_points", centsField: "recharge_cents" })}</td><td>{billingAmountLabel(company, { pointsField: "available_points", centsField: "available_cents" })}</td><td>{billingAmountLabel(company, { pointsField: "consumption_points", centsField: "consumption_cents" })}</td><td>{billingAmountLabel(company, { pointsField: "reserved_points", centsField: "reserved_cents" })}</td><td>{company.task_count}</td><td><strong>{rate}%</strong><small>{company.succeeded_count} 成功，{company.failed_count} 失败</small></td></tr>;
                })}
                {!companyRows.length && <EmptyRows colSpan={8} message="暂无企业数据" />}
              </tbody>
            </table>
          </div>
          <PageLoadMore loaded={companyRows.length} total={dashboard.total_companies} busy={paginationBusyKey === "platform-dashboard"} onLoadMore={() => loadMore("platform-dashboard")} noun="家企业" />
        </section>
      </>
    );
  };

  const renderAdminReports = () => {
    if (!hasPlatformConsumptionEvidence(data)) {
      const isPending = loading && !error;
      return (
        <>
          <PageHeader eyebrow="平台财务" title="跨公司消费报表" detail="按公司、员工、模型与结算时间核对全平台实际消费；仅成功结算进入本表。">
            {!isPending && <QuietButton onClick={() => {
              const cleared = { ...EMPTY_ADMIN_REPORT_FILTERS };
              setAdminReportFilters(cleared);
              setAppliedAdminReportFilters(cleared);
              load("reports", cleared);
            }}>清除筛选并重试</QuietButton>}
          </PageHeader>
          <CollectionState
            state={isPending ? "loading" : "error"}
            title={isPending ? "正在核验平台消费报表" : "平台消费报表证据不可用"}
            detail={isPending
              ? "成功结算明细返回前不会展示零笔消费。"
              : error || "当前筛选没有获得完整的成功结算记录；不会继续展示上一组筛选结果。"}
            onRetry={isPending ? undefined : () => load("reports")}
          />
        </>
      );
    }
    return (
    <>
      <PageHeader eyebrow="平台财务" title="跨公司消费报表" detail="按公司、员工、模型与结算时间核对全平台实际消费；仅成功结算进入本表。">
        <PrimaryButton onClick={exportAdminReport} disabled={busy}><DownloadSimple size={16} /> 导出 CSV</PrimaryButton>
      </PageHeader>
      <form
        className="control-filterbar"
        onSubmit={(event) => {
          event.preventDefault();
          const applied = { ...adminReportFilters };
          setAppliedAdminReportFilters(applied);
          load("reports", applied);
        }}
      >
        {canUsePlatformPermission("platform.companies.read") ? (
          <label><span>公司</span><select aria-label="按公司筛选平台消费报表" value={adminReportFilters.company_id} onChange={(event) => setAdminReportFilters((current) => ({ ...current, company_id: event.target.value }))}><option value="">全部公司</option>{(data.companies?.items || []).map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</select></label>
        ) : (
          <label><span>公司 ID</span><input aria-label="按公司 ID 筛选平台消费报表" type="search" value={adminReportFilters.company_id} onChange={(event) => setAdminReportFilters((current) => ({ ...current, company_id: event.target.value }))} placeholder="输入企业 ID" /><small>未授予企业目录权限，可按已知 ID 精确筛选。</small></label>
        )}
        <label><span>员工</span><input aria-label="按员工姓名或邮箱筛选平台消费报表" type="search" value={adminReportFilters.employee_query} onChange={(event) => setAdminReportFilters((current) => ({ ...current, employee_query: event.target.value }))} placeholder="姓名或邮箱" /></label>
        {canUsePlatformPermission("platform.models.read") ? (
          <label><span>模型</span><select aria-label="按模型筛选平台消费报表" value={adminReportFilters.model_id} onChange={(event) => setAdminReportFilters((current) => ({ ...current, model_id: event.target.value }))}><option value="">全部模型</option>{data.adminModels.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</select></label>
        ) : (
          <label><span>模型 ID</span><input aria-label="按模型 ID 筛选平台消费报表" type="search" value={adminReportFilters.model_id} onChange={(event) => setAdminReportFilters((current) => ({ ...current, model_id: event.target.value }))} placeholder="输入模型 ID" /><small>未授予模型目录权限，可按已知 ID 精确筛选。</small></label>
        )}
        <label><span>开始时间</span><input aria-label="平台消费报表开始时间" type="datetime-local" value={adminReportFilters.start_time} max={adminReportFilters.end_time || undefined} onChange={(event) => setAdminReportFilters((current) => ({ ...current, start_time: event.target.value }))} /></label>
        <label><span>结束时间</span><input aria-label="平台消费报表结束时间" type="datetime-local" value={adminReportFilters.end_time} min={adminReportFilters.start_time || undefined} onChange={(event) => setAdminReportFilters((current) => ({ ...current, end_time: event.target.value }))} /></label>
        <button type="submit">应用筛选</button>
        <span>{demoMode ? filteredDemoAdminConsumption.length : data.adminConsumption?.total || 0} 笔 · 共 {demoMode
          ? formatPointAmount(filteredDemoAdminConsumption.reduce((sum, item) => sum + Number(item.amount_points || 0), 0))
          : billingTotalsLabel(data.adminConsumption, { pointsField: "total_amount_points", centsField: "total_amount_cents" })}</span>
      </form>
      <section className="control-section">
        <div className="control-section-title"><div><h2>分单位消费明细</h2><p>积分与历史人民币任务按各自提交快照展示；任何积分消费都不用于反推人民币收入。</p></div><span>{demoMode ? filteredDemoAdminConsumption.length : data.adminConsumption?.total || 0} 笔</span></div>
        <div className="control-table-wrap">
          <table className="control-table">
            <thead><tr><th>公司</th><th>员工</th><th>模型</th><th>计费方式</th><th>单价</th><th>数量</th><th>金额</th><th>结算时间</th></tr></thead>
            <tbody>
              {filteredDemoAdminConsumption.map((item) => (
                <tr key={item.ledger_entry_id}><td><strong>{item.company_name}</strong><small>{safeId(item.company_id)}</small></td><td><strong>{item.employee_display_name}</strong><small>{item.employee_email}</small></td><td>{item.model_display_name}</td><td>{pricingModeLabel(item.pricing_mode)}</td><td>{billingAmountLabel(item, { pointsField: "unit_price_points", centsField: "unit_price_cents" })}/{item.pricing_mode === "per_second" ? "秒" : "条"}</td><td>{pricingQuantityLabel(item)}</td><td>{billingAmountLabel(item, { pointsField: "amount_points", centsField: "amount_cents" })}</td><td>{shortDate(item.consumed_at)}</td></tr>
              ))}
              {!filteredDemoAdminConsumption.length && <EmptyRows colSpan={8} message="当前筛选条件下没有消费记录" />}
            </tbody>
          </table>
        </div>
        <PageLoadMore loaded={data.adminConsumption?.items?.length} total={data.adminConsumption?.total} busy={paginationBusyKey === "platform-consumption"} onLoadMore={() => loadMore("platform-consumption")} noun="笔消费" />
      </section>
    </>
    );
  };

  const renderCompanies = () => (
    <PlatformCompaniesView
      data={data}
      busy={busy}
      demoMode={demoMode}
      ownerInvitationLinks={ownerInvitationLinks}
      paginationBusyKey={paginationBusyKey}
      canUsePlatformPermission={canUsePlatformPermission}
      companyDashboardRow={companyDashboardRow}
      copyOwnerInvitationLink={copyOwnerInvitationLink}
      loadMore={loadMore}
      makeOperationKey={makeOperationKey}
      openCompanyControl={openCompanyControl}
      setCompanyStatus={setCompanyStatus}
      setDrawer={setDrawer}
    />
  );

  const renderAdminModels = () => (
    <PlatformModelsView
      data={data}
      loading={loading}
      busy={busy}
      canUsePlatformPermission={canUsePlatformPermission}
      demoMode={demoMode}
      approveRelayModelRevision={approveRelayModelRevision}
      loadRelayCapabilityHistory={loadRelayCapabilityHistory}
      refreshModels={() => load("models")}
      reconcileRelayModels={reconcileRelayModels}
      syncRelayModelCandidate={syncRelayModelCandidate}
      setDrawer={setDrawer}
      setModelState={setModelState}
      savePersonalModelGrant={savePersonalModelGrant}
      previewPersonalModelGrantBatch={previewPersonalModelGrantBatch}
      savePersonalModelGrantBatch={savePersonalModelGrantBatch}
    />
  );

  const renderResources = () => (
    <PlatformResourcesView
      data={data}
      canUsePlatformPermission={canUsePlatformPermission}
      setDrawer={setDrawer}
    />
  );

  const renderAudit = () => (
    <PlatformAuditView
      data={data}
      paginationBusyKey={paginationBusyKey}
      loadMore={loadMore}
    />
  );


  return {
    users: renderGlobalUsers,
    overview: renderPlatformOverview,
    reports: renderAdminReports,
    companies: renderCompanies,
    models: renderAdminModels,
    resources: renderResources,
    audit: renderAudit,
  };
}
