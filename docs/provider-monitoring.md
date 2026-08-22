# 历史 Python Provider 监控语义（离线 oracle）

> **冻结说明**：本文保留旧 Python Relay 的故障切换、未知提交、健康样本和告警状态机，
> 仅供隔离回归与 new-api parity 审查。它不是生产启动或运维手册；当前发布、进程、proof、
> route 和告警配置以 `new-api-production-deployment.md` 为准。

更新时间：2026-08-05

本文说明冻结 Python Relay 5.4 曾实现的运行语义：同一公开能力如何在多个路由间安全
切换、Monitor 如何持久化健康与成功率证据，以及哪些不变量必须由当前 new-api 实现继承。

## 1. 边界与原则

高可用由两条相互独立的链路组成：

- **请求路径**：Provider Router 按公开能力、账号准入、冷却、RPM、健康检查和优先级选择
  路由；只有能够证明上游没有创建任务时，才允许切换账号或渠道。
- **观测路径**：独立的 `provider_monitor_worker` 定时探测每个具体路由，汇总真实上游终态，
  生成去重的触发/恢复事件，并可投递到签名 Webhook。监控不自动停用账号、不修改冷却，
  也不接管正在执行的任务。

同一公开模型别名由多条路由承载时，Relay 只发布这些路由都能保证的能力交集。请求只会在
兼容该公开能力的候选路由之间切换，不能借备用路由扩大分辨率、时长、素材数量或其他
能力。

## 2. 安全故障切换

### 2.1 提交前

冻结 Router 曾并行检查候选路由，单路健康检查受固定超时限制。禁用、冷却、达到活跃任务上限或本地
固定窗口 RPM 上限的账号不接收新任务；若还有兼容候选，Router 继续选下一条路由。所有
账号都只是暂时忙、冷却或本地限流时，Generation Worker 使用 Redis 延迟队列重新投递，
不会消耗 Provider 提交尝试次数。

健康检查是只读信号。一次失败不会直接增加账号失败计数、关闭准入或迁移存量任务。

### 2.2 Provider 已返回错误

适配器必须通过 `ProviderError.failure_scope` 声明**已经证明未创建任务**的故障范围：

| 范围 | 含义 | 当前任务的安全行为 |
| --- | --- | --- |
| `request` | 只影响当前请求；通常是可重试的临时拒绝 | 仅当 `retryable=True` 时继续下一兼容路由 |
| `account` | 当前账号不可用 | 记录账号失败，按阈值冷却或永久停用，再尝试其他账号/渠道 |
| `channel` | 同一 Provider 的其他账号也不值得继续尝试 | 当前提交跳过该 Provider 的其余账号，尝试其他兼容 Provider |

用户参数错误、模型不支持等不可重试错误立即失败，不做跨渠道尝试。永久鉴权失效只有在
上游明确拒绝且确认未创建任务时，才可设置 `disable_account=True`。

### 2.3 绝不自动切换的情况

POST 超时、断连、畸形成功响应、提交成功后本地落库失败，或者账号分配的持久化结果无法
确认时，外部任务可能已经创建。适配器必须返回
`submission_outcome_unknown=True`；Relay 固定保留原 `provider@account_id` 和账号槽，
进入 `reconciliation_required`，**不会重新提交，也不会切换到备用账号或渠道**。

这一限制优先于可用性。否则一次网络抖动就可能在两个渠道各创建一条付费任务。只有运营
在供应商后台证明未创建后，才能通过受保护的对账流程结束任务并释放槽位；确认已创建则
绑定原渠道任务号并恢复粘性轮询。

已经接受的任务始终轮询原 `provider@account_id`。账号冷却、永久失效、人工 drain 或新
任务切换都不能把存量任务迁移到另一个渠道。

## 3. Provider Monitor

历史实现使用独立 Monitor Worker；本文故意不提供启动命令、镜像或生产环境变量。离线
oracle 测试只能使用临时 PostgreSQL/Redis 与合成 Provider。冻结 Worker 每个周期曾执行：

