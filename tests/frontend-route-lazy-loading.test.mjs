import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const mainSource = await readFile(new URL("../src/main.jsx", import.meta.url), "utf8");
const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const fallbackSource = await readFile(new URL("../src/RouteLoadingFallback.jsx", import.meta.url), "utf8");

test("authentication stays in the entry chunk while the authenticated application is lazy", () => {
  assert.match(mainSource, /const App = lazy\(async \(\) => \{[\s\S]*?import\("\.\/App\.jsx"\)/);
  assert.doesNotMatch(mainSource, /import\s+\{?\s*App\s*\}?\s+from\s+["']\.\/App\.jsx["']/);
  assert.match(mainSource, /<AuthGateway demoMode=\{DEMO_MODE\}>[\s\S]*?<Suspense fallback=\{<RouteLoadingFallback/);
  assert.match(mainSource, /<App \/>/);
});

test("the React root survives Vite HMR invalidation without a duplicate createRoot call", () => {
  assert.equal(mainSource.match(/createRoot\(/g)?.length, 1);
  assert.match(mainSource, /import\.meta\.hot\?\.data\.reactRoot \?\? createRoot\(rootElement\)/);
  assert.match(mainSource, /import\.meta\.hot\.data\.reactRoot = reactRoot/);
  assert.match(mainSource, /reactRoot\.render\(/);
});

test("large Studio and management routes use route-level lazy boundaries", () => {
  for (const path of [
    "./ManagementConsole.jsx",
    "./CommunityHome.jsx",
    "./CreationHub.jsx",
    "./PublishingCenter.jsx",
    "./AccountCenter.jsx",
    "./pages/studio/ArtworksView.jsx",
    "./pages/studio/HistoryView.jsx",
    "./pages/studio/ResultDetailView.jsx",
  ]) {
    assert.match(appSource, new RegExp(`import\\(["']${path.replaceAll(".", "\\.")}["']\\)`));
  }
  assert.match(appSource, /<Suspense fallback=\{\([\s\S]*?<RouteLoadingFallback[\s\S]*?label=\{`正在打开\$\{activeNav/);
  assert.match(appSource, /<Suspense fallback=\{<RouteLoadingFallback label=\{effectiveSurface === "platform" \? "正在打开平台运营台" : "正在打开企业管理台"\} \/>\}>/);
  assert.match(fallbackSource, /setTimeout\?\.\(\(\) => setTakingLonger\(true\), 8_000\)/);
  assert.match(fallbackSource, /加载时间较长[\s\S]*?重新加载页面/);
  assert.doesNotMatch(appSource, /from\s+["']\.\/ManagementConsole\.jsx["']/);
  assert.doesNotMatch(appSource, /from\s+["']\.\/PublishingCenter\.jsx["']/);
});
