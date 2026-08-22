export const ACTIVE_PERMISSION_CATALOG = Object.freeze([
  { code: "assets.read", description: "查看公司素材" },
  { code: "assets.manage", description: "上传与停用公司素材" },
  { code: "users.read", description: "查看成员" },
  { code: "users.manage", description: "管理成员与角色" },
  { code: "models.read", description: "查看模型" },
  { code: "resources.read", description: "查看功能资源" },
  { code: "billing.read", description: "查看余额流水" },
  { code: "billing.manage", description: "管理余额" },
  { code: "tasks.read", description: "查看生成任务" },
  { code: "tasks.create", description: "创建生成任务" },
  { code: "reports.read", description: "查看报表" },
  { code: "reports.export", description: "导出报表" },
  { code: "publish.accounts.read", description: "查看发布账号" },
  { code: "publish.accounts.manage", description: "管理发布账号" },
  { code: "publish.jobs.read", description: "查看发布任务" },
  { code: "publish.jobs.manage", description: "创建、审核与处理发布任务" },
]);

export const ACTIVE_PERMISSION_CODES = Object.freeze(
  ACTIVE_PERMISSION_CATALOG.map(({ code }) => code),
);

export function requirePermissionCatalog(permissions) {
  if (Array.isArray(permissions) && permissions.length > 0) return permissions;
  throw new Error("权限目录为空或尚未加载，已阻止修改；请刷新后重试。");
}
