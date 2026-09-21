import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = await readFile(
  new URL("../src/ManagementConsole.jsx", import.meta.url),
  "utf8",
);
const companySections = await readFile(
  new URL("../src/components/management/legacy/CompanyManagementSections.jsx", import.meta.url),
  "utf8",
);
const platformSections = await readFile(
  new URL("../src/components/management/legacy/PlatformBasicSections.jsx", import.meta.url),
  "utf8",
);
const stateHook = await readFile(
  new URL("../src/components/management/legacy/useLegacyManagementState.js", import.meta.url),
  "utf8",
);
const entitlementsPanel = await readFile(
  new URL("../src/components/management/CompanyEntitlementsPanel.jsx", import.meta.url),
  "utf8",
);
const capabilityEditor = await readFile(
  new URL("../src/components/management/CapabilityEditorFields.jsx", import.meta.url),
  "utf8",
);
const managementSupport = await readFile(
  new URL("../src/components/management/managementConsoleSupport.js", import.meta.url),
  "utf8",
);
const demoFixtures = await readFile(
  new URL("../src/demo/managementFixtures.js", import.meta.url),
  "utf8",
);

test("management configuration is split into state, company, and platform responsibilities", () => {
  assert.ok(
    root.split(/\r?\n/).length < 1900,
    "ManagementConsole should remain an orchestration shell, not absorb extracted domain components",
  );
  assert.match(root, /createCompanyManagementSectionRenderers/);
  assert.match(root, /createPlatformBasicSectionRenderers/);
  assert.match(root, /createCompanyManagementActions/);
  assert.match(root, /createPlatformBasicActions/);
  assert.match(root, /createManagementDrawerSubmitter/);
  assert.match(root, /useManagementConsoleState/);
  assert.match(root, /import \{ CapabilityEditorFields \}/);
  assert.match(root, /import \{ CompanyEntitlementsPanel \}/);
  assert.doesNotMatch(root, /function (?:CapabilityEditorFields|CompanyEntitlementsPanel)\(/);
  assert.match(companySections, /overview: renderCompanyOverview/);
  assert.match(companySections, /wallet: renderWallet/);
  assert.match(platformSections, /users: renderGlobalUsers/);
  assert.match(platformSections, /audit: renderAudit/);
  assert.match(entitlementsPanel, /export function CompanyEntitlementsPanel/);
  assert.match(entitlementsPanel, /role="tablist"/);
  assert.match(capabilityEditor, /export function CapabilityEditorFields/);
  assert.match(capabilityEditor, /role="alert"/);
  assert.match(managementSupport, /export function readCapabilityEditor/);
  assert.match(managementSupport, /return toCanonicalGenerationConfig\(modes\)/);

  const configurationBody = root.slice(
    root.indexOf("function ManagementConfigurationConsole"),
    root.indexOf("function PlatformManagementRouter"),
  );
  assert.doesNotMatch(configurationBody, /\buseState\(/);
  assert.doesNotMatch(root, /LegacyManagementConsole|view === "legacy"|setView\("legacy"\)/);
  assert.match(stateHook, /loadAbortControllerRef\.current\?\.abort\(\)/);
});

test("management demo fixtures are development-only and absent from the production root", () => {
  assert.doesNotMatch(root, /new\.member@example\.cn|CinemoX Pro 2\.1|DEMO_MEMBERS/);
  assert.match(root, /!import\.meta\.env\.PROD && props\.demoMode/);
  assert.match(root, /import\("\.\/demo\/managementFixtures\.js"\)/);
  assert.match(demoFixtures, /const DEVELOPMENT_DEMO_FIXTURES = !import\.meta\.env\.PROD/);
  assert.match(demoFixtures, /export function demoSnapshot/);
});
