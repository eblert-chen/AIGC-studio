import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const communitySource = await readFile(new URL("../src/CommunityHome.jsx", import.meta.url), "utf8");
const communityCss = await readFile(new URL("../src/community.css", import.meta.url), "utf8");
const lightTheme = await readFile(new URL("../src/light-theme.css", import.meta.url), "utf8");
const shellsCss = await readFile(new URL("../src/design-system/shells.css", import.meta.url), "utf8");

function matchingRuleBodies(css, selectorFragment) {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, selector]) => selector.includes(selectorFragment))
    .map(([, , body]) => body);
}

function splitGridTracks(value) {
  const tracks = [];
  let current = "";
  let depth = 0;
  for (const character of value.trim()) {
    if (character === "(") depth += 1;
    if (character === ")") depth -= 1;
    if (/\s/.test(character) && depth === 0) {
      if (current) tracks.push(current);
      current = "";
    } else {
      current += character;
    }
  }
  if (current) tracks.push(current);
  return tracks;
}

function usesInFlowCreationDock(css) {
  const shellRules = matchingRuleBodies(css, ".app-shell.is-creation-hub");
  const canvasRules = matchingRuleBodies(css, ".is-creation-hub .main-canvas");
  const composerRules = matchingRuleBodies(css, ".is-creation-hub .community-composer");

  const hasThreeRows = shellRules.some((body) => {
    const declaration = body.match(/grid-template-rows:\s*([^;]+);/i);
    return declaration && splitGridTracks(declaration[1]).length === 3;
  });
  const mainUsesContentRow = canvasRules.some((body) => /grid-row:\s*2(?:\s*\/\s*3)?\s*;/i.test(body));
  const composerUsesDockRow = composerRules.some((body) => (
    /position:\s*(?:static|relative|sticky)\s*;/i.test(body)
    && /grid-row:\s*3(?:\s*\/\s*4)?\s*;/i.test(body)
    && !/position:\s*(?:fixed|absolute)\s*;/i.test(body)
  ));

  return hasThreeRows && mainUsesContentRow && composerUsesDockRow;
}

test("Studio 手机导航固定在底部且不会继承旧版 top 定位", () => {
  const mobileStart = lightTheme.indexOf("@media (max-width: 900px)");
  const mobileEnd = lightTheme.indexOf("@media (max-width: 620px)", mobileStart);
  const mobileStyles = lightTheme.slice(mobileStart, mobileEnd);

  assert.ok(mobileStart >= 0 && mobileEnd > mobileStart);
  assert.match(mobileStyles, /\.app-shell\.is-secondary-page \.side-nav\s*\{[\s\S]*?position:\s*fixed;[\s\S]*?top:\s*auto;[\s\S]*?bottom:\s*0;/);
  assert.match(mobileStyles, /height:\s*100dvh;[\s\S]*?min-height:\s*0;[\s\S]*?padding-bottom:\s*64px;/);
});

test("共享换肤控件保留清晰的键盘焦点", () => {
  assert.match(lightTheme, /\.skin-switcher:focus-within\s*\{[\s\S]*?outline:\s*2px solid var\(--accent\);[\s\S]*?outline-offset:\s*2px;/);
});

test("产物对话框支持焦点进入、循环、Escape 和关闭后还原", () => {
  assert.match(appSource, /const resultDialogRef = useRef\(null\)/);
  assert.match(appSource, /const resultReturnFocusRef = useRef\(null\)/);
  assert.match(appSource, /event\.key === "Escape"/);
  assert.match(appSource, /event\.key !== "Tab"/);
  assert.match(appSource, /!dialog\.contains\(focused\)/);
  assert.match(appSource, /element\.getClientRects\(\)\.length > 0/);
  assert.match(appSource, /returnTarget\.focus\(\{ preventScroll: true \}\)/);
  assert.match(appSource, /taskbar\.focus\(\{ preventScroll: true \}\)/);
  assert.match(appSource, /resultReturnFocusRef\.current = event\.currentTarget/);
  assert.match(appSource, /body\.style\.overflow = "hidden"/);
  assert.match(appSource, /ref=\{resultDialogRef\}[\s\S]*?role="dialog"[\s\S]*?aria-modal="true"[\s\S]*?tabIndex=\{-1\}/);
});

test("首页和生成器标签支持 roving tabindex 与方向键", () => {
  assert.match(communitySource, /tabIndex=\{activeSection === section \? 0 : -1\}/);
  assert.match(communitySource, /event\.key === "ArrowRight"/);
  assert.match(communitySource, /event\.key === "ArrowLeft"/);
  assert.match(communitySource, /role="tabpanel"/);

  assert.match(appSource, /onKeyDown=\{handleComposerMediaKeyDown\}/);
  assert.match(appSource, /aria-controls="composer-parameters-panel"/);
  assert.match(appSource, /id="composer-parameters-panel"[\s\S]*?role="tabpanel"/);
});

test("Studio 导航切换只重置主画布滚动位置", () => {
  assert.match(appSource, /import \{[^}]*useLayoutEffect[^}]*\} from "react"/);
  assert.match(appSource, /const mainCanvasRef = useRef\(null\)/);
  assert.match(appSource, /useLayoutEffect\(\(\) => \{[\s\S]*?const canvas = mainCanvasRef\.current;[\s\S]*?if \(!canvas\) return;[\s\S]*?canvas\.scrollTop = 0;[\s\S]*?canvas\.scrollLeft = 0;[\s\S]*?\}, \[activeNav\]\)/);
  assert.match(appSource, /<main ref=\{mainCanvasRef\} className="main-canvas">/);

  const resetStart = appSource.indexOf("useLayoutEffect(() => {");
  const resetEnd = appSource.indexOf("}, [activeNav]);", resetStart);
  const resetContract = appSource.slice(resetStart, resetEnd);
  assert.ok(resetStart >= 0 && resetEnd > resetStart);
  assert.doesNotMatch(resetContract, /creation-hub|querySelector|scrollIntoView/);
});

