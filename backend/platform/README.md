# AI 视频客户管理平台后端

这是客户管理平台的首个可运行后端骨架，包含多公司隔离、成员与权限、模型授权、
公司钱包账本和任务记录。金额一律以整数分保存。

## 本地运行

```powershell
cd backend/platform
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn platform_api.main:app --reload
```

默认使用当前目录的 SQLite 数据库，但不会自动建表。数据库结构统一由 Alembic 管理。
生产环境请复制 `.env.example`，使用 PostgreSQL，并在发布应用前以独立迁移步骤执行：

```powershell
python -m alembic upgrade head
python -m alembic check
```

`AUTO_CREATE_TABLES` 仅保留给隔离测试；生产配置若尝试开启，应用会拒绝启动。

## 生产配置

生产必须至少显式配置：

```text
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://...
AUTO_CREATE_TABLES=false
ENABLE_BOOTSTRAP=false
CORS_ORIGINS=["https://app.example.com"]
```

开发环境未配置 `CORS_ORIGINS` 时默认允许 `http://localhost:5173` 和
`http://127.0.0.1:5173`。生产环境没有显式来源时会拒绝启动；不要使用 `*` 搭配凭据。

最小容器镜像：

```powershell
docker build -t ai-video-platform .
docker run --rm -p 8000:8000 --env-file .env ai-video-platform
```

容器不会自动执行迁移，应由发布流水线或一次性迁移任务先执行 Alembic。

## 健康检查

- `GET /health`：兼容旧调用，只表示应用进程可响应
- `GET /health/live`：存活探针，不依赖数据库
- `GET /health/ready`：就绪探针，会执行数据库查询；不可用时返回 503

## 认证边界

开发环境可以使用以下请求头进行本地联调：

- `X-Company-ID`: 当前公司 ID
- `X-User-ID`: 当前用户 ID

服务端会校验 URL 中的公司、请求头中的公司以及用户的有效公司成员关系。生产环境
不会信任这些开发请求头，也不会接受旧浏览器 Bearer；身份由 OIDC PKCE 登录映射到
服务端可撤销 Cookie 会话。每次请求仍会重新校验全局账号状态、`auth_version`、公司状态
与 membership，客户端提供的公司 ID 只是待核验的上下文选择，不是授权声明。

## 初始化

本地默认启用 `POST /api/v1/bootstrap`，一次创建公司、老板用户、公司钱包及老板角色。
`POST /api/v1/bootstrap/models` 可在开发期创建模型与带版本的能力配置，随后由平台管理员
通过管理员模型授权接口为公司选择唯一的按秒或按条价格。
生产必须设置 `ENABLE_BOOTSTRAP=false`，由平台管理端或受保护的运维流程创建公司。

公司内固定为老板、组长、运营三级。新成员默认是运营，也可在创建时显式设为组长；
普通成员必须且只能拥有一个主级别，升降级会原子替换原级别。自定义权限角色可以额外
附加，老板可配置组长与运营的权限模板。平台管理员是公司层级之外的独立身份。

权限目录由 `GET /api/v1/companies/{company_id}/permissions` 统一返回。当前有效目录包含 16
项，每项都绑定实际的公司端路由校验；只有公司老板可以为员工逐项配置。角色权限只是默认
模板；个人状态为 `inherit`（无个人覆盖，跟随模板）、`allow` 或 `deny`。最终权限的优先级为
“个人覆盖 > 所有已分配角色的并集 > 默认拒绝”。`models.manage` 与 `tasks.manage` 是已退役
且永不复用的权限码，不会由目录 API 返回，不接受新的角色分配或个人覆盖，也不参与有效权限
求值。
`PUT /members/{membership_id}/access` 会在同一事务内同时替换公司级别、附加角色和个人
权限覆盖，避免前端分两次提交造成半完成状态。调用方必须同时提交读取时的
`expected_role_ids` 与 `expected_permission_overrides`；服务端在成员行锁内比较快照，陈旧编辑
返回 409，不能静默覆盖其他会话的新配置。expected 快照只覆盖当前角色分配和个人覆盖，
不包含角色模板的权限内容；模板变更遵循其独立生命周期。批量替换字段必须显式提交（清空
也要传 `{}`），未知字段直接返回 422。停用成员、老板本人、操作者本人、跨公司成员及非老板
操作者都不能被该接口修改；实际变更会记录完整前后值审计。

## 服务端报价

创建任务时客户端不能提交报价。服务端从公司模型授权中读取价格：

