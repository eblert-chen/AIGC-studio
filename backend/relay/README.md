# AI 视频生成中转站（首版骨架）

这是一个供应商无关的 FastAPI 服务。默认本地环境只启用明确标识的
`mock-video`；仓库同时提供可灵 3.0、阿里百炼 Wan 2.7、火山方舟 Ark 的官网契约
适配器，但三者均保持 `production_ready=False`，没有真实密钥和 staging 验收时不得
宣称已经接通生产渠道。

## 已提供的边界

- `POST /v1/generations`：幂等提交生成任务，以 `202` 返回统一资源的 `id`、`object` 和初始状态。
- `GET /v1/generations/{job_id}`：查询统一任务状态。
- `GET /v1/models`、`GET /v1/models/{model_id}`：使用服务凭证查询版本化模型能力；支持 `ETag`/`If-None-Match`。
- `GET /v1/models/capabilities`：兼容旧调用方的受认证能力视图，新接入不得依赖。
- `POST /v1/providers/{provider}/webhooks`：供应商回调入口，包含验签、事件去重和状态转换语义。
- `GET /health/live`、`GET /health/ready`：进程和依赖健康状态。
- Provider Adapter：提交、能力发现、健康检查、可信 Webhook 或主动轮询抽象。
- Provider Router：按模型筛选健康渠道，按优先级选择，并在已证明未创建任务的安全错误
  范围内切换账号或渠道；提交结果未知时固定原路由并进入对账。
- Provider Account Pool：PostgreSQL 共享的活跃任务槽、固定窗口 RPM、冷却、停用和粘性路由。
- Provider Monitor：定时保存每条路由的健康/延迟、真实上游终态成功率、批量账号失效，
  生成去重的触发/恢复事件并可投递签名告警 Webhook。
- Queue：带 ack/nack/defer 的异步工作项接口、进程内实现及 Redis Streams + 延迟集合实现。
- Repository：任务、幂等键、回调事件接口、进程内实现及 SQLAlchemy 持久实现。
- Transactional Outbox：任务、幂等键和待投递消息在同一数据库事务中提交。

## 本地运行

在本目录执行：

```bash
python -m uvicorn relay_service.main:app --reload
```

API 文档位于 `http://127.0.0.1:8000/docs`。

默认 `memory` 模式使用进程内仓库和队列，仅适合开发与测试。进程重启后数据会丢失，
也不支持多实例部署。

## 持久化生产模式

配置：

```text
RELAY_ENVIRONMENT=production
RELAY_RUNTIME_MODE=production
RELAY_DATABASE_URL=postgresql+asyncpg://...
RELAY_REDIS_URL=redis://...
RELAY_CLIENT_CREDENTIALS_JSON={"customer-platform":{"tenant_id":"<customer-platform-tenant-uuid>","api_key":"<customer-platform-random-secret-at-least-32-bytes>","scopes":["generation:invoke"]},"customer-platform-operations":{"tenant_id":"<customer-platform-tenant-uuid>","api_key":"<different-operations-secret-at-least-32-bytes>","scopes":["operations:submission-reconciliation"]},"internal-tiktok":{"tenant_id":"<different-internal-tiktok-tenant-uuid>","api_key":"<different-internal-tiktok-random-secret-at-least-32-bytes>","scopes":["generation:invoke"]},"internal-tiktok-operations":{"tenant_id":"<different-internal-tiktok-tenant-uuid>","api_key":"<different-tiktok-operations-secret-at-least-32-bytes>","scopes":["operations:submission-reconciliation"]}}
RELAY_PROVIDER_MONITOR_ENABLED=true
RELAY_PROVIDER_MONITOR_RETIRED_PROVIDERS=
RELAY_PROVIDER_ALERT_WEBHOOK_URL=https://alerts.example.com/relay/provider
RELAY_PROVIDER_ALERT_SIGNING_SECRET=<independent-random-secret-at-least-32-bytes>
RELAY_PROVIDER_ALERT_CLAIM_LEASE_SECONDS=60
RELAY_ARTIFACT_STORE=huawei_obs
HUAWEI_OBS_ACCESS_KEY_ID=...
HUAWEI_OBS_SECRET_ACCESS_KEY=...
HUAWEI_OBS_ENDPOINT=https://obs.<region>.myhuaweicloud.com
HUAWEI_OBS_BUCKET=...
```

