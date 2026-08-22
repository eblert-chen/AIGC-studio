import assert from "node:assert/strict";
import test from "node:test";
import {
  deriveArtworksFromTasks,
  downloadRecordState,
  downloadState,
  normalizePage,
  taskAuthor,
  taskCompany,
  taskCostLabel,
  taskParametersLabel,
} from "../src/taskArtifacts.js";

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
      request_payload: { mode: "text_to_video" },
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
  assert.equal(taskCostLabel(task), "¥4.25");
  assert.equal(
    taskParametersLabel(task.request_payload),
    "图生视频，9:16，1080p，10 秒，2 个产物，2 图 + 1 音频，人脸已启用",
  );
  assert.equal(taskCostLabel({ status: "failed", quote_cents: 900 }), "未扣费");
});

test("personal points never pass through the company money formatter", () => {
  assert.equal(taskCostLabel({ status: "succeeded", actual_cost_points: 45 }), "45 积分");
  assert.equal(taskCostLabel({ status: "accepted", reserved_points: 30 }), "预占 30 积分");
  assert.equal(taskCostLabel({ status: "draft", quote_points: 60 }), "预计 60 积分");
  assert.equal(
    taskCostLabel({ status: "succeeded", actual_cost_points: 0, actual_cost_cents: 999 }),
    "0 积分",
  );
  assert.equal(taskCompany({ workspace_id: "personal-user-1" }), "个人空间");
});
