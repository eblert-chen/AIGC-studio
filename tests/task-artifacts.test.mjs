import assert from "node:assert/strict";
import test from "node:test";
import {
  deriveArtworksFromTasks,
  downloadRecordState,
  downloadState,
  normalizePage,
  taskAuthor,
  taskArtifactEvidence,
  taskCompany,
  taskCostLabel,
  taskParametersLabel,
} from "../src/taskArtifacts.js";
import {
  artifactEvidenceIssueMessage,
  downloadStatusPresentation,
  studioErrorMessage,
  studioTaskParametersLabel,
} from "../src/components/studio/studioPresentation.js";

test("normalizes paged responses and preserves the legacy task array", () => {
  assert.deepEqual(normalizePage([{ id: "task-1" }], { pageSize: 24 }), {
    page: 1,
    page_size: 24,
    total: 1,
    items: [{ id: "task-1" }],
    legacy: true,
  });
  assert.deepEqual(
    normalizePage({ page: 3, page_size: 20, total: 42, items: [{ id: "task-2" }] }),
    {
      page: 3,
      page_size: 20,
      total: 42,
      items: [{ id: "task-2" }],
      legacy: false,
    },
  );
});

test("derives real archived artifacts from legacy successful tasks without inventing download evidence", () => {
  const artworks = deriveArtworksFromTasks([
    {
      id: "task-1",
      company_id: "company-1",
      user_id: "user-1",
      status: "succeeded",
      model_id: "model-1",
      model_display_name: "Rush Video",
      request_payload: { mode: "text_to_video", output_count: 1 },
      output_artifacts: [
        {
          asset_id: "asset-1",
          media_type: "video",
          content_type: "video/mp4",
          size_bytes: 2048,
          sha256: "abc",
        },
      ],
    },
    {
      id: "task-2",
      status: "failed",
      output_artifacts: [{ asset_id: "must-not-render" }],
    },
  ]);

  assert.equal(artworks.length, 1);
  assert.equal(artworks[0].asset_id, "asset-1");
  assert.equal(artworks[0].download_evidence_available, false);
  assert.equal(downloadState(artworks[0]).key, "unknown");
});

test("successful tasks expose artifacts only when archive evidence is complete", () => {
  const base = {
    status: "succeeded",
    request_payload: { output_count: 2 },
  };
  assert.equal(taskArtifactEvidence({ ...base, output_artifacts: [] }).state, "evidence_missing");
  assert.equal(taskArtifactEvidence({
    ...base,
    output_artifacts: [{ asset_id: "asset-1", media_type: "video" }],
  }).complete, false);
  assert.equal(taskArtifactEvidence({
    ...base,
    output_artifacts: [null, { asset_id: "asset-2", media_type: "video" }],
  }).complete, false);
  const complete = taskArtifactEvidence({
    ...base,
    output_artifacts: [
      { asset_id: "asset-1", media_type: "video" },
      { asset_id: "asset-2", media_type: "video" },
    ],
  });
  assert.equal(complete.complete, true);
  assert.equal(complete.artifacts.length, 2);
  assert.equal(taskArtifactEvidence({ status: "succeeded", output_artifacts: [] }).complete, false);
});

test("keeps download issuance separate from storage-confirmed completion", () => {
  assert.deepEqual(downloadState({ downloaded: false, download_issue_count: 0 }).key, "not_downloaded");
  assert.equal(downloadState({ download_status: "issued" }).key, "issued");
  assert.equal(downloadState({}, { issuedLocally: true }).key, "issued");
  assert.equal(
    downloadState({ downloaded: false, download_issue_count: 3, download_completed_count: 0 }).key,
    "issued",
  );
  assert.equal(
    downloadState({ downloaded: true, download_issue_count: 3, download_completed_count: 1 }).key,
    "completed",
  );
  assert.equal(downloadState({}).key, "unknown");
});

test("presents download issuance and completion as distinct user states", () => {
  assert.deepEqual(
    {
      label: downloadStatusPresentation({ download_status: "issued" }).label,
      detail: downloadStatusPresentation({ download_status: "issued" }).detail,
    },
    {
      label: "下载链接已生成",
      detail: "临时下载链接已生成，尚未确认下载完成",
    },
  );
  assert.equal(downloadStatusPresentation({ downloaded: true }).label, "已确认下载");
  assert.equal(
    downloadStatusPresentation({ downloaded: false, download_issue_count: 0 }).label,
    "未下载",
  );
  assert.equal(downloadStatusPresentation({}).label, "状态待同步");
});

test("collection presentation hides internal failure language without weakening fail-closed states", () => {
  const fallback = "作品暂时无法读取，请稍后重试。";
  assert.equal(studioErrorMessage("tasks.read denied", fallback), fallback);
  assert.equal(studioErrorMessage("Input asset is referenced by a task", fallback), fallback);
  assert.equal(studioErrorMessage("网络连接已中断", fallback), "网络连接已中断");
  assert.match(artifactEvidenceIssueMessage({ complete: false }), /作品文件信息不完整/);
  assert.match(studioTaskParametersLabel({ mode: "text_to_image", output_count: 2 }), /2 个作品/);
  assert.doesNotMatch(
    studioTaskParametersLabel({ mode: "text_to_image", output_count: 2 }),
    /产物/,
  );
});

