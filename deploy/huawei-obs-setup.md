# Huawei OBS production binding

This deployment is pinned to the private `chen-aivideo` bucket in Huawei Cloud
South China (Guangzhou):

- Endpoint: `https://obs.cn-south-1.myhuaweicloud.com`
- Bucket: `chen-aivideo`
- Object host: `chen-aivideo.obs.cn-south-1.myhuaweicloud.com`
- Platform-owned prefixes: private inputs under `inputs/*`, plus published
  homepage showcase media under `showcase/media/*`
- Relay-owned prefix: `outputs/*`

The bucket remains private with Block Public Access enabled. Browser code and
the Download Edge must never receive an OBS access key.

## IAM identities (current account uses the legacy IAM console)

The checked-in policies use legacy IAM policy version `1.1`, matching the
account's active console. Create two programmatic-only IAM users without
console passwords or membership in the `admin` group:

1. `ai-video-platform-obs`, a member only of
   `ai-video-platform-obs-group`; that group receives only
   `deploy/huawei-obs-platform-policy.json`.
2. `ai-video-relay-obs`, a member only of `ai-video-relay-obs-group`; that
   group receives only `deploy/huawei-obs-relay-policy.json`.

The runtime policies intentionally exclude object deletion, bucket deletion,
bucket-policy mutation, public ACLs, and cross-prefix access. A separate,
temporary acceptance identity may receive `DeleteObject` on `outputs/*` only;
disable that key immediately after the live gate.

## Secret injection

Do not put either IAM credential in `deploy/relay-secure.env` or the container
environment. The approved secret manager writes the Relay identity into typed
bundle B (`NEW_API_RELAY_API_RUNTIME_SECRETS_FILE`) and writes the Platform
identity independently into both `PLATFORM_API_RUNTIME_SECRETS_FILE` and
`PLATFORM_DISPATCHER_RUNTIME_SECRETS_FILE`. The two Platform copies must match
the same Platform-scoped IAM identity; they must remain distinct from Relay:

```text
PLATFORM_HUAWEI_OBS_ACCESS_KEY_ID
PLATFORM_HUAWEI_OBS_SECRET_ACCESS_KEY
NEW_API_RELAY_HUAWEI_OBS_ACCESS_KEY_ID
NEW_API_RELAY_HUAWEI_OBS_SECRET_ACCESS_KEY
```

Represent `security_token` as JSON `null` for permanent IAM access keys.
Never place OBS credentials in a `VITE_*` variable, browser runtime config,
image layer, committed file, screenshot, chat, or log.

The former raw intake file `deploy/secrets/huawei-obs.runtime.env` is not a
valid secure-runtime source. Rotate any credential ever stored there, remove
the retired file through the approved secret-handling procedure, and render
only the typed bundles above. A live acceptance runner may receive a separate,
short-lived acceptance identity in its own isolated subprocess; never reuse a
runtime identity for that purpose.

## Required acceptance

Credential creation is not production acceptance. The candidate Relay image
must pass the repository's host-side OBS live gate:

```text
node scripts/run-relay-obs-live-acceptance.mjs --candidate-image <immutable-image> --evidence-dir <absolute-existing-private-directory>
```

The gate proves anonymous denial, PUT/HEAD metadata integrity, signed full-byte
GET integrity, exact cleanup, and secret-free evidence. Platform input upload
must also be exercised through its API so the stored size, content type, and
SHA-256 metadata are verified against OBS.
