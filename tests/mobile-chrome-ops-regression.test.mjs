import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const mobileStudio = await readFile(
  new URL("../src/design-system/mobile-studio.css", import.meta.url),
  "utf8",
);
const mobileOperations = await readFile(
  new URL("../src/design-system/mobile-operations.css", import.meta.url),
  "utf8",
);

test("extreme-phone Studio selectors stay bounded and expose a visible focus proxy", () => {
  assert.match(
    mobileStudio,
    /:is\(\.project-select, \.demo-account-switcher\) select\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*max-width:\s*100%;[^}]*box-sizing:\s*border-box;/s,
  );
  assert.match(
    mobileStudio,
    /@media \(max-width: 360px\)[\s\S]*?\.topbar-command-cluster \.project-select\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;[^}]*min-height:\s*44px;/s,
  );
  assert.match(
    mobileStudio,
    /\.topbar-command-cluster \.project-select:focus-within\s*\{[^}]*background:\s*var\(--accent-soft\);[^}]*box-shadow:\s*inset 0 0 0 2px var\(--focus-ring\);/s,
  );
  assert.match(
    mobileStudio,
    /\.topbar-command-cluster \.project-select select\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;[^}]*max-width:\s*44px;[^}]*min-height:\s*44px;/s,
  );
  assert.match(
    mobileStudio,
    /> \.topbar \.demo-account-switcher\s*\{[^}]*width:\s*92px;[^}]*min-width:\s*92px;[^}]*flex-basis:\s*92px;/s,
  );
  assert.match(
    mobileStudio,
    /> \.topbar \.demo-account-switcher select\s*\{[^}]*min-width:\s*44px;/s,
  );
});

test("phone workspace actions and Operations chart disclosures meet the 44px contract", () => {
  assert.match(
    mobileStudio,
    /\.app-shell\[data-theme\] \.surface-switch button\s*\{[^}]*height:\s*44px;[^}]*min-height:\s*44px;/s,
  );
  assert.match(
    mobileOperations,
    /@media \(max-width: 820px\)[\s\S]*?\.ops-console \.ops-chart-data > summary\s*\{[^}]*min-height:\s*44px;/s,
  );
  assert.match(
    mobileOperations,
    /\.ops-admin-tools \.demo-account-switcher select\s*\{[^}]*width:\s*100%;[^}]*max-width:\s*100%;[^}]*min-width:\s*0;[^}]*height:\s*44px;[^}]*box-sizing:\s*border-box;/s,
  );
});
