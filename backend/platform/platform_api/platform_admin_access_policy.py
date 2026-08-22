from __future__ import annotations

from dataclasses import dataclass
import re

from .platform_admin_access_catalog import validate_platform_admin_permission_code


@dataclass(frozen=True)
class PlatformAdminRoutePolicy:
    method: str
    route_path: str
    permission_code: str


def _policy(method: str, route_path: str, permission_code: str):
    validate_platform_admin_permission_code(permission_code)
    return PlatformAdminRoutePolicy(
        method=method, route_path=route_path, permission_code=permission_code
    )


# This registry is deliberately explicit. Adding a platform-administrator route
# without adding a policy keeps delegated administrators out of that route. The
# product owner allowlist is evaluated before this registry and remains complete.
PLATFORM_ADMIN_ROUTE_POLICIES = (
    _policy("GET", "/api/v1/platform-admin/me", "platform.admin_access.read"),
    _policy(
        "POST",
        "/api/v1/platform-admin/download-gateway-registration-attempts/{attempt_id}/reconcile",
        "platform.asset_exceptions.manage",
    ),
    _policy("GET", "/api/v1/platform-admin/models", "platform.models.read"),
    _policy("POST", "/api/v1/platform-admin/models", "platform.models.manage"),
    _policy("GET", "/api/v1/platform-admin/models/{model_id}", "platform.models.read"),
    _policy(
        "PUT", "/api/v1/platform-admin/models/{model_id}", "platform.models.manage"
    ),
    _policy(
        "DELETE", "/api/v1/platform-admin/models/{model_id}", "platform.models.manage"
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/models/{model_id}/relay-capability",
        "platform.models.manage",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/models/{model_id}/publish",
        "platform.models.manage",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/models/{model_id}/disable",
        "platform.models.manage",
    ),
    _policy("GET", "/api/v1/platform-admin/relay-models", "platform.models.read"),
    _policy("GET", "/api/v1/platform-admin/companies", "platform.companies.read"),
    _policy("POST", "/api/v1/platform-admin/companies", "platform.companies.manage"),
    _policy(
        "PATCH",
        "/api/v1/platform-admin/companies/{company_id}/status",
        "platform.companies.manage",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/companies/{company_id}/entitlements",
        "platform.entitlements.read",
    ),
    _policy(
        "PUT",
        "/api/v1/platform-admin/companies/{company_id}/model-grants",
        "platform.entitlements.manage",
    ),
    _policy(
        "PUT",
        "/api/v1/platform-admin/companies/{company_id}/resources/{resource_id}",
        "platform.entitlements.manage",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/companies/{company_id}/publishing/jobs",
        "platform.publishing_exceptions.manage",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/companies/{company_id}/recharge",
        "platform.finance.manage",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/companies/{company_id}/recharges",
        "platform.finance.read",
    ),
    _policy("GET", "/api/v1/platform-admin/resources", "platform.resources.read"),
    _policy("POST", "/api/v1/platform-admin/resources", "platform.resources.manage"),
    _policy(
        "PUT",
        "/api/v1/platform-admin/resources/{resource_id}",
        "platform.resources.manage",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/reports/consumption",
        "platform.finance.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/reports/consumption/export.csv",
        "platform.finance.read",
    ),
    _policy(
        "GET", "/api/v1/platform-admin/channel-costs", "platform.provider_costs.read"
    ),
    _policy(
        "POST", "/api/v1/platform-admin/channel-costs", "platform.provider_costs.manage"
    ),
    _policy("GET", "/api/v1/platform-admin/dashboard", "platform.analytics.read"),
    _policy("GET", "/api/v1/platform-admin/audit-logs", "platform.audit.read"),
    _policy(
        "GET",
        "/api/v1/platform-admin/analytics/operating-series",
        "platform.analytics.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/analytics/task-operations",
        "platform.analytics.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/relay/channels",
        "platform.relay_health.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/relay/channels/{channel_id}",
        "platform.relay_health.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/relay/channels/{channel_id}/operations/{operation_id}",
        "platform.relay_health.read",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/relay/channels/{channel_id}/test",
        "platform.relay_health.manage",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/relay/channels/{channel_id}/status",
        "platform.relay_health.manage",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/relay/native-console/open",
        "platform.relay_health.manage",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/relay/submission-unknown",
        "platform.relay_health.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/relay/submission-unknown/{job_id}",
        "platform.relay_health.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/relay/submission-unknown/{job_id}/result",
        "platform.relay_health.read",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/relay/submission-unknown/{job_id}/resolve",
        "platform.relay_health.manage",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/relay/callback-dead-letters",
        "platform.relay_health.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/relay/callback-dead-letters/{event_id}",
        "platform.relay_health.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/relay/callback-dead-letters/{event_id}/result",
        "platform.relay_health.read",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/relay/callback-dead-letters/{event_id}/redrive",
        "platform.relay_health.manage",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/analytics/data-readiness",
        "platform.analytics.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/analytics/model-profitability",
        "platform.analytics.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/analytics/company-health",
        "platform.analytics.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/analytics/channel-health",
        "platform.relay_health.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/analytics/exceptions",
        "platform.publishing_exceptions.read",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/analytics/exceptions/companies/{company_id}/"
        "publication-jobs/{job_id}/reconcile",
        "platform.publishing_exceptions.manage",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/entitlements/matrix",
        "platform.entitlements.read",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/entitlements/coverage",
        "platform.entitlements.read",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/entitlements/batch/preview",
        "platform.entitlements.read",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/entitlements/copy/preview",
        "platform.entitlements.read",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/entitlements/templates/preview",
        "platform.entitlements.read",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/entitlements/batch/execute",
        "platform.entitlements.manage",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/entitlements/copy/execute",
        "platform.entitlements.manage",
    ),
    _policy(
        "POST",
        "/api/v1/platform-admin/entitlements/templates/execute",
        "platform.entitlements.manage",
    ),
    _policy(
        "GET",
        "/api/v1/platform-admin/access/permissions",
        "platform.admin_access.read",
    ),
    _policy("GET", "/api/v1/platform-admin/access/roles", "platform.admin_access.read"),
    _policy(
        "POST", "/api/v1/platform-admin/access/roles", "platform.admin_access.manage"
    ),
    _policy(
        "PUT",
        "/api/v1/platform-admin/access/roles/{role_id}",
        "platform.admin_access.manage",
    ),
    _policy("GET", "/api/v1/platform-admin/access/users", "platform.admin_access.read"),
    _policy(
        "GET",
        "/api/v1/platform-admin/access/users/{user_id}",
        "platform.admin_access.read",
    ),
    _policy(
        "PUT",
        "/api/v1/platform-admin/access/users/{user_id}",
        "platform.admin_access.manage",
    ),
    _policy(
        "PUT",
        "/api/v1/platform-admin/access/users/{user_id}/status",
        "platform.admin_access.manage",
    ),
)


