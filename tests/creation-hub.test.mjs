import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { taskUserMessage } from "../src/taskStatus.js";
import { readCreationWorkbench } from "../src/app/useCreationWorkspaceSession.js";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const editorSource = await readFile(new URL("../src/components/GenerationEditor.jsx", import.meta.url), "utf8");
const uiSource = `${appSource}\n${editorSource}`;
const collectionsSource = await readFile(
  new URL("../src/app/useStudioCollections.js", import.meta.url),
  "utf8",
);
const routeStateSource = await readFile(
  new URL("../src/app/useStudioRouteState.js", import.meta.url),
  "utf8",
);
const hubSource = await readFile(new URL("../src/CreationHub.jsx", import.meta.url), "utf8");
const workbenchSource = await readFile(
  new URL("../src/pages/studio/CreationWorkbenchViews.jsx", import.meta.url),
  "utf8",
);
const lineageSource = await readFile(
  new URL("../src/pages/studio/LineageGraphWorkspace.jsx", import.meta.url),
  "utf8",
);
const hubStyles = await readFile(
  new URL("../src/design-system/studio-routes.css", import.meta.url),
  "utf8",
);
const mobileStudioStyles = await readFile(
  new URL("../src/design-system/mobile-studio.css", import.meta.url),
  "utf8",
);
const workbenchStyles = await readFile(
  new URL("../src/design-system/workbenches.css", import.meta.url),
  "utf8",
);
const designSystemStyles = await readFile(
  new URL("../src/design-system/index.css", import.meta.url),
  "utf8",
);
const creationSource = `${hubSource}\n${workbenchSource}\n${lineageSource}`;

test("创作页复用现有任务、模型和提交控制器", () => {
  assert.match(appSource, /<CreationHub/);
  assert.match(appSource, /tasks=\{LIVE_MODE \? historyTasks : DEMO_HISTORY_TASKS\}/);
  assert.match(appSource, /models=\{models\}/);
  assert.match(appSource, /onOpenTask=\{openHistoryTask\}/);
  assert.match(appSource, /onContinueTask=\{createAgainFromTask\}/);
  assert.match(appSource, /onAdjust=\{adjustHistoricalTask\}/);
  assert.match(collectionsSource, /\["create", "history"\]\.includes\(activeNav\)/);
});

