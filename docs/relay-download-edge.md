# Controlled Huawei OBS download completion

## Why the Relay uses an edge gateway

Huawei OBS bucket access logs record `REST.GET.OBJECT`, HTTP status, `BytesSent`,
`ObjectSize`, request ID, object key, version ID, and server timing. Huawei notes
that log delivery is delayed (normally about 15 minutes), and defines
`BytesSent` as the size of the HTTP response. Those facts are valuable for OBS
operations, but they are generated at the storage service boundary. They do not
prove that the final browser connection accepted the complete response when a
CDN, reverse proxy, or disconnected client sits downstream.

OBS event notifications include object-created and other object lifecycle
events; they do not provide a GET/download-completed event. Therefore this
implementation does not turn an OBS access-log row into a customer “downloaded”
fact. The official references used for that decision are:

- [OBS server access logging fields](https://support.huaweicloud.com/intl/en-us/tr-central-201-usermanual-obs/en-us_topic_0045853553.html)
- [OBS access logging and `REST.GET.OBJECT`](https://support.huaweicloud.com/intl/en-us/ugobs-obs/obs_41_0046.html)
- [OBS event notification structure and event types](https://support.huaweicloud.com/intl/en-us/usermanual-obs/obs_06_19_05.html)

The production evidence source is instead the controlled
`relay-download-edge` binary in the extended new-api Relay image. It accepts a
one-time ticket, performs the only OBS GET, streams the full body to the caller,
and creates completion evidence only after all of these are true:

1. OBS returned exactly HTTP 200 with no `Content-Range`.
2. The body reached EOF without a redirect or transport error.
3. The bytes accepted by the downstream writer exactly equal the immutable
   expected size.
4. The streamed SHA-256 equals the immutable artifact digest.
5. The ticket lease and random claim token still own the PostgreSQL fence.

Range requests and redirects are rejected. A ticket is consumed by its first
valid full-body GET; a failed transfer requires the Platform to register a new
ticket.

## Service and data boundary

The browser still calls the customer Platform for download issuance. The
Platform creates its `DownloadRecord`, obtains a short OBS signed URL in memory,
and registers it with the Gateway. The browser receives only the Gateway ticket
URL.

The Gateway uses the new-api Relay PostgreSQL database:

- `platform_download_edge_tickets` is mutable only through token-fenced state
  transitions. It stores the source URL as AES-256-GCM ciphertext and stores
  only SHA-256 of the public ticket bearer.
- `platform_download_completion_events` is immutable full-transfer evidence.
- `platform_download_completion_proofs` is the immutable canonical Ed25519
  producer proof created after Platform accepts the signed callback.
- `platform_relay_external_deliveries` is the separate mutable retry/dead-letter
  outbox.

The completion and proof tables have ORM guards and database triggers. On
PostgreSQL the triggers reject `UPDATE`, `DELETE`, and `TRUNCATE`; retry state is
never embedded in either immutable row.

Neither event/proof payloads nor normal errors contain the OBS signed URL,
query string, registration credentials, completion HMAC key, or Ed25519 private
key.

## Registration contract

The fixed endpoint is:

`POST /internal/v1/download-tickets`

Required headers:

- `X-Download-Gateway-Token`: independent Platform-to-Gateway service token.
- `X-Download-Gateway-Timestamp`: canonical Unix seconds.
- `X-Download-Gateway-Request-ID`: canonical UUID used for persistent replay
  control.
- `X-Download-Gateway-Signature`: `sha256=<lowercase hex HMAC-SHA256>`.

The signature input is the exact concatenation below, including newlines and
the original body bytes:

```text
download-edge-registration.v1
POST
/internal/v1/download-tickets
{timestamp}
{request_id}
{raw JSON body}
```

The strict JSON request is:

```json
{
  "api_version": "v1",
  "schema_version": 1,
  "download_record_id": "uuid",
  "company_id": "uuid",
  "task_id": "uuid",
  "asset_id": "platform asset id",
  "expected_size_bytes": 123,
  "artifact_sha256": "64 lowercase hex characters",
  "source_url": "short-lived HTTPS Huawei OBS signed URL",
  "source_expires_at": "RFC3339Nano UTC timestamp ending in Z",
  "obs_binding": {
    "bucket": "private-bucket",
    "object_key": "durable/object/key.mp4",
    "version_id": "optional OBS version"
  },
  "issuance_request_id": "Platform DownloadRecord request_id",
  "transfer_reference": "independent random UUID"
}
```

Unknown fields, duplicate JSON keys, an unlisted source host, user-info,
fragments, a missing signature query, and bucket/object path mismatches are
rejected before a ticket is created. The source host allowlist is exact; it does
not accept wildcards. Production also requires an official Huawei OBS hostname.

`source_expires_at` is part of the signed raw body and persisted replay
binding. Using the PostgreSQL clock, the Gateway sets ticket lifetime to
`min(300s, source_expires_at - now - 60s)` and rejects a new registration when
less than 30 seconds remains. Relay source URLs therefore use an explicit
600-second lifetime. An exact replay is checked before freshness validation:
while live it returns the original `201`; after committed expiry it returns
terminal `410` with `outcome: committed_expired`, the raw registration-body
SHA-256 and stable entity/ticket bindings, but no bearer URL. Reusing the same
request ID with different raw bytes returns `409`.

The `201` response echoes the four entity IDs, `issuance_request_id`, and
`transfer_reference`, and returns:

```json
{
  "api_version": "v1",
  "schema_version": 1,
  "gateway_ticket_id": "uuid",
  "one_time": true,
  "ticket_url": "https://downloads.example.com/downloads/<bearer>",
  "issued_at": "RFC3339 UTC",
  "expires_at": "RFC3339 UTC",
  "expires_seconds": 300
}
```

The raw ticket is a keyed, high-entropy derivation and can be reconstructed for
an exact registration replay, but only its SHA-256 is stored. The Platform must
validate every echoed field, the configured ticket origin, `one_time`, and its
TTL before returning the URL.

`gateway_request_id` in completion evidence is deliberately the Platform
`issuance_request_id`; it is the distributed trace binding to the immutable
`DownloadRecord`. `gateway_transfer_reference` remains the separate random
per-transfer identity.

## Completion callback and durable delivery

After a verified stream, the Gateway transactionally inserts one immutable
completion event and one pending outbox row. The worker posts the exact stored
JSON bytes only to:

`/internal/artifact-download-completions/edge-gateway`

It sends `X-Internal-Service-Token`, `X-Download-Event-ID`,
`X-Download-Timestamp`, and `X-Download-Signature`. The HMAC input is:

```text
download-completion.v1
edge_gateway
{timestamp}
{event_id}
{exact stored JSON body}
```

Only a strict, matching Platform `201` response completes the outbox. Timeout,
429, 5xx, and idempotency conflict responses are retried with leased random
token fencing and a bounded attempt budget. Non-retryable rejection or budget
exhaustion remains visible as `dead_letter`; it never changes the immutable
transfer evidence.

## Detached producer proof

After the Platform response is verified, the same PostgreSQL transaction marks
the delivery complete and creates the proof. The canonical v1 JSON struct binds:

- Platform completion, download record, company, task, and asset IDs;
- Platform signed event ID and exact payload SHA-256;
- issuance request, transfer reference, and Gateway request ID;
- OBS bucket, object key, and optional version;
- HTTP 200, `full_body`, bytes, expected bytes, and artifact SHA-256;
- transfer completion, Platform delivery, proof production times, producer
  subject, and a random nonce.

The Ed25519 signature input is:

```text
relay-download-completion-proof.v1
{exact canonical proof JSON bytes}
```

An acceptance runner may retrieve the immutable bytes with an independent
read-only token; it never receives the private key or completion HMAC secret:

- `GET /internal/v1/download-completions/{signed_event_id}/proof`
- `GET /internal/v1/download-completions/{signed_event_id}/proof/signature`
- header: `X-Download-Proof-Read-Token`

The signature response contains `schema_version`, `algorithm: Ed25519`,
`key_id`, `payload_sha256` in `sha256:<64 lowercase hex>` form, and
`signature_base64`. The acceptance configuration
must pin the separate public-key fingerprint and must reject reuse of the
provider-billing approval key.

## Production deployment requirements

The Docker image contains `/relay-download-edge`; Compose exposes it as the
opt-in `relay-download-edge` service in the `new-api-relay` profile. Production
must provide every `RELAY_DOWNLOAD_EDGE_*` secret from a secret manager. In
particular:

- registration token and registration HMAC key;
- independent 32-byte ticket derivation key;
- independent 32-byte AES-GCM source encryption key;
- Platform internal token and edge completion HMAC key;
- Ed25519 private key, key ID, independent proof read token, and producer
  subject.

The Gateway has a dedicated Compose environment and must not inherit the main
Relay environment. In particular, its container must not receive Redis,
tenant/channel/provider credentials, Huawei OBS AK/SK, generation callback,
provider-monitor, alert-webhook, or channel-cost secrets. Production startup
rejects short or placeholder credentials, the deterministic local ticket,
encryption, and proof keys, and reuse of any authentication, signing,
encryption, ticket, or proof root key.

The edge container runs as UID/GID 10001 with a read-only root filesystem, all
Linux capabilities dropped, `no-new-privileges`, and only a small no-exec
`/tmp` tmpfs.

Set `RELAY_DOWNLOAD_EDGE_SQL_DSN` to the exact `relay_download_edge`
PostgreSQL role. The command
maps it to the new-api library's `SQL_DSN` only inside the process and forces
`common.IsMasterNode=false`, so the Gateway never runs AutoMigrate. The main
`relay-new-api` service must complete migrations and become healthy before the
Gateway starts. Compose then runs
`infra/postgres/provision-relay-download-edge.sql` as the migration owner. It
removes accidental memberships, grants no schema creation, uses column-level
UPDATE grants for state-machine fields, and applies RLS so this role can see and
write only `download_completion` outbox rows. The Gateway database role needs
only connection/schema usage and narrow DML on `platform_download_edge_tickets`,
`platform_download_completion_events`, `platform_download_completion_proofs`,
and `platform_relay_external_deliveries`; do not grant table ownership,
`CREATE`, `ALTER`, `DROP`, trigger modification, or access to channel, tenant,
provider credential, usage-log, or billing tables. Keep the immutable-table
triggers owned by the migration role.

Production startup independently queries `current_user`, PostgreSQL role flags,
schema/table/column privileges, and sensitive-table access. A role mismatch,
superuser/RLS-bypass/DDL authority, a missing required state-machine privilege,
or any channel/token/user/cost/alert read access aborts startup.

The Platform uses `DOWNLOAD_GATEWAY_REGISTRATION_URL`,
`DOWNLOAD_GATEWAY_PUBLIC_BASE_URL`, `DOWNLOAD_GATEWAY_SERVICE_TOKEN`, and
`DOWNLOAD_GATEWAY_REGISTRATION_SIGNING_SECRET`. Production sets
`RELAY_ALLOW_LEGACY_ARTIFACT_DOWNLOAD_RESPONSE=false` (or leaves it unset if
false is the default); it must never fall back to an OBS URL.

The root Compose stack runs new-api as the only active Relay data plane and
binds the local download edge explicitly. Protected environments inject the
four `DOWNLOAD_GATEWAY_*` values through their role-specific bundles;
production validation requires the registration worker and forbids the legacy
artifact response. There is no Python Relay profile or runtime fallback.

The 300-second transfer timeout is covered by a 330-second ticket claim lease
and an explicit 10-second final-commit margin. The completion delivery lease is
validated against the fixed 15-second Platform HTTP timeout plus its own
10-second final-commit margin. Event/outbox and proof rows are created inside a
transaction before the final token-and-database-clock state update; an expired
claim rolls all provisional rows back.

Terminate public TLS at the Gateway or at a tightly controlled proxy that
propagates downstream disconnects. Disable response buffering, caching,
internal redirect interception, automatic retries, and range slicing on the
`/downloads/` route. If a proxy acknowledges the complete body to the Gateway
before forwarding it to the browser, the evidence applies to that proxy rather
than the final client and is not acceptable for this completion contract.

Monitor `/health/ready`. It checks PostgreSQL and reports pending, claimed, and
dead-letter completion deliveries without exposing ticket or OBS data. Alert on
any dead letter and on a growing pending/claimed backlog.

## Verification

The normal Go test is:

```text
go test ./service -run '^TestPlatformDownloadEdge' -count=1
```

The real PostgreSQL concurrency and append-only test uses `TEST_POSTGRES_DSN`:

```text
go test ./model -run '^TestPlatformDownloadEdgePostgres' -count=1
```

It creates and removes one isolated schema, races 16 registrations, 16 ticket
claims, and 16 finalizers, verifies exactly one winner/event/outbox, and proves
that PostgreSQL rejects update, delete, and truncate on both immutable tables.
It also forces lease expiry between provisional inserts and the final fence,
exercises outbox reclaim with a new random token, and verifies that stale
workers commit neither event/outbox nor detached proof.
