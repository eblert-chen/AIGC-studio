import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const chromeSource = await readFile(
  new URL("../src/design-system/chrome.css", import.meta.url),
  "utf8",
);
const mobileStudioSource = await readFile(
  new URL("../src/design-system/mobile-studio.css", import.meta.url),
  "utf8",
);

test("keeps account semantics and compacts secondary chrome without mobile overflow", () => {
  assert.match(appSource, /className="popover-anchor notification-anchor"/);
  assert.match(appSource, /className="popover-anchor user-anchor"/);
  assert.match(
    appSource,
    /aria-label=\{`\$\{sessionIdentity\?\.display_name \|\| "已登录用户"\} · \$\{identityRoleLabel\(sessionIdentity\)\} · 账号菜单`\}/,
  );

  assert.match(
    chromeSource,
    /@media \(max-width:\s*900px\)[\s\S]*?\.topbar-account-cluster > :is\(\.skin-switcher, \.user-anchor, \.personal-balance\)\s*\{\s*display:\s*none;/,
  );
  assert.match(
    chromeSource,
    /@media \(max-width:\s*560px\)[\s\S]*?\.topbar-account-cluster > \.notification-anchor\s*\{\s*display:\s*none;/,
  );
  assert.match(
    mobileStudioSource,
    /\.app-shell\[data-theme\] > \.side-nav \.side-nav-track\s*\{[^}]*overflow-x:\s*auto;[^}]*overscroll-behavior-inline:\s*contain;[^}]*scrollbar-width:\s*none;/,
  );
  assert.match(
    mobileStudioSource,
    /\.app-shell\[data-theme\] > \.side-nav button\s*\{[^}]*min-height:\s*44px;/s,
  );
  assert.match(appSource, /if \(activeNav === "settings"\) \{[\s\S]*?<AccountCenter/);
  assert.match(appSource, /className="side-nav-footer"[\s\S]*?onClick=\{\(\) => navigateStudio\("settings"\)\}[\s\S]*?<span>设置<\/span>/);
  assert.match(appSource, /className="side-nav-scroll-forward"[\s\S]*?aria-label="显示后续导航；抵达末尾后返回开头"/);
});
