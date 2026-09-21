import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  issueDisposableSmokeManifest,
  validateDisposableSmokeManifest,
} from "../scripts/smoke-disposable-environment.mjs";

const smokeSource = readFileSync(
  new URL("../scripts/smoke-local.ps1", import.meta.url),
  "utf8",
);
const runbookSource = readFileSync(
  new URL("../docs/deployment-runbook.md", import.meta.url),
  "utf8",
);

const now = Date.parse("2026-08-28T04:00:00.000Z");
const disposableTargets = Object.freeze({
  gateway_base: "http://127.0.0.1:18180",
  platform_base: "http://127.0.0.1:18200",
  relay_base: "http://127.0.0.1:18300",
});

function validManifest() {
  return issueDisposableSmokeManifest(
    {
      environmentId: "run-20260828",
      targets: disposableTargets,
    },
    now,
  );
}

test("disposable smoke manifest binds a short-lived run to exact loopback targets and cleanup", () => {
  const manifest = validManifest();
  const validated = validateDisposableSmokeManifest(
    manifest,
    disposableTargets,
    now + 1_000,
  );

  assert.equal(validated.environment_id, "run-20260828");
  assert.deepEqual(validated.targets, disposableTargets);
  assert.deepEqual(validated.isolation, {
    kind: "docker-compose-project",
    compose_project: "ai-video-smoke-run-20260828",
    cleanup_mode: "docker-compose-down-volumes",
    remove_volumes: true,
  });
});

test("disposable smoke manifest refuses the long-lived local development ports", () => {
  for (const [key, port] of [
    ["gateway_base", "8180"],
    ["platform_base", "8200"],
    ["relay_base", "8300"],
  ]) {
    assert.throws(
      () =>
        issueDisposableSmokeManifest(
          {
            environmentId: "run-20260828",
            targets: {
              ...disposableTargets,
              [key]: `http://127.0.0.1:${port}`,
            },
          },
          now,
        ),
      new RegExp(`protected long-lived development port ${port}`),
    );
  }
});

test("disposable smoke manifest fails closed on target mismatch or a remote host", () => {
  const manifest = validManifest();
  assert.throws(
    () =>
      validateDisposableSmokeManifest(
        manifest,
        { ...disposableTargets, platform_base: "http://127.0.0.1:18201" },
        now + 1_000,
      ),
    /platform_base does not match/,
  );
  assert.throws(
    () =>
      validateDisposableSmokeManifest(
        manifest,
        {
          ...disposableTargets,
          platform_base: "https://platform.example.com:4443",
        },
        now + 1_000,
      ),
    /exact loopback IP address/,
  );
});

test("disposable smoke manifest rejects stale evidence and cleanup without volume removal", () => {
  const expired = structuredClone(validManifest());
  assert.throws(
    () =>
      validateDisposableSmokeManifest(
        expired,
        disposableTargets,
        Date.parse("2026-08-28T07:00:01.000Z"),
      ),
    /expired/,
  );

  const retainedVolumes = structuredClone(validManifest());
  retainedVolumes.isolation.remove_volumes = false;
  assert.throws(
    () =>
      validateDisposableSmokeManifest(
        retainedVolumes,
        disposableTargets,
        now + 1_000,
      ),
    /volume-removing cleanup/,
  );
});

test("PowerShell smoke guard runs before the first HTTP request and reports cleanup ownership", () => {
  const manifestGuard = smokeSource.indexOf(
    "if ([string]::IsNullOrWhiteSpace($DisposableEnvironmentManifest))",
  );
  const manifestValidation = smokeSource.indexOf(
    "$validationExitCode = $LASTEXITCODE",
  );
  const firstHttpRequest = smokeSource.indexOf("Invoke-RestMethod");

  assert(manifestGuard >= 0);
  assert(manifestValidation > manifestGuard);
  assert(firstHttpRequest > manifestValidation);
  assert.match(smokeSource, /result\s*=\s*"preflight_passed"/);
  assert.match(smokeSource, /cleanup_required\s*=\s*\$true/);
  assert.match(
    smokeSource,
    /cleanup_mode\s*=\s*\$validatedDisposableEnvironment\.isolation\.cleanup_mode/,
  );
});

test("manifest CLI issues only a bounded non-default disposable declaration", () => {
  const helperPath = fileURLToPath(
    new URL("../scripts/smoke-disposable-environment.mjs", import.meta.url),
  );
  const result = spawnSync(
    process.execPath,
    [
      helperPath,
      "issue",
      "--environment-id",
      "run-20260828",
      "--gateway-base",
      disposableTargets.gateway_base,
      "--platform-base",
      disposableTargets.platform_base,
      "--relay-base",
      disposableTargets.relay_base,
    ],
    { encoding: "utf8" },
  );

  assert.equal(result.status, 0, result.stderr);
  const manifest = JSON.parse(result.stdout);
  assert.equal(manifest.isolation.remove_volumes, true);
  assert.equal(
    Date.parse(manifest.expires_at_utc) - Date.parse(manifest.created_at_utc),
    2 * 60 * 60 * 1000,
  );
});

test("runbook requires exact project teardown and verifies every project-scoped resource class", () => {
  assert.match(
    runbookSource,
    /docker compose --project-name \$smokeProject up --build --detach --wait/,
  );
  assert.match(
    runbookSource,
    /docker compose --project-name \$smokeProject down --volumes --remove-orphans/,
  );
  for (const resource of ["container", "volume", "network"]) {
    assert.match(
      runbookSource,
      new RegExp(
        `docker ${resource} ls [^\\n]*label=com\\.docker\\.compose\\.project=\\$smokeProject`,
      ),
    );
  }
  assert.match(runbookSource, /containerCheckExitCode -ne 0/);
  assert.match(runbookSource, /volumeCheckExitCode -ne 0/);
  assert.match(runbookSource, /networkCheckExitCode -ne 0/);
  assert.match(runbookSource, /cleanup is incomplete: \$smokeProject/);
  assert.match(runbookSource, /cleanup_required=true/);
});
