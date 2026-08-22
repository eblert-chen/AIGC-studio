import {
  Alarm,
  ArrowRight,
  Check,
  CheckCircle,
  Clock,
  Eye,
  Minus,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  changeTone,
  entitlementStateLabel,
  formatDurationSeconds,
  formatInteger,
  formatMoneyFromCents,
  formatMoneyYuan,
  formatPercent,
  resolveEntitlementState,
  riskLabel,
} from "../adminConsoleUtils.js";
import {
  changeIcon,
  ChartDataTable,
  ChartTooltip,
  CHART_COLORS,
  cx,
  DataReadinessStatus,
  DatasetState,
  EmptyState,
  formatModelAxisTick,
  KIND_LABELS,
  PanelHeader,
  Priority,
  StatusPill,
  TableScroller,
} from "./operationsShared.jsx";

function MetricStrip({ items }) {
  if (!items.length) return <EmptyState title="没有经营指标" />;
  return (
    <div className="ops-metric-strip">
      {items.map((item) => {
        return (
          <div className="ops-metric" key={item.key}>
            <span>{item.label}</span>
            <strong>{item.valuePercent != null ? formatPercent(item.valuePercent, 2) : formatMoneyFromCents(item.valueCents)}</strong>
            <MetricComparison label="环比" value={item.change} status={item.comparisonStatus} />
            {Object.hasOwn(item, "yearOverYearChange")
              ? <MetricComparison label="同比" value={item.yearOverYearChange} status={item.yearOverYearStatus} />
              : null}
          </div>
        );
      })}
    </div>
  );
}

function MetricComparison({ label, value, status }) {
  if (value == null) {
    return <small className="is-flat">{label} 暂无对比{status === "partial" ? "（数据不完整）" : ""}</small>;
  }
  const Icon = changeIcon(value);
  return (
    <small className={cx(`is-${changeTone(value)}`)}>
      <Icon size={14} />{label} {Math.abs(value).toFixed(1)}%{status === "partial" ? "（部分数据）" : ""}
    </small>
  );
}

function BusinessTrendChart({ data }) {
  if (!data.length) return <EmptyState title="没有经营趋势数据" />;
  return (
    <>
      <div className="ops-chart is-business" role="img" aria-label="充值、收入、渠道成本和毛利趋势图">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 16, right: 18, left: -2, bottom: 0 }}>
            <CartesianGrid stroke="var(--ops-chart-grid)" vertical={false} />
            <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} interval="preserveStartEnd" minTickGap={24} />
            <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} tickFormatter={(value) => `${Math.round(value / 10000)}万`} />
            <Tooltip content={<ChartTooltip valueFormatter={formatMoneyYuan} />} />
            <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="recharge" name="充值" fill="var(--ops-chart-recharge)" radius={[2, 2, 0, 0]} barSize={14} />
            <Line type="monotone" dataKey="revenue" name="结算收入" stroke={CHART_COLORS.primary} strokeWidth={2.2} dot={false} />
            <Line type="monotone" dataKey="cost" name="渠道成本" stroke={CHART_COLORS.orange} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="grossProfit" name="毛利" stroke={CHART_COLORS.blue} strokeWidth={2} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <ChartDataTable
        caption="平台经营趋势数值"
        rows={data}
        columns={[
          { key: "date", label: "日期" },
          { key: "recharge", label: "充值", format: formatMoneyYuan },
          { key: "revenue", label: "结算收入", format: formatMoneyYuan },
          { key: "cost", label: "渠道成本", format: formatMoneyYuan },
          { key: "grossProfit", label: "毛利", format: formatMoneyYuan },
        ]}
      />
    </>
  );
}