先执行数据库迁移：

```bash
alembic upgrade head
```

再分别运行 API、Outbox Dispatcher 和 Consumer Worker：

```bash
uvicorn relay_service.main:app --host 0.0.0.0 --port 8000
python -m relay_service.dispatcher
python -m relay_service.worker
python -m relay_service.transfer_worker
python -m relay_service.provider_sync_worker
python -m relay_service.provider_monitor_worker
python -m relay_service.callback_worker
```

API 在生产模式只负责同事务写入任务、幂等记录和 `relay_outbox`，不会在请求进程内
执行任务。Dispatcher 使用短租约认领 Outbox，投递到 Redis Streams 后才标记
`published`；投递失败会保留消息并指数退避。因此不会出现“数据库建任务成功但根本
没有可恢复消息”的窗口。投递采用 at-least-once 语义，Worker 必须按任务状态去重；
当前 Worker 会跳过已经进入 `processing` 或终态的重复投递。

Worker 从 Redis consumer group 读取 delivery，处理成功或落库明确失败后 ack；
可重试渠道错误会 nack 并增加 delivery attempt。默认最多 3 次，可用
`RELAY_WORKER_MAX_ATTEMPTS` 调整；达到上限后任务写入
`PROVIDER_RETRIES_EXHAUSTED` 并 ack，避免毒消息无限循环。SIGINT/SIGTERM 会停止
领取新任务，并在当前处理边界后关闭 Redis 和数据库连接。

默认不会自动装载任何官方渠道；本地契约环境显式设置
`RELAY_ENABLE_MOCK_PROVIDER=true` 才会装载 `mock-video`。可灵、Wan 和 Ark 通过
`RELAY_PROVIDER_FACTORIES` 按需启用，当前均为 staging 契约实现。官方没有公开通用
提交幂等键，因此 POST 网络结果不明确时禁止自动重提，必须进入渠道对账门禁。

Redis Streams 实现使用 consumer group、ack、删除及空闲消息 reclaim。SQL 查询在
PostgreSQL 下使用 `FOR UPDATE SKIP LOCKED`，允许多个 Dispatcher 并行工作。SQLite
支持用于持久化契约测试，不应作为生产数据库。

`/health/ready` 会分别报告：

- Repository 类型、是否持久、是否支持 Outbox 和连接健康；
- Queue 类型、是否持久、连接健康和深度；
- Provider 健康；
- 生产运行模式下 Provider Monitor 的最近成功周期、活动告警、待投递积压和死信。

生产模式只要 Repository、Queue、转存队列或产物存储不可用，readiness 就返回
`503 unavailable`。上游渠道是新任务准入依赖，不是 API 进程依赖；所有 Provider 都不可用
时返回 HTTP `200`、`state=degraded`，让存量任务回调、查询、产物访问和对账继续进入
Relay。Provider Monitor 周期陈旧、有活动告警、待投递陈旧、有死信或未配置告警接收端
同样会降级，但仍返回 HTTP `200`。该状态由数据库中的周期和投递记录推导，不是 Worker
进程的直接心跳；生产编排必须解析 readiness 内容，并另行检查进程和重启次数。

仓库包含生产形状的 `Dockerfile`。容器启动迁移应作为独立 release job 执行，不要让
每个 API 副本同时自动迁移。Dispatcher 可复用同一镜像并覆盖启动命令。

> 仓库已有三家官方接口适配器，但“有接口文件”不等于“真实渠道已接通”。没有真实
> 账号、地区、额度、账单和 staging 端到端验收前，不能用于生产生成流量。

## 服务客户端认证与租户隔离

任务提交和查询必须同时携带：

```text
X-Client-ID: <service client id>
X-API-Key: <service API key>
```

