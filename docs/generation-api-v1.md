# 统一生成 API v1 契约

状态：v1 冻结候选；机器契约、主动回调和双平台工程闭环已实现，双方签字与真实公网验收仍是上线门禁  
基础路径：`/v1`

以下三份机器文件是本页的规范性组成部分，示例与正文发生冲突时以机器契约和更严格的
安全规则为准：

- [`contracts/relay-generation-v1.openapi.yaml`](../contracts/relay-generation-v1.openapi.yaml)：上层调用接口；文件内容是 JSON 兼容的 OpenAPI 3.1。
- [`contracts/callback-event-v1.schema.json`](../contracts/callback-event-v1.schema.json)：Relay 主动回调消息体。
- [`contracts/error-codes-v1.json`](../contracts/error-codes-v1.json)：公开错误码、重试动作和预占处理。

联合冻结记录使用 [`generation-api-v1-freeze-checklist.md`](generation-api-v1-freeze-checklist.md)。
在负责人、环境、限流和回调/轮询选择未签字前，“冻结候选”不能对外表述为已经联调完成。

## 1. 通用约定

生成任务与产物下载接口使用服务间凭证：

```http
X-Client-ID: <service-client-id>
X-API-Key: <service-api-key>
X-Request-ID: <caller-generated-id>
Content-Type: application/json
```

创建任务还必须携带长度为 8 至 128 个字符的稳定幂等键：

```http
Idempotency-Key: <stable-key-for-one-logical-submission>
```

- `tenant_id` 由 Relay 根据已认证的服务客户端确定，不能由请求体声明。
- 同一租户以相同幂等键和相同请求重试时返回原任务；相同键对应不同请求时返回 `409`。
- API 密钥不得写入日志或返回值；生产凭证应由密钥服务注入。
- 约定客户平台使用 `customer-platform`，内部 TikTok 系统使用 `internal-tiktok`；两者必须绑定不同的 tenant UUID 与 API key。
- Relay 接受调用方提供的 `X-Request-ID`；未提供时自动生成，并始终在响应头 `X-Request-ID` 返回。
- 模型目录与生成接口都要求服务凭证；只有健康检查不要求凭证。浏览器不得直接调用 Relay。
- 所有调用方可见的 v1 JSON 资源都明确返回 `api_version="v1"` 和
  `schema_version=1`。结构按机器契约 `additionalProperties=false` 关闭：调用方不得发送
  未声明字段，也不得静默接受响应或回调中的未知字段。
- 普通 `internal-tiktok` 凭证只获得生成、查询模型、查询任务和签发产物下载的业务权限。
  人工提交对账必须使用另一个带 `operations:submission-reconciliation` scope 的受控运维凭证。

## 2. 提交生成任务

`POST /v1/generations`

`expected_capability_revision` 是必填字段。调用方必须先读取 `/v1/models`，选择明确的
`model + mode` 并固定该模型返回的 `capability_revision`；缺失 revision 的提交以
`422 REQUEST_VALIDATION_FAILED` 拒绝，不能进入队列。

```json
{
  "mode": "image_to_video",
  "model": "mock.video.v1",
  "expected_capability_revision": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "inputs": {
    "prompt": "突出产品防水便携的特点",
    "assets": [
      {
        "url": "https://signed-input.example/object",
        "media_type": "image"
      }
    ]
  },
  "output": {
    "duration_seconds": 5,
    "aspect_ratio": "16:9",
    "resolution": "1080p",
    "count": 1,
    "face_enabled": true
  },
  "client_reference_id": "job_01J...",
  "metadata": {
    "trace_id": "req_01J..."
  },
  "callback": {
    "url": "https://platform.example.com/internal/relay-callbacks"
  }
}
```

`callback` 可选。Relay 不接受任意回调地址：请求中的 URL 必须与当前认证租户在
`RELAY_CALLBACK_ROUTES_JSON` 中配置的地址完全一致。客户平台由服务端从
`RELAY_CALLBACK_PUBLIC_URL` 注入该字段，浏览器不能自行指定。生产地址必须是
公网 HTTPS 443、不得含凭证、查询参数或片段。`customer-platform` 使用平台的
`/internal/relay-callbacks` 路由和平台专属签名密钥；若 `internal-tiktok` 需要主动
回调，必须使用自己的 HTTPS 路由和另一把签名密钥。TikTok 若只轮询状态，则不得提交
`callback`，也无需配置 TikTok 回调路由。

