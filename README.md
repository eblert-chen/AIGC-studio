# AI 视频生成双平台

本仓库正在建设需求文档中的两个系统：

1. **客户管理平台**：公司、成员、权限、授权、余额、计费、任务和作品。
2. **生成中转站**：统一生成 API、渠道适配器、异步任务、路由和故障切换。

现有 React 产品包含员工制作、公司管理和平台运营三套工作面，并明确区分演示数据与真实 API 模式。后端当前只接入明确标记的模拟渠道，
用于验证契约、异步任务、产物转存和计费边界；尚未连接任何真实生成供应商，也尚未配置生产华为云 OBS。

## 目录

```text
src/                 React 制作/公司/平台三套工作面
backend/platform/    客户管理平台 FastAPI 服务
backend/new-api-relay/ 唯一活动生产 Relay（Go/new-api + 本产品扩展）
backend/relay/       仅供隔离合同测试的 Python 行为 oracle artifact
docs/                架构、统一 API 契约和里程碑
infra/nginx/         后端入口反向代理
worker/              Sites 前端托管 Worker
```

## 当前里程碑

- 双平台边界和统一生成契约已经形成。
- 客户平台具备多公司、成员与角色生命周期、个人权限、生产模型目录、Relay 公共目录受控对账与新模型草稿自动物化、完整模型/资源权益矩阵、私有输入素材、钱包账本、任务历史、不可变作品索引、签发/可信完成下载审计、消费报表、渠道成本账本、收入/成本/毛利看板、主动回调接收、超时补偿、平台管理员和审计。专用 `relay-catalog-sync` 后台进程定时用 ETag 条件请求读取 `/v1/models`；管理页面打开时只读 Platform 已持久化目录，不触发 Relay 请求或写库。目录无变化不写库；有变化时以系统审计身份只物化未发布草稿和能力候选，能力批准、发布、定价及个人/企业分发仍由 Platform Owner 人工审阅。
- 中转站具备持久任务、Outbox、Redis 队列、适配器路由、失败切换、安全产物转存、Huawei OBS 上传后对象元数据核验，以及带签名、重试和死信的主动回调。
- 前端可在配置真实平台地址后上传或复用私有素材、创建多产物任务、按本人或授权公司范围查看任务历史与作品，并管理成员/角色、公司、模型/资源授权、充值和报表；未配置时明确显示演示数据。
- 本地容器编排已包含隔离 PostgreSQL、Redis、唯一 new-api Relay、Download Edge、Platform API/Workers 和客户网关。
- 支付订单、退款/拒付、积分批次、持久投递、对账、自动充值与企业月结已有软件实现及本地回归；真实 PSP、可信账单来源、生产常驻 billing worker 和通知仍未接入，不能作为生产收费资格。

完整范围和验收门槛见：

- [双平台架构基线](docs/architecture.md)
- [统一生成 API v1](docs/generation-api-v1.md)
- [new-api 生产部署与渠道接入门禁](docs/new-api-production-deployment.md)
- [new-api 真实渠道与 OBS 验收](docs/relay-real-channel-acceptance.md)
- [历史 Python 适配器合同（离线 oracle only）](docs/provider-adapter-v1.md)
- [历史 Python Provider 监控语义（离线 oracle only）](docs/provider-monitoring.md)
- [模型能力配置 v1](docs/model-capability-v1.md)
- [模型能力发布与 Relay 目录对账](docs/model-capability-release-system.md)
- [平台管理员控制面 v1](docs/platform-admin-v1.md)
- [首页精选案例管理](docs/showcase-management.md)
- [第一里程碑](docs/first-milestone.md)
- [需求追踪与完成度矩阵](docs/requirements-traceability.md)
- [需求差距与上线门禁](docs/release-readiness.md)
- [支付与财务闭环：实现及上线边界](docs/payment-finance-closure.md)
- [部署与上线运行手册](docs/deployment-runbook.md)
- [根仓库与 new-api 源码管理](docs/source-control.md)

## 安全提示

