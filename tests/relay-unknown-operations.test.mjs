import assert from "node:assert/strict";
import test from "node:test";

import {
  adaptRelayUnknownPage,
  adaptRelayUnknownResult,
  adaptRelayUnknownSubmission,
  assertRelayUnknownResultMatchesResolution,
  buildRelayUnknownResolution,
  refreshRelayUnknownWithReadback,
  relayUnknownResolveFieldErrors,
  relayUnknownResolveErrorMessage,
  resolveRelayUnknownWithReadback,
} from "../src/admin/relayUnknownOperations.js";

const rawDetail = {
  api_version: "v1",
  schema_version: 1,
  object: "generation.reconciliation",
  job_id: "91c5cd71-bde2-4cf9-b6ed-b264b0841f51",
  tenant_id: "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
  client_reference_id: "platform-task-2086",
  model: "kling-video-v2.1",
  mode: "image_to_video",
  status: "reconciliation_required",
  provider_route_id: 208,
  provider_route_key: "kling-official-prod-01",
  provider_name: "Kling Official",
  provider_account_id: "kling-account-07",
  provider_channel_id: 31,
  provider_key_index: 0,
  provider_channel_class: "official",
  provider_upstream_model: "kling-v2-1-master",
  provider_submission_attempt: 2,
  unknown_at: "2026-08-07T03:31:00Z",
  reconciliation_token: `sha256:${"a".repeat(64)}`,
  error_code: "PROVIDER_RESPONSE_LOSS",
  error_message: "Provider response was lost",
  created_at: "2026-08-07T03:30:42Z",
  updated_at: "2026-08-07T03:31:00Z",
};

const createdForm = {
  outcome: "created",
  upstreamTaskId: "provider-task-8842",
  verificationReference: "provider-console-event-7781",
  reason: "值班负责人已完成双人核对",
  approved: true,
};

const synchronousRawDetail = {
  ...rawDetail,
  model: "seedream-5.0",
  mode: "text_to_image",
  provider_upstream_model: "doubao-seedream-5-0-260128",
};

const synchronousForm = {
  outcome: "created",
  verificationReference: "provider-console-event-seedream-42",
  reason: "值班负责人核对同步图片响应归档",
  approved: true,
  synchronousResult: {
    providerModelId: "doubao-seedream-5-0-260128",
    providerResponseSha256: "d".repeat(64),
    artifactUrl: "https://provider.example/result.png?temporary=one-time",
    providerCreatedAt: "2026-08-28T01:02:03Z",
    generatedImages: "1",
    outputTokens: "144",
    totalTokens: "160",
  },
};

function createdReceipt(overrides = {}) {
  return {
    api_version: "v1",
    schema_version: 1,
    object: "generation.reconciliation_result",
    event_id: "76b2dd9a-85aa-48f9-8462-9242dc855fc2",
    operation_id: "relay-reconcile-op-20260811",
    request_id: "relay-result-read-20260811",
    tenant_id: rawDetail.tenant_id,
    job_id: rawDetail.job_id,
    outcome: "created",
    upstream_task_id: "provider-task-8842",
    expected_route_id: rawDetail.provider_route_id,
    expected_submission_attempt: rawDetail.provider_submission_attempt,
    expected_reconciliation_token: rawDetail.reconciliation_token,
    verification_reference: "provider-console-event-7781",
    approved_by: "admin-zhou",
    approval_reason: "值班负责人已完成双人核对",
    approval_key_id: "relay-approval-key-1",
    approval_signature: `hmac-sha256:${"b".repeat(64)}`,
    resolved_status: "processing",
    current_status: "processing",
    payload_sha256: "c".repeat(64),
    resolved_at: "2026-08-11T08:18:00Z",
    ...overrides,
  };
}

function synchronousReceipt(overrides = {}) {
  return createdReceipt({
    outcome: "created",
    upstream_task_id: `seedream:${"d".repeat(64)}`,
    verification_reference: synchronousForm.verificationReference,
    approval_reason: synchronousForm.reason,
    synchronous_result: {
      provider_model_id: synchronousForm.synchronousResult.providerModelId,
      provider_response_sha256: synchronousForm.synchronousResult.providerResponseSha256,
      artifact_url_sha256: "e".repeat(64),
      provider_created_at: synchronousForm.synchronousResult.providerCreatedAt,
      generated_images: 1,
      output_tokens: 144,
      total_tokens: 160,
    },
    ...overrides,
  });
}