1. 在 `provider_monitor_lease` 领取带随机 token 的全局租约；多个副本中只有一个执行本轮。
2. 并行调用每个具体路由的 `healthcheck()`，不按 Provider 名做 OR 聚合，也不跳过人工
   drain 的路由。
3. 只保存稳定、非敏感的路由/账号标识、渠道类型、健康布尔值、规范化错误码、延迟和准入
   状态；不保存异常文本、Endpoint、Cookie、Token 或原始响应。
4. 汇总窗口内 `provider_outcome_events`，计算 Provider 级成功率。
5. 在同一数据库事务中校验租约 token、写入健康样本、裁剪过期样本、推进告警状态机并
   生成触发/恢复事件。过期 Worker 不能提交本轮结果。

上游成功在任务进入 `transferring` 时记录，上游明确失败或取消记录为失败。后续 OBS 转存
失败不会反向污染 Provider 成功率；同一任务最多记录一条上游终态。该终态是数据库级
只追加审计证据：generation job 外键使用 `RESTRICT`，PostgreSQL 拒绝 `UPDATE`、
`DELETE` 和 `TRUNCATE`，SQLite 拒绝 `UPDATE` 和 `DELETE`。

迁移 `0011_provider_monitoring` 在 `0010_provider_account_pool` 之上新增：

| 表 | 用途 |
| --- | --- |
| `provider_health_samples` | 每轮、每具体路由的健康与延迟样本；按保留期清理 |
| `provider_outcome_events` | 每个 generation job 一条、幂等且数据库级只追加的上游终态；job 外键禁止级联删除 |
| `provider_alert_states` | 每个 Provider/规则的活动状态及连续触发/恢复计数 |
| `provider_alert_events` | 持久化触发/恢复事件及 Webhook 投递状态 |
| `provider_monitor_lease` | 多副本周期互斥、最小周期和 token fencing |

迁移还为 `provider_account_states` 增加 `admission_disabled_reason`，用来区分人工 drain 与
Provider 错误导致的永久停用。

## 4. 默认告警规则

规则按稳定 `provider_name` 聚合。连续达到 `breach_cycles` 才产生一个 `triggered` 事件；
告警活动期间不重复产生触发事件。连续达到 `recovery_cycles` 后产生一个 `recovered` 事件。
默认两者都是 2 个周期。

| 规则 | 默认判定 | 严重级别 | 证据不足时 |
| --- | --- | --- | --- |
| `provider_success_rate_drop` | 最近 300 秒至少 20 个真实上游终态，成功率低于 80% | `warning` | 不推进触发或恢复计数 |
| `widespread_channel_failure` | 本轮至少 2 条具体路由，健康失败比例达到或超过 50% | `critical` | 不推进触发或恢复计数 |
| `batch_account_invalidation` | 同一 Provider 至少 3 个账号因 `provider_error` 关闭新准入 | `critical` | 账号数低于阈值即作为恢复证据 |

批量账号失效不统计人工 drain，也不把临时冷却当作永久失效。健康探测异常会使用
`HEALTHCHECK_UNHEALTHY`、`HEALTHCHECK_TIMEOUT`、`HEALTHCHECK_INVALID_RESPONSE` 或
`HEALTHCHECK_FAILED` 等规范化代码，不把 Provider 原始异常写入数据库和普通日志。

冻结 oracle 的默认行为如下；环境变量名已删除，避免把本页当作生产配置清单：

| 参数语义 | 历史默认值 | 说明 |
| --- | ---: | --- |
| Monitor 必须启用 | `true` | 受保护准入不能关闭监控 |
| 最小周期 / 成功率窗口 | `30s / 300s` | 窗口必须覆盖多个周期 |
| 最小终态样本 / 成功率下限 | `20 / 0.80` | 样本不足不推进触发或恢复 |
| 大面积失败比例 / 最小路由 | `0.50 / 2` | 按具体路由而非 Provider 名做 OR |
| 批量永久失效账号数 | `3` | 不统计人工 drain |
| 连续触发 / 恢复周期 | `2 / 2` | 去重状态机 |
| 周期租约 / 样本保留 | `120s / 30d` | 租约不短于周期并大于探测超时 |
| 计划退役集合 | 空 | 必须显式产生恢复证据 |

