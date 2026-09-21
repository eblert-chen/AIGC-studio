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
const PRODUCT_CONTEXT_BY_ACCOUNT_KIND = Object.freeze({
  personal: "personal",
  company: "company",
  platform: "platform",
  platform_admin: "platform",
});

function normalizedProductContext(value) {
  return PRODUCT_CONTEXT_BY_ACCOUNT_KIND[String(value || "").trim()] || "";
}

export function availableProductContexts(source) {
  if (!source || typeof source !== "object" || Array.isArray(source)) return [];
  const declared = Array.isArray(source.available_product_contexts)
    ? source.available_product_contexts
    : Array.isArray(source.available_account_kinds)
      ? source.available_account_kinds
      : [];
  const active = normalizedProductContext(
    source.active_product_context
      || source.account_type
      || (source.is_platform_admin ? "platform" : source.workspace_kind),
  );
  const contexts = new Set(declared.map(normalizedProductContext).filter(Boolean));
  if (active) contexts.add(active);
  return [...contexts];
}

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
  const declaredAccountType = ["personal", "company", "platform_admin", "unavailable"]
    .includes(String(source.account_type || "").trim())
    ? String(source.account_type).trim()
    : "";
  const inferredAccountType = platformAdmin
    ? "platform_admin"
    : companies.some(isCompanyMembershipContext)
      ? "company"
      : personal
        ? "personal"
        : "unavailable";
  const accountType = declaredAccountType || inferredAccountType;
  const productContexts = availableProductContexts({
    ...source,
    account_type: accountType,
  });

  return {
    user,
    personal: accountType === "personal" ? personal : null,
    companies: accountType === "company" ? companies : [],
    platform_admin: accountType === "platform_admin" ? platformAdmin : false,
    account_type: accountType,
    active_product_context: normalizedProductContext(
      source.active_product_context || accountType,
    ),
    available_product_contexts: productContexts,
  };
}

export function personalIdentityFromSession(session) {
  if (
    sessionAccountKind(session) !== "personal"
    || !session?.user?.id
    || !session?.personal?.workspace_id
  ) return null;
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
    available_surfaces: ["personal"],
  };
}

export function personalCapability(identity, key) {
  return identity?.workspace_kind === "personal"
    && identity?.personal_capabilities?.[key] === true;
}

export function isActiveCompanyContext(company) {
  return Boolean(company?.company_id)
    && String(company.status || "active").trim().toLowerCase() === "active";
}

export function isCompanyMembershipContext(company) {
  return Boolean(company?.company_id)
    && String(company.status || "active").trim().toLowerCase() !== "deleted";
}

export function sessionAccountKind(session) {
  const declaredAccountType = String(session?.account_type || "").trim();
  const companies = Array.isArray(session?.companies) ? session.companies : [];
  const hasCompanyMembership = companies.some(isCompanyMembershipContext);

  if (declaredAccountType === "platform_admin") {
    return session?.platform_admin && !hasCompanyMembership && !session?.personal
      ? "platform"
      : "unavailable";
  }
  if (declaredAccountType === "company") {
    return companies.some(isActiveCompanyContext) ? "company" : "company_unavailable";
  }
  if (declaredAccountType === "personal") {
    return session?.personal?.workspace_id
      ? "personal"
      : "unavailable";
  }
  if (declaredAccountType === "unavailable") return "unavailable";

  // Older deployments did not return account_type. Preserve strict precedence
  // without unioning the lower-priority workspaces into the chosen identity.
  if (session?.platform_admin) return "platform";
  if (hasCompanyMembership) {
    return companies.some(isActiveCompanyContext) ? "company" : "company_unavailable";
  }

  return session?.personal?.workspace_id ? "personal" : "unavailable";
}

export function preferredCompanyId(session, preferredId = "") {
  const companies = Array.isArray(session?.companies) ? session.companies : [];
  const preferred = String(preferredId || "").trim();
  if (preferred && companies.some((company) => (
    company.company_id === preferred && isActiveCompanyContext(company)
  ))) {
    return preferred;
  }
  return companies.find(isActiveCompanyContext)?.company_id || "";
}