`tenant_id` 不再属于生成请求契约，也绝不能由调用方在请求体中自行声明。服务端通过
认证客户端取得可信的租户 UUID，并以它作为建任务、幂等隔离和查询过滤条件。其他
租户查询已存在任务也统一返回 `404 JOB_NOT_FOUND`，不泄露任务存在性。
认证得到的 `client_id` 会作为内部审计字段随任务持久化，但不会出现在任务响应或回调中。
生产配置拒绝不同客户端复用同一 `tenant_id`，因此客户平台和内部 TikTok 系统必须使用
彼此独立的 client、API key 与 tenant。

开发环境在未设置凭证表时，继续从以下环境变量创建一个单客户端，兼容本地
Compose 和原有联调配置：

```text
RELAY_CLIENT_ID
RELAY_API_KEY
RELAY_TENANT_ID
```

未配置时会使用代码内的开发默认值，它只能用于本地开发，不能用于部署。

生产环境必须显式提供 `RELAY_CLIENT_CREDENTIALS_JSON`，格式是以 `client_id`
为键的非空 JSON 对象。每个调用方独立绑定可信租户 UUID 和 API key，例如：

```json
{
  "customer-platform": {
    "tenant_id": "8b2f60c2-3f90-4ec7-ae43-0df53e8fa7c5",
    "api_key": "<由密钥管理系统注入的随机密钥>",
    "scopes": ["generation:invoke"]
  },
  "customer-platform-operations": {
    "tenant_id": "8b2f60c2-3f90-4ec7-ae43-0df53e8fa7c5",
    "api_key": "<另一把仅供受控对账使用的随机密钥>",
    "scopes": ["operations:submission-reconciliation"]
  },
  "internal-tiktok": {
    "tenant_id": "3d575eb0-e28b-4b7c-a445-6c2456b29570",
    "api_key": "<另一把独立随机密钥>",
    "scopes": ["generation:invoke"]
  },
  "internal-tiktok-operations": {
    "tenant_id": "3d575eb0-e28b-4b7c-a445-6c2456b29570",
    "api_key": "<TikTok 受控对账专用的独立随机密钥>",
    "scopes": ["operations:submission-reconciliation"]
  }
}
```

同一业务调用方的生成凭证和对账凭证共享 tenant UUID，以便只处理本租户任务，
但必须使用不同的 client ID 和 API key。生成凭证只授予 `generation:invoke`；
对账凭证只授予 `operations:submission-reconciliation`，不能提交生成任务。

生产 API key 按 UTF-8 计算必须至少 32 字节，并禁止使用代码内已知开发默认值、
`replace-with-...`、`change-me` 等明显占位值；不同 `client_id` 也必须使用不同
API key。JSON 为空、格式错误、键重复、字段缺失、UUID 非法、密钥重复或密钥
不合格都会让 API 在启动阶段直接失败；生产环境不会退回 `RELAY_CLIENT_ID`、
`RELAY_API_KEY` 和 `RELAY_TENANT_ID`。部署时应通过密钥管理服务或受保护的容器
Secret 注入整份 JSON，不要提交到仓库、镜像或普通配置文件。

应用工厂 `create_app(authenticator=...)` 仍支持注入其他
`ClientAuthenticator`，例如改用密钥管理服务或签名令牌。默认静态实现使用
常量时间比较校验 API key，响应与错误详情不会回显 key。接入日志系统时也必须
对认证头整体脱敏。

## 任务与回调语义

任务状态：`queued -> submitting -> processing -> succeeded`，处理中也可进入
`failed` 或 `cancelled`。回调事件必须有全局唯一 `event_id`，重复事件会返回已接收，
但不会重复更新任务。终态不可被后续回调覆盖。

Mock 回调使用 `X-Mock-Webhook-Secret`，默认测试密钥为 `development-only-secret`。
该机制仅用于证明适配器必须负责验签；接入真实供应商时必须实现其官方签名算法，
并从密钥管理服务注入密钥。

幂等键按认证所得的 `tenant_id` 隔离。同一租户用相同键和相同请求重试会返回原任务；
相同键携带不同请求会返回 `409 IDEMPOTENCY_KEY_REUSED`。生产实现需在数据库中
对 `(tenant_id, idempotency_key)` 建立唯一约束，并让“建任务 + 写入 outbox”处于
同一事务，避免数据库成功而消息丢失。

