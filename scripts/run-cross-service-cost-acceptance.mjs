#!/usr/bin/env node

import { createHash, randomBytes, randomUUID } from "node:crypto";
import { execFile as execFileCallback, spawn } from "node:child_process";
import { createServer } from "node:net";
import {
  chmod,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import {
  basename,
  dirname,
  posix as posixPath,
  relative,
  resolve,
  sep,
  win32 as win32Path,
} from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import {
  CANDIDATE_IMAGE_LABELS,
  assertSecretFreeEvidence,
  harnessSourceSnapshot,
  relaySourceSnapshot,
  validateCandidateImageLabels,
  validateRouteAcceptanceTrustDigest,
} from "./relay-fault-source-snapshot.mjs";
import {
  CANDIDATE_UPSTREAM_GIT_REVISION,
  canonicalJson,
} from "./relay-migration-acceptance.mjs";

const execFile = promisify(execFileCallback);
const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const platformRoot = resolve(workspace, "backend", "platform");
const evidenceKind = "relay_platform_cross_service_cost_acceptance";
const snapshotFormat = "sorted-portable-path-nul-content-nul-v1";
const postgresImage = "postgres:16-alpine";
const redisImage = "redis:7-alpine";
const ignoredPlatformDirectories = new Set([
  ".git",
  ".mypy_cache",
  ".nox",
  ".pytest_cache",
  ".ruff_cache",
  ".tox",
  ".venv",
  "__pycache__",
  "htmlcov",
  "venv",
]);
const ignoredGeneratedPlatformTestDirectories = new Set([
  "artifact",
  "artifacts",
  "evidence",
  "temp",
  "tmp",
]);
const platformRootFiles = new Set([
  ".dockerignore",
  ".env.example",
  "Dockerfile",
  "README.md",
  "alembic.ini",
  "requirements.txt",
]);

function portable(path) {
  return path.split(sep).join("/");
}

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function safeEqual(left, right) {
  return canonicalJson(left) === canonicalJson(right);
}

function assertNoForbiddenText(text, forbiddenValues) {
  for (const forbidden of forbiddenValues) {
    if (typeof forbidden === "string" && forbidden.length >= 6 && text.includes(forbidden)) {
      throw new Error("acceptance process output exposed a protected runtime value");
    }
  }
}

const redactedDiagnosticProperty = "redactedAcceptanceDiagnostic";
const diagnosticCleanupProperty = "redactedDiagnosticCleanupVerified";

function outputText(value) {
  if (typeof value === "string") return value;
  if (Buffer.isBuffer(value) || value instanceof Uint8Array) return Buffer.from(value).toString("utf8");
  return "";
}

export function sensitiveEnvironmentValues(environment = {}) {
  const sensitiveName = /(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|authorization|cookie|session|dsn|connection[_-]?string|(?:database|redis|sql)[_-]?url)/i;
  return [...new Set(Object.entries(environment)
    .filter(([name, value]) => sensitiveName.test(name) && typeof value === "string" && value.length >= 6)
    .map(([, value]) => value))];
}

export function redactAcceptanceDiagnostic(stdout, stderr, forbiddenValues = [], maxChars = 12 * 1024) {
  const sections = [];
  const stdoutText = outputText(stdout);
  const stderrText = outputText(stderr);
  if (stdoutText) sections.push(`[pytest stdout]\n${stdoutText}`);
  if (stderrText) sections.push(`[pytest stderr]\n${stderrText}`);
  if (sections.length === 0) return "";

  const protectedValues = [...new Set(forbiddenValues
    .filter((value) => typeof value === "string" && value.length >= 6))]
    .sort((left, right) => right.length - left.length);
  let diagnostic = sections.join("\n");
  for (const protectedValue of protectedValues) {
    diagnostic = diagnostic.split(protectedValue).join("[REDACTED]");
  }
  diagnostic = diagnostic
    .replace(/\x1B\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, "Bearer [REDACTED]")
    .replace(/([A-Za-z][A-Za-z0-9+.-]*:\/\/)[^/@\s]+:[^/@\s]+@/g, "$1[REDACTED]@")
    .replace(/\b((?:[A-Za-z0-9]+[_-])*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|authorization|cookie|signature)\b\s*(?:=|:)\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)/gi, "$1[REDACTED]")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .replace(/\r\n?/g, "\n")
    .trimEnd();
  assertNoForbiddenText(diagnostic, protectedValues);
  assertSecretFreeEvidence(diagnostic, protectedValues);

  const limit = Number.isInteger(maxChars) && maxChars > 0 ? maxChars : 12 * 1024;
  if (diagnostic.length > limit) {
    return `[output truncated; final ${limit} characters follow]\n${diagnostic.slice(-limit)}`;
  }
  return diagnostic;
}

export function formatAcceptanceFailure(error) {
  const message = error instanceof Error
    ? error.message.replace(/[\r\n\t]+/g, " ").slice(0, 240)
    : "unknown failure";
  let output = `cross-service cost acceptance failed: ${message}\n`;
  if (
    error &&
    error[diagnosticCleanupProperty] === true &&
    typeof error[redactedDiagnosticProperty] === "string" &&
    error[redactedDiagnosticProperty].length > 0
  ) {
    output += `redacted pytest output (runner cleanup verified):\n${error[redactedDiagnosticProperty]}\n`;
  }
  return output;
}

export function platformSnapshotDirectoryIgnored(name, scope) {
  const normalized = String(name).toLowerCase();
  if (ignoredPlatformDirectories.has(normalized)) return true;
  return scope === "tests" && (
    normalized.startsWith("_tmp") ||
    ignoredGeneratedPlatformTestDirectories.has(normalized)
  );
}

async function walk(directory, scope) {
  const files = [];
  const entries = await readdir(directory, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
  for (const entry of entries) {
    if (entry.isDirectory() && platformSnapshotDirectoryIgnored(entry.name, scope)) continue;
    const absolute = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(absolute, scope));
    else if (entry.isFile() && !entry.name.endsWith(".pyc")) files.push(absolute);
  }
  return files;
}

async function hashWorkspaceFiles(files) {
  const normalized = [...files].sort((left, right) => left.localeCompare(right, "en"));
  const sha1Hash = createHash("sha1");
  const sha256Hash = createHash("sha256");
  for (const file of normalized) {
    const contents = await readFile(resolve(workspace, file));
    for (const hash of [sha1Hash, sha256Hash]) {
      hash.update(file);
      hash.update("\0");
      hash.update(contents);
      hash.update("\0");
    }
  }
  return {
    format: snapshotFormat,
    files: normalized,
    file_count: normalized.length,
    sha1: sha1Hash.digest("hex"),
    sha256: `sha256:${sha256Hash.digest("hex")}`,
  };
}

export async function platformSourceSnapshot() {
  const files = [];
  for (const file of platformRootFiles) {
    files.push(portable(relative(workspace, resolve(platformRoot, file))));
  }
  for (const directory of ["platform_api", "migrations", "tests"]) {
    for (const absolute of await walk(resolve(platformRoot, directory), directory)) {
      files.push(portable(relative(workspace, absolute)));
    }
  }
  return hashWorkspaceFiles(files);
}

function parseArgs(argv) {
  const output = { candidateImage: "", out: "", python: process.env.COST_ACCEPTANCE_PYTHON || "python" };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (flag === "--candidate-image" && value) output.candidateImage = value;
    else if (flag === "--out" && value) output.out = value;
    else if (flag === "--python" && value) output.python = value;
    else throw new Error(`unknown or incomplete argument ${flag}`);
    index += 1;
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,300}$/.test(output.candidateImage)) {
    throw new Error("--candidate-image must name an existing local candidate image");
  }
  if (!output.python || /[\r\n\0]/.test(output.python)) throw new Error("Python executable is invalid");
  return output;
}

async function runCommand(command, args, options = {}) {
  try {
    const result = await execFile(command, args, {
      cwd: options.cwd || workspace,
      env: options.env || process.env,
      timeout: options.timeoutMs || 120_000,
      maxBuffer: options.maxBuffer || 8 * 1024 * 1024,
      windowsHide: true,
      encoding: "utf8",
    });
    return { stdout: result.stdout || "", stderr: result.stderr || "" };
  } catch (error) {
    const commandError = new Error(options.failure || `${basename(command)} command failed`);
    if (Array.isArray(options.diagnosticForbiddenValues)) {
      try {
        const diagnostic = redactAcceptanceDiagnostic(
          error?.stdout,
          error?.stderr,
          options.diagnosticForbiddenValues,
        );
        if (diagnostic) {
          Object.defineProperty(commandError, redactedDiagnosticProperty, {
            configurable: false,
            enumerable: false,
            value: diagnostic,
            writable: false,
          });
        }
      } catch {
        // Fail closed: retain the generic command error rather than risk unsafe output.
      }
    }
    throw commandError;
  }
}

export function parseCanonicalPythonExecutable(stdout, platform = process.platform) {
  if (typeof stdout !== "string" || stdout.includes("\0")) {
    throw new Error("Python executable resolution returned invalid output");
  }
  let line = stdout.replace(/\r\n/g, "\n");
  if (line.endsWith("\n")) line = line.slice(0, -1);
  if (!line || line.includes("\r") || line.includes("\n")) {
    throw new Error("Python executable resolution did not return exactly one line");
  }
  const pathApi = platform === "win32" ? win32Path : posixPath;
  if (!pathApi.isAbsolute(line)) {
    throw new Error("Python executable resolution did not return an absolute path");
  }
  return line;
}

