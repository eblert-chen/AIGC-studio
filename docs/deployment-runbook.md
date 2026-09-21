# 双平台部署与上线运行手册

本手册把当前可执行的本地联调流程与未来生产流程分开。`docker-compose.yml` 是本地持久化联调环境，不是生产部署文件。

## 1. 部署拓扑

```text
浏览器
  -> Web 静态站点
  -> HTTPS 网关
  -> 客户平台 API
       -> 平台 PostgreSQL
       -> 私有输入素材（生产：Huawei OBS）
       -> 派发进程 / 状态同步进程（轮询兜底）/ 超时补偿进程
       -> 扩展版 new-api API（唯一活动生成数据面）
            -> 中转站 PostgreSQL
            -> Redis Stream
            -> Outbox / 生成 Worker / Provider 状态同步 Worker / Provider 监控 Worker /
               产物转存 Worker / 回调 Worker
            -> HMAC 主动回调 -> 客户平台回调接收端
            -> HMAC Provider 告警 -> 外部值班/告警接收端
            -> 真实供应商
            -> 私有华为云 OBS
```

公网只暴露 Web 和客户平台网关。中转站、数据库、Redis、Worker 和 OBS 永久凭证必须留在受控网络内。

## 2. 本地持久化联调

前置条件：

- Docker Engine 与 Docker Compose；
- 端口 `8300`（new-api）、`8400`（Download Edge）、`8200`（Platform）和 `8180`（Gateway）可用，或在 `.env` 中覆盖；
- 至少 4 GB 可用内存。

首次启动：

```powershell
Copy-Item .env.example .env
# 编辑 .env：除数据库、Relay 与内部服务凭据外，还必须填写 Auth0/OIDC 端点、
# Client ID、回调地址、前端 Origin 和 PLATFORM_OWNER_USER_IDS_JSON；示例值默认不可登录。
npm run services:start:local
```

`services:start:local` 是 Windows 本地长期开发栈的唯一一键启动入口。它固定按
`.env -> huawei-obs.runtime.env -> paid-canary.runtime.env -> platform-canary.runtime.env`
加载配置，先验证 Auth0/OIDC 必需字段和 Compose 配置，再启动 Docker 服务、重启网关并
确认 `login_available=true`。如果 5173 尚未监听，它还会以隐藏窗口启动 Vite；如果该端口
已被其他服务占用但 `/platform` 不可用，脚本会失败关闭而不会停止未知进程。

存在成功的 Relay `role-post` 初始化凭证时，脚本走幂等的 warm-start 路径，只启动数据库和
长驻服务，不会重新执行会与运行中 Relay 生命周期锁冲突的数据库角色/迁移 one-shot；只有首次
启动才执行完整依赖启动图。若 Relay/下载边缘仍在运行却缺少成功凭证，脚本会失败关闭而不是
冒险并发迁移。Platform API 在 warm-start 中会明确重建，确保当前 `.env` 真正进入容器。

脚本不会执行 `down`、删除 volume、清理 orphan 或 prune。海外生产不得使用本地 `.env`，
必须继续使用独立的受控生产秘密文件与生产部署流程。

网关通过 Docker 内置 DNS 持续解析 `platform-api`，后端容器被重建后无需人工重启
Nginx。若健康检查未恢复，应先查看网关与平台日志，不能用反复重启掩盖故障。

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8300/api/status
Invoke-RestMethod http://127.0.0.1:8400/health/ready
Invoke-RestMethod http://127.0.0.1:8200/health/ready
Invoke-RestMethod http://127.0.0.1:8180/health/ready
```

本地 new-api 中转站使用 PostgreSQL/Redis、共享卷产物仓库和 Mock route。共享卷只用于
本机验收，生产仍强制使用华为云 OBS。本地默认未配置真实 Provider 与外部告警 sink，
所以本地健康与合同通过不能证明生产路由、跨渠道切换或值班通知已验收。

完整本地冒烟：

```powershell
$smokeId = "run-" + (Get-Date -Format "yyyyMMddHHmmss")
$smokeProject = "ai-video-smoke-$smokeId"
$smokeManifest = Join-Path ([System.IO.Path]::GetTempPath()) "$smokeProject.json"
$gatewayBase = "http://127.0.0.1:18180"
$platformBase = "http://127.0.0.1:18200"
$relayBase = "http://127.0.0.1:18300"

node .\scripts\smoke-disposable-environment.mjs issue `
  --environment-id $smokeId `
  --gateway-base $gatewayBase `
  --platform-base $platformBase `
  --relay-base $relayBase |
  Set-Content -LiteralPath $smokeManifest -Encoding utf8

$env:GATEWAY_HOST_PORT = ([Uri]$gatewayBase).Port
$env:PLATFORM_HOST_PORT = ([Uri]$platformBase).Port
$env:NEW_API_RELAY_HOST_PORT = ([Uri]$relayBase).Port