阈值必须基于真实渠道基线和业务量调整。低流量 Provider 若长期达不到最小样本数，成功率
规则不会自行恢复；仍应依赖健康探测、任务年龄和外部业务监控。

Provider 从路由配置中消失不会被自动当成恢复，避免误删配置掩盖事故。计划下线时，先移除
真实路由，再把稳定 Provider 名加入当前 new-api 的显式退役集合；Monitor
会忽略仍在滚动窗口内的历史终态，并按正常恢复周期关闭遗留活动告警。仍有健康样本的
Provider 不得同时标记为 retired，启动和周期检查都会拒绝这种冲突。完成恢复事件投递后可在
后续发布中清理该显式列表。

## 5. 签名告警 Webhook

冻结实现要求告警 HTTPS URL 与独立签名密钥成对存在。当前 new-api 必须从最小权限 secret
bundle 获取这两项，精确字段见其生产部署合同；本页不提供可复制的环境变量或 secret
样例。地址必须是规范化的公网 `https://` 443 地址，不能包含用户名、密码、query 或
fragment；发送端拒绝重定向并固定已校验的公网 DNS 结果。告警密钥必须和租户任务回调
密钥分开。

请求头：

```text
Content-Type: application/json
X-Relay-Alert-ID: <event UUID>
X-Relay-Alert-Timestamp: <Unix seconds>
X-Relay-Alert-Signature: v1=<HMAC-SHA256 hex>
```

签名输入是原始字节 `timestamp + "." + event_id + "." + body`。接收端必须先检查时间窗，
再用原始请求体做常量时间验签，并按 `X-Relay-Alert-ID` 幂等去重。`triggered` 和
`recovered` 使用不同事件 ID，均需接收。正文版本为 `1`，包含事件类型、严重级别、状态、
Provider 名、fingerprint 和本轮无秘密观测值。

示例正文：

```json
{
  "fingerprint": "provider_success_rate_drop:acme",
  "id": "8efcb561-d99b-4ac5-a82a-35391803405b",
  "object": "relay.provider_alert",
  "observed": {
    "failed": 8,
    "succeeded": 22,
    "success_rate": 0.7333333333333333,
    "threshold": 0.8,
    "total": 30,
    "window_seconds": 300
  },
  "occurred_at": "2026-08-05T10:00:00+00:00",
  "provider": {"name": "acme"},
  "severity": "warning",
  "status": "triggered",
  "type": "provider_success_rate_drop",
  "version": "1"
}
```

发送端使用无多余空白、键排序后的 UTF-8 JSON；验签必须使用收到的原始字节，不能把 JSON
解析后重新序列化再验签。

投递成功条件是 HTTP `2xx`。失败采用持久化 claim 和指数退避，默认最多 8 次、首个延迟
5 秒、最大延迟 900 秒；达到上限进入 `dead_letter`。发送至少一次，接收端不能依赖“只会
收到一次”。冻结参数语义：

| 参数语义 | 历史默认值 |
| --- | ---: |
| HTTP 超时 | `5s` |
| 最大尝试 | `8` |
| claim 租约 | `60s` |
| 首次 / 最大退避 | `5s / 900s` |
| 投递轮询 | `0.5s` |

投递 claim 租约必须大于端到端告警 HTTP 超时，默认 60 秒对 5 秒；过期发送者不能确认或
改写新持有者的结果。claim 本身不消耗失败次数：Worker 无论在 HTTP POST 前崩溃，还是在
POST 后、确认前崩溃，租约过期后都会用同一事件 ID 至少再投递一次；只有已知的 HTTP/传输
失败才增加 attempts 并消耗最大尝试预算。历史开发 fixture 缺 Webhook 时只保存 pending
事件；当前 new-api 受保护模式必须启用 Monitor 并成对配置规范化告警 URL/签名密钥，任一
缺失都应在任何外部准入前失败。

## 6. Readiness 语义

`GET /health/ready` 每次直接检查账号准入和 Provider 健康，并按 Provider 名聚合：同一
Provider 有任一可接新任务的健康账号时，该 Provider 依赖为 `healthy`。Provider 是新任务
准入依赖，不是 Relay API 进程依赖：