test("程序化文件选择器不会产生不可见的 Tab 停点", () => {
  assert.match(appSource, /const mediaInputId = `media-input-\$\{kind\}`/);
  assert.match(appSource, /<label htmlFor=\{mediaInputId\}>\{label\}<\/label>/);
  assert.match(appSource, /id=\{mediaInputId\}[\s\S]*?type="file"[\s\S]*?tabIndex=\{-1\}[\s\S]*?aria-label=\{`\$\{label\}文件选择器`\}/);
  assert.match(appSource, /id="asset-library-upload-input"[\s\S]*?type="file"[\s\S]*?tabIndex=\{-1\}[\s\S]*?aria-label="上传素材文件选择器"/);
  assert.match(appSource, /onClick=\{\(\) => inputRef\.current\?\.click\(\)\}/);
});

test("Studio 桌面生成器按 80px 导航后的工作画布居中", () => {
  assert.match(
    lightTheme,
    /\.is-community-home \.community-composer,[\s\S]*?left:\s*calc\(80px \+ \(100vw - 80px\) \/ 2\);/,
  );
  assert.match(
    lightTheme,
    /@media \(max-width: 1180px\)[\s\S]*?left:\s*calc\(72px \+ \(100vw - 72px\) \/ 2\);/,
  );
});

test("创作页桌面生成器进入专属第三行 dock 而不覆盖内容", () => {
  assert.match(
    appSource,
    /activeNav === "create" \? "is-creation-hub" : ""/,
  );

  const mobileStart = lightTheme.indexOf("@media (max-width: 900px)");
  const desktopStyles = lightTheme.slice(0, mobileStart);

  assert.ok(mobileStart >= 0);
  assert.ok(
    usesInFlowCreationDock(desktopStyles),
    "desktop create must use three grid rows, keep main in row 2, and place an in-flow composer in row 3",
  );
  assert.match(
    shellsCss,
    /\.app-shell\.is-creation-hub > \.community-composer,[\s\S]*?inset-inline:\s*auto;/,
    "the in-flow dock must clear the retired fixed composer's logical offsets",
  );
});

test("首页生成器仍保持浮动且创作页专属规则不会泄漏到首页", () => {
  assert.match(
    communityCss,
    /\.is-community-home \.community-composer\s*\{[^}]*position:\s*fixed\s*;/s,
  );
  assert.match(
    appSource,
    /const isPrimaryStudioView = activeNav === "shots" \|\| activeNav === "create"/,
  );
  assert.match(
    appSource,
    /className=\{`app-shell \$\{isPrimaryStudioView \? "is-community-home" : "is-secondary-page"\} \$\{activeNav === "create" \? "is-creation-hub" : ""\} \$\{composerExpanded \? "is-composer-expanded" : ""\}`\}/,
  );
});

test("移动创作页生成器也不会覆盖末尾业务内容", () => {
  const mobileStart = lightTheme.indexOf("@media (max-width: 900px)");
  const mobileEnd = lightTheme.indexOf("@media (max-width: 720px)", mobileStart);
  const mobileStyles = lightTheme.slice(mobileStart, mobileEnd);

  assert.ok(mobileStart >= 0 && mobileEnd > mobileStart);
  assert.ok(
    usesInFlowCreationDock(mobileStyles),
    "mobile create must preserve the three-row in-flow dock instead of restoring a fixed overlay",
  );
});

test("Studio 移动导航与历史数据使用共享 12px 可读下限", () => {
  const compactStart = lightTheme.indexOf("@media (max-width: 390px)");
  const compactEnd = lightTheme.indexOf("@media (prefers-reduced-motion", compactStart);
  const compactStyles = lightTheme.slice(compactStart, compactEnd);

  assert.ok(compactStart >= 0 && compactEnd > compactStart);
  assert.match(
    compactStyles,
    /\.app-shell\.is-secondary-page \.side-nav button span\s*\{[^}]*font-size:\s*var\(--text-caption, 12px\);/s,
  );
  assert.doesNotMatch(compactStyles, /font-size:\s*(?:8|9|10)px;/);

  assert.match(
    lightTheme,
    /\.is-secondary-page \.history-toolbar label,[\s\S]*?\.is-secondary-page \.history-cost\s*\{[^}]*font-size:\s*var\(--text-caption, 12px\);/s,
  );
  assert.match(
    lightTheme,
    /\.is-secondary-page \.history-toolbar select,[\s\S]*?\.is-secondary-page \.task-history-row footer \.text-button\s*\{[^}]*font-size:\s*12px;/s,
  );
});

test("公司移动模块栏以细滚动条提示横向内容", () => {
  assert.match(
    lightTheme,
    /@media \(max-width: 720px\)[\s\S]*?\.control-shell \.control-sidebar\s*\{[^}]*scrollbar-width:\s*thin;[\s\S]*?\.control-shell \.control-sidebar::\-webkit-scrollbar\s*\{[^}]*display:\s*block;[^}]*height:\s*4px;/s,
  );
});