## 稳定错误码

| HTTP | code | 含义 | 可重试 |
| --- | --- | --- | --- |
| 401 | `CLIENT_AUTHENTICATION_REQUIRED` | 缺少客户端认证头 | 否 |
| 401 | `INVALID_CLIENT_CREDENTIALS` | 客户端凭证无效 | 否 |
| 404 | `ROUTE_NOT_FOUND` | API 路由不存在 | 否 |
| 405 | `METHOD_NOT_ALLOWED` | HTTP 方法不受支持 | 否 |
| 500 | `INTERNAL_ERROR` | 未预期的服务端故障，响应已脱敏 | 是 |
| 409 | `IDEMPOTENCY_KEY_REUSED` | 同一幂等键对应了不同请求 | 否 |
| 404 | `JOB_NOT_FOUND` | 本地任务不存在 | 否 |
| 404 | `PROVIDER_NOT_FOUND` | 回调渠道未注册 | 否 |
| 404 | `PROVIDER_TASK_NOT_FOUND` | 回调中的上游任务无法关联 | 否 |
| 401 | `WEBHOOK_SIGNATURE_INVALID` | 渠道回调验签失败 | 否 |
| 422 | `WEBHOOK_PAYLOAD_INVALID` | 渠道回调格式不符合契约 | 否 |
| 任务错误 | `NO_PROVIDER_AVAILABLE` | 没有健康且兼容的渠道 | 是 |
| 任务错误 | `PROVIDER_TEMPORARILY_UNAVAILABLE` | 渠道暂时不可用 | 是 |
| 任务错误 | `MODE_NOT_SUPPORTED_BY_MODEL` | 模型不支持所选生成模式 | 否 |
| 任务错误 | `REQUEST_NOT_SUPPORTED_BY_MODEL` | 输入或输出参数超出模型能力 | 否 |
| 任务错误 | `CAPABILITY_REVISION_MISMATCH` | 已确认能力版本发生漂移 | 否 |
| 任务错误 | `GENERATION_CHANNEL_UNAVAILABLE` | 底层生成渠道不可用，具体身份不公开 | 视来源 |
| 任务错误 | `PROVIDER_RETRIES_EXHAUSTED` | 可重试渠道错误达到 Worker 上限 | 否 |
| 任务错误 | `WORKER_ATTEMPTS_EXHAUSTED` | Worker 内部处理连续失败达到上限 | 否 |

请求校验错误统一返回 `422 REQUEST_VALIDATION_FAILED` 和相同的 `error` 信封。
错误详情只包含位置、类型和说明，不包含原始输入、API key 或用户提示词。
所有 Provider/Webhook 原始错误都经过公开错误映射；即使以后接入未预登记名称的第三方或
逆向渠道，其错误码、诊断消息和账号身份也不会原样返回给调用方。
生产接入还需增加限流、凭证轮换、审计以及认证失败监控。

## 模型能力契约

生成模式包含 `text_to_image`、`text_to_video`、`image_to_video` 和
`video_to_video`。统一请求最多容纳 15 个输入素材，可表达 9 图 + 3 视频 + 3 音频等
组合；每个模型逐模式声明 `max_images`、`max_videos`、`max_audio`，单类上限和三类
合计都不能超过统一请求上限。

路由在调用 Provider 前校验提示词长度、输入媒体类型、每类媒体数量、时长、比例、
分辨率和输出条数。超出能力返回任务错误
`REQUEST_NOT_SUPPORTED_BY_MODEL`，不会把已知无效请求发送给上游。

`GET /v1/models` 按模式返回严格能力文档，单模型 `capability_revision` 与目录
`catalog_revision` 都是稳定的 SHA-256 内容摘要。目录由已配置路由形成，不会随渠道一次
健康检查的短暂波动消失；同一公开模型由多个路由承载时，只发布这些路由共同保证的能力
交集。响应不暴露 Provider、账号或候选路由。调用方确认 revision 后，可在提交顶层携带
`expected_capability_revision`；不匹配时 Relay 在 Provider POST 前以
`CAPABILITY_REVISION_MISMATCH` 终止任务。

