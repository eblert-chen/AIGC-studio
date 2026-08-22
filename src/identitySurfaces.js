import { hasCompanyConsolePermission } from "./companyConsolePermissions.js";

export function identityRoleLabel(identity) {
  if (identity?.is_platform_admin) return "平台管理员";
  if (identity?.workspace_kind === "personal" || identity?.is_personal) {
    return "个人用户";
  }
  return identity?.roles?.map((role) => role.name).filter(Boolean).join(" · ") || "公司成员";
}

export function hasCompanyConsoleAccess(identity) {
  if (!identity || identity.is_platform_admin || !identity.company_id) return false;
  if (identity.roles?.some((role) => role.system_key === "owner")) return true;
  return hasCompanyConsolePermission(identity);
}

export function allowedSurfacesForIdentity(identity) {
  if (Array.isArray(identity?.available_surfaces)) {
    const allowed = new Set(["personal", "studio", "company", "platform"]);
    return identity.available_surfaces.filter((surface, index, values) => (
      allowed.has(surface) && values.indexOf(surface) === index
    ));
  }
  if (identity?.is_platform_admin) return ["platform"];
  if (!identity) return [];
  if (identity.workspace_kind === "personal" || identity.is_personal) {
    return ["personal"];
  }
  return hasCompanyConsoleAccess(identity) ? ["studio", "company"] : ["studio"];
}

export function defaultSurfaceForIdentity(identity) {
  if (identity?.is_platform_admin && !Array.isArray(identity?.available_surfaces)) {
    return "platform";
  }
  const allowed = allowedSurfacesForIdentity(identity);
  if (identity?.workspace_kind === "personal" && allowed.includes("personal")) {
    return "personal";
  }
  if (identity?.is_platform_admin && allowed.includes("platform")) return "platform";
  if (allowed.includes("studio")) return "studio";
  return allowed[0] || "studio";
}

export function resolveSurfaceForIdentity(identity, requestedSurface) {
  const allowed = allowedSurfacesForIdentity(identity);
  return allowed.includes(requestedSurface)
    ? requestedSurface
    : defaultSurfaceForIdentity(identity);
}

export function canUseSurface(identity, surface) {
  return allowedSurfacesForIdentity(identity).includes(surface);
}
