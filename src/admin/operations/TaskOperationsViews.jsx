import {
  Alarm,
  ArrowRight,
  CaretDown,
  CaretLeft,
  CaretRight,
  Receipt,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  changeTone,
  exceptionStatusLabel,
  formatDurationSeconds,
  formatInteger,
  formatPercent,
} from "../adminConsoleUtils.js";
import {
  changeIcon,
  ChartDataTable,
  ChartTooltip,
  CHART_COLORS,
  cx,
  DatasetState,
  EmptyState,
  FAILURE_COLORS,
  FAILURE_PALETTE,
  FlowIcon,
  PanelHeader,
  Priority,
  StatusPill,
  TableScroller,
} from "./operationsShared.jsx";

function TaskFlow({ items, timings }) {
  if (!items.length) return <EmptyState title="没有任务状态数据" />;
  return (
    <div className="ops-flow-wrap">
      <div className="ops-flow-scroll-hint" aria-hidden="true"><CaretLeft size={13} />横向滑动查看完整任务链路<CaretRight size={13} /></div>
      <div className="ops-flow-scroll-region" role="region" aria-label="完整任务状态链路，可横向滚动" tabIndex="0">
        <div className="ops-flow" aria-label="任务状态流">
          {items.map((item, index) => (
            <div className="ops-flow-segment" key={item.key}>
              <div className="ops-flow-node">
                <span className="ops-flow-label"><FlowIcon flowKey={item.key} />{item.label}</span>
                <strong>{item.total == null ? "—" : formatInteger(item.total)}</strong>
                {item.delta != null ? (
                  <small className={cx(`is-${changeTone(item.delta)}`)}>
                    {item.delta > 0 ? "↑" : item.delta < 0 ? "↓" : ""} {Math.abs(item.delta).toFixed(1)}% vs 昨日
                  </small>
                ) : <small>{item.rate == null ? "数据不可用" : formatPercent(item.rate)}</small>}
              </div>
              {item.dropoffLabel ? (
                <div className="ops-flow-dropoff">
                  <span>{item.dropoffLabel}</span>
                  <strong>{item.dropoff == null ? "—" : formatInteger(item.dropoff)}</strong>
                  <small>{item.dropoffRate == null ? "(不可用)" : `(${formatPercent(item.dropoffRate)})`}</small>
                </div>
              ) : null}
              {index < items.length - 1 ? <ArrowRight className="ops-flow-arrow" size={22} aria-hidden="true" /> : null}
            </div>
          ))}
        </div>
        <div className="ops-timing-strip" aria-label="关键耗时">
          <strong>关键耗时 <span>(中位数 / P95)</span></strong>
          {timings.map((item) => (
            <span key={item.key}>
              <small>{item.label}</small>
              {formatDurationSeconds(item.p50)} <i>/</i> {formatDurationSeconds(item.p95)}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function TaskTrendChart({ data }) {
  if (!data.length) return <EmptyState title="没有任务趋势数据" />;
  return (
    <>
      <div className="ops-chart is-tall" role="img" aria-label="任务流转趋势折线图">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 12, right: 20, left: -6, bottom: 0 }}>
            <CartesianGrid stroke="var(--ops-chart-grid)" vertical={false} />
            <XAxis dataKey="time" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} interval="preserveStartEnd" minTickGap={24} />
            <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} tickFormatter={(value) => `${Math.round(value / 10000)}万`} />
            <Tooltip content={<ChartTooltip />} />
            <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, paddingTop: 4 }} />
            <Line type="monotone" dataKey="submitted" name="提交" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} activeDot={{ r: 3 }} />
            <Line type="monotone" dataKey="generating" name="生成中" stroke={CHART_COLORS.blue} strokeWidth={2} dot={false} activeDot={{ r: 3 }} />
            <Line type="monotone" dataKey="completed" name="回调完成" stroke={CHART_COLORS.cyan} strokeWidth={2} dot={false} activeDot={{ r: 3 }} />
            <Line type="monotone" dataKey="failed" name="失败 / 放弃" stroke={CHART_COLORS.red} strokeWidth={2} dot={false} activeDot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ChartDataTable
        caption="任务流转趋势数值"
        rows={data}
        columns={[
          { key: "time", label: "时间" },
          { key: "submitted", label: "提交", format: formatInteger },
          { key: "generating", label: "生成中", format: formatInteger },
          { key: "completed", label: "回调完成", format: formatInteger },
          { key: "failed", label: "失败 / 放弃", format: formatInteger },
        ]}
      />
    </>
  );
}

