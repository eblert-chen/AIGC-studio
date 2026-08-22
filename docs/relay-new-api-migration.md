# new-api Relay 生产切换与回滚合同

状态：**生产数据面合同已收口；扩展版 new-api 是唯一可接受新生成任务的 Relay。**

本文区分两件事：

- 软件发布合同已经从“双 Relay 迁移期”收口为单一 new-api 数据面；
- 真实 Provider、Huawei OBS、外部告警、目标 IdP、支付、备份恢复和容量证据仍是部署外部门禁。缺少这些证据时公网商用仍为 `NO-GO`，但这不授权把 Python Relay 重新接回生产。

## 1. 唯一活动数据面

受保护 staging/production 必须同时满足：

1. Platform 只配置一个 backend：`new-api-v1`，合同固定为 `generations.v1`。
2. Platform API、dispatcher、relay-sync 和 timeout-worker 使用同一个经证明的 new-api release；浏览器仍只访问 Platform。
3. 根 Compose、secure overlay、staging 和 production 不定义 Python Relay service、profile、依赖、环境变量或数据卷。
4. Python `backend/relay/` 只保留为离线行为 oracle 和显式归档 artifact；它不能由生产 Compose 启动，不能拥有 production service credential，也不能接收新准入。
5. 不允许 Python/new-api 按比例、按请求或按租户并行准入。任何把 Python 恢复为默认、把 new-api 降为可选或要求紧急切回 Python URL 的发布材料均为陈旧合同。

本地隔离测试可以启动 Python oracle 来比较公开契约。该进程必须使用测试凭据、测试数据库和测试 Redis，不能访问生产网络、Provider、OBS、Platform release proof 或生产任务。

## 2. 数据归属与任务亲和

Platform 自 `0033_relay_backend_affinity` 起已经在 task 与 outbox 上持久化
`relay_backend_id` 和 `relay_contract_revision`。这些字段是历史归属证据，不是运行时改路由开关：

- 新受保护任务只能写入 `new-api-v1 / generations.v1`；
- 历史 `legacy-default-v1` 行只能查询、审计和完成切换前对账，受保护进程不得配置一个可调用的 Python backend；
- 已接受任务必须继续由原 Relay job、Provider route、submission token 和 lease 链完成；
- 不得复制任务、改写 affinity、把 unknown submission 重新提交到另一数据面，或用新任务伪装“迁移”；
- Platform 钱包预占、Relay job、artifact transfer、callback、provider cost 和 reconciliation evidence 必须逐项闭合，不能只比较两边的任务总数。

Platform 迁移 `0039_new_api_relay_defaults` 只把新建 task/outbox 的 ORM 与数据库
server default 从 `legacy-default-v1` 改为 `new-api-v1`，合同仍为 `generations.v1`；迁移不得
`UPDATE` 或重写任何既有 affinity。受保护运行时发现仍绑定 legacy backend 的活动 task/outbox
必须 fail closed，终态历史行才可继续查询和审计。`submission_unknown` 与
`reconciliation_required` 都属于非终态，不能被排空门禁误判为已完成。

new-api 使用自己的 PostgreSQL、Redis、artifact/OBS namespace 和 release proof。Python
Alembic head `0012_generation_contract_v1` 只描述离线 oracle artifact 的冻结形状，不能替代
new-api `target=2,min=1,max=2` schema/release-proof 链，也不属于正常生产发布步骤。

## 3. 一次性切换前排空

从遗留部署执行最后一次切换时，必须先关闭生成新准入，但继续运行完成存量所需的原进程。
至少保存以下绑定同一 release/数据库快照的证据：

1. Python 遗留任务没有 `accepted`、`queued`、`submitting`、`submission_unknown`、
   `processing`、`transferring` 或 `callback_pending` 行；
2. outbox、callback delivery、dead letter、artifact cleanup、Provider alert 和未对账成本均为零；
3. Platform 中所有 `legacy-default-v1` 任务已到业务终态，钱包预占已 settle/release，
   reconciliation 不再需要访问 Python；
4. Platform 与 Relay 数据库及对象存储元数据已备份，恢复点、RPO/RTO 和负责人已记录；
5. 当前 new-api 镜像 digest、源码 snapshot、route-acceptance trust digest、数据库 schema/proof、
   service-principal 和进程 secret receipt 全部匹配。

任一项不满足时停止切换并前向修复遗留数据。不得通过删除、改状态或丢弃证据把计数“清零”。

## 4. new-api 发布顺序

受保护 fresh install 与后续 release 必须遵守
[new-api 生产部署门禁](new-api-production-deployment.md)中的精确顺序。摘要如下：

