import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const viewSource = await readFile(
  new URL("../src/pages/studio/ResultDetailView.jsx", import.meta.url),
  "utf8",
);
const routeStyles = await readFile(
  new URL("../src/design-system/studio-routes.css", import.meta.url),
  "utf8",
);

test("result detail uses a fixed header, single scrolling body, and fixed footer", () => {
  assert.match(viewSource, /className="result-detail-header"/);
  assert.match(viewSource, /className="result-detail-body"/);
  assert.match(viewSource, /className="result-detail-footer"/);
  assert.match(
    routeStyles,
    /\.result-modal\s*\{[\s\S]*?grid-template-rows:\s*auto minmax\(0, 1fr\) auto;[\s\S]*?overflow:\s*hidden;/,
  );
  assert.match(
    routeStyles,
    /\.result-detail-body\s*\{[\s\S]*?overflow-y:\s*auto;/,
  );
});

test("phone result detail preserves touch targets and the bottom safe area", () => {
  assert.match(
    routeStyles,
    /\.result-modal :is\([^)]*\) button\s*\{\s*min-height:\s*var\(--control-size-touch\);/,
  );
  assert.match(
    routeStyles,
    /\.result-detail-footer\s*\{[\s\S]*?env\(safe-area-inset-bottom\)/,
  );
  assert.match(routeStyles, /@media \(max-width:\s*360px\)/);
});

test("real result actions stay wired while demo and incomplete archive evidence fail closed", () => {
  for (const callback of [
    "onDownloadArtifact",
    "onPromoteArtifact",
    "onOpenPublication",
    "onAdjust",
  ]) {
    assert.match(viewSource, new RegExp(`${callback}\\(`));
  }
  assert.match(viewSource, /liveMode && !resultArtifactEvidence\?\.complete/);
  assert.match(viewSource, /示例结果，不包含真实文件，无法下载/);
  assert.match(viewSource, /示例不可下载/);
  assert.doesNotMatch(viewSource, /onDemoDownload/);
  assert.doesNotMatch(viewSource, /产物|归档|访问 URL/);
  assert.match(viewSource, /label="关闭任务详情" onClick=\{onClose\}/);
});