export async function resolveCanonicalPythonExecutable(
  requested,
  {
    platform = process.platform,
    run = runCommand,
    inspect = stat,
  } = {},
) {
  const { stdout } = await run(requested, [
    "-I",
    "-S",
    "-c",
    "import os, sys; print(os.path.realpath(sys.executable) if sys.platform == 'win32' else os.path.abspath(sys.executable), flush=True)",
  ], { failure: "Python executable could not be resolved" });
  const executable = parseCanonicalPythonExecutable(stdout, platform);
  try {
    const inspected = await inspect(executable);
    if (!inspected.isFile()) throw new Error("not a regular file");
  } catch {
    throw new Error("resolved Python executable is not a regular file");
  }
  return executable;
}

async function commandSucceeds(command, args, options = {}) {
  try {
    await execFile(command, args, {
      cwd: options.cwd || workspace,
      env: options.env || process.env,
      timeout: options.timeoutMs || 30_000,
      maxBuffer: 1024 * 1024,
      windowsHide: true,
      encoding: "utf8",
    });
    return true;
  } catch {
    return false;
  }
}

async function inspectDocker(kind, reference) {
  const { stdout } = await runCommand(
    "docker",
    [kind, "inspect", reference],
    { failure: `Docker ${kind} inspection failed` },
  );
  const parsed = JSON.parse(stdout);
  if (!Array.isArray(parsed) || parsed.length !== 1) throw new Error(`Docker ${kind} inspection was ambiguous`);
  return parsed[0];
}

export function validateRunnerOwnedVolumeBinding(container, volume, expected) {
  const errors = [];
  const labels = volume?.Labels || {};
  if (volume?.Name !== expected.name) errors.push("runner-owned volume name does not match");
  if (volume?.Driver !== "local" || volume?.Scope !== "local") {
    errors.push("runner-owned volume is not a local Docker volume");
  }
  if (labels["ai.video.acceptance-run"] !== expected.runSuffix) {
    errors.push("runner-owned volume run label does not match");
  }
  if (labels["ai.video.acceptance-role"] !== expected.role) {
    errors.push("runner-owned volume role label does not match");
  }
  if (container?.State?.Running !== true) {
    errors.push("runner-owned volume container is not running");
  }
  const volumeMounts = Array.isArray(container?.Mounts)
    ? container.Mounts.filter((mount) => mount?.Type === "volume")
    : [];
  if (volumeMounts.length !== 1) {
    errors.push("runner-owned data container does not have exactly one Docker volume");
  } else {
    const mount = volumeMounts[0];
    if (mount.Name !== expected.name) errors.push("runner-owned data mount uses an unexpected volume");
    if (mount.Destination !== expected.destination) {
      errors.push("runner-owned data mount uses an unexpected destination");
    }
    if (mount.RW !== true) errors.push("runner-owned data mount is not read-write");
  }
  return errors;
}

function isIPv4Address(value) {
  const octets = typeof value === "string" ? value.split(".") : [];
  return octets.length === 4 && octets.every((octet) => (
    /^\d{1,3}$/.test(octet) && Number(octet) >= 0 && Number(octet) <= 255
  ));
}

export function runnerPlatformBinding(platform, network) {
  if (platform !== "linux") {
    return {
      bindHost: "127.0.0.1",
      containerHostAddress: "host-gateway",
      probeHost: "127.0.0.1",
    };
  }
  const gateways = (Array.isArray(network?.IPAM?.Config) ? network.IPAM.Config : [])
    .map((config) => config?.Gateway)
    .filter(isIPv4Address);
  if (gateways.length !== 1 || gateways[0].startsWith("127.")) {
    throw new Error("runner-owned Docker network does not have one safe IPv4 gateway");
  }
  return {
    bindHost: "0.0.0.0",
    containerHostAddress: gateways[0],
    probeHost: "127.0.0.1",
  };
}

async function createRunnerOwnedVolume(volume, runSuffix) {
  const { stdout } = await runCommand("docker", [
    "volume", "create",
    ...acceptanceResourceLabels(runSuffix),
    "--label", `ai.video.acceptance-role=${volume.role}`,
    volume.name,
  ], { failure: "runner-owned acceptance volume could not be created" });
  if (stdout.trim() !== volume.name) {
    throw new Error("Docker did not return the exact runner-owned volume name");
  }
}

async function assertRunnerOwnedVolumeBinding(containerName, volume, runSuffix) {
  const [container, inspectedVolume] = await Promise.all([
    inspectDocker("container", containerName),
    inspectDocker("volume", volume.name),
  ]);
  const errors = validateRunnerOwnedVolumeBinding(container, inspectedVolume, {
    ...volume,
    runSuffix,
  });
  if (errors.length > 0) throw new Error(errors[0]);
}

async function publishedPort(container, privatePort) {
  const { stdout } = await runCommand(
    "docker",
    ["port", container, `${privatePort}/tcp`],
    { failure: "Docker published-port inspection failed" },
  );
  const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
  const match = lines.find((line) => line.startsWith("127.0.0.1:"))?.match(/:(\d+)$/);
  if (!match) throw new Error("acceptance container does not have an exact loopback port binding");
  return Number(match[1]);
}

async function availablePort(host = "127.0.0.1") {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, host, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => error ? reject(error) : resolvePort(port));
    });
  });
}

function envFileContents(values) {
  return Object.entries(values).map(([key, value]) => {
    const text = String(value);
    if (!/^[A-Z][A-Z0-9_]*$/.test(key) || /[\r\n\0]/.test(text)) {
      throw new Error("acceptance environment contains an invalid value");
    }
    return `${key}=${text}`;
  }).join("\n") + "\n";
}

export function acceptanceResourceLabels(runSuffix, environment = process.env) {
  const labels = ["--label", `ai.video.acceptance-run=${runSuffix}`];
  const ciRun = environment.COST_ACCEPTANCE_RUN_LABEL?.trim();
  if (ciRun) {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(ciRun)) {
      throw new Error("COST_ACCEPTANCE_RUN_LABEL is invalid");
    }
    labels.push("--label", `ai.video.acceptance-ci-run=${ciRun}`);
  }
  return labels;
}

async function writePrivateFile(path, contents) {
  await writeFile(path, contents, { flag: "wx", mode: 0o600 });
  try {
    await applyPrivateEvidencePermissions(path);
  } catch (error) {
    await rm(path, { force: true });
    throw error;
  }
}

async function waitForDockerPostgres(container, database, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await commandSucceeds("docker", ["exec", container, "pg_isready", "-U", "cost_acceptance", "-d", database])) return;
    await new Promise((resolveWait) => setTimeout(resolveWait, 250));
  }
  throw new Error("acceptance PostgreSQL did not become ready");
}

function collectChildOutput(child) {
  const chunks = [];
  let size = 0;
  const append = (chunk) => {
    if (size >= 64 * 1024) return;
    const buffer = Buffer.from(chunk);
    chunks.push(buffer.subarray(0, 64 * 1024 - size));
    size += buffer.length;
  };
  child.stdout?.on("data", append);
  child.stderr?.on("data", append);
  return () => Buffer.concat(chunks).toString("utf8");
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    cache: "no-store",
    signal: AbortSignal.timeout(options.timeoutMs || 3_000),
  });
  const raw = await response.text();
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    throw new Error("acceptance endpoint returned malformed JSON");
  }
  return { response, raw, body };
}

export function evaluatePlatformProbeBinding(
  { httpStatus, nonceHeader, pidHeader, body },
  { nonce, pid },
) {
  return {
    fetch_ok: true,
    http_status: httpStatus,
    nonce_bound: safeEqual(nonceHeader, nonce),
    pid_bound: safeEqual(pidHeader, String(pid)),
    body_bound:
      body?.status === "ok" &&
      body?.service === "customer-platform",
  };
}

async function waitForPlatformProbe(url, nonce, pid, child, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  let observation = {
    fetch_ok: false,
    http_status: 0,
    nonce_bound: false,
    pid_bound: false,
    body_bound: false,
  };
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error("runner-started Platform process exited before its probe");
    try {
      const result = await fetchJSON(`${url}/health/live`);
      observation = evaluatePlatformProbeBinding({
        httpStatus: result.response.status,
        nonceHeader: result.response.headers.get("x-cost-acceptance-process-nonce"),
        pidHeader: result.response.headers.get("x-cost-acceptance-process-pid"),
        body: result.body,
      }, { nonce, pid });
      if (
        observation.http_status === 200 &&
        observation.nonce_bound &&
        observation.pid_bound &&
        observation.body_bound
      ) {
        return {
          pid,
          probe_nonce_sha256: sha256(nonce),
          probe_body_sha256: sha256(result.raw),
          response_bound_to_process: true,
        };
      }
    } catch {
      // The one runner-owned process is still starting.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 200));
  }
  throw new Error(
    "runner-started Platform process did not return its bound probe " +
    `(fetch_ok=${observation.fetch_ok};http_status=${observation.http_status};` +
    `nonce_bound=${observation.nonce_bound};pid_bound=${observation.pid_bound};` +
    `body_bound=${observation.body_bound})`,
  );
}