- PostgreSQL、Redis、转存队列或产物存储不可用时返回 `503 unavailable`。
- 持久化依赖可用、但所有 Provider 都不能接新任务时返回 HTTP `200`、`state=degraded`。
  这样网关仍可把 Provider 回调、任务查询、产物访问和未知提交对账送到 Relay。
- 只要至少一个 Provider 可用，生产环境的整体状态可以是 `healthy`；单个不可用 Provider
  仍会出现在 dependencies 中。
- 冻结 oracle 的旧本地 fixture 使用开发模式和文件系统产物仓库，整体 `degraded` 是预期
  结果；当前 new-api Compose 不从该 fixture 继承配置。

受保护 new-api readiness 还必须报告 `provider_monitor` 依赖，包括最近成功周期及年龄、
活动告警数、待投递数及最老待投递时间、死信数及最老死信时间。周期新鲜度阈值为
`max(monitor_lease, monitor_interval * 3)`；待投递事件超过
`alert_max_delay + monitor_interval` 也视为
陈旧。没有成功周期、周期陈旧、有活动告警、有任意死信、待投递陈旧或未配置告警接收端时，
`provider_monitor` 为 `degraded`；状态查询失败时该依赖为 `unavailable`。

这些监控异常只把 Relay 整体状态降为 `degraded`，仍返回 HTTP `200`；只有 PostgreSQL、
Redis、转存队列或产物存储等持久化依赖不可用才返回 `503 unavailable`。这样不会因告警或
Monitor 故障摘掉全部 API 副本，存量回调、查询和对账仍可进入。它是基于数据库状态推导的
周期新鲜度和投递积压，不是 Worker 进程的直接心跳；默认容器健康检查只看 HTTP 成功，
也不会因 `200 degraded` 自动重启。生产编排和独立外部监控仍必须解析 readiness JSON、
监控 Worker 进程与重启次数，并对陈旧周期、活动告警、待投递积压和死信单独报警。

## 7. new-api 继承合同与离线诊断

生产发布不得执行 Python Alembic、加载 Python 工厂或启动旧 Worker。当前顺序固定为
new-api root/bootstrap → database role-pre → migration → role-post/proof → service principals
→ API/workers/edge，并按 [new-api 生产部署门禁](new-api-production-deployment.md)验收。
new-api 必须继承以下结果，而不是复用旧进程：

1. 配置并验证独立告警 Webhook；接收端先验签、去重，再映射到值班系统。
2. 确认每个真实路由都有新鲜健康样本，Monitor 租约按期更新，告警表没有持续增长的
   `pending`/`dead_letter`。
3. 用同一能力的至少两个真实 staging 渠道执行故障演练，再逐步放量。

以下旧表查询仅可用于临时 Python oracle 数据库的差异调查，不能对 new-api 或生产数据库
执行，也不能成为当前发布证据：

```sql
SELECT provider_name, route_id, healthy, admission_enabled,
       admission_disabled_reason, error_code, latency_ms, checked_at
FROM provider_health_samples
ORDER BY checked_at DESC
LIMIT 100;

SELECT fingerprint, kind, provider_name, active, breach_count, recovery_count,
       opened_at, resolved_at, last_observed_at
FROM provider_alert_states
ORDER BY last_observed_at DESC;

SELECT id, kind, event_type, provider_name, occurred_at, delivery_status,
       attempts, response_status, last_error
FROM provider_alert_events
ORDER BY occurred_at DESC
LIMIT 100;
```

告警触发时：

1. 先区分是健康检查失败、真实终态成功率下降，还是多个账号因 Provider 错误被永久停用。
2. 核对同窗口任务、Provider 状态页、账号配额/鉴权、429/5xx、内部 DNS 与出网；不要只
   看一次健康探测就手工改任务状态。
3. Router 会绕开不健康、冷却或已停用的新任务路由。不要迁移或重提存量任务，尤其不要
   对 `reconciliation_required` 做跨渠道补单。
4. 若需人工 drain/恢复账号，必须走有鉴权和审计的控制面；不要直接更新
   `provider_account_states`。
