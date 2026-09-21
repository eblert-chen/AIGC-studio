import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const script = readFileSync(
  new URL("../scripts/start-all-local.ps1", import.meta.url),
  "utf8",
);
const packageJson = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));

test("local service startup fixes the authenticated env-file order", () => {
  const envFiles = Array.from(
    script.matchAll(/"--env-file",\s*"([^"]+)"/g),
    (match) => match[1],
  );
  assert.deepEqual(envFiles, [
    ".env",
    "deploy/secrets/huawei-obs.runtime.env",
    "deploy/secrets/paid-canary.runtime.env",
    "deploy/secrets/platform-canary.runtime.env",
  ]);
  assert.match(script, /PLATFORM_OIDC_ENABLED[\s\S]*PLATFORM_OIDC_ISSUER[\s\S]*PLATFORM_OIDC_CLIENT_ID/);
  assert.match(script, /PLATFORM_OIDC_ENABLED must be true/);
});

test("paid canary runtime admits only reviewed manifest bindings", () => {
  assert.match(script, /function Assert-PaidCanaryRouteInventory/);
  assert.match(script, /function Get-DotEnvDefinitionCount/);
  assert.match(script, /function Get-ComposeResolvedEnvironment/);
  assert.match(
    script,
    /Assert-PaidCanaryRouteInventory[\s\S]*-RawRoutes \$composeEnvironment\[\$routeSettingName\][\s\S]*-Environment \$relayCompatEnvironment/,
  );
  assert.match(script, /HashSet\[string\][\s\S]*reviewedModelIDs/);
  assert.match(script, /generationprofile\/seedance_models\.v1\.json/);
  assert.match(script, /generationprofile\/minimax_h3_models\.v1\.json/);
  assert.match(script, /generationprofile\/google_video_models\.v1\.json/);
  assert.match(script, /lifecycle -ceq "acceptance_candidate"/);
  assert.match(script, /new_routes_allowed -eq \$true/);
  assert.match(script, /reviewedModelIDs\.Contains/);
  assert.match(script, /model_release\.public_model_id -cne \[string\]\$modelID/);
  assert.match(script, /"volcengine\.ark\.image-generation\.v1"/);
  assert.match(script, /legacy_public_aliases[\s\S]*"seedream-5-lite"/);
  assert.match(script, /Properties\['staging_ready'\][\s\S]*Properties\['production_ready'\]/);
  assert.match(script, /signed acceptance evidence instead of readiness booleans/);
  assert.match(script, /NEW_API_RELAY_COMPAT_ENVIRONMENT/);
  assert.match(script, /\$relayCompatEnvironment -notin @\("development", "test", "staging", "production"\)/);
  assert.match(script, /\$Environment -in @\("staging", "production"\)/);
  assert.match(script, /model_release\.attestation\.signature/);
  assert.match(script, /declaration\.acceptance\.signature/);
  assert.match(script, /requires both signed model-release attestation and route acceptance evidence/);
  assert.match(script, /\$routeDefinitionCount -ne 1/);
  assert.match(script, /\$routeDefinitionSource -cne "deploy\/secrets\/paid-canary\.runtime\.env"/);
  assert.match(script, /\$definitionPattern = "\^\\s\*\(\?:export\\s\+\)\?\$escapedName\\s\*\(\?:=\|:\)"/);
  assert.match(script, /config", "--environment"/);
  assert.match(script, /\$composeEnvironment\[\$compatSettingName\]\.Trim\(\)\.ToLowerInvariant\(\)/);
  assert.match(script, /\$compatDefinitionSource -cne "deploy\/secrets\/paid-canary\.runtime\.env"/);
  assert.match(script, /Ambient NEW_API_RELAY_MODEL_ROUTES_JSON overrides are forbidden/);
  assert.match(script, /if \(\$null -ne \$ambientRouteSetting\)/);
  assert.match(script, /Ambient NEW_API_RELAY_COMPAT_ENVIRONMENT overrides are forbidden/);
  assert.match(script, /if \(\$null -ne \$ambientCompatEnvironment\)/);
  assert.doesNotMatch(script, /video\.seedance\.2/);
});

test("local service startup validates, starts, and verifies the real login boundary", () => {
  assert.match(script, /Invoke-Compose -CommandArguments @\("config", "--quiet"\)/);
  assert.match(script, /Invoke-Compose -CommandArguments @\("up", "-d"\)/);
  assert.match(script, /Test-ComposeOneShotSucceeded -Service "relay-new-api-db-role-post"/);
  assert.match(script, /if \(\$relayBootstrapComplete\)/);
  assert.match(script, /running without a successful role-post bootstrap receipt/);
  assert.match(script, /http:\/\/127\.0\.0\.1:8300\/health\/ready/);
  assert.match(script, /http:\/\/127\.0\.0\.1:8400\/health\/ready/);
  assert.match(script, /"up", "-d", "--build", "--no-deps", "--force-recreate"[\s\S]*"platform-api"/);
  assert.match(
    script,
    /"up", "-d", "--build", "--no-deps", "--force-recreate"[\s\S]*"platform-timeout-worker"[\s\S]*"platform-relay-catalog-sync"[\s\S]*"platform-relay-sync"/,
  );
  assert.match(script, /Invoke-Compose -CommandArguments @\("restart", "api-gateway"\)/);
  assert.match(script, /http:\/\/127\.0\.0\.1:8200\/health/);
  assert.match(script, /http:\/\/127\.0\.0\.1:8180\/health/);
  assert.match(script, /http:\/\/127\.0\.0\.1:8180\/api\/v1\/auth\/session/);
  assert.match(script, /login_available -eq \$true/);
  assert.match(script, /http:\/\/127\.0\.0\.1:5173\/platform/);
});

test("local service startup rebuilds a changed Relay before the ordered lifecycle upgrade", () => {
  assert.match(script, /function Get-RelayRuntimeBuildIdentity/);
  assert.match(script, /X-Relay-Source-Revision/);
  assert.match(script, /X-Relay-Source-Snapshot-SHA256/);
  assert.match(script, /X-Relay-Source-File-Count/);
  assert.match(script, /function Assert-RelayTargetImageIdentity/);
  assert.match(script, /--network none[\s\S]*--read-only[\s\S]*relay-build-identity/);
  assert.match(script, /Assert-RelayTargetImageIdentity[\s\S]*-Image \$relayTaggedImageId/);
  assert.match(script, /Compose Relay image tag changed during offline identity verification/);
  assert.match(
    script,
    /-not \$relayBootstrapComplete[\s\S]*IsNullOrWhiteSpace\(\$relayApiContainerId\)[\s\S]*IsNullOrWhiteSpace\(\$relayEdgeContainerId\)/,
  );
  assert.match(
    script,
    /\$relayRuntimeMatchesTarget = \([\s\S]*\$relayApiImageId -ceq \$relayTaggedImageId[\s\S]*\$relayEdgeImageId -ceq \$relayTaggedImageId[\s\S]*SourceRevision -ceq \$relayTargetSourceRevision[\s\S]*SourceSnapshotSHA256 -ceq \$relayTargetSourceSnapshot[\s\S]*SourceFileCount -eq \$relayTargetSourceFileCount/,
  );

  const upgradeStart = script.indexOf(
    "if ($relayBootstrapComplete -and -not $relayRuntimeMatchesTarget)",
  );
  const longLivedStart = script.indexOf(
    "if ($relayBootstrapComplete) {",
    upgradeStart + 1,
  );
  assert.ok(upgradeStart >= 0 && longLivedStart > upgradeStart);
  const upgrade = script.slice(upgradeStart, longLivedStart);
  const build = upgrade.indexOf('"build", "relay-new-api"');
  const prove = upgrade.indexOf("Assert-RelayTargetImageIdentity");
  const stopEdge = upgrade.indexOf('"stop", "relay-download-edge"');
  const stopApi = upgrade.indexOf('"stop", "relay-new-api"');
  const volumeInit = upgrade.indexOf('"relay-new-api-volume-init"');
  const rolePre = upgrade.indexOf('"relay-new-api-db-role-pre"');
  const migrate = upgrade.indexOf('"relay-new-api-migrate"');
  const rolePost = upgrade.indexOf('"relay-new-api-db-role-post"');
  assert.ok(build >= 0 && build < prove);
  assert.ok(prove < stopEdge && stopEdge < stopApi);
  assert.ok(stopApi < volumeInit);
  assert.ok(volumeInit < rolePre && rolePre < migrate && migrate < rolePost);
  assert.match(upgrade, /Invoke-ComposeOneShot/);
  assert.doesNotMatch(upgrade, /"up"[^\r\n]*"relay-new-api"[^\r\n]*"relay-download-edge"/);

  assert.match(
    script,
    /if \(\$relayRuntimeUpgradePerformed\)[\s\S]*"--force-recreate"[\s\S]*"relay-new-api"[\s\S]*"relay-download-edge"/,
  );
  assert.match(
    script,
    /Exact source and image identity:[\s\S]*do not rerun privileged database one-shots/,
  );
});

test("local service startup remains non-destructive and never echoes auth values", () => {
  assert.doesNotMatch(script, /\bdown\b/i);
  assert.doesNotMatch(script, /--volumes|--remove-orphans|\bprune\b/i);
  assert.doesNotMatch(script, /Write-(?:Output|Host)[^\r\n]*(?:OIDC|OWNER_USER|SECRET|TOKEN)/i);
  assert.match(script, /refusing to stop an unknown process/);
  assert.match(script, /low-disruption warm[\s\S]*do not rerun privileged database one-shots/);
});

test("package exposes one stable Windows startup command", () => {
  assert.equal(
    packageJson.scripts["services:start:local"],
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start-all-local.ps1",
  );
});