export function validateRuntimeIdentity(identity, relaySnapshot, imageId, labels) {
  const errors = [];
  if (identity?.schema_version !== 1 || identity?.kind !== "relay_runtime_build_identity") {
    errors.push("runtime identity envelope is invalid");
    return errors;
  }
  const candidate = identity.candidate;
  if (!candidate || typeof candidate !== "object") {
    errors.push("runtime identity candidate is absent");
    return errors;
  }
  const expected = {
    upstream_git_revision: CANDIDATE_UPSTREAM_GIT_REVISION,
    source_git_revision: relaySnapshot.sha1,
    source_snapshot_sha256: relaySnapshot.sha256,
    source_snapshot_file_count: relaySnapshot.file_count,
    image_digest: imageId,
  };
  for (const [key, value] of Object.entries(expected)) {
    if (candidate[key] !== value) errors.push(`runtime identity ${key} does not match the frozen candidate`);
  }
  if (typeof candidate.instance_id !== "string" || candidate.instance_id.length < 16) {
    errors.push("runtime identity instance_id is invalid");
  }
  if (labels?.[CANDIDATE_IMAGE_LABELS.sourceRevision] !== candidate.source_git_revision) {
    errors.push("runtime source revision does not match the candidate label");
  }
  if (labels?.[CANDIDATE_IMAGE_LABELS.sourceSnapshot] !== candidate.source_snapshot_sha256) {
    errors.push("runtime source digest does not match the candidate label");
  }
  if (labels?.[CANDIDATE_IMAGE_LABELS.sourceFileCount] !== String(candidate.source_snapshot_file_count)) {
    errors.push("runtime source file count does not match the candidate label");
  }
  if (labels?.[CANDIDATE_IMAGE_LABELS.upstreamRevision] !== candidate.upstream_git_revision) {
    errors.push("runtime upstream revision does not match the candidate label");
  }
  return errors;
}

export function parseCompiledBuildIdentity(stdout, stderr = "") {
  if (stderr.trim() !== "") throw new Error("compiled Relay identity wrote unexpected diagnostics");
  const lines = stdout.trim().split(/\r?\n/);
  if (lines.length !== 1) throw new Error("compiled Relay identity must be exactly one JSON line");
  let value;
  try {
    value = JSON.parse(lines[0]);
  } catch {
    throw new Error("compiled Relay identity is malformed");
  }
  const keys = Object.keys(value || {}).sort();
  const expectedKeys = [
    "kind",
    "route_acceptance_trust_keys_sha256",
    "schema_version",
    "source_revision",
    "source_snapshot_file_count",
    "source_snapshot_sha256",
    "upstream_git_revision",
  ].sort();
  if (!safeEqual(keys, expectedKeys) || value.schema_version !== 1 || value.kind !== "relay_compiled_build_identity") {
    throw new Error("compiled Relay identity schema is invalid");
  }
  if (
    !/^[0-9a-f]{40}$/.test(value.upstream_git_revision) ||
    !/^[0-9a-f]{40}$/.test(value.source_revision) ||
    !/^sha256:[0-9a-f]{64}$/.test(value.source_snapshot_sha256) ||
    !/^sha256:[0-9a-f]{64}$/.test(value.route_acceptance_trust_keys_sha256) ||
    value.route_acceptance_trust_keys_sha256 === `sha256:${"0".repeat(64)}` ||
    !Number.isInteger(value.source_snapshot_file_count) ||
    value.source_snapshot_file_count < 1
  ) {
    throw new Error("compiled Relay identity values are invalid");
  }
  return value;
}

async function compiledBuildIdentity(imageId, containerName, relaySnapshot) {
  const result = await runCommand("docker", [
    "run", "--rm", "--name", containerName,
    "--network", "none",
    "--read-only",
    "--user", "65532:65532",
    "--pids-limit", "32",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges:true",
    "--entrypoint", "/new-api",
    imageId,
    "relay-build-identity",
  ], { timeoutMs: 30_000, failure: "compiled Relay identity command failed" });
  const identity = parseCompiledBuildIdentity(result.stdout, result.stderr);
  const expected = {
    schema_version: 1,
    kind: "relay_compiled_build_identity",
    upstream_git_revision: CANDIDATE_UPSTREAM_GIT_REVISION,
    source_revision: relaySnapshot.sha1,
    source_snapshot_sha256: relaySnapshot.sha256,
    source_snapshot_file_count: relaySnapshot.file_count,
    route_acceptance_trust_keys_sha256: validateRouteAcceptanceTrustDigest(
      process.env.NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256,
    ),
  };
  if (!safeEqual(identity, expected)) throw new Error("compiled Relay identity does not match the frozen host source");
  return identity;
}

function classifyRelayStartupFailure(logText) {
  if (/permission denied|read-only file system/i.test(logText)) return "runtime-filesystem-policy";
  if (/migration|migrat(?:e|ing|ion)/i.test(logText)) return "database-migration";
  if (/connection refused|no such host|database.*(?:failed|error)|redis.*(?:failed|error)/i.test(logText)) {
    return "dependency-connectivity";
  }
  if (/configuration|config.*invalid|requires?\s/i.test(logText)) return "configuration-gate";
  return "process-exit";
}

export function parseRelaySchemaMigrationResult(stdout) {
  if (typeof stdout !== "string" || stdout.includes("\0")) {
    throw new Error("Relay schema migration output is invalid");
  }
  const canonical = stdout.replace(/\r\n/g, "\n").trimEnd();
  if (!canonical || canonical.includes("\r") || canonical.includes("\n")) {
    throw new Error("Relay schema migration did not return exactly one JSON document");
  }
  let value;
  try {
    value = JSON.parse(canonical);
  } catch {
    throw new Error("Relay schema migration output is not JSON");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Relay schema migration result is invalid");
  }
  const requiredKeys = ["from_version", "kind", "schema_version", "state", "status", "to_version"];
  const allowedKeys = [...requiredKeys, "attempt_id"].sort();
  const keys = Object.keys(value).sort();
  if (
    requiredKeys.some((key) => !keys.includes(key)) ||
    keys.some((key) => !allowedKeys.includes(key)) ||
    value.schema_version !== 1 ||
    value.kind !== "relay_schema_migration" ||
    !["current", "migrated"].includes(value.state) ||
    !Number.isSafeInteger(value.from_version) || value.from_version < 0 ||
    !Number.isSafeInteger(value.to_version) || value.to_version < 1 ||
    value.to_version < value.from_version ||
    !value.status || typeof value.status !== "object" || Array.isArray(value.status) ||
    value.status.classification !== "current" ||
    value.status.state !== "clean" ||
    value.status.dirty !== false ||
    value.status.compatible !== true ||
    value.status.current !== true ||
    value.status.current_version !== value.to_version ||
    (value.state === "current" && value.from_version !== value.to_version) ||
    (value.state === "migrated" && value.from_version >= value.to_version)
  ) {
    throw new Error("Relay schema migration result is invalid");
  }
  return value;
}

export function parseDockerWaitExitCode(stdout) {
  if (typeof stdout !== "string" || stdout.includes("\0")) {
    throw new Error("Relay schema migration wait result is invalid");
  }
  const canonical = stdout.replace(/\r\n/g, "\n");
  const match = canonical.match(/^(0|[1-9]\d{0,2})\n?$/);
  if (!match) throw new Error("Relay schema migration wait result is invalid");
  const exitCode = Number(match[1]);
  if (exitCode > 255) throw new Error("Relay schema migration wait result is invalid");
  return exitCode;
}

async function waitForRelaySchemaMigration(container, forbiddenValues, timeoutMs = 180_000) {
  const wait = await runCommand("docker", ["wait", container], {
    timeoutMs,
    failure: "Relay schema migration container did not exit within its deadline",
    diagnosticForbiddenValues: forbiddenValues,
  });
  const exitCode = parseDockerWaitExitCode(wait.stdout);
  if (wait.stderr.trim() !== "") {
    throw new Error("Relay schema migration wait returned diagnostics");
  }

  const inspected = await inspectDocker("container", container);
  if (
    inspected.State?.Running !== false ||
    inspected.State?.Status !== "exited" ||
    inspected.State?.ExitCode !== exitCode
  ) {
    throw new Error("Relay schema migration container exit state is inconsistent");
  }

  const logs = await runCommand("docker", ["logs", container], {
    failure: "Relay schema migration logs could not be audited",
    diagnosticForbiddenValues: forbiddenValues,
  });
  assertNoForbiddenText(`${logs.stdout}\n${logs.stderr}`, forbiddenValues);

  let result;
  let failure;
  if (exitCode !== 0) {
    failure = new Error(
      `candidate Relay schema migration failed (${classifyRelayStartupFailure(`${logs.stdout}\n${logs.stderr}`)})`,
    );
  } else {
    try {
      result = parseRelaySchemaMigrationResult(logs.stdout);
    } catch (error) {
      failure = error;
    }
  }
  if (failure) {
    try {
      const diagnostic = redactAcceptanceDiagnostic(logs.stdout, logs.stderr, forbiddenValues);
      if (diagnostic) {
        Object.defineProperty(failure, redactedDiagnosticProperty, {
          configurable: false,
          enumerable: false,
          value: diagnostic,
          writable: false,
        });
      }
    } catch {
      // Fail closed with the generic migration failure when its logs cannot be
      // proven safe for cleanup-gated disclosure.
    }
    throw failure;
  }
  return result;
}

async function assertRelayStillRunning(container, forbiddenValues) {
  const inspected = await inspectDocker("container", container);
  if (inspected.State?.Running === true) return;
  const logs = await runCommand("docker", ["logs", container], {
    failure: "candidate Relay startup logs could not be audited",
  });
  const text = `${logs.stdout}\n${logs.stderr}`;
  assertNoForbiddenText(text, forbiddenValues);
  throw new Error(`candidate Relay exited during startup (${classifyRelayStartupFailure(text)})`);
}

async function waitForRelayRuntimeBound(
  url,
  admissionValue,
  relaySnapshot,
  imageId,
  labels,
  container,
  forbiddenValues,
  timeoutMs = 90_000,
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await assertRelayStillRunning(container, forbiddenValues);
    try {
      const result = await fetchJSON(`${url}/internal/platform-relay/runtime-build-identity`, {
        headers: { "X-Relay-Internal-Admission": admissionValue },
      });
      if (result.response.status === 200) {
        const errors = validateRuntimeIdentity(result.body, relaySnapshot, imageId, labels);
        if (errors.length > 0) throw new Error(errors[0]);
        if (!/no-store/i.test(result.response.headers.get("cache-control") || "")) {
          throw new Error("runtime identity response is cacheable");
        }
        return result.body;
      }
    } catch (error) {
      if (error instanceof Error && /does not match|invalid|cacheable/.test(error.message)) throw error;
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 250));
  }
  throw new Error("candidate Relay runtime identity did not become available");
}