test("创作默认入口提供三个平级工作方式，普通进入与明确带稿分开", () => {
  assert.match(hubSource, /data-creation-workbench=\{workbench\}/);
  assert.match(hubSource, /workbench === "console"[\s\S]*?<DirectorConsoleView/);
  assert.match(hubSource, /workbench === "notebook"[\s\S]*?<DirectorNotebookView/);
  assert.match(hubSource, /workbench === "canvas"[\s\S]*?<LineageCanvasView/);
  assert.match(hubSource, /<CreationEntryView/);
  assert.match(workbenchSource, /data-ui="creation-invitation-entry"/);
  assert.match(workbenchSource, /<h2 id="creation-entry-title">今天想创作什么？<\/h2>/);
  assert.match(workbenchSource, /id: "console"[\s\S]*?id: "notebook"[\s\S]*?id: "canvas"/);
  assert.match(workbenchSource, /data-workbench=\{id\}/);
  assert.match(workbenchSource, /className="wb-entry-ways"[\s\S]*?工作方式[\s\S]*?PATHS\.map/);
  assert.match(workbenchSource, /onClick=\{\(\) => onOpenWorkbench\(id\)\}/);
  assert.match(workbenchSource, /disabled=\{workbenchController\.locked\}[\s\S]*?workbenchController\.onTransfer\(id\)[\s\S]*?带当前草稿进入/);
  assert.match(workbenchSource, /className="creation-ideas"[\s\S]*?onUsePrompt\?\.\(idea\.prompt\)/);
  assert.doesNotMatch(workbenchSource, /data-ui="creation-fast-lane"/);
  assert.doesNotMatch(workbenchSource, /未找到视频|开始新创作/);

  const entrySource = workbenchSource.slice(
    workbenchSource.indexOf("export function CreationEntryView"),
    workbenchSource.indexOf("function orderedWorkbenchEntries"),
  );
  assert.doesNotMatch(entrySource, /<textarea|<input/);
  const focusComposerSource = appSource.slice(
    appSource.indexOf("const focusCommunityComposer"),
    appSource.indexOf("const useCommunityPrompt"),
  );
  assert.doesNotMatch(focusComposerSource, /navigateStudio\("create"\)/);
  assert.match(appSource, /closeResultDialog\(\);\s*if \(submissionContext\.kind === "quick"\) navigateStudio\("create"\);\s*generationTaskRuntime\.queueSubmission\(\);/);
  assert.match(appSource, /const isAdvancedWorkbench = activeNav === "create" && ADVANCED_WORKBENCHES\.includes\(creationWorkbench\)/);
  assert.match(appSource, /const isQuickStudioView = isPrimaryStudioView && !isAdvancedWorkbench/);
  assert.equal(appSource.match(/isQuickStudioView && renderGenerationEditor\(\)/g)?.length, 1);
  assert.equal(appSource.match(/renderEditor=\{renderGenerationEditor\}/g)?.length, 1);
  assert.match(uiSource, /\{isQuick && <>\s*<button\s*id="mobile-composer-launcher"/);
  assert.match(appSource, /useStudioRouteState\(\)/);
  assert.match(routeStateSource, /const navigateCreationWorkbench = useCallback\(\(nextWorkbench\) => \{[\s\S]*?setActiveNav\("create"\);[\s\S]*?history\.pushState/);
  assert.doesNotMatch(uiSource, /进\s*3D\s*导演台|href=\{creationWorkbenchPath\("console"\)\}/);
  assert.doesNotMatch(appSource, /isPrimaryStudioView && \(\s*<aside/);
});

test("三个工作台路由由共享壳层持有，支持深链、返回与独立草稿恢复", () => {
  for (const kind of ["console", "notebook", "canvas"]) {
    assert.equal(readCreationWorkbench(`?workbench=${kind}`), kind);
    assert.equal(readCreationWorkbench(`?unrelated=1&workbench=${kind}`), kind);
  }
  for (const search of ["", "?workbench=entry", "?workbench=unknown", "?workbench=__proto__"]) {
    assert.equal(readCreationWorkbench(search), "entry");
  }
  assert.match(routeStateSource, /useState\(readCreationWorkbench\)/);
  assert.match(routeStateSource, /nextWorkbench === "entry"[\s\S]*?`\$\{nextPath\}\?workbench=\$\{encodeURIComponent\(nextWorkbench\)\}`/);
  assert.match(routeStateSource, /setCreationWorkbench\(readCreationWorkbench\(\)\)/);
  assert.match(routeStateSource, /addEventListener\?\.\("popstate", handlePopState\)/);
  assert.match(routeStateSource, /removeEventListener\?\.\("popstate", handlePopState\)/);
  assert.match(hubSource, /onWorkbenchChange\?\.\("entry"\)/);
  assert.match(workbenchSource, /aria-label="切换工作方式"/);
  assert.match(workbenchSource, /controller\?\.onOpenWorkbench\?\.\(path\.id\)/);
  assert.doesNotMatch(hubSource, /history\.(?:pushState|replaceState|back)|useState\(\(\) => readCreationWorkbench/);
  assert.match(workbenchSource, /data-ui="director-console"/);
  assert.match(workbenchSource, /data-ui="director-notebook"/);
  assert.match(workbenchSource, /lazy\(\(\) => import\("\.\/LineageGraphWorkspace\.jsx"\)\)/);
  assert.match(lineageSource, /data-ui="lineage-canvas"/);
});

test("创作页在全部数据状态下保留稳定标题、记录区域和键盘标签语义", () => {
  assert.match(hubSource, /<h1[^>]*id="creation-hub-title"[^>]*>创作<\/h1>/);
  assert.match(hubSource, /aria-labelledby="creation-hub-title"/);
  assert.match(workbenchSource, /loading \? \(/);
  assert.match(workbenchSource, /className="creation-record-browser"/);
  assert.match(workbenchSource, /id=\{`creation-media-tab-\$\{id\}`\}/);
  assert.match(workbenchSource, /tabIndex=\{media === id \? 0 : -1\}/);
  assert.match(hubSource, /event\.key === "ArrowRight"/);
  assert.match(hubSource, /event\.key === "ArrowLeft"/);
  assert.match(hubSource, /event\.key === "Home"/);
  assert.match(hubSource, /event\.key === "End"/);
});

test("高级工作台使用当前镜头、场次、节点编辑面并共用唯一提交控制器", () => {
  assert.match(hubSource, /const continueTask = workbench !== "entry" && contextualController\s*\? contextualController\.onImportTask\s*:\s*onAdjust \|\| onContinueTask/);
  assert.match(hubSource, /entry\.taskIds\.flatMap\(\(id\) => \{\s*const task = workbenchTasks\.find\(\(item\) => String\(taskId\(item\)\) === String\(id\)\)/);
  for (const kind of ["console", "notebook"]) {
    assert.equal(workbenchSource.match(new RegExp(`renderEditor\\?\\.\\(\\{ variant: "${kind}"`, "g"))?.length, 1);
  }
  assert.match(lineageSource, /renderEditor\(\{ variant: "canvas"/);
  assert.match(workbenchSource, /controller\?\.onEditEntry\?\.\(entry\.id\)/);
  assert.match(workbenchSource, /controller\?\.onNewEntry\?\.\(\{\}\)/);
  assert.match(workbenchSource, /props\.onOpenTask\(take\.task\)/);
  assert.match(workbenchSource, /props\.onPublishArtifact\(take\.task, take\.artifact\)/);
  assert.match(appSource, /renderEditor=\{renderGenerationEditor\}/);
  assert.equal(appSource.match(/const startGeneration = async \(\) =>/g)?.length, 1);
  assert.doesNotMatch(workbenchSource, /setTimeout|setInterval|createTask|startGeneration|扣费成功|生成成功/);
  const advancedSource = workbenchSource.slice(workbenchSource.indexOf("function orderedWorkbenchEntries"), workbenchSource.indexOf("export function CreationRecordBrowser"));
  assert.doesNotMatch(advancedSource, /onStartCreation|onContinueTask|带此设置进入极速档|onUsePrompt/);
});

test("场次和版本有稳定身份，谱系仅来自明确父节点而不是任务时间", () => {
  assert.match(workbenchSource, /这一册，共 \{entries\.length\} 场/);
  assert.match(workbenchSource, /entries\.map\(\(entry, index\) => <NotebookScene key=\{entry\.id\}/);
  assert.match(workbenchSource, /data-scene-id=\{entry\.id\}/);
  assert.match(workbenchSource, /const versions = entry\?\.takes \|\| \[\]/);
  assert.match(workbenchSource, /<WorkbenchVersions entry=\{entry\} controller=\{controller\} \/>/);
  assert.match(workbenchSource, /<StoryAiDirectorDesk[\s\S]*?key=\{entry\.id\}[\s\S]*?shotId=\{entry\.id\}/);
  assert.match(creationSource, /controller\?\.onSelectVersion\?\.\(entry\.id, taskId, artifactId\)/);
  assert.match(lineageSource, /const parent = verifiedParent\(entry, byId\)/);
  assert.match(lineageSource, /source: parent\.id,\s*target: entry\.id/);
  assert.match(lineageSource, /sourceTake: selectedTake/);
  assert.match(workbenchSource, /未指定来源的记录保持独立/);
  assert.doesNotMatch(workbenchSource, /takes\.map\(\(take, index\) => <NotebookScene|这一册，共 \{takes\.length\} 场|index % 3/);
});

test("任务视觉只为真实视频暴露原生播放控制，不给静态图伪造播放按钮", () => {
  assert.match(workbenchSource, /take\.previewUrl && take\.media === "video"[\s\S]*?<video[\s\S]*?controls/);
  assert.doesNotMatch(workbenchSource, /creation-task-play|<Play\b/);
});

test("创作页使用有形态差异的邀请、导演台、手记与画布样式", () => {
  assert.match(hubStyles, /\.creation-entry-copy h2\s*\{/);
  assert.match(designSystemStyles, /@import "\.\/workbenches\.css" layer\(system\.routes\)/);
  assert.match(workbenchStyles, /\.director-embed\s*\{[^}]*height:\s*clamp\(/s);
  assert.match(workbenchStyles, /\.wb3d-production\s*\{[^}]*display:\s*grid;/s);
  assert.match(workbenchStyles, /\.wb3d-stage\s*\{[^}]*border-radius:\s*var\(--radius-lg\)/s);
  assert.match(workbenchStyles, /\.wb-notebook-paper\s*\{/);
  assert.match(workbenchStyles, /\.wb-scene-prose\s*\{[^}]*font-family:[^;]*serif/s);
  assert.match(workbenchStyles, /\.lineage-graph\s*\{[^}]*height:\s*clamp\(/s);
  assert.match(lineageSource, /<Background variant=\{BackgroundVariant\.Dots\}/);
  assert.doesNotMatch(workbenchStyles, /:root\s*\{|!important/);
  assert.doesNotMatch(hubStyles, /\.director-console-stage\s*\{|\.director-notebook-sheet\s*\{|\.lineage-canvas-map\s*\{/);
});

test("高级工作台手机端在编辑器挂载前统一进入桌面端说明页", () => {
  assert.match(appSource, /const PHONE_ADVANCED_WORKBENCH_QUERY = "\(max-width: 720px\)"/);
  assert.match(appSource, /workbench: phoneAdvancedWorkbenchGate \? "entry" : creationWorkbench/);
  assert.match(appSource, /enabled:[\s\S]*?!phoneAdvancedWorkbenchGate/);
  assert.match(hubSource, /const desktopGateActive = advancedWorkbenchesDesktopOnly && CREATION_WORKBENCHES\.has\(workbench\)/);
  assert.match(hubSource, /desktopGateActive \? \([\s\S]*?<DesktopWorkbenchGate kind=\{workbench\}/);
  assert.match(workbenchSource, /data-ui="desktop-workbench-gate"/);
  assert.match(workbenchSource, /手机端不会加载编辑器/);
  assert.match(workbenchStyles, /\.wb-desktop-gate-header \.wb-back\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/);
  assert.match(workbenchStyles, /\.wb-desktop-gate-body \.wb-primary\s*\{[^}]*min-height:\s*48px;/);
  assert.match(workbenchStyles, /prefers-reduced-motion:\s*reduce/);
  assert.doesNotMatch(workbenchStyles, /\.wb-desktop-gate[^}]*position:\s*fixed/s);
  assert.doesNotMatch(`${hubStyles}\n${mobileStudioStyles}\n${workbenchStyles}`, /calc\(100vw\s*-\s*80px\)/);
});

test("创作内容类型与能力驱动生成器保持同步并保留渐进筛选和分页", () => {
  assert.match(appSource, /generationMediaKind=\{composerMediaKind\}/);
  assert.match(appSource, /onGenerationMediaChange=\{selectComposerMediaKind\}/);
  assert.match(appSource, /models\.find\(\(item\) => modelSupportsMediaKind\(item, nextKind\)\)/);
  assert.match(hubSource, /onGenerationMediaChange\?\.\(nextMedia\) === false/);
  assert.match(workbenchSource, /role="tablist" aria-label="创作内容类型"/);
  assert.match(workbenchSource, /最近 30 天/);
  assert.match(workbenchSource, /全部模型/);
  assert.match(workbenchSource, /按时间排列/);
  assert.match(workbenchSource, /aria-label="创作记录分页"/);
  assert.match(workbenchSource, /onPageChange\?\.\(page \+ 1\)/);
  assert.match(appSource, /supportsSearch=\{!isPersonalWorkspace\}/);
  assert.match(appSource, /supportsDateFilter=\{!isPersonalWorkspace\}/);
  assert.match(hubSource, /onMediaFilterChange\?\./);
  assert.match(hubSource, /queryChangeRef\.current\?\.\(query\)/);
});

test("任务费用、预览和结果动作继续使用既有真实契约", () => {
  assert.match(hubSource, /import \{ taskCostLabel \} from "\.\/taskArtifacts\.js"/);
  assert.match(hubSource, /costLabel: taskCostLabel\(task \|\| \{\}\)/);
  assert.match(appSource, /previewUrls=\{artworkPreviewUrls\}/);
  assert.match(appSource, /previewActionKey=\{artifactActionKey\}/);
  assert.match(appSource, /onRequestPreview=\{\(task, artifact\) => accessArtifact\(artifact,/);
  assert.match(hubSource, /activePreviewUrl\(previewUrls\[previewKey\]\)/);
  assert.match(workbenchSource, /onClick=\{\(\) => onRequestPreview\?\.\(take\.task, take\.artifact\)\}/);
  assert.match(workbenchSource, /<video[\s\S]*?muted[\s\S]*?playsInline[\s\S]*?preload="metadata"/);
  assert.match(workbenchSource, /正在加载预览/);
  assert.doesNotMatch(hubSource, /\.png"/);
  assert.doesNotMatch(creationSource, /function costLabel|预计剩余/);
});

test("创作页主层使用用户语言并为无权、失败和空结果提供下一步", () => {
  assert.match(workbenchSource, /无法查看创作记录/);
  assert.match(workbenchSource, /前往历史页面/);
  assert.match(workbenchSource, /清除筛选/);
  assert.match(workbenchSource, /先写一个镜头/);
  assert.match(workbenchSource, /写第一场/);
  assert.match(lineageSource, /先定义这个镜头想拍什么/);
  assert.match(workbenchSource, /本机保存，不跨设备同步/);
  assert.doesNotMatch(
    creationSource,
    /tasks\.read|tasks\.create|assets\.read|assets\.manage|Platform|能力合同|唯一提交入口|服务端结果为准|不会伪造|无伪连线|脱离真实生成器/,
  );
});

test("任务消息把权限和资源代码留在技术层", () => {
  const permissionMessage = taskUserMessage("missing tasks.read permission");
  const resourceMessage = taskUserMessage("required_resource_keys: feature.video");
  const unknownTechnicalMessage = taskUserMessage("UPSTREAM_CONTRACT_ERROR");

  assert.equal(permissionMessage, "当前账号没有查看任务记录的权限。请联系管理员开通权限。");
  assert.equal(resourceMessage, "当前创作所需的权限、额度或资源暂不可用。请调整设置，或联系管理员确认账号权限。");
  assert.equal(unknownTechnicalMessage, "暂时无法读取任务信息，请稍后再试。");
  assert.doesNotMatch(`${permissionMessage}\n${resourceMessage}\n${unknownTechnicalMessage}`, /tasks\.read|required_resource_keys|UPSTREAM_CONTRACT_ERROR/);
});