响应使用 `202 Accepted`：

```json
{
  "api_version": "v1",
  "schema_version": 1,
  "object": "generation",
  "id": "33333333-3333-4333-8333-333333333333",
  "job_id": "33333333-3333-4333-8333-333333333333",
  "status": "queued",
  "idempotent_replay": false,
  "expected_capability_revision": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "capability_revision": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "reservation_action": "hold",
  "created_at": "2026-07-31T12:00:00Z"
}
```

`202` 只表示 Relay 已持久接受任务，不表示供应商已接单或生成成功。异步执行失败写入任务状态，不会把提交响应改成同步失败。
`id` 与 `job_id` 必须相同；两个 revision 必须与本次固定的 revision 相同。
幂等重放的 `202` 可能反映原任务已经到达终态，例如返回 `status=succeeded` 和
`reservation_action=settle`。Accepted 只反映当前状态，不能单独作为结算证据；调用方
必须再通过完整 `GET` 资源或可信验签回调确认全部转存产物后，才可执行一次幂等结算。

## 3. 查询任务

`GET /v1/generations/{job_id}`

```json
{
  "api_version": "v1",
  "schema_version": 1,
  "object": "generation",
  "id": "33333333-3333-4333-8333-333333333333",
  "client_reference_id": "job_01J...",
  "model": "mock.video.v1",
  "mode": "image_to_video",
  "inputs": {
    "prompt": "突出产品防水便携的特点",
    "assets": [
      {
        "url": "https://signed-input.example/object",
        "media_type": "image"
      }
    ]
  },
  "output": {
    "duration_seconds": 5,
    "aspect_ratio": "16:9",
    "resolution": "1080p",
    "count": 1,
    "face_enabled": true
  },
  "metadata": {
    "trace_id": "req_01J..."
  },
  "status": "succeeded",
  "progress": 100,
  "expected_capability_revision": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "capability_revision": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "reservation_action": "settle",
  "outputs": [
    {
      "asset_id": "44444444-4444-4444-8444-444444444444",
      "object_key": "outputs/<tenant>/<job>/<asset>",
      "media_type": "video",
      "content_type": "video/mp4",
      "size_bytes": 12345678,
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ],
  "error": null,
  "created_at": "2026-07-31T12:00:00Z",
  "updated_at": "2026-07-31T12:01:15Z"
}
```

公开任务响应不会返回 `tenant_id`、`provider`、`provider_task_id` 或 `transfer_sources`。它也不会返回供应商临时 URL 或 OBS 永久公开 URL。

终态结构是契约不变量：

- `succeeded` 必须同时满足 `progress=100`、`error=null`、`outputs` 非空且已经全部转存；
  `outputs` 数量必须等于请求快照的 `output.count`。
- `failed` 必须有规范化 `error`，且 `outputs=[]`；`cancelled` 也必须 `outputs=[]`。
- 所有非成功状态都不得暴露部分产物。`transferring` 仍是 `hold`，不能提前展示、下载或结算。
- `expected_capability_revision` 是调用方提交的固定值；`capability_revision` 是 Relay 实际
  校验的物理能力版本。两者不一致时，在调用真实渠道前失败并释放预占。

成功后，调用方通过以下接口获取默认有效期为 300 秒的短时下载地址：

`GET /v1/generations/{job_id}/artifacts/{asset_id}/download`

```json
{
  "api_version": "v1",
  "schema_version": 1,
  "url": "https://signed-download.example/...",
  "expires_seconds": 300
}
```

## 4. 状态机

当前可观察状态只有：`queued`、`submitting`、`reconciliation_required`、`processing`、`transferring`、`succeeded`、`failed`、`cancelled`。

```text
queued -> submitting -> processing -> transferring -> succeeded
   ^          |       |             |              |
   |          |       +-------------+--------------+-> failed
   |          +-> reconciliation_required -> processing / failed
   +---- 可重试提交失败

processing -> cancelled   （供应商取消回调）
```