- 本地模拟密钥和开发身份请求头不得直接用于生产。
- 生产必须关闭初始化接口。
- 金额只使用整数最小货币单位。
- 浏览器不能持有中转站、供应商、OBS、数据库或 Redis 密钥。
- 目标 IdP、真实渠道、生产存储和支付未完成部署 canary 前，不得向真实客户收费。
- 当前 `docker-compose.yml` 仅用于本地持久化联调，不得直接当作生产编排。
## 生产登录、账号与 Relay 边界（2026-08-21）

客户平台使用外部 OIDC Authorization Code + PKCE 和 Platform BFF 会话。生产浏览器不再
保存 Bearer token；只携带 Secure/HttpOnly/SameSite 服务端会话 Cookie，所有写请求另做
Origin + 双提交 CSRF 校验。OIDC `issuer/sub` 映射稳定本地账号，多公司上下文仍由服务端逐次
核验 membership；全局停用与 `auth_version` 会立即切断个人、公司和平台管理范围。

密码、找回、MFA 和通行密钥属于外部 IdP。仓库实现登录启动、PKCE callback、RS256/JWKS
轮换验证、服务端会话、单设备/全设备退出、邀请激活、全局账号状态、平台所有者强认证与
step-up；部署仍必须提供目标 IdP、精确 redirect URI 和真实 canary 证据。前端仅由部署层注入
非秘密 `platformApiUrl`，生产代码固定忽略旧 `sessionStorage` Bearer。

生产生成数据面固定为扩展版 new-api。受保护 Platform 只接受唯一
`new-api-v1 / generations.v1` backend；route 必须绑定当前不可变镜像、经签名验收的能力声明、
服务端 Provider credential keyring 和版本化合同费率。Provider submission、poll、artifact transfer
与 callback 均使用持久 lease/token fencing，上游 unknown submission 不能跨账号或跨渠道重投。
平台私有输入素材和 Relay 长期产物分别使用最小权限 Huawei OBS 边界；Relay 回调使用租户级
HTTPS 白名单与 HMAC-SHA256 签名，平台按事件 ID 幂等接收，状态轮询只作降级兜底。

Relay 的两个服务调用方统一命名为 `customer-platform` 与 `internal-tiktok`；生产 `RELAY_CLIENT_CREDENTIALS_JSON` 必须为两者配置不同的 tenant UUID 和 API key。客户平台使用自己的回调路由与签名密钥；若 TikTok 也需要主动回调，必须配置另一条 HTTPS 路由和另一把签名密钥，不能复用客户平台配置。若 TikTok 只轮询状态，则不提交 callback，也不需要配置 TikTok 回调路由。

受保护发布先运行全局 secret-isolation validator。new-api Relay 随后严格执行 database
role-pre → Go migration → role-post，并由 role-pre 独占写入 generation-bound database
release proof；Platform 再执行自己的 role-pre 与 Alembic migration，并发布独立证明。
API 与全部 Worker 只能只读验证对应证明后启动。客户平台当前源码 head 是
`0050_model_commercial_release`，直接前序为 `0049_payment_finance_closure`。0050 新增证据绑定、
不可变的模型商业发布计划及失败关闭的执行状态；0049 继续保留持久支付投递、Webhook 重放、
原始账单绑定、拒付偿债归属和企业催收。
源码 head 不等于已获生产迁移资格；当前受保护发布保持 `BLOCKED / NO-GO`。冻结的
`0038_download_evidence_checks` 下载证据约束与 `0037_production_auth_lifecycle`
认证生命周期继续保留。历史 `0045_system_audit_actor` 为用户与系统审计主体建立显式、互斥的身份字段，并为周期性
Relay 目录对账增加专用最小权限数据库 principal；历史 `0044_account_product_partition`
持久化互斥的个人、企业和平台管理员账号类型，保留历史钱包、
账本、任务、产物和成员证据，只关闭与账号类型不一致的入口，并以数据库约束和 trigger 阻止
静默跨类型；个人账号只进入个人 Studio 并使用个人积分，企业账号只进入企业 Studio 并使用
企业共享钱包，平台管理员只进入 Platform 控制面；三类账号可以共用同一 Auth0 tenant，但不会
因此合并产品边界、钱包或导航。0043 是 data-only 权限目录迁移，只持久加入受控任务内容
调阅的只读权限 `platform.task_content.read`，表与 ACL 投影保持 0042 的冻结值；0042 用全局
唯一、事务内完成的 journal
保护企业授权批量、复制与模板执行，并将无法重建原始意图的旧审计 key 冻结为禁止自动重放；
`0041_model_capability_releases` 把 Relay capability candidate 与最后批准 ceiling 分离；
0040 新增仅 Platform Owner 可管理的首页精选案例草稿、不可变发布版本、紧急下线事件和
最小权限存储索引；0039 仍只负责新 task/outbox 的 new-api affinity 默认值。

