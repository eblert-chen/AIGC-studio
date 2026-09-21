import assert from "node:assert/strict";
import test from "node:test";
import viteConfig from "../vite.config.mjs";

test("the development watcher excludes backend and pytest scratch trees", () => {
  const ignored = viteConfig.server?.watch?.ignored ?? [];

  assert.ok(ignored.includes("**/backend/**"));
  assert.ok(ignored.includes("**/.pytest-*/**"));
  assert.ok(ignored.includes("**/artifacts/pytest-tmp/**"));
  assert.ok(ignored.includes("**/artifacts/**/pytest-tmp/**"));
});