test("Relay unknown list and detail preserve the provider route fencing evidence", () => {
  const page = adaptRelayUnknownPage({
    data: [rawDetail],
    page: 3,
    page_size: 50,
    total: 121,
  });
  assert.equal(page.page, 3);
  assert.equal(page.sourceStatus, "available");
  assert.equal(page.pageSize, 50);
  assert.equal(page.total, 121);
  assert.deepEqual(
    {
      jobId: page.items[0].jobId,
      providerRouteId: page.items[0].providerRouteId,
      providerSubmissionAttempt: page.items[0].providerSubmissionAttempt,
      reconciliationToken: page.items[0].reconciliationToken,
    },
    {
      jobId: rawDetail.job_id,
      providerRouteId: 208,
      providerSubmissionAttempt: 2,
      reconciliationToken: rawDetail.reconciliation_token,
    },
  );
});

test("a missing operations response remains unavailable instead of looking empty", () => {
  const page = adaptRelayUnknownPage();
  assert.equal(page.sourceStatus, "unavailable");
  assert.equal(page.total, null);
  assert.deepEqual(page.items, []);
});

test("resolve cannot be built before an explicit human approval", () => {
  const detail = adaptRelayUnknownSubmission(rawDetail);
  assert.throws(
    () => buildRelayUnknownResolution(detail, {
      outcome: "not_created",
      verificationReference: "provider-console-event-7781",
      reason: "Provider 控制台确认没有任务",
      approved: false,
    }),
    /明确确认/,
  );
});

test("created resolution pins the freshly-read route, attempt and token", () => {
  const detail = adaptRelayUnknownSubmission(rawDetail);
  assert.deepEqual(
    buildRelayUnknownResolution(detail, {
      outcome: "created",
      upstreamTaskId: " provider-task-8842 ",
      verificationReference: " provider-console-event-7781 ",
      reason: " 值班负责人已完成双人核对 ",
      approved: true,
    }),
    {
      outcome: "created",
      upstream_task_id: "provider-task-8842",
      expected_route_id: 208,
      expected_submission_attempt: 2,
      expected_reconciliation_token: rawDetail.reconciliation_token,
      verification_reference: "provider-console-event-7781",
      reason: "值班负责人已完成双人核对",
    },
  );
});

test("created text-to-image reconciliation derives its identity and sends strict synchronous evidence", () => {
  const detail = adaptRelayUnknownSubmission(synchronousRawDetail);
  const resolution = buildRelayUnknownResolution(detail, synchronousForm);

  assert.equal(resolution.upstream_task_id, `seedream:${"d".repeat(64)}`);
  assert.deepEqual(resolution.synchronous_result, {
    provider_model_id: "doubao-seedream-5-0-260128",
    provider_response_sha256: "d".repeat(64),
    artifact_url: "https://provider.example/result.png?temporary=one-time",
    provider_created_at: "2026-08-28T01:02:03Z",
    generated_images: 1,
    output_tokens: 144,
    total_tokens: 160,
  });

  for (const synchronousResult of [
    { ...synchronousForm.synchronousResult, artifactUrl: "http://provider.example/result.png" },
    { ...synchronousForm.synchronousResult, providerCreatedAt: "2026-08-28T01:02:03+00:00" },
    { ...synchronousForm.synchronousResult, generatedImages: "17" },
    { ...synchronousForm.synchronousResult, totalTokens: "143" },
  ]) {
    assert.throws(
      () => buildRelayUnknownResolution(detail, { ...synchronousForm, synchronousResult }),
      /HTTPS|UTC|生成图片数|总 token 数/,
    );
  }
});

test("not-created text-to-image reconciliation never sends synchronous evidence", () => {
  const detail = adaptRelayUnknownSubmission(synchronousRawDetail);
  const resolution = buildRelayUnknownResolution(detail, {
    ...synchronousForm,
    outcome: "not_created",
    upstreamTaskId: "must-not-leak",
  });

  assert.equal(resolution.upstream_task_id, "");
  assert.equal(Object.hasOwn(resolution, "synchronous_result"), false);
  assert.doesNotMatch(JSON.stringify(resolution), /temporary=one-time/);
});

