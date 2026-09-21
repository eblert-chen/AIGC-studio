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
const packageManifest = JSON.parse(
  await readFile(new URL("../package.json", import.meta.url), "utf8"),
);
const playwrightConfig = await readFile(
  new URL("../playwright.config.mjs", import.meta.url),
  "utf8",
);
const browserGate = await readFile(
  new URL("./browser/critical-surfaces.spec.mjs", import.meta.url),
  "utf8",
);
const mobileWorkbenchGate = await readFile(
  new URL("./browser/advanced-workbench-mobile-gate.spec.mjs", import.meta.url),
  "utf8",
);
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
const platformClosureMigration = await readFile(
  new URL("../backend/platform/migrations/versions/0049_payment_finance_closure.py", import.meta.url),
  "utf8",
);
const platformPrivilegeFacade = await readFile(
  new URL("../backend/platform/platform_api/database_privileges.py", import.meta.url),
  "utf8",
);
const currentPlatformPolicyAlias = platformPrivilegeFacade.match(
  /^CURRENT_PLATFORM_DATABASE_PRIVILEGE_POLICY = (_policy_v([0-9]+))$/m,
);
if (!currentPlatformPolicyAlias) {
  throw new Error("Platform privilege facade does not expose a canonical current policy alias");
}
const currentPlatformPolicyVersion = Number.parseInt(currentPlatformPolicyAlias[2], 10);
const currentPlatformPolicy = await readFile(
  new URL(
    `../backend/platform/platform_api/database_privileges_v${currentPlatformPolicyVersion}.py`,
    import.meta.url,
  ),
  "utf8",
);
const currentPlatformHeadMatch = currentPlatformPolicy.match(/^ALEMBIC_HEAD = "([a-z0-9_]+)"$/m);
if (!currentPlatformHeadMatch) throw new Error("Current Platform policy does not bind an Alembic head");
const currentPlatformHead = currentPlatformHeadMatch[1];
const currentPlatformMigration = await readFile(
  new URL(`../backend/platform/migrations/versions/${currentPlatformHead}.py`, import.meta.url),
  "utf8",
);
const currentPlatformPreviousMatch = currentPlatformMigration.match(
  /^down_revision: str \| None = "([a-z0-9_]+)"$/m,
);
if (!currentPlatformPreviousMatch) throw new Error("Current Platform migration does not bind one direct predecessor");
const currentPlatformPrevious = currentPlatformPreviousMatch[1];
const unqualifiedPlatformPolicies = await Promise.all(Array.from(
  { length: currentPlatformPolicyVersion - 11 },
  (_, index) => index + 12,
).map((version) =>
  readFile(new URL(`../backend/platform/platform_api/database_privileges_v${version}.py`, import.meta.url), "utf8"),
));
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

test("release overview binds the current source head without claiming database qualification", () => {
  assert.match(platformClosureMigration, /revision: str = "0049_payment_finance_closure"/);
  assert.match(platformClosureMigration, /down_revision: str \| None = "0048_commercial_billing"/);
  assert.match(
    platformPrivilegeFacade,
    new RegExp(`CURRENT_PLATFORM_DATABASE_PRIVILEGE_POLICY = ${currentPlatformPolicyAlias[1]}`),
  );
  assert.match(currentPlatformMigration, new RegExp(`revision: str = "${currentPlatformHead}"`));
  assert.match(currentPlatformMigration, new RegExp(`down_revision: str \\| None = "${currentPlatformPrevious}"`));
  for (const policy of unqualifiedPlatformPolicies) {
    assert.match(policy, /UNQUALIFIED_CATALOG_SHA256 = "0" \* 64/);
    assert.match(policy, /^CATALOG_SHA256 = UNQUALIFIED_CATALOG_SHA256$/m);
  }
  for (const source of [readme, deploymentRunbook, releaseReadiness]) {
    assert.match(source, new RegExp("当前源码 head (?:是|为)\\s*`" + currentPlatformHead + "`"));
    assert.match(source, new RegExp("直接前序为\\s*`" + currentPlatformPrevious + "`"));
    assert.match(source, new RegExp(`数据库权限策略(?:当前)?为 v${currentPlatformPolicyVersion}`));
    assert.match(source, new RegExp(`v${currentPlatformPolicyVersion}[\\s\\S]{0,64}UNQUALIFIED`));
    assert.match(source, /v12\/0047[\s\S]{0,40}v13\/0048[\s\S]{0,20}(?:尚未|未)\s*资格化/);
    assert.match(source, /BLOCKED \/ NO-GO/);
    assert.match(source, /不能证明当前链或授权生产/);
    assert.doesNotMatch(source, /当前唯一(?:资格化)?发布值为/);
  }
  assert.match(readme, /0045_system_audit_actor/);
  assert.match(deploymentRunbook, /0045_system_audit_actor/);
  assert.match(releaseReadiness, /0045_system_audit_actor/);
  assert.match(readme, /0044_account_product_partition/);
  assert.match(readme, /0041_model_capability_releases/);
  assert.match(deploymentRunbook, /0041_model_capability_releases/);
  assert.match(releaseReadiness, /0041_model_capability_releases/);
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

test("frontend CI executes real desktop and extreme-phone browser gates", () => {
  assert.equal(packageManifest.scripts["test:browser"], "playwright test");
  assert.equal(packageManifest.devDependencies["@playwright/test"], "1.55.1");
  assert.match(workflow, /npx playwright install --with-deps chromium/);
  assert.match(workflow, /npm run test:browser/);
  assert.match(playwrightConfig, /name: "desktop-1440"/);
  assert.match(playwrightConfig, /name: "phone-390"/);
  assert.match(playwrightConfig, /name: "phone-320"/);
  assert.match(playwrightConfig, /VITE_ENABLE_DEMO: "true"/);
  assert.match(playwrightConfig, /--strictPort/);
  assert.match(playwrightConfig, /--enable-webgl/);
  assert.match(playwrightConfig, /--enable-unsafe-swiftshader/);
  assert.match(browserGate, /\.generate-button/);
  assert.match(browserGate, /public login keeps one clear, keyboard-reachable recovery action/);
  assert.match(browserGate, /auth-entry\.html\?logged_out=1/);
  assert.match(browserGate, /公司管理导航/);
  assert.match(browserGate, /平台管理员模块/);
  assert.match(browserGate, /expectNoHorizontalPageOverflow/);
  assert.match(mobileWorkbenchGate, /testInfo\.project\.name\.startsWith\("phone-"\)/);
  assert.match(mobileWorkbenchGate, /\/creation\?workbench=\$\{workbench\.id\}/);
  assert.match(mobileWorkbenchGate, /data-ui="desktop-workbench-gate"/);
  assert.match(mobileWorkbenchGate, /data-ui="workbench-editor"/);
  assert.match(mobileWorkbenchGate, /iframe\[title="StoryAI 3D 导演台"\]/);
  assert.match(mobileWorkbenchGate, /desktop deep links still mount the advanced workbenches/);
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
