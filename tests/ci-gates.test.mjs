import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { candidateImageArgs } from "../scripts/relay-candidate-image-args.mjs";
import {
  CANDIDATE_IMAGE_LABELS,
  expectedCandidateImageLabels,
  relaySourceSnapshot,
} from "../scripts/relay-fault-source-snapshot.mjs";
import { CANDIDATE_UPSTREAM_GIT_REVISION } from "../scripts/relay-migration-acceptance.mjs";
import {
  acceptanceResourceLabels,
  runnerPlatformBinding,
} from "../scripts/run-cross-service-cost-acceptance.mjs";

const workflow = await readFile(new URL("../.github/workflows/ci.yml", import.meta.url), "utf8");
const readme = await readFile(new URL("../README.md", import.meta.url), "utf8");
const costRunner = await readFile(
  new URL("../scripts/run-cross-service-cost-acceptance.mjs", import.meta.url),
  "utf8",
);
const sourceControl = await readFile(new URL("../docs/source-control.md", import.meta.url), "utf8");
const deploymentRunbook = await readFile(
  new URL("../docs/deployment-runbook.md", import.meta.url),
  "utf8",
);
const releaseReadiness = await readFile(
  new URL("../docs/release-readiness.md", import.meta.url),
  "utf8",
);
const routeAcceptanceTrustDigest = `sha256:${"1".repeat(64)}`;

