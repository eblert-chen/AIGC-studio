import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const workflow = await readFile(
  new URL("../.github/workflows/relay-live-staging-acceptance.yml", import.meta.url),
  "utf8",
);

const uploadArtifactV7Pin =
  "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1";
const packageManifest = JSON.parse(
  await readFile(new URL("../package.json", import.meta.url), "utf8"),
);
const runbook = await readFile(
  new URL("../docs/deployment-runbook.md", import.meta.url),
  "utf8",
);

test("live Relay acceptance is manual, default-branch-only, and environment protected", () => {
  assert.match(workflow, /^on:\n  workflow_dispatch:\s*$/m);
  assert.doesNotMatch(workflow, /^\s{2}(?:push|pull_request|schedule):/m);
  assert.match(workflow, /^    environment: relay-staging-acceptance$/m);
  assert.match(workflow, /GITHUB_REF[\s\S]*refs\/heads\/\$\{DEFAULT_BRANCH\}/);
  assert.match(workflow, /^permissions:\n  contents: read$/m);
  assert.match(workflow, /cancel-in-progress: false/);
});

test("live Relay acceptance fails closed on fixed protected inputs without printing values", () => {
  for (const name of [
    "RELAY_STAGING_ACCEPTANCE_CONFIG_B64",
    "RELAY_STAGING_ACCEPTANCE_EVIDENCE_BUNDLE_B64",
    "RELAY_ACCEPTANCE_TENANT_A_API_KEY",
    "RELAY_ACCEPTANCE_TENANT_B_API_KEY",
    "RELAY_ACCEPTANCE_FAULT_CONTROL_TOKEN",
  ]) {
    assert.match(workflow, new RegExp(name));
  }
  assert.match(workflow, /Missing protected staging input/);
  assert.match(workflow, /acceptance config must use the fixed tenant credential names/);
  assert.match(workflow, /acceptance config must use the fixed fault-control credential name/);
  assert.match(workflow, /evidence bundle contains an unsafe path/);
  assert.match(workflow, /totalEvidenceBytes > 20 \* 1024/);
  assert.match(workflow, /only small metadata evidence/);
  assert.doesNotMatch(workflow, /echo\s+[^\n]*(?:API_KEY|TOKEN|CONFIG_B64|EVIDENCE_BUNDLE_B64)/);
  assert.doesNotMatch(workflow, /--config\s+\$\{\{\s*(?:secrets|vars)\./);
});

test("live Relay acceptance verifies baseline, executes all gates, and uploads only the report", () => {
  assert.ok(workflow.includes(uploadArtifactV7Pin));
  const baseline = workflow.indexOf("npm run relay:candidate:check");
  const acceptance = workflow.indexOf("node scripts/relay-migration-acceptance.mjs");
  const upload = workflow.indexOf("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a");
  assert(baseline >= 0 && acceptance > baseline && upload > acceptance);
  assert.match(workflow, /--execute-all/);
  assert.match(workflow, /technical_acceptance_passed !== true/);
  assert.match(workflow, /OFFLINE_PARITY_PASSED_REQUIRES_EXTERNAL_RELEASE_GATES/);
  assert.match(
    workflow,
    /report\.gates\?\.\["contract\.candidate_readiness"\]\?\.status !== "PASS"/,
  );
  assert.match(
    workflow,
    /report\.gates\?\.\["contract\.candidate_readiness_final"\]\?\.status !== "PASS"/,
  );
  assert.match(workflow, /entry\?\.phase === "final"/);
  assert.match(workflow, /finalReadiness\?\.state !== "healthy"/);
  assert.match(workflow, /finalReadiness\?\.provider_runtime_state !== "healthy"/);
  assert.match(workflow, /finalReadiness\?\.monitor_fresh !== true/);
  assert.match(workflow, /finalReadiness\?\.monitor_last_error_code !== ""/);
  for (const field of [
    "active_alerts",
    "unavailable_routes",
    "alert_backlog",
    "alert_dead_letter",
    "task_stage_backlog",
    "task_stage_dead_letter",
    "operations_snapshot_backlog",
    "operations_snapshot_dead_letter",
    "provider_result_reconciliation_backlog",
  ]) {
    assert.match(workflow, new RegExp(`"${field}"`));
  }
  assert.match(workflow, /final candidate readiness evidence is not production-cutover clean/);
  assert.match(workflow, /const workflowSourceRevision = process\.env\.GITHUB_SHA \?\? ""/);
  assert.match(workflow, /report\.candidate\?\.git_revision !== workflowSourceRevision/);
  assert.match(workflow, /finalReadiness\?\.source_git_revision !== workflowSourceRevision/);
  assert.match(
    workflow,
    /finalReadiness\?\.upstream_git_revision !== report\.candidate\?\.upstream_git_revision/,
  );
  assert.match(workflow, /finalReadiness\?\.image_digest !== report\.candidate\?\.image_digest/);
  assert.match(
    workflow,
    /final candidate readiness provenance does not match the workflow release identity/,
  );
  assert.match(workflow, /credentialBytes\.length < 8/);
  assert.match(workflow, /reportBytes\.includes\(credentialBytes\)/);
  assert(workflow.indexOf("reportBytes.includes(credentialBytes)") < upload);
  assert.match(workflow, /sha256sum "\$\{ACCEPTANCE_REPORT_PATH\}"/);
  assert.match(workflow, /if: \$\{\{ success\(\) \}\}/);
  assert.match(workflow, /if-no-files-found: error/);
  assert.doesNotMatch(workflow, /path:[\s\S]{0,160}(?:config\.json|evidence\/)/);
  assert.match(workflow, /EXPECTED_SOURCE_GIT_REVISION: \$\{\{ github\.sha \}\}/);
  assert.match(workflow, /\^\[0-9a-f\]\{40\}\$/);
  assert.match(
    workflow,
    /config\.candidate\?\.gitRevision !== expectedSourceGitRevision/,
  );
  assert.match(
    workflow,
    /acceptance candidate source revision must equal the workflow commit SHA/,
  );
  assert.match(workflow, /name: relay-live-staging-\$\{\{ github\.sha \}\}/);
  assert.match(
    packageManifest.scripts["test:relay-migration"],
    /tests\/relay-live-staging-workflow\.test\.mjs/,
  );
});

test("live Relay acceptance always validates and removes its fixed temporary directory", () => {
  const upload = workflow.indexOf("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a");
  const cleanup = workflow.indexOf("name: Remove protected acceptance inputs and report");
  assert(upload >= 0 && cleanup > upload);
  assert.match(workflow, /name: Remove protected acceptance inputs and report\n\s+if: \$\{\{ always\(\) \}\}/);
  assert.match(workflow, /expected_root="\/tmp\/relay-live-staging-\$\{GITHUB_RUN_ID\}-\$\{GITHUB_RUN_ATTEMPT\}"/);
  assert.match(workflow, /\^\/tmp\/relay-live-staging-\[0-9\]\+-\[0-9\]\+\$/);
  assert.match(workflow, /\[\[ -L "\$\{ACCEPTANCE_ROOT\}" \]\]/);
  assert.match(workflow, /rm -rf -- "\$\{ACCEPTANCE_ROOT\}"/);
});

test("runbook makes a protected successful run an explicit but non-sufficient promotion input", () => {
  assert.match(runbook, /GitHub Environment\s+`relay-staging-acceptance`/);
  assert.match(runbook, /成功 run 与上传的 `report\.json`/);
  assert.match(runbook, /production promotion/);
  assert.match(runbook, /不能替代 GitHub 仓库外的 branch protection/);
  assert.match(runbook, /required reviewers/);
  assert.match(runbook, /Environment variable.*容量限制/);
  assert.match(runbook, /20 KiB/);
});
