import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const secure = read("deploy/compose.relay.secure.yml");
const envExample = read("deploy/relay-secure.env.example");
const productionEnvExample = read("deploy/relay-production.env.example");
const runbook = read("docs/deployment-runbook.md");
const releaseReadiness = read("docs/release-readiness.md");
const traceability = read("docs/requirements-traceability.md");
const stagingReadiness = read("docs/internal-staging-deployment-readiness-form.md");
const databasePrivilegesV5 = read("backend/platform/platform_api/database_privileges_v5.py");
const platformIngress = read("infra/nginx/platform-api.conf");
const platformTrustedEdge = read("infra/nginx/platform-api-trusted-edge.conf.template");

function serviceBlock(source, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(
    new RegExp(
      `^  ${escaped}:\\r?\\n([\\s\\S]*?)(?=^  [a-zA-Z0-9][a-zA-Z0-9_-]*:\\r?\\n|(?![\\s\\S]))`,
      "m",
    ),
  );
  assert.ok(match, `missing Compose service ${name}`);
  return match[0];
}

test("secure Platform API uses public OIDC PKCE and disables legacy browser bearer", () => {
  const api = serviceBlock(secure, "platform-api");
  for (const contract of [
    /AUTH_LEGACY_BEARER_ENABLED:\s*"false"/,
    /OIDC_ENABLED:\s*"true"/,
    /OIDC_SELF_SIGNUP_ENABLED:\s*\$\{PLATFORM_OIDC_SELF_SIGNUP_ENABLED:/,
    /OIDC_ISSUER:\s*\$\{PLATFORM_OIDC_ISSUER:/,
    /OIDC_AUTHORIZATION_ENDPOINT:\s*\$\{PLATFORM_OIDC_AUTHORIZATION_ENDPOINT:/,
    /OIDC_TOKEN_ENDPOINT:\s*\$\{PLATFORM_OIDC_TOKEN_ENDPOINT:/,
    /OIDC_JWKS_URI:\s*\$\{PLATFORM_OIDC_JWKS_URI:/,
    /OIDC_CLIENT_ID:\s*\$\{PLATFORM_OIDC_CLIENT_ID:/,
    /OIDC_REDIRECT_URI:\s*\$\{PLATFORM_PUBLIC_BASE_URL:[^\n]+\/api\/v1\/auth\/callback/,
    /FRONTEND_ORIGIN:\s*\$\{PLATFORM_FRONTEND_ORIGIN:/,
    /ACCOUNT_MANAGEMENT_URL:\s*\$\{PLATFORM_ACCOUNT_MANAGEMENT_URL:\?set fixed HTTPS IdP account-management URL\}/,
    /PLATFORM_ADMIN_STEP_UP_MAX_AGE_SECONDS:\s*\$\{PLATFORM_ADMIN_STEP_UP_MAX_AGE_SECONDS:-300\}/,
  ]) assert.match(secure, contract);

  assert.doesNotMatch(api, /^\s+JWT_ISSUER:/m);
  assert.doesNotMatch(api, /^\s+JWT_AUDIENCE:/m);
  assert.doesNotMatch(api, /OIDC_CLIENT_SECRET|client_secret/i);
  assert.match(api, /--no-access-log/);
});

test("OIDC callback capabilities are excluded from proxy and application access logs", () => {
  const callback = platformIngress.match(
    /location = \/api\/v1\/auth\/callback \{([\s\S]*?)^    \}/m,
  );
  assert.ok(callback, "missing exact OIDC callback ingress");
  assert.match(callback[1], /access_log off;/);
  assert.match(callback[1], /error_log \/dev\/stderr crit;/);
  assert.match(callback[1], /Cache-Control "no-store"/);
  assert.match(callback[1], /Referrer-Policy no-referrer/);
});

test("protected browser ingress has one non-spoofable proxy boundary", () => {
  const api = serviceBlock(secure, "platform-api");
  const gateway = serviceBlock(secure, "api-gateway");
  assert.match(api, /ports:\s*!reset\s*\[\]/);
  assert.match(
    api,
    /--forwarded-allow-ips",\s*"\$\{PLATFORM_API_GATEWAY_IP:\?set the fixed dedicated Platform gateway IP\}"/,
  );
  assert.doesNotMatch(api, /--forwarded-allow-ips",\s*"?\*"?/);
  assert.match(
    api,
    /networks:\s*!override[\s\S]*?platform-api-ingress:[\s\S]*?ipv4_address:\s*\$\{PLATFORM_API_INTERNAL_IP:/,
  );
  assert.match(gateway, /PLATFORM_TRUSTED_EDGE_CIDR:\s*\$\{PLATFORM_TRUSTED_EDGE_CIDR:/);
  assert.match(gateway, /platform-api-trusted-edge\.conf\.template/);
  assert.match(gateway, /ipv4_address:\s*\$\{PLATFORM_API_GATEWAY_IP:/);
  assert.match(secure, /PLATFORM_API_INGRESS_SUBNET:\?set a dedicated non-overlapping Platform ingress subnet/);

  assert.doesNotMatch(platformIngress, /proxy_add_x_forwarded_for/);
  assert.equal(
    [...platformIngress.matchAll(/proxy_set_header X-Forwarded-For \$remote_addr;/g)].length,
    4,
  );
  assert.match(platformTrustedEdge, /set_real_ip_from \$\{PLATFORM_TRUSTED_EDGE_CIDR\};/);
  assert.match(platformTrustedEdge, /real_ip_header X-Forwarded-For;/);
  assert.match(platformTrustedEdge, /real_ip_recursive on;/);

  for (const key of [
    "PLATFORM_API_INGRESS_NETWORK_NAME",
    "PLATFORM_API_INGRESS_SUBNET",
    "PLATFORM_API_GATEWAY_IP",
    "PLATFORM_API_INTERNAL_IP",
    "PLATFORM_TRUSTED_EDGE_CIDR",
  ]) assert.match(envExample, new RegExp(`^${key}=`, "m"));
  assert.match(runbook, /不得使用\s*`--forwarded-allow-ips=\*`/);
  assert.match(runbook, /两个真实来源.*`ip_hash` 不同/);
});

test("OIDC browser configuration is not copied into non-API Platform processes", () => {
  for (const role of [
    "platform-migrate",
    "platform-dispatcher",
    "platform-relay-sync",
    "platform-timeout-worker",
    "platform-publishing-worker",
    "platform-download-gateway-registration-worker",
  ]) {
    const block = serviceBlock(secure, role);
    assert.doesNotMatch(block, /^\s+(?:AUTH_LEGACY_BEARER_ENABLED|OIDC_|FRONTEND_ORIGIN|ACCOUNT_MANAGEMENT_URL)/m);
  }
});

test("operator template and runbook describe BFF cookies without a client secret or browser token", () => {
  for (const key of [
    "PLATFORM_FRONTEND_ORIGIN",
    "PLATFORM_OIDC_SELF_SIGNUP_ENABLED",
    "PLATFORM_OIDC_ISSUER",
    "PLATFORM_OIDC_AUTHORIZATION_ENDPOINT",
    "PLATFORM_OIDC_TOKEN_ENDPOINT",
    "PLATFORM_OIDC_JWKS_URI",
    "PLATFORM_OIDC_CLIENT_ID",
    "PLATFORM_ACCOUNT_MANAGEMENT_URL",
    "PLATFORM_AUTH_SESSION_TTL_SECONDS",
    "PLATFORM_AUTH_SESSION_IDLE_TTL_SECONDS",
    "PLATFORM_AUTH_ACCOUNT_STEP_UP_MAX_AGE_SECONDS",
    "PLATFORM_ADMIN_STEP_UP_MAX_AGE_SECONDS",
    "PLATFORM_OIDC_LOGIN_IP_WINDOW_SECONDS",
    "PLATFORM_OIDC_LOGIN_IP_MAX_ATTEMPTS",
    "PLATFORM_INVITATION_TTL_SECONDS",
  ]) assert.match(envExample, new RegExp(`^${key}=`, "m"));

  assert.match(runbook, /Authorization Code \+ PKCE public client/);
  assert.match(runbook, /__Host-ai_video_session/);
  assert.match(runbook, /__Host-ai_video_csrf/);
  assert.match(runbook, /生产构建固定忽略旧 `sessionStorage\["ai-video\.access-token"\]`/);
  assert.doesNotMatch(envExample, /PLATFORM_OIDC_CLIENT_SECRET|OIDC_CLIENT_SECRET/);
});

test("secure and production examples share one frontend placeholder site", () => {
  assert.match(
    envExample,
    /^PLATFORM_FRONTEND_ORIGIN=https:\/\/app\.example\.invalid$/m,
  );
  assert.match(
    productionEnvExample,
    /^PLATFORM_CORS_ORIGINS_JSON=\["https:\/\/app\.example\.invalid"\]$/m,
  );
  assert.match(
    productionEnvExample,
    /^PLATFORM_PUBLIC_BASE_URL=https:\/\/platform\.example\.invalid$/m,
  );
});

test("current release documents describe the native auth lifecycle and migration head", () => {
  for (const document of [releaseReadiness, traceability, stagingReadiness]) {
    assert.match(document, /OIDC/);
    assert.match(document, /BFF/);
    assert.match(document, /0040_showcase_management/);
    assert.match(document, /0039_new_api_relay_defaults/);
    assert.match(document, /0038_download_evidence_checks/);
    assert.match(document, /0037_production_auth_lifecycle|`0037`/);
  }
  assert.doesNotMatch(
    stagingReadiness,
    /仅有 JWT 验签和管理员强认证校验；无项目内 OIDC 登录回调/,
  );
  assert.doesNotMatch(
    traceability,
    /生产 Bearer JWT 验签、issuer\/audience、公司与管理员声明校验已实现/,
  );
  assert.match(releaseReadiness, /目标 IdP.*canary/);
  const catalog = databasePrivilegesV5.match(/CATALOG_SHA256\s*=\s*"([0-9a-f]{64})"/);
  assert.ok(catalog, "missing protected Platform v5 catalog fingerprint");
  assert.notEqual(catalog[1], "0".repeat(64), "protected Platform v5 catalog is not qualified");
  assert.match(releaseReadiness, new RegExp(catalog[1]));
  assert.match(stagingReadiness, new RegExp(catalog[1]));
});