test("CI builds a provenance-bound candidate and executes the real cross-service cost gate", () => {
  assert.match(workflow, /^  cross-service-cost:\s*$/m);
  assert.match(workflow, /node scripts\/relay-candidate-image-args\.mjs/);
  assert.match(workflow, /mapfile -t relay_build_args/);
  assert.match(workflow, /docker build "\$\{relay_build_args\[@\]\}"/);
  assert.match(
    workflow,
    /NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256: sha256:[1-9a-f][0-9a-f]{63}/,
  );
  assert.match(workflow, /node scripts\/run-cross-service-cost-acceptance\.mjs/);
  assert.match(workflow, /COST_CANDIDATE_IMAGE: ai-video\/new-api-relay:ci-cost-/);
  assert.match(workflow, /--candidate-image \$\{COST_CANDIDATE_IMAGE\}/);
  assert.match(workflow, /--out \$\{COST_EVIDENCE_PATH\}/);
  assert.match(
    workflow,
    /Run real cross-service channel-cost acceptance[\s\S]*?COST_EVIDENCE_PATH: \$\{\{ runner\.temp \}\}/,
  );
  assert.match(workflow, /--python \$\{GITHUB_WORKSPACE\}\/\.venv-cost-ci\/bin\/python/);
  assert.match(workflow, /Reject leaked runner-owned acceptance resources/);
  assert.match(workflow, /COST_ACCEPTANCE_RUN_LABEL: \$\{\{ github\.run_id \}\}-\$\{\{ github\.run_attempt \}\}/);
  assert.match(workflow, /docker ps -a --filter "label=ai\.video\.acceptance-ci-run=/);
  assert.match(workflow, /docker volume ls --filter "label=ai\.video\.acceptance-ci-run=/);
  assert.match(workflow, /docker network ls --filter "label=ai\.video\.acceptance-ci-run=/);
  assert.match(workflow, /exit "\$leaked"/);
  assert.match(workflow, /actions\/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a/);
  assert.match(workflow, /if-no-files-found: error/);
});

test("release overview names the current migration heads", () => {
  assert.match(readme, /0040_showcase_management/);
  assert.match(deploymentRunbook, /0040_showcase_management/);
  assert.match(releaseReadiness, /0040_showcase_management/);
  assert.match(readme, /0039_new_api_relay_defaults/);
  assert.match(deploymentRunbook, /0039_new_api_relay_defaults/);
  assert.match(releaseReadiness, /0039_new_api_relay_defaults/);
  assert.match(readme, /0038_download_evidence_checks/);
  assert.match(readme, /0012_generation_contract_v1/);
  assert.doesNotMatch(readme, /0027_channel_cost_evidence/);
  assert.doesNotMatch(deploymentRunbook, /0028_provider_alert_bridge/);
  assert.doesNotMatch(releaseReadiness, /0028_provider_alert_bridge/);
});

test("one stable required check aggregates every executable gate", () => {
  assert.match(workflow, /^  required-gates:\s*$/m);
  assert.match(workflow, /name: Required CI gates/);
  for (const job of [
    "frontend",
    "platform",
    "offline-python-relay-oracle",
    "new-api-web",
    "new-api-go-race",
    "new-api-postgres-redis",
    "cross-service-cost",
    "contracts",
  ]) {
    assert.match(workflow, new RegExp(`^\\s+- ${job}$`, "m"), `required-gates is missing ${job}`);
  }
  assert.match(workflow, /value\.result !== "success"/);
  assert.match(workflow, /Offline historical Python Relay oracle regression/);
  assert.doesNotMatch(workflow, /^  python-relay:\s*$/m);
  assert.match(sourceControl, /Required CI gates/);
});

test("candidate image helper emits the complete source-bound label and build argument set", async () => {
  const snapshot = await relaySourceSnapshot();
  const args = await candidateImageArgs({
    NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256: routeAcceptanceTrustDigest,
  });
  const expectedLabels = expectedCandidateImageLabels(snapshot, CANDIDATE_UPSTREAM_GIT_REVISION);

  for (const [label, value] of Object.entries(expectedLabels)) {
    assert(args.includes(`${label}=${value}`), `missing candidate label ${label}`);
  }
  assert.equal(args.filter((value) => value === "--label").length, Object.keys(CANDIDATE_IMAGE_LABELS).length);
  assert.equal(args.filter((value) => value === "--build-arg").length, 5);
  assert(args.includes(`RELAY_BUILD_SOURCE_SNAPSHOT_SHA256=${snapshot.sha256}`));
  assert(args.includes(`RELAY_BUILD_SOURCE_SNAPSHOT_FILE_COUNT=${snapshot.file_count}`));
  assert(args.includes(`RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256=${routeAcceptanceTrustDigest}`));
  await assert.rejects(() => candidateImageArgs({}), /NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256/);
});

test("Linux cost runner exposes Platform to only the runner-owned Docker network", () => {
  assert.deepEqual(
    runnerPlatformBinding("linux", { IPAM: { Config: [{ Gateway: "172.28.0.1" }] } }),
    {
      bindHost: "0.0.0.0",
      containerHostAddress: "172.28.0.1",
      probeHost: "127.0.0.1",
    },
  );
  assert.deepEqual(
    runnerPlatformBinding("win32", {}),
    {
      bindHost: "127.0.0.1",
      containerHostAddress: "host-gateway",
      probeHost: "127.0.0.1",
    },
  );
  assert.throws(
    () => runnerPlatformBinding("linux", { IPAM: { Config: [] } }),
    /one safe IPv4 gateway/,
  );
  assert.match(costRunner, /"--host", platformBinding\.bindHost/);
  assert.match(
    costRunner,
    /`host\.docker\.internal:\$\{platformBinding\.containerHostAddress\}`/,
  );
});

test("acceptance resources carry a validated CI-run cleanup label", () => {
  assert.deepEqual(
    acceptanceResourceLabels("abcdef123456", { COST_ACCEPTANCE_RUN_LABEL: "42-3" }),
    [
      "--label",
      "ai.video.acceptance-run=abcdef123456",
      "--label",
      "ai.video.acceptance-ci-run=42-3",
    ],
  );
  assert.throws(
    () => acceptanceResourceLabels("abcdef123456", { COST_ACCEPTANCE_RUN_LABEL: "unsafe label" }),
    /invalid/,
  );
});
