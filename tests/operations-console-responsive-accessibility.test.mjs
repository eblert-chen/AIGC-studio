import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { operationsSource } from "./operations-source.mjs";

const operationsCss = await readFile(
  new URL("../src/design-system/operations-routes.css", import.meta.url),
  "utf8",
);
const operationsRoutes = await readFile(
  new URL("../src/design-system/operations-routes.css", import.meta.url),
  "utf8",
);
const mobileOperations = await readFile(
  new URL("../src/design-system/mobile-operations.css", import.meta.url),
  "utf8",
);
const finalOperationsCss = `${operationsRoutes}\n${mobileOperations}`;

test("permissions and audit tabs expose a complete keyboard and ARIA contract", () => {
  assert.match(operationsSource, /tabId: "ops-access-audit-tab-audit"/);
  assert.match(operationsSource, /panelId: "ops-access-audit-panel-audit"/);
  assert.match(operationsSource, /tabId: "ops-access-audit-tab-access"/);
  assert.match(operationsSource, /panelId: "ops-access-audit-panel-access"/);
  assert.match(operationsSource, /tabIndex=\{selected \? 0 : -1\}/);
  assert.match(operationsSource, /aria-controls=\{item\.panelId\}/);
  assert.match(operationsSource, /event\.key === "ArrowRight"/);
  assert.match(operationsSource, /event\.key === "ArrowLeft"/);
  assert.match(operationsSource, /event\.key === "Home"/);
  assert.match(operationsSource, /event\.key === "End"/);
  assert.match(operationsSource, /tabRefs\.current\.get\(nextTab\.id\)\?\.focus\(\)/);
  assert.match(
    operationsSource,
    /id="ops-access-audit-panel-audit"[\s\S]*?role="tabpanel"[\s\S]*?aria-labelledby="ops-access-audit-tab-audit"/,
  );
  assert.match(
    operationsSource,
    /id="ops-access-audit-panel-access"[\s\S]*?role="tabpanel"[\s\S]*?aria-labelledby="ops-access-audit-tab-access"/,
  );
});

test("audit and exception filters have explicit accessible names", () => {
  assert.match(
    operationsSource,
    /<span className="sr-only">搜索审计事件<\/span>[\s\S]*?<input value=\{auditQuery\}/,
  );
  assert.match(
    operationsSource,
    /<span className="sr-only">筛选审计结果<\/span>[\s\S]*?<select value=\{auditResult\}/,
  );
  assert.match(
    operationsSource,
    /<span className="sr-only">搜索异常<\/span>[\s\S]*?<input value=\{query\}/,
  );
  assert.match(
    operationsSource,
    /<span className="sr-only">筛选异常优先级<\/span>[\s\S]*?<select value=\{priority\}/,
  );
  assert.match(
    operationsSource,
    /<span className="sr-only">筛选异常状态<\/span><select value=\{status\}/,
  );
});

test("operations nav exposes accessible two-way overflow controls and keeps the active module visible", () => {
  assert.match(operationsSource, /const operationsNavRef = useRef\(null\)/);
  assert.match(operationsSource, /navigation\.scrollWidth - navigation\.clientWidth/);
  assert.match(operationsSource, /before: navigation\.scrollLeft > 1/);
  assert.match(operationsSource, /after: navigation\.scrollLeft < maxScroll - 1/);
  assert.match(operationsSource, /activeButton\?\.scrollIntoView\(\{ block: "nearest", inline: "center" \}\)/);
  assert.match(operationsSource, /scrollRoot\.scrollTop = documentScroll\.top/);
  assert.match(operationsSource, /new ResizeObserver\(keepActiveModuleVisible\)/);
  assert.match(operationsSource, /aria-label="查看前面的平台模块"/);
  assert.match(operationsSource, /aria-label="查看更多平台模块"/);
  assert.match(operationsSource, /aria-current=\{renderSection === item\.id \? "page" : undefined\}/);
  assert.doesNotMatch(operationsSource, /ops-nav-overflow-hint/);
  assert.doesNotMatch(finalOperationsCss, /ops-nav-overflow-hint/);
});

