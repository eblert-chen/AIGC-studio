import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const env = read("deploy/relay-secure.env.example");
const platformPolicy = JSON.parse(read("deploy/huawei-obs-platform-policy.json"));
const relayPolicy = JSON.parse(read("deploy/huawei-obs-relay-policy.json"));

const expectedEndpoint = "https://obs.cn-south-1.myhuaweicloud.com";
const expectedBucket = "chen-aivideo";
const expectedHost = "chen-aivideo.obs.cn-south-1.myhuaweicloud.com";

function valueOf(name) {
  const match = env.match(new RegExp(`^${name}=(.*)$`, "m"));
  assert.ok(match, `missing ${name}`);
  return match[1].trim();
}

function actions(policy) {
  return new Set(policy.Statement.flatMap((statement) => statement.Action));
}

function resources(policy) {
  return new Set(policy.Statement.flatMap((statement) => statement.Resource));
}

test("pins the confirmed private Guangzhou OBS binding without committing credentials", () => {
  assert.equal(valueOf("PLATFORM_HUAWEI_OBS_ENDPOINT"), expectedEndpoint);
  assert.equal(valueOf("PLATFORM_HUAWEI_OBS_BUCKET"), expectedBucket);
  assert.equal(valueOf("NEW_API_RELAY_HUAWEI_OBS_ENDPOINT"), expectedEndpoint);
  assert.equal(valueOf("NEW_API_RELAY_HUAWEI_OBS_BUCKET"), expectedBucket);
  assert.equal(valueOf("NEW_API_RELAY_DOWNLOAD_EDGE_ALLOWED_OBS_HOSTS"), expectedHost);

  for (const name of [
    "PLATFORM_HUAWEI_OBS_ACCESS_KEY_ID",
    "PLATFORM_HUAWEI_OBS_SECRET_ACCESS_KEY",
    "NEW_API_RELAY_HUAWEI_OBS_ACCESS_KEY_ID",
    "NEW_API_RELAY_HUAWEI_OBS_SECRET_ACCESS_KEY",
  ]) {
    assert.doesNotMatch(env, new RegExp(`^${name}=`, "m"));
  }
  assert.match(valueOf("NEW_API_RELAY_API_RUNTIME_SECRETS_FILE"), /relay-api-runtime-secrets\.json$/);
  assert.match(valueOf("PLATFORM_API_RUNTIME_SECRETS_FILE"), /platform-api-runtime-secrets\.json$/);
  assert.match(
    valueOf("PLATFORM_DISPATCHER_RUNTIME_SECRETS_FILE"),
    /platform-dispatcher-runtime-secrets\.json$/,
  );
});

test("keeps Platform and Relay OBS permissions prefix-scoped and non-destructive", () => {
  assert.equal(platformPolicy.Version, "1.1");
  assert.equal(relayPolicy.Version, "1.1");

  assert.deepEqual(
    resources(platformPolicy),
    new Set([
      `obs:*:*:object:${expectedBucket}/inputs/*`,
      `obs:*:*:object:${expectedBucket}/showcase/media/*`,
    ]),
  );
  assert.deepEqual(
    actions(platformPolicy),
    new Set(["obs:object:GetObject", "obs:object:PutObject"]),
  );

  assert.deepEqual(
    resources(relayPolicy),
    new Set([
      `obs:*:*:bucket:${expectedBucket}`,
      `obs:*:*:object:${expectedBucket}/outputs/*`,
    ]),
  );
  assert.deepEqual(
    actions(relayPolicy),
    new Set([
      "obs:bucket:HeadBucket",
      "obs:object:GetObject",
      "obs:object:PutObject",
    ]),
  );

  for (const action of [...actions(platformPolicy), ...actions(relayPolicy)]) {
    assert.doesNotMatch(action, /delete|acl|policy|listAllMyBuckets/i);
  }
});
