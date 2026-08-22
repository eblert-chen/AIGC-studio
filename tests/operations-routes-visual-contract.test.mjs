import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { operationsSource } from "./operations-source.mjs";

const operationsRoutes = readFileSync(
  new URL("../src/design-system/operations-routes.css", import.meta.url),
  "utf8",
);

test("Operations exposes the active module and a stable page-title relationship", () => {
  assert.match(
    operationsSource,
    /className=\{cx\("ops-console", className\)\}[\s\S]*?data-active-section=\{renderSection \|\| "access"\}/,
  );
  assert.match(operationsSource, /<h1 id="ops-page-title">\{title\}<\/h1>/);
  assert.match(operationsSource, /<main aria-labelledby="ops-page-title">/);
});

test("Operations resolves every rendered and brand navigation target through the permission-filtered modules", () => {
  assert.match(
    operationsSource,
    /const renderSection = availableNavItems\.some\(\(item\) => item\.id === activeSection\)[\s\S]*?: availableNavItems\[0\]\?\.id \|\| ""/,
  );
  assert.match(
    operationsSource,
    /const targetSection = availableNavItems\.some\(\(item\) => item\.id === section\)[\s\S]*?if \(!targetSection\) return/,
  );
  assert.match(
    operationsSource,
    /const brandTargetSection = availableNavItems\.some\(\(item\) => item\.id === "cockpit"\)[\s\S]*?: availableNavItems\[0\]\?\.id \|\| ""/,
  );
  assert.doesNotMatch(operationsSource, /onClick=\{\(\) => navigate\("cockpit"\)\}/);
});

test("Operations Canvas uses hierarchy instead of an equal-weight card wall", () => {
  assert.match(
    operationsRoutes,
    /\.ops-console \.ops-page-title p\s*\{[\s\S]*?display:\s*block;[\s\S]*?font-size:\s*var\(--text-body-sm, 13px\)/,
  );
  assert.match(
    operationsRoutes,
    /\.ops-console \.ops-panel\s*\{[\s\S]*?border:\s*0;[\s\S]*?border-block-start:\s*1px solid var\(--ops-line-strong\);[\s\S]*?border-radius:\s*0;[\s\S]*?box-shadow:\s*none;/,
  );
  assert.match(
    operationsRoutes,
    /\.ops-console \.ops-panel-header h2\s*\{[\s\S]*?font-size:\s*var\(--ops-panel-heading\)/,
  );
  assert.match(
    operationsRoutes,
    /--ops-panel-heading:\s*clamp\(1rem,[\s\S]*?1\.125rem\)/,
  );
});

test("live state, evidence, and guarded operations have distinct semantic bands", () => {
  assert.match(
    operationsRoutes,
    /\.ops-console \.ops-flow-panel\s*\{[^}]*border-block-start:\s*2px solid var\(--ops-accent\)/s,
  );
  assert.match(
    operationsRoutes,
    /\.ops-console \.ops-reliability-panel\s*\{[^}]*border-block-start:\s*2px solid var\(--ops-evidence-line\)/s,
  );
  assert.match(
    operationsRoutes,
    /data-active-section="channels"\] \.ops-relay-callback-dlq-panel\s*\{[^}]*var\(--ops-orange\)/s,
  );
  assert.match(
    operationsRoutes,
    /data-active-section="channels"\] \.ops-relay-reconciliation-panel\s*\{[^}]*var\(--ops-red\)/s,
  );
  assert.match(
    operationsRoutes,
    /\.ops-console \.ops-native-console-entry\s*\{[\s\S]*?border-inline-start:\s*3px solid var\(--ops-orange\);[\s\S]*?background:\s*var\(--ops-orange-soft\)/,
  );
});

test("evidence tables keep readable 13px copy and restrained row dividers", () => {
  assert.match(
    operationsRoutes,
    /\.ops-console \.ops-table\s*\{[\s\S]*?font-size:\s*var\(--text-body-sm, 13px\)/,
  );
  assert.match(
    operationsRoutes,
    /\.ops-console \.ops-table th,[\s\S]*?\.ops-console \.ops-table td\s*\{[\s\S]*?border-inline-end:\s*0;[\s\S]*?border-block-end:\s*1px solid var\(--ops-line\)/,
  );
  assert.match(
    operationsRoutes,
    /\.ops-console \.ops-table th\s*\{[\s\S]*?position:\s*sticky;[\s\S]*?background:\s*var\(--ops-surface-soft\)/,
  );
});

test("Operations refinement remains light, token-driven, and responsive", () => {
  assert.doesNotMatch(operationsRoutes, /(?:linear|radial|conic)-gradient\s*\(/i);
  assert.doesNotMatch(operationsRoutes, /text-shadow\s*:/i);
  assert.match(operationsRoutes, /min-height:\s*100dvh/);
  assert.match(operationsRoutes, /@media \(max-width:\s*820px\)/);
  assert.match(operationsRoutes, /@media \(max-width:\s*560px\)/);
  assert.match(
    operationsRoutes,
    /@media \(max-width:\s*560px\)[\s\S]*?\.ops-console\s*\{[\s\S]*?font-size:\s*var\(--text-body-sm, 13px\)/,
  );
});

test("visual refinement does not weaken guarded Operations workflows", () => {
  assert.match(operationsSource, /未知提交只允许人工对账，严禁自动重试或跨渠道切换/);
  assert.match(operationsSource, /结果未知的发布任务禁止自动重试/);
  assert.match(operationsSource, /成本缺失不会被静默当作零/);
  assert.match(operationsSource, /高风险运维/);
  assert.match(operationsSource, /operation_id 已锁定/);
});