export function OperatingCockpitScreen({ data, onNavigate, onExceptionSelect, onCompanyOpen, onModelOpen, onRetry }) {
  const urgent = data.exceptions.filter((item) => item.priority === "P1").slice(0, 4);
  const source = data.sourceStatus || {};
  return (
    <>
      {source.readiness === "available"
        ? <DataReadinessStatus readiness={data.dataReadiness} />
        : <DatasetState status={source.readiness} label="生产数据就绪状态" detail={data.sourceErrors?.readiness} onRetry={onRetry} compact />}
      <section className="ops-panel ops-cockpit-metrics">
        {source.operating === "available"
          ? <MetricStrip items={data.business.metrics} />
          : <DatasetState status={source.operating} label="经营指标" detail={data.sourceErrors?.operating} onRetry={onRetry} />}
      </section>
      <div className="ops-cockpit-grid">
        <section className="ops-panel">
          <PanelHeader title="经营趋势" detail="充值现金流、成功结算收入、真实渠道成本与毛利分开统计。" />
          {source.operating === "available"
            ? <BusinessTrendChart data={data.business.trend} />
            : <DatasetState status={source.operating} label="经营趋势" detail={data.sourceErrors?.operating} onRetry={onRetry} />}
        </section>
        <section className="ops-panel ops-action-panel">
          <PanelHeader title="需要处理" detail={source.exceptions === "available" ? `${data.exceptions.length} 项异常等待确认` : "异常数量尚未核验"} action={<button className="ops-text-button" type="button" onClick={() => onNavigate("task-operations")}>进入运营中心 <ArrowRight size={14} /></button>} />
          {source.exceptions === "available" ? (
            <div className="ops-action-list">
              {urgent.map((item) => (
                <button key={item.id} type="button" onClick={() => onExceptionSelect(item)}>
                  <Priority value={item.priority} />
                  <span><strong>{item.title}</strong><small>{item.description}</small></span>
                  <ArrowRight size={16} />
                </button>
              ))}
              {!urgent.length ? <EmptyState title="没有高优先级异常" detail="服务端已确认当前没有 P1 异常。" /> : null}
            </div>
          ) : <DatasetState status={source.exceptions} label="异常队列" detail={data.sourceErrors?.exceptions} onRetry={onRetry} />}
        </section>
      </div>
      <div className="ops-cockpit-lower">
        <section className="ops-panel">
          <PanelHeader title="模型利润" detail="收入、渠道成本、毛利与成本缺失率必须一起看。" action={<button type="button" className="ops-text-button" onClick={() => onNavigate("model-profit")}>完整分析 <ArrowRight size={14} /></button>} />
          {source.profitability === "available" ? (
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead><tr><th>模型</th><th>调用量</th><th>收入</th><th>渠道成本</th><th>毛利</th><th>毛利率</th><th>成本缺失</th></tr></thead>
                <tbody>
                  {data.modelProfitability.slice(0, 5).map((row) => <tr key={row.id} onClick={() => onModelOpen?.(row)} className={onModelOpen ? "is-clickable" : ""}><td>{onModelOpen ? <button className="ops-model-row-link" type="button" aria-label={`查看 ${row.model} 模型利润详情`} onClick={(event) => { event.stopPropagation(); onModelOpen(row); }}>{row.model}</button> : <strong>{row.model}</strong>}</td><td>{formatInteger(row.calls)}</td><td>{formatMoneyFromCents(row.revenueCents)}</td><td>{formatMoneyFromCents(row.costCents)}</td><td className="is-positive">{formatMoneyFromCents(row.grossProfitCents)}</td><td>{formatPercent(row.grossMargin, 2)}</td><td className={row.missingCostRate ? "is-negative" : ""}>{formatPercent(row.missingCostRate)}</td></tr>)}
                  {!data.modelProfitability.length ? <tr><td colSpan="7"><EmptyState title="当前周期没有模型利润数据" detail="服务端已返回空的模型盈利结果。" /></td></tr> : null}
                </tbody>
              </table>
            </div>
          ) : <DatasetState status={source.profitability} label="模型利润" detail={data.sourceErrors?.profitability} onRetry={onRetry} />}
        </section>
        <section className="ops-panel">
          <PanelHeader title="企业排名" detail="按成功结算收入排序" action={<button type="button" className="ops-text-button" onClick={() => onNavigate("company-health")}>企业健康 <ArrowRight size={14} /></button>} />
          {source.dashboard === "available" ? (
            <ol className="ops-ranking-list">
              {data.business.companyRanking.map((row, index) => (
                <li key={row.id}>
                  <span className="ops-rank-number">{String(index + 1).padStart(2, "0")}</span>
                  {onCompanyOpen
                    ? <button type="button" onClick={() => onCompanyOpen(row)}><strong>{row.name}</strong><small>{formatInteger(row.taskCount)} 个任务 · 成功率 {formatPercent(row.successRate)}</small></button>
                    : <span className="ops-ranking-copy"><strong>{row.name}</strong><small>{formatInteger(row.taskCount)} 个任务 · 成功率 {formatPercent(row.successRate)}</small></span>}
                  <span><strong>{formatMoneyFromCents(row.revenueCents)}</strong><small>余额 {formatMoneyFromCents(row.balanceCents)}</small></span>
                </li>
              ))}
              {!data.business.companyRanking.length ? <EmptyState title="没有企业排行数据" detail="服务端已确认当前周期没有企业排行记录。" /> : null}
            </ol>
          ) : <DatasetState status={source.dashboard} label="企业排名" detail={data.sourceErrors?.dashboard} onRetry={onRetry} />}
        </section>
      </div>
    </>
  );
}

