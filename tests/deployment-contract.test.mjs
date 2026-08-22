import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const compose = readFileSync(
  new URL("../docker-compose.yml", import.meta.url),
  "utf8",
);
const smoke = readFileSync(
  new URL("../scripts/smoke-local.ps1", import.meta.url),
  "utf8",
);
const gateway = readFileSync(
  new URL("../infra/nginx/platform-api.conf", import.meta.url),
  "utf8",
);

test("keeps the local smoke Relay identity aligned with Compose", () => {
  const composeClient = compose.match(
    /"client_id":"\$\{RELAY_CLIENT_ID:-([^}]+)\}"/,
  )?.[1];
  const composeKey = compose.match(
    /"api_key":"\$\{RELAY_API_KEY:-([^}]+)\}"/,
  )?.[1];
  const smokeClient = smoke.match(
    /\$RelayClientId\s*=\s*"([^"]+)"/,
  )?.[1];
  const smokeKey = smoke.match(
    /\$RelayApiKey\s*=\s*"([^"]+)"/,
  )?.[1];

  assert.equal(smokeClient, composeClient);
  assert.equal(smokeKey, composeKey);
  assert.doesNotMatch(compose, /^  RELAY_(?:CLIENT_ID|API_KEY):/m);
});

test("keeps the local smoke internal service identity aligned with Compose", () => {
  const composeToken = compose.match(
    /INTERNAL_SERVICE_TOKEN:\s*\$\{INTERNAL_SERVICE_TOKEN:-\$\{NEW_API_RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN:-([^}]+)\}\}/,
  )?.[1];
  const smokeToken = smoke.match(
    /\$InternalServiceToken\s*=\s*"(local-internal-service-token-[^"]+)"/,
  )?.[1];

  assert.equal(smokeToken, composeToken);
  assert.match(
    smoke,
    /GetEnvironmentVariable\("INTERNAL_SERVICE_TOKEN",\s*"Process"\)/,
  );
  assert.doesNotMatch(smoke, /Write-(?:Host|Output)[^\r\n]*InternalServiceToken/);
});

test("local smoke authenticates every bootstrap request without exposing the token", () => {
  assert.match(smoke, /\[string\]\$BootstrapToken\s*=\s*""/);
  assert.match(
    smoke,
    /GetEnvironmentVariable\("PLATFORM_BOOTSTRAP_TOKEN",\s*"Process"\)/,
  );
  assert.match(
    smoke,
    /Read-DotEnvValue\s+-Path\s+\$dotEnvPath\s+-Name\s+"PLATFORM_BOOTSTRAP_TOKEN"/,
  );
  assert.match(
    smoke,
    /\$BootstrapToken\s*=\s*"local-platform-bootstrap-secret-2026-08-14"/,
  );
  assert.match(
    smoke,
    /\$bootstrapHeaders\["X-Bootstrap-Token"\]\s*=\s*\$BootstrapToken/,
  );
  assert.equal(
    (smoke.match(/-Headers\s+\$bootstrapHeaders/g) ?? []).length,
    3,
    "all three bootstrap calls must use the protected header",
  );
  assert.doesNotMatch(smoke, /Write-(?:Host|Output)[^\r\n]*BootstrapToken/);
});

test("local smoke can reuse the development owner without weakening admin authorization", () => {
  assert.match(smoke, /\[string\]\$PlatformAdminUserId\s*=\s*""/);
  assert.match(
    smoke,
    /\$PlatformAdminUserId\s*=\s*\$bootstrappedAdmin\.user_id/,
  );
  assert.match(
    smoke,
    /"X-Platform-Admin-User-ID"\s*=\s*\$PlatformAdminUserId/,
  );
  assert.doesNotMatch(smoke, /development_platform_owner_user_ids/);
});

test("uses a video artifact for the image-to-video smoke task", () => {
  assert.match(smoke, /mode\s*=\s*"image_to_video"/);
  assert.match(smoke, /media_type\s*=\s*"video"/);
  assert.match(smoke, /content_type\s*=\s*"video\/mp4"/);
});

test("local smoke treats incomplete provider cost as known—not final—profit", () => {
  assert.match(smoke, /dashboardAfterCost\.known_gross_profit_cents/);
  assert.match(smoke, /\$expectedUnreconciledCount\s*-eq\s*0/);
  assert.match(
    smoke,
    /\$dashboardAfterCost\.gross_profit_cents\s*-ne\s*\$expectedGrossProfit/,
  );
  assert.doesNotMatch(
    smoke,
    /dashboardBeforeCost\.gross_profit_cents\s*-\s*9/,
  );
});

test("local smoke never fabricates trusted download completion from a browser GET", () => {
  assert.doesNotMatch(
    smoke,
    /-Uri\s+"\$PlatformBase\/internal\/artifact-download-completions/,
  );
  assert.match(smoke, /\$postTransferRecords\.items\[0\]\.status\s*-ne\s*"issued"/);
  assert.match(smoke, /trusted_download_completion\s*=\s*\$false/);
});

test("gateway re-resolves the platform container after a Compose deploy", () => {
  assert.match(gateway, /resolver\s+127\.0\.0\.11\b[^;]*;/);
  assert.match(gateway, /upstream\s+platform_api\s*{[\s\S]*?\bzone\s+/);
  assert.match(
    gateway,
    /server\s+platform-api:8000\s+resolve\s*;/,
  );
});

test("gateway fails closed instead of exposing Relay or internal routes", () => {
  assert.match(gateway, /location\s+\/\s*{[\s\S]*?return\s+404\s*;/);
  assert.doesNotMatch(gateway, /proxy_pass\s+http:\/\/relay/i);
});

test("gateway exposes only the exact signed Relay-to-Platform ingress paths", () => {
  const allowlist = gateway.match(
    /location\s+~\s+"\^\(\?:([^\r\n"]+)\)\$"\s*\{([\s\S]*?)\n\s*\}/,
  );
  assert.ok(allowlist, "missing anchored Relay internal ingress allowlist");
  const paths = allowlist[1].split("|").sort();
  assert.deepEqual(paths, [
    "/internal/artifact-download-completions/edge-gateway",
    "/internal/channel-costs",
    "/internal/relay-callbacks",
    "/internal/relay-callbacks/[a-z0-9][a-z0-9._-]{0,63}",
    "/internal/relay/operations-snapshots",
    "/internal/relay/provider-alerts",
    "/internal/relay/task-stages",
  ]);
  assert.match(allowlist[2], /proxy_pass\s+http:\/\/platform_api\s*;/);
  assert.match(
    gateway,
    /location\s+\/internal\/\s*\{[\s\S]*?return\s+404\s*;/,
  );
  assert.doesNotMatch(gateway, /location\s+(?:\^~\s+)?\/internal\/\s*\{[\s\S]*?proxy_pass/);
});
