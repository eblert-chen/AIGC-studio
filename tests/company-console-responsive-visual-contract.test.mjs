import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { managementSource } from "./management-source.mjs";

const managementCss = await readFile(
  new URL("../src/design-system/management-routes.css", import.meta.url),
  "utf8",
);
const mobileManagementCss = await readFile(
  new URL("../src/design-system/mobile-management.css", import.meta.url),
  "utf8",
);
test("company shell and modal drawer fit short viewports", () => {
  assert.match(
    managementCss,
    /\.control-shell\s*\{[^}]*height:\s*100dvh;[^}]*min-height:\s*0;/s,
  );
  assert.match(
    mobileManagementCss,
    /@media\s*\(max-width:\s*720px\)[\s\S]*?\.control-shell\s*\{[^}]*min-height:\s*0;/s,
  );
  assert.match(
    managementCss,
    /\.control-shell \.control-drawer-layer\s*\{[^}]*inset:\s*0;[^}]*overflow:\s*hidden;/s,
  );
  assert.match(
    managementCss,
    /\.control-shell \.control-drawer\s*\{[^}]*height:\s*100%;[^}]*max-height:\s*100dvh;/s,
  );
  assert.doesNotMatch(managementCss, /\.control-drawer-layer\s*\{[^}]*inset:\s*(?:52|54)px/s);
});

test("company mobile topbar preserves the explicit persona control", () => {
  assert.match(
    mobileManagementCss,
    /\.control-shell > \.control-topbar > :not\(\.control-mobile-commandbar\)\s*\{\s*display:\s*none;/,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-mobile-commandbar\s*\{[^}]*grid-template-columns:\s*44px minmax\(0, 1fr\) 44px;/s,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-mobile-command-panel :is\(\.skin-switcher, \.demo-account-switcher\)\s*\{[^}]*min-height:\s*44px;/s,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-mobile-command-panel \.skin-switcher-trigger\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*min-height:\s*44px;[^}]*grid-template-columns:\s*auto minmax\(0, 1fr\) auto;/s,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-mobile-command-panel :is\(\.demo-account-switcher, \.control-company-context-switcher\) select\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*min-height:\s*44px;[^}]*text-overflow:\s*ellipsis;/s,
  );
  assert.match(
    managementSource,
    /<summary aria-label="打开工作区、皮肤与账号菜单">[\s\S]*?<SkinSwitcher value=\{activeSkin\}[\s\S]*?setMobileDemoPersonaHost/,
  );
  assert.equal((managementSource.match(/<DemoAccountSwitcher\b/g) || []).length, 1);
  assert.match(managementSource, /createPortal\([\s\S]*?<DemoAccountSwitcher value=\{demoPersonaId\}[\s\S]*?activeDemoPersonaHost/);
  assert.match(
    managementSource,
    /event\.key !== "Escape"[\s\S]*?event\.currentTarget\.open = false;[\s\S]*?summary\?\.focus\(\)/,
  );
});