export function ModelProfitabilityScreen({ data, onModelOpen, onRetry }) {
  // Recharts forwards arbitrary data keys to multiple SVG rectangles. Keeping
  // the row `id` in chart payloads therefore creates duplicate DOM ids when a
  // model is rendered in each revenue/cost/profit series.
  const chartRows = data.modelProfitability.map(({ id: _id, ...row }) => row);
  const sourceStatus = data.sourceStatus?.profitability || "unavailable";
  if (sourceStatus !== "available") {
    return <DatasetState status={sourceStatus} label="模型盈利数据" detail={data.sourceErrors?.profitability} onRetry={onRetry} />;
  }
  return (
    <>
      <section className="ops-panel">
        <PanelHeader title="模型收入、成本与毛利" detail="成本缺失不会被静默当作零；缺失比例会与利润口径同时展示。" />
        {data.modelProfitability.length ? (
          <div className="ops-chart is-business" role="img" aria-label="各模型收入成本毛利对比图">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartRows} margin={{ top: 16, right: 18, left: 2, bottom: 4 }}>
                <CartesianGrid stroke="var(--ops-chart-grid)" vertical={false} />
                <XAxis dataKey="model" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} tickFormatter={formatModelAxisTick} interval="preserveStartEnd" minTickGap={10} />
                <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} tickFormatter={(value) => `${Math.round(value / 1000000)}万`} />
                <Tooltip content={<ChartTooltip valueFormatter={formatMoneyFromCents} />} />
                <Legend iconType="square" wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="revenueCents" name="收入" fill={CHART_COLORS.primary} radius={[2, 2, 0, 0]} />
                <Bar dataKey="costCents" name="渠道成本" fill={CHART_COLORS.orange} radius={[2, 2, 0, 0]} />
                <Bar dataKey="grossProfitCents" name="毛利" fill={CHART_COLORS.blue} radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : <EmptyState title="没有模型利润数据" />}
      </section>
      <section className="ops-panel">
        <PanelHeader title="模型盈利明细" detail="点击模型进入目录或定价配置。" />
        <TableScroller hasActions label="模型盈利明细">
          <table className="ops-table">
            <thead><tr><th>模型</th><th>调用量</th><th>收入</th><th>渠道成本</th><th>毛利</th><th>毛利率</th><th>成功率</th><th>平均耗时</th><th>成本缺失率</th><th>操作</th></tr></thead>
            <tbody>
              {data.modelProfitability.map((row) => (
                <tr key={row.id}>
                  <td><strong>{row.model}</strong></td><td>{formatInteger(row.calls)}</td><td>{formatMoneyFromCents(row.revenueCents)}</td><td>{formatMoneyFromCents(row.costCents)}</td><td className="is-positive">{formatMoneyFromCents(row.grossProfitCents)}</td><td>{formatPercent(row.grossMargin, 2)}</td><td className={row.successRate < 90 ? "is-negative" : "is-positive"}>{formatPercent(row.successRate, 2)}</td><td>{formatDurationSeconds(row.avgSeconds)}</td><td className={row.missingCostRate ? "is-negative" : ""}>{formatPercent(row.missingCostRate)}</td><td><button className="ops-table-link" type="button" onClick={() => onModelOpen?.(row)} disabled={!onModelOpen}>{onModelOpen ? "定价与授权" : "只读"}</button></td>
                </tr>
              ))}
              {!data.modelProfitability.length ? <tr><td colSpan="10"><EmptyState /></td></tr> : null}
            </tbody>
          </table>
        </TableScroller>
      </section>
    </>
  );
}

