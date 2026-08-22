# AI 视频生成平台：双平台架构基线

状态：扩展版 `new-api` 是唯一活动生产 Relay；Python 实现仅保留为离线行为 oracle/归档 artifact，不具备生产准入路径
适用范围：客户管理平台、统一生成中转站、内部 TikTok 运营系统接入

## 1. 系统边界

### 客户管理平台（Control Plane）

职责：

- 浏览器登录和会话管理。
- 公司、成员、老板/组长/运营角色管理。
- 逐项权限、公司级模型/功能/智能体/API 授权。
- 模型目录、对外名称、能力声明和版本化价格。
- 公司钱包、充值、预占、结算、释放和对账。
- 生成任务、产物、下载记录、报表和审计。
- 在通过鉴权、授权和余额校验后调用中转站。
- 接收并验证中转站 HMAC 主动回调，按事件编号幂等更新任务和钱包。

非职责：

- 不直接实现供应商协议。
- 不持有逆向渠道账号池。
- 不根据供应商返回值自行推断底层渠道健康度。

### 统一生成中转站（Data Plane）

目标实现以固定 revision 的 `QuantumNous/new-api` 为渠道网关与运维控制面，在其上增加本产品的
`/v1/generations`、能力 revision、耐久队列、租约 fencing、产物转存、签名回调、Provider
监控和渠道成本事件。Python Relay 的冻结测试只用于离线比较公开合同；受保护运行时不得
配置、启动或认证 Python 数据面。上游 `new-api` 的相似功能也不能替代本产品已经冻结的
安全不变量。

职责：

- 对客户平台和内部 TikTok 系统提供同一套版本化生成 API。
- 把文生视频、图生视频、文生图等请求转换为渠道适配器调用。
- 通过 v1 动态工厂插件统一装载逆向、第三方 API 和官方渠道；适配器逐
  `model + mode` 声明能力，API 与 Worker 启动时统一校验，现有 v1 范围内新增渠道不在
  上层增加供应商分支。
- 维护渠道、账号池、限流冷却、健康度、路由和故障切换。
- 用独立 Monitor Worker 持久化每路由健康、数据库级只追加的真实上游终态和 Provider
  告警状态，向外部值班接收端投递签名触发/恢复事件；监控只观测，不迁移存量任务。
- 异步提交、轮询或接收供应商回调。
- 统一任务状态、错误码和产物信息。
- 通过服务认证的模型目录按生成模式发布物理能力、稳定内容 revision 与 ETag；共享模型只发布所有可故障切换路由都保证的安全交集。
- 将临时产物转存到华为云 OBS，再通过持久化 HMAC 回调通知调用方。
- 对回调执行租户地址白名单、指数退避重试、死信和投递状态查询。

非职责：

- 不决定外部客户价格。
- 不直接修改客户平台余额。
- 不承载公司成员和逐项权限。

## 2. 请求链路

模型能力先形成独立的控制闭环：平台管理员读取 Relay `/v1/models`，把平台对外能力
与 Relay 物理上限比较；平台只能保持一致或收紧，不能扩张。管理员确认后，平台保存
`capability_revision`，公司模型接口继续返回平台的生效子集，创建任务时把确认过的
revision 固化进任务与 Outbox。Relay 在任何 Provider POST 之前复核 revision；漂移时
返回明确错误并停止外部调用，避免界面、报价和真实渠道悄悄使用不同能力。
Relay 还会按公开的故障切换安全交集再次做请求准入，不能利用某条更宽的私有主路由
绕过目录限制；提交结果未知时禁止跨账号或跨渠道重提。

```text
浏览器
  -> 客户平台：身份认证
  -> 客户平台：公司状态、个人权限、公司授权、参数能力校验
  -> 客户平台：计算最高可能费用并预占公司余额
  -> 客户平台：使用幂等键调用中转站
  -> 中转站：选择渠道和账号，异步执行
  -> 中转站：把成功产物转存至华为云 OBS，并用 HEAD 核对对象元数据
  -> 中转站：持久化状态事件并向客户平台发送 HMAC 主动回调
  -> 客户平台：验签、校验时间窗和任务映射，按事件 ID 幂等落库任务及不可变产物索引
  -> 客户平台：成功结算实际费用；失败或取消释放预占
  -> 浏览器：按本人默认范围查询任务历史、作品和短时下载地址
  -> OBS 日志/边缘网关：完整传输后提交可信下载完成事件
```

客户平台的独立同步进程继续轮询中转站，但它是回调延迟、死信和网络故障时的
兜底对账链路，不是正常状态同步主链路。回调和轮询必须复用同一套状态约束、
预占结算和失败释放服务，先后顺序不得影响最终结果。

