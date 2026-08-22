import assert from "node:assert/strict";
import { mkdtemp, mkdir, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import { CANDIDATE_UPSTREAM_GIT_REVISION } from "../scripts/relay-migration-acceptance.mjs";
import { expectedCandidateImageLabels } from "../scripts/relay-fault-source-snapshot.mjs";
import {
  OBS_LIVE_GO_RUNNER_IMAGE,
  assertOBSLiveRunnerOutputSafe,
  bindOBSLiveCandidateProvenance,
  buildOBSLiveCompiledIdentityArgs,
  buildOBSLiveGoRunnerArgs,
  parseOBSLiveCompiledBuildIdentity,
  publishValidatedOBSLiveEvidence,
} from "../scripts/run-relay-obs-live-acceptance.mjs";

const snapshot = Object.freeze({
  sha1: "1".repeat(40),
  sha256: `sha256:${"2".repeat(64)}`,
  file_count: 321,
});
const imageDigest = `sha256:${"3".repeat(64)}`;
const harnessSnapshot = Object.freeze({
  sha1: "4".repeat(40),
  sha256: `sha256:${"5".repeat(64)}`,
  file_count: 17,
});
const compiledIdentity = Object.freeze({
  schema_version: 1,
  kind: "relay_compiled_build_identity",
  upstream_git_revision: CANDIDATE_UPSTREAM_GIT_REVISION,
  source_revision: snapshot.sha1,
  source_snapshot_sha256: snapshot.sha256,
  source_snapshot_file_count: snapshot.file_count,
});

test("OBS live provenance is the frozen source snapshot and matches all candidate image labels", () => {
  const labels = expectedCandidateImageLabels(snapshot, CANDIDATE_UPSTREAM_GIT_REVISION);
  assert.deepEqual(bindOBSLiveCandidateProvenance(imageDigest, labels, snapshot, compiledIdentity), {
    sourceRevision: snapshot.sha1,
    sourceSnapshotSHA256: snapshot.sha256,
    sourceSnapshotFileCount: snapshot.file_count,
    imageDigest,
    compiledIdentityVerified: true,
  });
});

test("OBS live provenance rejects an upstream HEAD substituted for the source snapshot", () => {
  const labels = expectedCandidateImageLabels(snapshot, CANDIDATE_UPSTREAM_GIT_REVISION);
  labels["org.opencontainers.image.revision"] = CANDIDATE_UPSTREAM_GIT_REVISION;
  assert.throws(
    () => bindOBSLiveCandidateProvenance(imageDigest, labels, snapshot, compiledIdentity),
    /org\.opencontainers\.image\.revision does not match the frozen source snapshot/,
  );
});

test("OBS live provenance rejects forged matching labels when the compiled binary identity differs", () => {
  const labels = expectedCandidateImageLabels(snapshot, CANDIDATE_UPSTREAM_GIT_REVISION);
  assert.throws(
    () => bindOBSLiveCandidateProvenance(imageDigest, labels, snapshot, {
      ...compiledIdentity,
      source_snapshot_sha256: `sha256:${"6".repeat(64)}`,
    }),
    /compiled identity does not match/,
  );
});

test("compiled identity parser is strict and rejects diagnostics or mutable identity", () => {
  assert.deepEqual(parseOBSLiveCompiledBuildIdentity(JSON.stringify(compiledIdentity)), compiledIdentity);
  assert.throws(
    () => parseOBSLiveCompiledBuildIdentity(JSON.stringify({ ...compiledIdentity, extra: true })),
    /envelope is invalid/,
  );
  assert.throws(
    () => parseOBSLiveCompiledBuildIdentity(JSON.stringify(compiledIdentity), "service startup warning"),
    /unexpected diagnostic output/,
  );
  assert.throws(
    () => parseOBSLiveCompiledBuildIdentity(JSON.stringify({ ...compiledIdentity, source_snapshot_file_count: 0 })),
    /envelope is invalid/,
  );
});

test("compiled identity is read offline from the immutable image with no network or mounts", () => {
  const args = buildOBSLiveCompiledIdentityArgs({ imageDigest });
  assert(args.includes("--rm"));
  assert.deepEqual(args.slice(args.indexOf("--network"), args.indexOf("--network") + 2), ["--network", "none"]);
  assert(args.includes("--read-only"));
  assert(args.includes("65532:65532"));
  assert(args.includes("no-new-privileges:true"));
  assert(args.includes("ALL"));
  assert.deepEqual(args.slice(args.indexOf("--entrypoint"), args.indexOf("--entrypoint") + 2), ["--entrypoint", "/new-api"]);
  assert.equal(args.at(-2), imageDigest);
  assert.equal(args.at(-1), "relay-build-identity");
  assert(!args.includes("--env"));
  assert(!args.includes("--mount"));
});

test("OBS live Go runner is digest-pinned, read-only, least-privilege, and has no Docker socket", () => {
  const args = buildOBSLiveGoRunnerArgs({ evidenceDirectory: resolve("test-evidence") });
  assert(args.includes("--rm"), "the exact test container must be removed on success or failure");
  assert(args.includes("-timeout=3m"), "the test process must terminate so --rm can complete after a failed or abandoned run");
  assert(args.includes(OBS_LIVE_GO_RUNNER_IMAGE));
  assert.match(OBS_LIVE_GO_RUNNER_IMAGE, /@sha256:[0-9a-f]{64}$/);
  assert(args.includes("--read-only"));
  const userIndex = args.indexOf("--user");
  assert(userIndex > 0);
  assert.match(args[userIndex + 1], /^[1-9][0-9]*:[1-9][0-9]*$/);
  assert(args.includes("--pids-limit"));
  assert(args.some((value) => value.startsWith("/tmp:rw,exec,nosuid,nodev,size=4g,uid=") && value.includes(",gid=") && value.endsWith(",mode=1777")));
  assert(args.includes("--cap-drop"));
  assert(args.includes("ALL"));
  assert(args.includes("no-new-privileges:true"));
  assert(args.some((value) => value.includes("target=/workspace,readonly")));
  assert(args.some((value) => value.includes("target=/evidence") && !value.includes("readonly")));
  assert(!args.includes("--privileged"));
  assert(!args.some((value) => /docker\.sock|docker_engine/i.test(value)));
  assert(args.includes("RELAY_OBS_LIVE_SOURCE_SNAPSHOT_SHA256"));
  assert(args.includes("RELAY_OBS_LIVE_SOURCE_FILE_COUNT"));
  assert(args.includes("RELAY_OBS_LIVE_PROVENANCE_ATTESTATION"));
  assert(args.includes("RELAY_OBS_LIVE_HARNESS_SNAPSHOT_SHA256"));
  assert(args.includes("RELAY_OBS_LIVE_HARNESS_FILE_COUNT"));
  for (const sensitiveName of [
    "HUAWEI_OBS_ACCESS_KEY_ID",
    "HUAWEI_OBS_SECRET_ACCESS_KEY",
    "HUAWEI_OBS_SECURITY_TOKEN",
    "HUAWEI_OBS_BUCKET",
  ]) {
    const index = args.indexOf(sensitiveName);
    assert(index > 0 && args[index - 1] === "--env", `${sensitiveName} must be inherited by name only`);
    assert(!args.some((value) => value.startsWith(`${sensitiveName}=`)), `${sensitiveName} value must not appear in Docker argv`);
  }
});

test("OBS live Go runner rejects a mutable toolchain image", () => {
  assert.throws(
    () => buildOBSLiveGoRunnerArgs({ evidenceDirectory: resolve("test-evidence"), runnerImage: "golang:latest" }),
    /must be pinned by sha256 digest/,
  );
});

test("OBS live runner rejects sensitive failure output without reflecting it", () => {
  const secret = "sensitive-live-secret-value";
  let failure;
  try {
    assertOBSLiveRunnerOutputSafe("", `request failed: ${secret}`, [secret]);
  } catch (error) {
    failure = error;
  }
  assert(failure instanceof Error);
  assert(!failure.message.includes(secret));
  assert.throws(
    () => assertOBSLiveRunnerOutputSafe("", "Get https://obs.example/object?AccessKeyId=redacted&Signature=redacted", []),
    /signed-request material/,
  );
  assert.doesNotThrow(() => assertOBSLiveRunnerOutputSafe("PASS endpoint_host=obs.example", "", [secret]));
});

test("post-run source drift cannot publish staged PASS evidence", async () => {
  const directory = await mkdtemp(join(tmpdir(), "relay-obs-live-publish-test-"));
  try {
    const stagingDirectory = join(directory, ".relay-obs-live-staging-fixed");
    await mkdir(stagingDirectory);
    const evidenceName = "relay-obs-live-evidence-11111111-1111-4111-8111-111111111111.json";
    const stagedEvidencePath = join(stagingDirectory, evidenceName);
    const provenance = {
      sourceRevision: snapshot.sha1,
      sourceSnapshotSHA256: snapshot.sha256,
      sourceSnapshotFileCount: snapshot.file_count,
      imageDigest,
      compiledIdentityVerified: true,
    };
    const evidence = {
      schema_version: 3,
      kind: "relay_huawei_obs_live_acceptance",
      status: "PASS",
      verified: true,
      source_revision: provenance.sourceRevision,
      source_revision_attestation: "candidate-image-label-compiled-binary-and-host-snapshot-bound-v2",
      source_snapshot_sha256: provenance.sourceSnapshotSHA256,
      source_snapshot_file_count: provenance.sourceSnapshotFileCount,
      harness_source_snapshot_sha256: harnessSnapshot.sha256,
      harness_source_snapshot_file_count: harnessSnapshot.file_count,
      image_source_labels_verified: true,
      image_compiled_identity_verified: true,
      container_image_digest: provenance.imageDigest,
    };
    await writeFile(stagedEvidencePath, `${JSON.stringify(evidence)}\n`, { mode: 0o600 });
    await assert.rejects(
      publishValidatedOBSLiveEvidence({
        evidenceDirectory: directory,
        stagedEvidencePath,
        evidence,
        provenance,
        sourceSnapshot: snapshot,
        postRunSourceSnapshot: { ...snapshot, sha256: `sha256:${"4".repeat(64)}` },
        harnessSnapshot,
        postRunHarnessSnapshot: harnessSnapshot,
      }),
      /source changed/,
    );
    const formalEvidence = (await readdir(directory)).filter((name) => /^relay-obs-live-evidence-/.test(name));
    assert.deepEqual(formalEvidence, []);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("post-run harness drift cannot publish staged PASS evidence", async () => {
  const directory = await mkdtemp(join(tmpdir(), "relay-obs-live-harness-drift-test-"));
  try {
    const stagingDirectory = join(directory, ".relay-obs-live-staging-fixed");
    await mkdir(stagingDirectory);
    const evidenceName = "relay-obs-live-evidence-22222222-2222-4222-8222-222222222222.json";
    const stagedEvidencePath = join(stagingDirectory, evidenceName);
    const provenance = {
      sourceRevision: snapshot.sha1,
      sourceSnapshotSHA256: snapshot.sha256,
      sourceSnapshotFileCount: snapshot.file_count,
      imageDigest,
      compiledIdentityVerified: true,
    };
    const evidence = {
      schema_version: 3,
      kind: "relay_huawei_obs_live_acceptance",
      status: "PASS",
      verified: true,
      source_revision: provenance.sourceRevision,
      source_revision_attestation: "candidate-image-label-compiled-binary-and-host-snapshot-bound-v2",
      source_snapshot_sha256: provenance.sourceSnapshotSHA256,
      source_snapshot_file_count: provenance.sourceSnapshotFileCount,
      harness_source_snapshot_sha256: harnessSnapshot.sha256,
      harness_source_snapshot_file_count: harnessSnapshot.file_count,
      image_source_labels_verified: true,
      image_compiled_identity_verified: true,
      container_image_digest: provenance.imageDigest,
    };
    await writeFile(stagedEvidencePath, `${JSON.stringify(evidence)}\n`, { mode: 0o600 });
    await assert.rejects(
      publishValidatedOBSLiveEvidence({
        evidenceDirectory: directory,
        stagedEvidencePath,
        evidence,
        provenance,
        sourceSnapshot: snapshot,
        postRunSourceSnapshot: snapshot,
        harnessSnapshot,
        postRunHarnessSnapshot: { ...harnessSnapshot, sha256: `sha256:${"7".repeat(64)}` },
      }),
      /harness changed/,
    );
    const formalEvidence = (await readdir(directory)).filter((name) => /^relay-obs-live-evidence-/.test(name));
    assert.deepEqual(formalEvidence, []);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