async function assertRuntimeIdentityIsProtected(url, relaySnapshot) {
  for (const headers of [
    {},
    { "X-Relay-Internal-Admission": "deliberately-invalid-runtime-admission" },
  ]) {
    const result = await fetchJSON(`${url}/internal/platform-relay/runtime-build-identity`, { headers });
    if (
      result.response.status !== 401 ||
      result.raw.includes(relaySnapshot.sha1) ||
      result.raw.includes(relaySnapshot.sha256)
    ) {
      throw new Error("Relay runtime identity endpoint is not admission protected");
    }
  }
}

function stableRuntimeIdentity(identity) {
  const candidate = identity.candidate;
  return {
    upstream_git_revision: candidate.upstream_git_revision,
    source_git_revision: candidate.source_git_revision,
    source_snapshot_sha256: candidate.source_snapshot_sha256,
    source_snapshot_file_count: candidate.source_snapshot_file_count,
    image_digest: candidate.image_digest,
  };
}

function parseRedisInfo(raw) {
  const values = {};
  for (const line of raw.split(/\r?\n/)) {
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf(":");
    if (separator > 0) values[line.slice(0, separator)] = line.slice(separator + 1).trim();
  }
  for (const required of ["redis_version", "redis_mode", "run_id", "process_id", "tcp_port"]) {
    if (!values[required]) throw new Error("Redis fingerprint is incomplete");
  }
  return {
    redis_version: values.redis_version,
    redis_mode: values.redis_mode,
    run_id_sha256: sha256(values.run_id),
    process_id: Number(values.process_id),
    tcp_port: Number(values.tcp_port),
  };
}

async function redisFingerprint(container) {
  const { stdout } = await runCommand(
    "docker",
    [
      "exec",
      container,
      "sh",
      "-ec",
      "redis-cli --no-auth-warning -a \"$REDIS_PASSWORD\" --raw PING >/dev/null && exec redis-cli --no-auth-warning -a \"$REDIS_PASSWORD\" --raw INFO server",
    ],
    { failure: "acceptance Redis fingerprint failed" },
  );
  return parseRedisInfo(stdout);
}

async function postgresFingerprints(python, platformUrl, relayUrl) {
  const { stdout } = await runCommand(
    python,
    [resolve(workspace, "scripts", "collect-cost-acceptance-postgres.py")],
    {
      env: {
        ...process.env,
        COST_ACCEPTANCE_PLATFORM_DATABASE_URL: platformUrl,
        COST_ACCEPTANCE_RELAY_DATABASE_URL: relayUrl,
      },
      failure: "PostgreSQL fingerprint gate failed",
    },
  );
  const value = JSON.parse(stdout.trim());
  if (!value?.platform || !value?.relay) throw new Error("PostgreSQL fingerprints are incomplete");
  if (value.platform.system_identifier_sha256 === value.relay.system_identifier_sha256) {
    throw new Error("cost acceptance did not use two distinct PostgreSQL instances");
  }
  return value;
}

async function seedRelayNativeChannel(
  python,
  relayDatabaseUrl,
  { channelId, channelKey, model, providerCredentialKeyring },
) {
  const script = String.raw`
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


def reject_duplicate_keys(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate keyring field")
        output[key] = value
    return output


def assert_plaintext_guard(connection, channel_id, model):
    try:
        with connection.begin_nested():
            connection.execute(
                text("""
                    INSERT INTO channels
                        (id, type, \"key\", status, name, models, \"group\", created_time)
                    VALUES
                        (:id, 50, 'guard-probe-plaintext-must-fail', 2,
                         'cost-acceptance-guard-probe', :model, 'default', :created_time)
                """),
                {"id": channel_id + 1, "model": model, "created_time": int(time.time())},
            )
    except DBAPIError as error:
        diagnostic = getattr(error.orig, "diag", None)
        if (
            getattr(error.orig, "sqlstate", None) != "P0001"
            or getattr(diagnostic, "message_primary", None)
            != "plaintext provider channel credentials are forbidden"
        ):
            raise RuntimeError("Relay plaintext credential guard returned an unexpected rejection") from None
    else:
        raise RuntimeError("Relay plaintext credential guard accepted a forbidden write")

engine = create_engine(os.environ["COST_ACCEPTANCE_RELAY_DATABASE_URL"], pool_pre_ping=True)
deadline = time.monotonic() + 90
while True:
    try:
        with engine.connect() as connection:
            ready = connection.scalar(text("SELECT to_regclass('public.channels') IS NOT NULL"))
        if ready:
            break
    except Exception:
        pass
    if time.monotonic() >= deadline:
        raise RuntimeError("Relay channels schema did not become ready")
    time.sleep(0.25)

keyring = json.loads(
    os.environ["COST_ACCEPTANCE_RELAY_PROVIDER_CREDENTIAL_KEYRING"],
    object_pairs_hook=reject_duplicate_keys,
)
if set(keyring) != {"schema_version", "active_key_id", "keys"}:
    raise RuntimeError("Relay provider credential keyring envelope is invalid")
key_id = keyring["active_key_id"]
keys = keyring["keys"]
if (
    keyring["schema_version"] != 1
    or not isinstance(key_id, str)
    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", key_id) is None
    or not isinstance(keys, dict)
    or set(keys) != {key_id}
):
    raise RuntimeError("Relay provider credential keyring metadata is invalid")
encoded_kek = keys[key_id]
try:
    kek = base64.b64decode(encoded_kek, validate=True)
except Exception:
    raise RuntimeError("Relay provider credential KEK encoding is invalid") from None
if len(kek) != 32 or base64.b64encode(kek).decode("ascii") != encoded_kek or len(set(kek)) < 8:
    raise RuntimeError("Relay provider credential KEK is invalid")

channel_id = int(os.environ["COST_ACCEPTANCE_RELAY_CHANNEL_ID"])
channel_key = os.environ["COST_ACCEPTANCE_RELAY_CHANNEL_KEY"]
model = os.environ["COST_ACCEPTANCE_RELAY_MODEL"]
if not channel_key or channel_key.startswith("[") or "\n" in channel_key:
    raise RuntimeError("Relay native channel credential fixture is invalid")
credential_set_version = str(uuid.uuid4())
schema_version = 1
key_set_fingerprint = hashlib.sha256(channel_key.encode("utf-8")).hexdigest()
key_count = 1
nonce = os.urandom(12)
aad = b"\x00".join([
    b"new-api-provider-channel-credential-set",
    str(schema_version).encode("ascii"),
    credential_set_version.encode("ascii"),
    str(channel_id).encode("ascii"),
    key_id.encode("ascii"),
    key_set_fingerprint.encode("ascii"),
    str(key_count).encode("ascii"),
])
ciphertext = AESGCM(kek).encrypt(nonce, channel_key.encode("utf-8"), aad)

with engine.begin() as connection:
    guards = connection.execute(text("""
        SELECT tgname, tgenabled
          FROM pg_trigger
         WHERE tgrelid IN (
                   'public.channels'::regclass,
                   'public.provider_channel_credential_set_versions'::regclass
               )
           AND NOT tgisinternal
           AND tgname IN (
                   'trg_channels_provider_credential_storage',
                   'trg_provider_channel_credential_set_versions_no_update_delete',
                   'trg_provider_channel_credential_set_versions_no_truncate'
               )
         ORDER BY tgname
    """)).all()
    if len(guards) != 3 or any(enabled != "O" for _, enabled in guards):
        raise RuntimeError("Relay provider channel credential guards are not active")
    assert_plaintext_guard(connection, channel_id, model)

    connection.execute(
        text("""
            INSERT INTO provider_channel_credential_set_versions
                (credential_set_version, channel_id, key_id, schema_version,
                 key_set_fingerprint, key_count, nonce, ciphertext, created_at)
            VALUES
                (:credential_set_version, :channel_id, :key_id, :schema_version,
                 :key_set_fingerprint, :key_count, :nonce, :ciphertext, :created_at)
        """),
        {
            "credential_set_version": credential_set_version,
            "channel_id": channel_id,
            "key_id": key_id,
            "schema_version": schema_version,
            "key_set_fingerprint": key_set_fingerprint,
            "key_count": key_count,
            "nonce": nonce,
            "ciphertext": ciphertext,
            "created_at": datetime.now(timezone.utc),
        },
    )
    connection.execute(
        text("""
            INSERT INTO channels
                (id, type, \"key\", credential_set_version, status, name,
                 models, \"group\", created_time)
            VALUES
                (:id, 50, '', :credential_set_version, 2,
                 'cost-acceptance-native-channel', :model, 'default', :created_time)
        """),
        {
            "id": channel_id,
            "credential_set_version": credential_set_version,
            "model": model,
            "created_time": int(time.time()),
        },
    )
    row = connection.execute(
        text("""
            SELECT channels.id, channels.type, channels.\"key\", channels.status,
                   channels.credential_set_version, versions.key_id,
                   versions.schema_version, versions.key_set_fingerprint,
                   versions.key_count, versions.nonce, versions.ciphertext
              FROM channels
              JOIN provider_channel_credential_set_versions versions
                ON versions.credential_set_version = channels.credential_set_version
               AND versions.channel_id = channels.id
             WHERE channels.id = :id
        """),
        {"id": channel_id},
    ).mappings().one()
    assert row["type"] == 50 and row["status"] == 2
    assert row["key"] == "" and row["credential_set_version"] == credential_set_version
    assert row["key_id"] == key_id and row["schema_version"] == schema_version
    assert row["key_set_fingerprint"] == key_set_fingerprint and row["key_count"] == key_count
    restored = AESGCM(kek).decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), aad)
    assert hmac.compare_digest(restored, channel_key.encode("utf-8"))
engine.dispose()
`;
  await runCommand(python, ["-c", script], {
    env: {
      ...process.env,
      COST_ACCEPTANCE_RELAY_DATABASE_URL: relayDatabaseUrl,
      COST_ACCEPTANCE_RELAY_CHANNEL_ID: String(channelId),
      COST_ACCEPTANCE_RELAY_CHANNEL_KEY: channelKey,
      COST_ACCEPTANCE_RELAY_MODEL: model,
      COST_ACCEPTANCE_RELAY_PROVIDER_CREDENTIAL_KEYRING: providerCredentialKeyring,
    },
    timeoutMs: 100_000,
    failure: "Relay native channel bootstrap failed",
  });
}

