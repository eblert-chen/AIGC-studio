import React from "react";
import { createRoot } from "react-dom/client";
import { PublishingCenter } from "../../src/PublishingCenter.jsx";
import "../../src/design-system/index.css";

const artwork = {
  id: "artifact-browser-1",
  artifact_id: "artifact-browser-1",
  task_id: "task-browser-1",
  asset_id: "asset-browser-1",
  media_type: "video",
  model_display_name: "Seedance Pro",
  request_payload: { prompt: "城市清晨的品牌短片" },
};

const connection = {
  id: "connection-browser-1",
  provider: "douyin",
  status: "active",
  display_name: "品牌主账号",
  external_account_id: "account-browser-1",
};

let jobs = [
  {
    id: "job-pending",
    artifact_id: artwork.artifact_id,
    connection_id: connection.id,
    provider: connection.provider,
    status: "pending_approval",
    title: "待批准短片",
    caption: "待批准发布文案",
    scheduled_at: "2030-08-30T12:00:00+08:00",
    timezone: "Asia/Shanghai",
  },
  {
    id: "job-cancel",
    artifact_id: artwork.artifact_id,
    connection_id: connection.id,
    provider: connection.provider,
    status: "scheduled",
    title: "待取消排期",
    caption: "取消路径发布文案",
    scheduled_at: "2030-08-31T12:00:00+08:00",
    timezone: "Asia/Shanghai",
  },
  {
    id: "job-failed",
    artifact_id: artwork.artifact_id,
    connection_id: connection.id,
    provider: connection.provider,
    status: "failed",
    title: "可恢复任务",
    caption: "恢复路径发布文案",
    scheduled_at: "2030-09-01T12:00:00+08:00",
    timezone: "Asia/Shanghai",
  },
  {
    id: "job-unknown",
    artifact_id: artwork.artifact_id,
    connection_id: connection.id,
    provider: connection.provider,
    status: "submission_unknown",
    title: "渠道结果待核对",
    caption: "不得直接重试的发布文案",
    scheduled_at: "2030-09-02T12:00:00+08:00",
    timezone: "Asia/Shanghai",
  },
];

const calls = [];

function record(method, payload = null) {
  calls.push({ method, payload });
}

function updateJob(id, patch) {
  jobs = jobs.map((job) => (job.id === id ? { ...job, ...patch } : job));
  return jobs.find((job) => job.id === id);
}

const client = {
  async listPublisherConnections() {
    record("listPublisherConnections");
    return { items: [connection], total: 1 };
  },
  async listPublisherOAuthProviders() {
    record("listPublisherOAuthProviders");
    return { items: [{ provider: "douyin", display_name: "抖音" }] };
  },
  async listPublicationJobs(filters) {
    record("listPublicationJobs", filters);
    const filtered = filters?.status
      ? jobs.filter((job) => job.status === filters.status)
      : jobs;
    return { items: filtered, total: filtered.length };
  },
  async listArtworks() {
    record("listArtworks");
    return { items: [artwork], total: 1 };
  },
  async createPublicationJob(payload) {
    record("createPublicationJob", payload);
    const created = {
      id: `job-created-${calls.length}`,
      artifact_id: payload.artifactId,
      connection_id: payload.connectionId,
      provider: connection.provider,
      status: "pending_approval",
      title: payload.title,
      caption: payload.caption,
      scheduled_at: payload.scheduledAt,
      timezone: payload.timezone,
    };
    jobs = [created, ...jobs];
    return created;
  },
  async approvePublicationJob(id) {
    record("approvePublicationJob", { id });
    return updateJob(id, { status: "scheduled" });
  },
  async cancelPublicationJob(id) {
    record("cancelPublicationJob", { id });
    return updateJob(id, { status: "cancelled" });
  },
  async retryPublicationJob(id) {
    record("retryPublicationJob", { id });
    return updateJob(id, { status: "queued" });
  },
  async reconcilePublicationJob(id, payload) {
    record("reconcilePublicationJob", { id, ...payload });
    return updateJob(id, {
      status: payload.outcome === "published" ? "published" : "failed",
      external_post_id: payload.externalPostId,
      external_post_url: payload.externalPostUrl,
      error_message: payload.errorMessage,
    });
  },
  async getPublicationJob(id) {
    record("getPublicationJob", { id });
    return jobs.find((job) => job.id === id);
  },
  async deletePublisherConnection(id) {
    record("deletePublisherConnection", { id });
    return { id, status: "disabled" };
  },
  async startPublisherOAuth(payload) {
    record("startPublisherOAuth", payload);
    return { authorization_url: "http://127.0.0.1/oauth-placeholder" };
  },
};

globalThis.__publishingFixture = {
  calls,
  jobs: () => jobs.map((job) => ({ ...job })),
};

function PublishingCenterFixture() {
  return (
    <div className="app-shell" data-theme="paper">
      <main className="main-canvas is-secondary">
        <PublishingCenter
          client={client}
          artworks={[artwork]}
          canReadAccounts
          canManageAccounts
          canReadJobs
          canManageJobs
          autoPublishingEnabled
          publishingEntitlementResolved
          accountSideEffectsEnabled
          jobSideEffectsEnabled
        />
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <PublishingCenterFixture />
  </React.StrictMode>,
);
