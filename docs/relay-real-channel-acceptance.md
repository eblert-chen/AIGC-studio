# 真实 Provider、Download Gateway 与 Huawei OBS 验收

`backend/new-api-relay/cmd/relay-real-channel-acceptance` 是 staging 专用、失败关闭的真实渠道验收程序。它只接受配置 schema v2，不把 Mock、new-api quota、客户售价、Kling credits 或缺失账单解释成供应商成本。

## 边界与三阶段

验收程序始终通过客户 Platform 创建和读取任务；它不绕过 Platform 直接提交 Relay `/v1/generations`。`create` 和 `finalize` 必须使用规范化、非占位的 Bearer token，开发身份头只允许 loopback `preflight`。

- `preflight`：严格解析配置，检查所需环境变量名、Bearer、公开 Huawei OBS HTTPS endpoint、bucket 及公钥格式。它不连接外部服务，也不创建任务；缺少真实凭据时结果应为 `BLOCKED`，不是伪造的 `PASS`。
- `create`：必须新建 Platform 任务；配置中的 `existing_task_id` 和命令行 `-task-id` 都会被拒绝。程序等待单产物成功，核对真实 Kling 官方渠道、固定凭据指纹、provider task、OBS HEAD、私有匿名拒绝、Platform→Download Gateway ticket、完整下载 SHA-256、签名回调和 wallet reserve/settle。随后写出 schema v2 `create_checkpoint.json`，结果保持 `BLOCKED`，等待独立下载 producer proof 和独立财务审批。
- `finalize`：只接受 create 阶段 checkpoint 的精确文件 SHA-256。checkpoint 固定 company/user/task/job/model/capability/request、provider route/task、产物、OBS、callback 和 wallet；所有绑定都会再次从真实数据库验证。`-task-id` 可省略，若提供则必须与 checkpoint 完全一致。

示例配置位于 [`tests/relay-real-channel-acceptance.config.example.json`](../tests/relay-real-channel-acceptance.config.example.json)。其中所有 `sha256:...`、UUID、URL 和证据路径都必须替换成这次运行的真实值。

```text
go run ./cmd/relay-real-channel-acceptance -phase preflight -config <config-v2.json>
go run ./cmd/relay-real-channel-acceptance -phase create -config <config-v2.json>
go run ./cmd/relay-real-channel-acceptance -phase finalize -config <config-v2.json>
```

每次运行使用唯一输出目录；`report.json`、checkpoint 和拆分证据均以 `O_EXCL`、`0600` 创建，已有文件不会被覆盖。

## Gateway 下载与外部完成证明

Platform 不把 Relay 生成的 OBS 临时签名 URL返回给客户。它先验证 Relay 的结构化 `storage_binding`，把 source URL SHA、OBS endpoint/bucket/object、Relay 有效期及 Gateway registration/ticket/transfer 信息不可变地写入 `download_records`，再返回一次性 Gateway URL。

验收程序会执行以下检查：

1. 使用 OBS 凭据 HEAD 精确对象，核对 size、content type 和对象 metadata SHA-256；使用无签名的精确对象 URL确认匿名请求得到 401、403 或 404。
2. Gateway URL必须是配置 origin 下的 `HTTPS /downloads/{32-byte-base64url-token}`，禁止 userinfo、非 443 端口、query、fragment、模糊编码和跳转。
3. Platform 数据库中的 `source_url_sha256`、`gateway_ticket_url_sha256`、双层 TTL、company/task/asset/user 及四类标识必须完整。`registration_request_id` 只绑定注册幂等；`issuance_request_id` 等于 Platform `DownloadRecord.request_id`；`gateway_request_id` 必须等于该 issuance ID；`transfer_reference` 是独立 UUID。
4. Gateway 响应必须是无 Range 的完整 200，长度和 SHA-256 与 Relay 产物一致；响应 request/transfer 标识必须匹配持久化绑定。同一 ticket 第二次使用必须返回 404。

create checkpoint 写出后，必须另行从 Platform 申请一个新的下载 ticket 并由真实 Gateway 完整传输。这个外部记录的 `gateway_issued_at` 和 completion 都必须晚于 checkpoint；create 阶段自身产生的早期下载记录不能拿来通过 finalize。

Gateway 在 Platform 以 201 接受完成事件后，才把下面两份只读证据落库：