test("mobile operations copy uses the shared 12px floor without removing internal table scrolling", () => {
  const mobileStart = mobileOperations.indexOf("@media (max-width: 560px)");
  const mobileEnd = mobileOperations.indexOf("@media (prefers-reduced-motion: reduce)", mobileStart);
  const mobileStyles = mobileOperations.slice(mobileStart, mobileEnd);

  assert.ok(mobileStart >= 0 && mobileEnd > mobileStart);
  assert.doesNotMatch(`${operationsCss}\n${mobileStyles}`, /font-size:\s*(?:8|9|10|11)px\s*;/);
  assert.match(operationsCss, /\.ops-console :is\(\.ops-form-field > span, \.ops-detail-grid strong, \.ops-permission-groups strong\)\s*\{[^}]*font-size:\s*var\(--text-body-sm, 13px\)/s);
  assert.match(operationsCss, /\.ops-table th\s*\{[\s\S]*?font-size:\s*var\(--text-body-sm, 13px\)/);
  assert.match(operationsCss, /\.ops-table-wrap\s*\{[^}]*overflow:\s*auto/s);
  assert.match(mobileOperations, /@media \(max-width: 820px\)[\s\S]*?\.ops-flow-scroll-region\s*\{[^}]*overflow-x:\s*auto/s);
  assert.match(operationsSource, /横向滑动查看完整任务链路/);
  assert.match(operationsCss, /\.ops-table\s*\{[^}]*min-width:\s*820px/s);
});

test("narrow layouts retain readable controls, discoverable actions, and collision-free operational data", () => {
  assert.match(operationsSource, /className="ops-basic-config-button" data-icon-only="true"/);
  assert.match(operationsSource, /className="ops-basic-config-label">基础配置<\/span>/);
  assert.match(finalOperationsCss, /@media \(max-width: 560px\)[\s\S]*?\.ops-basic-config-label\s*\{[^}]*display:\s*none/s);
  assert.match(finalOperationsCss, /\.ops-console \.ops-basic-config-button\s*\{[^}]*width:\s*44px[^}]*min-height:\s*44px/s);
  assert.match(finalOperationsCss, /@media \(max-width: 1180px\)[\s\S]*?\.ops-task-grid,[\s\S]*?\.ops-analysis-grid\s*\{[^}]*grid-template-columns:\s*1fr/s);
  assert.match(finalOperationsCss, /\.ops-audit-diff\.is-compact > div\s*\{[^}]*grid-template-areas:[\s\S]*?"key key key"/s);
  assert.match(finalOperationsCss, /\.ops-table-wrap\.has-sticky-actions \.ops-table td:not\(\[colspan\]\):last-child\s*\{[^}]*position:\s*sticky[^}]*right:\s*0/s);
  assert.match(operationsSource, /左右滑动查看字段/);
  assert.match(finalOperationsCss, /\.ops-check,[\s\S]*?\.ops-product-head\s*\{[^}]*min-height:\s*44px/s);
  assert.match(operationsRoutes, /\.ops-range-controls\.has-time-range\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) minmax\(0, 1fr\) 44px/s);
  assert.match(operationsRoutes, /\.ops-console \.ops-icon-button,[\s\S]*?\.ops-console \.ops-drawer-close\s*\{[^}]*width:\s*44px[^}]*height:\s*44px/s);
  assert.match(operationsRoutes, /@media \(max-width: 1100px\) and \(min-width: 821px\)[\s\S]*?\.ops-page-title\s*\{[^}]*flex-direction:\s*column/s);
  assert.match(operationsRoutes, /\.ops-console :is\(\.ops-select-control, \.ops-environment-label\)\s*\{[^}]*white-space:\s*nowrap/s);
  assert.match(operationsRoutes, /\.ops-drawer\.is-wide\s*\{[^}]*height:\s*min\(94dvh/s);
  assert.match(operationsRoutes, /\.ops-audit-table :is\(th, td\):nth-child\(5\),[\s\S]*?display:\s*none/s);
  assert.match(operationsSource, /tickFormatter=\{formatModelAxisTick\}[\s\S]*?minTickGap=\{10\}/);
  assert.doesNotMatch(operationsSource, /<XAxis[^>]*interval=\{0\}/);
  assert.doesNotMatch(finalOperationsCss, /!important/);
});

test("phone navigation, identity and filter controls retain 44px touch targets", () => {
  const tabletRules = mobileOperations.slice(
    mobileOperations.indexOf("@media (max-width: 820px)"),
    mobileOperations.indexOf("@media (max-width: 560px)"),
  );
  const phoneRules = mobileOperations.slice(
    mobileOperations.indexOf("@media (max-width: 560px)"),
  );

  assert.match(tabletRules, /\.ops-console \.ops-topbar nav button\s*\{[^}]*min-height:\s*51px/s);
  assert.match(tabletRules, /\.ops-console \.ops-nav-scroll-button\s*\{[^}]*min-height:\s*51px/s);
  assert.match(tabletRules, /\.ops-admin-tools \.skin-switcher select\s*\{[^}]*min-height:\s*44px/s);
  assert.match(tabletRules, /\.ops-admin-tools \.demo-account-switcher select\s*\{[^}]*min-height:\s*44px/s);
  assert.match(finalOperationsCss, /\.ops-filterbar input,[\s\S]*?\.ops-filterbar select[\s\S]*?min-height:\s*44px/);
  assert.match(finalOperationsCss, /\.ops-range-controls select,[\s\S]*?min-height:\s*44px/);
  assert.match(finalOperationsCss, /\.ops-model-row-link,[\s\S]*?\.ops-table-actions button,[\s\S]*?\.ops-table-link[\s\S]*?min-width:\s*44px/);
  assert.match(phoneRules, /@media \(max-width: 360px\)[\s\S]*?--ops-mobile-gutter:\s*12px/);
});