function FailureAnalysis({ data, reasons }) {
  if (!data.length) return <EmptyState title="没有失败原因数据" />;
  return (
    <>
      <div className="ops-failure-layout">
        <div className="ops-chart is-medium" role="img" aria-label="失败原因堆叠趋势图">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 12, right: 6, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="var(--ops-chart-grid)" vertical={false} />
              <XAxis dataKey="time" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} interval="preserveStartEnd" minTickGap={24} />
              <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} tickFormatter={(value) => `${Math.round(value / 1000)}千`} />
              <Tooltip content={<ChartTooltip />} />
              {reasons.map((reason, index) => {
                const color = FAILURE_COLORS[reason.key] || FAILURE_PALETTE[index % FAILURE_PALETTE.length];
                return <Area key={reason.key} type="monotone" dataKey={reason.key} name={reason.label} stackId="failure" stroke={color} strokeWidth={1.4} fill={color} fillOpacity={0.9} />;
              })}
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="ops-reason-list">
          <div className="ops-reason-head"><span>原因</span><span>占比</span><span>相邻周期</span></div>
          {reasons.map((item, index) => {
            const Icon = changeIcon(item.change);
            const color = FAILURE_COLORS[item.key] || FAILURE_PALETTE[index % FAILURE_PALETTE.length];
            return (
              <div className="ops-reason-row" key={item.key}>
                <span><i style={{ backgroundColor: color }} />{item.label}<small>{formatInteger(item.count)}</small></span>
                <strong>{formatPercent(item.share)}</strong>
                {item.change == null
                  ? <em className="is-flat">暂无对比</em>
                  : <em className={cx(`is-${changeTone(item.change)}`)}><Icon size={13} />{Math.abs(item.change).toFixed(1)}%</em>}
              </div>
            );
          })}
        </div>
      </div>
      <ChartDataTable
        caption="失败原因趋势数值"
        rows={data}
        columns={[
          { key: "time", label: "时间" },
          ...reasons.map((reason) => ({
            key: reason.key,
            label: reason.label,
            format: formatInteger,
          })),
        ]}
      />
    </>
  );
}

function LatencyChart({ data, timings = [] }) {
  if (!data.length) return <EmptyState title="没有耗时分布数据" />;
  const chartData = data.map((item) => ({
    ...item,
    bucket: item.range || `${item.seconds}s`,
  }));
  const terminalTiming = timings.find((item) => item.key === "terminal");
  return (
    <>
      <div className="ops-latency-layout">
        <div className="ops-chart is-medium" role="img" aria-label="生成耗时累积分布图">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 12, right: 10, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="var(--ops-chart-grid)" vertical={false} />
              <XAxis dataKey="bucket" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} interval="preserveStartEnd" minTickGap={20} />
              <YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: CHART_COLORS.muted }} tickFormatter={(value) => `${value}%`} />
              <Tooltip content={<ChartTooltip valueFormatter={(value) => formatPercent(value, 0)} />} />
              <Area type="monotone" dataKey="cumulative" name="累计占比" stroke={CHART_COLORS.primary} fill={CHART_COLORS.primary} fillOpacity={0.09} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        {terminalTiming ? (
          <div className="ops-latency-values">
            <span><small>P50 (中位数)</small><strong>{terminalTiming.p50 == null ? "暂无数据" : formatDurationSeconds(terminalTiming.p50)}</strong></span>
            <span><small>P95</small><strong>{terminalTiming.p95 == null ? "暂无数据" : formatDurationSeconds(terminalTiming.p95)}</strong></span>
          </div>
        ) : null}
      </div>
      <ChartDataTable
        caption="生成耗时分布数值"
        rows={chartData}
        columns={[
          { key: "bucket", label: "耗时区间" },
          { key: "cumulative", label: "累计占比", format: (value) => formatPercent(value, 0) },
        ]}
      />
    </>
  );
}

function ExceptionQueue({ items, onSelect, onShowAll }) {
  return (
    <section className="ops-panel ops-exception-queue">
      <PanelHeader
        title="异常处理队列"
        action={<button className="ops-text-button" type="button" onClick={onShowAll}>全部状态 <CaretDown size={13} /></button>}
      />
      {items.length ? (
        <>
          <span className="ops-exception-scroll-hint" aria-hidden="true"><CaretLeft size={13} />左右滑动查看待处理项<CaretRight size={13} /></span>
          <div className="ops-exception-list">
            {items.slice(0, 6).map((item) => (
              <button className="ops-exception-row" type="button" key={item.id} onClick={() => onSelect(item)}>
                <Priority value={item.priority} />
                <span className="ops-exception-copy"><strong>{item.title}</strong><small>{item.description}</small></span>
                <span className="ops-exception-meta"><strong>{item.duration}</strong><small>{item.owner || "未分派"}</small></span>
                <span className={cx("ops-exception-state", `is-${item.status}`)}>{exceptionStatusLabel(item.status)}</span>
              </button>
            ))}
          </div>
        </>
      ) : <EmptyState title="当前没有待处理异常" detail="平台未发现需要人工介入的异常。" />}
      <button className="ops-queue-footer" type="button" onClick={onShowAll}>
        查看全部异常 <ArrowRight size={15} />
      </button>
    </section>
  );
}

