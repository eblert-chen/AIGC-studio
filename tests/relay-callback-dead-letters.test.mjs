import assert from "node:assert/strict";
import test from "node:test";

import {
  adaptRelayCallbackDeadLetter,
  buildRelayCallbackRedrive,
  redriveRelayCallbackWithReadback,
} from "../src/admin/relayCallbackDeadLetters.js";

const eventId = "b9b2537e-258c-4a98-af8a-6d23bdb135a4";
const operationId = "callback-redrive-operation-0001";
const raw = {
  event_id: eventId,
  tenant_id: "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
  job_id: "58775bb2-b6d2-4ad3-ab03-2f9d10854ba1",
  state: "dead_letter",
  attempts: 8,
  max_attempts: 8,
  payload_sha256: "1".repeat(64),
  callback_url_sha256: "2".repeat(64),
};

function receipt() {
  return {
    object: "generation.callback_redrive_result",
    delivery_event_id: eventId,
    evidence: {
      operation_id: operationId,
      actor: "oncall-a",
      reason: "Destination incident is resolved",
      previous_state: "dead_letter",
      result_state: "pending",
      receipt_sha256: "3".repeat(64),
    },
  };
}

test("callback redrive requires explicit approval and normalized evidence", () => {
  const item = adaptRelayCallbackDeadLetter(raw);
  assert.throws(() => buildRelayCallbackRedrive(item, { actor: "oncall-a", reason: "ok", approved: false }), /确认/);
  assert.deepEqual(buildRelayCallbackRedrive(item, { actor: " oncall-a ", reason: " Destination incident is resolved ", approved: true }), {
    actor: "oncall-a", reason: "Destination incident is resolved", approved: true,
  });
});

test("ambiguous redrive performs one POST call then one GET readback", async () => {
  const item = adaptRelayCallbackDeadLetter(raw);
  const request = { actor: "oncall-a", reason: "Destination incident is resolved", approved: true, operation_id: operationId };
  let posts = 0;
  let reads = 0;
  const error = new Error("network lost");
  error.status = 0;
  const result = await redriveRelayCallbackWithReadback({
    item,
    request,
    redrive: async () => { posts += 1; throw error; },
    getResult: async () => { reads += 1; return receipt(); },
  });
  assert.equal(result.confirmation, "result_readback");
  assert.equal(posts, 1);
  assert.equal(reads, 1);
});
