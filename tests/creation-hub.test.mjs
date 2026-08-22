import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const hubSource = await readFile(new URL("../src/CreationHub.jsx", import.meta.url), "utf8");
const hubStyles = await readFile(new URL("../src/creation-hub.css", import.meta.url), "utf8");

test("创作页复用现有任务、模型和提交控制器", () => {
  assert.match(appSource, /<CreationHub/);
  assert.match(appSource, /tasks=\{LIVE_MODE \? historyTasks : DEMO_HISTORY_TASKS\}/);
  assert.match(appSource, /models=\{models\}/);
  assert.match(appSource, /onOpenTask=\{openHistoryTask\}/);
  assert.match(appSource, /\["create", "history"\]\.includes\(activeNav\)/);
});

test("创作页提供可交互的媒体、筛选、视图和空状态", () => {
  assert.match(hubSource, /role="tablist" aria-label="创作内容类型"/);
  assert.match(hubSource, /最近 30 天/);
  assert.match(hubSource, /全部模型/);
  assert.match(hubSource, /分组方式：无/);
  assert.match(
    hubSource,
    /media === "saved" \? "还没有已保存内容" : `未找到\$\{emptyName\}`/,
  );
  assert.match(hubSource, /调整筛选条件，或开始创建您的第一个\$\{emptyName\}/);
  assert.match(hubSource, /当前 Platform 尚未开放音频生成任务/);
  assert.match(hubSource, /快应用创作尚未接入当前 Platform 能力合同/);
  assert.match(hubSource, /disabled=\{Boolean\(unavailableReason\)\}/);
});

test("创作页在全部数据状态下保留稳定标题和有效区域名称", () => {
  const headingIndex = hubSource.indexOf('<h1 id="creation-hub-title">创作</h1>');
  const stateBranchIndex = hubSource.indexOf("{loading ? (");

  assert.ok(headingIndex >= 0, "应始终渲染创作页主标题");
  assert.ok(headingIndex < stateBranchIndex, "主标题不能放进加载、错误或空态分支");
  assert.match(hubSource, /aria-labelledby="creation-hub-title"/);
  assert.match(hubSource, /id="creation-hub-panel"[\s\S]*role="tabpanel"[\s\S]*aria-labelledby=\{`creation-media-tab-\$\{media\}`\}/);
  assert.match(hubSource, /id=\{`creation-media-tab-\$\{id\}`\}/);
  assert.match(hubSource, /tabIndex=\{media === id \? 0 : -1\}/);
  assert.match(hubSource, /event\.key === "ArrowRight"/);
  assert.match(hubSource, /event\.key === "ArrowLeft"/);
  assert.match(hubSource, /event\.key === "Home"/);
  assert.match(hubSource, /event\.key === "End"/);
});

test("创作页使用独立作用域样式，不污染管理后台", () => {
  assert.match(hubStyles, /^\.creation-hub\s*\{/m);
  assert.match(hubStyles, /\.creation-media-tabs button\.is-active::after/);
  assert.match(hubStyles, /\.creation-task-grid\.is-list/);
  assert.doesNotMatch(hubStyles, /:root\s*\{/);
});

test("创作页移动端标签使用容器宽度并保留可触控控件", () => {
  const mobileStart = hubStyles.indexOf("@media (max-width: 900px)");
  const mobileEnd = hubStyles.indexOf("@media (max-width: 620px)");
  const mobileStyles = hubStyles.slice(mobileStart, mobileEnd);

  assert.ok(mobileStart >= 0 && mobileEnd > mobileStart);
  assert.match(mobileStyles, /\.creation-media-tabs\s*\{[\s\S]*?width:\s*100%;[\s\S]*?max-width:\s*100%;/);
  assert.match(mobileStyles, /min-height:\s*44px;/);
  assert.doesNotMatch(mobileStyles, /calc\(100vw\s*-\s*80px\)/);
  assert.match(hubStyles, /white-space:\s*nowrap;/);
  assert.match(hubStyles, /word-break:\s*keep-all;/);
});

test("创作内容类型与能力驱动生成器保持同步并保留分页", () => {
  assert.match(appSource, /generationMediaKind=\{composerMediaKind\}/);
  assert.match(appSource, /onGenerationMediaChange=\{selectComposerMediaKind\}/);
  assert.match(appSource, /models\.find\(\(item\) => modelSupportsMediaKind\(item, nextKind\)\)/);
  assert.match(hubSource, /onGenerationMediaChange\?\.\(nextMedia\) === false/);
  assert.match(hubSource, /aria-label="创作记录分页"/);
  assert.match(hubSource, /onPageChange\?\.\(page \+ 1\)/);
  assert.match(appSource, /media_type: activeNav === "create" \? creationMediaType : ""/);
  assert.match(appSource, /query: !isPersonalWorkspace && activeNav === "create" \? creationQuery\.trim\(\) : ""/);
  assert.match(appSource, /supportsSearch=\{!isPersonalWorkspace\}/);
  assert.match(appSource, /supportsDateFilter=\{!isPersonalWorkspace\}/);
  assert.match(hubSource, /onMediaFilterChange\?\./);
  assert.match(hubSource, /onMediaFilterChange\?\.\(generationMediaKind\)/);
  assert.match(hubSource, /queryChangeRef\.current\?\.\(query\)/);
});

test("创作卡复用统一费用语义且不暴露没有实现的伪操作", () => {
  assert.match(hubSource, /import \{ taskCostLabel \} from "\.\/taskArtifacts\.js"/);
  assert.match(hubSource, /\{taskCostLabel\(task\)\}/);
  assert.doesNotMatch(hubSource, /function costLabel/);
  assert.match(hubSource, /title="上传记录请前往素材页查看"/);
  assert.match(hubSource, /title="批量操作尚未开放"/);
});

test("创作卡和布局切换向键盘与读屏暴露可区分状态", () => {
  assert.match(hubSource, /aria-label=\{`查看创作记录：\$\{prompt\}`\}/);
  assert.match(hubSource, /aria-pressed=\{layout === "grid"\}/);
  assert.match(hubSource, /aria-pressed=\{layout === "compact"\}/);
  assert.match(hubSource, /aria-pressed=\{layout === "list"\}/);
});

test("真实创作卡通过独立安全预览契约加载缩略图", () => {
  assert.match(appSource, /previewUrls=\{artworkPreviewUrls\}/);
  assert.match(appSource, /previewActionKey=\{artifactActionKey\}/);
  assert.match(appSource, /onRequestPreview=\{\(task, artifact\) => accessArtifact\(artifact,/);
  assert.match(hubSource, /previewUrl=\{activePreviewUrl\(previewUrls\[previewKey\]\)\}/);
  assert.match(hubSource, /previewLoading=\{previewActionKey === `preview:\$\{previewKey\}`\}/);
  assert.match(hubSource, /onPreviewError=\{\(\) => onPreviewError\?\.\(previewKey\)\}/);
  assert.match(hubSource, /previewLoading \? "正在加载" : "加载预览"/);
  assert.match(hubSource, /liveMode && media === "video" && previewUrl/);
  assert.match(hubSource, /<video[\s\S]*?muted[\s\S]*?playsInline[\s\S]*?preload="metadata"/);
});
