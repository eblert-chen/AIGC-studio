import { spawnSync } from "node:child_process";
import { readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const repositoryRoot = fileURLToPath(new URL("../", import.meta.url));
const testDirectory = fileURLToPath(new URL("../tests/", import.meta.url));
const testFiles = (await readdir(testDirectory, { withFileTypes: true }))
  .filter(
    (entry) =>
      entry.isFile() &&
      entry.name.endsWith(".test.mjs") &&
      entry.name !== "sites-worker.test.mjs",
  )
  .map((entry) => `tests/${entry.name}`)
  .sort();

if (testFiles.length === 0) {
  throw new Error("No root test files were discovered");
}

const result = spawnSync(process.execPath, ["--test", ...testFiles], {
  cwd: repositoryRoot,
  stdio: "inherit",
});

if (result.error) {
  throw result.error;
}
if (result.signal) {
  throw new Error(`Root test process terminated by ${result.signal}`);
}

process.exitCode = result.status ?? 1;
