import { useState } from "react";
import { ArrowClockwise, WarningCircle } from "@phosphor-icons/react";
import { permissionOverrideMap, roleList } from "./managementAccess.js";

const PERMISSION_GROUP_LABELS = {
  assets: "素材",
  users: "成员与权限",
  models: "模型",
  resources: "功能资源",
  billing: "余额与计费",
  tasks: "生成任务",
  reports: "报表",
  publish: "自动发布",
};

export function MemberAccessFields({ roles, permissions, member }) {
  const assignedIds = new Set(roleList(member).map((role) => role.id));
  const primaryRoles = roles.filter((role) => ["operator", "team_lead"].includes(role.system_key));
  const assignedPrimary = primaryRoles.filter((role) => assignedIds.has(role.id));
  const defaultPrimary = assignedPrimary[0] || primaryRoles.find((role) => role.system_key === "operator");
  const customRoles = roles.filter((role) => !role.is_system);
  const [primaryRoleId, setPrimaryRoleId] = useState(defaultPrimary?.id || "");
  const [customRoleIds, setCustomRoleIds] = useState(
    () => new Set(customRoles.filter((role) => assignedIds.has(role.id)).map((role) => role.id)),
  );
  const [overrides, setOverrides] = useState(() => permissionOverrideMap(member));
  const catalog = permissions;
  const selectedRoleIds = new Set([primaryRoleId, ...customRoleIds]);
  const inherited = new Set();
  roles.filter((role) => selectedRoleIds.has(role.id)).forEach((role) => {
    (role.permission_codes || []).forEach((code) => inherited.add(code));
  });
  const effective = new Set(inherited);
  Object.entries(overrides).forEach(([code, effect]) => {
    if (effect === "deny") effective.delete(code);
    else if (effect === "allow") effective.add(code);
  });
  const groupedPermissions = catalog.reduce((groups, permission) => {
    const prefix = permission.code.split(".")[0];
    const label = PERMISSION_GROUP_LABELS[prefix] || "其他";
    if (!groups[label]) groups[label] = [];
    groups[label].push(permission);
    return groups;
  }, {});
  const allowCount = Object.values(overrides).filter((effect) => effect === "allow").length;
  const denyCount = Object.values(overrides).filter((effect) => effect === "deny").length;

  const toggleCustomRole = (roleId, checked) => {
    setCustomRoleIds((current) => {
      const next = new Set(current);
      if (checked) next.add(roleId);
      else next.delete(roleId);
      return next;
    });
  };

  const setPermissionEffect = (code, effect) => {
    setOverrides((current) => {
      const next = { ...current };
      if (effect === "inherit") delete next[code];
      else next[code] = effect;
      return next;
    });
  };

  return (
    <>
      {assignedPrimary.length !== 1 && (
        <div className="control-form-warning">
          <WarningCircle size={18} />
          <span>历史角色配置异常；本次提交会修复为唯一的运营或组长级别。</span>
        </div>
      )}
      <fieldset>
        <legend>公司级别（必选）</legend>
        {primaryRoles.map((role) => (
          <label className="control-check" key={role.id}>
            <input
              name="primaryRoleId"
              type="radio"
              value={role.id}
              required
              checked={primaryRoleId === role.id}
              onChange={() => setPrimaryRoleId(role.id)}
            />
            <span><strong>{role.name}</strong><small>{role.description}</small></span>
          </label>
        ))}
      </fieldset>
      {!!customRoles.length && (
        <fieldset>
          <legend>附加权限角色（可选）</legend>
          {customRoles.map((role) => (
            <label className="control-check" key={role.id}>
              <input
                name="customRoleIds"
                type="checkbox"
                value={role.id}
                checked={customRoleIds.has(role.id)}
                onChange={(event) => toggleCustomRole(role.id, event.target.checked)}
              />
              <span><strong>{role.name}</strong><small>{role.description}</small></span>
            </label>
          ))}
        </fieldset>
      )}
      <div className="control-permission-summary" aria-label="权限汇总" aria-live="polite" aria-atomic="true">
        <span><strong>{inherited.size}</strong><small>模板开启</small></span>
        <span><strong>{allowCount}</strong><small>个人允许</small></span>
        <span><strong>{denyCount}</strong><small>个人禁止</small></span>
        <span><strong>{effective.size}</strong><small>最终生效</small></span>
        <button type="button" onClick={() => setOverrides({})} disabled={!allowCount && !denyCount}>
          <ArrowClockwise size={14} /> 全部跟随模板
        </button>
      </div>
      <div className="control-permission-groups">
        {Object.entries(groupedPermissions).map(([group, items]) => (
          <fieldset key={group}>
            <legend>{group}</legend>
            {items.map((permission) => {
              const effect = overrides[permission.code] || "inherit";
              const isEffective = effect === "allow" || (effect === "inherit" && inherited.has(permission.code));
              const statusId = `permission-status-${permission.code.replace(/[^a-z0-9_-]/gi, "-")}`;
              return (
                <label className="control-permission-row" key={permission.code}>
                  <span className="control-permission-copy">
                    <strong>{permission.description}</strong>
                    <small>{permission.code}</small>
                  </span>
                  <span id={`${statusId}-template`} className={inherited.has(permission.code) ? "is-template-on" : "is-template-off"}>
                    {inherited.has(permission.code) ? "模板开启" : "模板关闭"}
                  </span>
                  <select
                    name={`permission:${permission.code}`}
                    value={effect}
                    onChange={(event) => setPermissionEffect(permission.code, event.target.value)}
                    aria-label={`${permission.description}的个人权限`}
                    aria-describedby={`${statusId}-template ${statusId}-effective`}
                  >
                    <option value="inherit">跟随模板</option>
                    <option value="allow">个人允许</option>
                    <option value="deny">个人禁止</option>
                  </select>
                  <span id={`${statusId}-effective`} className={isEffective ? "is-effective-on" : "is-effective-off"}>
                    {isEffective ? "最终开启" : "最终关闭"}
                  </span>
                </label>
              );
            })}
          </fieldset>
        ))}
      </div>
    </>
  );
}