async function declaredMigrationHeads(python, env) {
  const { stdout } = await runCommand(python, ["-m", "alembic", "heads"], {
    cwd: platformRoot,
    env,
    failure: "Platform migration-head discovery failed",
  });
  const heads = [...stdout.matchAll(/^([A-Za-z0-9_]+)\s+\(head\)$/gm)].map((match) => match[1]).sort();
  if (heads.length < 1) throw new Error("Platform does not declare a migration head");
  return heads;
}

function publicRuntimeIdentity(identity) {
  return {
    instance_id: identity.candidate.instance_id,
    ...stableRuntimeIdentity(identity),
  };
}

export function buildEvidenceEnvelope(payload) {
  return {
    payload,
    integrity: {
      canonicalization: "recursive-sorted-json-keys-no-whitespace-v1",
      canonical_payload_sha256: sha256(canonicalJson(payload)),
    },
  };
}

export function validateEvidenceEnvelope(envelope) {
  return Boolean(
    envelope?.payload &&
    envelope?.integrity?.canonicalization === "recursive-sorted-json-keys-no-whitespace-v1" &&
    envelope.integrity.canonical_payload_sha256 === sha256(canonicalJson(envelope.payload)),
  );
}

async function applyPrivateEvidencePermissions(path) {
  await chmod(path, 0o600);
  if (process.platform === "win32") {
    const principal = process.env.USERNAME;
    if (!principal) throw new Error("Windows owner identity is unavailable for evidence ACL");
    await runCommand("icacls", [path, "/inheritance:r", "/grant:r", `${principal}:(R,W)`], {
      failure: "evidence owner-only ACL could not be applied",
    });
    const { stdout } = await runCommand("icacls", [path], { failure: "evidence ACL could not be verified" });
    if (!stdout.includes(`${principal}:(R,W)`) || stdout.includes("Everyone") || stdout.includes("BUILTIN\\Users")) {
      throw new Error("evidence ACL is not owner-only");
    }
    return "windows-owner-only-acl-posix-0600-equivalent";
  }
  const mode = (await stat(path)).mode & 0o777;
  if (mode !== 0o600) throw new Error("evidence mode is not 0600");
  return "posix-0600";
}

export async function writeCreateOnlyEvidence(path, envelope) {
  if (!validateEvidenceEnvelope(envelope)) throw new Error("evidence canonical digest is invalid");
  await mkdir(dirname(path), { recursive: true });
  const handle = await open(path, "wx", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(envelope, null, 2)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    const permissionScheme = await applyPrivateEvidencePermissions(path);
    const reparsed = JSON.parse(await readFile(path, "utf8"));
    if (!validateEvidenceEnvelope(reparsed)) throw new Error("persisted evidence failed canonical verification");
    return permissionScheme;
  } catch (error) {
    await rm(path, { force: true });
    throw error;
  }
}

async function stopChild(child) {
  const stopped = () => child.exitCode !== null || child.signalCode !== null;
  if (!child || stopped()) return;
  child.kill("SIGTERM");
  const gracefulDeadline = Date.now() + 5_000;
  while (!stopped() && Date.now() < gracefulDeadline) {
    await new Promise((resolveWait) => setTimeout(resolveWait, 50));
  }
  if (!stopped()) {
    child.kill("SIGKILL");
    const forcedDeadline = Date.now() + 5_000;
    while (!stopped() && Date.now() < forcedDeadline) {
      await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    }
  }
  if (!stopped()) throw new Error("runner-started Platform process could not be stopped");
}

