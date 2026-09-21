import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { managementSource } from "./management-source.mjs";

const managementStyles = await readFile(
  new URL("../src/design-system/management-routes.css", import.meta.url),
  "utf8",
);
const mobileStyles = await readFile(
  new URL("../src/design-system/mobile-management.css", import.meta.url),
  "utf8",
);

test("account lifecycle exposes truthful personal wallet evidence and owner-only grant affordance", () => {
  assert.match(managementSource, /personal_workspace_id/);
  assert.match(managementSource, /personal_workspace_active/);
  assert.match(managementSource, /available_points/);
  assert.match(managementSource, /reserved_points/);
  assert.match(managementSource, /只有平台所有者可以向个人用户赠送积分/);
  assert.match(managementSource, /完成正式登录后初始化个人空间/);
  assert.match(managementSource, /personalPointGrantEligibilityError\(user\)/);
  assert.match(managementSource, /赠送积分/);
});

test("personal point grant form preserves strict integer and idempotent retry semantics", () => {
  assert.match(managementSource, /makeOperationKey\("personal-points-grant"\)/);
  assert.match(managementSource, /Number\.isSafeInteger\(amountPoints\)/);
  assert.match(managementSource, /name="amountPoints" type="number" inputMode="numeric" min="1"/);
  assert.match(managementSource, /name="note" minLength=\{1\} maxLength=\{240\} required/);
  assert.match(managementSource, /当前操作凭据会保留，可直接重试而不会重复入账/);
  assert.match(managementSource, /该请求已安全重放，系统未重复增加积分/);
  assert.match(managementSource, /开始下一笔/);
});

test("grant result refreshes wallet evidence and renders server-scoped grant history", () => {
  assert.match(managementSource, /listPersonalUserPointGrants/);
  assert.match(managementSource, /wallet\.available_points \?\? item\.available_points/);
  assert.match(managementSource, /wallet\.reserved_points \?\? item\.reserved_points/);
  assert.match(managementSource, /最近赠送记录/);
  assert.match(managementSource, /累计 \$\{personalPointBalanceLabel\(drawer\.history\.total_amount_points \|\| 0\)\}/);
  assert.match(managementSource, /drawer\.history\.items\.map/);
  assert.doesNotMatch(managementSource, /entry\.idempotency_key/);
});

test("personal grant operation stays usable in dense desktop and phone layouts", () => {
  assert.match(managementStyles, /\.is-global-users-table \{ min-width: 940px; \}/);
  assert.match(managementStyles, /\.control-personal-point-history/);
  assert.match(managementStyles, /\.control-personal-point-result/);
  assert.match(mobileStyles, /is-global-users-table td:nth-child\(3\)::before \{ content: "个人积分"; \}/);
  assert.match(mobileStyles, /is-global-users-table td:nth-child\(7\)::before \{ content: "操作"; \}/);
  assert.match(mobileStyles, /\.control-personal-point-result \{ grid-template-columns: 20px minmax\(0, 1fr\); \}/);
});