test("text-to-image receipts contain only a digest and must match submitted evidence", () => {
  const detail = adaptRelayUnknownSubmission(synchronousRawDetail);
  const resolution = buildRelayUnknownResolution(detail, synchronousForm);
  const receipt = assertRelayUnknownResultMatchesResolution(
    synchronousReceipt(),
    detail,
    resolution,
  );

  assert.equal(receipt.synchronousResult.artifactUrlSha256, "e".repeat(64));
  assert.equal(JSON.stringify(receipt).includes("temporary=one-time"), false);
  assert.throws(
    () => assertRelayUnknownResultMatchesResolution(
      synchronousReceipt({
        synchronous_result: {
          ...synchronousReceipt().synchronous_result,
          total_tokens: 161,
        },
      }),
      detail,
      resolution,
    ),
    /receipt 与本次审批证据不一致/,
  );
  assert.throws(
    () => assertRelayUnknownResultMatchesResolution(
      synchronousReceipt({
        synchronous_result: {
          ...synchronousReceipt().synchronous_result,
          artifact_url: synchronousForm.synchronousResult.artifactUrl,
        },
      }),
      detail,
      resolution,
    ),
    /签名元数据或最终状态无效/,
  );
});

test("Platform validation locations become field-level reconciliation errors", () => {
  assert.deepEqual(
    relayUnknownResolveFieldErrors({
      details: {
        validationErrors: [
          {
            loc: ["body", "synchronous_result", "artifact_url"],
            msg: "artifact_url must be an absolute credential-free HTTPS URL",
          },
          {
            loc: ["body", "reason"],
            msg: "String should have at least 3 characters",
          },
        ],
      },
    }),
    {
      artifact_url: "artifact_url must be an absolute credential-free HTTPS URL",
      reason: "String should have at least 3 characters",
    },
  );
});

test("Relay receipt is structurally valid and matches every approved evidence field", () => {
  const detail = adaptRelayUnknownSubmission(rawDetail);
  const resolution = buildRelayUnknownResolution(detail, createdForm);
  const receipt = assertRelayUnknownResultMatchesResolution(
    createdReceipt(),
    detail,
    resolution,
    {
      expectedOperationId: "relay-reconcile-op-20260811",
      expectedApprovedBy: "admin-zhou",
    },
  );
  assert.equal(adaptRelayUnknownResult(createdReceipt()).payloadSha256, "c".repeat(64));
  assert.equal(receipt.operationId, "relay-reconcile-op-20260811");

  const conflictingResolutions = [
    { ...resolution, outcome: "not_created", upstream_task_id: "" },
    { ...resolution, upstream_task_id: "provider-task-conflict" },
    { ...resolution, expected_route_id: 999 },
    { ...resolution, expected_submission_attempt: 3 },
    { ...resolution, expected_reconciliation_token: `sha256:${"d".repeat(64)}` },
    { ...resolution, verification_reference: "different-evidence" },
    { ...resolution, reason: "不同审批原因" },
  ];
  for (const conflicting of conflictingResolutions) {
    assert.throws(
      () => assertRelayUnknownResultMatchesResolution(
        createdReceipt(),
        detail,
        conflicting,
        { expectedApprovedBy: "admin-zhou" },
      ),
      /receipt 与本次审批证据不一致.*禁止再次 resolve/,
    );
  }
  assert.throws(
    () => assertRelayUnknownResultMatchesResolution(
      createdReceipt({ approval_signature: "unsigned" }),
      detail,
      resolution,
    ),
    /签名元数据或最终状态无效.*保持锁定/,
  );
  assert.throws(
    () => assertRelayUnknownResultMatchesResolution(
      createdReceipt({ resolved_status: "failed" }),
      detail,
      resolution,
    ),
    /签名元数据或最终状态无效.*保持锁定/,
  );
});

