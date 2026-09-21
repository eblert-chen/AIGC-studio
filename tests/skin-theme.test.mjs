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
const tokensSource = await readFile(
  new URL("../src/design-system/tokens.css", import.meta.url),
  "utf8",
);
const studioRoutesSource = await readFile(
  new URL("../src/design-system/studio-routes.css", import.meta.url),
  "utf8",
);
const controlsSource = await readFile(
  new URL("../src/design-system/controls.css", import.meta.url),
  "utf8",
);
const designSystemIndex = await readFile(
  new URL("../src/design-system/index.css", import.meta.url),
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

test("the skin chooser is a product menu rather than a browser-native select", () => {
  assert.doesNotMatch(skinSource, /<select\b|<option\b/);
  assert.match(skinSource, /className="skin-switcher-trigger"/);
  assert.match(skinSource, /aria-haspopup="menu"/);
  assert.match(skinSource, /aria-expanded=\{open\}/);
  assert.match(skinSource, /role="menuitemradio"/);
  assert.match(skinSource, /aria-checked=\{skin === option\.id\}/);
  assert.match(skinSource, /data-skin-sample=\{skin\}/);
  assert.match(skinSource, /<SkinSample skin=\{option\.id\} large \/>/);
  assert.match(skinSource, /createPortal\(/);
  assert.match(skinSource, /addEventListener\("pointerdown", handleOutsidePointer, true\)/);
  for (const key of ["ArrowDown", "ArrowUp", "Home", "End", "Escape", "Enter", "Tab"]) {
    assert.ok(skinSource.includes(`event.key === "${key}"`), `${key} must remain supported`);
  }
  assert.match(skinSource, /focusAdjacentControl\(triggerRef\.current, event\.shiftKey\)/);
  assert.match(controlsSource, /\.skin-switcher-menu\s*\{[^}]*position:\s*fixed;[^}]*box-shadow:\s*var\(--shadow-menu/s);
  assert.match(controlsSource, /\.skin-switcher-option\[aria-checked="true"\]/);
  assert.match(controlsSource, /\.skin-switcher-trigger:focus-visible,[\s\S]*?outline:\s*2px solid/);
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

test("every light skin is complete in the token layer and routes do not override it", () => {
  const requiredTokens = [
    "--bg",
    "--canvas",
    "--surface",
    "--line",
    "--line-strong",
    "--text",
    "--text-soft",
    "--text-muted",
    "--accent",
    "--accent-strong",
    "--accent-soft",
  ];
  for (const skin of EXPECTED_SKINS) {
    assert.match(tokensSource, new RegExp(`--skin-${skin}-bg\\s*:`));
    assert.match(tokensSource, new RegExp(`--skin-${skin}-surface\\s*:`));
    assert.match(tokensSource, new RegExp(`--skin-${skin}-line\\s*:`));
  }
  for (const skin of EXPECTED_SKINS) {
    const palette = cssRuleBody(
      tokensSource,
      `:where([data-theme="${skin}"], [data-skin="${skin}"])`,
    );
    assert.ok(palette, `${skin} must declare a shared light palette`);
    for (const token of requiredTokens) {
      assert.match(
        palette,
        new RegExp(`${token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*:`),
        `${skin} must own ${token}`,
      );
    }
  }

  assert.doesNotMatch(studioRoutesSource, /data-(?:theme|skin)\s*=/i);
  assert.doesNotMatch(studioRoutesSource, /--(?:bg|canvas|surface|text|accent)\s*:/i);
});

test("main loads the explicit cascade through one design-system entry point", () => {
  const stylesheetImports = [
    ...mainSource.matchAll(/import\s+["']([^"']+\.css)["']\s*;/g),
  ].map(([, path]) => path);

  assert.deepEqual(stylesheetImports, ["./design-system/index.css"]);
  assert.match(tokensSource, /color-scheme\s*:\s*light\s*;/i);
  assert.match(designSystemIndex, /@import\s+"\.\/tokens\.css"\s+layer\(system\.tokens\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/studio-routes\.css"\s+layer\(system\.routes\)/);

  const declaredThemes = [
    ...tokensSource.matchAll(/data-(?:theme|skin)\s*=\s*["']([^"']+)["']/gi),
  ].map(([, id]) => id);
  assert.ok(declaredThemes.includes("mist"), "mist must have a light token override");
  assert.ok(declaredThemes.includes("warm"), "warm must have a light token override");
  assert.ok(
    declaredThemes.every((id) => EXPECTED_SKINS.includes(id)),
    `theme CSS contains a value outside the light allowlist: ${declaredThemes.join(", ")}`,
  );

  assert.doesNotMatch(tokensSource, /data-(?:theme|skin)\s*=\s*["']dark["']/i);
  assert.doesNotMatch(tokensSource, /prefers-color-scheme\s*:\s*dark/i);
  assert.doesNotMatch(tokensSource, /color-scheme\s*:\s*dark/i);
});