- `submitting` 表示 Worker 已取得带租约的提交声明，正在执行供应商提交。
- `reconciliation_required` 表示供应商 POST 可能已创建收费任务，但 Relay 尚不能证明结果。该状态不会自动重提，也不会伪装成失败；Relay 会发送该状态的签名回调，客户平台必须继续保留余额预占。
- 供应商成功回调先把任务推进到 `transferring`；全部产物安全转存后才进入 `succeeded`。
- 可重试的供应商提交错误在重试额度内回到 `queued`；额度耗尽后进入 `failed`。
- 当前没有调用方取消接口，也没有 `accepted`、`cancelling` 或 `expired` 状态。
- 终态只有 `succeeded`、`failed`、`cancelled`。
- `progress` 只用于界面提示；成功必须以 `status=succeeded` 且产物已经转存为准，不能用进度驱动结算。

### 4.1 状态与预占动作

每个 Accepted、完整任务和回调任务对象都返回 `reservation_action`。它必须严格由状态
决定，调用方不得根据 `progress`、HTTP `retryable` 或某个渠道错误码自行推断：

| 任务状态 | `reservation_action` | 上层动作 |
| --- | --- | --- |
| `queued` | `hold` | 保留预占，等待 Relay 调度 |
| `submitting` | `hold` | 保留预占；禁止用新幂等键重提 |
| `reconciliation_required` | `hold` | 保留预占，进入受控对账 |
| `processing` | `hold` | 保留预占，轮询或等待回调 |
| `transferring` | `hold` | 保留预占，等待全部产物安全转存 |
| `succeeded` | `settle` | 仅从完整 GET 或可信回调确认产物后幂等结算一次 |
| `failed` | `release` | 幂等释放一次，不收费 |
| `cancelled` | `release` | 幂等释放一次，不收费 |

内部 TikTok 系统不使用客户平台钱包，但仍应持久化该字段，作为业务成本确认和任务状态
对账依据。客户平台必须以任务 ID 为幂等边界执行 `settle/release`，回调和轮询先后顺序
不得造成重复流水。

### 4.2 提交结果对账与权限范围

Relay 内网运维接口：

这两个接口不属于普通生成调用方 OpenAPI。除租户隔离外，凭证必须具有
`operations:submission-reconciliation` scope；普通 `customer-platform` 派发凭证和普通
`internal-tiktok` 凭证都不得获得该 scope。客户平台或 TikTok 的业务进程只消费任务
状态，不能自行声明 `created/not_created`。运维凭证应独立轮换，并在受控网络、人工审批
和不可变审计下使用。

`GET /v1/operations/submission-reconciliations?limit=100`

按当前服务凭证的租户列出全部待对账任务，响应中的任务仍不会暴露内部 Provider 路由。
处理单个任务使用：

`POST /v1/operations/submission-reconciliations/{job_id}`

确认供应商已创建任务时提交：

```json
{
  "outcome": "created",
  "provider_task_id": "upstream-task-id"
}
```

如果任务在持久化渠道路由前发生 Worker 崩溃，还需由运维系统提供已核实的内部
`provider_route`。只有错误码为 `SUBMISSION_RECONCILIATION_REQUIRED`、并且 Relay 尚无
`provider_task_id` 的未知提交，才允许在确认供应商未创建任务后提交
`{"outcome":"not_created"}`。该结论会进入 `failed`，从而允许客户平台释放预占。

轮询阶段进入对账的任务已经持久化了 Provider route 和任务编号，不能使用
`not_created`，也不能更换任何一个标识；运维使用 `created` 和原任务编号恢复为
`processing`，再依据真实 Provider 终态结算或释放。接口按服务凭证绑定的租户隔离，
重复提交相同结论幂等，非法或冲突结论返回 `409`。

对账只在原任务、原租户、原路由范围内恢复或确认未创建，绝不代表允许切换渠道。
`not_created` 必须有供应商后台、账号、时间窗口和账单证据；任何未知结果都继续
`reservation_action=hold`。运维接口的同步 HTTP 错误不能改变现有任务预占，处理者必须
重新读取任务并以其最新 `reservation_action` 为准。

## 5. 版本化模型能力目录

新调用方使用带服务凭证的 `GET /v1/models`。响应采用主流 API 的 list/resource
外形，同时让每个生成模式拥有独立能力：