try {
  docker compose --project-name $smokeProject up --build --detach --wait
  if ($LASTEXITCODE -ne 0) { throw "Disposable smoke Compose startup failed" }

  .\scripts\smoke-local.ps1 `
    -GatewayBase $gatewayBase `
    -PlatformBase $platformBase `
    -RelayBase $relayBase `
    -DisposableEnvironmentManifest $smokeManifest
}
finally {
  docker compose --project-name $smokeProject down --volumes --remove-orphans
  $cleanupExitCode = $LASTEXITCODE
  $remainingContainers = @(docker container ls --all --quiet --filter "label=com.docker.compose.project=$smokeProject")
  $containerCheckExitCode = $LASTEXITCODE
  $remainingVolumes = @(docker volume ls --quiet --filter "label=com.docker.compose.project=$smokeProject")
  $volumeCheckExitCode = $LASTEXITCODE
  $remainingNetworks = @(docker network ls --quiet --filter "label=com.docker.compose.project=$smokeProject")
  $networkCheckExitCode = $LASTEXITCODE
  Remove-Item -LiteralPath $smokeManifest -Force -ErrorAction SilentlyContinue
  Remove-Item Env:GATEWAY_HOST_PORT, Env:PLATFORM_HOST_PORT, Env:NEW_API_RELAY_HOST_PORT -ErrorAction SilentlyContinue

  if ($cleanupExitCode -ne 0 -or $containerCheckExitCode -ne 0 -or
      $volumeCheckExitCode -ne 0 -or $networkCheckExitCode -ne 0 -or
      $remainingContainers.Count -ne 0 -or
      $remainingVolumes.Count -ne 0 -or $remainingNetworks.Count -ne 0) {
    throw "Disposable smoke project cleanup is incomplete: $smokeProject"
  }
}
```

脚本会自动读取仓库根目录 `.env` 中的 `RELAY_CLIENT_ID` 与
`RELAY_API_KEY`，也可用 `-RelayClientId`、`-RelayApiKey` 显式覆盖。
本地 Compose 为 `customer-platform` 和 `internal-tiktok` 分别注册生成凭证和
最小权限对账凭证；两个业务调用方使用不同 tenant，冒烟脚本使用
`customer-platform` 生成凭证。本地没有 TikTok 回调接收端，
因此 `internal-tiktok` 联调应省略 callback 并轮询，或先显式增加独立回调路由和密钥。

该脚本创建隔离的测试公司和测试余额，跑通派发、Mock Provider 回调、Relay 主动回调、外部测试图片转存、任务历史、作品索引、短时下载签发、完整字节传输、可信完成事件和结算。
它不会调用真实支付或真实生成渠道，但需要能够访问脚本参数中的 HTTPS 测试图片。
这些数据是持久化合同证据，不能逐表猜测删除；隔离边界必须是清单中精确绑定的
`ai-video-smoke-<environment_id>` Compose project。脚本默认拒绝无清单、过期清单、非回环地址、
地址不匹配、保留卷的清理策略，以及长期开发栈默认的 `8180/8200/8300` 端口，并在任何 HTTP
请求前失败关闭。`-PreflightOnly` 只验证清单和目标，不发送网络请求。

无论冒烟通过还是失败，上面的 `finally` 都会对该精确 project 执行
`docker compose --project-name $smokeProject down --volumes --remove-orphans`，然后分别以
`com.docker.compose.project=$smokeProject` 标签查询 container、volume 和 network；三者均为空才算
清理完成，任何残留都会使命令失败。不得把 `down --volumes` 指向默认或其他长期 project。冒烟 JSON 只报告
`cleanup_required=true`、精确 project 和清理模式，不会把“测试通过”误报成“清理完成”。

停止服务：

```powershell
docker compose down
```

长期开发 project 的保留卷可保留数据库与队列，且不得运行这个写入型冒烟。只有上面由清单绑定的
短时 smoke project 才必须删除自身卷；该操作不可恢复，所以 project 名和标签核验不可省略。

### 唯一 new-api Relay 本地栈

普通 `docker compose up` 启动 new-api；本地 Mock 只验证合同和队列，不构成真实 Provider
证据。根 Compose 不再定义 Python Relay service/profile/volume，也不能让 Platform 默认回落
到 `relay-api:8000`。需要单独检查 Relay 时：

```powershell
docker compose config --quiet
docker compose build relay-new-api
docker compose up -d relay-new-api
Invoke-RestMethod http://127.0.0.1:8300/api/status
```

首次启动成功只说明控制面和持久化依赖可用，不代表 `/v1/generations`、真实渠道、OBS、
回调、监控或成本已验收。Platform 的活动 backend 固定为
`new-api-v1 / generations.v1`；切换前排空、任务亲和、稳态发布与 previous-new-api 回滚合同见
[new-api Relay 生产切换与回滚合同](relay-new-api-migration.md)。

## 3. 前端模式

- 未提供 `window.__AI_VIDEO_RUNTIME_CONFIG__`、受控公司上下文或当前用户会话 token：明确进入演示模式。
- 部署层只通过 `window.__AI_VIDEO_RUNTIME_CONFIG__` 注入非秘密 `platformApiUrl`；生产 OIDC/BFF 会话建立后进入真实 API 模式。`companyId` 来自服务端 surface 列表和用户显式选择。未注入 `platformApiUrl` 时浏览器固定调用页面同源地址；不得从 `sessionStorage`、URL 参数或其他用户可写来源覆盖 API Origin，生产也不会从这些位置读取 Bearer token。
- 禁止再使用 `VITE_PLATFORM_API_URL`、`VITE_COMPANY_ID`、`VITE_USER_ID` 或其他静态构建变量固化生产身份；真实 API 模式在外部 IdP 完成前仍只能用于受控环境。
构建与测试：

```powershell
npm test
npm run build
```

## 4. 生产资源清单

生产部署前必须由运维或云平台准备：

- 独立生产域名与有效 TLS 证书；
- 客户平台与中转站使用的 PostgreSQL 数据库；
- Redis 高可用实例，开启认证和持久化；
- 私有华为云 OBS 桶、最小权限 IAM 用户、生命周期与跨域策略；当前 Relay 的孤儿对象
  清理要求桶版本控制为“未启用”，`Enabled`、`Suspended`、无法读取或未知状态都会使生产
  启动/readiness fail closed；
- 独立的 Relay 客户端凭证和内部服务令牌；
- 正式 JWT/SSO 或会话密钥；
- 真实供应商账号、测试额度、回调密钥、限流和成本配置；
- 独立的 Provider 告警 HTTPS 接收端、轮换签名密钥和实际值班系统路由；
- 集中日志、指标、告警、密钥管理、数据库和 OBS 备份。

## 5. 生产强制配置

客户平台：

```text
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://...
AUTO_CREATE_TABLES=false
ENABLE_BOOTSTRAP=false
CORS_ORIGINS=["https://正式前端域名"]
RELAY_DEFAULT_BACKEND_ID=new-api-v1
RELAY_DEFAULT_CONTRACT_REVISION=generations.v1
# backend URL/client/API key 仅进入 platform-api、dispatcher、relay-sync、timeout-worker
# 的最小文件型 secret bundle；protected runtime 禁止 raw env、多个 backend 和 legacy fallback。
# 运维地址必须与唯一 new-api data backend 使用同一 canonical origin。
RELAY_OPERATIONS_BASE_URL=https://relay-new-api.internal.example
RELAY_TENANT_ID=<customer-platform tenant UUID>
RELAY_OPERATIONS_TOKEN=<独立的至少 32 字节原始运维令牌>
RELAY_RECONCILIATION_APPROVAL_KEY_ID=<当前 Platform 审批签名 key id>
RELAY_RECONCILIATION_APPROVAL_SECRET=<独立于运维令牌和其他密钥的至少 32 字节 HMAC 密钥>
RELAY_DISPATCH_MAX_ATTEMPTS=12
RELAY_CALLBACK_PUBLIC_URL=https://api.example.com/internal/relay-callbacks
RELAY_CALLBACK_SIGNING_SECRET=<至少 32 字节随机密钥>
RELAY_CALLBACK_MAX_AGE_SECONDS=300
INPUT_ASSET_STORE=huawei_obs
INPUT_ASSET_PUBLIC_BASE_URL=https://api.example.com
INPUT_ASSET_RELAY_BASE_URL=https://api.example.com
INPUT_ASSET_SIGNING_SECRET=<至少 32 字节随机密钥>
INPUT_ASSET_SIGNED_URL_SECONDS=300
INPUT_ASSET_RELAY_SIGNED_URL_SECONDS=3600
TASK_QUEUED_TIMEOUT_SECONDS=3600
TASK_PROCESSING_TIMEOUT_SECONDS=21600
TASK_TIMEOUT_SCAN_INTERVAL_SECONDS=30
TASK_TIMEOUT_BATCH_SIZE=100
INTERNAL_SERVICE_TOKEN=...
CHANNEL_COST_SIGNING_SECRET=<与 Relay 成本投递端完全一致、独立且至少 32 字节>
CHANNEL_COST_SIGNATURE_REQUIRED=true
CHANNEL_COST_SIGNATURE_MAX_AGE_SECONDS=300
RELAY_TELEMETRY_SIGNING_SECRET=<与 new-api Relay 遥测投递端一致、独立且至少 32 字节>
RELAY_TELEMETRY_SIGNATURE_MAX_AGE_SECONDS=300
PROVIDER_ALERT_SIGNING_SECRET=<与 new-api Relay 告警投递端一致、独立且至少 32 字节>
PROVIDER_ALERT_SIGNATURE_MAX_AGE_SECONDS=300
PROVIDER_ALERT_FORWARD_WEBHOOK_URL=https://alerts.example.com/platform/provider
PROVIDER_ALERT_FORWARD_SIGNING_SECRET=<Platform 到值班系统的另一把独立随机密钥，至少 32 字节>
PROVIDER_ALERT_FORWARD_TIMEOUT_SECONDS=5
DOWNLOAD_COMPLETION_EDGE_GATEWAY_SIGNING_SECRET=<EDGE 专用、独立且至少 32 字节>
DOWNLOAD_COMPLETION_OBS_ACCESS_LOG_SIGNING_SECRET=<OBS 日志桥专用、独立且至少 32 字节>
DOWNLOAD_COMPLETION_SIGNATURE_MAX_AGE_SECONDS=300
```

平台到 Relay 的链路必须使用 HTTPS，或由经过验收的 mTLS 服务网格提供等价的
传输加密；禁止在明文 HTTP 上传输 `X-API-Key`、提示词和任务状态。
new-api 的 `RELAY_PROVIDER_ALERT_SIGNING_SECRET` 与 Platform 的
`PROVIDER_ALERT_SIGNING_SECRET` 必须相同，Compose 统一从
`NEW_API_RELAY_PROVIDER_ALERT_SIGNING_SECRET` 注入；Platform 再使用不同的
`PROVIDER_ALERT_FORWARD_SIGNING_SECRET` 把已验签并持久化的不可变告警回执转发到值班系统。
三项接收/转发配置缺少任一项时生产启动失败，入站与出站密钥不得复用。

中转站（唯一活动 new-api）：

不要从旧 Python 环境变量清单手写生产配置。以
`deploy/relay-secure.env.example`、对应 staging/production 非秘密模板和
`docs/new-api-production-deployment.md` 的逐进程文件型 secret schema 为唯一入口。受保护
运行时必须在任何数据库或配置文件读取前拒绝 raw DSN、raw API key、Python factory、Mock、
多个 generation backend、旧 callback 单 secret 和 legacy artifact fallback。

`customer-platform` 与 `internal-tiktok` 是两个固定业务 principal，必须绑定不同 tenant、
调用凭据、限额和 callback key。Platform API、dispatcher、relay-sync、timeout-worker 只读取
自己的 backend-qualified bundle；其他 Platform 进程不得持有 generation credential。TikTok
若只轮询就不注册 callback；若使用 callback，必须有独立 HTTPS 地址与密钥。所有 route、
费率、Provider credential keyring、OBS 身份和数据库 principal 都绑定同一不可变 release，
不能从 Python oracle 或宿主共享 env 回退。

Python oracle 中的适配器状态不参与生产渠道判定。唯一活动 new-api Relay 仍必须对每条
生产 route 提供绑定当前源码/镜像/能力 revision 的外部签名验收，并完成真实凭证、地区、
模型、配额、账单、unknown 对账和 OBS 转存 canary；缺失时对应 route fail closed，不能用
离线 oracle 测试代替。

生产 Relay 镜像必须包含并启用受保护 OBS 实现。所有密码和密钥通过密钥管理系统注入，
禁止写入镜像、源码或普通日志。

扩展 new-api 使用独立数据库/Redis、固定源码与镜像 digest、原生渠道 token、生产 route
能力声明和数据库 release proof。一次性历史排空与不可回接 Python 的约束见
[new-api Relay 生产切换与回滚合同](relay-new-api-migration.md)。

扩展版 new-api 还必须显式配置以下 Platform 遥测闭环；四项缺少任一项都会使生产启动
失败，签名密钥不得复用回调、成本、告警或内部服务令牌：

```text
RELAY_PLATFORM_TASK_STAGE_URL=https://api.internal.example/internal/relay/task-stages
RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL=https://api.internal.example/internal/relay/operations-snapshots
RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN=<独立内部服务令牌>
RELAY_TELEMETRY_SIGNING_SECRET=<与 Platform 一致的独立随机密钥，至少 32 字节>
```

仓库 `.env.example` 使用 `NEW_API_RELAY_PLATFORM_TASK_STAGE_URL` 和
`NEW_API_RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL` 作为 Compose 输入名；Compose 会把它们
映射为容器内的 canonical `RELAY_PLATFORM_*` 变量。生产密钥必须由密钥管理系统注入，
不得使用 Compose 的开发默认值。上线前同时检查 Platform 的遥测接收表已迁移、签名拒绝
用例通过，并从 Relay readiness 核对遥测 backlog 与最近成功投递时间。

未知提交运维凭据必须与生成 API key 分离。Platform 保存原始
`RELAY_OPERATIONS_TOKEN`，new-api 只保存其小写 SHA-256：

```text
RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON=[{"tenant_id":"<同一 tenant UUID>","token_sha256":"<原始运维令牌的小写 SHA-256>"}]
RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON=[{"tenant_id":"<同一 tenant UUID>","key_id":"<与 Platform 一致>","secret":"<与 Platform 审批签名密钥一致>"}]
```

运维 bearer token 本身不能证明人工审批。Platform 会用第二把 tenant-bound HMAC 密钥签署
job、tenant、稳定 operation ID、核实结果、route/attempt/token fencing、凭证、审批人和原因；
new-api 必须先验证该签名，再在同一行锁事务内写入不可变 reconciliation receipt。两把密钥
必须独立，生产配置必须覆盖每个 Relay tenant。轮换时先在 Relay 验签目录加入新 key，再切换
Platform signer；旧 key 至少保留到所有使用它签署的未决 operation 均可完成结果回读。

运维人员先从 Platform 的
`GET /api/v1/platform-admin/relay/submission-unknown` 发现任务，再读取详情并到供应商后台
核实。只有 `platform.relay_health.manage` 权限可以提交 resolve；请求必须回传详情中的
route、attempt 和 reconciliation token，并填写证据编号、审批原因。网络结果不明时禁止
自动创建新的 operation；使用
`GET /api/v1/platform-admin/relay/submission-unknown/{job_id}/result` 按 Platform 已持久化的
operation ID 回读 Relay receipt，确认首次提交结果。`RELAY_OPERATIONS_BASE_URL` 必须绑定唯一
new-api canonical origin；previous-new-api 镜像回滚不能切断同一数据面的对账入口。

正常运行时的渠道成本由 new-api 根据成功 Provider 终态和不可变合同费率物化。费率通过
`RELAY_PROVIDER_CONTRACT_RATES_JSON` 注入，Compose 输入名为
`NEW_API_RELAY_PROVIDER_CONTRACT_RATES_JSON`。每条记录必须包含 UUID `id`、精确
`provider_name/channel_id/upstream_model/mode/resolution`、`billing_unit`（`output_item`
或 `output_second`）、正整数 `unit_amount_cents`、固定为 `CNY` 的 `currency`、带时区的
`effective_from`、`source_reference` 和合同原件的小写 SHA-256。修改价格必须新增版本，
不得改写旧记录；缺少匹配费率时 readiness 保持成本对账不完整，绝不能推断为零成本。

Provider Monitor 和告警在生产配置中 fail-closed：Monitor 必须启用，地址和密钥必须成对
配置。生产只接受规范化公网 HTTPS 443 地址，发送端拒绝
重定向并校验/固定公网 DNS；密钥必须独立于租户任务回调密钥。接收端按原始请求体验证
`timestamp.event_id.body` 的 HMAC-SHA256，并按事件 ID 幂等。默认最多投递 8 次，指数
退避从 5 秒开始、最多 900 秒，之后写入 `dead_letter`。投递 claim 默认 60 秒，必须大于
端到端告警超时；关闭 Monitor 或缺少告警地址/密钥会使生产 Relay 启动失败。

### 下载完成事件

签名 URL 的成功签发只能证明 `issued`，不能证明浏览器完成下载。生产必须选择并部署以下至少一种可信事件源：

- Huawei OBS access log 解析器：只接受成功的完整对象 GET，解析对象键后映射到下载签发记录；
- 受控边缘下载网关：完整向客户端发送对象后，以自己的服务身份提交完成事件。

事件源位于受控网络，调用来源固定的
`POST /internal/artifact-download-completions/edge-gateway` 或
`POST /internal/artifact-download-completions/obs-access-log`。两个入口不进入公开 OpenAPI，也不由公网客户网关暴露；`INTERNAL_SERVICE_TOKEN` 只作为第二因子，不能单独证明下载。EDGE 与 OBS 分别使用独立轮换的 HMAC 密钥，通过 `X-Download-Event-ID`、`X-Download-Timestamp`、`X-Download-Signature` 对来源、规范事件 ID、时间戳和原始请求体签名。请求体无权选择 `source`，并必须同时提供
`download_record_id`、`company_id`、`task_id`、`asset_id`、稳定的 `external_event_id`、
实际 `bytes_sent`、带 UTC 偏移的 `completed_at`、不可变 `artifact_sha256`、`expected_size_bytes`、`http_status=200` 和 `transfer_scope=full_body`。EDGE 还必须绑定真实 gateway request/transfer reference；OBS 必须绑定 bucket、object key 以及 version ID 或 OBS request ID。平台会核对产物摘要、完整字节数、签发时间和所有归属字段；只有完整签名证据计入 downloaded，历史未签名行保留但不计数。幂等键为来源、事件 ID 和原始 body SHA-256/业务字段；响应丢失后的发送方可用新鲜 HMAC timestamp 重签同一事件并得到首次行，平台保留首次接收时间戳。相同事件 ID 但 body 或业务字段改变则返回 409，过期签名仍返回 401。

仓库当前提供 EDGE 验收程序，但没有可声称生产可信的 Huawei OBS access-log 事件桥；部署并真实验收该桥之前，OBS 来源验收必须保持 `BLOCKED`。

上线前必须用一条视频验证：仅点击下载按钮后状态仍为 `issued`；完整传输并投递可信事件后才变为 `completed`。事件源未部署或故障时不得由前端补写“已下载”。

## 6. 发布顺序

本节是唯一活动 new-api Relay 的常规版本发布顺序。若环境仍有 Python 遗留任务，必须先按
迁移手册完成一次性排空；正常发布和回滚均不得重建 Python production admission。
以下是资格完成后的执行顺序，不是当前生产执行授权：Platform v15 catalog 为 `UNQUALIFIED`，
v12/0047、v13/0048 也尚未资格化，v14/0049 同样未资格化；当前受保护发布保持 `BLOCKED / NO-GO`。
Relay 当前源码合同为 `target=8,min=1,max=8`：fresh-v8 预期只记录 `[8]`，完整历史链预期为
`[1,2,3,4,5,6,7,8]`，current-v8 只接受 exact v7 并新增不可变的供应商成本分摊证据。
专用 PostgreSQL 用例 `TestRelaySchemaPostgresConstructedV7ToV8ProviderCostEvidence` 已通过
`TEST_RELAY_SCHEMA_V7_TO_V8_DSN` 在一次性 `postgres:16-alpine` 上完成
`PG16-constructed-v7→v8-gate=PASS (9.021s)`，证明构造 v7→v8、v7 ledger 不变、v8 表与
UPDATE/DELETE 不可变 guard。该专项没有 TLS 或 pgAudit，不是生产资格门禁；
`PG16+TLS+pgAudit-full-qualification-gate=NOT_RUN`，尚未完成生产资格。下面既有 v7 长门禁只到历史 v7 边界，不能授权 v8；v8 成功后
max-v7 镜像必须判为 `ahead`。因此 Relay 同样保持 `BLOCKED / NO-GO`。

1. 冻结发布版本和数据库迁移版本。
2. 备份平台数据库、中转站数据库和关键 OBS 元数据。
3. 在 staging 使用同一镜像执行迁移与完整冒烟测试。
4. new-api 按 secret validator → database role-pre → migration → role-post → root/principal
   lifecycle 顺序验证 `target=8,min=1,max=8`、catalog、ACL 与 generation-bound release proof；
   protected API/edge/Worker 只接受 Current v8。v8 的独立生产资格完成前不得执行该步骤。
5. 客户平台当前源码 head 为 `0050_model_commercial_release`，直接前序为
   `0049_payment_finance_closure`；当前 Platform 数据库权限策略为 v15。先完成当前链的独立
   PostgreSQL 16 catalog/角色与签名 release proof 资格，再按受保护 role-pre → migration → proof
   时序执行 `python -m alembic upgrade head`、`python -m alembic check` 和
   `python -m alembic current`。开发 PG16 迁移/并发通过不等于资格通过；不得用全零占位或历史
   hash 放宽受保护校验。0050 新增证据绑定、不可变的模型商业发布计划及失败关闭的执行状态；
   0048 建立商业计费与财务状态机，0049 加固持久支付投递、Webhook
   重放、账单原件绑定、拒付偿债归属与企业催收。0049 对无原件绑定的旧结算行、无 Mandate
   的历史自动扣款订单会在 DDL 前失败关闭；必须制定保留历史的迁移方案，不得删旧事实、
   捏造原件或用户同意来绕过检查。
   历史 `0045_system_audit_actor` 为用户/系统审计主体建立显式互斥字段，并增加专用、
   最小权限的 `relay-catalog-sync` 数据库 principal；`0044_account_product_partition`
   持久化互斥的个人、企业和平台管理员账号类型，保留历史
   钱包、账本、任务、产物和成员证据，只停用不属于该账号类型的入口，并安装数据库约束和
   trigger 阻止静默跨类型；0043 仅持久加入只读提示词收集权限
   `platform.task_content.read`，不提供 manage、编辑或删除能力，表与 ACL 投影保持 0042 的
   冻结值；0042 在任何企业授权副作用前原子 claim 全局 idempotency key，并将 journal、
   授权、审计与结果同事务提交；`0041_model_capability_releases` 分离 Relay capability
   candidate 与最后批准 ceiling；
   0040 新增 Owner-only 首页精选案例草稿、不可变发布版本和紧急下线事件；0039 仍只把新
   task/outbox 的数据库 default 冻结到 new-api，不改写任何
   历史 affinity；更早的 `0038_download_evidence_checks` 与认证生命周期前序继续冻结保留。
   历史 v10/0045 的最小权限边界继续保留。`relay-catalog-sync` 只获得
   `model_definitions` 的 SELECT/INSERT/UPDATE、`model_capabilities` 的 SELECT/INSERT 和
   `audit_logs` 的 INSERT；它不能读取用户表，也不能批准、发布、定价或分发模型。受保护的
   PostgreSQL 16 当前链仍未资格化。v10/0045 的
   `7ce8849ecc4be298fe9889bdeaeb7ea17932a51c9ff9eeb024cc55bbcad44142` 与 v9/0044 的
   `64640e8ccf7069fc6ca0773af64def56babfdb80101ea9cd22e6b8e7fc00c167` 均仅保留为历史
   资格证据，不能证明当前链或授权生产；更早 hash 同样不能替代 v15 attestation。
   **0041 不会把旧 `relay_capability_revision` 或 Platform 自报能力自动回填为候选/批准
   ceiling。** 这是故意的 fail-closed 升级：旧 revision 只保留为历史证据，所有既有模型的
   candidate/approved ceiling 均保持 `NULL`，直到重新读取 live Relay 内容。
   支付订单、退款/拒付、积分、对账、自动充值及企业月结已有软件实现；真实 PSP、可信账单
   来源、生产常驻 billing worker 和通知仍未接入。`python -m platform_api.billing_worker`
   只是已实现的 HTTP-only 调度入口，不证明已发布为生产常驻服务或客户已收到通知。
   上传文件只能证明收到的 bytes，不能证明 PSP/银行/供应商来源可信；相关对账继续保留
   `SOURCE_AUTHENTICITY_UNVERIFIED`。完整边界见 [支付与财务闭环](payment-finance-closure.md)。
6. 依次启动 new-api API、生成 Worker、Provider 状态同步 Worker、Provider 监控 Worker、
   转存 Worker、回调 Worker和 download edge。
7. 在客户新准入仍关闭、Platform 管理入口单独受保护的状态下启动只配置
   `new-api-v1 / generations.v1` 的 Platform API、派发/同步/超时 Worker，以及专用
   `relay-catalog-sync` 周期 Worker；派发、状态同步和超时补偿必须继续处理迁移前已接受的
   任务，不能因公司或模型准入关闭而搁置余额和上游任务。
8. 对 `GET /api/v1/platform-admin/models` 返回的每个已发布或启用模型执行下面的升级门禁；
   任一模型缺失于 Relay、能力不安全或路由验收证据不完整时，保持该模型/客户新准入关闭，
   不得复制旧 Platform 能力来“补齐”未知 Relay 能力：
   1. 等待 `relay-catalog-sync` 完成一次单 leader 周期。Worker 使用上一轮 ETag 条件请求
      `GET /v1/models`；`304` 或完整目录无语义变化时不得写库或追加审计；
   2. 打开模型目录页面或调用 `GET /api/v1/platform-admin/models` 只读取 Platform 已持久化目录，
      不得因页面加载调用 Relay 或触发写入。新 `public_model_id` 只能出现为未发布草稿；候选
      变化必须带 `actor_kind=system`、`actor_key=relay-catalog-sync` 的系统审计；
   3. 由 Platform Owner 独立审阅候选与最后批准 ceiling 的差异，再调用
      `POST /api/v1/platform-admin/models/{model_id}/relay-capability`。批准端会再次拉取 live
      catalog，并在数据库行锁内确认 catalog revision、capability revision 和完整内容都与
      stored candidate 完全一致；同 revision 不同内容按 collision 拒绝；
   4. 人工设置显示名称、发布状态、计费方式/价格以及个人或企业分发；后台对账进程没有这些
      权限。再次读取模型清单，确认所有拟恢复分发的模型均为
      `relay_capability_approval_status=approved`、
      `relay_capability_requires_approval=false`、approved ceiling 非空且 route evidence ready。
      `unavailable`、`legacy_approved`、`pending` 或 `unapproved` 任一状态都禁止恢复该模型。
9. 验证内部健康检查、Monitor 最近成功周期、告警 Webhook 验签和第 8 步批准清单后，再恢复
   客户新准入。
10. 用测试公司执行一条低成本真实任务，核对预占、任务历史、作品索引、短签签发、完整下载事件、结算和审计日志。
11. 观察错误率、队列积压、预占余额、生成耗时、转存失败、Provider 健康样本、活动告警和
   告警投递状态至少 30 分钟。

### 受保护的 live staging 验收

生产 promotion 前必须从默认分支手动运行 GitHub Actions 的
`Relay live staging acceptance`。该 workflow 绑定 GitHub Environment
`relay-staging-acceptance`，不接受 dispatch 参数；候选配置与非敏感证据包固定来自该
Environment 的 variables，两个 Relay service credential 与 fault-control token 固定来自
Environment secrets。任一配置、证据或 credential 缺失时任务直接失败，不能降级为 dry-run。

Environment 管理员必须完成以下仓库设置，workflow 本身不会替管理员创建这些保护：

- 只允许受保护默认分支部署到 `relay-staging-acceptance`，配置 required reviewers，并限制
  谁可以发起或批准该 Environment deployment；
- `RELAY_STAGING_ACCEPTANCE_CONFIG_B64` 使用 canonical base64 编码 staging 配置；配置中的
  `candidate.gitRevision` 必须精确等于本次 workflow 的默认分支 commit SHA；workflow 会在访问
  staging candidate 前失败关闭地比较该值与 `github.sha`，防止把其他提交部署的候选证据错误
  归档到当前 commit。配置中的
  两个 `apiKeyEnv` 必须固定为 `RELAY_ACCEPTANCE_TENANT_A_API_KEY` 与
  `RELAY_ACCEPTANCE_TENANT_B_API_KEY`，fault token 名必须固定为
  `RELAY_ACCEPTANCE_FAULT_CONTROL_TOKEN`；
- `RELAY_STAGING_ACCEPTANCE_EVIDENCE_BUNDLE_B64` 使用 canonical base64 编码 JSON：
  `{"schema_version":1,"files":{"evidence/...json":"<canonical-base64>"}}`。只能包含
  `evidence/` 下最多 16 个小型 JSON 元数据、哈希或摘要证据，解码后的证据合计不得超过
  20 KiB，不能放 credential；配置中每个 `evidenceFiles[].path` 必须在包内。GitHub
  Environment variable 本身有容量限制，因此该 bundle 不是大日志、录屏或二进制文件的传输通道；
- 三个同名 secret 只保存 service credential/fault token 原值。禁止把 secret 写进配置、证据、
  workflow 参数、命令行或 artifact。

workflow 会先执行 `relay:candidate:check`，再以 `--execute-all` 对已部署 staging candidate
运行 migration acceptance，并只上传 secret-free、create-only 的 `report.json` 及其 SHA-256。
验收会把候选就绪证据分成两份独立门禁：
`contract.candidate_readiness` 是任何变更请求之前的 `preflight`，
`contract.candidate_readiness_final` 则只在所有合同流量、故障场景的清理/恢复确认和真实渠道证据
校验全部结束后执行。每个故障场景除了自身断言外，还必须返回由原始证据绑定的
`fault_environment_restored=true`；对应证据必须明确标识 `kind=fault_cleanup`、
`action=restore` 和 `data.restored=true`，仅返回场景 `PASS` 不能代替清理完成证明。
故障控制证据采用精确字段合同；每条原始证据的摘要通过后仍会递归检查已配置 credential、
credential-bearing 字段和绝对 HTTP(S) URL。任何包含 provider 临时/签名 URL、token、secret
或额外未声明字段的故障证据都会使门禁失败，且不复制进可上传报告。
真实渠道记录同样只接受声明的摘要字段；task/bill/ledger 等 reference 必须是稳定标识符，
不能用 provider URL 代替，任何绝对 HTTP(S) URL 都会失败关闭且不会进入报告。
最终门禁会重新读取 `/health/ready`，再次要求 HTTP 200、顶层 `state=healthy`、
`production_cutover_ready=true`、同一 source/upstream git revision 与 image digest，并确认 cost
incomplete/backlog/dead-letter、native billing reconciliation 均为零且成本核对完整。preflight 与
final 还会逐字段验证 Provider Runtime 已启用、Monitor 新鲜且没有 Worker error、active alert 和
unavailable route 均为零，并要求 alert、task-stage、operations-snapshot 的 backlog/dead-letter
以及 provider-result reconciliation backlog
全部为零；不能只信一个 `production_cutover_ready` 布尔值，顶层 `degraded` 即使保持 API 可服务也不能
授权切流。此后验收工具
不得再发送任何候选或故障控制变更请求。故障清理失败或最终就绪复检失败都会阻止 PASS 和 artifact
上传，preflight 证据不能替代 final 证据。
报告 schema v3 会在 `execution.mutation_tracking` 中分别记录 `python_oracle`、
`new_api_candidate` 与 `fault_control` 的变更请求尝试和已确认受理次数。请求一旦发出，
即使超时、断线或响应无法解析，`request_attempted` 也保持为 `true`；
`accepted_mutation_confirmed=false` 只表示没有拿到明确受理响应，不能解释为远端一定未发生变更。
`python_relay_source_or_deployment_changed=false` 仅说明验收工具没有修改 Python Relay 的源码或部署，
Python oracle 运行时收到的生成请求另由 `python_oracle_runtime_mutation` 如实记录。
production promotion 必须消费同一 commit 的成功 run 与上传的 `report.json`；这里的“同一
commit”同时指 workflow checkout、配置中的 `candidate.gitRevision`、候选 readiness 返回的
`x-relay-source-revision` 和 artifact 名中的 commit SHA 四者完全一致。发布记录至少保存
run URL/ID、commit SHA、artifact 名和报告 SHA-256；`contract.candidate_readiness` 与
`contract.candidate_readiness_final` 必须分别以 `preflight` 和 `final` 证据 PASS，
live cost 字段必须完整，报告 decision 仍必须是
`OFFLINE_PARITY_PASSED_REQUIRES_EXTERNAL_RELEASE_GATES`。报告明确不授权 Python production
admission，也不能替代 Provider、OBS、IdP、支付、备份恢复与容量的独立发布证据。

这个 workflow 不能替代 GitHub 仓库外的 branch protection、Environment protection 或生产
部署系统的 required check。管理员仍须在 GitHub 与生产 promotion 系统中把上述 Environment
审批和成功 run/artifact 设为强制条件；没有该外部绑定时，仓库只能证明验收能力存在，不能声称
生产放行已经被强制执行。

### 扩展 new-api Relay 原生数据库 v8

Python Relay 的 `0012_generation_contract_v1` 只冻结离线行为 oracle artifact，既不是生产
发布步骤，也不是回滚目标。new-api 本次源码契约固定为
`target=8,min=1,max=8`，使用 `relay-schema-status` 与 `relay-migrate`，不得用 Alembic
命令或生成接口的 `schema_version=1` 代替其状态证明。

- fresh v8 必须报告 `from=0`、`baseline=current=target=8`，ledger 只能有 version 8
  一行，即 `[8]`；不得为没有执行的 v1/v2/v3/v4/v5/v6/v7 伪造历史。
- exact v1 输入在迁移诊断中可为 `compatible,current=false`；它必须先由冻结的 historical
  v1→v2 no-catalog-delta bridge 形成 exact v2，再由 pinned v3 完成 v2→v3 修复。发布门禁随后只在
  自己持有的 exact-v3 合成数据库上，用不可变 pinned-v4 digest
  `ai-video/new-api-relay@sha256:53a5d65cefbc4400e9e572665b227cf4a9628774aa77c814be4082f1abd7f84f`
  实跑 v3→v4 route-binding；不可变 pinned-v5 digest
  `ai-video/new-api-relay@sha256:d5998561f1142e5189ca15f6086b42da31127ecb5d58269ac492dc4aa3f61b9a`
  接着在同一门禁数据库独占执行 v4→v5 diagnostic-taxonomy。固定的 pinned-v6 镜像
  `ai-video/new-api-relay:v6-canary-0b0b8bf5`（image/repo digest
  `sha256:576b836d26f19825532feaca1f9f7950f28affc78477d61d7b29dca474b8817f`，source revision
  `0b0b8bf597aeb9e69e89a04f9b4d7b1d712e3391`，source snapshot
  `sha256:1ad8305e212ac45bca32d2bbcf064c20bdb4337623b952452659748b17d2e836`，file count `2106`，
  upstream `0ab02020603d22e5613bc4cf46bfab06f8567769`）独占执行 v5→v6 durable route-test lifecycle，
  再由历史 v7 执行 v6→v7 artifact content-type release；current v8 只允许从 exact v7
  执行 v7→v8 provider-cost allocation evidence。预期终态为
  `baseline=1,current=target=8`，ledger 精确为 `[1,2,3,4,5,6,7,8]`，原 v1..v7 行完全不变。
  current v8 不得跳过 exact v7。pinned-v4 与 pinned-v5 都只是 immutable binary
  behavior fixture；pinned-v6 是 5→6 release owner。所有 pinned image 均不得访问业务/live 数据库，
  也不是运营迁移源；业务数据库只允许由本次经审阅的 current-v8 migrator 执行其冻结版本链。
  v1/v2/v3
  PostgreSQL catalog digest 相同是经冻结的设计；v2 source/checksum 仍必须独立，冻结值分别为
  `sha256:03de3ed038c3a9f7b6e160ac720e4350b9d468c09417cdc9e280289ed390fef2` 与
  `sha256:a3dc154ca42086544096cc0c3e3f2c84479e52e2ad76bd4d32aa2806c2c9af0e`；v3 source/checksum
  分别为 `sha256:4d784286e5480a10a83f4408b303eec075a347fa405d45650e12c19425e4659d` 与
  `sha256:0295d36ca5032088cc2e0b3b7f935aaeb24c3c5847a6b0a92a4dc3099d58e553`。v4 增加已接收任务恢复所需的
  adapter profile、model release 与 route snapshot 六列；三元组必须全空或全非空，历史空组仅允许一次
  原子回填，绑定后仅允许同值更新。v4 source/model/catalog/checksum 分别为
  `sha256:6c1ff01ee6567115190f12dcd947a96b01d88bbee5668705d811f51a526e4c34`、
  `sha256:75c900e5d3edb4785f774d44b23e60b7d0fdd23ba541c28ccf6cb0ec727bb01c`、
  `sha256:b8260ee751d0b9bb6dcd0c2d2d4105bef475296f25a4babb13fca3e117888126` 与
  `sha256:4a91686133814c07401a11eea3fe373154219923c4d63666ca99e3049d96079d`。
- v5 为 channel-test diagnostics 增加受数据库保护的 taxonomy；source/model/catalog/checksum 分别为
  `sha256:1d63451fdcdc0edfd869df3995d4ea14b3d0a3fa0871dadde9a842197b92c9d8`、
  `sha256:75c900e5d3edb4785f774d44b23e60b7d0fdd23ba541c28ccf6cb0ec727bb01c`、
  `sha256:2bc1bf2f68e513d12de36cd4f8c2ca6a569d102b93a6fbaea444facad3189fc1` 与
  `sha256:d8066d7081eb4a73239333bab10b78e825457dcf78bc3d3aaa195c52edc8b7f6`。
- v6 为 route-bound channel test 增加 transport revision/digest、durable provider task、sticky resume、
  secret-free artifact evidence 与 owner reconciliation 数据库 guard；provider success 后的 proof/artifact
  验收失败保持 `pending+submitted`，不得释放 route block。v6 checksum/catalog 分别固定为
  `sha256:8cffc546bb13c3f36f734dbb2e45a750da5b3e614de3beda82c6dd87dd84af00` 与
  `sha256:180546808883afca58bb9fd246340339d87ac023aff91fe23186d9274dc15135`。
- v7 只把受保护渠道测试的 artifact 类型从 `video/mp4` 扩展为 `video/mp4` 或 `image/png`；
  其余五组 v6 constraints/index OID、v1..v6 ledger 和 ACL 必须保持不变。v7 checksum/catalog
  分别固定为 `sha256:da3ddb86260818f894b13fc6b3ad34031b6089dddb83954172984d07e451c4c3`
  与 `sha256:af1377416cb2788093391a03491d2bb380fc4b81db5f08d0d628f3f8a2abef01`。
- v8 只在 exact v7 上增加不可变、回执绑定的 provider-cost allocation evidence；
  source/model/checksum/catalog 分别冻结为
  `sha256:4d1422f6f691d1440600536f9b89c3e1fb9af9e69f880437b95d437b101f71dd`、
  `sha256:24f03d665ed5df5958cbf82075bfd28fb8aeb97fee277164e443a0218132776d`、
  `sha256:1def22667e226cf8dfbd467e18445874dcce6959a8b9e74b43623e14bbc31de7` 与
  `sha256:6374866475d3b9c404592c39ef5ad8be1b63066308b502e5362dbc47e469823c`。
  这些冻结值只证明源码身份，不证明生产 PG 资格。`TEST_RELAY_SCHEMA_V7_TO_V8_DSN`
  专项已有 `PG16-constructed-v7→v8-gate=PASS (9.021s)`，但没有覆盖 TLS/pgAudit；
  `PG16+TLS+pgAudit-full-qualification-gate=NOT_RUN`。
- raw/unversioned previous-candidate 必须先由固定的 v1 migrator 转成 exact v1，再依次执行
  v1→v2、v2→v3、v3→v4、v4→v5、pinned-v6 的 v5→v6、历史 v7 的 v6→v7，最后由
  current-v8 从 exact v7 执行 v7→v8。current v8 image
  直接重放 live v1 bootstrap、测试 SKIP、dirty/partial/ahead/unknown、catalog/ACL/ledger drift 都是发布失败。
- pinned v1 段只准新增 schema/ledger/catalog/roles；旧候选中既有的唯一
  `lifecycle_root` 与 setup marker 必须和普通用户、业务行、加密 credential 一样逐行 digest
  后原样保留，不得新增或改写 root/setup，principal 仍必须为零，且不准启动 API/edge。
  historical v2 bridge 与 v3 correction 必须在同一数据库再次重核这些数据，随后完整执行
  历史 v7 长门禁只证明 proof -> root exact replay (`unchanged`) -> principal creation -> API/edge
  v7 lifecycle；它不能替代尚未运行的 current-v8 同库终态证据。fresh-v8 与 pinned-v1 application-catalog
  参考库也不能替代这项 current-v8 证据。pinned-v1 参考库只证明冻结的 v1 application catalog 与单行
  ledger，不代表旧 v1 runtime 或 system-catalog acceptance。
- 受保护的 post、service-principal provision/rotation、root bootstrap、API、download
  edge、database-release proof consumer 与 runtime readiness 全部要求 Current v8；只有
  role-pre 与 migrate proof path 可读取 compatible v1/v2/v3/v4/v5/v6/v7 以完成升级。v8 成功后，`max=7`
  镜像必须把数据库判为 `ahead` 并失败关闭，不能直接回滚；旧镜像不兼容时只能前向修复。
- 已归档的 v7 跨版本 ACL 边界严格执行 `pinned-v3 migration -> pre-v4 zero-ACL role-pre ->
  pinned-v4 migration -> pinned-v4 full verifier -> pre-v5 zero-ACL role-pre -> pinned-v5 migration ->
  pinned-v5 rollback verifier -> pre-v6 zero-ACL role-pre -> pinned-v6 migration ->
  pinned-v6 rollback verifier -> pre-v7 zero-ACL role-pre -> historical-v7 migration`。
  v3 提交 ACL 后，current provisioner 先恢复零 ACL 角色桩，才允许不可变 v4 binary 独占执行
  3→4；完整 v4 verifier 通过后再次恢复零 ACL 角色桩，才允许 pinned v5 独占执行 4→5；
  v5 verifier 通过后第三次恢复零 ACL 角色桩，才允许 pinned v6 独占执行 5→6；
  v6 rollback verifier 通过后第四次恢复零 ACL 角色桩，才允许历史 v7 独占执行 6→7。
  role-pre does not perform schema migration，只负责清除上一版本已提交授权并重建受保护角色桩。
  四个边界必须分别归档 `pre-v4-zero-acl-role-stub-gate=PASS`、
  `pre-v5-zero-acl-role-stub-gate=PASS`、`pre-v6-zero-acl-role-stub-gate=PASS` 与
  `pre-v7-zero-acl-role-stub-gate=PASS`。

运行 `make test-relay-schema-legacy-pg16` 时归档固定 PG16.14/pgaudit16.1/TLS image、固定
 v1/v2/v3 source、不可变 pinned-v4/pinned-v5/pinned-v6 image provenance 与 test-only fixture digest，以及 fresh-v7-row7-only、fresh-v1-row1-only
application-reference、legacy-to-v1、v1 保留既有 root/setup 且无新增 runtime side effect、historical frozen v1-to-v2 no-catalog-delta、
 immutable v2-to-v3 one-shot、gate-owned exact-v3 上的 pinned-v4 one-shot、
 `pre-v4-zero-acl-role-stub-gate=PASS`、pinned-v4 one-shot、
 `pinned-v4-state-ledger-catalog-acl-guards-gate=PASS` 只读/回滚核验、
 `pre-v5-zero-acl-role-stub-gate=PASS`、pinned-v5 的 v4→v5 one-shot、同库 exact-v1-to-v5 ledger、
 pinned-v5→v6 rollback verifier、`pre-v6-zero-acl-role-stub-gate=PASS`、pinned-v6 的 v5→v6 one-shot、
 exact-v1-to-v6 ledger、pinned-v6→v7 rollback verifier、`pre-v7-zero-acl-role-stub-gate=PASS`、
 current-v7 的 v6→v7 one-shot、exact-v1-to-v7 ledger、post-v7 proof/root/principal/API/download-edge、
 route-binding mutation/fencing 与 max-v6-ahead/no-direct-rollback 各项明确 PASS；Go 测试与两项
rotation 测试也必须有 JSON PASS event，缺失或 SKIP 均失败。完整 protected Compose 仍按
`validator -> role-pre -> migrate -> post -> root/principal/API/edge` 的既定 proof 顺序执行。
这些是历史 v7 证据；当前另有一次聚焦的 `PG16-constructed-v7→v8-gate=PASS (9.021s)`，
但它不包含 TLS/pgAudit、v8 当前角色/ACL 或 Current-v8 API/edge lifecycle；
`PG16+TLS+pgAudit-full-qualification-gate=NOT_RUN`，不能标为生产 v8 PASS，也不能用于生产切流。

## 7. 回滚

生产回滚只允许上一版已验证、schema-compatible 的 new-api 不可变镜像。Python Relay 没有
受保护配置、Compose profile 或 production credential，不能作为回滚目标。

- 应用回滚使用上一版已验证的 new-api digest，不在生产容器内临时改代码。
- 数据迁移优先采用向前兼容和修复迁移；当前 schema/proof 与旧镜像不兼容时只能前向修复，
  不得 downgrade 或伪造 release proof。
- 若 Relay 故障，先停止新任务入口，保留队列与数据库，不删除任务。
- 若单个 Provider 故障，让 Router 只对已证明未创建任务的新请求安全切换；不要把存量任务
  改绑到备用渠道，也不要对 `reconciliation_required` 人工补单。
- 若状态长期不确定，不人工直接改余额；通过幂等补偿命令释放或结算，并留下审计记录。
- 若 OBS 转存异常，任务不得标记成功，也不得结算。
- 关闭新准入或替换镜像不等于关闭清理职责。只要
  `artifact_cleanup.maintenance_required=true`，至少保留一个连接原 Relay 数据库和原 OBS
  BindingID 的 new-api cleanup supervisor；不得把最后一个实例缩容为零。

## 8. 监控与告警最低集合

- API 5xx、P95 延迟、认证失败率；
- Relay 队列深度、最老消息年龄、生成重试、回调重试和回调死信；
- Provider 5 分钟真实上游终态成功率、路由健康失败比例、批量账号失效、触发/恢复事件；
- `provider_monitor_worker` 存活、最近成功周期、租约年龄、健康样本年龄、告警
  `pending`/`dead_letter` 数量和最老年龄；
- `reserved_cents` 长时间不归零的任务；
- 超时扫描的 `compensated`、`reconciled`、`deferred` 数量，尤其是
  `deferred_unsafe_submission`、Relay 查询失败和最老未决任务年龄；
- 产物转存失败、大小异常、哈希不一致；同时检查 Relay readiness 的
  `artifact_store.binding_id`，以及 `artifact_cleanup` 的 `pending`、`claimed`、
  `quarantined`、`cleaned`、`published`、`due`、`retrying`、
  `retrying_binding_mismatch`、`cleaned_retrying`、`cleaned_binding_mismatch`、
  `dead_letter` 和 `binding_mismatch_dead_letter`，以及 worker 的 started/running/stale、最近
  heartbeat/success/error 和连续错误数。转存会在上传前写入 token-scoped durable intent，
  成功发布与任务完成同事务提交；未发布对象由与 Redis/生成准入解耦的 supervisor 使用
  30 秒数据库时钟租约和随机 fencing token 清理，每 500ms 扫描一次。第一次删除后隔离
  10 分钟再删，至少两次成功后保留 `cleaned` tombstone 并每 24 小时永久复删；初始清理
  最多失败 8 次后进入死信，但 cleaned 周期复删失败不会死信，会无限低频重试并持续使
  readiness 降级。晚到 Put 会重新激活所有非 published 的清理状态并重置重试预算；正常
  live upload 不会被提前扫描。切换 OBS endpoint/bucket 或 filesystem root 前必须先处理旧
  BindingID 的待清理项；BindingID 不匹配时 worker 绝不向新存储删除同名对象；
- 数据库连接、慢查询、磁盘和备份状态；
- OBS 4xx/5xx、匿名访问检查与存储增长；
- 下载签发量、可信完成量、事件延迟、幂等重放、冲突和长期未完成比例；
- 充值、结算、退款和管理员操作审计。

Provider Monitor 默认需要连续 2 个异常周期才触发，连续 2 个健康周期才恢复：成功率低于
80% 需要窗口内至少 20 个上游终态；大面积失败需要至少 2 条路由且失败比例达到 50%；
批量失效需要同一 Provider 至少 3 个账号因 Provider 错误永久停用。人工 drain 和临时冷却
不计入批量失效。阈值应在 staging 按真实流量基线校准。

`/health/ready` 把上游 Provider 视为新任务准入依赖：持久化依赖健康但所有 Provider
不可用时返回 HTTP `200`、`state=degraded`，不能让负载均衡因此摘掉所有 Relay API
副本，否则存量任务回调、查询和对账也会中断。PostgreSQL、Redis、转存队列或产物存储
不可用才返回 `503 unavailable`。生产运行模式下，readiness 还报告 Provider Monitor 的
最近成功周期、活动告警、待投递积压和死信；周期陈旧、活动告警、待投递陈旧、任意死信、
缺少告警接收端或状态查询失败会把整体状态降为 `degraded`，但仍返回 HTTP `200`。
这不是 Worker 进程的直接心跳，默认只检查 HTTP 状态码的容器健康检查也不会因此重启；
编排平台和独立外部监控必须解析 JSON 详情并检查进程和重启次数。

当前 new-api 的发布、proof、进程与告警接入以
[new-api 生产部署门禁](new-api-production-deployment.md)为准；需要核对不得丢失的状态机
不变量时，可只读参考[历史 Python Provider 监控语义（离线 oracle only）](provider-monitoring.md)。当前没有公开的 Provider 告警
管理 API 或 dead-letter 重放工具；禁止直接改告警表和账号池表，恢复/补发必须走受审计的
运维流程。

## 9. 超时补偿运维

容器编排中的 `platform-timeout-worker` 会持续扫描超时任务；也可从受控网络携带
`X-Internal-Service-Token` 调用 `POST /internal/tasks/timeout-scan` 手动执行一批，使用
`GET /internal/tasks/timeout-events` 查询不可变处理记录。处理记录会关联对应的钱包
`SETTLE`/`RELEASE` 流水，便于对账。

自动退款只覆盖“Outbox 仍为 pending、attempt_count=0、无 Relay ID”的确定未派发任务。
`retry` 或 `processing` 可能代表供应商已接受但平台丢失响应，必须继续使用稳定幂等键
恢复或查询 Relay，不能仅凭本地超时释放。已有 Relay ID 的任务以 Relay 终态为准：
成功先结算，失败/取消才释放，非终态或查询失败保持预占并列入 `deferred`。

上线时把 queued/processing 阈值设置在渠道正常 P99 时延之上。若 `deferred` 连续增长，
先检查 Relay 状态查询、平台派发 Worker 和内部网络；不要直接修改钱包字段。需要临时
停止补偿时只缩容 `platform-timeout-worker`，不要删除事件、流水或任务。恢复后使用受
保护的单次扫描接口验证一批，再恢复循环 Worker。

## 10. 自动发布 Worker 运维

`platform-publishing-worker` 是自动发布的独立常驻进程。生产必须显式设置
`PUBLISHING_WORKER_ENABLED=true`、`PUBLISHING_ADAPTERS` 和
`PUBLISHING_MEDIA_RESOLVER`；适配器及素材解析器均使用受信任的
`python.module:factory` 工厂。生产启动会拒绝 Mock、空适配器目录、空素材解析器以及未声明
`production_ready=true` 的适配器。

只有存在启用的 `feature.auto_publish` 公司授权时，平台 readiness 才要求该 Worker 的生产
配置完整；配置缺失会返回 503，避免创建发布任务后无人执行。发布渠道 POST 前 Worker 会再次
核对公司授权，授权已撤销则在调用外部平台前失败关闭。`submission_unknown` 不得自动重投，
必须由拥有 `publish.jobs.manage` 的人员根据外部平台证据走人工对账接口确认成功或失败。

部署或修改发布适配器后，至少核对以下进程与 readiness：

```powershell
docker compose up -d --force-recreate platform-api platform-publishing-worker
docker compose ps platform-api platform-publishing-worker
Invoke-RestMethod http://127.0.0.1:8180/health/ready
docker compose logs --since 10m platform-publishing-worker
```

日志中不得出现原始凭证。停用自动发布时先停止新的发布任务准入，等待 `queued`、
`submitting` 和 `submission_unknown` 全部完成或人工对账，再缩容 Worker；不得删除发布任务和
尝试记录来清空积压。

## 生产认证、前端运行时配置与 Relay claim 补充

先为浏览器入口分配一个不与宿主、VPC 或其他 Compose 网络重叠的专用 `/29`。受保护
overlay 固定只让 `api-gateway` 与 `platform-api` 加入该入口网络，取消 Platform API 继承的
`8200` 宿主端口，并让 Uvicorn 只信任网关的单个固定 IP；不得使用
`--forwarded-allow-ips=*`。示例环境中的 `.1/.2/.3` 分别是宿主 TLS 入口的 bridge 地址、
Nginx 与 Platform API：

```text
PLATFORM_API_INGRESS_NETWORK_NAME=ai-video-platform-api-ingress
PLATFORM_API_INGRESS_SUBNET=172.30.254.0/29
PLATFORM_API_GATEWAY_IP=172.30.254.2
PLATFORM_API_INTERNAL_IP=172.30.254.3
PLATFORM_TRUSTED_EDGE_CIDR=172.30.254.1/32
```

宿主 TLS/CDN 入口必须删除来客提供的全部 `X-Forwarded-*`，再写入实际来源地址；
`PLATFORM_TRUSTED_EDGE_CIDR` 只能是该固定入口的地址或最小 CIDR。受保护 Nginx 先用
`real_ip` 解析这一跳，再以单值 `X-Forwarded-For: $remote_addr` 转给 Platform。发布前用
两个真实来源验证 OIDC 登录事务的 `ip_hash` 不同、单一来源超限只返回该来源的 `429`，并
确认宿主和非入口容器都不能直接访问 `platform-api:8000`。若宿主网络变化，必须同时重签
这五项配置并重新执行该门禁，不能临时扩大可信代理范围。

客户 Platform API 进程使用 OIDC Authorization Code + PKCE public client：

```text
AUTH_LEGACY_BEARER_ENABLED=false
OIDC_ENABLED=true
OIDC_SELF_SIGNUP_ENABLED=false
OIDC_ISSUER=https://idp.example.com/
OIDC_AUTHORIZATION_ENDPOINT=https://idp.example.com/oauth2/authorize
OIDC_TOKEN_ENDPOINT=https://idp.example.com/oauth2/token
OIDC_JWKS_URI=https://idp.example.com/.well-known/jwks.json
OIDC_CLIENT_ID=ai-video-platform
OIDC_REDIRECT_URI=https://platform.example.com/api/v1/auth/callback
FRONTEND_ORIGIN=https://app.example.com
PLATFORM_OWNER_USER_IDS=["<最高权限账号在 IdP 中的固定 sub>"]
PLATFORM_ADMIN_REQUIRED_AMR=["webauthn","passkey","fido","hwk"]
PLATFORM_ADMIN_STEP_UP_MAX_AGE_SECONDS=300
```

OIDC client 不配置 secret。IdP 必须启用 PKCE S256、RS256 ID token、稳定 `sub`、精确
issuer/audience、已验证 email、nonce、`kid`/JWKS 轮换；平台管理员还必须提供真实
`auth_time` 和防钓鱼 `amr`。`PLATFORM_OWNER_USER_IDS` 是 IdP subject allowlist，不是本地
User UUID。修改 allowlist 属于密钥级生产变更，需要双人复核和变更审计。

浏览器不再保存访问 token。Platform callback 创建服务端 `__Host-ai_video_session`
Secure/HttpOnly/SameSite=Lax Cookie；写请求还必须通过精确 Origin、可读
`__Host-ai_video_csrf` Cookie 和 `X-CSRF-Token`。会话、OIDC state、邀请 capability 的
HMAC pepper 来自 Platform API typed bundle 的 `jwt_signing_secret` 历史字段；轮换该值
等价于全局登出并使未接受邀请失效，必须先公告并准备重发。密码、MFA、通行密钥和恢复
全部由 IdP 处理。

`FRONTEND_ORIGIN` 与 Platform callback/API Origin 必须是浏览器判定的同一
schemeful site（推荐 `app.example.com` 与 `platform.example.com`，且都为 HTTPS），
否则 `SameSite=Lax` 的 host-only BFF Cookie 不会随前端 fetch 到达 API。上线 canary 必须
在目标浏览器实际确认 Cookie 被发送；不得为绕过错误域名规划而改成宽松 Domain Cookie。

OIDC callback 的授权码与 state 位于一次性查询串。受保护 Compose 必须保留 Platform
Uvicorn 的 `--no-access-log`，Nginx 的精确 `/api/v1/auth/callback` location 必须保持
`access_log off`、`error_log ... crit`、`Cache-Control: no-store` 与
`Referrer-Policy: no-referrer`；不得为了常规请求日志而重新记录完整 callback URI。
外层 CDN、WAF、Ingress 与负载均衡器也必须对该精确路径关闭查询串采集或只记录 `$uri`；
登录成功/失败、会话创建与吊销应以 Platform 只追加、无 token 的安全事件和 request ID
观测。

前端镜像不得烘焙用户身份或 token。部署层可以向页面注入：

```html
<script>
window.__AI_VIDEO_RUNTIME_CONFIG__ = {
  platformApiUrl: "https://api.example.com",
  companyId: "<current-company-uuid>"
};
</script>
```

生产构建固定忽略旧 `sessionStorage["ai-video.access-token"]`。不得使用 `VITE_*`、静态
`.env`、URL、`localStorage` 或公共 HTML 保存 token、用户 ID、公司身份或管理员身份。
前端只把所选 company ID 作为上下文提示发送，Platform 每次都重新验证公司状态和 membership。

上线 canary 必须覆盖：正常登录；错误/重放 state；JWKS `kid` 轮换；邀请接受/过期/撤销；
单设备与全设备登出；会员停用只影响单公司；全局 suspend/deactivate 立即切断全部范围；
平台 owner 的 WebAuthn step-up；缺 Origin/CSRF 的写请求被拒。日志、URL、审计 details 和
浏览器存储都不得出现 code、state、session、CSRF 或邀请原文。

若 Sites 前端与 Platform 跨域部署，Worker 必须同时配置 `PLATFORM_API_ORIGIN=https://api.example.com`，且该值必须与页面注入的 `platformApiUrl` Origin 完全一致。首页精选媒体由 Platform 稳定地址跳转到私有 OBS 的短期签名地址时，还必须将 `PLATFORM_SHOWCASE_MEDIA_ORIGIN` 设为精确的 OBS 虚拟主机 Origin（例如 `https://<bucket>.<obs-endpoint-host>`）；Worker 只把这两个经过严格 HTTPS Origin 校验的值加入相应 CSP，禁止通配域名、路径、查询串或凭据。前后端同源且不使用 OBS 跳转时不需要额外绑定。

