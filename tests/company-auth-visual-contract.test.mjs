import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [managementCss, mobileCss, authCss, authShell] = await Promise.all([
  readFile(new URL("../src/design-system/management-routes.css", import.meta.url), "utf8"),
  readFile(new URL("../src/design-system/mobile-management.css", import.meta.url), "utf8"),
  readFile(new URL("../src/design-system/auth.css", import.meta.url), "utf8"),
  readFile(new URL("../src/auth/AuthShell.jsx", import.meta.url), "utf8"),
]);

test("loaded Company route CSS owns every entitlement drawer structure", () => {
  for (const selector of [
    ".control-entitlement-section > header",
    ".control-entitlement-resource-group > header",
    ".control-company-ledger-section > header",
    ".control-entitlement-list.is-models > article",
    ".control-entitlement-list.is-resources > article",
    ".control-chip-row > span",
    ".control-access-note.is-adjusted",
    ".capability-editor-intro",
    ".capability-limit-grid",
  ]) {
    assert.ok(managementCss.includes(selector), `${selector} must be owned by loaded Company CSS`);
  }
  assert.match(managementCss, /\.control-shell \.spin\s*\{[^}]*animation:\s*management-spin/);
  assert.match(managementCss, /@keyframes management-spin/);
});

test("tablet company switching remains reachable and mobile actions keep 44px targets", () => {
  const tablet = managementCss.slice(managementCss.indexOf("@media (max-width: 1120px)"));
  assert.match(tablet, /\.control-company-context-switcher\.is-desktop\s*\{[^}]*display:\s*flex/);
  assert.doesNotMatch(tablet.slice(0, tablet.indexOf("@media (max-width: 900px)")), /\.control-company-context-switcher\.is-desktop\s*\{[^}]*display:\s*none/);
  assert.match(mobileCss, /\.control-company-panel-footer button,[\s\S]*?\.control-collection-state > button\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px/);
  assert.match(mobileCss, /\.control-alert-close\s*\{[^}]*width:\s*44px;[^}]*min-height:\s*44px/);
});

test("full auth flows keep their evidence split while the login entry is single-column", () => {
  assert.ok(authShell.indexOf('<section className="auth-panel">') < authShell.indexOf('<aside className="auth-assurance"'));
  assert.match(authCss, /grid-template-areas:\s*"assurance panel"/);
  assert.match(authCss, /\.auth-assurance\s*\{[\s\S]*?grid-area:\s*assurance/);
  assert.match(authCss, /\.auth-panel\s*\{[\s\S]*?grid-area:\s*panel/);
  assert.match(authCss, /--auth-cobalt:\s*var\(--selection-violet\)/);
  assert.match(authCss, /--auth-orange:\s*var\(--workflow-orange\)/);
  assert.match(authShell, /variant === "login"/);
  assert.match(authShell, /!loginVariant \? \([\s\S]*?<aside className="auth-assurance"/);
  assert.match(authCss, /\.auth-shell\.is-login \.auth-workspace\s*\{[\s\S]*?grid-template-areas:\s*"panel"/);
  assert.match(authCss, /\.auth-shell\.is-login \.auth-panel\s*\{[\s\S]*?width:\s*min\(100%, 480px\)/);
  assert.match(authCss, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.auth-shell \.is-spinning\s*\{[^}]*animation:\s*none/);
});