内部 TikTok 系统直接调用中转站，但必须使用独立的服务凭证、调用方标识、
限流策略和回调密钥。它不经过客户平台，因此也不使用客户平台钱包。
生产启动会拒绝两个服务客户端复用同一租户；可信 `client_id` 随 Relay 任务持久化，
但不返回给外部任务查询或回调。

生成提交、产物转存、Provider 状态轮询和租户回调都必须按任务使用持久化 lease 与
随机 token fencing。过期 Worker 可以完成已经发出的网络请求，但不能再覆盖新 Worker
写入的终态；幂等键仍负责请求级去重，两者不能互相替代。

## 3. 部署单元

| 单元 | 技术 | 建议暴露范围 |
| --- | --- | --- |
| Web 前端 | React/Vite，Sites 托管 | 公网 |
| 客户平台 API | FastAPI | 公网业务 API，以及仅供已验签 Relay 调用的回调入口 |
| Python Relay（离线 oracle artifact） | FastAPI + Python Worker | 不属于生产部署；仅隔离测试使用测试数据库/Redis 比较历史合同 |
| 扩展版 new-api Relay（唯一活动数据面） | Go/new-api + 本产品兼容层与耐久 Worker | 内网；独占生产生成准入、PostgreSQL、Redis、artifact/OBS 与 release proof |
| Relay Callback / Monitor Worker | 与当前活动 Relay 同版本的耐久进程 | 内网运行；按租户策略访问回调、Provider 和告警接收端 |
| PostgreSQL | 两个逻辑数据库或独立 schema | 内网 |
| Redis | 队列、锁、限流和短期缓存 | 内网 |
| 华为云 OBS | 输入素材和长期产物 | 通过服务端或短时授权访问 |
| Nginx | TLS、反向代理、请求大小和超时边界 | 公网入口 |

第一阶段允许客户平台 API 和中转站 API 共享一台 PostgreSQL 实例，但必须使用
不同数据库账号和 schema。生产环境不能让浏览器直接访问中转站、数据库、
Redis、OBS 永久凭证或任何供应商密钥。

### Relay 生产边界

客户平台受保护运行时只认识一个 backend/contract：`new-api-v1 / generations.v1`。
Platform API、dispatcher、relay-sync 和 timeout-worker 的最小 secret bundle
分别携带同一 release 的调用凭据；配置多个 backend、`legacy-default-v1`、旧
`RELAY_BASE_URL` fallback 或 Python credential 都必须在文件、proof 和数据库读取前失败。
new-api 使用隔离的 PostgreSQL、Redis、artifact/OBS namespace 和 generation-bound release
proof；浏览器和其他 Platform 进程不能直接访问这些数据服务。

Platform 自 `0033_relay_backend_affinity` 起已在 task/outbox 上持久化 backend 与合同 revision。
新任务只能固定为 `new-api-v1`；历史 legacy affinity 只保留审计，不能改写、重投或配置一个
可调用的 Python backend。生产回滚只能使用上一版 schema-compatible new-api 不可变镜像，
不能把 URL 切回 Python。一次性排空、发布顺序和回滚限制见
[new-api Relay 生产切换与回滚合同](relay-new-api-migration.md)。

## 4. 核心数据归属

### 客户平台

- `companies`
- `users`
- `memberships`
- `permission_definitions`
- `role_permission_grants`
- `member_permission_overrides`
- `models`
- `model_capabilities`
- `price_versions`
- `company_entitlements`
- `company_wallets`
- `ledger_entries`
- `credit_reservations`
- `generation_tasks`
- `task_artifacts`
- `input_assets`
- `download_records`
- `download_completions`
- `recharge_orders`
- `api_keys`
- `audit_logs`
- `relay_callback_events`

### 中转站

- `relay_clients`
- `relay_jobs`
- `provider_routes`
- `providers`
- `provider_models`
- `provider_accounts`
- `provider_attempts`
- `provider_webhook_events`
- `artifact_transfers`
- `callback_deliveries`
- `provider_health_samples`
- `provider_outcome_events`
- `provider_alert_states`
- `provider_alert_events`
- `provider_monitor_lease`

## 5. 不可破坏的业务不变量

### 多租户

1. 所有公司数据查询必须显式包含 `company_id`。
2. `company_id` 从已验证的会话或 API 密钥中获得，不能信任浏览器提交值。
3. 资源 ID 即使猜中，也不能绕过公司边界。
4. 管理员跨公司操作必须记录审计日志。

### 权限与授权