test("an ambiguous resolve performs one POST then one read-only result lookup", async () => {
  const detail = adaptRelayUnknownSubmission(rawDetail);
  const resolution = buildRelayUnknownResolution(detail, createdForm);
  const calls = [];
  const result = await resolveRelayUnknownWithReadback({
    item: detail,
    resolution,
    expectedApprovedBy: "admin-zhou",
    resolve: async (jobId, body) => {
      calls.push({ method: "POST", jobId, body });
      throw Object.assign(new Error("service unavailable after write"), {
        status: 503,
        code: "HTTP_ERROR",
      });
    },
    getResult: async (jobId, operationId) => {
      calls.push({ method: "GET", jobId, operationId });
      return createdReceipt();
    },
  });

  assert.equal(result.confirmation, "result_readback");
  assert.deepEqual(calls.map(({ method }) => method), ["POST", "GET"]);
  assert.equal(calls[0].body, resolution);
  assert.equal(calls[1].operationId, undefined);
});

test("an ambiguous resolve fails closed when the receipt conflicts", async () => {
  const detail = adaptRelayUnknownSubmission(rawDetail);
  const resolution = buildRelayUnknownResolution(detail, createdForm);
  let postCount = 0;
  let readCount = 0;
  await assert.rejects(
    resolveRelayUnknownWithReadback({
      item: detail,
      resolution,
      expectedApprovedBy: "admin-zhou",
      resolve: async () => {
        postCount += 1;
        throw Object.assign(new Error("network reset"), {
          status: 0,
          code: "NETWORK_ERROR",
        });
      },
      getResult: async () => {
        readCount += 1;
        return createdReceipt({ verification_reference: "other-evidence" });
      },
    }),
    (error) => {
      assert.match(error.message, /receipt 与本次审批证据不一致/);
      assert.equal(error.relayResultProofRequired, true);
      return true;
    },
  );
  assert.equal(postCount, 1);
  assert.equal(readCount, 1);
});

test("refresh turns a pending-detail 404 into a read-only receipt confirmation", async () => {
  const detail = adaptRelayUnknownSubmission(rawDetail);
  const resolution = buildRelayUnknownResolution(detail, createdForm);
  const calls = [];
  const result = await refreshRelayUnknownWithReadback({
    item: detail,
    resolution,
    expectedApprovedBy: "admin-zhou",
    getDetail: async () => {
      calls.push("detail");
      throw Object.assign(new Error("not pending"), { status: 404 });
    },
    getResult: async () => {
      calls.push("result");
      return createdReceipt();
    },
  });
  assert.equal(result.state, "resolved");
  assert.equal(result.receipt.jobId, detail.jobId);
  assert.deepEqual(calls, ["detail", "result"]);
});

test("refresh never queries a receipt while the pending detail still exists", async () => {
  const detail = adaptRelayUnknownSubmission(rawDetail);
  let resultReads = 0;
  const result = await refreshRelayUnknownWithReadback({
    item: detail,
    getDetail: async () => rawDetail,
    getResult: async () => {
      resultReads += 1;
      return createdReceipt();
    },
  });
  assert.equal(result.state, "pending");
  assert.equal(resultReads, 0);
});

test("not-created resolution cannot leak an upstream id and rejects bad fencing", () => {
  const detail = adaptRelayUnknownSubmission(rawDetail);
  const payload = buildRelayUnknownResolution(detail, {
    outcome: "not_created",
    upstreamTaskId: "must-not-be-sent",
    verificationReference: "provider-console-event-7781",
    reason: "Provider 控制台确认没有任务",
    approved: true,
  });
  assert.equal(payload.upstream_task_id, "");

  assert.throws(
    () => buildRelayUnknownResolution(
      { ...detail, reconciliationToken: "stale-token" },
      {
        outcome: "not_created",
        verificationReference: "provider-console-event-7781",
        reason: "Provider 控制台确认没有任务",
        approved: true,
      },
    ),
    /token fencing/,
  );
});

test("unknown network results and stale proofs require a refresh instead of a retry", () => {
  assert.match(
    relayUnknownResolveErrorMessage({ status: 0, code: "NETWORK_ERROR" }),
    /严禁自动或手动重复提交.*刷新详情/,
  );
  assert.match(
    relayUnknownResolveErrorMessage({ status: 409, code: "HTTP_ERROR" }),
    /fencing.*禁止重复提交.*刷新详情/,
  );
});
