# 历史 Python 逆向渠道号池语义（离线 oracle）

> **冻结说明**：本文只保存旧 Python Relay 的账号池与未知提交不变量，供隔离回归和
> new-api parity 审查。不得据此安装公司 Python 包、配置真实账号、执行 Python migration、
> 启动 Python Worker 或恢复生产准入。当前 route 接入与发布只走 new-api 门禁。

更新日期：2026-08-05

本文说明历史实现曾要求的账号调度和安全隔离语义，不是当前接入手册；也不包括逆向、
补号、验证码绕过、设备破解或账号来源处理。

## 1. 当前实现边界

Relay 已提供多 Worker 共享的账号池核心：

- 每个真实账号对应一个 `ProviderAdapter` 实例和稳定的
  `provider_name@account_id` 路由；上层永远只看到统一模型能力。
- PostgreSQL `provider_account_states` 保存不含秘密的账号准入、优先级、并发、RPM、
  冷却和调度统计；`0011_provider_monitoring` 还区分人工 drain 与 Provider 错误停用，并
  持久化路由健康、上游终态和告警。
- 账号分配在 Provider POST 前完成，并与 generation job 的 submission claim token 一起
  受数据库锁保护。多个 Generation Worker 不会把同一个槽位同时卖给两个任务。
- 选中路由随任务持久化。提交成功、提交结果未知或进入对账后都不能换账号；主动轮询
  始终回到原账号。
- 只有已证明上游没有创建任务的错误才允许释放分配并切到下一个账号。上游明确终态后
  释放活跃槽；产物转存阶段不再占 Provider 生成槽。
- 账号池忙、RPM 到顶或所有候选仍在冷却时，工作项进入 Redis 延迟队列，稍后按
  原 `attempt` 再试，不会在几秒内耗尽 Provider 提交重试次数。

这不等于任何真实逆向渠道已接通。公司未交付具体 API 文件、账号、合法使用范围和
staging 环境前，只能验收抽象层和模板。

## 2. 安全隔离

未经逐行安全评审的交付文件默认放在独立 sidecar 或独立内部服务中运行，不直接 import
进 Relay 主进程。该进程应使用非 root 用户、只读文件系统、最小出网白名单、CPU/内存/
进程数限制和独立日志脱敏策略，只能访问它负责的账号秘密及上游域名。

Relay 适配器只调用 sidecar 的窄内网接口，例如：

```text
POST /internal/tasks       -> provider_task_id + correlation_id
GET  /internal/tasks/{id}  -> status + progress + temporary HTTPS outputs
GET  /internal/health      -> strict boolean health
```

sidecar 不得获得 Relay 数据库、Redis、租户 API key、回调签名密钥、OBS 密钥或其他账号
秘密。请求中只传生成所需字段和 Relay 生成的关联 ID，禁止传公司、员工、余额、计费或
客户回调信息。返回内容必须经过严格 schema 校验，不能把任意上游响应透传到日志或 API。

只有通过代码审计并确认依赖、网络行为、日志和升级来源后，才可评估同进程加载；即便
同进程加载，密钥隔离和错误脱敏要求也不降低。

## 3. 冻结的一账号一路由语义

历史 oracle 用一个实例表达一个合成账号；离线 fixture 只允许使用假 `account_id` 与假
credential reference，不列真实工厂或环境配置。当前 new-api route 也必须保持一个稳定、
非敏感的账号标识对应一个可审计调度单元，轮换凭据时不得改变标识。实际 Cookie、Session、
Token、设备材料不得进入 Manifest、数据库、任务、能力响应或普通日志，也不得把多个真实
账号藏在一个 route 内随机切换。

## 4. 调度语义

### 活跃任务槽

`max_concurrency` 是长时任务上限。任务从获得账号并进入 `submitting` 开始占槽，
`processing` 和 `reconciliation_required` 继续占槽；Provider 明确返回成功、失败或取消
终态后释放。提交结果未知不能提前释放，因为上游可能已经在生成。

冻结 Python SQL 曾从 `generation_jobs` 的持久状态派生活跃数；该表只属于离线 oracle。
当前 new-api 必须用自己的 PostgreSQL 共享状态证明相同的不超卖语义，进程内计数不能作为
生产号池。

### 每分钟限流

`requests_per_minute` 是每账号固定一分钟窗口，不是滑动窗口。账号获得提交资格时消耗
一个窗口计数；到达上限后返回距当前窗口结束的重试时间。窗口计数持久化在 PostgreSQL，
所以增加 Worker 副本不会绕过上限。

### 冷却、永久停用与 drain

冻结实现中，账号级失败连续达到审核阈值后进入固定冷却期。成功提交会清除连续失败和冷却。永久凭据失效使用
`disable_account=True` 关闭新准入；人工 drain 也使用同一准入开关。

