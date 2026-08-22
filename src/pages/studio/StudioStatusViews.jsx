import { CloudCheck, WarningCircle } from "@phosphor-icons/react";

export function WorkspaceCapabilityUnavailableView({ capability, description }) {
  return (
    <section className="secondary-view capability-unavailable-view" aria-labelledby="capability-unavailable-title">
      <div className="secondary-heading">
        <div>
          <span className="view-kicker">个人空间能力边界</span>
          <h1 id="capability-unavailable-title">{capability}</h1>
          <p>{description}</p>
        </div>
      </div>
      <div className="capability-unavailable-panel" role="note">
        <WarningCircle size={28} weight="duotone" aria-hidden="true" />
        <div>
          <strong>当前个人能力合同未开放此功能</strong>
          <p>页面不会伪造数据、公司身份或可操作按钮，也不会向企业接口发送个人请求。</p>
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
    <section className="secondary-view settings-view">
      <div className="secondary-heading">
        <div>
          <span className="view-kicker">工作台偏好</span>
          <h1>设置</h1>
          <p>查看平台安全规则，并管理当前账号在此浏览器中的工作台偏好。</p>
        </div>
      </div>
      <div className="settings-list">
        <div className="setting-row is-policy">
          <span>
            <strong>安全归档</strong>
            <small>生成完成后，产物必须经过校验并转存到平台控制的私有存储。该安全规则不能关闭。</small>
          </span>
          <span className="setting-fixed-state" aria-label="安全归档始终开启">
            <CloudCheck size={18} weight="fill" aria-hidden="true" />
            始终开启
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
        提示偏好按账号保存在当前浏览器中，不会改变
        {workspaceKind === "personal" ? "个人空间能力、积分计费" : "企业权限、计费"}
        或产物归档策略。
      </p>
    </section>
  );
}