new-api 生产配置必须来自固定 inventory 与分进程 secret bundle，并至少冻结：

- `new-api-v1 / generations.v1` 唯一 Platform backend；
- 逐 route 的公开能力、安全故障切换交集和 release-pinned Ed25519 acceptance evidence；
- 仅服务端可读的 Provider credential keyring、最小权限账户与版本化 CNY 合同费率；
- submission/poll/transfer/callback lease、随机 token fencing、Provider Monitor 和外部告警；
- customer-platform 与 internal-tiktok 分离的 tenant/client credential、callback route 和限额；
- Relay-output 与 Platform-input 分离的 Huawei OBS credential/bucket/prefix。

production 无条件拒绝 Mock、浮动镜像、过期或错误 digest 的 route acceptance、raw secret env、
缺失真实 route、未配置 Monitor/alert sink 和不完整成本证据。结构校验与本地测试不能验证真实
账号、配额、限流、账单或 OBS；仍必须执行绑定当前 image digest 的低成本 canary。完整规则见
[new-api 生产部署门禁](new-api-production-deployment.md)、
[Relay 真实渠道与 OBS 验收](relay-real-channel-acceptance.md) 和
[历史 Python Provider 监控语义（仅作离线 parity 参考）](provider-monitoring.md)。有官方幂等设施时才使用稳定
`job.id`；没有官方保证时必须禁止对结果未知的 POST 自动重提或跨渠道切换。提交、Provider
轮询与产物转存 claim 租期必须按各自最坏网络时延设置并通过心跳续租。