export function CompanyHealthScreen({ data, onCompanyOpen, onRetry }) {
  const sourceStatus = data.sourceStatus?.companyHealth || "unavailable";
  if (sourceStatus !== "available") {
    return <DatasetState status={sourceStatus} label="企业健康数据" detail={data.sourceErrors?.companyHealth} onRetry={onRetry} />;
  }
  const counts = data.companyHealth.reduce((result, company) => ({ ...result, [company.risk]: (result[company.risk] || 0) + 1 }), {});
  const hasCompanyDetails = Boolean(onCompanyOpen);
  return (
    <>
      <section className="ops-panel">
        <div className="ops-health-summary">
          {[
            { key: "critical", label: "高风险", icon: Alarm },
            { key: "warning", label: "需关注", icon: WarningCircle },
            { key: "inactive", label: "不活跃", icon: Clock },
            { key: "healthy", label: "健康", icon: CheckCircle },
          ].map(({ key, label, icon: Icon }) => <span className={cx(`is-${key}`)} key={key}><Icon size={20} /><small>{label}</small><strong>{counts[key] || 0}</strong></span>)}
        </div>
      </section>
      <section className="ops-panel">
        <PanelHeader title="企业健康清单" detail="余额不足、异常消费、长期预留、失败率、活跃度和权益到期统一进入风险判断。" />
        <TableScroller hasActions label="企业健康清单">
          <table className="ops-table">
            <thead><tr><th>企业</th><th>健康状态</th><th>可用余额</th><th>未活跃</th><th>消费环比</th><th>最长预留</th><th>任务失败率</th><th>到期权益</th><th>风险原因</th>{hasCompanyDetails ? <th>操作</th> : null}</tr></thead>
            <tbody>
              {data.companyHealth.map((row) => (
                <tr key={row.id}>
                  <td><strong>{row.name}</strong></td>
                  <td><StatusPill value={row.risk} label={riskLabel(row.risk)} /></td>
                  <td className={row.balanceCents < 500000 ? "is-negative" : ""}>{formatMoneyFromCents(row.balanceCents)}</td>
                  <td>{row.daysInactive ? `${row.daysInactive} 天` : "今天活跃"}</td>
                  <td className={cx(`is-${changeTone(row.consumptionChange)}`)}>{row.consumptionChange > 0 ? "+" : ""}{formatPercent(row.consumptionChange)}</td>
                  <td className={row.reservationAgeHours >= 168 ? "is-negative" : ""}>{row.reservationAgeHours ? `${row.reservationAgeHours}h` : "—"}</td>
                  <td className={row.failureRate >= 10 ? "is-negative" : ""}>{formatPercent(row.failureRate)}</td>
                  <td>{row.entitlementsExpiring || 0}</td>
                  <td><span className="ops-reason-summary">{row.reasons?.join("；") || "未发现异常"}</span></td>
                  {hasCompanyDetails ? <td><button className="ops-table-link" type="button" onClick={() => onCompanyOpen(row)}>企业全景</button></td> : null}
                </tr>
              ))}
              {!data.companyHealth.length ? <tr><td colSpan={hasCompanyDetails ? 10 : 9}><EmptyState title="没有企业健康数据" detail="服务端已确认当前周期没有企业健康记录。" /></td></tr> : null}
            </tbody>
          </table>
        </TableScroller>
      </section>
    </>
  );
}