5. 修复后等待连续恢复周期，确认 `recovered` 已持久化并成功投递，再结束事故。

告警投递进入 `dead_letter` 时，先修复接收端 TLS、DNS、验签或响应状态，并保留事件 ID。
当前版本没有公开的死信重放 API/CLI，不要直接篡改数据库状态；由受审计的运维流程按原始
事件 ID 补发，或在后续版本增加 token-fenced redrive 工具。必须为 `dead_letter` 行数量和
最老年龄配置独立外部告警，否则告警链路故障会静默。

## 8. new-api Staging 故障演练

至少覆盖：

- 主路由健康检查失败，新的兼容任务转到备用 Provider；恢复后仍满足公开能力交集。
- 已明确未创建任务的账号级 429/鉴权失败绕到下一账号；Provider 级 5xx 使用
  `failure_scope=channel` 时跳过同 Provider 其余账号。
- POST 结果未知时只进入 `reconciliation_required`，备用渠道调用次数保持为零。
- 两个 Generation Worker 竞争时不超卖账号槽；本地 RPM 饱和延迟重投且不增加 Provider
  尝试次数。
- 人工 drain 不触发批量失效；三个 Provider 错误停用账号连续两轮后触发 critical，恢复
  两轮后发送 recovered。
- 构造足够真实上游终态，验证成功率触发/恢复以及 OBS 转存失败不计为 Provider 失败。
- 告警接收端返回 5xx/超时，验证指数退避、claim 过期接管、最大尝试和 dead-letter。
- 多个 Monitor 副本同时运行时，一轮只有一个租约持有者提交样本与状态迁移。
- 所有 Provider 不可用时 readiness 为 `degraded` 而非 `503`，回调、查询和对账仍可访问。

## 9. 冻结 artifact 限制与当前生产责任

- Python artifact 中可灵、阿里百炼和火山方舟适配器永久保持
  `production_ready=False`；其代码和本地 Mock 只证明历史机制，不是真实渠道可用性。
- 当前 Compose 不包含 Python Monitor。生产监控只允许由 new-api 服务与受保护外部告警链
  提供，离线 oracle 不能拥有 production credential、DNS、Ingress 或 release proof。
- 成功率和大面积故障当前按 `provider_name` 聚合，不按模型、模式、地区或错误码单独告警；
  这些维度仍需外部指标平台补充。
- 当前没有 Provider 告警管理 API/管理员页面、审计化账号 drain 控制面或 dead-letter
  redrive 工具；事件可从 PostgreSQL 只读查询并经通用 Webhook 接入外部值班系统。
- 当前 new-api 生产配置必须 fail-closed：不能关闭 Monitor，也不能省略告警 Webhook/密钥；但这只能
  证明配置存在，不能证明接收端、值班路由和 Worker 本身持续可用。
- Readiness 会根据最近成功周期、活动告警、待投递年龄和死信推导 `provider_monitor`
  状态，但不会直接证明 Worker 进程存活，并且监控降级仍返回 HTTP `200`。数据库整体
  不可用时，数据库内的监控也无法自我告警。生产必须由编排平台和独立外部监控解析
  readiness 内容，并检查进程、数据库、Redis、OBS、队列年龄和告警投递年龄。
- `healthcheck()` 的质量取决于适配器实现。当前内置 Kling 与 Alibaba Wan 因供应商没有
  文档化的免费健康端点，只做本地配置检查并返回 `True`；它们的“大面积健康失败”规则
  不能证明真实上游可用，主要故障证据仍来自真实终态成功率和请求错误。生产验收必须增加
  合规的外部 synthetic canary，或实现经供应商确认的只读探针；不能把恒真检查当作 SLA。
- 其他真实渠道上线前也必须证明健康检查覆盖可生成的账号/服务状态，而不是一个永远返回
  200 的公共首页。
- 多 API/Worker 副本、跨可用区 PostgreSQL、Redis 高可用、内部负载均衡、OBS、Secret
  Manager、日志/指标采集和通用 Webhook 到实际值班系统的映射，均由生产基础设施负责；
  本仓库只提供共享状态、租约 fencing、故障切换和持久化告警语义。
