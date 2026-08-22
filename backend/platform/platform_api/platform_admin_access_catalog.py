from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformAdminPermissionSpec:
    code: str
    domain: str
    action: str
    description: str


_DOMAIN_DESCRIPTIONS = {
    "analytics": "经营、任务、模型盈利和企业健康分析",
    "companies": "企业资料、状态和企业生命周期",
    "entitlements": "企业模型、功能、智能体、外部 API 和自动发布权益",
    "models": "模型目录、能力声明、定价模式和 Relay 映射审批",
    "resources": "功能、智能体和外部 API 资源目录",
    "finance": "充值、结算收入、企业余额和账务异常",
    "provider_costs": "渠道成本、成本缺失和毛利对账",
    "publishing_exceptions": "发布失败、未知提交和 OAuth 异常",
    "asset_exceptions": "产物转存和下载登记异常",
    "audit": "平台操作审计、筛选和导出线索",
    "relay_health": "Relay 渠道、账号池、限流、切换和告警摘要",
    "admin_access": "平台管理员角色和权限分配",
}


def _build_catalog() -> tuple[PlatformAdminPermissionSpec, ...]:
    permissions: list[PlatformAdminPermissionSpec] = []
    for domain, subject in _DOMAIN_DESCRIPTIONS.items():
        permissions.extend(
            (
                PlatformAdminPermissionSpec(
                    code=f"platform.{domain}.read",
                    domain=domain,
                    action="read",
                    description=f"查看{subject}",
                ),
                PlatformAdminPermissionSpec(
                    code=f"platform.{domain}.manage",
                    domain=domain,
                    action="manage",
                    description=f"管理{subject}",
                ),
            )
        )
    return tuple(permissions)


PLATFORM_ADMIN_PERMISSION_CATALOG = _build_catalog()
PLATFORM_ADMIN_PERMISSION_BY_CODE = {
    permission.code: permission for permission in PLATFORM_ADMIN_PERMISSION_CATALOG
}
PLATFORM_ADMIN_PERMISSION_CODES = frozenset(PLATFORM_ADMIN_PERMISSION_BY_CODE)


def validate_platform_admin_permission_code(code: str) -> str:
    if code not in PLATFORM_ADMIN_PERMISSION_CODES:
        raise ValueError(f"Unknown platform administrator permission: {code}")
    return code

