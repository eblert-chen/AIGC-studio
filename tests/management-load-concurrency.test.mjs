import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../src/ManagementConsole.jsx", import.meta.url),
  "utf8",
);

test("management loads abort their predecessor and only the current generation commits", () => {
  assert.match(source, /const loadRequestGenerationRef = useRef\(0\)/);
  assert.match(source, /const loadAbortControllerRef = useRef\(null\)/);
  assert.match(source, /loadAbortControllerRef\.current\?\.abort\(\)/);
  assert.match(source, /const requestGeneration = \+\+loadRequestGenerationRef\.current/);
  assert.match(source, /requestGeneration === loadRequestGenerationRef\.current/);
  assert.match(source, /if \(isCurrentRequest\(\)\) mergeData\(patch\)/);
  assert.match(source, /if \(!isCurrentRequest\(\) \|\| loadError\?\.name === "AbortError"\) return/);
  assert.match(source, /return \(\) => \{[\s\S]*?loadRequestGenerationRef\.current \+= 1;[\s\S]*?loadAbortControllerRef\.current\?\.abort\(\)/);
});

test("management data requests receive the active abort signal", () => {
  assert.match(source, /client\.getCompanyMe\(\{ signal \}\)/);
  assert.match(source, /client\.getTaskReport\(\{ page_size: 8 \}, \{ signal \}\)/);
  assert.match(source, /client\.getPlatformDashboard\(\{ page_size: 50 \}, \{ signal \}\)/);
  assert.match(source, /client\.listAdminAuditLogs\(\{ page_size: 100 \}, \{ signal \}\)/);
});
