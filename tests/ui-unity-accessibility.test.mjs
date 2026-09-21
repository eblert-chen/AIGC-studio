import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const editorSource = await readFile(new URL("../src/components/GenerationEditor.jsx", import.meta.url), "utf8");
const uiSource = `${appSource}\n${editorSource}`;
const communitySource = await readFile(new URL("../src/CommunityHome.jsx", import.meta.url), "utf8");
const shellsCss = await readFile(new URL("../src/design-system/shells.css", import.meta.url), "utf8");
const controlsCss = await readFile(new URL("../src/design-system/controls.css", import.meta.url), "utf8");
const chromeCss = await readFile(new URL("../src/design-system/chrome.css", import.meta.url), "utf8");
const studioRoutesCss = await readFile(new URL("../src/design-system/studio-routes.css", import.meta.url), "utf8");
const composerCss = await readFile(new URL("../src/design-system/composer.css", import.meta.url), "utf8");
const mobileStudioCss = await readFile(new URL("../src/design-system/mobile-studio.css", import.meta.url), "utf8");
const mobileManagementCss = await readFile(new URL("../src/design-system/mobile-management.css", import.meta.url), "utf8");

test("Studio 手机模块栏进入壳层网格并横向滚动，不挤压触控目标", () => {
  assert.match(
    shellsCss,
    /@media \(max-width:\s*900px\)[\s\S]*?\.app-shell\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);[^}]*grid-template-rows:\s*var\(--shell-topbar-size\) var\(--studio-mobile-nav-height, 50px\) minmax\(0, 1fr\);[\s\S]*?\.app-shell > \.side-nav\s*\{[^}]*grid-row:\s*2;/,
  );
  assert.match(
    mobileStudioCss,
    /\.app-shell\[data-theme\] > \.side-nav \.side-nav-track\s*\{[^}]*overflow-x:\s*auto;[^}]*overscroll-behavior-inline:\s*contain;[^}]*scrollbar-width:\s*none;/,
  );
  assert.match(mobileStudioCss, /\.app-shell\[data-theme\] > \.side-nav button\s*\{[^}]*min-height:\s*44px;/s);
  assert.match(appSource, /className="side-nav-footer"[\s\S]*?onClick=\{\(\) => navigateStudio\("settings"\)\}[\s\S]*?<span>设置<\/span>/);
  assert.match(appSource, /className="side-nav-scroll-forward"[\s\S]*?track\.scrollTo/);
});

test("共享换肤控件保留清晰的键盘焦点", () => {
  assert.match(
    controlsCss,
    /:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--control-focus-ring\);[^}]*outline-offset:\s*2px;/s,
  );
  assert.match(controlsCss, /\.demo-account-switcher:focus-within\s*\{[^}]*border-color:\s*var\(--control-border-color\);[^}]*background:\s*var\(--control-surface-hover\);/s);
  assert.match(controlsCss, /\.skin-switcher-trigger:focus-visible,[\s\S]*?\.skin-switcher-option:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--control-focus-ring/);
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

  assert.match(uiSource, /onKeyDown=\{handleComposerMediaKeyDown\}/);
  assert.match(uiSource, /aria-controls="composer-parameters-panel"/);
  assert.match(uiSource, /tabIndex=\{composerMediaKind === "video" \? 0 : -1\}/);
  assert.match(uiSource, /tabIndex=\{composerMediaKind === "image" \? 0 : -1\}/);
  assert.match(uiSource, /id="composer-parameters-panel"[\s\S]*?role=\{isQuick \? "tabpanel" : "group"\}/);
  assert.match(uiSource, /aria-labelledby=\{isQuick && activeCapability \? `composer-media-tab-\$\{composerMediaKind\}` : undefined\}/,
    "contextual editors must not reference media tabs that only exist in quick creation");
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

test("Studio 桌面生成器参与主工作区网格而不覆盖画布", () => {
  assert.match(
    shellsCss,
    /\.app-shell\s*\{[^}]*grid-template-columns:\s*var\(--shell-studio-rail-size\) minmax\(0, 1fr\);[\s\S]*?\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*3;/,
  );
  assert.match(
    shellsCss,
    /@media \(max-width:\s*1180px\)[\s\S]*?--shell-studio-rail-size:\s*144px;/,
  );
  assert.match(
    composerCss,
    /\.community-composer\s*\{[^}]*position:\s*relative;[^}]*display:\s*grid;[^}]*overflow:\s*hidden;/s,
  );
});