- 按秒计费：模型目录 `billing_mode=per_second`，读取请求的 `duration_seconds`
- 按条计费：模型目录 `billing_mode=per_item`，读取 `output_count`，默认 1

计费方式属于全局模型定义；公司授权只能为该方式配置单价，不能让同一个模型在不同公司使用不同计费方式。

按秒与按条必须且只能配置一种。任务创建时会固化单价、数量、最终报价、授权记录、
模型能力版本与完整能力配置，后续调整模型或价格不会改变历史任务。

制作台创建任务时应提交读取模型响应时得到的 `expected_capability_version`。服务端会在
报价、余额预占和 Outbox 入库前确认模型仍处于已发布且启用状态，并核对该版本；能力已更新
时返回 409。`request_payload` 只允许 `mode`、`prompt`、`assets`、`duration_seconds`、
`aspect_ratio`、`resolution`、`output_count`、`face_enabled` 和对象类型的 `metadata`，未知
字段默认拒绝。调用方 metadata 只会进入 Relay 的 `metadata.client_metadata`，不能覆盖平台
身份字段或绕过能力契约向 Provider 传参。

平台管理员通过 `GET /api/v1/platform-admin/relay-models` 读取中转站的服务认证模型目录，
检查平台模型是与 Relay 一致、安全收紧、未配置还是越界。只有一致或安全收紧的模型可用
`POST /api/v1/platform-admin/models/{model_id}/relay-capability` 确认当前 Relay revision。
确认值不会改变平台能力版本；它会随新任务快照和 Relay Outbox 固化为
`expected_capability_revision`。Relay 若在真实渠道提交前发现 revision 漂移，会明确失败且
不会调用供应商。管理员模型页已提供检查状态与“确认能力”操作。只要平台配置了 Relay，
未确认 revision 的草稿不能发布，迁移前遗留的未固定模型也不能创建新任务。

## 中转站可靠提交

创建生成任务、预占公司额度和写入 `relay_submission_outbox` 在同一个数据库事务中
完成。浏览器永远不会拿到 role-specific `relay_backends` 中的 client ID 或 API key。
独立派发进程使用任务 ID 派生的稳定幂等键调用中转站：

受保护的 staging/production 只接受一个 code-owned Relay 身份：
`new-api-v1 / generations.v1`。Platform API、dispatcher、relay-sync 和 timeout-worker
各自从不可变的进程密钥包取得最小权限凭据，但必须指向同一个 canonical new-api
origin；`RELAY_BASE_URL/RELAY_CLIENT_ID/RELAY_API_KEY`、多 backend 与隐式 fallback 均被
拒绝。历史 `legacy-default-v1` task/outbox affinity 只为审计保留，切换前必须 drain 或
reconcile 到终态，受保护运行时不会再解析到 Python Relay。内部 TikTok 系统继续使用
独立 tenant 与 service principal，不能复用 Platform 凭据。

```powershell
python -m platform_api.dispatcher
python -m platform_api.relay_sync_worker
python -m platform_api.timeout_worker
```

三个进程都支持 `--once`，生产循环支持 SIGINT/SIGTERM 优雅停止、空闲退避，并避免把
异常消息中的 DSN 或密钥写入日志。派发遇到网络、429、5xx、响应丢失或无法解析的成功
响应时会保留预占并延迟重试；重试耗尽或幂等冲突会进入 `reconciliation_required`，不会
推测失败并退款。只有可证明未被 Relay 接受的永久 4xx 才终止任务并释放预占。状态同步
进程轮询中转站，成功后按服务端报价结算，失败或取消时释放预占，重复终态同步不会重复记账。

配置 `RELAY_CALLBACK_PUBLIC_URL` 与
`RELAY_CALLBACK_SIGNING_SECRETS={"new-api-v1":"..."}` 后，平台会把 backend-qualified
回调目标随任务提交给 Relay，并通过
`POST /internal/relay-callbacks/new-api-v1` 接收 HMAC-SHA256 签名事件。无 backend 的旧
`POST /internal/relay-callbacks` 在默认及受保护配置中不可调用。
接收端校验原始请求体、时间窗、事件 ID、任务及 Relay job 对应关系；事件按 ID 幂等，
不可变记录可由携带内部服务令牌的
`GET /internal/relay-callback-events?page=1&page_size=50` 查询。主动回调是主路径，状态同步
进程继续作为网络故障时的降级兜底；两项回调配置必须同时存在，签名密钥至少 32 字节。

`POST /internal/relay/dispatch-once` 和 `POST /internal/relay/status` 仅用于受控内部调用，
必须携带 `X-Internal-Service-Token`。普通公司用户没有结算或失败释放接口。

