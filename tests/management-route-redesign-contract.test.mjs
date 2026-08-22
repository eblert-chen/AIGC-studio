import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { managementSource } from "./management-source.mjs";

const source = await readFile(
  new URL("../src/ManagementConsole.jsx", import.meta.url),
  "utf8",
);
const css = await readFile(
  new URL("../src/design-system/management-routes.css", import.meta.url),
  "utf8",
);

test("company and platform management correct stale sections without rendering a fallback overview", () => {
  assert.match(
    source,
    /if \(!nav\.length \|\| nav\.some\(\(\[id\]\) => id === section\)\) return;\s*setSection\(nav\[0\]\[0\]\);/,
  );
  assert.match(source, /const sectionIsAccessible = nav\.some\(\(\[id\]\) => id === section\)/);
  assert.match(
    source,
    /const content = !nav\.length\s*\? <ManagementAccessState mode=\{mode\} pending=\{isResolvingIdentity\} \/>\s*: sectionIsAccessible/,
  );
  assert.doesNotMatch(
    source,
    /const content = mode === "company"[\s\S]*?\[section\]\?\.\(\)/,
    "content must be permission-gated before a route renderer is selected",
  );
});

test("management sections restore from the URL and expose non-sensitive page titles", () => {
  assert.match(source, /function managementSectionParam\(mode\)/);
  assert.match(source, /function sectionFromLocation\(mode, fallback\)/);
  assert.match(
    source,
    /setSection\(sectionFromLocation\(\s*mode,\s*mode === "platform" \? initialPlatformSection : "overview",\s*\)\)/,
  );
  assert.match(source, /globalThis\.history\?\.replaceState\?\.\(\{\}, "", `\$\{url\.pathname\}\$\{url\.search\}\$\{url\.hash\}`\)/);
  assert.match(source, /document\.title = `\$\{activeSectionLabel\} · \$\{mode === "platform" \? "平台基础配置" : "公司管理"\} · 旭天 AI VIDEO`/);
  assert.doesNotMatch(source, /document\.title[\s\S]{0,120}(?:company_id|user_id|membership_id)/);
});

test("legacy platform configuration authenticates and authorizes before data-plane reads", () => {
  const platformBranch = source.slice(
    source.indexOf("let identity = data.adminMe || initialPlatformIdentity"),
    source.indexOf("} catch (loadError)"),
  );
  assert.match(platformBranch, /identity = await client\.getPlatformAdminMe\(\{ signal \}\)/);
  assert.match(platformBranch, /if \(!canOpenPlatformSection\(identity, requestedSection, demoMode\)\)/);
  assert.ok(
    platformBranch.indexOf("canOpenPlatformSection(identity, requestedSection, demoMode)")
      < platformBranch.indexOf("client.getPlatformDashboard"),
    "permission validation must precede the first platform dashboard request",
  );
  assert.match(source, /没有基础配置模块权限/);
});

test("company reports do not probe member or model APIs without their read permissions", () => {
  const reportsBranch = source.slice(
    source.indexOf('} else if (requestedSection === "reports") {'),
    source.indexOf('} else if (requestedSection === "wallet") {'),
  );
  assert.match(reportsBranch, /permissions\.has\("users\.read"\)/);
  assert.match(reportsBranch, /permissions\.has\("models\.read"\)/);
  assert.match(reportsBranch, /canReadUsers[\s\S]*?client\.listMembers\(\{ signal \}\)/);
  assert.match(reportsBranch, /canReadModels[\s\S]*?client\.listModels\(\{ signal \}\)/);
});