1. 角色只是默认模板，个人允许 `allow` 或 `deny` 覆盖。
2. 个人有效权限的优先级为：显式个人覆盖 > 角色模板 > 默认拒绝。
3. 员工有操作权限并不代表公司已获模型或功能授权，两层必须同时通过。
4. 新增权限定义默认对所有公司和成员关闭。

### 计费

1. 金额统一使用整数最小货币单位，禁止浮点数。
2. 账本只追加，不原地修改历史流水。
3. 每个生成任务最多有一个有效预占。
4. 成功任务只能结算一次。
5. 失败、取消或超时任务不得产生消费，已有预占必须释放。
6. 重复请求、重复回调和重复支付通知不得重复扣款或充值。
7. 价格在任务创建时快照，后续调价不能改变历史任务费用。

### 生成任务

1. 对外任务 ID 不暴露供应商任务 ID。
2. 同一调用方和幂等键只能创建一个逻辑任务。
3. 状态只能按状态机允许的方向变化。
4. 供应商临时 URL 不能作为长期产物地址。
5. 成功回调必须发生在产物转存验证成功之后。
6. 无法确认供应商 POST 是否创建任务时必须进入 `reconciliation_required`，禁止自动重提或伪造失败状态；该待对账状态本身可以主动回调调用方。
7. `reconciliation_required` 不得结算或释放预占；只有核实供应商未创建任务后才可失败释放，核实已创建则绑定供应商任务并恢复轮询。
8. 任务、详情、作品和下载默认只对发起人可见；公司范围必须显式请求并通过报表权限校验。
9. 签名 URL 签发只表示 `issued`；只有可信事件证明完整字节传输后才能表示 `completed`。

### 主动回调

1. 浏览器不能指定回调地址；客户平台从服务端配置注入，Relay 再按认证租户的精确地址策略授权。
2. 生产回调只允许公网 HTTPS 443，禁止凭证、查询参数、片段、重定向和私网/回环解析结果。
3. Relay 使用 `X-Relay-Timestamp`、`X-Relay-Event-ID` 和原始请求体计算 `v1` HMAC-SHA256；客户平台必须在解析 JSON 前验签。
4. Relay delivery、客户平台 event receipt 都必须持久化。相同事件编号和相同消息体只能应用一次；相同编号对应不同消息体必须拒绝。
5. 非 `2xx` 和网络错误按指数退避重试，达到上限后进入死信；轮询兜底负责最终对账，但不能掩盖死信告警。
6. 回调、轮询和超时补偿无论以何种顺序到达，都不能重复结算或释放余额。

## 6. 环境

- `local`：本地 PostgreSQL、Redis 和模拟渠道。
- `staging`：独立数据库、存储路径、密钥、供应商测试额度和真实公网 HTTPS 回调域名。
- `production`：真实资源；禁止复用 staging 密钥，回调地址和密钥按租户独立注入。

所有环境使用同一套镜像，通过环境变量和密钥管理注入差异。真实供应商密钥、
数据库密码、OBS 密钥、JWT 密钥和回调密钥不得进入代码库。

主动回调在代码、本地测试和跨服务契约层已经闭环，但生产发布前仍必须通过真实
外网演练：Relay 出网、防火墙、DNS 公网解析固定、TLS、无重定向、反向代理原始
请求体、双方密钥、时钟同步、超时重试、重复事件、死信告警和轮询补偿都要验证。
未通过真实 IdP、Provider、OBS、告警、支付、备份恢复与容量演练时，公网商用状态仍为
`NO-GO`。该部署外部结论不改变 new-api 作为唯一活动 Relay 的软件合同。

## 7. 第一阶段完成定义

第一阶段不是“正式上线”，而是证明架构闭环：

1. FastAPI Platform 与 Go/new-api Relay 能独立启动并通过各自健康检查。
2. 客户平台能够创建公司、成员、授权和模型配置。
3. 钱包可以完成充值、预占、成功结算和失败释放，并通过并发/幂等测试。
4. 客户平台使用统一契约向模拟中转站提交任务。
5. 中转站能够通过适配器选择模拟渠道并返回标准状态。
6. 失败任务不会扣费。
7. 前端不再把浏览器内存状态当作真实任务状态。
8. 全部服务可在本地容器环境启动，自动化测试通过。
9. Relay HMAC 回调、客户平台验签接收、事件幂等、重试和死信可在本地契约测试中闭环；生产公网验证单独作为发布门禁。
10. Provider Monitor 能持久化健康样本，按连续周期产生成功率/大面积失败/批量账号失效的
    触发与恢复事件，并在未知提交时保持“绝不跨渠道重提”的安全边界。