## 长任务超时补偿

`timeout_worker` 按以下配置扫描超过运行预算的 `queued`/`processing` 任务：

```text
TASK_QUEUED_TIMEOUT_SECONDS=3600
TASK_PROCESSING_TIMEOUT_SECONDS=21600
TASK_TIMEOUT_SCAN_INTERVAL_SECONDS=30
TASK_TIMEOUT_BATCH_SIZE=100
```

自动释放遵循保守规则：只有 `relay_submission_outbox` 仍为 `pending`、派发次数为 0 且
任务没有 Relay ID 时，才能证明请求从未发送，Worker 才会幂等释放预占并标记失败。
`retry`/`processing` 表示上游可能已接受但响应丢失，绝不直接退款。已有 Relay ID 时会先
查询权威状态：成功按原报价结算，失败/取消释放预占，仍在处理或查询失败则保留预占并
等待下一轮。这样可以避免超时扫描与成功结算竞态造成“已出片又退款”。

每次由超时 Worker 完成的终态处理都会写入不可变 `task_timeout_events`，并关联正常的
`SETTLE`/`RELEASE` 钱包流水。运维可使用以下受保护接口手动触发和分页查询：

- `POST /internal/tasks/timeout-scan`
- `GET /internal/tasks/timeout-events?page=1&page_size=50`

两者都必须携带 `X-Internal-Service-Token`。返回中的 `deferred` 任务不是失败，而是当前
无法安全判断；需要保持预占、修复 Relay 查询或继续用稳定幂等键恢复派发，禁止人工
直接改余额。两个超时阈值应大于生产渠道正常 P99 生成时间，并配合
`deferred` 数量、最老预占年龄和 Worker 存活告警进行调整。

前端只读数据可使用：

- `GET /api/v1/companies/{company_id}/models`
- `GET /api/v1/companies/{company_id}/tasks/{task_id}`

二者都会同时校验公司上下文、成员关系及逐项权限。

## 平台管理员边界

开发环境可通过 `POST /api/v1/bootstrap/platform-admin` 创建平台管理员，并在管理员
请求中携带 `X-Platform-Admin-User-ID`。服务端仍会查询 `users.is_platform_admin`；
公司老板身份和公司内角色无法替代平台管理员，也不能借此跨公司访问。该请求头仅是
开发期边界，生产环境会拒绝使用它，必须接入 JWT/SSO 后才能启用管理员端。

平台管理员首版支持：

- 分页创建、查询、启停公司；创建时自动建立老板、成员关系、老板角色和钱包
- 创建 `feature`、`agent`、`external_api` 资源定义并逐公司授权
- 对公司充值及配置模型授权和整数分价格
- 查询按公司的充值、消费、当前预占及任务成功失败聚合
- 查询不可变审计日志

新增资源不存在公司授权记录时默认关闭。渠道成本目前明确返回 `null`，状态为
`pending_relay_cost_data`；平台收入只按成功结算的 `SETTLE` 流水汇总，不推测或伪造
中转站成本。

管理员变更会写入追加式 `audit_logs`，包含操作者、动作、目标、变更前后摘要和
`request_id`。API 会接受或生成 `X-Request-ID` 并在响应中返回。

## 下载审计与公司报表

产物下载接口只有在 Relay 成功签发且平台完成 URL 安全校验后，才追加一条
`download_records`。记录包含公司、任务、产物、下载申请人、请求 ID、签发时间和
短时地址失效时间；不会保存带签名的下载 URL。签发只表示 `issued`，不会冒充下载完成。
来源固定且不进入公开 OpenAPI 的 EDGE/OBS 完成事件接口，会在内部服务令牌之外分别验证
独立 HMAC，再核对公司、任务、产物 SHA-256、完整字节数、HTTP 200、`full_body`、
签发时间和来源专属传输引用，之后才追加不可变 `download_completions` 并标记
`completed`。历史未签名行继续保留但不计入已下载。生产环境必须由真实受控边缘网关或
可信 OBS access-log 事件桥投递；目前仓库只提供 EDGE staging 验收程序，OBS 桥未验收前
必须保持阻断状态。

员工默认只能查询自己发起的任务、作品和下载记录；拥有 `reports.read` 的成员可以显式使用
`scope=company`。相关分页和筛选接口包括：

- `GET /api/v1/companies/{company_id}/download-records`
- `GET /api/v1/companies/{company_id}/task-history`
- `GET /api/v1/companies/{company_id}/artworks`
- `GET /api/v1/companies/{company_id}/reports/tasks`
- `GET /api/v1/companies/{company_id}/reports/consumption`

