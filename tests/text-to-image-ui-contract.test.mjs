import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "src", "App.jsx"), "utf8");
const editorSource = fs.readFileSync(path.join(root, "src", "components", "GenerationEditor.jsx"), "utf8");

test("text-to-image keeps the duration sentinel internal across live, history and composer views", () => {
  assert.match(appSource, /modeUsesDuration,/);
  const guardedDurationSurfaces = `${appSource}\n${editorSource}`.match(/modeUsesDuration\(generationMode\)\s*&&/g) ?? [];
  assert.ok(guardedDurationSurfaces.length >= 3, "every reachable duration surface stays mode guarded");
  assert.match(appSource, /buildCapabilityRequestPayload\(/);
  assert.match(appSource, /setDuration\(payload\.duration_seconds\)/);
});
