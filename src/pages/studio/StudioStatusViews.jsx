import { CloudCheck, WarningCircle } from "@phosphor-icons/react";

function unavailableCopy(capability, description = "") {
  const personal = description.includes("个人空间");
  if (personal) {
    if (capability === "素材") {
      return {
        summary: "个人空间暂不支持素材上传和引用。",
        title: "仍可直接使用文字创作",
        detail: "前往首页或创作页，输入制作说明即可开始生成。",
      };
    }
    if (capability === "发布") {
      return {
        summary: "个人空间暂不支持发布到外部平台。",
        title: "请切换到公司空间",
        detail: "进入已开通发布功能的公司空间后，可以连接账号并提交审核。",
      };
    }
    return {
      summary: `个人空间暂不支持${capability}。`,
      title: "当前功能尚未开放",
      detail: "你仍可使用当前空间中已经开放的创作功能。",
    };
  }

  const objectName = capability === "历史" ? "任务记录" : capability;
  return {
    summary: `当前账号不能查看${objectName}。`,
    title: "需要公司负责人开通访问",
    detail: `开通后即可在这里查看和处理${objectName}。`,
  };
}

export function WorkspaceCapabilityUnavailableView({ capability, description }) {
  const copy = unavailableCopy(capability, description);
  return (
    <section className="secondary-view capability-unavailable-view" aria-labelledby="capability-unavailable-title">
      <div className="secondary-heading">
        <div>
          <h1 id="capability-unavailable-title">{capability}</h1>
          <p>{copy.summary}</p>
        </div>
      </div>
      <div className="capability-unavailable-panel" role="note">
        <WarningCircle size={28} weight="duotone" aria-hidden="true" />
        <div>
          <strong>{copy.title}</strong>
          <p>{copy.detail}</p>
        </div>
      </div>
    </section>
  );
}

export function SettingsView({
  taskCompletionNotices,
  onTaskCompletionNoticesChange,
  workspaceKind = "company",
}) {
  return (
    <section className="secondary-view settings-view" aria-labelledby="settings-title">
      <div className="secondary-heading">
        <div>
          <h1 id="settings-title">设置</h1>
          <p>管理当前账号在此浏览器中的工作台偏好。</p>
        </div>
      </div>
      <div className="settings-list">
        <div className="setting-row is-policy">
          <span>
            <strong>作品保存保护</strong>
            <small>生成完成后，作品文件会先完成校验并保存，确保后续可以安全访问。</small>
          </span>
          <span className="setting-fixed-state" aria-label="作品保存保护始终开启">
            <CloudCheck size={18} weight="fill" aria-hidden="true" />
            系统保护
          </span>
        </div>
        <label className="setting-row">
          <span>
            <strong>任务结果站内提示</strong>
            <small>任务完成、失败、超时或需要人工确认时显示站内提示。</small>
          </span>
          <input
            type="checkbox"
            checked={taskCompletionNotices}
            onChange={(event) => onTaskCompletionNoticesChange(event.target.checked)}
          />
        </label>
      </div>
      <p className="settings-storage-note">
        提示偏好仅保存在当前浏览器，不会改变
        {workspaceKind === "personal" ? "个人空间功能、积分计费" : "公司访问范围、计费"}
        或作品保存规则。
      </p>
    </section>
  );
}