账号池的非敏感状态位于 PostgreSQL `provider_account_states`。`max_concurrency` 是提交、
生成和未知提交对账期间的长时活跃任务槽，`requests_per_minute` 是每账号固定一分钟窗口。
账号池忙或 RPM 到顶时，Generation Worker 使用 Redis 延迟集合重新投递且不增加 Provider
尝试次数；延迟由上游 `retry_after_seconds` 或
`RELAY_PROVIDER_ADMISSION_RETRY_SECONDS` 决定。永久失效/人工 drain 只关闭新准入，存量
任务必须继续粘在原 `provider@account_id` 轮询。生产控制面应对停用、启用和批量 drain
提供独立鉴权与审计，禁止直接修改账号池表。

Relay 回调对原始请求体按 `timestamp.event_id.body` 做 HMAC-SHA256 签名。平台严格校验时间窗、事件 ID、任务与 Relay job 对应关系；重复事件只返回幂等成功，内容冲突拒绝。主动回调是主路径，状态同步 Worker 是网络故障时的兜底。发布前必须在 staging 验证公网 HTTPS、DNS、防火墙、密钥轮换、重试与 dead-letter 告警，不能只依赖本地容器契约测试。

上线前必须在 staging 验证：过期 claim 会进入 `reconciliation_required` 而不是被新 Worker 接管重投、旧 token 不能覆盖新状态、Provider 已返回而落库失败时不会退款或重复下单。new-api 统一经上文 Platform 管理 API 完成发现、证据核实、审批和 resolve。确认已创建时提交供应商任务编号并恢复轮询；只有错误码为 `SUBMISSION_RECONCILIATION_REQUIRED`、且 Relay 没有 `provider_task_id` 的未知提交，才能确认未创建并标记失败、释放预占。轮询对账已有 Provider route 和任务编号，接口会拒绝 `not_created` 或改写标识，必须恢复原任务轮询并依据真实终态处理。生产还必须完成真实 IdP、真实 Provider、私有 OBS、支付回调、监控告警和 previous-new-api 回滚演练。

> 本节没有改变当前 NO-GO 结论：真实 IdP、真实 Provider、生产 OBS 和可信支付未接入前，不得开放公网商用。
