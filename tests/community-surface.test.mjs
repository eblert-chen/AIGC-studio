import test from "node:test";
import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("社区首页接在原客户平台控制器上而不是另起假生成流程", async () => {
  const source = await readFile(path.join(projectRoot, "src", "App.jsx"), "utf8");
  assert.match(source, /<CommunityHome/);
  assert.match(source, /onClick=\{startGeneration\}/);
  assert.match(source, /selectStudioModel\(event\.target\.value\)/);
  assert.match(source, /selectGenerationMode\(event\.target\.value\)/);
  assert.match(source, /const isPrimaryStudioView = activeNav === "shots" \|\| activeNav === "create"/);
  assert.match(source, /isPrimaryStudioView \? "is-community-home" : "is-secondary-page"/);
});

test("社区卡片不会把示例包装成虚假公开用户数据", async () => {
  const source = await readFile(path.join(projectRoot, "src", "CommunityHome.jsx"), "utf8");
  assert.match(source, /灵感示例/);
  assert.match(source, /不代表真实社区数据/);
  assert.doesNotMatch(source, /点赞|粉丝|关注/);
});

test("首页使用单一页面标题、连续筛选工具条和无装饰编号的媒体舞台", async () => {
  const source = await readFile(path.join(projectRoot, "src", "CommunityHome.jsx"), "utf8");
  const css = await readFile(path.join(projectRoot, "src", "design-system", "home.css"), "utf8");

  assert.match(source, /<h1 id="community-title">创作灵感<\/h1>/);
  assert.match(source, /className="community-toolbar"/);
  assert.match(source, /className="community-feed-grid"/);
  assert.match(source, /className="community-card-media"/);
  assert.match(source, /onClick=\{onFocusComposer\}/);
  assert.match(source, /onClick=\{\(\) => onUsePrompt\(item\.prompt\)\}/);
  assert.doesNotMatch(source, /community-card-badge|community-hero-index/);

  assert.match(css, /\.community-feed-grid\s*\{[\s\S]*?grid-template-columns:\s*repeat\(12,/);
  assert.match(css, /\.community-card-caption\s*\{/);
  assert.match(css, /\.app-shell\.is-community-home:not\(\.is-creation-hub\) > \.community-composer/);
});

test("社区视觉使用项目内真实生成素材", async () => {
  const requiredAssets = [
    "public/community/community-hero.png",
    "public/community/cyber-fashion-portrait.png",
    "public/community/neon-anime-street.png",
    "public/community/miniature-fashion.png",
    "public/community/ice-landscape.png",
    "public/community/blue-editorial-portrait.png",
  ];
  await Promise.all(requiredAssets.map((relativePath) => access(path.join(projectRoot, relativePath))));
});

test("在素材库选择演示素材后不会自动跳回首页", async () => {
  const source = await readFile(path.join(projectRoot, "src", "App.jsx"), "utf8");
  const mediaView = source.slice(
    source.indexOf('if (activeNav === "media")'),
    source.indexOf('if (activeNav === "history")'),
  );

  assert.match(mediaView, /onUse=\{\(id\) => \{\s*chooseScene\(id\);/);
  assert.doesNotMatch(mediaView, /setActiveNav\("shots"\)/);
});
