const PERSONAL_CAPABILITY_KEYS = Object.freeze([
  "generation",
  "models",
  "tasks",
  "artworks",
  "assets",
  "artifact_access",
  "publishing",
  "task_cancel",
]);

function normalizeCapabilities(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return Object.fromEntries(
    PERSONAL_CAPABILITY_KEYS.map((key) => [key, source[key] === true]),
  );
}

export function normalizeSessionSurfaces(payload) {
  const source = payload && typeof payload === "object" && !Array.isArray(payload)
    ? payload
    : {};
  const user = source.user && typeof source.user === "object" && !Array.isArray(source.user)
    ? {
        id: String(source.user.id || source.user.user_id || "").trim(),
        email: String(source.user.email || "").trim(),
        display_name: String(source.user.display_name || source.user.name || "").trim(),
      }
    : null;
  const personalSource = source.personal && typeof source.personal === "object"
    && !Array.isArray(source.personal) ? source.personal : null;
  const personal = personalSource && String(personalSource.workspace_id || "").trim()
    ? {
        kind: "personal",
        workspace_id: String(personalSource.workspace_id).trim(),
        label: String(personalSource.label || "个人空间").trim() || "个人空间",
        capabilities: normalizeCapabilities(personalSource.capabilities),
      }
    : null;
  const companies = Array.isArray(source.companies)
    ? source.companies.flatMap((company) => {
        const companyId = String(company?.company_id || "").trim();
        if (!companyId) return [];
        return [{
          kind: "company",
          company_id: companyId,
          name: String(company?.name || company?.company_name || companyId).trim(),
          status: String(company?.status || "active").trim(),
        }];
      })
    : [];
  const platformAdmin = source.platform_admin === true
    ? true
    : source.platform_admin && typeof source.platform_admin === "object"
      && !Array.isArray(source.platform_admin) ? source.platform_admin : false;

  return { user, personal, companies, platform_admin: platformAdmin };
}

export function personalIdentityFromSession(session, availableSurfaces = ["personal"]) {
  if (!session?.user?.id || !session?.personal?.workspace_id) return null;
  return {
    user_id: session.user.id,
    email: session.user.email,
    display_name: session.user.display_name || session.user.email || "个人用户",
    company_id: null,
    workspace_id: session.personal.workspace_id,
    workspace_kind: "personal",
    workspace_label: session.personal.label,
    personal_capabilities: session.personal.capabilities,
    permission_codes: [],
    roles: [],
    is_personal: true,
    is_platform_admin: false,
    available_surfaces: [...availableSurfaces],
  };
}

export function personalCapability(identity, key) {
  return identity?.workspace_kind === "personal"
    && identity?.personal_capabilities?.[key] === true;
}

export function preferredCompanyId(session, preferredId = "") {
  const companies = Array.isArray(session?.companies) ? session.companies : [];
  const preferred = String(preferredId || "").trim();
  if (preferred && companies.some((company) => company.company_id === preferred)) {
    return preferred;
  }
  return companies.find((company) => company.status !== "deleted")?.company_id || "";
}
