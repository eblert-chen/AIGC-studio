import test from "node:test";
import assert from "node:assert/strict";
import { access, readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { COMMUNITY_FALLBACK_FEED } from "../src/communityFeed.js";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("社区首页接在原客户平台控制器上而不是另起假生成流程", async () => {
  const source = await readFile(path.join(projectRoot, "src", "App.jsx"), "utf8");
  const editorSource = await readFile(path.join(projectRoot, "src", "components", "GenerationEditor.jsx"), "utf8");
  const uiSource = `${source}\n${editorSource}`;
  assert.match(source, /<CommunityHome/);
  assert.match(uiSource, /onClick=\{startGeneration\}/);
  assert.match(uiSource, /selectStudioModel\(item\.id\)/);
  assert.match(uiSource, /selectGenerationMode\(item\)/);
  assert.match(source, /const isPrimaryStudioView = activeNav === "shots" \|\| activeNav === "create"/);
  assert.match(source, /const isAdvancedWorkbench = activeNav === "create" && ADVANCED_WORKBENCHES\.includes\(creationWorkbench\)/);
  assert.match(source, /const isQuickStudioView = isPrimaryStudioView && !isAdvancedWorkbench/);
  assert.match(source, /isQuickStudioView \? "is-community-home" : isAdvancedWorkbench \? "is-advanced-workbench" : "is-secondary-page"/);
  assert.match(source, /isQuickStudioView && mobileComposerOpen \? "is-mobile-composer-open" : ""/);
  assert.match(source, /const focusComposerPrompt = \(\) => \{[\s\S]*?requestAnimationFrame\(\(\) => \{[\s\S]*?requestAnimationFrame\(focusPrompt\)/);
});

test("首页把示例内容就地说明且不暴露后台实现", async () => {
  const source = await readFile(path.join(projectRoot, "src", "CommunityHome.jsx"), "utf8");
  assert.match(source, /示例内容，仅用于创作参考/);
  assert.match(source, /示例内容，不会生成真实任务或作品/);
  assert.match(source, /看看社区在创作什么，喜欢就直接开场/);
  assert.doesNotMatch(source, /点赞|粉丝|关注/);
  assert.doesNotMatch(source, /内容来自 Platform|真实画面证据|当前可查看 \{visibleDirectionCount\}/);
});

test("首页分别说明筛选无结果与首次空内容", async () => {
  const source = await readFile(path.join(projectRoot, "src", "CommunityHome.jsx"), "utf8");
  assert.match(source, /没有符合当前筛选的灵感/);
  assert.match(source, /暂无\$\{activeSection\}灵感/);
  assert.match(source, /categoryFiltered \? "查看全部" : "开始创作"/);
});

test("首页使用单一页面标题、连续筛选工具条和无装饰编号的媒体舞台", async () => {
  const source = await readFile(path.join(projectRoot, "src", "CommunityHome.jsx"), "utf8");
  const appSource = await readFile(path.join(projectRoot, "src", "App.jsx"), "utf8");
  const editorSource = await readFile(path.join(projectRoot, "src", "components", "GenerationEditor.jsx"), "utf8");
  const uiSource = `${appSource}\n${editorSource}`;
  const css = await readFile(path.join(projectRoot, "src", "design-system", "home.css"), "utf8");

  assert.match(source, /<h1 id="community-title">首页<\/h1>/);
  assert.match(source, /className="community-featured-grid"/);
  assert.match(source, /className="community-toolbar"/);
  assert.match(source, /className="community-feed-grid"/);
  assert.match(source, /className="community-card-media"/);
  assert.match(source, /onClick=\{onFocusComposer\}/);
  assert.match(source, /onClick=\{\(\) => onUsePrompt\(item\.prompt\)\}/);
  assert.doesNotMatch(source, /community-card-badge|community-hero-index/);

  assert.match(css, /\.community-feed-grid\s*\{[\s\S]*?columns:\s*3;/);
  assert.match(css, /\.community-heading\s*\{[\s\S]*?display:\s*flex;[\s\S]*?align-items:\s*center;/);
  assert.match(css, /\.community-gallery\s*\{[\s\S]*?display:\s*block;/);
  assert.match(css, /\.community-card-caption\s*\{/);
  assert.equal(appSource.match(/isQuickStudioView && renderGenerationEditor\(\)/g)?.length, 1);
  assert.equal(appSource.match(/function renderGenerationEditor\(/g)?.length, 1);
  assert.match(uiSource, /const isQuick = variant === "quick"/);
  assert.match(uiSource, /data-ui=\{isQuick \? "director-composer" : "workbench-editor"\}/);
  assert.match(uiSource, /\{isQuick && <>\s*<button\s*id="mobile-composer-launcher"/);
  assert.doesNotMatch(appSource, /isPrimaryStudioView && \(\s*<aside/);
  assert.doesNotMatch(css, /radial-gradient\(|!important/);
  assert.doesNotMatch(css, /font(?:-size)?\s*:[^;\n]*\b(?:[0-9]|1[01])px\b/);
});

test("社区视觉使用项目内真实生成素材", async () => {
  const requiredAssets = [COMMUNITY_FALLBACK_FEED.hero, ...COMMUNITY_FALLBACK_FEED.items]
    .map((item) => item.mediaUrl || item.image)
    .filter((url) => url.startsWith("/"))
    .map((url) => `public${url}`);
  assert.equal(new Set(requiredAssets).size, requiredAssets.length);
  await Promise.all(requiredAssets.map((relativePath) => access(path.join(projectRoot, relativePath))));
  const webpAssets = requiredAssets.filter((relativePath) => relativePath.endsWith(".webp"));
  const metadata = await Promise.all(
    webpAssets.map((relativePath) => stat(path.join(projectRoot, relativePath))),
  );
  assert.ok(metadata.every((entry) => entry.size > 0 && entry.size <= 500_000));
});

test("首页为高图和手机分类控件保留稳定几何与触控尺寸", async () => {
  const css = await readFile(path.join(projectRoot, "src", "design-system", "home.css"), "utf8");
  assert.match(css, /\.community-card:not\(\.is-featured\)\.is-tall \.community-card-media\s*\{[\s\S]*?aspect-ratio:\s*2 \/ 3;/);
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*?\.community-category-tabs button\s*\{[\s\S]*?min-height:\s*var\(--control-lg\);/);
});

test("在素材库选择演示素材后不会自动跳回首页", async () => {
  const source = await readFile(path.join(projectRoot, "src", "App.jsx"), "utf8");
  const mediaView = source.slice(
    source.indexOf('if (activeNav === "media")'),
    source.indexOf('if (activeNav === "history")'),
  );

  assert.match(mediaView, /onUse=\{\(id\) => \{\s*chooseScene\(id\);/);
  assert.match(source, /用于当前创作/);
  assert.doesNotMatch(mediaView, /setActiveNav\("shots"\)/);
});

test("历史与作品的复用动作只恢复草稿而不直接提交", async () => {
  const source = await readFile(path.join(projectRoot, "src", "App.jsx"), "utf8");
  assert.match(
    source,
    /const retryHistoryTask = \(task\) => prepareHistoricalTask\(task, \{\s*expanded: true,\s*actionLabel: "恢复失败任务草稿"/,
  );
  assert.match(
    source,
    /const createAgainFromTask = \(task\) => prepareHistoricalTask\(task, \{\s*expanded: false,\s*actionLabel: "用此设置新建草稿"/,
  );
  assert.match(source, /onRetry=\{LIVE_MODE \? retryHistoryTask : retryGeneration\}/);
  assert.match(source, /onCreateAgain=\{createAgainFromTask\}/);
  assert.match(source, /navigateStudio\("create"\)/);
});
