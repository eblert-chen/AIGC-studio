const COMPANY_STUDIO_PERMISSION_KEYS = Object.freeze([
  "models.read",
  "assets.read",
  "assets.manage",
  "tasks.read",
  "tasks.create",
]);

function permissionSet(permissionCodes) {
  return new Set(
    (Array.isArray(permissionCodes) ? permissionCodes : [])
      .filter((code) => typeof code === "string" && code.trim())
      .map((code) => code.trim()),
  );
}

export function resolveCompanyStudioAccess(permissionCodes) {
  const permissions = permissionSet(permissionCodes);
  const access = Object.fromEntries(
    COMPANY_STUDIO_PERMISSION_KEYS.map((code) => [code, permissions.has(code)]),
  );
  return {
    canReadModels: access["models.read"],
    canReadAssets: access["assets.read"],
    canManageAssets: access["assets.manage"],
    canReadTasks: access["tasks.read"],
    canCreateTasks: access["tasks.create"],
    // Company task/artifact endpoints share the same user-scoped tasks.read boundary.
    canReadArtworks: access["tasks.read"],
    canAccessArtifacts: access["tasks.read"],
  };
}

export function studioRouteAvailable(routeId, access) {
  if (routeId === "create") {
    return Boolean(access.canReadModels || access.canReadTasks || access.canCreateTasks);
  }
  if (routeId === "media") {
    return Boolean(access.canReadAssets || access.canManageAssets);
  }
  if (routeId === "artworks" || routeId === "history") {
    return Boolean(access.canReadTasks);
  }
  return true;
}