冷却或 drain 只影响新任务。已经绑定的任务继续使用原账号查询，不会因为账号不再接新
任务而跨号查询。重新启用会清空失败、冷却和最后错误。当前核心已提供受控准入开关，
但仓库没有面向公网的账号运维接口；生产应通过单独鉴权、审计的内部控制面调用，不要让
运营直接改数据库。

如果匹配模型的账号全部被永久停用，新任务会明确失败并释放上层预留，不会在延迟队列中
无限等待；只要仍有未停用但处于冷却的候选，任务才等待最早冷却结束。

健康检查是只读的候选筛选，不累计失败次数、不自动禁用账号。实际提交结果才驱动账号
失败、冷却或停用，避免 readiness 探测本身改变生产调度状态。

### 延迟重新入队

以下本地准入错误不会增加工作项 `attempt`：

- `PROVIDER_ACCOUNT_POOL_BUSY`
- `PROVIDER_ACCOUNT_POOL_RATE_LIMITED`

冻结 Redis 行为把工作项放入有时间分数的延迟集合，到期后原子提升回工作流；默认准入
等待可由 RPM/冷却的 `retry_after_seconds` 覆盖。这类等待不代表 Provider 调用失败，不能
消耗 Provider 尝试预算。普通、已实际
发生的可重试提交错误仍按 Provider 重试预算处理。所有路由健康检查都失败时返回
`NO_PROVIDER_AVAILABLE`，它不是账号容量等待，仍消耗普通重试预算并应触发健康告警。

## 5. 错误映射

交付文件应先转换成模板中的类型化结果，再映射成 `ProviderError`：

| 交付文件结果 | `ProviderError` 语义 | 调度结果 |
| --- | --- | --- |
| `DeliveredRequestRejected` | 不可重试、账号可用、明确未创建 | 任务失败，不换账号 |
| `DeliveredAccountUnavailable(permanent=False)` | 可重试、账号不可用、明确未创建 | 记录失败/冷却，可切下一账号 |
| `DeliveredAccountUnavailable(permanent=True)` | 账号不可用且 `disable_account=True` | 停止该账号新准入，可切下一账号 |
| `DeliveredSubmissionOutcomeUnknown` | `submission_outcome_unknown=True` | 保留路由和槽位，禁止重提/切号，进入对账 |
| `DeliveredQueryTemporarilyUnavailable` | 查询可重试但不是提交失败 | 原账号退避后继续轮询，不影响新准入 |
| 上游明确 `failed/cancelled` | 统一 Provider 终态事件 | 原任务终止并释放槽位 |

任何 POST 超时、断连、成功响应缺任务号、响应无法校验或适配器在 POST 后抛出未知异常，
只要不能证明“任务未创建”，都必须按未知提交处理。不得用 HTTP 5xx、本地异常或“换个号
试试”作为重复下单的依据。

## 6. 当前发布边界

本文故意不保留 Python 工厂环境变量、镜像安装、migration 或进程启动顺序。生产接入必须
把账号 route、限流、claim lease、监控和 credential reference 纳入同一个不可变 new-api
release，并执行 root/proof/service-principal 链。精确发布顺序见
[new-api 生产部署门禁](new-api-production-deployment.md)。真实 canary 必须核对任务、上游
后台、产物转存和渠道账单后才能逐步放量。

人工 drain 不计入批量失效告警；只有因规范化 Provider 错误永久停用的账号才参与批量
失效阈值。完整告警规则、签名 Webhook 和运维查询见
[历史 Python Provider 监控语义（离线 oracle）](provider-monitoring.md)。

## 7. new-api 真实验收

离线 Python 单元测试只核对冻结行为，不得加载真实公司包或凭据，也不构成上线证据。
new-api staging 必须另外验证：

- 两个 Worker 同时竞争一个 `max_concurrency=1` 账号时只有一个任务获得槽位。
- 一个长时任务运行期间不会把槽位提前释放；终态提交后能恢复容量。
- RPM 到顶后任务等待到窗口结束，`attempt` 不增加，也不会重复请求 Provider。
- 临时失败触发冷却并切换账号；永久失效只停用目标账号；批量 drain 不影响已有轮询。
- POST 结果未知时不重提、不切账号、不释放槽，且人工对账可恢复或确认未创建。
- Worker 崩溃、租约过期和旧 token 晚返回时不会覆盖新所有者或回退终态。
- 密钥轮换前后 `account_id` 不变；日志、任务、数据库和错误响应中没有秘密。
- sidecar 不能访问 Relay 数据库、Redis、OBS 密钥和非目标上游域名。

## 8. 仍需真实交付的内容

接通一个逆向渠道还需要公司提供：版本化 API 文件/sidecar、合法使用说明、账号清单和
`secret_ref`、模型与模式映射、状态码说明、临时 URL 时效、限流规则、失败是否收费、
账号失效信号、负责人和回滚包。缺少其中任何关键项时，适配器必须保持
Python artifact 永久保持 `production_ready=False`，不能把模板或结构检查结果称为生产
接通；只有通过当前 new-api route acceptance 的渠道才可获得准入。
