import { CaretLeft, CaretRight } from "@phosphor-icons/react";
import { downloadState } from "../../taskArtifacts.js";

export function ScopeControl({ value, onChange, canViewCompany }) {
  return (
    <div className="scope-control" aria-label="记录范围">
      <button
        className={value === "mine" ? "is-active" : ""}
        type="button"
        onClick={() => onChange("mine")}
        aria-pressed={value === "mine"}
      >
        我的
      </button>
      {canViewCompany && (
        <button
          className={value === "company" ? "is-active" : ""}
          type="button"
          onClick={() => onChange("company")}
          aria-pressed={value === "company"}
        >
          全公司
        </button>
      )}
    </div>
  );
}

export function PageControls({ page, pageSize, total, onChange }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;
  return (
    <nav className="page-controls" aria-label="分页">
      <button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        <CaretLeft size={16} aria-hidden="true" />上一页
      </button>
      <span>第 {page} / {pageCount} 页，共 {total} 条</span>
      <button type="button" disabled={page >= pageCount} onClick={() => onChange(page + 1)}>
        下一页<CaretRight size={16} aria-hidden="true" />
      </button>
    </nav>
  );
}

export function DownloadBadge({ source, issuedLocally = false }) {
  const state = downloadState(source, { issuedLocally });
  return (
    <span className={`download-state is-${state.tone}`} title={state.detail}>
      {state.label}
    </span>
  );
}

export function LoadingRows({ label }) {
  return (
    <div className="history-skeleton" role="status" aria-label={label}>
      {Array.from({ length: 3 }, (_, index) => (
        <span key={index}><i /><i /><i /></span>
      ))}
    </div>
  );
}
