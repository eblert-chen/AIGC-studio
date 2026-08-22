export const COMPANY_CONSOLE_SECTION_PERMISSIONS = Object.freeze({
  overview: Object.freeze({ all: Object.freeze(["billing.read", "reports.read"]) }),
  members: Object.freeze({ any: Object.freeze(["users.read"]) }),
  models: Object.freeze({ any: Object.freeze(["models.read", "resources.read"]) }),
  reports: Object.freeze({ any: Object.freeze(["reports.read"]) }),
  wallet: Object.freeze({ any: Object.freeze(["billing.read"]) }),
});

export const COMPANY_CONSOLE_PERMISSION_CODES = Object.freeze([
  ...new Set(
    Object.values(COMPANY_CONSOLE_SECTION_PERMISSIONS)
      .flatMap(({ all = [], any = [] }) => [...all, ...any]),
  ),
]);

export function canOpenCompanyConsoleSection(identity, section) {
  const rule = COMPANY_CONSOLE_SECTION_PERMISSIONS[section];
  if (!rule) return false;
  const effective = new Set(identity?.permission_codes || []);
  const allAllowed = (rule.all || []).every((code) => effective.has(code));
  const anyAllowed = !rule.any?.length || rule.any.some((code) => effective.has(code));
  return allAllowed && anyAllowed;
}

export function hasCompanyConsolePermission(identity) {
  return Object.keys(COMPANY_CONSOLE_SECTION_PERMISSIONS)
    .some((section) => canOpenCompanyConsoleSection(identity, section));
}