```json
{
  "api_version": "v1",
  "schema_version": 1,
  "object": "list",
  "catalog_revision": "sha256:...",
  "data": [
    {
      "api_version": "v1",
      "schema_version": 1,
      "id": "mock.video.v1",
      "object": "model",
      "capability_revision": "sha256:...",
      "capabilities": {
        "schema_version": 1,
        "modes": {
          "image_to_video": {
            "input_media_types": ["image", "video", "audio"],
            "supports_face": true,
            "required_resource_keys": [],
            "limits": {
              "max_prompt_length": 10000,
              "max_images": 9,
              "max_videos": 3,
              "max_audio": 3,
              "duration_seconds": [5, 10],
              "aspect_ratios": ["16:9", "9:16", "1:1"],
              "resolutions": ["720p", "1080p"],
              "output_counts": [1, 2, 3, 4]
            }
          }
        }
      }
    }
  ]
}
```

响应带 `ETag`，调用方可用 `If-None-Match` 获取 `304`。目录基于已配置能力，不会因
渠道短暂不健康而消失；运行健康度仍由 readiness 独立表达。同一公开模型由多个渠道
承载时，对外只发布所有可故障切换路由都能保证的安全交集，不再使用“第一个渠道胜出”。

能力响应不会暴露内部 `available_providers` 或任何渠道名称。`face_enabled=true`
只有在响应明确返回 `supports_face=true` 时可提交。客户平台可以覆盖对外名称和价格，
但不得声明 Relay 不支持的输入或参数；公司级能力覆盖也只能收紧模式、输入数量和参数集合。
通用协议允许 1 至 3600 秒、1 至 16 个产物和最多 15 个输入素材，真正可提交的值始终以
目标模型的能力响应为准。

平台审批某个 `capability_revision` 后，把它作为 `expected_capability_revision` 随任务提交。
Relay 在调用真实渠道前再次核对；发生漂移时任务以
`CAPABILITY_REVISION_MISMATCH` 失败，真实渠道不会收到请求。旧的
`GET /v1/models/capabilities` 暂留作受认证兼容接口，但会返回
`Deprecation`、`Sunset`、`Link` 和 `Warning` 响应头。由于旧扁平结构不能忠实表达同一模型
不同模式的限制，它现在逐 `model + mode` 返回一行；新集成必须使用 `/v1/models`。

## 6. 错误

Relay 明确定义的业务、认证和请求校验错误使用以下 envelope：

```json
{
  "api_version": "v1",
  "schema_version": 1,
  "error": {
    "code": "IDEMPOTENCY_KEY_REUSED",
    "message": "Idempotency-Key was already used with a different payload",
    "retryable": false,
    "request_id": "req_01J...",
    "details": {}
  }
}
```

当前 `error` 对象只有 `code`、`message`、`retryable`、`request_id`、`details`，没有 `chargeable` 字段。常见同步错误如下：

[`contracts/error-codes-v1.json`](../contracts/error-codes-v1.json) 是完整、机器可读的公开
注册表；下表只是最常见的上层接口错误摘要。同步 HTTP 错误分别声明
`create_reservation_action` 和 `existing_job_action`：创建请求已被明确拒绝时可以释放本次
创建预占，但查询、目录或下载错误永远不能改变已有任务预占。HTTP 超时、连接中断、
`500` 和幂等冲突都不是“未创建”的证据，必须保持 `hold` 并按原键对账。

| HTTP | 错误码 | 含义 |
| --- | --- | --- |
| `401` | `CLIENT_AUTHENTICATION_REQUIRED` | 缺少服务凭证 |
| `401` | `INVALID_CLIENT_CREDENTIALS` | 服务凭证无效 |
| `403` | `INSUFFICIENT_CLIENT_SCOPE` | 当前凭证无权执行受控运维操作 |
| `404` | `JOB_NOT_FOUND` | 当前租户不可见该任务；跨租户查询也返回此错误 |
| `404` | `ARTIFACT_NOT_FOUND` | 任务未成功或产物不存在 |
| `409` | `IDEMPOTENCY_KEY_REUSED` | 相同幂等键被用于不同请求 |
| `422` | `REQUEST_VALIDATION_FAILED` | 请求、路径或请求头校验失败；响应不会回显敏感输入 |
| `404` | `ROUTE_NOT_FOUND` | API 路由不存在 |
| `405` | `METHOD_NOT_ALLOWED` | 路由不接受当前 HTTP 方法 |

