import assert from "node:assert/strict";
import test from "node:test";

import {
  evidenceDigest,
  validateLiveResult,
  validateLiveStart,
} from "../scripts/run-relay-live-fault-harness.mjs";

const candidate = {
  instance_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  upstream_git_revision: "0ab02020603d22e5613bc4cf46bfab06f8567769",
  source_git_revision: "1".repeat(40),
  image_digest: `sha256:${"2".repeat(64)}`,
};

function fixture() {
  const runID = "11111111-1111-4111-8111-111111111111";
  const runNonce = "22222222-2222-4222-8222-222222222222";
  const acceptedAtUTC = "2026-08-06T22:00:01.000Z";
  const evidence = {
    id: "evidence-001",
    observed_at_utc: "2026-08-06T22:00:03.000Z",
    kind: "provider_and_postgres_state",
    action: "record one provider side effect and retained route state",
    data: { provider_effect_count: 1, outbox_id: 7, persisted_submission_token_hash: `sha256:${"3".repeat(64)}` },
  };
  evidence.sha256 = evidenceDigest(evidence);
  const assertions = {
    provider_side_effect_observed: true,
    reconciliation_required: true,
    sticky_route_and_slot_retained: true,
    automatic_resubmit_absent: true,
  };
  return {
    expectedStart: {
      runNonce,
      scenario: "live_provider_response_loss",
      candidate,
      requestSentAtMs: Date.parse("2026-08-06T22:00:00.000Z"),
      responseReceivedAtMs: Date.parse("2026-08-06T22:00:02.000Z"),
    },
    start: {
      schema_version: 1,
      run_id: runID,
      run_nonce: runNonce,
      scenario: "live_provider_response_loss",
      candidate,
      accepted_at_utc: acceptedAtUTC,
    },
    expectedResult: {
      runId: runID,
      runNonce,
      scenario: "live_provider_response_loss",
      candidate,
      acceptedAtUTC,
      resultReceivedAtMs: Date.parse("2026-08-06T22:00:06.000Z"),
    },
    result: {
      schema_version: 1,
      run_id: runID,
      run_nonce: runNonce,
      scenario: "live_provider_response_loss",
      candidate,
      execution_scope: "live_candidate_api_and_generation_worker",
      status: "PASS",
      request_received_at_utc: acceptedAtUTC,
      started_at_utc: "2026-08-06T22:00:01.000Z",
      completed_at_utc: "2026-08-06T22:00:05.000Z",
      assertions,
      assertion_evidence: Object.fromEntries(Object.keys(assertions).map((key) => [key, [evidence.id]])),
      raw_evidence: [evidence],
    },
  };
}

test("live evidence accepts a nonce, candidate, digest, and time-bound result", () => {
  const value = fixture();
  assert.deepEqual(validateLiveStart(value.start, value.expectedStart), []);
  assert.deepEqual(validateLiveResult(value.result, value.expectedResult), []);
});

test("live evidence rejects a replay under another nonce or candidate instance", () => {
  const value = fixture();
  const replayedStart = { ...value.start, run_nonce: "stale-nonce", candidate: { ...candidate, instance_id: "stale-instance" } };
  const startErrors = validateLiveStart(replayedStart, value.expectedStart);
  assert(startErrors.includes("start run nonce mismatch"));
  assert(startErrors.includes("start candidate binding mismatch"));

  const replayedResult = { ...value.result, run_nonce: "stale-nonce", candidate: { ...candidate, image_digest: `sha256:${"4".repeat(64)}` } };
  const resultErrors = validateLiveResult(replayedResult, value.expectedResult);
  assert(resultErrors.includes("run nonce mismatch"));
  assert(resultErrors.includes("candidate binding mismatch"));
});

test("live evidence rejects stale acceptance and observations outside the run window", () => {
  const value = fixture();
  const staleStart = { ...value.start, accepted_at_utc: "2026-08-06T20:00:00.000Z" };
  assert(validateLiveStart(staleStart, value.expectedStart).includes("start accepted before request time window"));

  const staleEvidence = { ...value.result.raw_evidence[0], observed_at_utc: "2026-08-06T20:00:00.000Z" };
  staleEvidence.sha256 = evidenceDigest(staleEvidence);
  const staleResult = { ...value.result, raw_evidence: [staleEvidence] };
  assert(validateLiveResult(staleResult, value.expectedResult).some((error) => error.includes("predates run time window")));
});

test("worker replacement assertion requires hashed Docker and process-instance evidence", () => {
  const value = fixture();
  const scenario = "live_candidate_worker_kill";
  const dockerEvidence = {
    id: "docker-candidate-replacement",
    observed_at_utc: "2026-08-06T22:00:04.000Z",
    kind: "docker_process_fault",
    action: "SIGKILL old candidate and create replacement from identical image",
    data: {
      old_container_id: "a".repeat(64),
      new_container_id: "b".repeat(64),
      old_process_instance_id: candidate.instance_id,
      new_process_instance_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      image_digest: candidate.image_digest,
      killed_at_utc: "2026-08-06T22:00:03.500Z",
    },
  };
  dockerEvidence.sha256 = evidenceDigest(dockerEvidence);
  const assertions = {
    provider_side_effect_observed: true,
    candidate_process_replaced: true,
    lease_recovered_without_resubmit: true,
    old_worker_token_fenced: true,
  };
  const expected = { ...value.expectedResult, scenario };
  const result = {
    ...value.result,
    scenario,
    assertions,
    assertion_evidence: {
      provider_side_effect_observed: ["evidence-001"],
      candidate_process_replaced: ["evidence-001", dockerEvidence.id],
      lease_recovered_without_resubmit: ["evidence-001"],
      old_worker_token_fenced: ["evidence-001"],
    },
    raw_evidence: [...value.result.raw_evidence, dockerEvidence],
  };
  assert.deepEqual(validateLiveResult(result, expected), []);

  const missingDockerMapping = {
    ...result,
    assertion_evidence: { ...result.assertion_evidence, candidate_process_replaced: ["evidence-001"] },
  };
  assert(validateLiveResult(missingDockerMapping, expected).includes("candidate_process_replaced lacks Docker process evidence"));
});
