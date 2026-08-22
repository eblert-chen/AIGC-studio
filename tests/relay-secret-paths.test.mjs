import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  assertNoTrackedRelaySecretPaths,
  forbiddenRelaySecretPaths,
} from "../scripts/check-relay-secret-paths.mjs";
import {
  assertNoRelayRootBuildArtifacts,
  harnessSourceSnapshot,
  relaySourceSnapshot,
} from "../scripts/relay-fault-source-snapshot.mjs";

test("release source rejects force-tracked Relay secret directories without reading them", () => {
  const fakeTrackedPaths = [
    "README.md",
    "deploy/secrets/fake-only-for-path-test",
    "infra\\postgres\\local\\fake-dsn",
  ];
  assert.deepEqual(forbiddenRelaySecretPaths(fakeTrackedPaths), [
    "deploy/secrets/fake-only-for-path-test",
    "infra/postgres/local/fake-dsn",
  ]);
  assert.throws(
    () => assertNoTrackedRelaySecretPaths(fakeTrackedPaths),
    /forbidden secret path/,
  );
  assert.doesNotThrow(() => assertNoTrackedRelaySecretPaths([
    "deploy/relay-secure.env.example",
    "scripts/prepare-relay-local-db-secrets.ps1",
  ]));
});

test("candidate and harness snapshots cannot package Relay secret directories", async () => {
  for (const snapshot of [await relaySourceSnapshot(), await harnessSourceSnapshot()]) {
    assert.deepEqual(forbiddenRelaySecretPaths(snapshot.files), []);
  }
  const faultRunner = readFileSync(
    new URL("../scripts/run-relay-fault-harness.mjs", import.meta.url),
    "utf8",
  );
  const liveFaultRunner = readFileSync(
    new URL("../scripts/run-relay-live-fault-harness.mjs", import.meta.url),
    "utf8",
  );
  for (const runner of [faultRunner, liveFaultRunner]) {
    assert.match(runner, /resolve\(workspace, "backend", "new-api-relay"\)/);
    assert.doesNotMatch(runner, /deploy[\\/]secrets|infra[\\/]postgres[\\/]local/);
  }
});

test("candidate source rejects repository-root Relay build artifacts", () => {
  assert.throws(
    () => assertNoRelayRootBuildArtifacts([
      fileURLToPath(new URL("../backend/new-api-relay/.tmp-new-api-lifecycle", import.meta.url)),
    ]),
    /forbidden temporary build artifact/,
  );
  assert.doesNotThrow(() => assertNoRelayRootBuildArtifacts([
    fileURLToPath(new URL("../backend/new-api-relay/model/main.go", import.meta.url)),
  ]));
});
