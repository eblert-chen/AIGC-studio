import {
  ArrowClockwise,
  Plus,
  ShieldCheck,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import { capabilitySummary } from "../../modelCapabilities.js";

export const STATUS_LABELS = {
  active: "正常",
  suspended: "已停用",
  deactivated: "已停用并注销",
  pending: "待接受",
  accepted: "已接受",
  expired: "已过期",
  revoked: "已撤销",
  disabled: "已下线",
  draft: "草稿",
  published: "已发布",
  succeeded: "成功",
  processing: "处理中",
  queued: "排队中",
  failed: "失败",
  cancelled: "已取消",
  recharge: "充值",
  reserve: "预留",
  settle: "结算",
  release: "释放",
};

export function ModelCapabilitySummary({ model, compact = false }) {
  const rows = capabilitySummary(model);
  if (!rows.length) {
    return <span className="model-capability-empty">尚未声明能力</span>;
  }
  if (compact) {
    return (
      <details className="model-capability-summary is-compact">
        <summary>
          <span>{rows.length} 个模式</span>
          <small>查看能力</small>
        </summary>
        <div className="model-capability-details">
          {rows.map((row) => (
            <div key={row.id}>
              <strong>{row.label}</strong>
              <span>{row.detail}</span>
            </div>
          ))}
        </div>
      </details>
    );
  }
  return (
    <div className="model-capability-summary">
      {rows.map((row) => (
        <div key={row.id}>
          <strong>{row.label}</strong>
          <span>{row.detail}</span>
        </div>
      ))}
    </div>
  );
}

export function StatusPill({ value, label }) {
  return (
    <span className={`control-status is-${value || "unknown"}`}>
      <span aria-hidden="true" />
      {label || STATUS_LABELS[value] || value || "未知"}
    </span>
  );
}

export function PageHeader({ eyebrow, title, detail, children }) {
  return (
    <header className="control-page-header">
      <div>
        <span>{eyebrow}</span>
        <h1>{title}</h1>
        <p>{detail}</p>
      </div>
      {children && <div className="control-page-actions">{children}</div>}
    </header>
  );
}

export function ManagementAccessState({ mode, pending = false }) {
  const title = pending ? "正在核验管理权限" : "没有可访问的管理模块";
  const detail = pending
    ? "系统正在读取服务端身份与权限目录，完成前不会显示任何管理数据。"
    : mode === "platform"
      ? "当前平台管理员未获得基础配置模块权限。请联系平台所有者调整权限。"
      : "当前公司成员未获得管理台模块权限。请联系公司老板调整个人权限或角色模板。";
  return (
    <section className="control-access-state" aria-live="polite" aria-labelledby="management-access-title">
      <span className="control-access-mark" aria-hidden="true"><ShieldCheck size={22} /></span>
      <div>
        <span>{pending ? "权限核验" : "访问受限"}</span>
        <h1 id="management-access-title">{title}</h1>
        <p>{detail}</p>
      </div>
    </section>
  );
}

export function EmptyRows({ colSpan, message = "暂无数据" }) {
  return (
    <tr>
      <td className="control-empty" colSpan={colSpan}>
        {message}
      </td>
    </tr>
  );
}

export function CollectionState({ state = "empty", title, detail, onRetry }) {
  const isLoading = state === "loading";
  return (
    <div className={`control-collection-state is-${state}`} role={state === "error" ? "alert" : "status"} aria-live="polite">
      {isLoading ? <SpinnerGap size={18} className="spin" aria-hidden="true" /> : <WarningCircle size={18} aria-hidden="true" />}
      <div>
        <strong>{title}</strong>
        {detail && <small>{detail}</small>}
      </div>
      {state === "error" && onRetry && <button type="button" onClick={onRetry}><ArrowClockwise size={15} />重试</button>}
    </div>
  );
}

export function PermissionNotice({ title, detail }) {
  return (
    <div className="control-permission-notice" role="note">
      <ShieldCheck size={18} aria-hidden="true" />
      <div><strong>{title}</strong><small>{detail}</small></div>
    </div>
  );
}

export function PageLoadMore({ loaded, total, busy = false, onLoadMore, noun = "条记录" }) {
  const safeLoaded = Number(loaded) || 0;
  const safeTotal = Number(total) || 0;
  if (!safeTotal) return null;
  const hasMore = safeLoaded < safeTotal;
  return (
    <footer className="control-pagination" aria-live="polite">
      <span>已显示 {Math.min(safeLoaded, safeTotal)} / {safeTotal} {noun}</span>
      {hasMore && (
        <button type="button" onClick={onLoadMore} disabled={busy}>
          {busy ? <SpinnerGap size={15} className="spin" /> : <Plus size={15} />}
          {busy ? "正在加载" : "加载更多"}
        </button>
      )}
    </footer>
  );
}

export function AuditChangeSummary({ before, after }) {
  const beforeText = JSON.stringify(before || {}, null, 2);
  const afterText = JSON.stringify(after || {}, null, 2);
  const hasChange = beforeText !== "{}" || afterText !== "{}";
  if (!hasChange) return <span className="control-audit-no-change">无字段摘要</span>;
  return (
    <details className="control-audit-change">
      <summary>查看变更</summary>
      <div>
        <section><strong>变更前</strong><pre>{beforeText}</pre></section>
        <section><strong>变更后</strong><pre>{afterText}</pre></section>
      </div>
    </details>
  );
}

export function PrimaryButton({ children, ...props }) {
  return (
    <button className="control-button is-primary" type="button" {...props}>
      {children}
    </button>
  );
}

export function QuietButton({ children, ...props }) {
  return (
    <button className="control-button" type="button" {...props}>
      {children}
    </button>
  );
}

export function SummaryStrip({ items }) {
  return (
    <dl className="control-summary-strip">
      {items.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
          {item.note && <small>{item.note}</small>}
        </div>
      ))}
    </dl>
  );
}
