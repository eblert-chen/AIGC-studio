import { hasCompanyConsolePermission } from "./companyConsolePermissions.js";

export function creationSurfacesForAvailableSurfaces(availableSurfaces = []) {
  if (!Array.isArray(availableSurfaces)) return [];
  const authorized = new Set(availableSurfaces);
  if (authorized.has("platform")) return [];
  if (authorized.has("company") || authorized.has("studio")) {
    return authorized.has("studio") ? ["studio"] : [];
  }
  return authorized.has("personal") ? ["personal"] : [];
}

export function preferredCreationSurfaceForManagement(
  availableSurfaces = [],
  managementMode = "platform",
) {
  const authorized = new Set(
    creationSurfacesForAvailableSurfaces(availableSurfaces),
  );
  const priority = managementMode === "company"
    ? ["studio", "personal"]
    : ["personal", "studio"];
  return priority.find((surface) => authorized.has(surface)) || "";
}

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
  if (!identity) return [];
  if (identity.is_platform_admin) return ["platform"];
  if (identity.company_id) {
    return hasCompanyConsoleAccess(identity)
      ? ["studio", "company"]
      : ["studio"];
  }
  if (identity.workspace_kind === "personal" || identity.is_personal) {
    return ["personal"];
  }
  return [];
}

export function defaultSurfaceForIdentity(identity) {
  const allowed = allowedSurfacesForIdentity(identity);
  if (allowed.includes("platform")) return "platform";
  if (allowed.includes("personal")) return "personal";
  if (allowed.includes("studio")) return "studio";
  return allowed[0] || "";
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
