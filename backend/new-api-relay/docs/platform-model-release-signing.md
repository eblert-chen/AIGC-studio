# Signed generation model releases

Staging and production Relay routes must bind a public model ID, provider
model ID, immutable adapter profile revision, legacy aliases, reviewed public
capability, and audit record in a signed `generation_model_release` document.
The route-level acceptance signature is a second proof; it does not replace
the model-release attestation.

## Trust boundary

- Keep the Ed25519 private key outside the Relay image, runtime environment,
  filesystem, database, and secret-manager namespace used by Relay.
- Relay receives only the public keys already configured through
  `RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON`. Model-release and route-
  acceptance key IDs may differ, but both public keys must be present in the
  build-bound trust set.
- Pass private material to the signer only through `--private-key-file`. The
  path must be absolute and identify a regular non-symlink file containing
  canonical base64 for a 32-byte Ed25519 seed or internally consistent 64-byte
  private key. POSIX key files must have no group/world permission bits;
  Windows applies the same regular-file and identity checks without treating
  POSIX mode bits as authoritative.
- The command is an offline release tool. It is not built or copied by the
  production Relay Dockerfile.

## Release order

The order is mandatory:

1. Review an unsigned model-release JSON document. It must decode strictly,
   select a registered adapter profile, and narrow rather than expand that
   profile.
2. Sign that document with `relay-model-release-sign`.
3. Embed the signed release into every reviewed route for its canonical public
   model. Do not carry draft models or legacy readiness booleans into the
   secure route inventory.
4. Build the final candidate, freeze its source snapshot, and resolve the
   immutable image digest.
5. Run `relay-route-acceptance-sign` after embedding the signed release. Its
   route digest commits the release ID, binding revision, capability revision,
   and exact environment/source/image provenance. Secure Relay independently
   verifies the embedded model-release attestation itself.

For the current paid canary, the repository script enforces this sequence and
admits only canonical `seedream-5` with legacy alias `seedream-5-lite`:

```powershell
backend/new-api-relay/scripts/sign-paid-canary-model-route-release.ps1 `
  -UnsignedModelReleasePath C:\release\seedream-5.unsigned.json `
  -ReviewedRoutesPath C:\release\seedream-5.routes.json `
  -SignedModelReleasePath C:\release\seedream-5.signed.json `
  -SignedRoutesPath C:\release\seedream-5.routes.signed.json `
  -ModelReleasePrivateKeyFile C:\release-authority\model-release.ed25519 `
  -RouteAcceptancePrivateKeyFile C:\release-authority\route-acceptance.ed25519 `
  -ModelReleaseKeyId model-release-2026-q3 `
  -RouteAcceptanceKeyId route-acceptance-2026-q3 `
  -RouteAcceptanceReleaseId 11111111-2222-4333-8444-555555555555 `
  -Environment staging `
  -SourceRevision 0123456789abcdef0123456789abcdef01234567 `
  -SourceSnapshotSha256 sha256:<64-lowercase-hex> `
  -ImageDigest sha256:<64-lowercase-hex> `
  -ModelReleaseSignedAt 2026-08-29T00:00:00Z `
  -ModelReleaseNotAfter 2026-09-29T00:00:00Z `
  -RouteAcceptanceNotBefore 2026-08-29T00:00:00Z `
  -RouteAcceptanceNotAfter 2026-09-05T00:00:00Z
```

The script never edits `deploy/secrets` or starts Relay. Review and archive
both immutable outputs, verify the final trust set and candidate provenance,
then promote the exact signed-routes JSON through the deployment secret
manager as `NEW_API_RELAY_MODEL_ROUTES_JSON`. An unsigned intermediate or a
partially produced pair is not eligible for secure deployment.

Both output paths must be in the same existing directory. The script stages
and flushes both documents there, verifies their closed JSON shape and exact
Seedream/legacy-alias binding, then uses atomic no-replace links. The model
artifact is linked first and the signed-routes artifact last; the latter is
the commit marker for the pair. A crash after the first link is safely
resumable only when the existing model bytes exactly match. A different
existing artifact is never overwritten, and a complete exact replay is
idempotent.

The underlying first step can also be run independently:

```powershell
go run ./cmd/relay-model-release-sign `
  --release C:\release\seedream-5.unsigned.json `
  --private-key-file C:\release-authority\model-release.ed25519 `
  --key-id model-release-2026-q3 `
  --signed-at 2026-08-29T00:00:00Z `
  --not-after 2026-09-29T00:00:00Z > C:\release\seedream-5.signed.json
```

`signed-at` and `not-after` must be canonical UTC RFC3339 values. Signing may
not precede the release audit timestamp, expiry must be later than signing,
and the validity window is capped at 365 days. The orchestration additionally
requires the model attestation window to contain the complete route-acceptance
window (`model signed_at <= route not_before` and `model not_after >= route
not_after`). Identical reviewed input, key, key ID, and time window produce
byte-identical JSON and Ed25519 signature.

## Failure behavior

The signer rejects unknown or trailing JSON fields, an already attested input,
an unavailable or mismatched adapter profile, capability expansion, invalid
audit data, noncanonical times, excessive lifetime, malformed or inconsistent
keys, symlinks, permission violations, and file identity/content changes while
opening or reading. Errors never include private key bytes or the reviewed
release document. Secure Relay subsequently revalidates both signatures,
their current validity windows, the public trust set, route/model bindings,
and final candidate provenance before startup can become ready.

Pair publication is also fail closed: a staged-file flush failure publishes
nothing; a second-link failure removes only the model link created by that
attempt; and a racing target is never replaced. The routes commit marker must
exist before operators may archive or promote either output.