任务和消费报表支持 `employee_user_id`、`model_id`、`status`、`start_time`、
`end_time`。平台管理员消费报表还支持 `company_id` 与 `employee_query`，可跨公司检索。
时间必须带 UTC 偏移，区间为 `[start_time, end_time)`。消费报表只统计已成功结算的
`SETTLE` 流水，不把预占当消费，也不把失败任务计费；每行保留任务创建时的计费模式、
单价和计费数量快照。

拥有独立 `reports.export` 权限的成员可从相同筛选条件导出：

- `GET /api/v1/companies/{company_id}/reports/tasks/export.csv`
- `GET /api/v1/companies/{company_id}/reports/consumption/export.csv`

导出固定使用 UTF-8 BOM、禁用缓存并对电子表格公式前缀进行转义。单次最多 10,000
行；超出时返回 413，调用方必须缩小时间范围。所有查询始终附带当前公司条件，不能用
筛选参数跨公司读取数据。

## 测试

```powershell
python -m pytest
```

测试使用内存 SQLite，不需要 PostgreSQL 或 Redis。
## 生产 OIDC、BFF 会话与账号生命周期

客户平台原生使用外部 OIDC Authorization Code + PKCE。受保护的 staging/production
固定关闭浏览器 HS256 Bearer 兼容入口；浏览器只持有服务端可撤销的
`__Host-ai_video_session`（Secure、HttpOnly、SameSite=Lax）Cookie。所有 Cookie
认证的写请求还必须同时通过精确 `Origin`、`__Host-ai_video_csrf` Cookie 与
`X-CSRF-Token`。密码、恢复、MFA 与通行密钥由 IdP 管理，Platform 不保存本地密码。