统一 API 借鉴 OpenAI 视频接口的异步资源语义，但由于同时支持图像、多类素材和多产物，
并不是 OpenAI SDK 的 wire/drop-in 兼容层。完整公开契约见 `docs/generation-api-v1.md`。

## 产物转存边界

供应商的成功回调不会直接把任务标记为 `succeeded`。状态链为：

```text
processing -> transferring -> succeeded
                         \-> failed (ARTIFACT_TRANSFER_FAILED)
```

成功回调、`transferring` 状态和 `artifact.transfer` Outbox 在同一个数据库事务中
提交。独立 `transfer_worker` 从专用 Redis Stream 读取转存任务。每个转存任务先领取
持久化 token lease，慢下载/上传期间续租，部分进度和最终状态都以 token-CAS 写入；过期
旧 Worker 无法覆盖新 Worker 的成功结果。它只接受 HTTPS
443 临时地址，解析并固定全部 DNS 地址，拒绝私网、本机、保留地址、URL 凭证和
重定向；下载过程限制总超时、MIME 和最大字节数，并在流式写入临时文件时计算
SHA-256。

目标对象键固定为：

```text
outputs/{tenant_id}/{job_id}/{asset_id}
```

任务响应只保存对象键、MIME、大小和 SHA-256，不保存供应商临时 URL 或 OBS 永久
公开 URL。下载通过
`GET /v1/generations/{job_id}/artifacts/{asset_id}/download` 临时创建 5 分钟签名
地址，并继续执行客户端认证和租户隔离。

转存逐个对象保存检查点。部分成功后重试只继续未完成对象；达到
`RELAY_TRANSFER_MAX_ATTEMPTS` 后任务进入 `failed`，错误码为
`ARTIFACT_TRANSFER_FAILED`。确定性对象键让崩溃后的重复写保持幂等。

`ArtifactStore` 提供内存测试实现和 Huawei OBS 适配器。OBS SDK 是可选依赖：

```bash
pip install ".[obs]"
docker build --build-arg INSTALL_OBS=true -t ai-video-relay .
```

SDK 未安装或环境凭证不完整时会安全拒绝启动，不会回显 AK/SK。OBS 桶必须保持
私有；适配器使用官方 SDK 的 `putContent` 可读流上传，随后以 `getObjectMetadata`
核对对象大小、Content-Type 和 SHA-256 自定义元数据，全部一致后任务才能成功；下载使用
`createSignedUrl` 签发短时地址：