异步执行错误出现在任务的 `error` 字段中，结构为 `code`、`message`、`retryable`、
`details`。完整枚举及每项的 `stage`、`retry`、`caller_action` 和
`reservation_action` 只维护在错误注册表中；文档或调用方代码不得复制一个更小的固定
列表。`retryable=true` 表示同一个已接受任务仍可由 Relay 或调用方继续观察，不代表允许
换一个幂等键创建新任务。

Relay 不直接修改客户钱包，但它通过签名、版本化的 `reservation_action` 给出唯一状态
动作。客户平台只能在完整成功任务及全部安全转存产物验证通过后结算；`failed` 或
`cancelled` 必须释放且不得收费。`reconciliation_required` 既不得结算，也不得释放。
任何渠道专属错误码和诊断信息只保留在 Relay 私有字段或受控日志；公开任务、回调和
错误响应只返回统一分类，不得暴露 Kling、Wan、Ark 等底层渠道身份。

## 7. 调用方状态同步

当前主链路是 Relay 的持久化主动回调。客户平台同时保留独立状态同步进程轮询
`GET /v1/generations/{job_id}`，仅用于回调延迟、死信或网络故障时的兜底对账；
轮询与回调最终进入同一套幂等状态和钱包结算逻辑。

Relay 为 `reconciliation_required`、`processing`、`succeeded`、`failed`、`cancelled`
状态创建持久化回调事件。
`processing` 按进度值生成稳定事件编号，终态按任务和状态生成稳定事件编号。
请求发送到调用方在生成请求中声明、且经租户策略精确授权的 `callback.url`。

### 7.1 请求头和签名

```http
Content-Type: application/json
X-Relay-Event-ID: 55555555-5555-4555-8555-555555555555
X-Relay-Timestamp: 1785326400
X-Request-ID: req_01J...
X-Relay-Signature: v1=<hex-hmac-sha256>
```

签名使用租户独立密钥和 HMAC-SHA256。签名原文按以下字节顺序拼接；
`raw-request-body` 是网络发送的原始 JSON 字节，接收方不得先解析或重新序列化：

```text
<timestamp>.<event-id>.<raw-request-body>
```

`X-Relay-Timestamp` 是十进制 Unix 秒。Relay 租户路由和客户平台必须配置同一把
至少 32 UTF-8 字节的签名密钥；生产环境拒绝已知占位密钥。

回调请求体如下：

消息体必须通过
[`contracts/callback-event-v1.schema.json`](../contracts/callback-event-v1.schema.json)
精确校验，未知字段、重复 JSON key 或版本不符都要拒绝：

```json
{
  "api_version": "v1",
  "schema_version": 1,
  "event_id": "55555555-5555-4555-8555-555555555555",
  "type": "generation.status_changed",
  "occurred_at": "2026-08-03T09:30:00Z",
  "job": {
    "api_version": "v1",
    "id": "33333333-3333-4333-8333-333333333333",
    "client_reference_id": "job_01J...",
    "status": "succeeded",
    "progress": 100,
    "expected_capability_revision": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "capability_revision": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "reservation_action": "settle",
    "outputs": [
      {
        "asset_id": "44444444-4444-4444-8444-444444444444",
        "object_key": "outputs/<tenant>/<job>/<asset>",
        "media_type": "video",
        "content_type": "video/mp4",
        "size_bytes": 12345678,
        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
      }
    ],
    "error": null
  }
}
```

回调中的 `outputs` 只包含已转存产物标识和校验元数据，不携带可长期复用的 URL。
接收方在处理 `succeeded` 后，用自己的服务凭证调用产物下载签发接口；短时 URL 过期时
重新签发。这样不会把供应商临时 URL 或长期公开 OBS 地址固化进消息队列和日志。

`X-Request-ID` 优先沿用首次提交任务时的安全请求编号；没有来源编号时使用稳定的
`relay-callback-<event-id>`。客户平台接收端为
`POST /internal/relay-callbacks`，该端点不使用普通内部服务令牌，而是验证以上
HMAC、时间戳和事件编号。默认重放窗口为 300 秒，可通过
`RELAY_CALLBACK_MAX_AGE_SECONDS` 调整。接收端还会拒绝重复 JSON 键、请求头与
消息体事件编号不一致、无时区的 `occurred_at`，以及无法与
`client_reference_id` 和 Relay 任务 ID 同时匹配的平台任务。

