import { useId } from "react";
import { ArrowRight } from "@phosphor-icons/react";

export function OperationsWorkspaceActions({
  canOpenPersonalCreation = false,
  onOpenPersonalCreation,
  pending = false,
  errorMessageId = "",
  compact = false,
}) {
  const descriptionId = useId();

  if (!canOpenPersonalCreation || !onOpenPersonalCreation) return null;

  return (
    <div className="ops-workspace-actions" role="group" aria-label="创作工作区" data-compact={compact ? "true" : undefined}>
      <button
        className="ops-creation-entry"
        type="button"
        onClick={onOpenPersonalCreation}
        disabled={pending}
        aria-busy={pending ? "true" : undefined}
        aria-label="进入我的个人创作"
        aria-describedby={[descriptionId, errorMessageId].filter(Boolean).join(" ")}
        title="使用当前账号进入我的个人创作"
      >
        <span>{pending ? "正在进入" : compact ? "创作" : "进入创作"}</span>
        {!pending && <ArrowRight size={14} aria-hidden="true" />}
      </button>
      <span id={descriptionId} className="visually-hidden">
        使用当前平台所有者身份进入独立的个人创作空间，不会退出登录或访问其他人的数据。
      </span>
    </div>
  );
}

export default OperationsWorkspaceActions;
