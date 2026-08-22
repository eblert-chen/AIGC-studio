# Signed route acceptance releases

Staging and production Relay instances do not trust `staging_ready` or
`production_ready` values from route configuration. Each route declaration
must carry an Ed25519 acceptance release signed outside the Relay runtime.

## Trust boundary

- The acceptance authority keeps the Ed25519 private key outside the Relay
  image, environment, filesystem, database, and secret manager namespace.
- Relay receives only `RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON`, a JSON
  object from key ID to canonical base64 Ed25519 public-key bytes.
- The release build canonicalizes that public-key object, calculates
  `sha256:<hex>`, and supplies it as Docker build argument
  `RELAY_BUILD_ROUTE_ACCEPTANCE_KEYS_SHA256`.
- A staging/production binary rejects runtime public keys whose digest differs
  from its build-time trust anchor.
- `image_digest` is supplied by external registry/deployment attestation and
  enforced operationally by digest-pinned Compose. Relay verifies that the
  signed manifest equals its configured runtime value, but a process inside a
  container cannot independently prove its own OCI manifest digest; deployment
  evidence must therefore verify the registry digest, Compose render, and
  runtime provenance response together.

Key rotation is a release action: build with an overlapping old/new public-key
set, reissue route acceptances with the new key, then build a later release
without the old public key.

## Manifest contract

`acceptance.manifest` has schema version `1` and kind
`relay_route_acceptance`. It binds:

- acceptance ID and signing key ID;
- exact environment (`staging` or `production`);
- model and sorted modes;
- route, provider, account, channel ID, native new-api channel type, key index,
  key fingerprint, channel class, upstream model, RPM and active-task limit;
- canonical capability SHA-256 and canonical complete route-declaration
  SHA-256;
- committed extension revision, frozen source snapshot SHA-256, deployed image
  digest;
- canonical UTC RFC3339 `not_before` and `not_after`, with a maximum 90-day
  validity window.

The signature input is:

```text
"ai-video/new-api-relay/route-acceptance/v1\0" || canonical_json(manifest)
```

`canonical_json` recursively sorts object keys and emits no insignificant
whitespace, matching `platformRelayCanonicalJSON`. The detached Ed25519
signature is canonical standard base64 in `acceptance.signature`.

The repository includes an offline signer that is not built or copied into the
Relay runtime image:

```powershell
go run ./cmd/relay-route-acceptance-sign `
  --routes C:\release\reviewed-routes.json `
  --private-key-file C:\release-authority\route-acceptance.ed25519 `
  --key-id release-2026-q3 `
  --release-id 11111111-2222-4333-8444-555555555555 `
  --environment staging `
  --source-revision 0123456789abcdef0123456789abcdef01234567 `
  --source-snapshot-sha256 sha256:<64-lowercase-hex> `
  --image-digest sha256:<64-lowercase-hex> `
  --not-before 2026-08-15T00:00:00Z `
  --not-after 2026-08-22T00:00:00Z > C:\release\signed-routes.json
```

The private-key flag accepts only an absolute path to a regular non-symlink
file containing a canonical base64 32-byte seed or 64-byte Ed25519 private key.
On POSIX the file must be owner-only (`0600` or stricter); group/world-readable
key files fail closed. This permission rule does not apply to the reviewed
secret-free route JSON.
Raw private-key bytes are never accepted through argv or environment variables.
The `release-id` deterministically derives one acceptance ID per model/route,
so rerunning identical reviewed inputs produces byte-equivalent evidence.

The acceptance authority must obtain the route and capability digests from the
same reviewed release tooling that produces the manifest. Relay exposes only
secret-free proof digests, IDs, expiry, channel identity and the aggregate
acceptance-set digest from its authenticated runtime-build-identity endpoint.

## Failure behavior

Staging and production fail closed when evidence is absent, expired, not yet
active, signed by an untrusted key, bound to another environment, route,
capability, adapter, source snapshot, or image. A staging signature cannot be
replayed in production. Development and tests remain compatible with unsigned
route declarations.