function ReliabilityTable({ rows, onAction }) {
  return (
    <section className="ops-panel ops-reliability-panel">
      <PanelHeader title="模型与渠道可靠性" detail="模型调用质量、故障切换与渠道成本完整性。" />
      <TableScroller hasActions label="模型与渠道可靠性">
        <table className="ops-table is-dense">
          <thead><tr><th>模型</th><th>渠道</th><th>渠道类型</th><th>调用量</th><th>成功率</th><th>P95 (生成耗时)</th><th>限流账号</th><th>全局故障切换</th><th>成本证据</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td><strong>{row.model}</strong></td>
                <td>{row.channel}</td>
                <td>{row.channelClass}</td>
                <td>{row.calls == null ? "—" : formatInteger(row.calls)}</td>
                <td className={row.successRate == null ? "" : row.successRate < 90 ? "is-negative" : row.successRate < 96 ? "is-warning" : "is-positive"}>{row.successRate == null ? "—" : formatPercent(row.successRate, 2)}</td>
                <td>{row.p95 == null ? "—" : formatDurationSeconds(row.p95)}</td>
                <td>{row.rateLimited == null ? "—" : `${formatInteger(row.rateLimited)} 个`}</td>
                <td>{row.failoverCount == null ? "—" : `${formatInteger(row.failoverCount)} 次（号池）`}</td>
                <td className={row.costDataStatus === "available" ? "is-positive" : "is-negative"}>{row.costDataStatus === "available" ? "已接入" : "—（未接入）"}</td>
                <td><StatusPill value={row.status} label={row.evidenceStatus === "available" ? ({ healthy: "正常", warning: "降级预警", critical: "异常" }[row.status]) : "数据未接入"} /></td>
                <td><div className="ops-table-actions"><button type="button" onClick={() => onAction?.("detail", row)} disabled={!onAction}>详情</button><button type="button" onClick={() => onAction?.("monitor", row)} disabled={!onAction}>监控</button></div></td>
              </tr>
            ))}
            {!rows.length ? <tr><td colSpan="11"><EmptyState title="没有可靠性数据" /></td></tr> : null}
          </tbody>
        </table>
      </TableScroller>
      {rows.length ? <div className="ops-table-footer"><span>共 {rows.length} 条</span><span>20 条 / 页</span></div> : null}
    </section>
  );
}

export function TaskOperationsScreen({ data, onExceptionSelect, onShowExceptionCenter, onReliabilityAction, onRetry }) {
  const taskStatus = data.sourceStatus?.taskOps || "unavailable";
  const exceptionStatus = data.sourceStatus?.exceptions || "unavailable";
  const reliabilityStatus = data.sourceStatus?.channelHealth || "unavailable";
  return (
    <>
      <div className="ops-task-grid">
        <div className="ops-task-main">
          {taskStatus === "available" ? (
            <>
              <section className="ops-panel ops-flow-panel">
                <div className="ops-alert-strip">
                  <span><Alarm size={15} />待处理 <strong>{exceptionStatus === "available" ? formatInteger(data.summary.pending) : "—"}</strong></span>
                  <span><WarningCircle size={15} />告警积压 <strong className="is-negative">{data.summary.alertBacklog == null ? "—" : formatInteger(data.summary.alertBacklog)}</strong></span>
                  <span><Receipt size={15} />未完成成本对账 <strong>{data.sourceStatus?.operating === "available" ? formatInteger(data.summary.unreconciledCosts) : "—"}</strong></span>
                </div>
                <TaskFlow items={data.taskFlow} timings={data.timings} />
              </section>
              <section className="ops-panel">
                <PanelHeader title="任务流转趋势" />
                <TaskTrendChart data={data.trends} />
              </section>
              <div className="ops-analysis-grid">
                <section className="ops-panel">
                  <PanelHeader title="失败原因与趋势" />
                  <FailureAnalysis data={data.failureTrends} reasons={data.failureReasons} />
                </section>
                <section className="ops-panel">
                  <PanelHeader title="耗时分布" detail="生成耗时" compact />
                  <LatencyChart data={data.latencyDistribution} timings={data.timings} />
                </section>
              </div>
            </>
          ) : (
            <DatasetState status={taskStatus} label="任务运营数据" detail={data.sourceErrors?.taskOps} onRetry={onRetry} />
          )}
        </div>
        {exceptionStatus === "available" ? (
          <ExceptionQueue items={data.exceptions} onSelect={onExceptionSelect} onShowAll={onShowExceptionCenter} />
        ) : (
          <section className="ops-panel ops-exception-queue"><DatasetState status={exceptionStatus} label="异常处理队列" detail={data.sourceErrors?.exceptions} onRetry={onRetry} /></section>
        )}
      </div>
      {reliabilityStatus === "available" ? (
        <ReliabilityTable rows={data.reliability} onAction={onReliabilityAction} />
      ) : (
        <section className="ops-panel ops-reliability-panel"><DatasetState status={reliabilityStatus} label="模型与渠道可靠性" detail={data.sourceErrors?.channelHealth} onRetry={onRetry} /></section>
      )}
    </>
  );
}