- exact payload 示例：[`tests/relay-real-channel-download-proof.example.json`](../tests/relay-real-channel-download-proof.example.json)
- signature envelope 示例：[`tests/relay-real-channel-download-proof.signature.example.json`](../tests/relay-real-channel-download-proof.signature.example.json)

读取端点为 `GET /internal/v1/download-completions/{signed_event_id}/proof` 与 `/proof/signature`，仅使用独立的 proof-read token。验收 CLI 保存并验证返回的精确字节，不持有 Gateway producer 私钥、completion HMAC 或 Platform internal service token，也不会自签或自行提交完成回执。

Gateway proof 使用：

```text
relay-download-completion-proof.v1\n + exact_payload_bytes
```

算法为 Ed25519。signature envelope 的 `payload_sha256` 使用 `sha256:<64-lowercase-hex>`；CLI 环境中只提供 base64 公钥，配置同时固定该公钥的 SHA-256 指纹。proof 必须逐字段绑定 Platform completion、download record、task/artifact、issuance/gateway/transfer ID、OBS object、200/full_body、字节数、SHA-256 和时间线。

当前 real gate 只接受 `edge_gateway` producer proof。OBS access log 只有在部署同等级、独立、可签名且可回读的 producer 后才能增加新的验收来源；在此之前该路径保持 `BLOCKED`。

## Provider 账单与独立财务审批

账单、审批 payload 和 signature envelope 示例分别为：

- [`tests/relay-real-channel-provider-bill.example.json`](../tests/relay-real-channel-provider-bill.example.json)
- [`tests/relay-real-channel-provider-bill-approval.example.json`](../tests/relay-real-channel-provider-bill-approval.example.json)
- [`tests/relay-real-channel-provider-bill-approval.signature.example.json`](../tests/relay-real-channel-provider-bill-approval.signature.example.json)

账单 schema v2 必须绑定 Platform task ID、Relay job ID、provider task、channel、账单引用、发生时间、CNY minor units、计费数量/单位、合同费率来源，以及真实原始发票、导出或合同文件的 SHA-256。原始来源文件必须是非 symlink、1 byte 至 16 MiB 的普通文件；CLI 实际读取并哈希，但不会复制原文、路径或内容到输出。

正成本要求 `amount_cents > 0`、`cost_disposition = billed`，且不得携带 zero-cost 字段。零成本不会被默认接受，必须同时满足：

- `amount_cents = 0`；
- `cost_disposition = verified_zero`；
- `zero_cost_reason` 是 `free_quota`、`promotional_credit`、`provider_waiver` 或 `included_contract`；
- `zero_cost_evidence_reference` 非空；
- `evidence_source` 只能是 `provider_invoice` 或 `contract_rate`。

财务审批签名输入为：

```text
relay-provider-bill-approval.v1\n + exact_approval_payload_bytes
```

审批 payload 固定 checkpoint、脱敏账单和原始来源文件的 SHA-256，并重复绑定 task/job/provider/channel/amount/currency/disposition。`decision` 必须为 `approved`，审批有 canonical nonce，过期时间不得超过批准后 7 天。CLI 只持有 Ed25519 公钥和固定指纹；财务审批公钥必须与 Gateway producer 公钥不同。

通过后，程序走 Relay 正式 `EnqueuePlatformChannelCost` 合同写入 provider cost，验证精确重放幂等、冲突拒绝、签名投递、Relay/Platform 双侧单事件及 append-only，并确认 provider cost 没有改变客户 task、wallet 或 ledger。

## Huawei OBS live gate 证据

带 `integration && obs_live` build tag 的真实 OBS round-trip 额外要求：

- `RELAY_OBS_LIVE_EVIDENCE_DIR`：已存在、绝对路径、非 symlink 目录；
- `RELAY_OBS_LIVE_SOURCE_REVISION`：冻结 Relay 运行时构建输入快照的 40 位小写 SHA-1，不是 Git commit，也不能填写上游 `HEAD`；
- `RELAY_OBS_LIVE_SOURCE_SNAPSHOT_SHA256`：同一冻结快照的 `sha256:<64-lowercase-hex>`；
- `RELAY_OBS_LIVE_SOURCE_FILE_COUNT`：该快照纳入哈希的正整数文件数；
- `RELAY_OBS_LIVE_IMAGE_DIGEST`：候选镜像本地不可变 Docker image ID，格式为 `sha256:<64-lowercase-hex>`；
- 真实 `HUAWEI_OBS_*` 凭据与私有 bucket。