function EntitlementCell({ state, onClick, readOnly = false }) {
  const Icon = state === "enabled" ? Check : state === "disabled" ? Minus : state === "expired" || state === "retired" ? X : Clock;
  return (
    <button
      type="button"
      className={cx("ops-entitlement-cell", `is-${state}`)}
      onClick={onClick}
      aria-label={`${readOnly ? "查看" : "配置"}${entitlementStateLabel(state)}权益详情`}
      title={`${readOnly ? "查看" : "配置"}：${entitlementStateLabel(state)}`}
    >
      <Icon size={14} weight="bold" />
      <span>{entitlementStateLabel(state)}</span>
    </button>
  );
}

export function toggleSetValue(current, value, checked) {
  const next = new Set(current);
  if (checked ?? !next.has(value)) next.add(value);
  else next.delete(value);
  return next;
}

export function EntitlementMatrixScreen({
  data,
  selectedCompanyIds,
  onSelectedCompanyIds,
  selectedProductIds,
  onSelectedProductIds,
  batchMode,
  onBatchMode,
  copySourceId,
  onCopySourceId,
  templateId,
  onTemplateId,
  onPreview,
  onCellOpen,
  onRetry,
  readOnly = false,
}) {
  const allCompaniesSelected = data.companies.length > 0 && selectedCompanyIds.size === data.companies.length;
  const matrixStatus = data.sourceStatus?.matrix || "unavailable";
  const coverageStatus = data.sourceStatus?.coverage || "unavailable";
  return (
    <>
      <section className="ops-panel ops-entitlement-intro">
        <div>
          <h2>一张表完成模型、功能、智能体与外部 API 分发</h2>
          <p>单元格显示真实授权状态；点击可配置企业价、能力限制、配额、并发和有效期。批量操作会先展示影响范围。</p>
        </div>
        <div className="ops-state-legend">
          {["enabled", "disabled", "unconfigured", "scheduled", "expiring", "expired", "retired"].map((state) => <span key={state} className={cx(`is-${state}`)}><i />{entitlementStateLabel(state)}</span>)}
        </div>
      </section>
      {coverageStatus === "available" && data.entitlementCoverage.length ? (
        <section className="ops-panel ops-coverage-panel">
          <PanelHeader title="企业授权覆盖率" detail="覆盖率按当前已生效且启用的企业授权计算；停用、待生效和过期单独列示。" compact />
          <div className="ops-coverage-list">
            {data.entitlementCoverage.map((item) => (
              <div className="ops-coverage-row" key={`${item.kind}-${item.id}`}>
                <span><small>{KIND_LABELS[item.kind] || item.kind}</small><strong>{item.name}</strong></span>
                <div className="ops-coverage-track" aria-label={`${item.name} 覆盖率 ${item.coverageRate == null ? "暂无基数" : formatPercent(item.coverageRate)}`}><i style={{ width: `${Math.max(0, Math.min(100, item.coverageRate || 0))}%` }} /></div>
                <strong>{item.coverageRate == null ? "—" : formatPercent(item.coverageRate)}</strong>
                <small>启用 {formatInteger(item.enabledCompanies)} · 停用 {formatInteger(item.disabledCompanies)} · 待生效 {formatInteger(item.scheduledCompanies)} · 过期 {formatInteger(item.expiredCompanies)}</small>
              </div>
            ))}
          </div>
        </section>
      ) : coverageStatus === "available" ? (
        <section className="ops-panel"><EmptyState title="没有权益覆盖率数据" detail="服务端已确认当前没有可统计的权益目录。" /></section>
      ) : <DatasetState status={coverageStatus} label="权益覆盖率" detail={data.sourceErrors?.coverage} onRetry={onRetry} />}
      {matrixStatus === "available" ? <section className="ops-panel">
        <div className="ops-batch-toolbar">
          <span className="ops-selection-count">已选择 <strong>{selectedCompanyIds.size}</strong> 家企业 · <strong>{selectedProductIds.size}</strong> 项权益</span>
          <label><span>批量动作</span><select value={batchMode} onChange={(event) => onBatchMode(event.target.value)} disabled={readOnly}><option value="enable">批量开通</option><option value="disable">批量停用</option><option value="copy">复制企业配置</option><option value="template">套用套餐模板</option></select></label>
          {batchMode === "copy" ? (
            <label><span>配置来源</span><select value={copySourceId} onChange={(event) => onCopySourceId(event.target.value)} disabled={readOnly}><option value="">选择企业</option>{data.companies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}</select></label>
          ) : null}
          {batchMode === "template" ? (
            <label><span>套餐模板</span><select value={templateId} onChange={(event) => onTemplateId(event.target.value)} disabled={readOnly}><option value="">{data.entitlementTemplates.length ? "选择模板" : "暂无服务端模板"}</option>{data.entitlementTemplates.map((template) => <option value={template.id} key={template.id}>{template.name}</option>)}</select></label>
          ) : null}
          <button className="ops-primary-button" type="button" onClick={onPreview} disabled={readOnly || !selectedCompanyIds.size || ((batchMode === "enable" || batchMode === "disable") && !selectedProductIds.size) || (batchMode === "copy" && !copySourceId) || (batchMode === "template" && !templateId)}>
            <Eye size={16} /> 预览影响
          </button>
        </div>
        <div className="ops-table-wrap ops-matrix-wrap">
          <table className="ops-table ops-entitlement-matrix">
            <thead>
              <tr>
                <th className="is-sticky">
                  <label className="ops-check"><input type="checkbox" checked={allCompaniesSelected} disabled={readOnly} onChange={(event) => onSelectedCompanyIds(event.target.checked ? new Set(data.companies.map((item) => item.id)) : new Set())} /><span>企业</span></label>
                </th>
                {data.entitlementProducts.map((product) => (
                  <th key={product.id}>
                    <label className="ops-product-head">
                      <input type="checkbox" checked={selectedProductIds.has(product.id)} disabled={readOnly} onChange={(event) => onSelectedProductIds(toggleSetValue(selectedProductIds, product.id, event.target.checked))} />
                      <small>{KIND_LABELS[product.kind] || product.kind}</small>
                      <span>{product.name}</span>
                    </label>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.companies.map((company) => (
                <tr key={company.id}>
                  <td className="is-sticky">
                    <label className="ops-company-select"><input type="checkbox" checked={selectedCompanyIds.has(company.id)} disabled={readOnly} onChange={(event) => onSelectedCompanyIds(toggleSetValue(selectedCompanyIds, company.id, event.target.checked))} /><span><strong>{company.name}</strong><small>{company.plan} · {company.status === "active" ? "正常" : "已停用"}</small></span></label>
                  </td>
                  {data.entitlementProducts.map((product) => {
                    const state = resolveEntitlementState(data.entitlementGrants, company.id, product.id);
                    const cellReadOnly = readOnly || product.status === "retired";
                    return <td key={product.id}><EntitlementCell state={state} readOnly={cellReadOnly} onClick={() => onCellOpen(company, product, cellReadOnly)} /></td>;
                  })}
                </tr>
              ))}
              {!data.companies.length ? <tr><td colSpan={data.entitlementProducts.length + 1}><EmptyState title="没有可配置企业" /></td></tr> : null}
            </tbody>
          </table>
        </div>
      </section> : <DatasetState status={matrixStatus} label="企业权益矩阵" detail={data.sourceErrors?.matrix} onRetry={onRetry} />}
    </>
  );
}
