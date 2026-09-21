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

function formatOptionalMoneyFromCents(value, fallback = "待核验") {
  return value == null ? fallback : formatMoneyFromCents(value);
}

function formatOptionalMoneyYuan(value) {
  return value == null ? "待核验" : formatMoneyYuan(value);
}

function formatBillingAmount(row, centsKey, pointsKey, fallback = "待核验") {
  if (row?.billingUnit === "POINT" && row?.billingVersion === 2) {
    return row[pointsKey] == null ? fallback : `${formatInteger(row[pointsKey])} 积分`;
  }
  if (row?.billingUnit === "CNY_CENT" && row?.billingVersion === 1) {
    return formatOptionalMoneyFromCents(row[centsKey], fallback);
  }
  return fallback;
}

function formatCompanyConsumption(row) {
  const value = formatBillingAmount(row, "consumptionCents", "consumptionPoints");
  return row?.billingUnit === "POINT" ? `消费 ${value}` : `历史结算 ${value}`;
}

function modelProfitIsComplete(row) {
  return row.costReconciliationStatus === "complete"
    && row.revenueReconciliationStatus === "complete"
    && row.grossProfitCents != null;
}

function modelProfitValue(row) {
  return modelProfitIsComplete(row)
    ? row.grossProfitCents
    : row.knownGrossProfitCents;
}

function modelProfitLabel(row) {
  if (modelProfitIsComplete(row)) return "最终毛利";
  if (row.revenueReconciliationStatus !== "complete") return "毛利待收入归因";
  return "已知毛利";
}

function modelProfitPendingReason(row) {
  if (row.revenueReconciliationStatus === "unavailable") return "积分收入待归因";
  if (row.revenueReconciliationStatus === "incomplete") return "结算收入待核验";
  return "成本未完整";
}

function ModelRevenueCell({ row }) {
  if (row.revenueReconciliationStatus === "complete") {
    return <td>{formatMoneyFromCents(row.revenueCents)}</td>;
  }
  const unavailable = row.revenueReconciliationStatus === "unavailable";
  const count = unavailable ? row.revenueUnavailableTaskCount : row.revenueMissingTaskCount;
  return (
    <td className="is-warning">
      <strong>{unavailable ? "待归因" : "待核验"}</strong>
      <small>{formatInteger(count)} 个成功任务{unavailable ? "缺少现金收入归因" : "缺少结算收入证据"}</small>
    </td>
  );
}

function UnattributedProviderCostNotice({ summary }) {
  const value = summary?.unattributedProviderCostCents;
  if (value == null) {
    return (
      <div className="ops-callout is-warning">
        <WarningCircle size={20} />
        <div><strong>未归因渠道成本待核验</strong><span>当前数据没有独立于模型行的未归因成本证据；下方模型行不能代表全量渠道成本。</span></div>
      </div>
    );
  }
  const requiresReconciliation = value !== 0;
  const Icon = requiresReconciliation ? WarningCircle : CheckCircle;
  return (
    <div className={cx("ops-callout", requiresReconciliation && "is-warning")}>
      <Icon size={20} />
      <div>
        <strong>未归因渠道成本：{formatMoneyFromCents(value)}</strong>
        <span>{requiresReconciliation
          ? "这部分成本没有任务或模型归属，未计入下方任何模型毛利，必须单独对账。"
          : "服务端确认当前周期没有脱离任务或模型归属的渠道成本。"}</span>
      </div>
    </div>
  );
}

function ModelRevenueReconciliationNotice({ summary }) {
  if (!summary || summary.revenueReconciliationStatus === "complete") return null;
  const unavailable = summary.revenueReconciliationStatus === "unavailable";
  const count = unavailable
    ? summary.revenueUnavailableTaskCount
    : summary.revenueMissingTaskCount;
  return (
    <div className="ops-callout is-warning">
      <WarningCircle size={20} />
      <div>
        <strong>{unavailable ? "积分收入待归因" : "结算收入待核验"}</strong>
        <span>{formatInteger(count)} 个成功任务{unavailable
          ? "只有积分消费证据，没有不可变现金收入归因；不会按积分兑换锚点反推收入。"
          : "缺少结算收入证据；最终毛利和毛利率保持不可用。"}</span>
      </div>
    </div>
  );
}