Platform 数据库权限策略当前为 v15，catalog 为 `UNQUALIFIED`；v12/0047、v13/0048 也尚未
资格化，v14/0049 同样未资格化。全零占位不是有效 fingerprint，开发 PG16 的迁移/并发测试不能替代当前链的受保护
catalog、角色与签名 release proof 验收，不得绕过失败关闭来启动生产。
历史 v10/0045 的 `7ce8849ecc4be298fe9889bdeaeb7ea17932a51c9ff9eeb024cc55bbcad44142`、
v9/0044 的 `64640e8ccf7069fc6ca0773af64def56babfdb80101ea9cd22e6b8e7fc00c167` 与
v8/0043 的 `8258cdc950161a8571df59c71fbcea466bf96a481f382b6a598565ce80c9f638`
均仅保留为历史资格证据，不能证明当前链或授权生产。历史 v10 引入的 `relay-catalog-sync`
最小权限边界继续保留：只读写模型草稿/能力候选、追加审计，不执行批准、发布、定价或分发。

new-api 当前原生数据库合同为 `target=7,min=1,max=7`：fresh v7 只记录 ledger `[7]`；
exact v1 依次经过冻结的 historical v1→v2 no-catalog-delta bridge、pinned v2→v3 修复、
冻结的 v3→v4 immutable route-binding、v4→v5 diagnostic-taxonomy、v5→v6 durable
channel-test lifecycle 与 current v6→v7 artifact content-type release 后记录
`[1,2,3,4,5,6,7]`。v5 仅是历史迁移边界，v4/v5/v6 的既有 guard 保持冻结；v7 将受保护
artifact 类型扩展到 `video/mp4` 与 `image/png`。跨版本 pinned image 只能用于门禁自有合成库，
业务升级只使用经审阅的 current-v7 migrator。迁移到 v7 后，`max=6` 的旧镜像必须判为
`ahead`，不能作为直接回滚目标。Python Relay 的
`0012_generation_contract_v1` 仅冻结离线 oracle artifact，不是生产迁移或回滚目标。根级
`.github/workflows/ci.yml` 会持续执行前端、Platform、new-api Web/Go race、真实
PostgreSQL/Redis integration、跨服务成本门禁，以及明确隔离的历史 oracle 回归。

new-api 未知提交的人工处置使用独立的 Platform 运维地址和 tenant-scoped token。渠道高风险
操作由 Platform owner 经 phishing-resistant AMR 与 recent step-up 授权后，在新标签页打开固定
native new-api `/channels`；不使用 iframe，不在 URL 携带 credential。生产回滚只允许上一版
schema-compatible new-api 不可变镜像，不能把新准入切回 Python。正常运行的成功 Provider
终态会按版本化合同费率物化只追加渠道成本事件；合同费率缺失会保持 reconciliation
incomplete，不会被记为零成本。

> 仓库已具备原生 OIDC/BFF 账号链路，但目标 IdP、真实 Provider、生产华为云 OBS 和可信支付仍需部署侧验收。完成这些真实 canary 前只能用于本地或受控演示，不得公网商用或向真实客户收费。
