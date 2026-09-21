import assert from "node:assert/strict";
import test from "node:test";
import { managementSource as source } from "./management-source.mjs";


test("Company and platform basic configuration share one responsive demo persona control", () => {
  assert.match(source, /function ManagementConfigurationConsole\(\{/);
  assert.match(source, /data-management-mode=\{mode\}/);
  assert.equal((source.match(/<DemoAccountSwitcher\b/g) || []).length, 1);
  assert.match(source, /globalThis\.matchMedia\?\.\("\(max-width: 720px\)"\)/);
  assert.match(
    source,
    /const activeDemoPersonaHost = compactManagementChrome\s*\? mobileDemoPersonaHost\s*:\s*desktopDemoPersonaHost/,
  );
  assert.match(
    source,
    /createPortal\([\s\S]*?<DemoAccountSwitcher value=\{demoPersonaId\} onChange=\{onDemoPersonaChange\} \/>[\s\S]*?activeDemoPersonaHost/,
  );
  assert.match(source, /ref=\{setMobileDemoPersonaHost\} className="control-demo-persona-host is-mobile"/);
  assert.match(source, /ref=\{setDesktopDemoPersonaHost\} className="control-demo-persona-host is-desktop"/);
});