### 7.2 幂等、重试和死信

- Relay 在数据库中持久化回调 delivery；进程崩溃后可以重新领取，不依赖进程内存。
- 任意 `2xx` 表示投递成功。网络异常、超时和非 `2xx` 响应按指数退避重试；默认从 5 秒开始、最长 3600 秒、最多 8 次，均可用 Relay 环境变量调整。
- 达到最大尝试次数后 delivery 进入 `dead_letter`，不会被当作成功。租户可通过带服务凭证的 `GET /v1/operations/callback-deliveries` 按 `pending`、`delivering`、`delivered`、`dead_letter` 状态查看投递记录；该视图不暴露回调 URL、签名密钥或完整消息体。
- 客户平台以 `event_id` 为主键保存不可变回执和消息体 SHA-256。同一事件编号和相同消息体重复到达时返回 `204`，并设置 `X-Relay-Callback-Duplicate: true`，不会重复改变任务或扣款；同一编号对应不同消息体时返回 `409`。
- 回调推进状态、成功结算和失败释放都复用既有幂等服务。轮询兜底晚到或先到时，也不得造成重复结算。

### 7.3 生产门禁

代码和本地契约测试已闭环，不等于生产公网已经验证。上线前必须在 staging 使用
真实域名和 TLS 完成 Relay 出网到客户平台入口的端到端演练，包括 DNS 解析与
公网地址固定、无重定向、反向代理保留原始请求体、双方密钥一致、主机时钟同步、
超时重试、重复事件、死信告警和轮询补偿。Relay 生产传输会拒绝私网、回环或
非公网 DNS 结果并固定本次解析地址；上述真实外网演练未通过前仍是发布阻断项。

## 8. 调用超时、重试与轮询约定

### 8.1 HTTP 边界

推荐调用方使用 3 秒连接超时和 15 秒响应超时；下载产物可使用 60 秒读取超时并流式
校验。超时是调用方停止等待的边界，不是 Relay 任务超时，也不能推导任务失败。

| 操作 | 可以自动重试的情况 | 必须保持不变 | 禁止动作 |
| --- | --- | --- | --- |
| `GET /v1/models` | 连接/超时、`429`、`5xx` | `If-None-Match` 与认证租户 | 用过期能力提交 |
| `POST /v1/generations` | 连接/超时、响应不可解析、`429`、`5xx` | 完整请求、`Idempotency-Key`、固定 revision | 生成新 key、修改 body 后复用旧 key |
| `GET /v1/generations/{id}` | 连接/超时、`429`、`5xx` | 原 job ID 和租户 | 把本地超时写成失败或释放预占 |
| 下载签发 | 连接/超时、`429`、`5xx` | job/asset ID | 缓存 URL 当作永久地址 |
| 短时产物 URL | 传输中断；URL 过期时重新签发 | 下载后的大小与 SHA-256 校验 | 在校验前标记下载完成 |

POST 请求应只序列化一次，并在一次逻辑提交的所有网络重试中复用相同字节和稳定幂等键。
收到 `409 IDEMPOTENCY_KEY_REUSED` 时禁止自动改 key；这说明调用方把同一个 key 用到了
不同请求，必须人工定位原任务。Relay 当前不自动清理幂等记录；任何归档或保留期变更
必须先形成新的双方数据保留契约。

### 8.2 退避和本地等待期限

- `429` 优先服从整数秒 `Retry-After`；其他临时错误使用带 0–20% 抖动的指数退避，建议
  `1s, 2s, 4s, 8s ...`，单次最多 30 秒。
- 任务轮询建议从 2 秒开始，逐步退避到 30 秒；回调模式也必须保留低频轮询作为死信补偿。
- 调用方可以为同步页面设置本地等待期限，但到期只能退出页面等待并转入后台同步，不能
  把 Relay 任务改成 `failed`。只要最新可信资源仍返回 `hold`，预占就继续保留。
- Relay 内部的供应商提交、轮询、转存和回调重试由持久队列管理；上层不得根据
  `retryable=true` 并行创建替代任务。

### 8.3 回调重试

