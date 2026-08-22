import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCandidateBaseline,
  validateCandidateBaseline,
} from "../scripts/relay-candidate-baseline.mjs";

test("candidate baseline freezes provenance without claiming acceptance", async () => {
  const baseline = await buildCandidateBaseline("2026-08-11T00:00:00.000Z");
  assert.equal(baseline.schema_version, 3);
  assert.equal(baseline.status, "UNVERIFIED");
  assert.equal(baseline.production_cutover_authorized, false);
  assert.equal(baseline.active_production_relay, "new-api-v1");
  assert.equal(baseline.python_relay_artifact_mode, "offline_historical_oracle_only");
  assert.equal(baseline.python_relay_production_admission_allowed, false);
  assert.match(baseline.note, /no PASS claim/);
  assert(Object.values(baseline.gates).every((status) => status === "NOT_RUN"));
  assert.deepEqual(validateCandidateBaseline(baseline, baseline), []);
});

test("candidate baseline rejects source drift and false PASS claims", async () => {
  const baseline = await buildCandidateBaseline("2026-08-11T00:00:00.000Z");
  const drifted = structuredClone(baseline);
  drifted.source.sha256 = `sha256:${"0".repeat(64)}`;
  drifted.platform_source.sha256 = `sha256:${"1".repeat(64)}`;
  drifted.gates.huawei_obs_live = "PASS";
  drifted.production_cutover_authorized = true;
  const errors = validateCandidateBaseline(drifted, baseline).join("\n");
  assert.match(errors, /source\.sha256/);
  assert.match(errors, /platform_source\.sha256/);
  assert.match(errors, /cannot claim PASS/);
  assert.match(errors, /cannot authorize production cutover/);
});

test("candidate baseline rejects any Python production admission", async () => {
  const baseline = await buildCandidateBaseline("2026-08-11T00:00:00.000Z");
  const invalid = structuredClone(baseline);
  invalid.python_relay_artifact_mode = "production_peer";
  invalid.python_relay_production_admission_allowed = true;
  assert.match(
    validateCandidateBaseline(invalid, baseline).join("\n"),
    /offline_historical_oracle_only/,
  );
  assert.match(
    validateCandidateBaseline(invalid, baseline).join("\n"),
    /python_relay_production_admission_allowed must equal false/,
  );
});

test("candidate baseline rejects missing and unexpected gates", async () => {
  const baseline = await buildCandidateBaseline("2026-08-11T00:00:00.000Z");
  const incomplete = structuredClone(baseline);
  delete incomplete.gates.huawei_obs_live;
  incomplete.gates.unreviewed_external_gate = "NOT_RUN";
  const errors = validateCandidateBaseline(incomplete, baseline).join("\n");
  assert.match(errors, /huawei_obs_live is missing/);
  assert.match(errors, /unreviewed_external_gate is not recognized/);
});

test("candidate baseline rejects non-canonical and impossible UTC timestamps", async () => {
  const baseline = await buildCandidateBaseline("2026-08-11T00:00:00.000Z");
  for (const generatedAt of [
    "2026-02-30T00:00:00.000Z",
    "2026-08-11T08:00:00+08:00",
    "2026-08-11T00:00:00Z",
  ]) {
    const invalid = structuredClone(baseline);
    invalid.generated_at_utc = generatedAt;
    assert.match(
      validateCandidateBaseline(invalid, baseline).join("\n"),
      /generated_at_utc is invalid/,
    );
  }
});

test("candidate baseline rejects extra release claims and non-canonical notes", async () => {
  const baseline = await buildCandidateBaseline("2026-08-11T00:00:00.000Z");
  const tampered = structuredClone(baseline);
  tampered.overall_status = "GO";
  tampered.release = { decision: "GO", production_authorized: true };
  tampered.source.unexpected_claim = "PASS";
  tampered.note = "Production is authorized.";
  const errors = validateCandidateBaseline(tampered, baseline).join("\n");
  assert.match(errors, /baseline\.overall_status is not recognized/);
  assert.match(errors, /baseline\.release is not recognized/);
  assert.match(errors, /source\.unexpected_claim is not recognized/);
  assert.match(errors, /canonical non-authorization statement/);
});