test("创作页桌面生成器进入专属第三行 dock 而不覆盖内容", () => {
  assert.match(
    appSource,
    /activeNav === "create" \? "is-creation-hub" : ""/,
  );

  assert.match(
    shellsCss,
    /\.app-shell\.is-community-home\s*\{[^}]*grid-template-rows:\s*var\(--shell-topbar-size\) minmax\(0, 1fr\) auto;/s,
  );
  assert.match(
    shellsCss,
    /\.app-shell\.is-community-home > \.main-canvas\s*\{[^}]*grid-row:\s*2;[\s\S]*?\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*3;/,
  );
  assert.match(
    composerCss,
    /\.community-composer\s*\{[^}]*position:\s*relative;[^}]*display:\s*grid;[^}]*overflow:\s*hidden;/s,
  );
});

test("首页与默认创作共享流内生成器，高级工作台不继承全局底栏与手机启动器", () => {
  assert.match(
    composerCss,
    /\.community-composer\s*\{[^}]*position:\s*relative;/s,
  );
  assert.match(
    appSource,
    /const isPrimaryStudioView = activeNav === "shots" \|\| activeNav === "create"/,
  );
  assert.match(
    appSource,
    /className=\{`app-shell \$\{isQuickStudioView \? "is-community-home" : isAdvancedWorkbench \? "is-advanced-workbench" : "is-secondary-page"\} \$\{activeNav === "create" \? "is-creation-hub" : ""\} \$\{isQuickStudioView && composerExpanded \? "is-composer-expanded" : ""\} \$\{isQuickStudioView && mobileComposerOpen \? "is-mobile-composer-open" : ""\}`\}/,
  );
  assert.match(
    appSource,
    /const isQuickStudioView = isPrimaryStudioView && !isAdvancedWorkbench/,
  );
  assert.equal(appSource.match(/\{isQuickStudioView && renderGenerationEditor\(\)\}/g)?.length, 1);
  assert.equal(appSource.match(/renderEditor=\{renderGenerationEditor\}/g)?.length, 1);
  assert.equal(uiSource.match(/data-editor-variant=\{variant\}/g)?.length, 1);
  assert.match(uiSource, /data-ui=\{isQuick \? "director-composer" : "workbench-editor"\}/);
  assert.match(uiSource, /\{isQuick && <>\s*<button\s*id="mobile-composer-launcher"/);
  assert.doesNotMatch(appSource, /\{isPrimaryStudioView && \(\s*<aside/);
});

test("移动创作页生成器也不会覆盖末尾业务内容", () => {
  assert.match(
    shellsCss,
    /@media \(max-width:\s*900px\)[\s\S]*?\.app-shell\.is-community-home\s*\{[^}]*grid-template-rows:\s*var\(--shell-topbar-size\) var\(--studio-mobile-nav-height, 50px\) minmax\(0, 1fr\) auto;[\s\S]*?\.app-shell\.is-community-home > \.main-canvas\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*3;[\s\S]*?\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*4;/,
  );
  assert.match(
    composerCss,
    /@media \(max-width: 900px\)[\s\S]*?\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*grid-column:\s*1;[^}]*margin:\s*0 12px 12px;/s,
  );
  assert.doesNotMatch(composerCss, /position:\s*fixed/);
});

test("Studio 移动导航与历史数据使用共享 12px 可读下限", () => {
  assert.match(
    chromeCss,
    /@media \(max-width:\s*900px\)[\s\S]*?\.app-shell > \.side-nav button span\s*\{[^}]*font-size:\s*13px;/,
  );
  assert.doesNotMatch(`${chromeCss}\n${mobileStudioCss}\n${studioRoutesCss}`, /font-size:\s*(?:8|9|10|11)px;/);

  assert.match(
    studioRoutesCss,
    /\.history-toolbar label,[\s\S]*?\.artwork-toolbar label\s*\{[^}]*font-size:\s*12px;/s,
  );
  assert.match(
    studioRoutesCss,
    /\.task-history-row > footer\s*\{[^}]*font-size:\s*12px;/s,
  );
});

test("公司移动模块栏可横向到达并只用一个柔和填充表达选中状态", () => {
  assert.match(
    mobileManagementCss,
    /@media \(max-width:\s*720px\)[\s\S]*?\.control-shell > \.control-sidebar\s*\{[^}]*overflow-x:\s*auto;[^}]*scroll-padding-inline:\s*12px;[^}]*scrollbar-width:\s*none;/,
  );
  assert.match(mobileManagementCss, /\.control-shell \.control-sidebar nav\s*\{[^}]*width:\s*max-content;[^}]*min-width:\s*100%;/s);
  assert.match(mobileManagementCss, /\.control-shell \.control-sidebar nav button\s*\{[^}]*min-width:\s*max-content;[^}]*min-height:\s*44px;[^}]*border-radius:\s*var\(--radius-xs, 8px\);/s);
  assert.match(mobileManagementCss, /\.control-shell \.control-sidebar nav button\.is-active\s*\{[^}]*color:\s*var\(--management-cobalt-strong\);[^}]*background:\s*var\(--management-cobalt-soft\);/s);
  assert.doesNotMatch(mobileManagementCss, /\.control-shell \.control-sidebar nav button\.is-active::before/);
});
