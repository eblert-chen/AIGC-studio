# Ali Wan2.7 + Volcengine Ark staging acceptance

Status: **code and offline contract tests complete; real-provider staging is blocked**. This document does not approve either route for production.

## Audited provider contracts

Audit date: 2026-08-12.

- Volcengine Ark create task: [`POST /api/v3/contents/generations/tasks`](https://api.volcengine.com/api-docs/view?action=CreateContentsGenerationsTasks&serviceCode=ark&version=2024-01-01)
- Volcengine Ark query task: [`GET /api/v3/contents/generations/tasks/{id}`](https://api.volcengine.com/api-docs/view?action=GetContentsGenerationsTask&serviceCode=ark&version=2024-01-01)
- Volcengine video-generation guide: [video generation](https://www.volcengine.com/docs/82379/1520757?lang=zh)
- Kling current authentication: [API Key authentication](https://kling.ai/document-api/api/get-started/authentication)
- Kling current video contract: [3.0/Omni text-to-video](https://kling.ai/document-api/api/video/3-0-omni/text-to-video)

Ark is the second-route candidate. Its current official create/query paths, Bearer authentication, model identifier `doubao-seedance-2-0-260128`, task states, and result field are represented by the local `DoubaoVideo` adapter. The adapter now treats `queued`, `running`, `succeeded`, `failed`, `cancelled`, and `expired` deterministically; an unknown state or a successful response without `content.video_url` is rejected instead of being polled forever or accepted as a durable result.

Kling is not used for this gate. The current Kling documentation uses the new API Key contract and current 3.0/Omni APIs, while the local adapter still implements the legacy AK/SK JWT and legacy `/v1/videos/text2video`/`image2video` protocol. Treating that adapter as a current official second route would not be verifiable.

## Fail-closed route template

Use [`platform-relay-two-provider-routes.staging.example.json`](./platform-relay-two-provider-routes.staging.example.json) as the starting value for `RELAY_COMPAT_MODEL_ROUTES_JSON`.

The example is deliberately not runnable:

- `channel_id`, RPM, and active-task limits are zero;
- key fingerprints and account identifiers are placeholders;
- `native_channel_type` is zero and no signed acceptance evidence exists;
- no provider credential or price is embedded.

Create the native new-api channels first:

| Provider | new-api channel | Base URL | Upstream model |
| --- | --- | --- | --- |
| Ali Bailian | `Ali` (type 17) | `https://dashscope.aliyuncs.com` | `wan2.7-t2v-2026-06-12` |
| Volcengine Ark | `DoubaoVideo` (type 54) | `https://ark.cn-beijing.volces.com` | `doubao-seedance-2-0-260128` |

Store the real keys only in the native channel store. Replace each template channel ID, native channel type, account identity, SHA-256 key fingerprint, and admission limit with values derived from that real channel and its approved quota. Route synchronization verifies the channel/key/adapter binding. After the individual-route canaries below pass, an external acceptance authority must issue the environment-, source-, image-, route-, and capability-bound Ed25519 evidence described in [`platform-route-acceptance-signing.md`](./platform-route-acceptance-signing.md). Never add a readiness boolean or a private signing key to Relay configuration.

## Shared public capability

The shared alias is `video.standard.t2v`. Both declarations intentionally publish the same conservative, failover-safe subset:

- mode: text-to-video only;
- prompt: at most 5,000 characters;
- no image, video, audio, or face input;
- duration: 5, 10, or 15 seconds;
- ratios: `16:9`, `9:16`, `1:1`, `4:3`, `3:4`;
- resolution: `720p` or `1080p`;
- exactly one output.

The Relay computes the public contract by intersecting every route declaration for the alias. A field or value absent from either route is therefore not admitted before routing.

Do not add image-to-video to this alias. A route declaration has one `upstream_model`, while Ali Wan2.7 uses different versioned upstream models for text-to-video and image-to-video. Image-to-video needs a separate public alias and its own independently audited declarations.

## Staging acceptance sequence

1. Call each native channel directly with one low-cost 5-second, 720p, 16:9 canary. Record the provider task ID, terminal status, provider-console task count, and result URL. Do not record the API key.
2. Fill the template with the real channel bindings and non-zero approved limits. Start in `staging`; route parsing, route synchronization, and readiness must all pass.
3. Fetch `/v1/models` and confirm that `video.standard.t2v` exposes exactly the shared contract above and a deterministic capability revision.
4. Submit through `/v1/generations`, pin that revision, and verify submit, sticky polling, verified artifact transfer, and callback completion for each route independently.
5. Exercise the fault matrix below. For every case, compare Relay rows with provider-console task counts so a hidden duplicate submission cannot pass the gate.
6. Reconcile provider billing evidence separately. Missing provider cost evidence is a failed production gate; customer price or quota is never a substitute.

| Injection point | Required result |
| --- | --- |
| First route disabled, cooling, RPM-limited, or at active capacity before provider POST | Another eligible route may be admitted; only one provider POST occurs. |
| Connection drops or response becomes unreadable after the provider POST may have been received | Job becomes `reconciliation_required`; original route and slot remain retained; no other account or provider receives a POST. |
| Poll request times out, returns 429, or is temporarily malformed | Poll remains sticky to the originally assigned route/task; no generation resubmission occurs. |
| Provider reports terminal failure, cancellation, or expiry | One terminal failure is recorded and the reservation follows the normal release path; no cross-provider resubmission occurs. |
| Manual unknown-submission reconciliation confirms no external task exists | Only the authorized reconciliation flow may release the retained slot and permit a new user-authorized attempt. |

Unknown submission is the critical invariant: it is not a retryable transport error. It must never cross an account or channel boundary automatically.

## Offline regression commands

Run from `backend/new-api-relay`:

```powershell
go test -count=1 ./relay/channel/task/ali ./relay/channel/task/doubao
go test -count=1 ./service ./model
```

The adapter tests cover pinned model enforcement, metadata isolation, input-mode bounds, current Ark poll shapes, terminal states, missing result URLs, unknown states, and task-ID path injection. Existing service/model tests cover capability intersection, transport-unknown handling, route/slot retention, and sticky manual reconciliation.

## External blockers

Real staging cannot be signed off until all of the following exist:

- a real-name-enabled Volcengine account with Ark access in `cn-beijing`;
- an Ark API key with permission and quota for `doubao-seedance-2-0-260128`;
- the corresponding real Ali Bailian channel for `wan2.7-t2v-2026-06-12`;
- real native channel IDs and selected-key fingerprints;
- approved RPM and active-task limits from the provider accounts;
- a low-cost canary budget and provider-console/billing evidence.

No secret, quota, price, or production-readiness decision is inferred by this repository.
