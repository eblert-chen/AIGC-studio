from __future__ import annotations


PERMISSION_CATALOG = {
    "assets.read": "查看公司输入素材",
    "assets.manage": "上传和停用公司输入素材",
    "users.read": "查看公司成员",
    "users.manage": "管理公司成员与角色",
    "models.read": "查看公司可用模型",
    "resources.read": "查看公司可用功能与资源",
    "billing.read": "查看公司余额与流水",
    "billing.manage": "调整或充值公司余额",
    "tasks.read": "查看本人的任务、作品和下载记录",
    "tasks.create": "创建生成任务",
    "reports.read": "查看全公司的任务、作品、消费和下载报表",
    "reports.export": "导出公司任务和消费报表",
    "publish.accounts.read": "查看公司的发布账号",
    "publish.accounts.manage": "连接、停用和管理公司的发布账号",
    "publish.jobs.read": "查看公司的发布任务",
    "publish.jobs.manage": "创建、审批、取消和重试公司的发布任务",
}

PERMISSION_CODES = frozenset(PERMISSION_CATALOG)

# Historical database rows may still contain these no-op codes. Keep the names
# reserved forever so an old role or personal override can never spring back to
# life if the catalog grows later.
RETIRED_PERMISSION_CODES = frozenset({"models.manage", "tasks.manage"})

if PERMISSION_CODES & RETIRED_PERMISSION_CODES:
    raise RuntimeError("退役权限码不能重新加入有效权限目录")
