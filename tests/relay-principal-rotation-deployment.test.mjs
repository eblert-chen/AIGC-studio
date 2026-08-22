import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const secure = read("deploy/compose.relay.secure.yml");
const rotation = read("deploy/compose.relay.principal-rotation.yml");
const sharedEnv = read("deploy/relay-secure.env.example");
const runbook = read("docs/new-api-production-deployment.md");

function serviceBlock(source, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(
    new RegExp(
      `^  ${escaped}:\\r?\\n([\\s\\S]*?)(?=^  [a-zA-Z0-9][a-zA-Z0-9_-]*:\\r?\\n|^volumes:\\r?\\n|(?![\\s\\S]))`,
      "m",
    ),
  );
  assert.ok(match, `missing Compose service ${name}`);
  return match[0];
}

function secretTargets(block) {
  return new Set(
    [...block.matchAll(/^\s+target:\s*(\/run\/secrets\/[^\s]+)\s*$/gm)].map(
      (match) => match[1],
    ),
  );
}

test("keeps service-principal rotation outside the ordinary rollout", () => {
  assert.doesNotMatch(secure, /RELAY_CURRENT_SERVICE_PRINCIPALS_FILE/);
  assert.doesNotMatch(secure, /principal-rotation/);
  assert.match(
    sharedEnv,
    /^NEW_API_RELAY_CURRENT_SERVICE_PRINCIPALS_FILE=/m,
  );
  for (const name of [
    "relay-new-api-principal-rotation-volume-init",
    "relay-new-api-principal-rotation-secret-isolation",
    "relay-new-api-service-principal-rotation",
  ]) {
    assert.match(
      serviceBlock(rotation, name),
      /profiles:\s*\["relay-principal-rotation"\]/,
    );
  }
});

test("gives the networkless rotation validator the full source set but no ordinary receipt writers", () => {
  const ordinary = serviceBlock(secure, "relay-new-api-secret-isolation");
  const validator = serviceBlock(
    rotation,
    "relay-new-api-principal-rotation-secret-isolation",
  );
  assert.match(
    validator,
    /relay-validate-service-principal-rotation-secret-isolation-v1/,
  );
  assert.match(ordinary, /network_mode:\s*none/);
  assert.match(
    validator,
    /extends:[\s\S]+file:\s*deploy\/compose\.relay\.secure\.yml[\s\S]+service:\s*relay-new-api-secret-isolation/,
  );
  assert.match(validator, /RELAY_CURRENT_SERVICE_PRINCIPALS_FILE:/);
  assert.match(validator, /relay-current-service-principals\.json/);
  assert.match(
    validator,
    /relay-new-api-root-secret-isolation-proof:\/run\/relay-root-secret-isolation-proof:ro/,
  );
  assert.match(
    validator,
    /relay-new-api-secret-isolation-principal-rotation:\/run\/relay-secret-isolation\/principal-rotation(?:\r?\n|$)/,
  );
  assert.doesNotMatch(
    validator,
    /relay-new-api-secret-isolation-(?:pre|migrate|post|principal|api|edge|platform[^:]*):\/run\/(?!relay-secret-isolation\/principal-rotation)/,
  );
  assert.doesNotMatch(validator, /relay-new-api-secret-isolation-commit:/);

  const ordinaryTargets = secretTargets(ordinary);
  const rotationTargets = secretTargets(validator);
  assert.deepEqual(
    [...ordinaryTargets].sort(),
    [...rotationTargets].filter((target) => !target.includes("current-service-principals")).sort(),
  );
});

test("mounts exactly six read-only inputs into the networked token-only rotation job", () => {
  const job = serviceBlock(rotation, "relay-new-api-service-principal-rotation");
  assert.match(job, /relay-rotate-service-principals-v1/);
  assert.match(job, /user:\s*"10001:10001"/);
  assert.match(job, /read_only:\s*true/);
  assert.match(job, /cap_drop:\s*\["ALL"\]/);
  assert.match(job, /no-new-privileges:true/);
  assert.match(job, /- relay-new-api-managed-state/);
  for (const target of [
    "relay-database-ca.pem",
    "relay-runtime-sql-dsn",
    "relay-current-service-principals.json",
    "relay-service-principals.json",
    "relay-secret-isolation:ro",
    "relay-database-release-proof:ro",
  ]) {
    assert.ok(job.includes(target), `rotation job missing ${target}`);
  }
  assert.doesNotMatch(
    job,
    /REDIS_TLS_CA|api-runtime|download-edge-runtime|provider-credential|platform-|role-admin|migration-password|root-password|SECRET_ISOLATION_COMMIT/,
  );
});

test("documents the token-only maintenance and mandatory post-rotation release chain", () => {
  assert.match(runbook, /Versioned service-principal token rotation/);
  assert.match(runbook, /credential_operation=token_rotation_only/);
  assert.match(runbook, /identity_set=immutable/);
  assert.match(
    runbook,
    /force-recreate the ordinary root-proof-present[\s\S]+Relay role-pre[\s\S]+Platform role-pre[\s\S]+exact service-principal provisioning/,
  );
  assert.match(runbook, /adding,[\s\S]+removing,[\s\S]+rejected/);
});