test("treats a legacy audit row as issued but never as completed", () => {
  const legacy = downloadRecordState({ id: "download-1" });
  const completed = downloadRecordState({
    id: "download-2",
    status: "completed",
    downloaded: true,
    completed_at: "2026-08-05T00:00:00Z",
  });
  assert.equal(legacy.key, "issued");
  assert.equal(completed.key, "completed");
});

test("formats task identity, parameters and charge semantics from persisted fields", () => {
  const task = {
    status: "succeeded",
    actual_cost_cents: 425,
    billing_unit: "CNY_CENT",
    billing_version: 1,
    company_id: "company-123456789",
    user_display_name: "林瑶",
    request_payload: {
      mode: "image_to_video",
      aspect_ratio: "9:16",
      resolution: "1080p",
      duration_seconds: 10,
      output_count: 2,
      face_enabled: true,
      assets: [
        { media_type: "image" },
        { media_type: "image" },
        { media_type: "audio" },
      ],
    },
  };
  assert.equal(taskAuthor(task), "林瑶");
  assert.equal(taskCompany(task), "公司 company-1234");
  assert.equal(taskCostLabel(task), "¥4.25（历史人民币计费）");
  assert.equal(
    taskParametersLabel(task.request_payload),
    "图生视频，9:16，1080p，10 秒，2 个产物，2 图 + 1 音频，人脸已启用",
  );
  const legacy = { billing_unit: "CNY_CENT", billing_version: 1 };
  assert.equal(taskCostLabel({
    ...legacy,
    status: "failed",
    quote_cents: 900,
    actual_cost_cents: null,
    reserved_cents: 0,
  }), "未扣费");
  assert.equal(taskCostLabel({
    ...legacy,
    status: "cancelled",
    quote_cents: 900,
    actual_cost_cents: null,
    reserved_cents: 0,
  }), "未扣费");
  assert.equal(
    taskCostLabel({ ...legacy, status: "failed", quote_cents: 900 }),
    "扣费状态待核验",
  );
  assert.equal(
    taskCostLabel({ status: "cancelled", actual_cost_cents: null, reserved_cents: 0 }),
    "账务单位待确认",
  );
  assert.equal(taskCostLabel({ ...legacy, status: "timed_out", quote_cents: 900 }), "费用待核验");
  assert.equal(taskCostLabel({ ...legacy, status: "succeeded", quote_cents: 900 }), "结算金额未记录");
  assert.equal(taskCostLabel({ ...legacy, status: "failed", actual_cost_cents: 900 }), "账务异常：¥9.00（历史人民币计费）");
  assert.equal(taskCostLabel({ ...legacy, status: "cancelled", reserved_cents: 900 }), "预占未释放：¥9.00（历史人民币计费）");
  for (const malformedReserved of [null, "", false]) {
    assert.equal(
      taskCostLabel({
        ...legacy,
        status: "failed",
        actual_cost_cents: null,
        reserved_cents: malformedReserved,
      }),
      "账务数据异常",
    );
  }
  assert.equal(
    taskCostLabel({
      billing_unit: "POINT",
      billing_version: 2,
      status: "cancelled",
      actual_cost_points: null,
      reserved_points: null,
    }),
    "账务数据异常",
  );
});

test("does not present the text-to-image protocol sentinel as a one-second duration", () => {
  assert.equal(
    taskParametersLabel({
      mode: "text_to_image",
      aspect_ratio: "1:1",
      resolution: "2048x2048",
      duration_seconds: 1,
      output_count: 1,
    }),
    "文生图，1:1，2048x2048，1 个产物",
  );
});

test("personal points never pass through the company money formatter", () => {
  const points = { billing_unit: "POINT", billing_version: 2 };
  assert.equal(taskCostLabel({ ...points, status: "succeeded", actual_cost_points: 45 }), "45 积分");
  assert.equal(taskCostLabel({ ...points, status: "accepted", reserved_points: 30 }), "预占 30 积分");
  assert.equal(taskCostLabel({ ...points, status: "draft", quote_points: 60 }), "预计 60 积分");
  assert.equal(
    taskCostLabel({ ...points, status: "succeeded", actual_cost_points: 0, actual_cost_cents: 999 }),
    "0 积分",
  );
  assert.equal(
    taskCostLabel({ status: "succeeded", actual_cost_points: 45 }),
    "结算单位待确认",
  );
  assert.equal(
    taskCostLabel({
      billing_unit: "POINT",
      billing_version: 2,
      billing_scope: "internal_test",
      status: "succeeded",
      actual_cost_points: 8,
    }),
    "8 影子积分",
  );
  assert.equal(taskCompany({ workspace_id: "personal-user-1" }), "个人空间");
});
