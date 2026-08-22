import assert from "node:assert/strict";
import test from "node:test";

import {
  isTaskActive,
  isTaskAttentionRequired,
  resolveTaskStatus,
} from "../src/taskStatus.js";

test("only accepted, queued and processing tasks use active progress semantics", () => {
  assert.equal(isTaskActive("accepted"), true);
  assert.equal(isTaskActive("queued"), true);
  assert.equal(isTaskActive("processing"), true);
  assert.equal(isTaskActive("timed_out"), false);
  assert.equal(isTaskActive("reconciliation_required"), false);
  assert.equal(isTaskActive("new-provider-status"), false);
});

test("timeout and reconciliation states are explicit attention states, never queued", () => {
  const timedOut = resolveTaskStatus("timed_out");
  const reconciliation = resolveTaskStatus("reconciliation_required");

  assert.equal(timedOut.stage, "timed-out");
  assert.equal(timedOut.label, "已超时");
  assert.equal(timedOut.progress, 0);
  assert.equal(reconciliation.stage, "reconciliation-required");
  assert.equal(reconciliation.label, "待人工确认");
  assert.equal(reconciliation.progress, 0);
  assert.equal(isTaskAttentionRequired("timed_out"), true);
  assert.equal(isTaskAttentionRequired("reconciliation_required"), true);
});

test("unrecognized task states fail closed instead of becoming a spinner", () => {
  const unknown = resolveTaskStatus("provider_paused");
  assert.equal(unknown.stage, "unknown");
  assert.equal(unknown.active, false);
  assert.equal(unknown.terminal, true);
  assert.match(unknown.label, /provider_paused/);
});