async function assertResourcesRemoved(containers, volumes, network) {
  for (const container of containers) {
    if (await commandSucceeds("docker", ["container", "inspect", container])) {
      throw new Error("runner-owned acceptance container survived cleanup");
    }
  }
  for (const volume of volumes) {
    if (await commandSucceeds("docker", ["volume", "inspect", volume.name])) {
      throw new Error("runner-owned acceptance volume survived cleanup");
    }
  }
  if (network && await commandSucceeds("docker", ["network", "inspect", network])) {
    throw new Error("runner-owned acceptance network survived cleanup");
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const pythonExecutable = await resolveCanonicalPythonExecutable(args.python);
  const startedAt = new Date().toISOString();
  const runSuffix = randomBytes(6).toString("hex");
  const resourcePrefix = `ai-video-cost-${runSuffix}`;
  const network = `${resourcePrefix}-network`;
  const platformPostgres = `${resourcePrefix}-platform-pg`;
  const relayPostgres = `${resourcePrefix}-relay-pg`;
  const redis = `${resourcePrefix}-redis`;
  const volumes = [
    {
      name: `${resourcePrefix}-platform-pg-data`,
      role: "platform-postgresql",
      destination: "/var/lib/postgresql/data",
    },
    {
      name: `${resourcePrefix}-relay-pg-data`,
      role: "relay-postgresql",
      destination: "/var/lib/postgresql/data",
    },
    {
      name: `${resourcePrefix}-redis-data`,
      role: "relay-redis",
      destination: "/data",
    },
  ];
  const relayMigration = `${resourcePrefix}-relay-migrate`;
  const relay = `${resourcePrefix}-relay`;
  const compiledIdentityContainer = `${resourcePrefix}-compiled-id`;
  const containers = [
    platformPostgres,
    relayPostgres,
    redis,
    relayMigration,
    relay,
    compiledIdentityContainer,
  ];
  const tempRoot = await mkdtemp(resolve(tmpdir(), `ai-video-cost-${runSuffix}-`));
  let platformProcess;
  let result;
  let acceptanceError;
  let cleanupVerified = false;
  let cleanupAuthorized = false;

  const platformPassword = randomBytes(32).toString("base64url");
  const relayPassword = randomBytes(32).toString("base64url");
  const redisPassword = randomBytes(32).toString("base64url");
  const bootstrapValue = randomBytes(32).toString("base64url");
  const internalValue = randomBytes(32).toString("base64url");
  const signingValue = randomBytes(32).toString("base64url");
  const admissionValue = randomBytes(32).toString("base64url");
  const relayClientAPIValue = randomBytes(32).toString("base64url");
  const relayUpstreamValue = randomBytes(32).toString("base64url");
  const relayChannelKeyValue = randomBytes(32).toString("base64url");
  const relayProviderCredentialKeyId = "cost-acceptance-v1";
  const relayProviderCredentialKek = randomBytes(32).toString("base64");
  const relayProviderCredentialKeyring = JSON.stringify({
    schema_version: 1,
    active_key_id: relayProviderCredentialKeyId,
    keys: { [relayProviderCredentialKeyId]: relayProviderCredentialKek },
  });
  const relayServiceTenantId = randomUUID();
  const contractRateId = randomUUID();
  const providerName = "cost-acceptance-provider";
  const providerChannelId = 9301;
  const providerUpstreamModel = "cost-acceptance-video-v1";
  const contractRateSourceReference = "provider-contract-cost-acceptance-v1";
  const contractRateSourceSha256 = createHash("sha256")
    .update("provider-contract-cost-acceptance-v1")
    .digest("hex");
  const processNonce = randomBytes(32).toString("base64url");
  const sessionValue = randomBytes(48).toString("base64url");
  const cryptoValue = randomBytes(48).toString("base64url");
  const forbiddenValues = [
    platformPassword,
    relayPassword,
    redisPassword,
    bootstrapValue,
    internalValue,
    signingValue,
    admissionValue,
    relayClientAPIValue,
    relayUpstreamValue,
    relayChannelKeyValue,
    relayProviderCredentialKek,
    relayProviderCredentialKeyring,
    relayServiceTenantId,
    processNonce,
    sessionValue,
    cryptoValue,
  ];

  try {
    for (const container of containers) {
      if (await commandSucceeds("docker", ["container", "inspect", container])) {
        throw new Error("a planned runner-owned container name is already in use");
      }
    }
    if (await commandSucceeds("docker", ["network", "inspect", network])) {
      throw new Error("the planned runner-owned network name is already in use");
    }
    for (const volume of volumes) {
      if (await commandSucceeds("docker", ["volume", "inspect", volume.name])) {
        throw new Error("a planned runner-owned volume name is already in use");
      }
    }
    cleanupAuthorized = true;
    const sourceBefore = {
      relay: await relaySourceSnapshot(),
      platform: await platformSourceSnapshot(),
      harness: await harnessSourceSnapshot(),
    };
    const candidateImage = await inspectDocker("image", args.candidateImage);
    const candidateImageId = String(candidateImage.Id || "");
    if (!/^sha256:[0-9a-f]{64}$/.test(candidateImageId)) throw new Error("candidate image does not have an immutable image ID");
    const candidateLabels = candidateImage.Config?.Labels || {};
    const labelErrors = validateCandidateImageLabels(
      candidateLabels,
      sourceBefore.relay,
      CANDIDATE_UPSTREAM_GIT_REVISION,
    );
    if (labelErrors.length > 0) throw new Error(labelErrors[0]);
    const compiledIdentity = await compiledBuildIdentity(
      candidateImageId,
      compiledIdentityContainer,
      sourceBefore.relay,
    );

    await runCommand("docker", ["network", "create", ...acceptanceResourceLabels(runSuffix), network], {
      failure: "runner-owned acceptance network could not be created",
    });
    const platformBinding = runnerPlatformBinding(
      process.platform,
      await inspectDocker("network", network),
    );
    for (const volume of volumes) await createRunnerOwnedVolume(volume, runSuffix);

    const platformPgEnv = resolve(tempRoot, "platform-postgres.env");
    const relayPgEnv = resolve(tempRoot, "relay-postgres.env");
    const redisEnv = resolve(tempRoot, "redis.env");
    await writePrivateFile(platformPgEnv, envFileContents({
      POSTGRES_USER: "cost_acceptance",
      POSTGRES_PASSWORD: platformPassword,
      POSTGRES_DB: "platform_cost_acceptance",
    }));
    await writePrivateFile(relayPgEnv, envFileContents({
      POSTGRES_USER: "cost_acceptance",
      POSTGRES_PASSWORD: relayPassword,
      POSTGRES_DB: "relay_cost_acceptance",
    }));
    await writePrivateFile(redisEnv, envFileContents({ REDIS_PASSWORD: redisPassword }));

    for (const [name, alias, envPath, database, volume] of [
      [platformPostgres, "platform-postgres", platformPgEnv, "platform_cost_acceptance", volumes[0]],
      [relayPostgres, "relay-postgres", relayPgEnv, "relay_cost_acceptance", volumes[1]],
    ]) {
      await runCommand("docker", [
        "run", "--detach", "--name", name,
        ...acceptanceResourceLabels(runSuffix),
        "--network", network, "--network-alias", alias,
        "--publish", "127.0.0.1::5432",
        "--mount", `type=volume,source=${volume.name},target=${volume.destination}`,
        "--env-file", envPath,
        postgresImage,
      ], { failure: "runner-owned PostgreSQL could not be started" });
      await waitForDockerPostgres(name, database);
    }

    await runCommand("docker", [
      "run", "--detach", "--name", redis,
      ...acceptanceResourceLabels(runSuffix),
      "--network", network, "--network-alias", "relay-redis",
      "--publish", "127.0.0.1::6379",
      "--mount", `type=volume,source=${volumes[2].name},target=${volumes[2].destination}`,
      "--env-file", redisEnv,
      redisImage,
      "sh", "-ec",
      "exec redis-server --appendonly yes --appendfsync everysec --requirepass \"$REDIS_PASSWORD\"",
    ], { failure: "runner-owned Redis could not be started" });
    await Promise.all([
      assertRunnerOwnedVolumeBinding(platformPostgres, volumes[0], runSuffix),
      assertRunnerOwnedVolumeBinding(relayPostgres, volumes[1], runSuffix),
      assertRunnerOwnedVolumeBinding(redis, volumes[2], runSuffix),
    ]);

    const platformPgPort = await publishedPort(platformPostgres, 5432);
    const relayPgPort = await publishedPort(relayPostgres, 5432);
    const platformDatabaseUrl = `postgresql+psycopg://cost_acceptance:${platformPassword}@127.0.0.1:${platformPgPort}/platform_cost_acceptance`;
    const relayDatabaseUrl = `postgresql+psycopg://cost_acceptance:${relayPassword}@127.0.0.1:${relayPgPort}/relay_cost_acceptance`;
    forbiddenValues.push(platformDatabaseUrl, relayDatabaseUrl);
    const platformPycache = resolve(tempRoot, "platform-pycache");
    await mkdir(platformPycache, { recursive: false });
    if ((await readdir(platformPycache)).length !== 0) {
      throw new Error("isolated Platform bytecode cache was not empty before launch");
    }

    const platformEnv = {
      ...process.env,
      DATABASE_URL: platformDatabaseUrl,
      ENVIRONMENT: "development",
      DEVELOPMENT_HEADER_AUTH_ENABLED: "true",
      AUTO_CREATE_TABLES: "false",
      ENABLE_BOOTSTRAP: "true",
      BOOTSTRAP_TOKEN: bootstrapValue,
      INTERNAL_SERVICE_TOKEN: internalValue,
      CHANNEL_COST_SIGNING_SECRET: signingValue,
      CHANNEL_COST_SIGNATURE_REQUIRED: "true",
      CHANNEL_COST_SIGNATURE_MAX_AGE_SECONDS: "300",
      PLATFORM_COST_ACCEPTANCE_PROCESS_NONCE: processNonce,
      PYTHONPYCACHEPREFIX: platformPycache,
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONUNBUFFERED: "1",
    };
    const migrationHeads = await declaredMigrationHeads(pythonExecutable, platformEnv);
    await runCommand(pythonExecutable, ["-m", "alembic", "upgrade", "head"], {
      cwd: platformRoot,
      env: platformEnv,
      timeoutMs: 120_000,
      failure: "Platform migration to head failed",
    });

    const platformPort = await availablePort(platformBinding.probeHost);
    platformProcess = spawn(pythonExecutable, [
      "-m", "uvicorn", "cost_acceptance_server:app",
      "--app-dir", "tests/integration",
      "--host", platformBinding.bindHost,
      "--port", String(platformPort),
      "--log-level", "warning",
    ], {
      cwd: platformRoot,
      env: platformEnv,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    const readPlatformOutput = collectChildOutput(platformProcess);
    await new Promise((resolveSpawn, rejectSpawn) => {
      platformProcess.once("spawn", resolveSpawn);
      platformProcess.once("error", rejectSpawn);
    });
    const platformUrl = `http://${platformBinding.probeHost}:${platformPort}`;
    let platformProbeBefore;
    try {
      platformProbeBefore = await waitForPlatformProbe(
        platformUrl,
        processNonce,
        platformProcess.pid,
        platformProcess,
      );
    } catch (error) {
      // Startup failures happen before pytest, so preserve the same bounded,
      // secret-redacted diagnostics used by the test command. The caller only
      // prints this evidence after exact resource cleanup has been verified.
      try {
        const diagnostic = redactAcceptanceDiagnostic(
          "",
          readPlatformOutput(),
          [...forbiddenValues, ...sensitiveEnvironmentValues(platformEnv)],
        );
        if (diagnostic && error instanceof Error) {
          Object.defineProperty(error, redactedDiagnosticProperty, {
            configurable: false,
            enumerable: false,
            value: diagnostic,
            writable: false,
          });
        }
      } catch {
        // Fail closed with the generic probe error if redaction cannot prove
        // that the child output is safe to disclose.
      }
      throw error;
    }
    assertNoForbiddenText(readPlatformOutput(), forbiddenValues);
    platformProcess.stdout?.resume();
    platformProcess.stderr?.resume();

    const relayMigrationEnvPath = resolve(tempRoot, "relay-migrate.env");
    const relayEnvPath = resolve(tempRoot, "relay.env");
    const relayInternalDatabaseUrl = `postgresql://cost_acceptance:${relayPassword}@relay-postgres:5432/relay_cost_acceptance?sslmode=disable`;
    const redisInternalUrl = `redis://:${redisPassword}@relay-redis:6379/0`;
    const relayModel = "cost-acceptance.video.v1";
    const relayClientCredentials = JSON.stringify({
      "cost-acceptance": {
        tenant_id: relayServiceTenantId,
        api_key: relayClientAPIValue,
        upstream_token: relayUpstreamValue,
      },
    });
    const relayCapability = {
      schema_version: 1,
      modes: {
        text_to_video: {
          input_media_types: [],
          supports_face: false,
          required_resource_keys: [],
          limits: {
            max_prompt_length: 1000,
            max_images: 0,
            max_videos: 0,
            max_audio: 0,
            duration_seconds: [5],
            aspect_ratios: ["16:9"],
            resolutions: ["720p"],
            output_counts: [1],
          },
        },
      },
    };
    const channelKeyFingerprint = createHash("sha256")
      .update(relayChannelKeyValue)
      .digest("hex");
    const relayModelRoutes = JSON.stringify({
      [relayModel]: [{
        route_id: "official.integration-route",
        provider_name: providerName,
        account_id: "cost-acceptance-account",
        channel_id: providerChannelId,
        key_index: 0,
        key_fingerprint: channelKeyFingerprint,
        channel_class: "official",
        upstream_model: providerUpstreamModel,
        staging_ready: false,
        production_ready: true,
        rpm_limit: 100,
        active_task_limit: 100,
        capabilities: relayCapability,
      }],
    });
    const relayProviderContractRates = JSON.stringify([{
      id: contractRateId,
      provider_name: providerName,
      channel_id: providerChannelId,
      upstream_model: providerUpstreamModel,
      mode: "text_to_video",
      resolution: "720p",
      billing_unit: "output_second",
      unit_amount_cents: 5,
      currency: "CNY",
      effective_from: "2026-08-07T11:00:00Z",
      source_reference: contractRateSourceReference,
      source_document_sha256: contractRateSourceSha256,
    }]);
    forbiddenValues.push(relayInternalDatabaseUrl, redisInternalUrl);

    // Contract-rate startup validation requires a configured native channel
    // before route synchronization. Run the candidate's dedicated one-shot
    // migrator, prove its exact terminal result and remove it, seed one
    // manually-disabled channel, then start the long-lived runtime.
    const relayMigrationEnv = {
      TZ: "UTC",
      SQL_DSN: relayInternalDatabaseUrl,
      NODE_TYPE: "master",
      NODE_NAME: `${resourcePrefix}-migrate`,
      ERROR_LOG_ENABLED: "false",
      BATCH_UPDATE_ENABLED: "false",
      UPDATE_TASK: "false",
      RELAY_COMPAT_ENABLED: "false",
      RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON: relayProviderCredentialKeyring,
    };
    await writePrivateFile(relayMigrationEnvPath, envFileContents(relayMigrationEnv));
    const relayMigrationForbiddenValues = [
      ...forbiddenValues,
      ...sensitiveEnvironmentValues(relayMigrationEnv),
    ];
    await runCommand("docker", [
      "run", "--detach", "--name", relayMigration,
      ...acceptanceResourceLabels(runSuffix),
      "--network", network,
      "--user", "10001:10001",
      "--read-only",
      "--tmpfs", "/data:rw,noexec,nosuid,size=64m,uid=10001,gid=10001",
      "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m,uid=10001,gid=10001",
      "--cap-drop", "ALL",
      "--security-opt", "no-new-privileges:true",
      "--pids-limit", "256",
      "--env-file", relayMigrationEnvPath,
      "--entrypoint", "/new-api",
      candidateImageId,
      "relay-migrate",
    ], { timeoutMs: 180_000, failure: "Relay schema migration container could not be started" });
    await waitForRelaySchemaMigration(
      relayMigration,
      relayMigrationForbiddenValues,
    );
    await runCommand("docker", ["rm", relayMigration], {
      timeoutMs: 60_000,
      failure: "Relay schema migration container could not be removed",
    });
    if (await commandSucceeds("docker", ["container", "inspect", relayMigration])) {
      throw new Error("Relay schema migration container survived its bounded cleanup");
    }
    await seedRelayNativeChannel(pythonExecutable, relayDatabaseUrl, {
      channelId: providerChannelId,
      channelKey: relayChannelKeyValue,
      model: relayModel,
      providerCredentialKeyring: relayProviderCredentialKeyring,
    });

    await writePrivateFile(relayEnvPath, envFileContents({
      TZ: "UTC",
      PORT: "3000",
      SQL_DSN: relayInternalDatabaseUrl,
      REDIS_CONN_STRING: redisInternalUrl,
      SESSION_SECRET: sessionValue,
      CRYPTO_SECRET: cryptoValue,
      NODE_TYPE: "master",
      NODE_NAME: `${resourcePrefix}-1`,
      ERROR_LOG_ENABLED: "false",
      BATCH_UPDATE_ENABLED: "false",
      UPDATE_TASK: "false",
      RELAY_COMPAT_ENABLED: "true",
      RELAY_COMPAT_ENVIRONMENT: "development",
      RELAY_COMPAT_WORKER_ENABLED: "false",
      RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN: admissionValue,
      RELAY_COMPAT_CLIENT_CREDENTIALS_JSON: relayClientCredentials,
      RELAY_COMPAT_MODEL_ROUTES_JSON: relayModelRoutes,
      RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON: relayProviderCredentialKeyring,
      RELAY_COMPAT_SOURCE_REVISION: sourceBefore.relay.sha1,
      RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256: sourceBefore.relay.sha256,
      RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT: sourceBefore.relay.file_count,
      RELAY_COMPAT_IMAGE_DIGEST: candidateImageId,
      RELAY_PROVIDER_MONITOR_ENABLED: "true",
      RELAY_PROVIDER_MONITOR_INTERVAL_SECONDS: "30",
      RELAY_PLATFORM_CHANNEL_COST_URL: `http://host.docker.internal:${platformPort}/internal/channel-costs`,
      RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN: internalValue,
      RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET: signingValue,
      RELAY_PROVIDER_CONTRACT_RATES_JSON: relayProviderContractRates,
      RELAY_CHANNEL_COST_CLAIM_LEASE_SECONDS: "60",
      RELAY_CHANNEL_COST_POLL_SECONDS: "0.1",
    }));
    await runCommand("docker", [
      "run", "--detach", "--name", relay,
      ...acceptanceResourceLabels(runSuffix),
      "--network", network,
      "--add-host", `host.docker.internal:${platformBinding.containerHostAddress}`,
      "--publish", "127.0.0.1::3000",
      "--user", "10001:10001",
      "--read-only",
      "--tmpfs", "/data:rw,noexec,nosuid,size=64m,uid=10001,gid=10001",
      "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m,uid=10001,gid=10001",
      "--cap-drop", "ALL",
      "--security-opt", "no-new-privileges:true",
      "--pids-limit", "256",
      "--env-file", relayEnvPath,
      candidateImageId,
    ], { timeoutMs: 180_000, failure: "candidate Relay container could not be started" });

    const relayContainer = await inspectDocker("container", relay);
    if (relayContainer.Image !== candidateImageId || relayContainer.State?.Running !== true) {
      throw new Error("running Relay container does not use the inspected immutable candidate image");
    }
    if (
      relayContainer.Config?.User !== "10001:10001" ||
      relayContainer.HostConfig?.ReadonlyRootfs !== true ||
      !relayContainer.HostConfig?.CapDrop?.includes("ALL") ||
      !relayContainer.HostConfig?.SecurityOpt?.includes("no-new-privileges:true") ||
      relayContainer.HostConfig?.PidsLimit !== 256 ||
      !relayContainer.HostConfig?.Tmpfs?.["/data"] ||
      !relayContainer.HostConfig?.Tmpfs?.["/tmp"]
    ) {
      throw new Error("candidate Relay container is missing acceptance runtime hardening");
    }
    const relayPort = await publishedPort(relay, 3000);
    const relayUrl = `http://127.0.0.1:${relayPort}`;
    const runtimeBefore = await waitForRelayRuntimeBound(
      relayUrl,
      admissionValue,
      sourceBefore.relay,
      candidateImageId,
      candidateLabels,
      relay,
      forbiddenValues,
    );
    await assertRuntimeIdentityIsProtected(relayUrl, sourceBefore.relay);
    const runtimeSecond = await waitForRelayRuntimeBound(
      relayUrl,
      admissionValue,
      sourceBefore.relay,
      candidateImageId,
      candidateLabels,
      relay,
      forbiddenValues,
    );
    if (!safeEqual(runtimeBefore.candidate, runtimeSecond.candidate)) {
      throw new Error("Relay runtime identity changed within one container process");
    }
    if (!safeEqual(stableRuntimeIdentity(runtimeBefore), {
      upstream_git_revision: compiledIdentity.upstream_git_revision,
      source_git_revision: compiledIdentity.source_revision,
      source_snapshot_sha256: compiledIdentity.source_snapshot_sha256,
      source_snapshot_file_count: compiledIdentity.source_snapshot_file_count,
      image_digest: candidateImageId,
    })) {
      throw new Error("running Relay identity does not match the offline compiled identity");
    }

    const fingerprintsBefore = await postgresFingerprints(
      pythonExecutable,
      platformDatabaseUrl,
      relayDatabaseUrl,
    );
    if (!safeEqual(fingerprintsBefore.platform.migration_versions, migrationHeads)) {
      throw new Error("running Platform database does not match the declared migration head");
    }
    if (fingerprintsBefore.relay.migration_versions.length !== 0) {
      throw new Error("Relay PostgreSQL unexpectedly reported a Platform migration head");
    }
    const redisBefore = await redisFingerprint(redis);

    const pytestStarted = Date.now();
    const testResult = await runCommand(pythonExecutable, [
      "-m", "pytest", "-q", "-ra", "-p", "no:cacheprovider",
      "tests/integration/test_new_api_channel_cost_delivery.py",
    ], {
      cwd: platformRoot,
      env: {
        ...platformEnv,
        PLATFORM_CHANNEL_COST_IT_URL: platformUrl,
        PLATFORM_CHANNEL_COST_IT_DATABASE_URL: platformDatabaseUrl,
        NEW_API_CHANNEL_COST_IT_DATABASE_URL: relayDatabaseUrl,
        PLATFORM_CHANNEL_COST_IT_INTERNAL_TOKEN: internalValue,
        PLATFORM_CHANNEL_COST_IT_SIGNING_SECRET: signingValue,
        NEW_API_CHANNEL_COST_IT_URL: relayUrl,
        NEW_API_CHANNEL_COST_IT_CLIENT_ID: "cost-acceptance",
        NEW_API_CHANNEL_COST_IT_API_KEY: relayClientAPIValue,
        NEW_API_CHANNEL_COST_IT_SERVICE_TENANT_ID: relayServiceTenantId,
        NEW_API_CHANNEL_COST_IT_CONTRACT_RATE_ID: contractRateId,
        NEW_API_CHANNEL_COST_IT_PROVIDER_NAME: providerName,
        NEW_API_CHANNEL_COST_IT_PROVIDER_CHANNEL_ID: String(providerChannelId),
        NEW_API_CHANNEL_COST_IT_PROVIDER_UPSTREAM_MODEL: providerUpstreamModel,
        NEW_API_CHANNEL_COST_IT_CONTRACT_RATE_SOURCE_REFERENCE:
          contractRateSourceReference,
        NEW_API_CHANNEL_COST_IT_CONTRACT_RATE_SOURCE_SHA256:
          contractRateSourceSha256,
      },
      timeoutMs: 180_000,
      maxBuffer: 16 * 1024 * 1024,
      failure: "cross-service channel-cost integration test failed",
      diagnosticForbiddenValues: [
        ...forbiddenValues,
        ...sensitiveEnvironmentValues(platformEnv),
      ],
    });
    const testOutput = `${testResult.stdout}\n${testResult.stderr}`;
    assertNoForbiddenText(testOutput, forbiddenValues);
    if (!/\b1 passed\b/.test(testOutput) || /\bskipped\b/.test(testOutput)) {
      throw new Error("cross-service channel-cost test did not execute exactly one passing case");
    }

    const runtimeAfter = await waitForRelayRuntimeBound(
      relayUrl,
      admissionValue,
      sourceBefore.relay,
      candidateImageId,
      candidateLabels,
      relay,
      forbiddenValues,
    );
    if (!safeEqual(runtimeBefore.candidate, runtimeAfter.candidate)) {
      throw new Error("Relay runtime identity changed during channel-cost acceptance");
    }
    const platformProbeAfter = await waitForPlatformProbe(
      platformUrl,
      processNonce,
      platformProcess.pid,
      platformProcess,
    );
    if (!safeEqual(platformProbeBefore, platformProbeAfter)) {
      throw new Error("Platform process-bound probe changed during channel-cost acceptance");
    }
    const fingerprintsAfter = await postgresFingerprints(
      pythonExecutable,
      platformDatabaseUrl,
      relayDatabaseUrl,
    );
    if (!safeEqual(fingerprintsBefore, fingerprintsAfter)) {
      throw new Error("PostgreSQL instance fingerprints changed during channel-cost acceptance");
    }
    const redisAfter = await redisFingerprint(redis);
    if (!safeEqual(redisBefore, redisAfter)) {
      throw new Error("Redis instance fingerprint changed during channel-cost acceptance");
    }
    if (platformProcess.exitCode !== null) {
      throw new Error("runner-started Platform process exited during channel-cost acceptance");
    }
    assertNoForbiddenText(readPlatformOutput(), forbiddenValues);
    if ((await readdir(platformPycache)).length !== 0) {
      throw new Error("Platform wrote bytecode into its isolated no-bytecode cache");
    }
    const relayLogs = await runCommand("docker", ["logs", relay], {
      failure: "candidate Relay logs could not be audited",
    });
    assertNoForbiddenText(`${relayLogs.stdout}\n${relayLogs.stderr}`, forbiddenValues);

    const sourceAfter = {
      relay: await relaySourceSnapshot(),
      platform: await platformSourceSnapshot(),
      harness: await harnessSourceSnapshot(),
    };
    if (!safeEqual(sourceBefore, sourceAfter)) {
      throw new Error("acceptance source changed while the cost gate was running");
    }

    const platformPgContainer = await inspectDocker("container", platformPostgres);
    const relayPgContainer = await inspectDocker("container", relayPostgres);
    const redisContainer = await inspectDocker("container", redis);
    const networkInspect = await inspectDocker("network", network);
    const expectedMembers = new Set([
      platformPgContainer.Id,
      relayPgContainer.Id,
      redisContainer.Id,
      relayContainer.Id,
    ]);
    const actualMembers = new Set(Object.keys(networkInspect.Containers || {}));
    if (expectedMembers.size !== actualMembers.size || [...expectedMembers].some((id) => !actualMembers.has(id))) {
      throw new Error("runner-owned acceptance network membership is not isolated");
    }

    result = {
      schema_version: 1,
      kind: evidenceKind,
      evidence_id: randomUUID(),
      status: "PASS",
      verified: true,
      started_at_utc: startedAt,
      workload_completed_at_utc: new Date().toISOString(),
      source_freeze: {
        before: sourceBefore,
        after: sourceAfter,
        unchanged: true,
      },
      candidate_relay: {
        immutable_image_id: candidateImageId,
        running_container_id: relayContainer.Id,
        container_image_id_matches: true,
        image_labels: {
          upstream_revision: candidateLabels[CANDIDATE_IMAGE_LABELS.upstreamRevision],
          source_revision: candidateLabels[CANDIDATE_IMAGE_LABELS.sourceRevision],
          source_snapshot_sha256: candidateLabels[CANDIDATE_IMAGE_LABELS.sourceSnapshot],
          source_file_count: Number(candidateLabels[CANDIDATE_IMAGE_LABELS.sourceFileCount]),
        },
        offline_compiled_build_identity: compiledIdentity,
        runtime_build_identity: publicRuntimeIdentity(runtimeBefore),
        protected_runtime_identity_matches_labels_and_host_snapshot: true,
        runtime_identity_rejects_unauthorized_requests: true,
        runtime_identity_matches_offline_compiled_binary: true,
        runtime_hardening: {
          non_root_uid_gid: "10001:10001",
          read_only_root_filesystem: true,
          isolated_tmpfs: true,
          linux_capabilities_dropped: true,
          no_new_privileges: true,
          process_limit: 256,
        },
      },
      platform_process: {
        pid: platformProcess.pid,
        source_snapshot: sourceBefore.platform,
        migration_heads: migrationHeads,
        ...platformProbeBefore,
        isolated_empty_bytecode_cache: true,
      },
      real_dependencies: {
        platform_postgresql: {
          container_id: platformPgContainer.Id,
          image_id: platformPgContainer.Image,
          ...fingerprintsBefore.platform,
        },
        relay_postgresql: {
          container_id: relayPgContainer.Id,
          image_id: relayPgContainer.Image,
          ...fingerprintsBefore.relay,
        },
        postgresql_instances_distinct: true,
        redis: {
          container_id: redisContainer.Id,
          image_id: redisContainer.Image,
          ...redisBefore,
        },
        isolated_network_id_sha256: sha256(networkInspect.Id),
      },
      contract_test: {
        test_file: "backend/platform/tests/integration/test_new_api_channel_cost_delivery.py",
        passed: 1,
        skipped: 0,
        duration_milliseconds: Date.now() - pytestStarted,
        verified_behaviors: [
          "authenticated /v1/generations requests persisted Platform company/task metadata under a distinct Relay service tenant",
          "runtime workers materialized processing, succeeded, failed, and cancelled costs from provider-success outcomes and an immutable contract rate",
          "materialized costs delivered with signed 201 acknowledgements",
          "exact replay remained idempotent",
          "binding conflicts returned 409 and invalid channel class returned 422",
          "unsigned bad-signature and internal-credential-as-signature requests returned 401",
          "customer wallet and terminal task states were unchanged",
        ],
      },
      cleanup: {
        unique_run_id: runSuffix,
        exact_runner_owned_resources_only: true,
        labeled_runner_owned_volumes: volumes.length,
        exact_volume_bindings_verified: true,
        verified_absent_before_evidence_write: true,
      },
      evidence_file: {
        create_only: true,
        requested_mode: "0600",
        permission_enforcement: process.platform === "win32"
          ? "windows-owner-only-acl-posix-0600-equivalent"
          : "posix-0600",
        secret_free_scan: true,
      },
      checks: {
        immutable_candidate_image_bound: true,
        offline_compiled_identity_bound: true,
        compiled_runtime_identity_bound: true,
        platform_process_probe_bound: true,
        two_real_postgresql_instances: true,
        real_redis_instance: true,
        source_unchanged: true,
        cross_service_cost_contract: true,
      },
    };
    assertSecretFreeEvidence(result, forbiddenValues);
  } catch (error) {
    acceptanceError = error;
  } finally {
    let cleanupError;
    try {
      await stopChild(platformProcess);
    } catch {
      cleanupError = new Error("runner-owned process cleanup failed");
    }
    if (cleanupAuthorized) {
      for (const container of [...containers].reverse()) {
        await commandSucceeds("docker", ["rm", "--force", container], { timeoutMs: 60_000 });
      }
      for (const volume of [...volumes].reverse()) {
        await commandSucceeds("docker", ["volume", "rm", volume.name], { timeoutMs: 60_000 });
      }
      if (await commandSucceeds("docker", ["network", "inspect", network])) {
        await commandSucceeds("docker", ["network", "rm", network], { timeoutMs: 60_000 });
      }
    }
    try {
      await rm(tempRoot, { recursive: true, force: true });
    } catch {
      cleanupError = new Error("runner-owned temporary-file cleanup failed");
    }
    if (cleanupAuthorized) {
      try {
        await assertResourcesRemoved(containers, volumes, network);
      } catch {
        cleanupError = new Error("runner-owned Docker resource cleanup failed");
      }
    }
    cleanupVerified = !cleanupError;
    if (cleanupError) throw cleanupError;
  }

  if (acceptanceError) {
    if (!cleanupVerified) throw new Error("cost acceptance failed before cleanup could be verified");
    if (
      cleanupAuthorized &&
      acceptanceError instanceof Error &&
      typeof acceptanceError[redactedDiagnosticProperty] === "string"
    ) {
      Object.defineProperty(acceptanceError, diagnosticCleanupProperty, {
        configurable: false,
        enumerable: false,
        value: true,
        writable: false,
      });
    }
    throw acceptanceError;
  }

  if (!result || !cleanupVerified) throw new Error("cost acceptance did not complete its verified cleanup");
  result.completed_at_utc = new Date().toISOString();
  const envelope = buildEvidenceEnvelope(result);
  assertSecretFreeEvidence(envelope, forbiddenValues);
  const defaultName = `${result.completed_at_utc.replace(/[:.]/g, "-")}-${runSuffix}.json`;
  const output = resolve(args.out || resolve(workspace, "artifacts", "cross-service-cost-acceptance", defaultName));
  const permissionScheme = await writeCreateOnlyEvidence(output, envelope);
  process.stdout.write(`cross-service cost acceptance: PASS (${output}; ${permissionScheme})\n`);
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  main().catch((error) => {
    process.stderr.write(formatAcceptanceFailure(error));
    process.exitCode = 1;
  });
}