function MetricValue({ item }) {
  if (item.valueKind === "points") {
    if (item.valuePoints == null || item.settlementCount == null) {
      return item.unavailableLabel || "待核验";
    }
    return `${formatInteger(item.valuePoints)} 积分 · ${formatInteger(item.settlementCount)} 笔`;
  }
  if (item.valueKind === "percent" || Object.hasOwn(item, "valuePercent")) {
    return item.valuePercent == null
      ? (item.unavailableLabel || "待核验")
      : formatPercent(item.valuePercent, 2);
  }
  return item.valueCents == null
    ? (item.unavailableLabel || "待核验")
    : formatMoneyFromCents(item.valueCents);
}

function OperatingFinanceNotice({ evidence }) {
  if (!evidence || evidence.financeStatus === "complete") return null;
  const details = [];
  if (evidence.revenueReconciliationStatus === "incomplete") {
    if (evidence.unattributedPointSettlementCount > 0) {
      details.push(
        `${formatInteger(evidence.unattributedPointSettlementCount)} 笔有效积分结算（${formatInteger(evidence.settledPoints)} 积分）尚无不可变现金归因`,
      );
    }
    if (evidence.pointSettlementMissingTaskCount > 0) {
      details.push(`${formatInteger(evidence.pointSettlementMissingTaskCount)} 个成功积分任务缺少结算分录`);
    }
    if (evidence.pointSettlementDuplicateTaskCount > 0) {
      details.push(`${formatInteger(evidence.pointSettlementDuplicateTaskCount)} 个成功积分任务存在重复结算分录`);
    }
  }
  if (evidence.costReconciliationStatus === "incomplete") {
    details.push("仍有成功任务缺少渠道成本证据");
  }
  return (
    <div className="ops-callout is-warning">
      <WarningCircle size={20} />
      <div>
        <strong>最终毛利尚未闭环</strong>
        <span>{details.join("；")}。积分只作为消费权益展示，不按兑换锚点折算为现金收入。</span>
      </div>
    </div>
  );
}

function ModelProfitCell({ row }) {
  const complete = modelProfitIsComplete(row);
  return (
    <td className={complete ? "is-positive" : "is-warning"}>
      <strong>{formatOptionalMoneyFromCents(modelProfitValue(row))}</strong>
      <small>{modelProfitLabel(row)}{complete ? "" : ` · ${modelProfitPendingReason(row)}`}</small>
    </td>
  );
}

function ModelGrossMarginValue({ row }) {
  if (!modelProfitIsComplete(row)) return modelProfitPendingReason(row);
  return row.grossMargin == null ? "暂无收入基数" : formatPercent(row.grossMargin, 2);
}

function EvidencePercent({ value, fallback = "待核验", digits = 2 }) {
  return value == null ? fallback : formatPercent(value, digits);
}

function MetricStrip({ items }) {
  if (!items.length) return <EmptyState title="没有经营指标" />;
  return (
    <div className="ops-metric-strip" role="list" aria-label="平台经营指标账页">
      {items.map((item, index) => {
        return (
          <div className={cx("ops-metric", index === 0 && "is-lead")} key={item.key} role="listitem">
            <div className="ops-metric-heading"><span>{item.label}</span><small>{String(index + 1).padStart(2, "0")}</small></div>
            <strong><MetricValue item={item} /></strong>
            <div className="ops-metric-comparisons">
              <MetricComparison label="环比" value={item.change} status={item.comparisonStatus} />
              {Object.hasOwn(item, "yearOverYearChange")
                ? <MetricComparison label="同比" value={item.yearOverYearChange} status={item.yearOverYearStatus} />
                : null}
            </div>
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
      <div className="ops-chart is-business" role="img" aria-label="人工入账、已归因法币收入、渠道成本、最终毛利和已知毛利趋势图">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 16, right: 18, left: -2, bottom: 0 }}>
            <CartesianGrid stroke="var(--ops-chart-grid)" vertical={false} />
            <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} interval="preserveStartEnd" minTickGap={24} />
            <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} tickFormatter={(value) => `${Math.round(value / 10000)}万`} />
            <Tooltip content={<ChartTooltip valueFormatter={formatOptionalMoneyYuan} />} />
            <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="recharge" name="账户人工入账" fill="var(--ops-chart-recharge)" radius={[2, 2, 0, 0]} barSize={14} />
            <Line type="monotone" dataKey="revenue" name="已归因法币收入" stroke={CHART_COLORS.primary} strokeWidth={2.2} dot={false} />
            <Line type="monotone" dataKey="cost" name="渠道成本" stroke={CHART_COLORS.orange} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="grossProfit" name="最终毛利" stroke={CHART_COLORS.blue} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="knownGrossProfit" name="已知毛利（成本未完整）" stroke={CHART_COLORS.muted} strokeWidth={2} strokeDasharray="5 4" dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <ChartDataTable
        caption="平台经营趋势数值"
        rows={data}
        columns={[
          { key: "date", label: "日期" },
          { key: "recharge", label: "账户人工入账", format: formatOptionalMoneyYuan },
          { key: "revenue", label: "已归因法币收入", format: formatOptionalMoneyYuan },
          { key: "settledPoints", label: "积分结算", format: (value) => `${formatInteger(value)} 积分` },
          { key: "pointSettlementCount", label: "积分结算笔数", format: (value) => `${formatInteger(value)} 笔` },
          { key: "cost", label: "渠道成本", format: formatOptionalMoneyYuan },
          { key: "grossProfit", label: "最终毛利", format: formatOptionalMoneyYuan },
          { key: "knownGrossProfit", label: "已知毛利（成本未完整）", format: formatOptionalMoneyYuan },
        ]}
      />
    </>
  );
}

