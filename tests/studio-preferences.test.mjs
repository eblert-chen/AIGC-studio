import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_STUDIO_PREFERENCES,
  readStudioPreferences,
  studioPreferenceStorageKey,
  writeStudioPreferences,
} from "../src/studioPreferences.js";

function memoryStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
  };
}

test("Studio preferences are isolated by signed-in subject and persist booleans", () => {
  const storage = memoryStorage();
  assert.equal(writeStudioPreferences("user-a", { taskCompletionNotices: false }, storage), true);
  assert.deepEqual(readStudioPreferences("user-a", storage), { taskCompletionNotices: false });
  assert.deepEqual(readStudioPreferences("user-b", storage), DEFAULT_STUDIO_PREFERENCES);
  assert.notEqual(studioPreferenceStorageKey("user-a"), studioPreferenceStorageKey("user-b"));
});

test("invalid or blocked storage fails safely to the honest default", () => {
  const invalid = {
    getItem() {
      return "{invalid";
    },
  };
  const blocked = {
    setItem() {
      throw new Error("blocked");
    },
  };

  assert.deepEqual(readStudioPreferences("user-a", invalid), DEFAULT_STUDIO_PREFERENCES);
  assert.equal(writeStudioPreferences("user-a", { taskCompletionNotices: false }, blocked), false);
});

