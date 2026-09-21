import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readdir, readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";

const projectRoot = fileURLToPath(new URL("../", import.meta.url));
const directorDeskRoot = "vendor/storyai-3d-director-desk";
const directorDeskReleaseInputs = Object.freeze([
  `${directorDeskRoot}/index.html`,
  `${directorDeskRoot}/LICENSE`,
  `${directorDeskRoot}/package.json`,
  `${directorDeskRoot}/tsconfig.json`,
  `${directorDeskRoot}/tsconfig.node.json`,
  `${directorDeskRoot}/vite.config.ts`,
  `${directorDeskRoot}/src`,
]);
const releaseRoots = Object.freeze([
  "src",
  "public",
  "playwright.config.mjs",
  "scripts/start-all-local.ps1",
  "scripts/smoke-disposable-environment.mjs",
  "tests/frontend-release-closure.test.mjs",
  ...directorDeskReleaseInputs,
]);

function gitLines(args) {
  return execFileSync("git", args, {
    cwd: projectRoot,
    encoding: "utf8",
    windowsHide: true,
  })
    .split(/\r?\n/u)
    .map((line) => line.trim().replaceAll("\\", "/"))
    .filter(Boolean);
}

test("frontend release inputs exactly match the Git candidate", () => {
  const untracked = gitLines([
    "ls-files",
    "--others",
    "--exclude-standard",
    "--",
    ...releaseRoots,
  ]);
  const unstaged = gitLines([
    "diff",
    "--name-only",
    "--diff-filter=ACDMRTUXB",
    "--",
    ...releaseRoots,
  ]);

  assert.deepEqual(
    { untracked, unstaged },
    { untracked: [], unstaged: [] },
    [
      "Frontend release bytes are not reproducible from the staged Git candidate.",
      `Untracked (missing from a clean checkout):\n${untracked.join("\n") || "<none>"}`,
      `Unstaged (clean checkout receives different bytes):\n${unstaged.join("\n") || "<none>"}`,
    ].join("\n\n"),
  );
});

test("director desk source inputs are inside the Git closure without generated or presentation files", () => {
  assert.deepEqual(
    directorDeskReleaseInputs,
    [
      `${directorDeskRoot}/index.html`,
      `${directorDeskRoot}/LICENSE`,
      `${directorDeskRoot}/package.json`,
      `${directorDeskRoot}/tsconfig.json`,
      `${directorDeskRoot}/tsconfig.node.json`,
      `${directorDeskRoot}/vite.config.ts`,
      `${directorDeskRoot}/src`,
    ],
  );
  for (const excluded of [
    `${directorDeskRoot}/dist`,
    `${directorDeskRoot}/images`,
    `${directorDeskRoot}/node_modules`,
    `${directorDeskRoot}/public`,
    `${directorDeskRoot}/screenshots`,
    `${directorDeskRoot}/temp`,
    `${directorDeskRoot}/test-results`,
  ]) {
    assert.ok(!releaseRoots.includes(excluded), `${excluded} must not become a source baseline`);
  }

  const visibleToGitClosure = new Set(gitLines([
    "ls-files",
    "--cached",
    "--others",
    "--exclude-standard",
    "--",
    ...directorDeskReleaseInputs,
  ]));
  for (const required of [
    `${directorDeskRoot}/index.html`,
    `${directorDeskRoot}/LICENSE`,
    `${directorDeskRoot}/package.json`,
    `${directorDeskRoot}/tsconfig.json`,
    `${directorDeskRoot}/tsconfig.node.json`,
    `${directorDeskRoot}/vite.config.ts`,
    `${directorDeskRoot}/src/main.tsx`,
    `${directorDeskRoot}/src/App.tsx`,
    `${directorDeskRoot}/src/editor/io/hostBridge.ts`,
    `${directorDeskRoot}/src/styles/index.css`,
  ]) {
    assert.ok(visibleToGitClosure.has(required), `${required} is invisible to the Git release closure`);
  }
});

test("director workspace build emits the ignored runtime document, asset, and license", async () => {
  const buildCommand = process.platform === "win32"
    ? [process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", "npm run director:build"]]
    : ["npm", ["run", "director:build"]];
  execFileSync(buildCommand[0], buildCommand[1], {
    cwd: projectRoot,
    encoding: "utf8",
    env: { ...process.env, CI: "1" },
    timeout: 180_000,
    windowsHide: true,
  });

  const outputRoot = new URL("../public/director-desk/", import.meta.url);
  const [indexHtml, emittedLicense, sourceLicense, assetEntries, entryMetadata] = await Promise.all([
    readFile(new URL("index.html", outputRoot), "utf8"),
    readFile(new URL("THIRD_PARTY_LICENSES.txt", outputRoot), "utf8"),
    readFile(new URL(`../${directorDeskRoot}/LICENSE`, import.meta.url), "utf8"),
    readdir(new URL("assets/", outputRoot), { withFileTypes: true }),
    stat(new URL("assets/director-desk.js", outputRoot)),
  ]);
  assert.match(indexHtml, /<script defer src="\/director-desk\/assets\/director-desk\.js"><\/script>/);
  assert.ok(assetEntries.some((entry) => entry.isFile() && entry.name === "director-desk.js"));
  assert.ok(entryMetadata.isFile() && entryMetadata.size > 0, "director runtime bundle must be non-empty");
  assert.equal(emittedLicense, sourceLicense);
  assert.match(emittedLicense, /MIT License/);

  const generatedOutputs = [
    "public/director-desk/index.html",
    "public/director-desk/assets/director-desk.js",
    "public/director-desk/THIRD_PARTY_LICENSES.txt",
  ];
  assert.deepEqual(
    gitLines(["ls-files", "--cached", "--", ...generatedOutputs]),
    [],
    "generated director output must not replace its reviewed source inputs in Git",
  );
  assert.deepEqual(
    new Set(gitLines(["check-ignore", "--", ...generatedOutputs])),
    new Set(generatedOutputs),
    "generated director output must remain an ignored reproducible artifact",
  );
});

test("critical frontend runtime dependencies are tracked or staged", () => {
  const required = [
    "playwright.config.mjs",
    "scripts/smoke-disposable-environment.mjs",
    "scripts/start-all-local.ps1",
    "src/AppErrorBoundary.jsx",
    "src/RouteLoadingFallback.jsx",
    "src/billingPresentation.js",
    "src/phosphorIcons.js",
    "src/publishingPresentation.js",
    "src/studioAccess.js",
    "public/brand/xutian-ai-studio-symbol.svg",
    "public/brand/xutian-ai-studio-touch-icon.png",
    "public/brand/xutian-ai-studio-wordmark.svg",
  ];
  const indexed = new Set(gitLines(["ls-files", "--cached", "--", ...required]));
  const missing = required.filter((path) => !indexed.has(path));

  assert.deepEqual(
    missing,
    [],
    `These runtime dependencies are not in the Git candidate:\n${missing.join("\n")}`,
  );
});