test("company mobile navigation and data tables expose hidden content", () => {
  assert.match(
    mobileManagementCss,
    /\.control-shell > \.control-sidebar\s*\{[^}]*overflow-x:\s*auto;[^}]*scroll-padding-inline:\s*12px;[^}]*scrollbar-width:\s*none;/s,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-sidebar nav\s*\{[^}]*width:\s*max-content;[^}]*min-width:\s*100%;/s,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-sidebar nav button\.is-active\s*\{[^}]*color:\s*var\(--management-cobalt-strong\);[^}]*background:\s*var\(--management-cobalt-soft\);/s,
  );
  assert.doesNotMatch(
    mobileManagementCss,
    /\.control-shell \.control-sidebar nav button\.is-active::before/,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-table-wrap\.is-mobile-records > \.control-table > tbody > tr\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/s,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-table-wrap\.is-mobile-records\s*\{[^}]*overflow:\s*visible;[\s\S]*?> \.control-table\s*\{[^}]*min-width:\s*0;/,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-table-wrap\.is-mobile-records > \.control-table > tbody > tr > td\s*\{[^}]*height:\s*auto;[^}]*white-space:\s*normal;[^}]*overflow-wrap:\s*anywhere;/s,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-table-wrap\.is-mobile-records td::before\s*\{[^}]*content:\s*attr\(data-label\);[^}]*font-size:\s*12px;/s,
  );
  assert.match(
    mobileManagementCss,
    /@media \(max-width:\s*330px\)[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\)/,
  );
  assert.match(
    mobileManagementCss,
    /\.control-shell \.control-table td\.is-actions > button\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/s,
  );
  assert.match(managementSource, /className="control-table is-members-table"[\s\S]*?data-label="成员"[\s\S]*?data-label="角色"[\s\S]*?data-label="状态"[\s\S]*?data-label="账号 ID"[\s\S]*?data-label="操作"/);
  assert.match(managementSource, /className="control-table is-company-models-table"[\s\S]*?data-label="模型"[\s\S]*?data-label="能力版本"[\s\S]*?data-label="计费方式"[\s\S]*?data-label="公司单价"[\s\S]*?data-label="能力"/);
  assert.match(managementSource, /className="control-table is-report-consumption-table"[\s\S]*?data-label="任务"[\s\S]*?data-label="员工"[\s\S]*?data-label="模型"[\s\S]*?data-label="计费方式"[\s\S]*?data-label="单价"[\s\S]*?data-label="数量"[\s\S]*?data-label="实际消费"[\s\S]*?data-label="结算时间"/);
  assert.match(managementSource, /className="control-table is-download-audit-table"[\s\S]*?data-label="任务"[\s\S]*?data-label="产物"[\s\S]*?data-label="下载人"[\s\S]*?data-label="状态"[\s\S]*?data-label="签发时间"[\s\S]*?data-label="完成时间"[\s\S]*?data-label="已传输"/);
  assert.match(managementSource, /className="control-table is-wallet-ledger-table"[\s\S]*?data-label="类型"[\s\S]*?data-label="可用变动"[\s\S]*?data-label="预留变动"[\s\S]*?data-label="关联任务"[\s\S]*?data-label="说明"[\s\S]*?data-label="时间"/);
  assert.match(
    mobileManagementCss,
    /\.is-global-users-table td:nth-child\(1\)::before \{ content: "账号"; \}[\s\S]*?nth-child\(3\)::before \{ content: "个人积分"; \}[\s\S]*?nth-child\(7\)::before \{ content: "操作"; \}/,
  );
  assert.match(managementSource, /<ModelCapabilitySummary model=\{model\} compact \/>/, "company model rows must use the responsive capability record");
  assert.match(managementSource, /<ModelCapabilitySummary model=\{row\.model\} compact \/>/, "platform release evidence must use the same responsive capability record");
  assert.match(mobileManagementCss, /\.control-shell \.control-drawer,[\s\S]*?max-height:\s*calc\(100dvh - 18px\);/);
  assert.match(managementCss, /\.control-shell \.control-form,[\s\S]*?\.control-shell \.control-company-panel\s*\{[^}]*min-height:\s*0;[^}]*overflow:\s*auto;/);
});

test("company tables controls and semantic states meet the readable light contract", () => {
  assert.match(
    managementCss,
    /\.control-shell \.control-table\s*\{[^}]*font-size:\s*var\(--text-body-sm, 13px\);/s,
  );
  assert.match(
    managementCss,
    /\.control-shell \.control-table th\s*\{[^}]*font-size:\s*var\(--text-body-sm, 13px\);/s,
  );
  assert.match(
    managementCss,
    /\.control-shell \.control-drawer-error > button\s*\{[^}]*min-width:\s*32px;[^}]*min-height:\s*32px;/s,
  );
  assert.match(
    managementCss,
    /\.control-entitlement-actions button,[\s\S]*?min-height:\s*32px;/s,
  );
  assert.match(
    managementCss,
    /\.control-shell :is\([^)]*\.control-status\.is-active[^)]*\.control-status\.is-settle[^)]*\)\s*\{[^}]*color:\s*var\(--success\)/,
  );
  assert.match(
    managementCss,
    /\.control-shell :is\([^)]*\.control-status\.is-processing[^)]*\.control-status\.is-reserve[^)]*\)\s*\{[^}]*color:\s*var\(--warning\)/,
  );
  assert.match(
    managementCss,
    /\.control-shell :is\([^)]*\.control-status\.is-failed[^)]*\.control-status\.is-cancelled[^)]*\)\s*\{[^}]*color:\s*var\(--danger\)/,
  );
});