1. 选择不可变 new-api image digest 和唯一 staging/production inventory；
2. 验证全局 secret isolation，禁止 raw secret environment；
3. fresh install 执行 root bootstrap；普通 rollout 禁止再次读取 root secret；
4. 执行 database role-pre → migration → role-post，并生成/验证 generation-bound release proof；
5. 创建或轮换最小权限 service principal，验证 TLS、`current_user`、schema current 和 ACL；
6. 启动 new-api API、generation/transfer/provider-sync/provider-monitor/callback workers、download edge；
7. 启动只配置 `new-api-v1` 的 Platform 进程，核对 operations origin 与唯一 data backend canonical origin 一致；
8. 在恢复准入前验证 `/health/live`、严格 release readiness、模型 capability revision、签名 callback、成本和积压；
9. 真实 Provider/OBS/告警证据未通过时保持外部准入关闭。

成功切换后不得保留能启动 Python production data plane 的 profile 或 secret。离线 oracle 的测试通过不构成生产 Provider/OBS PASS。

## 5. 稳态发布与排空

普通 new-api rollout：

1. 停止新准入或从负载均衡移除待替换实例；
2. 等待该 release 已 claim 的提交、轮询、转存和 callback 结束，保留 token fencing；
3. 执行 previous-candidate schema gate、当前 migration/proof/ACL gate；
4. 仅用不可变 digest 替换 new-api 进程；
5. 新实例通过严格 readiness 后恢复准入。

不得把 `degraded`、普通 `/health/live`、Mock route、过期 channel-test 或缺失 cost evidence 当成切流批准。Provider-only outage 时 API 可以保持查询/回调/对账服务，但新生成准入必须按 readiness 失败关闭。

## 6. 回滚

生产回滚的目标只能是**上一版已验证、schema-compatible 的 new-api 不可变镜像**；Python Relay 不是生产回滚目标。

1. 立即关闭新准入，保持当前 new-api 对已经接受的任务、unknown submission、artifact、callback 和 cost evidence 的所有权。
2. 若旧镜像仍兼容当前 schema 和 release proof，按同一 role/proof/secret-isolation 链部署旧 digest；否则前向修复，不能降级数据库或伪造 proof。
3. 已接受任务绝不改写 `relay_backend_id`、Provider route 或 submission token，也不跨数据面重投。
4. 数据库 restore 只允许在任何生产流量和业务写入之前使用已验证恢复点；一旦当前 release 接受业务写入，默认采用前向迁移/修复。
5. 回滚后继续保存失败 release 的数据库、日志、callback、Provider 和成本证据，直到全部对账完成。

任何临时重建 Python 生产准入的提案都属于新的架构变更，必须重新设计 protected config、secret、ACL、迁移和整链路验收；它不是本手册允许的应急操作。

## 7. 运营入口

渠道高风险操作只通过 Platform owner 的审计授权入口进入 native new-api `/channels`：

- owner subject allowlist、phishing-resistant AMR 和 recent step-up 全部通过后，Platform 返回服务器持有的固定 HTTPS URL；
- 浏览器在新标签页打开，不使用 iframe，不代理任意 new-api API；
- URL 不携带 new-api credential、Bearer token、session 或 SSO assertion；native console 仍需独立登录/SSO；
- Python oracle 没有生产运营入口。

## 8. 可执行门禁与外部证据

根 CI 必须拒绝以下回归：

- production/default backend 重新变成 `legacy-default-v1` 或 `relay-api:8000`；
- production Compose 重新出现 Python Relay service/profile/volume；
- 文档重新宣称 Python 默认、双数据面并行准入或 Python production rollback；
- Platform protected runtime 接受多个 backend、legacy credential 或旧 callback fallback；
- release checklist 把本地测试、Mock、`degraded` 或未绑定当前 digest 的报告当成真实外部 PASS。

仓库自动化可以证明合同、迁移、并发、故障注入、签名、provenance 和 fail-closed 行为；以下证据必须由授权 staging/production 单独产生并归档，仓库不得伪造：

- 真实 Provider create/finalize、unknown-submission 核实和账单；
- Relay 控制 Huawei OBS 的上传、HEAD/摘要、完整下载 proof；
- 外部 alert sink 的触发、恢复、重试和 dead letter；
- 目标 IdP、支付、备份恢复、容量/稳定性和轮换演练。

缺少任一必需外部证据时结论保持 `BLOCKED/NO-GO`，但活动生产 Relay 的软件合同仍只有 new-api。
