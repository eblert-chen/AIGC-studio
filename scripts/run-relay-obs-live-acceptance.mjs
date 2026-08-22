#!/usr/bin/env node

import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { chmod, link, lstat, mkdtemp, readFile, readdir, realpath, rm } from "node:fs/promises";
import { basename, dirname, isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { CANDIDATE_UPSTREAM_GIT_REVISION } from "./relay-migration-acceptance.mjs";
import { harnessSourceSnapshot, relaySourceSnapshot, validateCandidateImageLabels } from "./relay-fault-source-snapshot.mjs";

export const OBS_LIVE_GO_RUNNER_IMAGE = "golang:1.26.1-alpine@sha256:2389ebfa5b7f43eeafbd6be0c3700cc46690ef842ad962f6c5bd6be49ed82039";
export const OBS_LIVE_HOST_RUNNER_ATTESTATION = "host-image-label-compiled-binary-and-frozen-snapshot-verification-v2";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const workspace = resolve(scriptDirectory, "..");
const relayRoot = resolve(workspace, "backend", "new-api-relay");
const evidenceNamePattern = /^relay-obs-live-evidence-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.json$/i;
const immutableImagePattern = /^sha256:[0-9a-f]{64}$/;
const immutableRunnerPattern = /^[^\s@]+@sha256:[0-9a-f]{64}$/;
const testContainerNamePattern = /^relay-obs-live-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const identityContainerNamePattern = /^relay-obs-identity-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const maximumCapturedOutputBytes = 16 * 1024 * 1024;
const goGateTimeoutMilliseconds = 10 * 60 * 1000;

function command(commandName, args, options = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(commandName, args, {
      cwd: workspace,
      env: options.env ?? process.env,
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    let capturedBytes = 0;
    let outputOverflow = false;
    let timedOut = false;
    const capture = (chunks, chunk) => {
      capturedBytes += chunk.length;
      if (capturedBytes > maximumCapturedOutputBytes) {
        outputOverflow = true;
        child.kill();
        return;
      }
      chunks.push(chunk);
    };
    child.stdout.on("data", (chunk) => capture(stdout, chunk));
    child.stderr.on("data", (chunk) => capture(stderr, chunk));
    child.on("error", () => reject(new Error(`${commandName} could not be started`)));
    const timer = options.timeoutMs ? setTimeout(() => {
      timedOut = true;
      child.kill();
    }, options.timeoutMs) : null;
    child.on("close", (code) => {
      if (timer) clearTimeout(timer);
      const result = {
        code,
        stdout: Buffer.concat(stdout).toString("utf8").trim(),
        stderr: Buffer.concat(stderr).toString("utf8").trim(),
        timedOut,
      };
      if (outputOverflow) reject(new Error(`${commandName} output exceeded the safe capture limit`));
      else if (code === 0 || options.allowFailure) resolvePromise(result);
      else reject(new Error(`${commandName} failed with status ${code}`));
    });
  });
}

function sensitiveOutputVariants(value) {
  if (!value) return [];
  const bytes = Buffer.from(value, "utf8");
  return [
    value,
    encodeURIComponent(value),
    bytes.toString("base64"),
    bytes.toString("base64url"),
    JSON.stringify(value),
  ];
}

export function assertOBSLiveRunnerOutputSafe(stdout, stderr, sensitiveValues = []) {
  const output = `${stdout ?? ""}\n${stderr ?? ""}`;
  for (const value of sensitiveValues) {
    if (typeof value !== "string" || value.length < 6) continue;
    if (sensitiveOutputVariants(value).some((variant) => variant && output.includes(variant))) {
      throw new Error("OBS live Go test output contained sensitive material");
    }
  }
  const lower = output.toLowerCase();
  for (const marker of [
    "accesskeyid=", "signature=", "securitytoken=", "authorization:",
    "x-amz-credential", "x-amz-signature", "x-obs-credential", "x-obs-signature", "x-obs-security-token",
  ]) {
    if (lower.includes(marker)) throw new Error("OBS live Go test output contained signed-request material");
  }
}

export function parseOBSLiveCompiledBuildIdentity(stdout, stderr = "") {
  if (stderr.trim()) throw new Error("candidate compiled identity command wrote unexpected diagnostic output");
  let identity;
  try {
    identity = JSON.parse(stdout);
  } catch {
    throw new Error("candidate compiled identity command did not return valid JSON");
  }
  const expectedKeys = [
    "kind",
    "schema_version",
    "source_revision",
    "source_snapshot_file_count",
    "source_snapshot_sha256",
    "upstream_git_revision",
  ];
  if (!identity || Array.isArray(identity) || Object.keys(identity).sort().join("\0") !== expectedKeys.join("\0") ||
      identity.schema_version !== 1 || identity.kind !== "relay_compiled_build_identity" ||
      !/^[0-9a-f]{40}$/.test(identity.upstream_git_revision) ||
      !/^[0-9a-f]{40}$/.test(identity.source_revision) ||
      !/^sha256:[0-9a-f]{64}$/.test(identity.source_snapshot_sha256) ||
      !Number.isSafeInteger(identity.source_snapshot_file_count) || identity.source_snapshot_file_count < 1) {
    throw new Error("candidate compiled identity envelope is invalid");
  }
  return Object.freeze({ ...identity });
}

export function buildOBSLiveCompiledIdentityArgs({
  imageDigest,
  containerName = `relay-obs-identity-${randomUUID()}`,
} = {}) {
  if (!immutableImagePattern.test(imageDigest ?? "")) throw new Error("compiled identity inspection requires an immutable candidate image id");
  if (!identityContainerNamePattern.test(containerName)) throw new Error("compiled identity inspection container name is invalid");
  return [
    "run", "--rm", "--name", containerName,
    "--network", "none",
    "--read-only",
    "--user", "65532:65532",
    "--pids-limit", "32",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges:true",
    "--entrypoint", "/new-api",
    imageDigest,
    "relay-build-identity",
  ];
}

export function bindOBSLiveCandidateProvenance(imageDigest, labels, snapshot, compiledIdentity) {
  if (!immutableImagePattern.test(imageDigest)) throw new Error("candidate image inspection did not return an immutable image id");
  const errors = validateCandidateImageLabels(labels, snapshot, CANDIDATE_UPSTREAM_GIT_REVISION);
  if (compiledIdentity?.schema_version !== 1 || compiledIdentity?.kind !== "relay_compiled_build_identity" ||
      compiledIdentity.upstream_git_revision !== CANDIDATE_UPSTREAM_GIT_REVISION ||
      compiledIdentity.source_revision !== snapshot.sha1 ||
      compiledIdentity.source_snapshot_sha256 !== snapshot.sha256 ||
      compiledIdentity.source_snapshot_file_count !== snapshot.file_count) {
    errors.push("candidate binary compiled identity does not match the frozen source snapshot");
  }
  if (errors.length > 0) throw new Error(errors.join("; "));
  return Object.freeze({
    sourceRevision: snapshot.sha1,
    sourceSnapshotSHA256: snapshot.sha256,
    sourceSnapshotFileCount: snapshot.file_count,
    imageDigest,
    compiledIdentityVerified: true,
  });
}

async function inspectCandidateImage(imageReference, snapshot) {
  const identity = await command("docker", ["image", "inspect", imageReference, "--format", "{{.Id}}"]);
  const imageDigest = identity.stdout;
  if (!immutableImagePattern.test(imageDigest)) throw new Error("candidate image inspection did not return an immutable image id");
  const labelsResult = await command("docker", ["image", "inspect", imageDigest, "--format", "{{json .Config.Labels}}"]);
  let labels;
  try {
    labels = JSON.parse(labelsResult.stdout);
  } catch {
    throw new Error("candidate image labels are not valid JSON");
  }
  const labelErrors = validateCandidateImageLabels(labels, snapshot, CANDIDATE_UPSTREAM_GIT_REVISION);
  if (labelErrors.length > 0) throw new Error(labelErrors.join("; "));
  const identityContainerName = `relay-obs-identity-${randomUUID()}`;
  let identityResult;
  try {
    identityResult = await command("docker", buildOBSLiveCompiledIdentityArgs({ imageDigest, containerName: identityContainerName }), {
      allowFailure: true,
      timeoutMs: 30_000,
    });
    if (identityResult.timedOut) throw new Error("candidate compiled identity command timed out");
    if (identityResult.code !== 0) throw new Error(`candidate compiled identity command failed with status ${identityResult.code}`);
  } finally {
    const cleanup = await command("docker", ["container", "rm", "--force", identityContainerName], { allowFailure: true, timeoutMs: 30_000 });
    if (cleanup.timedOut) throw new Error("candidate compiled identity container cleanup timed out");
    const residual = await command("docker", ["container", "inspect", identityContainerName], { allowFailure: true, timeoutMs: 30_000 });
    if (residual.timedOut || residual.code === 0) throw new Error("candidate compiled identity container was not removed");
  }
  const compiledIdentity = parseOBSLiveCompiledBuildIdentity(identityResult.stdout, identityResult.stderr);
  return bindOBSLiveCandidateProvenance(imageDigest, labels, snapshot, compiledIdentity);
}

function dockerBindMount(source, target, readOnly = false) {
  if (source.includes(",")) throw new Error(`Docker bind source may not contain a comma: ${source}`);
  return `type=bind,source=${source},target=${target}${readOnly ? ",readonly" : ""}`;
}

function nonRootRunnerIdentity() {
  const hostUID = typeof process.getuid === "function" ? process.getuid() : 0;
  const hostGID = typeof process.getgid === "function" ? process.getgid() : 0;
  const uid = Number.isSafeInteger(hostUID) && hostUID > 0 ? hostUID : 65532;
  const gid = Number.isSafeInteger(hostGID) && hostGID > 0 ? hostGID : 65532;
  return { uid, gid };
}

export function buildOBSLiveGoRunnerArgs({
  evidenceDirectory,
  runnerImage = OBS_LIVE_GO_RUNNER_IMAGE,
  containerName = `relay-obs-live-${randomUUID()}`,
} = {}) {
  if (!isAbsolute(evidenceDirectory ?? "")) throw new Error("OBS live evidence directory must be absolute");
  if (!immutableRunnerPattern.test(runnerImage)) throw new Error("OBS live Go runner image must be pinned by sha256 digest");
  if (!testContainerNamePattern.test(containerName)) throw new Error("OBS live test container name is invalid");
  const { uid, gid } = nonRootRunnerIdentity();
  return [
    "run", "--rm", "--name", containerName, "--init", "--read-only",
    "--user", `${uid}:${gid}`,
    "--pids-limit", "256",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges:true",
    "--mount", dockerBindMount(relayRoot, "/workspace", true),
    "--mount", dockerBindMount(evidenceDirectory, "/evidence"),
    "--tmpfs", `/tmp:rw,exec,nosuid,nodev,size=4g,uid=${uid},gid=${gid},mode=1777`,
    "--workdir", "/workspace",
    "--env", "CGO_ENABLED=0",
    "--env", "GOWORK=off",
    "--env", "GOCACHE=/tmp/go-build",
    "--env", "GOMODCACHE=/tmp/go-mod",
    "--env", "RELAY_OBS_LIVE_EVIDENCE_DIR",
    "--env", "RELAY_OBS_LIVE_SOURCE_REVISION",
    "--env", "RELAY_OBS_LIVE_SOURCE_SNAPSHOT_SHA256",
    "--env", "RELAY_OBS_LIVE_SOURCE_FILE_COUNT",
    "--env", "RELAY_OBS_LIVE_IMAGE_DIGEST",
    "--env", "RELAY_OBS_LIVE_PROVENANCE_ATTESTATION",
    "--env", "RELAY_OBS_LIVE_HARNESS_SNAPSHOT_SHA256",
    "--env", "RELAY_OBS_LIVE_HARNESS_FILE_COUNT",
    "--env", "HUAWEI_OBS_ACCESS_KEY_ID",
    "--env", "HUAWEI_OBS_SECRET_ACCESS_KEY",
    "--env", "HUAWEI_OBS_SECURITY_TOKEN",
    "--env", "HUAWEI_OBS_ENDPOINT",
    "--env", "HUAWEI_OBS_BUCKET",
    runnerImage,
    "go", "test",
    "-tags=integration,obs_live",
    "-run", "^TestLiveHuaweiOBSPrivateRoundTrip$",
    "-count=1",
    "-timeout=3m",
    "-v",
    "./integration/platformobs",
  ];
}

function parseArguments(argv) {
  const parsed = {
    candidateImage: process.env.RELAY_OBS_LIVE_CANDIDATE_IMAGE?.trim() ?? "",
    evidenceDirectory: process.env.RELAY_OBS_LIVE_EVIDENCE_DIR?.trim() ?? "",
    runnerImage: OBS_LIVE_GO_RUNNER_IMAGE,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const option = argv[index];
    if (!["--candidate-image", "--evidence-dir", "--go-runner-image"].includes(option)) throw new Error(`unknown option ${option}`);
    const value = argv[index + 1];
    if (!value) throw new Error(`${option} requires a value`);
    index += 1;
    if (option === "--candidate-image") parsed.candidateImage = value;
    else if (option === "--evidence-dir") parsed.evidenceDirectory = value;
    else parsed.runnerImage = value;
  }
  if (!parsed.candidateImage) throw new Error("--candidate-image (or RELAY_OBS_LIVE_CANDIDATE_IMAGE) is required");
  if (!parsed.evidenceDirectory) throw new Error("--evidence-dir (or RELAY_OBS_LIVE_EVIDENCE_DIR) is required");
  return parsed;
}

async function validateEvidenceDirectory(directory) {
  if (!isAbsolute(directory) || directory !== directory.trim()) throw new Error("OBS live evidence directory must be an absolute path");
  const info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error("OBS live evidence directory must be an existing real directory");
  const canonical = resolve(await realpath(directory));
  const requested = resolve(directory);
  if ((process.platform === "win32" ? canonical.toLowerCase() : canonical) !== (process.platform === "win32" ? requested.toLowerCase() : requested)) {
    throw new Error("OBS live evidence directory must not traverse a symlink");
  }
}

function requiredCredentialEnvironment() {
  for (const name of ["HUAWEI_OBS_ACCESS_KEY_ID", "HUAWEI_OBS_SECRET_ACCESS_KEY", "HUAWEI_OBS_ENDPOINT", "HUAWEI_OBS_BUCKET"]) {
    if (!(process.env[name]?.trim())) throw new Error(`${name} is required for the live OBS gate`);
  }
}

function validateWrittenEvidence(evidence, provenance) {
  if (evidence?.schema_version !== 3 || evidence.kind !== "relay_huawei_obs_live_acceptance" || evidence.status !== "PASS" || evidence.verified !== true) {
    throw new Error("OBS live runner did not produce a valid PASS evidence envelope");
  }
  if (evidence.source_revision !== provenance.sourceRevision ||
      evidence.source_revision_attestation !== "candidate-image-label-compiled-binary-and-host-snapshot-bound-v2" ||
      evidence.source_snapshot_sha256 !== provenance.sourceSnapshotSHA256 ||
      evidence.source_snapshot_file_count !== provenance.sourceSnapshotFileCount ||
      evidence.image_source_labels_verified !== true ||
      evidence.image_compiled_identity_verified !== true ||
      evidence.container_image_digest !== provenance.imageDigest) {
    throw new Error("OBS live evidence provenance does not match the inspected candidate image labels and compiled identity");
  }
}

function assertIsolatedStagingDirectory(evidenceDirectory, stagingDirectory) {
  const target = resolve(evidenceDirectory);
  const staging = resolve(stagingDirectory);
  const child = relative(target, staging);
  if (!child || isAbsolute(child) || child.startsWith("..") || !basename(staging).startsWith(".relay-obs-live-staging-")) {
    throw new Error("OBS live staging directory escaped the evidence directory");
  }
}

async function createIsolatedStagingDirectory(evidenceDirectory) {
  const stagingDirectory = await mkdtemp(resolve(evidenceDirectory, ".relay-obs-live-staging-"));
  assertIsolatedStagingDirectory(evidenceDirectory, stagingDirectory);
  await chmod(stagingDirectory, 0o733);
  return stagingDirectory;
}

async function cleanupIsolatedStagingDirectory(evidenceDirectory, stagingDirectory) {
  assertIsolatedStagingDirectory(evidenceDirectory, stagingDirectory);
  await rm(stagingDirectory, { recursive: true, force: true });
}

export async function publishValidatedOBSLiveEvidence({
  evidenceDirectory,
  stagedEvidencePath,
  evidence,
  provenance,
  sourceSnapshot,
  postRunSourceSnapshot,
  harnessSnapshot,
  postRunHarnessSnapshot,
}) {
  if (postRunSourceSnapshot.sha256 !== sourceSnapshot.sha256 || postRunSourceSnapshot.file_count !== sourceSnapshot.file_count) {
    throw new Error("Relay source changed while the OBS live acceptance gate was running");
  }
  if (postRunHarnessSnapshot.sha256 !== harnessSnapshot.sha256 || postRunHarnessSnapshot.file_count !== harnessSnapshot.file_count ||
      evidence.harness_source_snapshot_sha256 !== harnessSnapshot.sha256 ||
      evidence.harness_source_snapshot_file_count !== harnessSnapshot.file_count) {
    throw new Error("OBS acceptance harness changed while the live gate was running or is not bound to the PASS evidence");
  }
  validateWrittenEvidence(evidence, provenance);
  const evidenceName = basename(stagedEvidencePath);
  if (!evidenceNamePattern.test(evidenceName)) throw new Error("OBS live staged evidence filename is invalid");
  const finalEvidencePath = resolve(evidenceDirectory, evidenceName);
  await link(stagedEvidencePath, finalEvidencePath);
  return finalEvidencePath;
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  await validateEvidenceDirectory(args.evidenceDirectory);
  requiredCredentialEnvironment();
  const stagingDirectory = await createIsolatedStagingDirectory(args.evidenceDirectory);
  try {
    const sourceSnapshot = await relaySourceSnapshot();
    const harnessSnapshot = await harnessSourceSnapshot();
    const provenance = await inspectCandidateImage(args.candidateImage, sourceSnapshot);
    const runnerEnvironment = {
      ...process.env,
      RELAY_OBS_LIVE_EVIDENCE_DIR: "/evidence",
      RELAY_OBS_LIVE_SOURCE_REVISION: provenance.sourceRevision,
      RELAY_OBS_LIVE_SOURCE_SNAPSHOT_SHA256: provenance.sourceSnapshotSHA256,
      RELAY_OBS_LIVE_SOURCE_FILE_COUNT: String(provenance.sourceSnapshotFileCount),
      RELAY_OBS_LIVE_IMAGE_DIGEST: provenance.imageDigest,
      RELAY_OBS_LIVE_PROVENANCE_ATTESTATION: OBS_LIVE_HOST_RUNNER_ATTESTATION,
      RELAY_OBS_LIVE_HARNESS_SNAPSHOT_SHA256: harnessSnapshot.sha256,
      RELAY_OBS_LIVE_HARNESS_FILE_COUNT: String(harnessSnapshot.file_count),
    };
    const containerName = `relay-obs-live-${randomUUID()}`;
    const dockerArgs = buildOBSLiveGoRunnerArgs({ evidenceDirectory: stagingDirectory, runnerImage: args.runnerImage, containerName });
    let runResult;
    try {
      runResult = await command("docker", dockerArgs, {
        env: runnerEnvironment,
        allowFailure: true,
        timeoutMs: goGateTimeoutMilliseconds,
      });
      assertOBSLiveRunnerOutputSafe(runResult.stdout, runResult.stderr, [
        process.env.HUAWEI_OBS_ACCESS_KEY_ID,
        process.env.HUAWEI_OBS_SECRET_ACCESS_KEY,
        process.env.HUAWEI_OBS_SECURITY_TOKEN,
        process.env.HUAWEI_OBS_BUCKET,
      ]);
      if (runResult.timedOut) throw new Error("OBS live Go test exceeded its runner timeout");
      if (runResult.code !== 0) throw new Error(`OBS live Go test failed with status ${runResult.code}`);
    } finally {
      const cleanup = await command("docker", ["container", "rm", "--force", containerName], { allowFailure: true, timeoutMs: 30_000 });
      if (cleanup.timedOut) throw new Error("OBS live test container cleanup timed out");
      const residual = await command("docker", ["container", "inspect", containerName], { allowFailure: true, timeoutMs: 30_000 });
      if (residual.timedOut || residual.code === 0) throw new Error("OBS live test container was not removed");
    }
    const stagedNames = await readdir(stagingDirectory);
    if (stagedNames.length !== 1 || !evidenceNamePattern.test(stagedNames[0])) {
      throw new Error("OBS live gate must create exactly one staged evidence file");
    }
    const stagedEvidencePath = resolve(stagingDirectory, stagedNames[0]);
    let evidence;
    try {
      evidence = JSON.parse(await readFile(stagedEvidencePath, "utf8"));
    } catch {
      throw new Error("OBS live gate staged evidence is not valid JSON");
    }
    const afterSnapshot = await relaySourceSnapshot();
    const afterHarnessSnapshot = await harnessSourceSnapshot();
    const evidencePath = await publishValidatedOBSLiveEvidence({
      evidenceDirectory: args.evidenceDirectory,
      stagedEvidencePath,
      evidence,
      provenance,
      sourceSnapshot,
      postRunSourceSnapshot: afterSnapshot,
      harnessSnapshot,
      postRunHarnessSnapshot: afterHarnessSnapshot,
    });
    process.stdout.write(`OBS_LIVE_RUNNER_PASS evidence=${evidencePath} image_id=${provenance.imageDigest} source_snapshot_sha1=${provenance.sourceRevision} source_snapshot_sha256=${provenance.sourceSnapshotSHA256} source_file_count=${provenance.sourceSnapshotFileCount} harness_snapshot_sha256=${harnessSnapshot.sha256} harness_file_count=${harnessSnapshot.file_count}\n`);
  } finally {
    await cleanupIsolatedStagingDirectory(args.evidenceDirectory, stagingDirectory);
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`OBS_LIVE_RUNNER_FAIL ${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