export function OperatingCockpitScreen({ data, onNavigate, onExceptionSelect, onCompanyOpen, onModelOpen, onRetry }) {
  const urgent = data.exceptions.filter((item) => item.priority === "P1").slice(0, 4);
  const source = data.sourceStatus || {};
  return (
    <div className="ops-cockpit-canvas">
      {source.readiness === "available"
        ? <DataReadinessStatus readiness={data.dataReadiness} />
        : <DatasetState status={source.readiness} label="生产数据就绪状态" detail={data.sourceErrors?.readiness} onRetry={onRetry} compact />}
      <section className="ops-panel ops-cockpit-metrics">
        {source.operating === "available"
          ? <>
            <OperatingFinanceNotice evidence={data.business.financeEvidence} />
            <MetricStrip items={data.business.metrics} />
          </>
          : <DatasetState status={source.operating} label="经营指标" detail={data.sourceErrors?.operating} onRetry={onRetry} />}
      </section>
      <div className="ops-cockpit-grid">
        <section className="ops-panel">
          <PanelHeader title="经营趋势" detail="人工入账、已归因法币收入、积分消费和真实渠道成本分开统计；积分与现金不混算。" />
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
            <div>
              <UnattributedProviderCostNotice summary={data.modelProfitabilitySummary} />
              <ModelRevenueReconciliationNotice summary={data.modelProfitabilitySummary} />
              <div className="ops-table-wrap">
                <table className="ops-table">
                  <thead><tr><th>模型</th><th>调用量</th><th>收入</th><th>渠道成本</th><th>毛利口径</th><th>最终毛利率</th><th>成本缺失</th></tr></thead>
                  <tbody>
                    {data.modelProfitability.slice(0, 5).map((row) => (
                      <tr key={row.id} onClick={() => onModelOpen?.(row)} className={onModelOpen ? "is-clickable" : ""}>
                        <td>{onModelOpen ? <button className="ops-model-row-link" type="button" aria-label={`查看 ${row.model} 模型利润详情`} onClick={(event) => { event.stopPropagation(); onModelOpen(row); }}>{row.model}</button> : <strong>{row.model}</strong>}</td>
                        <td>{formatInteger(row.calls)}</td>
                        <ModelRevenueCell row={row} />
                        <td>{formatMoneyFromCents(row.costCents)}</td>
                        <ModelProfitCell row={row} />
                        <td><ModelGrossMarginValue row={row} /></td>
                        <td className={row.missingCostCount > 0 ? "is-negative" : ""}><EvidencePercent value={row.missingCostRate} fallback={row.calls === 0 ? "暂无任务" : "待核验"} /></td>
                      </tr>
                    ))}
                    {!data.modelProfitability.length ? <tr><td colSpan="7"><EmptyState title="当前周期没有模型利润数据" detail="服务端已返回空的模型盈利结果。" /></td></tr> : null}
                  </tbody>
                </table>
              </div>
            </div>
          ) : <DatasetState status={source.profitability} label="模型利润" detail={data.sourceErrors?.profitability} onRetry={onRetry} />}
        </section>
        <section className="ops-panel">
          <PanelHeader title="企业使用排行" detail="按任务量排序；消费权益按企业当前计费单位展示，不把积分换算为现金收入。" action={<button type="button" className="ops-text-button" onClick={() => onNavigate("company-health")}>企业健康 <ArrowRight size={14} /></button>} />
          {source.dashboard === "available" ? (
            <ol className="ops-ranking-list">
              {data.business.companyRanking.map((row, index) => (
                <li key={row.id}>
                  <span className="ops-rank-number">{String(index + 1).padStart(2, "0")}</span>
                  {onCompanyOpen
                    ? <button type="button" onClick={() => onCompanyOpen(row)}><strong>{row.name}</strong><small>{row.taskCount == null ? "任务数待核验" : `${formatInteger(row.taskCount)} 个任务`} · {row.successRate == null ? "成功率待核验" : `成功率 ${formatPercent(row.successRate)}`}</small></button>
                    : <span className="ops-ranking-copy"><strong>{row.name}</strong><small>{row.taskCount == null ? "任务数待核验" : `${formatInteger(row.taskCount)} 个任务`} · {row.successRate == null ? "成功率待核验" : `成功率 ${formatPercent(row.successRate)}`}</small></span>}
                  <span><strong>{formatCompanyConsumption(row)}</strong><small>余额 {formatBillingAmount(row, "balanceCents", "balancePoints")}</small></span>
                </li>
              ))}
              {!data.business.companyRanking.length ? <EmptyState title="没有企业排行数据" detail="服务端已确认当前周期没有企业排行记录。" /> : null}
            </ol>
          ) : <DatasetState status={source.dashboard} label="企业排名" detail={data.sourceErrors?.dashboard} onRetry={onRetry} />}
        </section>
      </div>
    </div>
  );
}