- [华为云 OBS Python 流式上传](https://support.huaweicloud.com/sdk-python-devg-obs/obs_22_0902.html)
- [华为云 OBS Python 获取对象元数据](https://support.huaweicloud.com/intl/zh-cn/sdk-python-devg-obs/obs_22_0920.html)
- [华为云 OBS Python 创建签名 URL](https://support.huaweicloud.com/intl/en-us/sdk-python-devg-obs/obs_22_1301.html)

环境与持久运行模式相互独立：

- `RELAY_ENVIRONMENT=production` 强制 PostgreSQL/Redis 持久模式、Huawei OBS，并
  禁止 Mock Provider；不能降级到内存存储。
- `RELAY_ENVIRONMENT=development` 可以用持久 PostgreSQL/Redis 与 Compose
  共享卷上的 `RELAY_ARTIFACT_STORE=filesystem` 完成本地跨容器产物转存和下载
  联调；readiness 返回 `degraded`，并标记
  `production_controls_enforced=false`。`memory` 仍只适合单进程状态机测试。
  `filesystem` 不允许在生产环境启用；生产产物存储只允许 Huawei OBS。
## Provider 工厂、提交租约与轮询迁移

真实适配器通过逗号分隔的工厂入口注册：

```text
RELAY_PROVIDER_FACTORIES=relay_service.providers.kling:create_kling_provider,relay_service.providers.alibaba_wan:create_alibaba_wan_provider,relay_service.providers.volcengine_ark:create_volcengine_ark_provider
RELAY_SUBMISSION_CLAIM_LEASE_SECONDS=120
RELAY_PROVIDER_FAILURE_THRESHOLD=3
RELAY_PROVIDER_COOLDOWN_SECONDS=30
RELAY_PROVIDER_ADMISSION_RETRY_SECONDS=5
```

每个入口必须使用 `python.module:factory` 格式，并返回一个 `ProviderAdapter` 或非空适配器集合。当前适配器契约版本为 `1`；API、Generation Worker 和 Provider Sync Worker 启动时都会校验 Manifest、非空能力列表、字段一致性和每路由唯一的 `model + mode`。生产环境会拒绝未显式设置 `production_ready=True` 的适配器，也会无条件拒绝 Mock。能够核准上游幂等设施时才使用稳定 `job.id`；没有官方幂等保证的渠道必须在 POST 结果未知时停止自动重提，并确保提交 HTTP 超时短于 submission claim 租期。

适配器必须声明 `channel_type`（`reverse`、`third_party_api` 或
`official`）和不含密钥的稳定 `account_id`。同一工厂可返回同一 Provider 的多个账号；
Relay 以 `provider@account_id` 作为内部稳定路由，在同优先级账号间轮转，尊重每账号
`max_concurrency` 和 `requests_per_minute`，并在账号冷却或禁用时绕行。账号状态持久化到
PostgreSQL `provider_account_states`，数据库行锁协调所有 Generation Worker；活跃任务数
从任务状态派生，覆盖提交、长时生成和未知提交对账，Provider 明确终态后才释放。内部
路由在 POST 前绑定到 submission claim token 并随任务持久化，确保查询与回调仍回到创建
任务的账号；关闭准入只 drain 新任务，不会让已接受任务漂移。公开能力响应和账号状态表
都不会保存密钥。

账号池忙、固定一分钟速率窗口已满或所有候选暂时冷却时，Generation Worker 会把原工作
项延迟后重新投递而不增加 Provider 尝试次数，默认延迟由
`RELAY_PROVIDER_ADMISSION_RETRY_SECONDS` 控制；适配器提供的 `retry_after_seconds` 可覆盖
该值。连续账号级失败达到 `RELAY_PROVIDER_FAILURE_THRESHOLD` 后冷却
`RELAY_PROVIDER_COOLDOWN_SECONDS`。永久鉴权失效可关闭该账号的新任务准入，但原账号的
存量任务仍由 Provider Sync 粘性轮询。

新增逆向、第三方 API 或官方渠道的规范、可复制代码模板、错误标志决策表和验收命令见
[`docs/provider-adapter-v1.md`](../../docs/provider-adapter-v1.md) 与
[`docs/reverse-account-pool.md`](../../docs/reverse-account-pool.md)、
[`examples/provider_adapter_template.py`](examples/provider_adapter_template.py)。可用
`python -m relay_service.providers.verify python.module:factory` 在不输出密钥的前提下检查
渠道类型、账号 Manifest 和逐模式能力；增加 `--production` 会同时执行生产门禁。这是
结构检查，不会登录真实账号或替代 staging canary。

Worker 在调用 Provider 前以数据库 token 和到期时间原子领取任务；重复 delivery 在有效租期内不得再次提交。POST 结果未知、Worker 丢失租约或提交成功后落库失败时，任务进入 `reconciliation_required`，不会自动重提，并会发送明确的 `reconciliation_required` 状态回调而不是伪装成失败。内网运维通过 `GET /v1/operations/submission-reconciliations` 发现当前租户的待处理任务，再用 `POST /v1/operations/submission-reconciliations/{job_id}` 确认上游任务是否创建；只有确认未创建才进入失败，确认已创建则绑定上游任务编号并恢复轮询。

Provider Sync 遇到可重试查询错误时使用持久化失败计数和退避时间；每个待轮询任务还会领取独立 token lease 并续租，旧 Poller 晚返回不能写进度、失败、终态或启动转存。若 Provider 明确不可重试但又不能证明任务失败，则保留 `(provider, provider_task_id)` 并转入对账，避免永久轮询。唯一 `(provider, provider_task_id)` 约束继续防止上游任务被重复绑定。最新 Relay Alembic head 为 `0012_generation_contract_v1`，发布时必须先执行：

```bash
alembic upgrade 0012_generation_contract_v1
alembic check
```

`RELAY_SUBMISSION_CLAIM_LEASE_SECONDS`、`RELAY_PROVIDER_POLL_CLAIM_LEASE_SECONDS` 和
`RELAY_ARTIFACT_TRANSFER_CLAIM_LEASE_SECONDS` 必须为正数。默认值只是起点，应按真实
Provider 调用时延、大文件转存时延、Redis reclaim 周期和故障恢复目标在 staging 验证。

## Provider 高可用监控与告警（migration 0011）

`provider_monitor_worker` 使用 PostgreSQL 全局租约，定时探测每条具体
`provider@account_id` 路由，并把健康、延迟、准入状态和规范化错误码写入
`provider_health_samples`。真实上游成功在任务进入 `transferring` 时写入
`provider_outcome_events`；后续 OBS 转存失败不会污染 Provider 成功率。告警状态按
Provider 去重，连续达到触发/恢复周期时分别写入 `triggered`/`recovered` 事件。
上游终态事件在数据库层只追加：generation job 外键使用 `RESTRICT`，PostgreSQL 拒绝
`UPDATE`、`DELETE`、`TRUNCATE`，SQLite 拒绝 `UPDATE`、`DELETE`。

默认规则覆盖 5 分钟窗口成功率低于 80%（至少 20 个终态）、至少两条路由中 50% 健康
失败、以及同一 Provider 至少三个账号因 Provider 错误永久停用。人工 drain 不计入批量
失效。健康探测只观测，不会自动关闭账号或迁移任务；请求故障切换仍由 Router 根据安全
错误范围执行。

配置 `RELAY_PROVIDER_ALERT_WEBHOOK_URL` 与独立的
`RELAY_PROVIDER_ALERT_SIGNING_SECRET` 后，触发和恢复事件通过 HMAC-SHA256 签名、持久化
claim、指数退避和最多 8 次投递发送；达到上限进入 `dead_letter`。开发环境未配置
Webhook 时事件仍落库并写脱敏日志，但不会产生外部通知；生产环境强制启用 Monitor 并
要求告警 URL/密钥成对存在。完整变量、验签协议、readiness 语义、故障演练
和运维查询见 [`docs/provider-monitoring.md`](../../docs/provider-monitoring.md)。

## Durable tenant callbacks (migration 0004)

`POST /v1/generations` accepts an optional callback selector containing only
the URL. A caller cannot submit a signing secret or arbitrary callback headers:

```json
{
  "model": "provider.model",
  "mode": "text_to_video",
  "inputs": {"prompt": "...", "assets": []},
  "callback": {
    "url": "https://platform.example.com/internal/relay-callbacks"
  }
}
```

Each authenticated tenant that requests callbacks must have its own exact trusted
route in `RELAY_CALLBACK_ROUTES_JSON`. The platform's
`RELAY_CALLBACK_PUBLIC_URL` must equal the configured `url`, and its
`RELAY_CALLBACK_SIGNING_SECRET` must equal `signing_secret`:

```json
{
  "8b2f60c2-3f90-4ec7-ae43-0df53e8fa7c5": {
    "url": "https://platform.example.com/internal/relay-callbacks",
    "signing_secret": "<customer-platform-random-secret-at-least-32-bytes>"
  },
  "3d575eb0-e28b-4b7c-a445-6c2456b29570": {
    "url": "https://tiktok.example.com/internal/relay-callbacks",
    "signing_secret": "<different-internal-tiktok-random-secret-at-least-32-bytes>"
  }
}
```

The first tenant belongs to `customer-platform`; the second belongs to
`internal-tiktok`. They must never share a callback URL or signing secret. If the
TikTok system polls Relay instead of requesting callbacks, it must omit the
callback selector and its route may be omitted from this map. If it does request
callbacks, the matching secret is configured and verified by the TikTok receiver;
it is not the platform's `RELAY_CALLBACK_SIGNING_SECRET`.

Production routes require credential-free HTTPS on port 443. Query strings and
fragments are forbidden. Relay compares the requested URL to the trusted route
exactly, resolves the trusted hostname before every attempt, rejects any
non-public address, pins the approved DNS answers for the connection, and never
follows redirects. Development may use an HTTP loopback endpoint for local
integration only.

Transitions to `reconciliation_required`, `processing`, `succeeded`, `failed`, or `cancelled` insert a
stable event into `callback_deliveries` in the same database transaction as the
job update. Terminal event IDs are stable for `(job_id, status)`. Processing
event IDs are stable for `(job_id, status, progress)`, so a genuine progress
increase is delivered while duplicate or lower-progress provider updates are
not. Retries and worker reclaims therefore use at-least-once delivery without
creating a second logical event.
The callback worker sends this canonical compact JSON body (keys are sorted for
signing; actual IDs and timestamps vary):

```json
{
  "event_id": "e71ff966-c45b-5fab-b802-b422797f0d3b",
  "job": {
    "client_reference_id": "scene-001",
    "error": null,
    "id": "e00f2b0e-0f96-44ce-998f-20b303c72aa3",
    "outputs": [],
    "progress": 1,
    "status": "processing"
  },
  "occurred_at": "2026-08-03T08:00:00Z",
  "type": "generation.status_changed"
}
```

Prompts, input assets, request metadata, provider routing fields, callback URL,
and credentials are deliberately excluded. Each POST contains:

```text
Content-Type: application/json
X-Relay-Event-ID: <event_id from the body>
X-Relay-Timestamp: <Unix seconds generated for this attempt>
X-Relay-Signature: v1=<lowercase hex HMAC-SHA256>
X-Request-ID: <original safe Relay request id or stable callback fallback>
```

The exact signature input is the raw byte concatenation:

```text
UTF8(timestamp + "." + event_id + ".") || raw_request_body
```

The receiver must verify the HMAC in constant time against the raw body, reject
stale timestamps according to its replay window, and insert `event_id` into a
unique receipt table before applying state changes. A duplicate event should
return any 2xx response.

Migration `0004_callback_delivery` is included in the current head. Upgrade to
the latest head, then run the independent dispatcher:

```bash
alembic upgrade head
alembic check
python -m relay_service.callback_worker
```

Retry controls are `RELAY_CALLBACK_MAX_ATTEMPTS` (default `8`),
`RELAY_CALLBACK_BASE_DELAY_SECONDS` (default `5`),
`RELAY_CALLBACK_MAX_DELAY_SECONDS` (default `3600`),
`RELAY_CALLBACK_TIMEOUT_SECONDS` (default `10`), and
`RELAY_CALLBACK_POLL_SECONDS` (default `0.5`). Failures use capped exponential
backoff. Exhausted events enter `dead_letter` and are not retried automatically.
Authenticated clients can inspect their own delivery metadata at
`GET /v1/operations/callback-deliveries`; the response omits destinations,
payloads, and secrets.

This implementation and its contract tests do not prove reachability, TLS,
firewall rules, secret injection, or receiver behavior in a real deployment.
Those items still require staging verification with the customer platform.

### Submission claim renewal and adapter idempotency

While `ProviderAdapter.submit` is pending, the worker renews its claim with a
token-CAS update about once per one-third of the configured lease. If renewal
fails or the token no longer matches, that worker must not persist job state,
acknowledge the delivery, or actively enqueue a retry.

This heartbeat is not an upstream idempotency guarantee. A cancelled or timed
out HTTP request may already have committed at the provider. Every production
adapter must pass the exact stable `job.id` as the provider idempotency token,
or establish an equivalent durable submission fence before the request.

> 工厂加载能力不代表真实渠道已经接入。当前仓库没有经过生产验证的 Provider，真实 IdP、生产华为云 OBS 和支付也未完成，因此不得承载公网商用流量。