Relay 对非 `2xx`、连接失败或超时使用持久化指数退避，默认 5 秒起步、最长 3600 秒、
最多 8 次，然后进入 `dead_letter`。每次投递重新生成当前时间戳和签名，但复用同一个
`event_id` 和相同消息体。接收方必须在一个事务中保存 `event_id + body_sha256`、推进
业务任务并执行预占动作；重复同体返回 `2xx`，同 ID 异体返回 `409`。

## 9. 兼容、冻结和演进

- v1 借鉴 OpenAI 视频接口的异步资源语义：创建返回资源 ID，调用方轮询状态或接收回调，
  成功后再访问产物；但本协议同时支持图像、多素材和多产物，因此不是 OpenAI SDK 的
  wire/drop-in 兼容层，不能声称把现有 SDK 地址改一下即可调用。
- 本次冻结是 `api_version=v1`、`schema_version=1` 的精确结构。请求、响应和回调都
  `additionalProperties=false`；调用方必须拒绝未知字段，不能“忽略后继续”。
- 新增任何响应或回调字段，必须先发布新的 `schema_version`、机器契约、生产者/消费者
  契约测试和双方兼容窗口。旧 schema 在窗口结束前保持可选协商，不能静默改变。
- 删除公开字段、改变字段含义、改变状态/预占语义或收紧已有枚举，需要发布新的
  `api_version` 主版本和迁移方案。
- `GenerationJob`、内部 `ModelCapability`、供应商回调模型和持久化字段均是 Relay 内部实现，不属于公开契约。
- 新供应商、路由优先级、供应商任务 ID、候选渠道和产物源地址不得通过公开响应泄露。
- `capability_revision` 是能力文档内容摘要；改变回调事件结构或签名版本仍需作为明确的增量能力发布并配套契约测试，当前签名版本为 `v1`。
- 原始供应商响应只能保存到受控诊断记录，不能原样透传给外部调用方。

## 10. 内部 TikTok 独立接入

参考实现：

- [`examples/internal-tiktok/polling.py`](../examples/internal-tiktok/polling.py)：读取带 ETag
  的能力目录、固定 revision、用稳定幂等键安全提交、退避轮询、重新签发下载地址并校验
  大小和 SHA-256。
- [`examples/internal-tiktok/callback_receiver.py`](../examples/internal-tiktok/callback_receiver.py)：
  校验原始字节 HMAC、时间窗、事件编号、严格 schema 和状态动作，并用 SQLite 演示跨
  重启的事件幂等。生产实现必须把回执、TikTok 任务更新和成本/预占动作放进同一事务。

示例只从环境变量读取 API key 和回调签名密钥，不包含生产凭据。`internal-tiktok` 使用
独立 tenant、API key、限流/容量策略和可选回调密钥，不经过客户平台，也不共享客户平台
钱包。若选择纯轮询，请求中省略 `callback`；若选择主动回调，URL 必须与 Relay 中该
TikTok tenant 的 HTTPS 白名单完全一致。

轮询示例的必填环境变量是 `RELAY_BASE_URL`、`INTERNAL_TIKTOK_RELAY_API_KEY`、
`TIKTOK_CLIENT_REFERENCE_ID` 和 `TIKTOK_IDEMPOTENCY_KEY`。可选项包括
`INTERNAL_TIKTOK_RELAY_CLIENT_ID`、`TIKTOK_MODEL_ID`、`TIKTOK_GENERATION_MODE`、
`TIKTOK_GENERATION_PROMPT`、需要图/视频输入时的 `TIKTOK_INPUT_ASSET_URL`、
`INTERNAL_TIKTOK_RELAY_CALLBACK_URL`、`TIKTOK_POLL_DEADLINE_SECONDS` 和
`TIKTOK_OUTPUT_DIRECTORY`。只有本机开发可显式设置
`TIKTOK_ALLOW_INSECURE_LOCALHOST=1`。

回调示例必须设置 `INTERNAL_TIKTOK_RELAY_CALLBACK_SECRET`；监听地址、端口、重放窗口和
SQLite 回执位置分别通过 `INTERNAL_TIKTOK_CALLBACK_BIND`、
`INTERNAL_TIKTOK_CALLBACK_PORT`、`INTERNAL_TIKTOK_CALLBACK_MAX_AGE_SECONDS` 和
`INTERNAL_TIKTOK_CALLBACK_DB_PATH` 配置。示例监听 HTTP 只为放在同机 HTTPS 反向代理
之后，不能直接暴露公网。
