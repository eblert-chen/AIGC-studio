import { readFile } from "node:fs/promises";

const managementSourceUrls = [
  new URL("../src/ManagementConsole.jsx", import.meta.url),
  new URL("../src/components/management/ManagementPrimitives.jsx", import.meta.url),
  new URL("../src/components/management/MemberAccessFields.jsx", import.meta.url),
  new URL("../src/components/management/CapabilityEditorFields.jsx", import.meta.url),
  new URL("../src/components/management/CompanyEntitlementsPanel.jsx", import.meta.url),
  new URL("../src/components/management/managementAccess.js", import.meta.url),
  new URL("../src/components/management/managementConsoleSupport.js", import.meta.url),
  new URL("../src/components/management/PlatformCatalogViews.jsx", import.meta.url),
  new URL("../src/components/management/PlatformModelReleases.jsx", import.meta.url),
  new URL("../src/components/management/managementPresentation.js", import.meta.url),
  new URL("../src/components/management/legacy/CompanyManagementSections.jsx", import.meta.url),
  new URL("../src/components/management/legacy/PlatformBasicSections.jsx", import.meta.url),
  new URL("../src/components/management/legacy/createCompanyManagementActions.js", import.meta.url),
  new URL("../src/components/management/legacy/createPlatformBasicActions.js", import.meta.url),
  new URL("../src/components/management/legacy/createManagementDrawerSubmitter.js", import.meta.url),
  new URL("../src/components/management/legacy/managementSnapshot.js", import.meta.url),
  new URL("../src/components/management/legacy/useLegacyManagementState.js", import.meta.url),
];

export const managementSource = (await Promise.all(
  managementSourceUrls.map((url) => readFile(url, "utf8")),
)).join("\n");