_DIRECT_POLICIES = {
    (policy.method, policy.route_path): policy.permission_code
    for policy in PLATFORM_ADMIN_ROUTE_POLICIES
}
if len(_DIRECT_POLICIES) != len(PLATFORM_ADMIN_ROUTE_POLICIES):
    raise RuntimeError("Duplicate platform administrator route policy")


def _template_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^{}]+\})", template)
    expression = "".join(
        r"[^/]+" if part.startswith("{") else re.escape(part) for part in parts
    )
    return re.compile(expression + r"\Z")


_MATCHABLE_POLICIES = tuple(
    (policy.method, _template_regex(policy.route_path), policy.permission_code)
    for policy in PLATFORM_ADMIN_ROUTE_POLICIES
    if "{" in policy.route_path
)


# A route may expose multiple independently sensitive domains. The primary code
# above keeps route discovery simple; these additional codes are conjunctive.
# The centralized authorizer requires every code, preventing an exception or
# profitability aggregator from weakening the underlying domain boundaries.
_ADDITIONAL_REQUIRED_PERMISSIONS: dict[tuple[str, str], tuple[str, ...]] = {
    (
        "GET",
        "/api/v1/platform-admin/analytics/operating-series",
    ): ("platform.finance.read", "platform.provider_costs.read"),
    (
        "GET",
        "/api/v1/platform-admin/analytics/model-profitability",
    ): ("platform.finance.read", "platform.provider_costs.read"),
    (
        "GET",
        "/api/v1/platform-admin/analytics/company-health",
    ): ("platform.finance.read",),
    (
        "GET",
        "/api/v1/platform-admin/analytics/exceptions",
    ): ("platform.asset_exceptions.read", "platform.relay_health.read"),
}
for extra_codes in _ADDITIONAL_REQUIRED_PERMISSIONS.values():
    for extra_code in extra_codes:
        validate_platform_admin_permission_code(extra_code)


def resolve_platform_admin_route_permission(
    *, method: str, route_path: str
) -> str | None:
    """Return the required permission, or ``None`` to fail closed.

    ``route_path`` should normally be Starlette's resolved route template
    (``request.scope['route'].path``). Matching concrete URL paths is supported
    as a defensive fallback for middleware and focused tests.
    """

    normalized_method = method.upper()
    if normalized_method == "HEAD":
        normalized_method = "GET"
    direct = _DIRECT_POLICIES.get((normalized_method, route_path))
    if direct is not None:
        return direct
    for candidate_method, pattern, permission_code in _MATCHABLE_POLICIES:
        if candidate_method == normalized_method and pattern.fullmatch(route_path):
            return permission_code
    return None


def resolve_platform_admin_route_permissions(
    *, method: str, route_path: str
) -> tuple[str, ...] | None:
    primary = resolve_platform_admin_route_permission(
        method=method, route_path=route_path
    )
    if primary is None:
        return None
    normalized_method = "GET" if method.upper() == "HEAD" else method.upper()
    # Resolved route templates are the normal input. For the concrete-path
    # fallback, identify the matching template before looking up extra guards.
    template = route_path
    if (normalized_method, template) not in _DIRECT_POLICIES:
        for policy in PLATFORM_ADMIN_ROUTE_POLICIES:
            if policy.method == normalized_method and _template_regex(
                policy.route_path
            ).fullmatch(route_path):
                template = policy.route_path
                break
    return (
        primary,
        *_ADDITIONAL_REQUIRED_PERMISSIONS.get((normalized_method, template), ()),
    )
