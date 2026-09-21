import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function source(path) {
  return readFile(new URL(`../${path}`, import.meta.url), "utf8");
}

function serviceBlock(sourceText, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = sourceText.match(
    new RegExp(
      `^  ${escaped}:\\r?\\n([\\s\\S]*?)(?=^  [a-zA-Z0-9][a-zA-Z0-9_-]*:\\r?\\n|(?![\\s\\S]))`,
      "m",
    ),
  );
  assert.ok(match, `missing Compose service ${name}`);
  return match[0];
}

test("local browser session discovery remains readable before OIDC is configured", async () => {
  const [main, compose, ingress] = await Promise.all([
    source("backend/platform/platform_api/main.py"),
    source("docker-compose.yml"),
    source("infra/nginx/platform-api.conf"),
  ]);

  assert.match(
    main,
    /allow_credentials=not settings\.development_header_auth_enabled/,
  );
  assert.match(compose, /x-platform-api-environment:\s*&platform-api-environment/);
  assert.match(serviceBlock(compose, "platform-api"), /environment:\s*\*platform-api-environment/);
  assert.match(ingress, /proxy_hide_header Access-Control-Allow-Credentials;/);
  assert.match(ingress, /add_header Access-Control-Allow-Credentials "true" always;/);
});

test("local formal OIDC wiring stays API-only and never accepts a client secret", async () => {
  const [compose, envExample] = await Promise.all([
    source("docker-compose.yml"),
    source(".env.example"),
  ]);
  const apiEnvironment = compose.match(
    /^x-platform-api-environment:[\s\S]*?(?=^x-[a-zA-Z0-9_-]+:|^services:)/m,
  )?.[0] || "";

  for (const key of [
    "OIDC_ENABLED",
    "OIDC_ISSUER",
    "OIDC_AUTHORIZATION_ENDPOINT",
    "OIDC_TOKEN_ENDPOINT",
    "OIDC_JWKS_URI",
    "OIDC_CLIENT_ID",
    "OIDC_REDIRECT_URI",
    "FRONTEND_ORIGIN",
  ]) {
    assert.match(apiEnvironment, new RegExp(`^  ${key}:`, "m"));
  }
  assert.doesNotMatch(compose, /OIDC_CLIENT_SECRET|client_secret/i);
  assert.doesNotMatch(envExample, /OIDC_CLIENT_SECRET|client_secret/i);
  assert.match(envExample, /^PLATFORM_OIDC_ENABLED=false$/m);
  assert.match(envExample, /^PLATFORM_OWNER_USER_IDS_JSON=\[\]$/m);

  for (const worker of [
    "platform-dispatcher",
    "platform-relay-sync",
    "platform-relay-catalog-sync",
    "platform-timeout-worker",
    "platform-publishing-worker",
  ]) {
    assert.doesNotMatch(
      serviceBlock(compose, worker),
      /environment:\s*\*platform-api-environment/,
      `${worker} must not receive browser/OIDC configuration`,
    );
  }
});