export function ModelProfitabilityScreen({ data, onModelOpen, onRetry }) {
  // Recharts forwards arbitrary data keys to multiple SVG rectangles. Keeping
  // the row `id` in chart payloads therefore creates duplicate DOM ids when a
  // model is rendered in each revenue/cost/profit series.
  const chartRows = data.modelProfitability.map(({ id: _id, ...row }) => row);
  for (const row of chartRows) {
    row.partialGrossProfitCents = row.costReconciliationStatus === "incomplete"
      ? row.knownGrossProfitCents
      : null;
  }
  const sourceStatus = data.sourceStatus?.profitability || "unavailable";
  if (sourceStatus !== "available") {
    return <DatasetState status={sourceStatus} label="模型盈利数据" detail={data.sourceErrors?.profitability} onRetry={onRetry} />;
  }
  return (
    <>
      <section className="ops-panel">
        <PanelHeader title="模型收入、成本与毛利" detail="最终毛利只在收入归因与渠道成本都完整时成立；积分不反推现金收入。" />
        <UnattributedProviderCostNotice summary={data.modelProfitabilitySummary} />
        <ModelRevenueReconciliationNotice summary={data.modelProfitabilitySummary} />
        {data.modelProfitability.length ? (
          <div className="ops-chart is-business" role="img" aria-label="各模型收入、渠道成本、最终毛利与已知毛利对比图">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartRows} margin={{ top: 16, right: 18, left: 2, bottom: 4 }}>
                <CartesianGrid stroke="var(--ops-chart-grid)" vertical={false} />
                <XAxis dataKey="model" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} tickFormatter={formatModelAxisTick} interval="preserveStartEnd" minTickGap={10} />
                <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} tickFormatter={(value) => `${Math.round(value / 1000000)}万`} />
                <Tooltip content={<ChartTooltip valueFormatter={formatOptionalMoneyFromCents} />} />
                <Legend iconType="square" wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="revenueCents" name="收入" fill={CHART_COLORS.primary} radius={[2, 2, 0, 0]} />
                <Bar dataKey="costCents" name="渠道成本" fill={CHART_COLORS.orange} radius={[2, 2, 0, 0]} />
                <Bar dataKey="grossProfitCents" name="最终毛利" fill={CHART_COLORS.blue} radius={[2, 2, 0, 0]} />
                <Bar dataKey="partialGrossProfitCents" name="已知毛利（成本未完整）" fill={CHART_COLORS.muted} radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : <EmptyState title="没有模型利润数据" />}
      </section>
      <section className="ops-panel">
        <PanelHeader title="模型盈利明细" detail="点击模型进入目录或定价配置。" />
        <TableScroller hasActions label="模型盈利明细">
          <table className="ops-table">
            <thead><tr><th>模型</th><th>调用量</th><th>收入</th><th>渠道成本</th><th>毛利口径</th><th>最终毛利率</th><th>成功率</th><th>平均耗时</th><th>成本缺失率</th><th>操作</th></tr></thead>
            <tbody>
              {data.modelProfitability.map((row) => (
                <tr key={row.id}>
                  <td><strong>{row.model}</strong></td>
                  <td>{formatInteger(row.calls)}</td>
                  <ModelRevenueCell row={row} />
                  <td>{formatMoneyFromCents(row.costCents)}</td>
                  <ModelProfitCell row={row} />
                  <td><ModelGrossMarginValue row={row} /></td>
                  <td className={row.successRate == null ? "" : row.successRate < 90 ? "is-negative" : "is-positive"}><EvidencePercent value={row.successRate} /></td>
                  <td>{row.avgSeconds == null ? "待核验" : formatDurationSeconds(row.avgSeconds)}</td>
                  <td className={row.missingCostCount > 0 ? "is-negative" : ""}><EvidencePercent value={row.missingCostRate} fallback={row.calls === 0 ? "暂无任务" : "待核验"} /></td>
                  <td><button className="ops-table-link" type="button" onClick={() => onModelOpen?.(row)} disabled={!onModelOpen}>{onModelOpen ? "定价与授权" : "只读"}</button></td>
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
        <PanelHeader title="企业健康清单" detail="余额、24 小时消费和预留严格按企业当前计费单位展示；积分不折算为现金。" />
        <TableScroller hasActions label="企业健康清单">
          <table className="ops-table">
            <thead><tr><th>企业</th><th>健康状态</th><th>可用余额</th><th>24h 消费</th><th>未活跃</th><th>消费环比</th><th>预留余额</th><th>任务失败率</th><th>到期权益</th><th>风险原因</th>{hasCompanyDetails ? <th>操作</th> : null}</tr></thead>
            <tbody>
              {data.companyHealth.map((row) => (
                <tr key={row.id}>
                  <td><strong>{row.name}</strong></td>
                  <td><StatusPill value={row.risk} label={riskLabel(row.risk)} /></td>
                  <td className={row.lowBalance ? "is-negative" : ""}>{formatBillingAmount(row, "balanceCents", "balancePoints")}</td>
                  <td>{formatBillingAmount(row, "spend24hCents", "spend24hPoints")}</td>
                  <td>{row.daysInactive == null ? "无任务记录" : row.daysInactive > 0 ? `${row.daysInactive} 天` : "今天活跃"}</td>
                  <td className={row.consumptionChange == null ? "" : cx(`is-${changeTone(row.consumptionChange)}`)}>{row.consumptionChange == null ? "未触发异常阈值" : <>{row.consumptionChange > 0 ? "+" : ""}{formatPercent(row.consumptionChange)}</>}</td>
                  <td className={row.reservationAgeHours ? "is-negative" : ""}><strong>{formatBillingAmount(row, "reservedCents", "reservedPoints")}</strong><small>{row.reservationAgeHours ? `存在超过 ${row.reservationAgeHours}h 的任务` : "无超时预留证据"}</small></td>
                  <td className={row.failureRate >= 10 ? "is-negative" : ""}>{formatPercent(row.failureRate)}</td>
                  <td>{row.entitlementsExpiring || 0}</td>
                  <td><span className="ops-reason-summary">{row.reasons?.join("；") || "未发现异常"}</span></td>
                  {hasCompanyDetails ? <td><button className="ops-table-link" type="button" onClick={() => onCompanyOpen(row)}>企业全景</button></td> : null}
                </tr>
              ))}
              {!data.companyHealth.length ? <tr><td colSpan={hasCompanyDetails ? 11 : 10}><EmptyState title="没有企业健康数据" detail="服务端已确认当前周期没有企业健康记录。" /></td></tr> : null}
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
                <strong>{item.coverageRate == null ? "待核验" : formatPercent(item.coverageRate)}</strong>
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
