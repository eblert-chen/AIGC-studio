import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const skinSource = await readFile(
  new URL("../src/SkinSwitcher.jsx", import.meta.url),
  "utf8",
);
const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const managementSource = await readFile(
  new URL("../src/ManagementConsole.jsx", import.meta.url),
  "utf8",
);
const operationsSource = await readFile(
  new URL("../src/admin/OperationsConsole.jsx", import.meta.url),
  "utf8",
);
const mainSource = await readFile(new URL("../src/main.jsx", import.meta.url), "utf8");
const communitySource = await readFile(
  new URL("../src/community.css", import.meta.url),
  "utf8",
);
const lightThemeSource = await readFile(
  new URL("../src/light-theme.css", import.meta.url),
  "utf8",
);

const EXPECTED_SKINS = ["paper", "mist", "warm"];

function configuredSkinIds(source) {
  const options = source.match(
    /export\s+const\s+SKIN_OPTIONS\s*=\s*Object\.freeze\(\s*\[([\s\S]*?)\]\s*\)/,
  );
  assert.ok(options, "SKIN_OPTIONS must remain an exported, immutable allowlist");
  return [...options[1].matchAll(/\bid\s*:\s*["']([^"']+)["']/g)].map(
    ([, id]) => id,
  );
}

function openingTagWith(source, element, classMarker) {
  const tags = source.match(new RegExp(`<${element}\\b[\\s\\S]*?>`, "g")) || [];
  return tags.find((tag) => tag.includes(classMarker));
}

function cssRuleBody(source, selector) {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return source.match(new RegExp(`${escapedSelector}[^{}]*\\{([^}]*)\\}`))?.[1] || "";
}

test("the light skin preference has one explicit three-value allowlist and a safe paper default", () => {
  assert.deepEqual(configuredSkinIds(skinSource), EXPECTED_SKINS);
  assert.match(
    skinSource,
    /export\s+const\s+SKIN_STORAGE_KEY\s*=\s*["']yingchuang-skin["']\s*;/,
  );
  assert.match(
    skinSource,
    /return\s+SKIN_IDS\.has\(value\)\s*\?\s*value\s*:\s*["']paper["']\s*;/,
    "unknown stored or requested values must normalize to paper",
  );
  assert.match(skinSource, /localStorage\?\.getItem\(SKIN_STORAGE_KEY\)/);
  assert.match(skinSource, /localStorage\?\.setItem\(SKIN_STORAGE_KEY,\s*skin\)/);
  assert.match(
    skinSource,
    /function\s+readStoredSkin\(\)\s*\{[\s\S]*?try\s*\{[\s\S]*?normalizeSkin\([\s\S]*?catch\s*\{[\s\S]*?return\s+["']paper["']/,
    "storage access failures must fail safely to paper",
  );
  assert.match(skinSource, /useState\(readStoredSkin\)/);
});

test("studio, company management, and platform operations roots receive the shared theme", () => {
  const appRoot = openingTagWith(appSource, "div", "app-shell");
  const managementRoot = openingTagWith(managementSource, "div", "control-shell");
  const operationsRoot = openingTagWith(operationsSource, "div", "ops-console");

  assert.ok(appRoot, "the studio root must remain identifiable as app-shell");
  assert.ok(managementRoot, "the management root must remain identifiable as control-shell");
  assert.ok(operationsRoot, "the operations root must remain identifiable as ops-console");
  assert.match(appRoot, /data-theme=\{skin\}/);
  assert.match(managementRoot, /data-theme=\{activeSkin\}/);
  assert.match(operationsRoot, /data-theme=\{activeSkin\}/);
  assert.match(managementSource, /const\s+activeSkin\s*=\s*normalizeSkin\(skin\)/);
  assert.match(operationsSource, /const\s+activeSkin\s*=\s*normalizeSkin\(skin\)/);

  assert.match(
    appSource,
    /<ManagementConsole\b[\s\S]*?\bskin=\{skin\}[\s\S]*?\bonSkinChange=\{setSkin\}[\s\S]*?\/>/,
  );
  assert.match(
    operationsSource,
    /<SkinSwitcher\b[^>]*\bvalue=\{activeSkin\}[^>]*\bonChange=\{onSkinChange\}[^>]*\/>/,
  );
});

test("every Studio skin outranks the retired secondary-page palette", () => {
  const retiredPalette = cssRuleBody(communitySource, ".app-shell.is-secondary-page");
  const overriddenTokens = [
    ...retiredPalette.matchAll(/(--[a-z0-9-]+)\s*:/gi),
  ].map(([, token]) => token);

  assert.ok(
    overriddenTokens.length > 0,
    "the contract must observe the legacy secondary-page token block",
  );

  for (const skin of EXPECTED_SKINS) {
    const shellPalette = cssRuleBody(
      lightThemeSource,
      `.app-shell[data-theme="${skin}"]`,
    );
    assert.ok(shellPalette, `${skin} must declare a Studio-shell palette`);
    for (const token of overriddenTokens) {
      assert.match(
        shellPalette,
        new RegExp(`${token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*:`),
        `${skin} must re-assert ${token} after the legacy secondary-page rule`,
      );
    }
  }
});

test("main loads the explicit cascade through one design-system entry point", () => {
  const stylesheetImports = [
    ...mainSource.matchAll(/import\s+["']([^"']+\.css)["']\s*;/g),
  ].map(([, path]) => path);

  assert.deepEqual(stylesheetImports, ["./design-system/index.css"]);
  assert.match(lightThemeSource, /color-scheme\s*:\s*light\s*;/i);

  const declaredThemes = [
    ...lightThemeSource.matchAll(/data-(?:theme|skin)\s*=\s*["']([^"']+)["']/gi),
  ].map(([, id]) => id);
  assert.ok(declaredThemes.includes("mist"), "mist must have a light token override");
  assert.ok(declaredThemes.includes("warm"), "warm must have a light token override");
  assert.ok(
    declaredThemes.every((id) => EXPECTED_SKINS.includes(id)),
    `theme CSS contains a value outside the light allowlist: ${declaredThemes.join(", ")}`,
  );

  assert.doesNotMatch(lightThemeSource, /data-(?:theme|skin)\s*=\s*["']dark["']/i);
  assert.doesNotMatch(lightThemeSource, /prefers-color-scheme\s*:\s*dark/i);
  assert.doesNotMatch(lightThemeSource, /color-scheme\s*:\s*dark/i);
});