Platform API 进程需要以下非秘密配置；OIDC client 是 PKCE public client，不配置
client secret：

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
```

IdP 必须签发 RS256 ID token，提供可轮换 `kid`/JWKS，并包含稳定 `sub`、已验证
`email`、`nonce`、`iat`、`exp`；平台管理员还必须提供 `auth_time` 与防钓鱼
`amr`。`PLATFORM_OWNER_USER_IDS` 保存的是 IdP `sub`，不是本地 User UUID。所有非只读
平台管理操作和账号停用都要求近期 step-up；普通密码或短信验证码不能加入强认证允许列表。

Platform API typed bundle 中历史命名的 `jwt_signing_secret` 现在同时作为会话、CSRF、
OIDC state、邀请 capability 与审计 IP 摘要的服务端 HMAC pepper。轮换它会刻意使现有
会话、未接受邀请和进行中的登录事务全部失效，因此只能按全局登出/重发邀请的受控变更执行。

账号由 `(issuer, sub)` 映射到稳定本地 User；全局 `pending/active/suspended/deactivated`
状态和 `auth_version` 会在每次请求重新核验。公司成员停用只撤销该公司范围；全局停用会
立即拒绝个人、其他公司和平台管理员范围并撤销全部服务端会话。新成员通过一次性、可过期、
可撤销、可重发的邀请加入，邀请 token 只放在 URL fragment 和 POST body，不进入查询参数。

浏览器只需要非秘密的 `platformApiUrl`。生产构建不再读取
`sessionStorage["ai-video.access-token"]`，也不得把 token、用户 ID、公司身份或管理员身份
写入 `VITE_*`、静态 `.env`、HTML、URL 或 `localStorage`。个人/公司切换只提交公司 ID，
服务端仍会重新核验有效 membership。

代码边界不替代外部系统验收：公网发布前仍必须用目标 IdP 完成 redirect、JWKS 轮换、
step-up、退出/全设备吊销和停用账号 canary；真实 Provider、生产 OBS 与可信支付也仍需
各自的上线证据。

当前 Alembic 唯一迁移 head 为 `0040_showcase_management`，直接前序为
`0039_new_api_relay_defaults`；`0038_download_evidence_checks` 与
`0037_production_auth_lifecycle` 继续保留为冻结前序。0040 新增仅 Platform Owner 可管理的
首页精选案例草稿、不可变发布版本和紧急下线事件；0039 仍只改变未来任务/outbox 的数据库默认
Relay affinity，不重写任何历史行。受保护 v5 catalog 的唯一 PostgreSQL 16 资格化值为
`ecd5b3faae20595e66396c59d37327d1e6e5b742c3d70697aaf6f109866591e6`。生产发布必须以独立迁移任务先执行
`python -m alembic upgrade head`、`python -m alembic check` 和
`python -m alembic current`，确认到达该 head 后再启动 API 与后台进程；不要把历史版本号
继续写成发布目标。

## 私有输入素材库

平台现在提供公司隔离的图片、视频和音频素材库。浏览器只提交素材 ID，绝不把对象键、
OBS 凭证或长期 URL 放进任务请求：

- `POST /api/v1/companies/{company_id}/assets`：multipart 上传，字段为 `file`，可选
  `media_type=image|video|audio`；需要 `assets.manage`。
- `GET /api/v1/companies/{company_id}/assets`：按 `status`、`media_type` 查询；需要
  `assets.read`。
- `GET /api/v1/companies/{company_id}/assets/{asset_id}/preview`：签发短时内联预览 URL。
- `GET /api/v1/companies/{company_id}/assets/{asset_id}/download`：签发短时下载 URL。
- `DELETE /api/v1/companies/{company_id}/assets/{asset_id}`：停用素材，不物理删除；仍被
  非终态任务引用时返回 409。
- `POST /api/v1/companies/{company_id}/tasks/{task_id}/artifacts/{asset_id}/input-asset?scope=mine|company`：
  把成功任务的 canonical 归档产物从 Relay 受控存储服务端校验并转存为私有输入素材；
  需要 `assets.manage`，`mine` 另需 `tasks.read` 且仅限本人任务，`company` 另需
  `reports.read`。请求 JSON 和 `Idempotency-Key` 使用同一个 8–120 位稳定幂等键；同源
  重放返回原素材，不同来源复用同键返回 409。此操作不预占或扣减钱包，也不创建生成
  Outbox；浏览器不会接触 Provider URL、对象键或存储凭据。

上传支持可选 `Idempotency-Key` 请求头（8–120 个无空白可见字符）。同一公司、上传人和
幂等键在文件 SHA-256、大小、MIME、媒体类型及原文件名完全一致时返回原素材；任一项不同
返回 409。默认单文件上限为 512 MiB，由 `INPUT_ASSET_MAX_BYTES` 调整。

创建任务时，`request_payload.assets` 必须使用：

```json
[{"asset_id":"<uuid>","media_type":"image"}]
```

平台会校验素材属于当前公司、仍为 active 且媒体类型一致，并写入不可跨租户的任务关联。
Outbox 先持久化受控素材引用；首次准备向 Relay 提交时签发一次短时 URL，并在 POST 前把
完整请求快照原子持久化。后续重试必须复用完全相同的请求和幂等键，避免签名时间变化造成
幂等冲突或重复收费任务。`INPUT_ASSET_RELAY_SIGNED_URL_SECONDS` 应覆盖最坏派发重试窗口；调用方
提交任意外部 `url` 会被拒绝。

开发环境可使用私有 filesystem：

```text
INPUT_ASSET_STORE=filesystem
INPUT_ASSET_FILESYSTEM_ROOT=/input-assets
INPUT_ASSET_PUBLIC_BASE_URL=http://127.0.0.1:8200
INPUT_ASSET_RELAY_BASE_URL=http://platform-api:8000
INPUT_ASSET_SIGNING_SECRET=<local-random-secret>
INPUT_ASSET_SIGNED_URL_SECONDS=300
INPUT_ASSET_RELAY_SIGNED_URL_SECONDS=3600
```

`INPUT_ASSET_PUBLIC_BASE_URL` 供浏览器访问，`INPUT_ASSET_RELAY_BASE_URL` 供容器网络内的
Relay 获取；它们与已退役的 Relay backend scalar `RELAY_BASE_URL` 无关，且不可混用。
filesystem 目录必须挂持久卷并只授予平台进程读写权限；它仅用于本地/测试，生产配置会拒绝
filesystem。

生产只允许 Huawei OBS 私有桶，并要求完整、非占位配置：

```text
INPUT_ASSET_STORE=huawei_obs
HUAWEI_OBS_ACCESS_KEY_ID=...
HUAWEI_OBS_SECRET_ACCESS_KEY=...
HUAWEI_OBS_ENDPOINT=https://obs.<region>.myhuaweicloud.com
HUAWEI_OBS_BUCKET=...
```

OBS 适配器上传时显式设置 private ACL，预览和 Relay 输入均使用最长 3600 秒的 HTTPS 签名
URL。构建包含官方可选 SDK 的镜像：

```powershell
docker build --build-arg INSTALL_OBS=true -t ai-video-platform .
```

代码具备 OBS 适配与严格启动校验，不代表真实生产桶、AK/SK、跨地域网络、CORS、生命周期和
告警已验收；完成 staging 实桶演练之前仍不得标记为生产可用。