test("compact model capability records disclose every server-derived row on demand", () => {
  assert.match(managementSource, /<details className="model-capability-summary is-compact">/);
  assert.match(managementSource, /<summary>[\s\S]*?\{rows\.length\} 个模式[\s\S]*?查看能力[\s\S]*?<\/summary>/);
  assert.match(managementSource, /className="model-capability-details"[\s\S]*?rows\.map\(\(row\) =>/);
  assert.equal(
    [...managementSource.matchAll(/<ModelCapabilitySummary model=\{model\} compact \/>/g)].length,
    2,
  );
});

test("management route styling uses one light precision geometry and continuous data surfaces", () => {
  assert.match(css, /--management-chrome:\s*56px/);
  assert.match(css, /--management-canvas:\s*var\(--bg, #fff\)/);
  assert.match(css, /\.control-shell \.control-button\s*\{[\s\S]*?border-radius:\s*6px/);
  assert.match(css, /\.control-shell \.control-section\s*\{[\s\S]*?border-radius:\s*10px/);
  assert.match(css, /\.control-shell \.control-drawer\s*\{[\s\S]*?border-radius:\s*14px 0 0 14px/);
  assert.match(
    css,
    /\.control-shell \.control-summary-strip\s*\{[\s\S]*?border-block:[\s\S]*?background:\s*transparent/,
  );
  assert.match(css, /\.control-shell \.control-table td\s*\{[\s\S]*?font-size:\s*13px/);
  assert.doesNotMatch(css, /(?:linear|radial|conic)-gradient\(/);
  assert.doesNotMatch(css, /backdrop-filter|:has\(/);
});

test("the no-access state is explicit and does not masquerade as an empty dashboard", () => {
  assert.match(managementSource, /function ManagementAccessState\(/);
  assert.match(managementSource, /没有可访问的管理模块/);
  assert.match(managementSource, /完成前不会显示任何管理数据/);
  assert.match(css, /\.control-shell \.control-access-state\s*\{/);
});

test("management reports use server totals, explicit download scope and bounded paging", () => {
  assert.match(
    source,
    /client\.getTaskReport\(\{ page_size: 1, status: "succeeded" \}, \{ signal \}\)/,
  );
  assert.match(source, /data\.succeededTaskReport\?\.total \|\| 0/);
  assert.match(
    source,
    /const downloadFilters = \{[\s\S]*?scope: "company",[\s\S]*?employee_user_id:[\s\S]*?start_time:[\s\S]*?end_time:/,
  );
  assert.match(managementSource, /function PageLoadMore\(/);
  assert.match(managementSource, /onLoadMore=\{\(\) => loadMore\("platform-audit"\)\}/);
});

test("partial platform permissions and expired access fail closed", () => {
  assert.match(
    source,
    /companies: \["platform\.companies\.read"\],[\s\S]*?reports: \["platform\.finance\.read"\]/,
  );
  assert.match(source, /const invalidateSensitiveData = useCallback/);
  assert.match(source, /setData\(emptySnapshot\(\)\)/);
  assert.match(source, /const nav = accessInvalidated\s*\? \[\]/);
  assert.match(
    managementSource,
    /disabled=\{!canUsePlatformPermission\("platform\.entitlements\.read"\) && !canUsePlatformPermission\("platform\.finance\.read"\)\}/,
  );
});

test("audit detail, tabs and mobile records remain complete and operable", () => {
  assert.match(managementSource, /function AuditChangeSummary\(/);
  assert.match(managementSource, /before=\{event\.before_summary\} after=\{event\.after_summary\}/);
  assert.match(source, /role="tab"[\s\S]*?aria-selected=[\s\S]*?aria-controls=[\s\S]*?onKeyDown=/);
  assert.match(source, /event\.key === "ArrowRight"[\s\S]*?event\.key === "Home"[\s\S]*?event\.key === "End"/);
  assert.match(source, /className="control-table is-members-table"/);
  assert.match(managementSource, /className="control-table is-companies-table"/);
  assert.match(managementSource, /className="control-table is-audit-table"/);
  assert.match(css, /\.control-shell \.control-mobile-session\s*\{[\s\S]*?display:\s*none/);
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*?\.control-shell \.control-mobile-session\s*\{[\s\S]*?display:\s*block/);
  assert.match(css, /min-height:\s*var\(--control-size-touch, 44px\)/);
});

test("mobile management actions keep the shared 44px touch contract", () => {
  const mobileRules = css.slice(css.indexOf("@media (max-width: 720px)"));
  assert.match(
    mobileRules,
    /\.control-shell \.surface-switch button,[\s\S]*?\.control-shell \.control-button,[\s\S]*?\.control-shell \.control-section-title > button,[\s\S]*?min-height:\s*var\(--control-size-touch, 44px\)/,
  );
  assert.match(
    mobileRules,
    /\.control-filterbar :is\(select, input, button\),[\s\S]*?\.model-capability-summary\.is-compact > summary,[\s\S]*?\.control-table td\.is-actions button\s*\{[^}]*min-height:\s*var\(--control-size-touch, 44px\)/,
  );
  assert.match(
    mobileRules,
    /\.control-table td\.is-actions button\s*\{[^}]*min-width:\s*var\(--control-size-touch, 44px\)/,
  );
  assert.match(
    mobileRules,
    /\.control-table-wrap\.is-mobile-records td\.is-actions > button\s*\{[^}]*min-width:\s*var\(--control-size-touch, 44px\)/,
  );
  assert.match(
    mobileRules,
    /\.control-role-actions > button\s*\{[^}]*min-width:\s*var\(--control-size-touch, 44px\)/,
  );
});