这些 provenance 值不接受人工自由填写。必须从主机侧 runner 启动 gate：

```text
node scripts/run-relay-obs-live-acceptance.mjs --candidate-image <candidate-tag-or-image-id> --evidence-dir <absolute-existing-directory>
```

## Candidate runtime build binding

Schema v2 pins the Relay base URL, an independent service-token environment
name, upstream revision, frozen source SHA-1/SHA-256/file count, and immutable
image digest. `create` and `finalize` both call the service-authenticated
`GET /internal/platform-relay/runtime-build-identity` endpoint. The response is
validated against those expected values and stored in the create checkpoint
and final report. A different process instance of the exact same build is
allowed; any build-field drift fails closed.

Source provenance is compiled into the candidate binary by Docker build args
derived from the same frozen snapshot as the OCI labels. Staging/production
startup rejects unknown linker values and rejects any deployment environment
that disagrees with the compiled SHA-1, SHA-256, or file count. The image digest
remains a deployment attestation value. Consequently an old binary cannot pass
by self-reporting a newer source snapshot through environment variables.

runner 先把候选引用解析成不可变 image ID，再以该 ID 执行 `docker image inspect`，并要求镜像的三个 label 与当前冻结源码快照精确一致：

- `org.opencontainers.image.revision` = snapshot SHA-1；
- `ai.video.relay.source-snapshot-sha256` = snapshot SHA-256；
- `ai.video.relay.source-file-count` = snapshot file count。

`ai.video.relay.upstream-revision` 只标识移植所基于的上游版本，不能替代上述 source snapshot 绑定。runner 随后使用 digest 固定的 Go toolchain 容器执行 tagged test；Relay 源码只读挂载，进程使用非 root UID/GID、PID 上限、cap-drop 和 no-new-privileges，不挂载 Docker socket，也不授予 privileged 权限。测试只接收 runner 已核对的 snapshot SHA-1/SHA-256/file count 和 image ID，schema v2 PASS 证据会再次固定全部值及 `image_source_labels_verified=true`。直接运行 tagged Go test 或仅手工设置这些环境变量不构成可接受的 live provenance。

Go test 的 stdout/stderr 由 runner 在内存中捕获，不原样转发。runner 会按本次实际 AK/SK/security token/bucket 及 URL/base64/JSON 变体扫描输出，同时拒绝 `AccessKeyId`、`Signature`、`x-obs-*`、`x-amz-*` 等签名请求标志；成功只输出固定的无秘密 PASS 摘要，失败只输出泛化原因或退出码。测试容器带唯一名称和 `--rm`，runner 在 `finally` 中仍会按该名称执行精确强制清理。

Go test 只向正式证据目录下由 runner 新建的隔离临时子目录写文件。runner 完成镜像绑定、证据解析、敏感扫描和运行后源码快照复核后，才用 create-only hard link 原子发布到正式目录；任何前置失败都会精确删除临时目录，因此不会遗留尚未认证的 PASS JSON。

live gate 上传唯一测试对象，验证匿名拒绝和签名完整下载，然后先删除精确对象并以带权限的 HEAD 确认 404，最后才写 PASS 证据。证据文件 create-only、`0600`，只包含冻结 source snapshot/image pin、endpoint host、bucket SHA-256、object key、bytes/SHA、HTTP 状态、UTC 时间线和 cleanup 结果；不会包含 raw bucket、AK/SK/security token 或任何 signed URL。写入前会扫描实际秘密及 URL/base64/JSON 变体。失败路径仍保留精确对象 emergency cleanup。

没有真实 OBS 环境时只能运行 helper 负测和 tagged compile，不能把未执行的 live test 记为通过。

## 结论边界

一次 finalize PASS 只证明这一条真实 staging 记录满足当前合同。手工固定的 provider 账单和财务签名仍是迁移验收证据，不是持续生产对账管道；生产仍需接入供应商账单导出/API 并走同一成本事件合同。

Python Relay 只保留为隔离、无生产凭据的历史行为 oracle；它不是生产 peer 或回滚路径。
本文和 CLI 的 PASS 只证明绑定当前 new-api 镜像的一条真实 staging 记录，不替代真实
Provider/OBS、callback、billing reconciliation、容量、备份恢复和签字发布门禁，也不授权
把 Python oracle 接入任何受保护环境。
