import assert from "node:assert/strict";
import test from "node:test";

import {
  isTaskActive,
  isTaskAttentionRequired,
  resolveTaskStatus,
  taskEtaSeconds,
  taskTimingLabel,
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
  assert.equal(unknown.rawStatus, "provider_paused");
  assert.equal(unknown.label, "状态待确认");
  assert.doesNotMatch(unknown.label, /provider_paused/);
});

test("active tasks use stage copy when the server provides no ETA", () => {
  assert.equal(taskTimingLabel({ status: "accepted" }), "任务已接收");
  assert.equal(taskTimingLabel({ status: "queued" }), "等待调度");
  assert.equal(taskTimingLabel({ status: "processing" }), "生成处理中");
  assert.equal(
    taskTimingLabel({ status: "processing", progress: 55 }),
    "生成处理中",
  );
});

test("ETA is displayed only from explicit valid server timing fields", () => {
  const now = Date.parse("2026-08-29T10:00:00.000Z");

  assert.equal(
    taskTimingLabel({ status: "processing", eta_seconds: 41.2 }, { now }),
    "预计剩余 42 秒",
  );
  assert.equal(
    taskTimingLabel(
      {
        status: "queued",
        estimated_completion_at: "2026-08-29T10:01:30.000Z",
      },
      { now },
    ),
    "预计剩余 90 秒",
  );
  assert.equal(taskEtaSeconds({ remaining_seconds: "12" }, { now }), null);
  assert.equal(taskEtaSeconds({ eta_at: "2026-08-29T10:01:30" }, { now }), null);
  assert.equal(
    taskTimingLabel({ status: "processing", eta_seconds: -1 }, { now }),
    "生成处理中",
  );
});
