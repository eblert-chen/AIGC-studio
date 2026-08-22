import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const stylesSource = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");

test("keeps the compact account menu accessible without mobile page overflow", () => {
  assert.match(appSource, /className="popover-anchor notification-anchor"/);
  assert.match(appSource, /className="popover-anchor user-anchor"/);
  assert.match(
    appSource,
    /aria-label=\{`\$\{sessionIdentity\?\.display_name \|\| "已登录用户"\} · \$\{identityRoleLabel\(sessionIdentity\)\} · 账号菜单`\}/,
  );

  const mobileRules = stylesSource.slice(stylesSource.indexOf("@media (max-width: 620px)"));
  assert.match(mobileRules, /\.topbar \.notification-anchor\s*\{\s*display:\s*none;/);
  assert.match(mobileRules, /\.topbar \.user-button\s*\{[\s\S]*?width:\s*36px;[\s\S]*?padding:\s*0;/);
  assert.match(mobileRules, /\.topbar \.user-button > svg:last-child\s*\{\s*display:\s*none;/);
});
