import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "src", "App.jsx"), "utf8");
const composerCss = fs.readFileSync(
  path.join(root, "src", "design-system", "composer.css"),
  "utf8",
);
const shellCss = fs.readFileSync(
  path.join(root, "src", "design-system", "shells.css"),
  "utf8",
);
const mobileCss = fs.readFileSync(
  path.join(root, "src", "design-system", "mobile-studio.css"),
  "utf8",
);

const mediaGroupSource = appSource.match(
  /function MediaInputGroup\([\s\S]*?function MediaLibrary/,
)?.[0] ?? "";

test("media capacity uses one real add trigger without removing upload behavior", () => {
  assert.ok(mediaGroupSource);
  assert.doesNotMatch(mediaGroupSource, /emptySlots/);
  assert.doesNotMatch(mediaGroupSource, /Array\.from\(\{\s*length:/);
  assert.match(mediaGroupSource, /displayedFiles\.map\(\(file\)\s*=>/);
  assert.match(mediaGroupSource, /onClick=\{\(\)\s*=>\s*onRemove\(file\)\}/);
  assert.match(mediaGroupSource, /files\.length\s*<\s*limit/);
  assert.match(mediaGroupSource, /inputRef\.current\?\.click\(\)/);
  assert.match(mediaGroupSource, /multiple=\{limit\s*>\s*1\}/);
  assert.match(mediaGroupSource, /uploadDisabled/);
  assert.match(mediaGroupSource, /className="media-slot media-slot-add"/);
  assert.match(mediaGroupSource, /files\.length\s*>\s*0\s*\?\s*"继续添加"\s*:\s*"添加"/);
});

test("expanded composer removes only redundant controls and retains every capability field", () => {
  assert.match(
    appSource,
    /!composerExpanded\s*&&\s*\([\s\S]*?className="composer-add-media"[\s\S]*?setComposerExpanded\(true\)/,
  );
  assert.match(appSource, /id="model"/);
  assert.match(appSource, /id="generation-mode"/);
  assert.match(appSource, /selectGenerationMode\(event\.target\.value\)/);
  assert.match(appSource, /label="参考图"/);
  assert.match(appSource, /label="参考视频"/);
  assert.match(appSource, /label="参考音频"/);
  assert.match(appSource, /setRatio\(event\.target\.value\)/);
  assert.match(appSource, /setResolution\(event\.target\.value\)/);
  assert.match(appSource, /setDuration\(Number\(event\.target\.value\)\)/);
  assert.match(appSource, /setOutputCount\(Number\(event\.target\.value\)\)/);
  assert.match(appSource, /onClick=\{startGeneration\}/);
});

test("expanded composer groups media and parameters into responsive compact bands", () => {
  assert.match(
    composerCss,
    /\.community-composer\.is-expanded\s+\.composer-mode-rail\s*\{\s*display:\s*none;/,
  );
  assert.match(
    composerCss,
    /\.media-groups\s*\{[\s\S]*?repeat\(auto-fit,\s*minmax\((?:200|220)px,\s*1fr\)\)/,
  );
  assert.match(
    composerCss,
    /\.compact-fields\s*\{[\s\S]*?repeat\(4,\s*minmax\(0,\s*1fr\)\)/,
  );
  assert.match(
    mobileCss,
    /@media\s*\(max-width:\s*720px\)[\s\S]*?\.media-groups\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\)/,
  );
  assert.match(
    mobileCss,
    /@media\s*\(max-width:\s*720px\)[\s\S]*?\.compact-fields,[\s\S]*?repeat\(2,\s*minmax\(0,\s*1fr\)\)/,
  );
  assert.match(mobileCss, /min-height:\s*44px/);
});

test("composer and live task state reserve one layout-participating workbench", () => {
  assert.match(shellCss, /--studio-composer-expanded-size:\s*590px/);
  assert.match(
    composerCss,
    /\.app-shell\.is-community-home\s*\{[\s\S]*?grid-template-rows:\s*var\(--shell-topbar-size\)\s+minmax\(0,\s*1fr\)\s+auto/,
  );
  assert.match(
    composerCss,
    /\.app-shell\.is-community-home:not\(\.is-creation-hub\)\s*>\s*\.community-composer\s*\{[\s\S]*?position:\s*relative;[\s\S]*?grid-row:\s*3;/,
  );
  assert.match(
    composerCss,
    /\.app-shell\.is-community-home\s*>\s*\.taskbar\s*\{[\s\S]*?position:\s*relative;[\s\S]*?grid-row:\s*3;/,
  );
  assert.match(
    mobileCss,
    /\.app-shell\[data-theme\]\.is-community-home\s*\{[\s\S]*?grid-template-rows:\s*var\(--shell-topbar-size\)\s+minmax\(0,\s*1fr\)\s+auto\s+auto;/,
  );
});
