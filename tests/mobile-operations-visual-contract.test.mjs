import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { operationsSource } from "./operations-source.mjs";

const mobileOperations = await readFile(
  new URL("../src/design-system/mobile-operations.css", import.meta.url),
  "utf8",
);

test("Operations mobile chrome is a sticky two-row command surface", () => {
  assert.match(mobileOperations, /@media \(max-width: 820px\)/);
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-topbar\s*\{[^}]*position:\s*sticky;[^}]*grid-template-rows:\s*56px 52px;[^}]*height:\s*108px;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-module-navigation\s*\{[^}]*grid-column:\s*1 \/ -1;[^}]*grid-row:\s*2;[^}]*grid-template-columns:\s*44px minmax\(0, 1fr\) 44px;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-topbar nav button\s*\{[^}]*min-height:\s*51px;[^}]*scroll-snap-align:\s*center;/s,
  );
});

test("task operations put urgent work first and keep the full lifecycle readable", () => {
  assert.match(
    mobileOperations,
    /data-active-section="task-operations"\] \.ops-task-grid\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;/s,
  );
  assert.match(
    mobileOperations,
    /data-active-section="task-operations"\] \.ops-exception-queue\s*\{[^}]*order:\s*-1;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-exception-list\s*\{[^}]*grid-auto-flow:\s*column;[^}]*overflow-x:\s*auto;[^}]*scroll-snap-type:\s*x mandatory;/s,
  );
  assert.match(operationsSource, /ops-exception-scroll-hint[\s\S]*?左右滑动查看待处理项/);
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-exception-scroll-hint\s*\{[^}]*display:\s*flex;[^}]*min-height:\s*34px;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-flow-scroll-region\s*\{[^}]*overflow-x:\s*auto;[^}]*scroll-snap-type:\s*x proximity;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-flow\s*\{[^}]*min-width:\s*max-content;[^}]*grid-auto-flow:\s*column;/s,
  );
});

test("mobile summaries reveal actions without turning into a card wall", () => {
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-cockpit-grid > \.ops-action-panel\s*\{[^}]*order:\s*-1;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-metric-strip\s*\{[^}]*min-width:\s*max-content;[^}]*grid-auto-flow:\s*column;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-coverage-list\s*\{[^}]*grid-auto-flow:\s*column;[^}]*overflow-x:\s*auto;/s,
  );
  assert.match(
    mobileOperations,
    /data-active-section="publishing-assets"\] \.ops-page-content > \.ops-callout\.is-warning\s*\{[^}]*order:\s*1;/s,
  );
  assert.doesNotMatch(mobileOperations, /backdrop-filter|(?:linear|radial|conic)-gradient\s*\(/i);
  assert.doesNotMatch(mobileOperations, /\.(?:app-shell|control-shell)\b/);
});

test("phone controls and evidence retain touch, type, and overflow contracts", () => {
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-range-controls \.ops-icon-button\s*\{[^}]*width:\s*44px;[^}]*min-width:\s*44px;[^}]*height:\s*44px;[^}]*min-height:\s*44px;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-page-controls > \.ops-primary-button\s*\{[^}]*width:\s*100%;[^}]*min-height:\s*46px;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-table-wrap\s*\{[^}]*max-width:\s*calc\(100vw - \(var\(--ops-mobile-gutter\) \* 2\)\);[^}]*overflow-x:\s*auto;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-console \.ops-table\s*\{[^}]*font-size:\s*var\(--text-body-sm, 13px\)/s,
  );
  assert.doesNotMatch(mobileOperations, /font-size:\s*(?:9|10|11)px/);
});

test("mobile-only presentation keeps guarded Operations semantics intact", () => {
  assert.match(operationsSource, /未知提交只允许人工对账，严禁自动重试或跨渠道切换/);
  assert.match(operationsSource, /结果未知的发布任务禁止自动重试/);
  assert.match(operationsSource, /operation_id 已锁定/);
  assert.match(operationsSource, /凭据、上游地址、请求头和原始错误不会进入浏览器/);
  assert.doesNotMatch(mobileOperations, /display:\s*none[^}]*ops-(?:table-link|primary-button|native-console)/s);
});
