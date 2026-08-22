import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../src/ManagementConsole.jsx", import.meta.url),
  "utf8",
);
const css = await readFile(
  new URL("../src/design-system/mobile-management.css", import.meta.url),
  "utf8",
);

test("company mobile chrome exposes one compact command bar and keeps every global control reachable", () => {
  assert.match(source, /className="control-mobile-commandbar"/);
  assert.match(source, /aria-label="返回制作工作区"/);
  assert.match(source, /className="control-mobile-heading"[\s\S]*?\{activeSectionLabel\}/);
  assert.match(source, /aria-label="打开工作区、皮肤与账号菜单"/);
  assert.match(source, /className="control-mobile-command-panel"[\s\S]*?<SkinSwitcher[\s\S]*?<DemoAccountSwitcher/);
  assert.match(source, /event\.target === summary && \(event\.key === "Enter" \|\| event\.key === " "\)[\s\S]*?event\.currentTarget\.open = !event\.currentTarget\.open/);
  assert.match(source, /event\.key === "Tab" && event\.currentTarget\.open && event\.target === summary && !event\.shiftKey[\s\S]*?firstControl\.focus\(\)/);
  assert.match(source, /event\.key !== "Escape"[\s\S]*?event\.currentTarget\.open = false[\s\S]*?summary\?\.focus\(\)/);
  assert.match(css, /> \.control-topbar > :not\(\.control-mobile-commandbar\)\s*\{[^}]*display:\s*none/s);
  assert.match(css, /\.control-mobile-commandbar\s*\{[^}]*grid-template-columns:\s*44px minmax\(0, 1fr\) 44px/s);
  assert.match(source, /const controlMainRef = useRef\(null\)/);
  assert.match(source, /main\.scrollTop = 0;[\s\S]*?main\.scrollLeft = 0;[\s\S]*?\}, \[mode, section\]\)/);
  assert.match(source, /<main ref=\{controlMainRef\} className="control-main"/);
});

test("company summary and sections form continuous mobile ledger surfaces", () => {
  assert.match(css, /\.control-summary-strip\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/s);
  assert.match(css, /\.control-section\s*\{[^}]*border:\s*0;[^}]*border-top:\s*1px solid[^}]*border-radius:\s*0/s);
  assert.match(css, /\.control-table-wrap\.is-mobile-records > \.control-table > tbody > tr\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)[^}]*border-bottom:\s*1px solid/s);
  assert.doesNotMatch(css, /(?:linear|radial|conic)-gradient\(|backdrop-filter/);
});

test("all five company modules preserve their mobile fields and actions", () => {
  for (const tableClass of [
    "is-recent-tasks-table",
    "is-members-table",
    "is-company-models-table",
    "is-report-tasks-table",
    "is-report-consumption-table",
    "is-download-audit-table",
    "is-recharges-table",
    "is-wallet-ledger-table",
  ]) {
    assert.match(source, new RegExp(`className="control-table ${tableClass}"`));
    assert.match(css, new RegExp(`\\.${tableClass} td:nth-child\\(`));
  }
  assert.match(css, /td\.is-actions > button\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px/s);
  assert.match(css, /\.model-capability-summary\.is-compact > summary\s*\{[^}]*min-height:\s*44px/s);
});

test("company report filters progressively disclose without changing form submission semantics", () => {
  assert.match(source, /<details className="control-filter-disclosure">[\s\S]*?<form\s+className="control-filterbar"/);
  assert.match(source, /event\.preventDefault\(\);[\s\S]*?setAppliedReportFilters\(applied\);[\s\S]*?load\("reports", applied\)/);
  assert.match(css, /\.control-filter-disclosure:not\(\[open\]\) > \.control-filterbar\s*\{[^}]*display:\s*none/s);
  assert.match(css, /\.control-filter-disclosure > \.control-filterbar\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/s);
});

test("company drawers are bounded keyboard-safe mobile bottom sheets", () => {
  assert.match(source, /role="dialog"[\s\S]*?aria-modal="true"/);
  assert.match(source, /if \(event\.key === "Escape"\)[\s\S]*?onClose\(\)/);
  assert.match(css, /\.control-drawer,[\s\S]*?\.control-drawer\.is-wide\s*\{[^}]*height:\s*auto;[^}]*min-height:\s*min\(440px, calc\(100dvh - 18px\)\);[^}]*max-height:\s*calc\(100dvh - 18px\)/s);
  assert.match(css, /\.control-drawer\.is-wide\s*\{[^}]*height:\s*min\(92dvh, 760px\)/s);
  assert.match(css, /\.control-drawer > header > button\s*\{[^}]*width:\s*44px;[^}]*min-height:\s*44px/s);
  assert.match(css, /\.control-form :is\(input, select, textarea\),[\s\S]*?min-height:\s*44px;[^}]*font-size:\s*13px/s);
});
