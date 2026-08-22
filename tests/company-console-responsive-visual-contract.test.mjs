import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { managementSource } from "./management-source.mjs";

const companyCss = await readFile(
  new URL("../src/styles.css", import.meta.url),
  "utf8",
);
const lightThemeCss = await readFile(
  new URL("../src/light-theme.css", import.meta.url),
  "utf8",
);
test("company shell and modal drawer fit short viewports", () => {
  assert.match(
    companyCss,
    /\.control-shell\s*\{[^}]*height:\s*100dvh;[^}]*min-height:\s*0;/s,
  );
  assert.match(
    companyCss,
    /@media\s*\(max-width:\s*720px\)[\s\S]*?\.control-shell\s*\{[^}]*min-height:\s*0;/s,
  );
  assert.match(
    companyCss,
    /\.control-drawer-layer\s*\{[^}]*inset:\s*0;[^}]*overflow:\s*hidden;/s,
  );
  assert.match(
    companyCss,
    /\.control-drawer\s*\{[^}]*height:\s*100%;[^}]*max-height:\s*100dvh;/s,
  );
  assert.doesNotMatch(companyCss, /\.control-drawer-layer\s*\{[^}]*inset:\s*(?:52|54)px/s);
});

test("company mobile topbar preserves the explicit persona control", () => {
  const mobile = lightThemeCss.slice(lightThemeCss.indexOf("@media (max-width: 560px)"));

  assert.match(mobile, /\.control-shell \.control-brand,[\s\S]*?display:\s*none;/);
  assert.match(
    mobile,
    /\.control-shell \.surface-switch button\s*\{[^}]*min-width:\s*34px;/s,
  );
  assert.match(
    mobile,
    /\.control-shell \.skin-switcher\s*\{[^}]*flex:\s*0 0 54px;[^}]*width:\s*54px;/s,
  );
  assert.match(
    mobile,
    /\.control-shell \.demo-account-switcher\s*\{[^}]*flex:\s*1 1 112px;[^}]*min-width:\s*96px;[^}]*max-width:\s*none;/s,
  );
  assert.match(
    mobile,
    /\.control-shell \.demo-account-switcher select\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*text-overflow:\s*ellipsis;/s,
  );
  assert.match(
    mobile,
    /\.control-shell \.control-platform-view-switch\s*\{[^}]*width:\s*34px;[^}]*overflow:\s*hidden;/s,
  );
});

test("company mobile navigation and data tables expose hidden content", () => {
  const mobileCompany = companyCss.slice(companyCss.indexOf("@media (max-width: 720px)"));
  const mobileTheme = lightThemeCss.slice(lightThemeCss.indexOf("@media (max-width: 720px)"));

  assert.match(
    mobileCompany,
    /\.control-sidebar\s*\{[^}]*scroll-padding-inline:\s*12px;[^}]*scrollbar-width:\s*thin;/s,
  );
  assert.match(
    mobileCompany,
    /\.control-sidebar nav button\.is-active::before\s*\{[^}]*inset:\s*auto 10px 0;/s,
  );
  assert.match(
    mobileTheme,
    /\.control-shell \.control-table th\.is-actions,[\s\S]*?position:\s*sticky;[^}]*right:\s*0;[^}]*min-width:\s*92px;/s,
  );
  assert.match(
    mobileTheme,
    /\.control-shell \.control-table:has\([\s\S]*?\.model-capability-summary\.is-compact[\s\S]*?\)\s*\{[^}]*display:\s*block;[^}]*white-space:\s*normal;/s,
  );
  assert.match(
    mobileTheme,
    /> tbody > tr\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*repeat\(auto-fit, minmax\(92px, 1fr\)\)/s,
  );
  assert.match(
    mobileTheme,
    /> tbody > tr > td\s*\{[^}]*height:\s*auto !important;[^}]*padding:\s*0 !important;/s,
  );
  assert.match(
    mobileTheme,
    /\.model-capability-summary\.is-compact\s*\{[^}]*display:\s*flex;[^}]*max-height:\s*none;[^}]*overflow-x:\s*auto;[^}]*overflow-y:\s*hidden;/s,
  );
  assert.doesNotMatch(
    mobileCompany,
    /\.model-capability-summary\.is-compact\s*\{[^}]*max-height:/s,
    "an off-screen capability cell must not determine mobile table row height",
  );
  assert.match(
    mobileTheme,
    /th:nth-child\(5\):last-child[\s\S]*?td:nth-child\(5\)::before \{ content: "能力"; \}/,
  );
  assert.match(
    mobileTheme,
    /th:nth-child\(8\):last-child[\s\S]*?td:nth-child\(8\)::before \{ content: "操作"; \}/,
  );
  assert.equal(
    [...managementSource.matchAll(/<ModelCapabilitySummary model=\{model\} compact \/>/g)].length,
    2,
    "both company and platform model tables must use the responsive capability record",
  );
});

test("company tables controls and semantic states meet the readable light contract", () => {
  assert.match(
    companyCss,
    /\.control-table\s*\{[^}]*font-size:\s*var\(--text-body-sm, 13px\);/s,
  );
  assert.match(
    companyCss,
    /\.control-table th\s*\{[^}]*font-size:\s*var\(--text-body-sm, 13px\);/s,
  );
  assert.match(
    companyCss,
    /\.control-drawer-error > button\s*\{[^}]*min-width:\s*32px;[^}]*min-height:\s*32px;/s,
  );
  assert.match(
    companyCss,
    /\.control-entitlement-actions button,[\s\S]*?min-height:\s*32px;/s,
  );
  assert.match(
    lightThemeCss,
    /\.control-status\.is-succeeded,[\s\S]*?\.control-status\.is-settle,[\s\S]*?color:\s*var\(--success\);/s,
  );
  assert.match(
    lightThemeCss,
    /\.control-status\.is-processing,[\s\S]*?\.control-status\.is-reserve\s*\{[^}]*color:\s*var\(--warning\);/s,
  );
  assert.match(
    lightThemeCss,
    /\.control-status\.is-failed,[\s\S]*?\.control-status\.is-disabled,[\s\S]*?color:\s*var\(--danger\);/s,
  );
});
