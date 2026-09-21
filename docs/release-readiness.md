# 需求差距与上线门禁

更新日期：2026-08-31

## 结论

Relay schema 跨版本门禁只在门禁自有 `exact-v3` 合成数据库推进；任何 pinned image 都不得访问业务/live 数据库。

需求文档中的“双平台”架构可以实现，当前已经从单页原型进入可运行的第一里程碑。

- **受控内网演示/研发联调**：当前共享工作树仍在收口；必须先在最终 commit 上完成根 CI、数据库 release proof 与本地/内网验收，不能继承旧 PASS 自动放行。
- **公网商用**：当前禁止放行。原生 OIDC/BFF 登录与账号生命周期代码已经收口，但目标 IdP 的真实 redirect/JWKS/step-up/吊销 canary 尚未签字；真实生成渠道、支付回调、生产 OBS、外部监控告警与备份恢复也仍是硬门槛。
- **当前数据库资格**：Platform v15 catalog 为 `UNQUALIFIED`，v12/0047、v13/0048 也尚未资格化，v14/0049 同样未资格化。当前受保护发布保持 `BLOCKED / NO-GO`；本地 PG16 功能与并发回归不等于生产 catalog、角色和签名 release proof 资格。

## 当前覆盖

| 需求域 | 当前状态 | 已完成 | 商用前剩余 |
| --- | --- | --- | --- |
| 双平台拆分 | 已形成 | 客户平台与生成中转站独立服务、数据和凭证边界 | 独立生产网络与容量验证 |
| 多公司与权限 | 第一版完成 | 公司、成员软停用、预置与自定义角色、角色替换/撤销；当前 16 个有效权限项均绑定实际公司端路由校验，只有老板可按完整目录为员工逐项设置 `inherit`/`allow`/`deny`。个人、企业与平台管理员账号类型互斥：个人账号只进入个人 Studio，企业账号只进入企业 Studio，只有企业账号可在其有效 membership 覆盖的多个企业之间切换 | 目标 IdP 真实邀请/换账号 canary；账号类型冲突和显式迁移演练；全角色长时间会话与离职演练 |
| 平台管理员 | 工程第一版完成 | 公司创建/启停、模型定价与完整权益矩阵、动态功能/智能体/外部 API 目录、人工充值、消费报表、只追加渠道成本账本、收入/成本/毛利看板和审计日志均已接入平台运营界面；Platform Owner 还可管理首页精选案例草稿、不可变发布/回滚和紧急下线。最高权限账号要求 IdP subject allowlist、防钓鱼 `amr`、`auth_time` 和近期 step-up | 目标 IdP 的 WebAuthn/passkey step-up canary、真实 OBS 精选媒体签名跳转、Relay 真实账单自动上报、可信支付、审批与告警 |
| 模型与定价 | 第一版完成 | 生产模型草稿/版本/发布/停用、v1 逐模式能力声明、公司授权、按秒/按条价格、价格与完整生效能力快照；专用 `relay-catalog-sync` 定时以 ETag 条件请求对账 Relay 目录，只物化未发布草稿和能力候选，页面打开只读、无变化不写库、真实变化写系统审计；批准、发布、定价和个人/企业分发仍由 Platform Owner 人工执行 | 成本价与毛利策略、真实渠道能力准确性和变更审批演练 |
| 钱包与计费 | 软件实现及本地回归完成，生产未接入 | 现金用 64 位整数分、权益用整数积分（10 积分 = ¥1）；只追加账本、支付订单、退款/拒付、持久投递、原件绑定对账、自动充值及企业月结；任务预占、成功结算、失败释放与并发保护 | 真实 PSP、可信账单来源、v15 受保护数据库资格、生产常驻 billing worker、通知、监控与恢复演练 |
| 异步生成中转 | 核心完成 | 扩展 new-api 是唯一活动 Relay；统一 `/v1/generations`、版本化能力、PostgreSQL/Redis 协调、路由粘性、unknown fencing、转存、签名回调与成本事件均由该数据面承载 | 真实账号沙箱、真实 Provider/OBS、容量、账单与外部告警证据 |
| 产物安全 | 第一版完成 | 成功前转存、SSRF 防护、大小/MIME/哈希校验、私有对象元数据；Huawei OBS 上传后以 HEAD 核对大小、类型和哈希元数据，核验前不成功、不结算 | 真实 OBS 桶验收、生命周期、病毒/内容扫描 |
| 员工/老板/平台界面 | 第一版完成 | 三套浅色响应式工作面；新增生产登录、回调、邀请接受、账号安全、设备会话、资料、全设备退出、账号停用、公司 owner 转移和平台 owner 全局账号状态界面；制作台继续只读取服务端生效能力 | 目标 IdP 与真实账号的桌面/390/320 端到端验收；真实全渠道验收 |
| 平台资源授权 | 第一版完成 | 功能、智能体、外部 API 使用动态服务端目录，新增项自动进入全部公司矩阵并默认拒绝；目录停用 fail-closed，生成能力可声明所需资源 | 非生成业务模块接入资源校验；对外 API 密钥、配额与调用计量 |
| 运维与发布 | 骨架完成 | 容器、迁移、健康检查、反向代理、本地持久化编排、超时补偿、签名回调 Worker、dead-letter 和不可变接收事件 | TLS、密钥系统、备份恢复、集中监控告警、面向运营的死信看板、灰度回滚 |

## 公网商用 P0 门槛

以下任一项未完成，均不得向真实客户开放或收费：

1. 为已实现的 OIDC Authorization Code + PKCE/BFF 会话配置目标 IdP，并完成正常登录、state/nonce/replay、JWKS 轮换、邀请、step-up、单设备/全设备吊销和全局停用 canary；生产环境不得信任开发身份请求头或旧浏览器 Bearer。
2. 使用至少一个真实生成供应商账号完成测试额度、地区/模型权限、失败分类、账单和结果转存验收；仅有官网契约实现不算通过。
3. 配置私有华为云 OBS 桶，证明匿名读取被拒绝，完成真实文件转存与短时下载测试，并把 OBS access log 或受控边缘下载完成事件接入客户平台。
4. 支付与积分软件闭环必须接入选定的真实 PSP，验收商户、Mandate 同意证据、回调验签、查询、退款/拒付和未知结果处理；公司成员不得自行增加余额。PSP、银行及供应商账单须有可信来源，上传文件摘要不能代替来源认证；生产常驻 billing worker、通知与告警仍须部署验收。
5. 使用真实生成供应商和生产 OBS 完成“创建任务 → 预占 → 生成 → 转存 → 下载 → 结算”的整链路演练；当前仅 Mock 与本地共享卷通过。
6. 把已经运行的超时补偿、派发/转存耗尽和回调 dead-letter 接入外部告警与统一运营看板；代码侧记录和受保护查询已完成。
7. 完成数据库与对象存储备份、恢复演练，以及发布失败回滚演练。
8. 完成 Platform 当前迁移链的 PostgreSQL 16 catalog、最小权限角色与签名 release proof 资格；v12/v13/v14/v15 的 `UNQUALIFIED` 或历史 hash 均不得用于当前生产放行。

## 明天可放行的范围

仅允许以下形态：

- 回环地址或访问受限的内网；
- 明确显示“演示/Mock”，不得宣称连接真实生成渠道；
- 使用测试公司与测试余额，不接真实支付；
- 不上传真实客户敏感素材；
- 在演示前完成一次自动化测试和整栈健康检查；
- 本地共享卷只用于验收下载链路，不代表生产 OBS 已通过验收。

## 归档工程证据（2026-08-05；不可作为当前发布证据）

以下记录只说明当时的工程演进，不绑定当前工作树、镜像 digest、配置或外部账号。旧测试
计数、Mock 冒烟和本地扫描不能被复制到当前发布表；当前 release 必须按 CI、数据库 proof、
真实 Provider/OBS/IdP/支付与运维门禁重新生成证据。

- 需求文档“六、接口契约”已形成可签字冻结包：`contracts/relay-generation-v1.openapi.yaml`、`callback-event-v1.schema.json` 和 `error-codes-v1.json` 分别冻结统一 HTTP API、签名回调和 50 项公开错误；`docs/generation-api-v1-freeze-checklist.md` 留出 Platform/TikTok 双方负责人、版本、超时、配额、域名、密钥轮换和回滚确认。接口不是 OpenAI SDK 的 wire/drop-in 兼容层，但采用异步资源语义。
- Relay 与 Platform 运行时强制 `api_version=v1`、`schema_version=1`、必填 `expected_capability_revision`、`id == job_id`、revision 一致和状态到 `hold/settle/release` 的唯一映射。`succeeded` 只有在长期产物齐全后才能 `settle`，`failed/cancelled` 才能 `release`，`reconciliation_required`、网络/5xx 不确定结果和转存中状态继续 `hold`；单独的 Accepted 不能作为结算证据。
- TikTok 参考轮询和 HMAC 回调接收代码已经交付；跨服务契约测试覆盖 TikTok 独立 tenant、ETag 能力目录、revision 固定、幂等创建、状态/下载、客户平台跨租户 404、普通凭证对账 403 和 operations-only 凭证生成 403。工程接入面已具备，但真实 TikTok 系统尚未接入，双方签字冻结和独立容量配额仍是上线前事项。
- 第 5.1 节统一生成 API 已收口：`POST /v1/generations` 以 `202` 返回 `id/object/status` 异步资源，查询、转存后短时产物地址、主动回调和统一错误信封闭环；404、405、422、500 及供应商执行失败不会泄露底层渠道身份。文生图、文生视频、图生视频均有完整 HTTP 到产物契约测试。
- Relay 通过服务凭证发布 `/v1/models` 与单模型资源，能力按模式拆分并带稳定 SHA-256 revision、目录 revision、ETag/304；目录不因瞬时健康波动消失，共享模型发布故障切换安全交集。平台管理员已在真实运行栈把 `mock.video.v1` 的安全子集确认为当前 revision，后续任务快照与 Outbox 固定同一 revision。
- Platform 的 `relay-catalog-sync` 后台进程按周期、单 leader 使用 ETag 条件请求 `GET /v1/models`。`304` 或完整目录无语义变化时不写模型或审计表；真实变化以 `actor_kind=system`、`actor_key=relay-catalog-sync` 审计，只创建未发布模型草稿或更新能力候选。管理员打开模型目录页面只读 Platform 数据库，不触发 Relay 请求或写入；能力批准、模型发布、计费方式/价格和个人/企业分发继续要求 Platform Owner 人工操作。
- 第 5.2 节三类渠道统一接入层已形成版本化 v1 契约：逆向渠道、第三方 API 平台和官方渠道必须显式分类，通过零参数工厂动态注册；启动时校验适配器版本、生产就绪状态和逐模型/逐模式能力，Mock 不能伪装成生产渠道。路由只接受公开故障切换安全能力，健康检查隔离超时与异常；可证明未创建的可重试失败才允许切换，Provider POST 结果未知时禁止换渠道重提。已交付可复制模板、自动校验命令和新渠道接入指引；客户身份、回调、计费、转存及平台授权仍由核心层负责，渠道适配器不能接管。
- 第 5.3 节号池调度已完成工程第一版：一个真实账号对应一个稳定路由，PostgreSQL 在 Provider POST 前按 submission token 加锁分配并跨 Worker 统一执行长任务并发与固定窗口 RPM；已绑定任务在 `processing` 和结果未知对账期间继续占槽并始终粘性轮询原账号。明确未创建的账号级失败才释放并切号，永久失效关闭新准入但不打断存量任务；池忙、冷却和 RPM 到顶通过 Redis 耐久延迟集合等待且不消耗 Provider 重试次数。逆向文件严格模板、sidecar 隔离要求和真实 canary 清单已交付，但公司具体 API 文件、账号与独立鉴权审计的运营控制面仍未交付。
- 第 5.4 节高可用与监控已完成工程第一版：Router 按 `request/account/channel` 的已证明未创建范围安全切换，未知提交固定原路由并进入对账；独立 Monitor Worker 用 PostgreSQL 租约定时保存每路由健康/延迟和数据库级只追加的真实上游终态，按连续周期检测成功率下降、大面积路由失败和批量 Provider 错误停用账号，并持久化去重的触发/恢复事件。通用告警 Webhook 使用 HMAC、token-fenced claim、指数退避和 dead-letter；readiness 会报告 Monitor 周期新鲜度、活动告警、投递积压和死信，监控异常或全部 Provider 不可用时保持 HTTP `200 degraded`，不阻断存量回调、查询和对账。真实多渠道演练、外部告警接收端、值班路由和 Worker 进程外部监控仍未完成生产验收。
- new-api 产物转存已增加上传前 durable intent、任务完成/发布原子提交、数据库时钟租约、随机 token fencing、幂等 Delete、指数退避和 8 次上限死信。readiness 暴露存储 BindingID 与 cleanup 的 pending/claimed/due/dead-letter；OBS endpoint+bucket 或 filesystem root 不匹配时禁止向新存储误删，并把遗留项显式留在死信。真实 Huawei OBS 慢上传、进程崩溃与桶切换验收仍为外部门禁，不能由本地模拟替代。
- 未知提交已具备 Platform 待核实列表、权限门禁、稳定 operation ID、审批前置审计、独立 tenant-bound HMAC 审批签名、Relay 侧不可变 receipt、幂等 replay 和结果回读。生成 token 与运维 bearer token 都不能单独伪造人工审批；响应丢失后按已持久化 operation ID 查询结果，禁止创建第二次 Provider 提交或跨渠道重试。生产仍需把运维 token 与审批密钥分别放入密钥管理系统，并演练轮换和旁路调用告警。
- 异步并发边界新增三道持久化保护：产物转存领取可续租 token，Provider 轮询按任务领取并续租，回调按条领取且成功/失败写入均以 token-CAS 收口；旧 Worker 或 Poller 的晚到结果不能把新终态降级，也不能重复启动转存。

- 第 5 节平台管理员控制面已形成完整闭环：管理员创建/启停公司、人工充值、模型企业价格、完整模型与资源权益矩阵、动态功能/智能体/外部 API 目录、逐企业开关、消费报表、渠道成本账本、收入/成本/毛利和未对账告警均有真实 API 与管理界面。
- 新目录项无需修改前端即可出现在每家公司的权益矩阵，且默认关闭；目录项可改显示名、说明和启停状态，稳定 `key` 与 `kind` 不可漂移。目录停用后不能新开通，历史 grant 保留但业务使用 fail-closed，管理员仍可关闭旧授权。
- 渠道成本使用独立只追加账本，支持正成本、明确零成本、负数退款/冲正、稳定幂等键、外部凭证、公司/任务/Relay 任务关联及渠道分类。ORM、SQLite 和 PostgreSQL 均拒绝 UPDATE/DELETE，PostgreSQL 额外拒绝 TRUNCATE；管理员入口和 Relay 内部入口共享幂等语义且保留首写来源。
- 看板不再把充值当收入，也不从客户售价猜渠道成本：`platform_recharge_cents`、成功任务 `platform_income_cents`、`channel_cost_cents` 和 `gross_profit_cents` 分开计算；成功任务缺少任务级成本时明确返回 `incomplete`，0 成本也必须写入对账记录。
- 第 6 节能力自适应闭环已加固：浏览器只使用公司 API 的 `effective_capabilities`；不同模型和模式会即时重构输入区与参数区，并同步清理超限素材、失效枚举和人脸选项。空白、畸形或无可用模式的声明默认拒绝；提交再次按当前能力清洗，并以 `expected_capability_version` 防止界面读取后模型能力被后台改动。服务端只接受白名单生成字段，客户 metadata 被隔离进 `client_metadata`，不能绕过能力校验向渠道注入参数。
- 第 7 节任务与产物闭环已完成工程第一版：任务历史保留发起人、公司、模型、参数、状态、报价与实际费用；成功产物写入不可变规范化索引；本人范围为默认值，公司范围必须显式请求且要求 `reports.read`，跨范围详情和下载返回 404。作品页支持分页及媒体、员工、模型、时间和可信下载状态筛选。
- 下载审计严格拆成 `issued` 与 `completed`。按钮点击和签名 URL 只追加不可变签发记录；只有受内部令牌保护、且公司/任务/产物/签发记录/完整字节数/时间全部匹配的 OBS access-log 或边缘事件才能追加完成记录。未部署事件源时界面保持“已签发”，不会虚报“已下载”。
- 上一版 0040 精选案例发布回归证据保留：前端/Node 506 项通过，Sites 7 项通过，生产构建成功；Platform 全量 1514 项通过、38 项按环境设计跳过，精选案例/数据库 focused 88 项通过。两套独立 PostgreSQL 16 资格库得到相同 v5 catalog，0039→0040、0040→0039→0040、`alembic current/check`、最小 ACL、不可变 trigger、并发幂等发布和 SQLSTATE 55000 拒绝均实测通过。该历史证据不能替代当前 0049 发布门禁，也不替代目标 IdP、真实 OBS/Provider、支付、外部告警和恢复演练。
- 契约边界在本轮继续收紧：Relay 请求和 Platform 消费端的整数/布尔字段禁止字符串隐式转换，50 项公开错误码在 OpenAPI、回调 Schema、Relay 和 Platform 四处机器比对；产物 ID 必须是规范 UUID；生产环境关闭可由调用方自报状态的旧内部结算入口。真实权限编辑必须取得非空的服务端权限目录，不能退回浏览器硬编码目录。
- 浏览器实测覆盖制作、公司、平台三套界面和 390×844 窄屏；模型切换会从 9 图 + 3 视频 + 3 音频收紧到 4 + 3 + 3，再收紧到仅 1 张图，并同步隐藏不支持的模式、人脸、视频与音频控件。已修复窄屏顶部账号区导致的整页横向溢出，并保留可访问账号菜单；桌面与窄屏控制台均无错误。
- 历史 Python oracle 的镜像/依赖扫描只用于冻结行为回归，不能作为生产 Relay 镜像安全证据。生产发布必须对本次 new-api immutable digest 重新生成 SBOM、漏洞扫描、签名与 provenance；仓库内没有真实 registry/签名服务的当前外部回执时，该项保持 `BLOCKED`。

## 当前数据库与支付发布边界（2026-08-31）

- 客户平台当前源码 head 为 `0050_model_commercial_release`，直接前序为 `0049_payment_finance_closure`，当前 Platform 数据库权限策略为 v15。0050 新增证据绑定、不可变的模型商业发布计划及失败关闭的执行状态；0049 保留持久支付投递、Webhook 重放、原始账单绑定、拒付偿债归属与企业催收。v15 catalog 为 `UNQUALIFIED`；v12/0047 与 v13/0048 也未资格化，v14/0049 同样未资格化，受保护运行与迁移保持失败关闭。只有当前链的独立 catalog、角色和签名 release proof 全部完成资格后，才可按 role-pre → migration → proof 顺序发布；开发 PG16 迁移和并发测试不是该资格证据。0049 对无原件绑定的旧结算行、无 Mandate 的历史自动扣款订单在 DDL 前停止，必须制定保留历史的操作员迁移方案，不能删事实或补造原件/同意来通过。
- 历史 `0045_system_audit_actor` 保留用户/系统审计身份和最小权限 `relay-catalog-sync` principal；`0044_account_product_partition` 保留个人、企业、平台管理员的互斥产品边界；0043/0042 保留只读提示词权限与 durable entitlement journal；`0041_model_capability_releases` 保留 candidate/approved ceiling 的分离；0040 保留首页精选状态机；0039 只设置新任务的 `new-api-v1 / generations.v1` 默认 affinity，不重写历史。`0038_download_evidence_checks` 与 `0037_production_auth_lifecycle` 继续保留。
- v10/0045 的 `7ce8849ecc4be298fe9889bdeaeb7ea17932a51c9ff9eeb024cc55bbcad44142`、v9/0044 的 `64640e8ccf7069fc6ca0773af64def56babfdb80101ea9cd22e6b8e7fc00c167`、v8/0043 的 `8258cdc950161a8571df59c71fbcea466bf96a481f382b6a598565ce80c9f638` 和 0041 的 `c5f909c67bcfc91482fb6423ccd9c0c0e98724cdba11e951664a311099bf3778` 均仅保留为历史资格证据，不能证明当前链或授权生产。旧版 hash、未资格化 hash 或占位值都不得用于当前发布。Python oracle 的 `0012_generation_contract_v1` 仅可在临时隔离测试库核对，不是生产迁移或回滚步骤。
- 支付订单、退款/拒付、积分、对账、自动充值与企业月结的软件实现及本地回归见 [支付与财务闭环](payment-finance-closure.md)。真实 PSP、可信账单来源、生产常驻 billing worker 和通知仍未接入。上传原件不能证明 PSP/银行/供应商签发，对账须保留 `SOURCE_AUTHENTICITY_UNVERIFIED`；HTTP-only `python -m platform_api.billing_worker` 的实现不等于常驻部署，催收 action 不等于通知已送达。支付交接的测试计数也不绑定本次最终候选，冻结后仍须复验。
- 扩展 new-api Relay 的当前源码合同为 `target=8,min=1,max=8`。fresh-v8 预期 ledger 只能是 `[8]`；raw legacy 依次经过 historical v1→v2 no-catalog-delta、pinned v3 correction、不可变 pinned-v4/v5、pinned-v6 与历史 v7 边界后，current-v8 只允许从 exact v7 增加不可变、回执绑定的 provider-cost allocation evidence，预期完整 ledger 为 `[1,2,3,4,5,6,7,8]`。v8 source/model/checksum/catalog 已冻结为 `sha256:4d1422f6f691d1440600536f9b89c3e1fb9af9e69f880437b95d437b101f71dd`、`sha256:24f03d665ed5df5958cbf82075bfd28fb8aeb97fee277164e443a0218132776d`、`sha256:1def22667e226cf8dfbd467e18445874dcce6959a8b9e74b43623e14bbc31de7` 与 `sha256:6374866475d3b9c404592c39ef5ad8be1b63066308b502e5362dbc47e469823c`，但冻结身份不等于生产资格。专用 PostgreSQL 用例 `TestRelaySchemaPostgresConstructedV7ToV8ProviderCostEvidence` 已通过 `TEST_RELAY_SCHEMA_V7_TO_V8_DSN` 在一次性 `postgres:16-alpine` 上完成 `PG16-constructed-v7→v8-gate=PASS (9.021s)`，证明构造 v7→v8、v7 ledger 不变、v8 表与 UPDATE/DELETE 不可变 guard。该专项没有覆盖 TLS 或 pgAudit；`PG16+TLS+pgAudit-full-qualification-gate=NOT_RUN`，尚未完成生产资格并保持 `BLOCKED / NO-GO`。pinned-v4/pinned-v5 都只是 immutable binary behavior fixture。既有长门禁只证明到历史 v7：`pinned-v3 migration -> pre-v4 zero-ACL role-pre -> pinned-v4 migration -> pinned-v4 full verifier -> pre-v5 zero-ACL role-pre -> pinned-v5 migration -> pinned-v5 rollback verifier -> pre-v6 zero-ACL role-pre -> pinned-v6 migration -> pinned-v6 rollback verifier -> pre-v7 zero-ACL role-pre -> historical-v7 migration`。这段归档证据的事件名依次为 `pre-v4-zero-acl-role-stub-gate=PASS`、`pre-v5-zero-acl-role-stub-gate=PASS`、`pre-v6-zero-acl-role-stub-gate=PASS` 与 `pre-v7-zero-acl-role-stub-gate=PASS`；其中 role-pre does not perform schema migration，四项历史 PASS 也不能写成完整生产 v8 PASS。v8 生产门禁还必须证明 exact-v7→v8、当前角色/ACL、release proof 与 Current-v8 API/edge lifecycle；成功后 max-v7 镜像必须判为 `ahead` 并失败关闭。所有 pinned image 仍不得访问业务/live 数据库；pinned image 不是运营迁移源。SKIP、missing test 或离线结果不能替代生产证据。生成 wire 和 secret/receipt envelope 的 `schema_version=1` 与数据库合同版本是不同命名空间。

## 归档本地冒烟补充（不可作为当前发布证据）

- 重建后的本地整链路冒烟通过：管理员创建公司；模型 revision 在任务快照与 Relay Outbox 中存在且一致；新增 feature、agent、external API 三类动态资源并验证新公司默认关闭；客户 API 返回能力版本 2 和 9 图 + 3 视频 + 3 音频、人脸能力；模型按条定价 25 分并授权；8,991 字节私有图片生成并转存为 4,372,373 字节 MP4，任务历史和作品库各返回 1 条完整记录；短时下载先保持 `issued`，受保护边缘事件确认后才变为 `completed`；任务成功结算 25 分且预留归零，渠道成本 9 分幂等入账，公司停用和恢复路径正常。
- Compose 重建后客户平台、网关、PostgreSQL 和 Redis 健康，全部容器重启计数为 0；Relay readiness 仍因 Mock Provider 与本地文件存储为预期的 `degraded`。平台 API 被替换且尚未就绪时，网关记录过一次短暂的 DNS 解析超时，随后无需重启即恢复；服务稳定后的日志窗口未出现 ERROR、Traceback、CRITICAL、Unhandled 或 Exception。
- 此归档的充值证据当时只覆盖管理员人工调账，不能证明当前支付订单链；当前 0048/0049 软件能力与未接入边界以上节为准。真实 IdP、Provider、生产 OBS、可信支付与账单、集中告警和恢复演练仍须独立验收，公网商用结论继续为 **NO-GO**。

## 归档工程证据（2026-08-04；不可作为当前发布证据）

- 自动化回归：Web/客户端/Sites 35 项、客户平台通用测试 226 项、真实 PostgreSQL 专项 7 项、Relay 185 项、双平台契约 9 项，合计 462 项通过；Sites 6 项专项另行复跑通过，生产构建成功。
- PostCSS 已升级至 8.5.25；使用 npm 官方审计端点复核生产依赖为 0 个已知漏洞。
- 客户平台迁移 head 为 `0014_billing_report_hardening`，Relay 为 `0006_source_client_identity`；两个 PostgreSQL 实例均在 head 且 `alembic check` 无漂移，平台迁移还完成空 SQLite 库和临时 PostgreSQL schema 的升级、回退及重升，并验证账本禁止 UPDATE、DELETE 与 TRUNCATE。
- 公司计费闭环已在真实 PostgreSQL 竞争条件下验证：两个员工共享余额时不会透支，成功结算与失败释放只能落一个终态，相同充值只入账一次，模型计费方式、公司授权及模型生命周期并发修改不会产生错配或陈旧覆盖；充值与消费分页的汇总和明细由单条 SQL 返回一致快照。
- 公司组织层级已收敛为老板、组长、运营：创建公司原子生成老板，新成员默认运营或显式设为组长，普通成员始终只有一个主级别，升降级原子替换；历史零个/多个主级别由 `0012` 迁移归一，运行中容器已完成“运营 → 组长 → 运营”验收。
- 成员权限已实现“角色模板 + 个人三态覆盖”：当前 16 个有效权限项均有实际公司端路由约束，完整目录、继承值、个人 `allow`/`deny` 和最终生效值均可见；只有老板可为员工逐项配置，清除个人覆盖即恢复 `inherit`。老板一次提交即可原子修改级别、附加角色与个人覆盖。批量请求字段必须显式提交并禁止未知字段；提交携带的 expected 快照只覆盖角色分配与个人覆盖，不覆盖角色模板内容，成员行锁内角色/覆盖快照不一致返回 409。`models.manage` 与 `tasks.manage` 已退役且永不复用，不出现在目录 API、不接受新分配/覆盖、也不参与有效权限求值。测试覆盖全目录翻转、恢复继承、非老板越权、跨公司、停用成员、自己/老板保护、失败回滚、陈旧编辑冲突、错误字段不清权、幂等和审计前后值。
- 公司私有素材链路已覆盖幂等上传、MIME/大小校验、短签访问、公司隔离和任务引用；首次 Relay POST 前只签发并持久化一次完整请求，后续用同一载荷和幂等键恢复，避免重签导致重复生成。
- Platform 派发遇到网络中断、5xx、异常成功响应或幂等冲突时不再推测失败退款；结果未知会进入待对账并保持预占。派发 attempt CAS 阻止租约过期的旧 Worker 覆盖新结果，签名回调可安全补绑响应丢失的 Relay job。
- 模型能力已收敛为版本化 v1 契约：逐模式声明图/视频/音频数量、人脸、提示词、时长、比例、分辨率、产物数和资源要求。公司 API 返回服务端计算的完整生效能力，制作台、报价、任务落库、余额预占及 Outbox 共用该语义；覆盖只能取子集、降低上限、关闭人脸或增加资源要求，声明式 `required_resource_keys` 缺定义、停用或未授权时默认拒绝。
- Relay 同一 Provider 已支持多账号稳定路由、优先级、并发上限、冷却和账号故障切换；任务持久化具体 route，人工对账不能覆盖已知 route 或任务编号。不可重试但不能证明失败的轮询错误进入待对账并主动回调；已有上游任务的对账不能伪报 `not_created`，避免错误退款。
- `customer-platform` 与 `internal-tiktok` 使用不同 client、tenant 和 key，Relay 持久化可信调用方身份；浏览器仍只能访问客户平台。
- Relay 主动回调已与 Platform HMAC 接收端真实契约测试打通，覆盖时间窗、防重放、事件幂等、任务/Relay ID 核对、状态变更和请求追踪；轮询继续作为降级兜底。
- Relay 回调发送端具备指数退避、最大尝试、dead-letter 和租户隔离运维查询；平台接收事件不可变并可通过内部受保护接口查询。
- 制作台支持按模型能力显式切换文生图、文生视频、图生视频和视频重绘，选择比例、分辨率、时长、产物数及人脸开关；支持最多 15 个图片/视频/音频私有输入的完整展示、移除、素材库复用、预览和停用，切换模型或模式时会提示超限素材裁减。
- 本地 Compose 已实跑 13 个长期服务与 2 个一次性卷初始化任务，新增独立 `relay-provider-sync`；平台、Relay、数据库和 Redis 健康。Relay readiness 的 `degraded` 仅表示开发环境仍使用 Mock Provider 与 filesystem，符合预期且不能当作生产健康声明。
- 上线前重建后端容器时发现本地 Nginx 会缓存旧 Compose 地址并返回 502；网关现已使用 Docker DNS 动态重解析，新增部署契约回归，容器替换不再依赖人工重启网关。
- 新版烟测实际完成“8,991 字节私有图片上传与短签预览 → 图生视频派发 → processing/succeeded 签名回调 → 4,372,373 字节 MP4 转存与下载 → 结算”：成功回调一次入库，预占 25 分最终归零，余额由 1,000 分变为 975 分。
- v1 能力运行态专项已在重建后的双平台完成：`mock.video.v1` 以 canonical 配置发布，客户 API 返回 9 图 + 3 视频 + 3 音频和人脸能力；1080p、人脸开启、2 个产物的请求原样到达 Relay，报价与最终结算均为 50 分，转存 2 个产物，任务快照保留完整生效能力。
- 网关已把素材上传上限对齐平台默认的 512 MiB，并只公开 HMAC 保护的精确回调路径；无签名回调返回 401，回调事件运维查询仍在公网网关返回 404。Nginx 配置检查通过。
- 以上均为本地工程与 Mock Provider 证据，不替代真实 IdP、真实 Provider、生产 OBS、支付、集中告警和备份恢复验收。

## 2026-08-01 前一轮本地验收证据

- 自动化回归全部通过：Web/客户端 17/17、客户平台 117/117、生成中转站 69/69，共 203 项；Sites 专项 4/4 另行复跑通过。
- Vite 6.4.3 生产构建与 Sites 打包检查通过；使用 npm 官方审计端点检查生产依赖，结果为 0 个已知漏洞。因公网门禁未满足，没有发布生产版本。
- 当时 PostgreSQL 实际迁移到平台 `0008_access_model_lifecycle`、Relay
  `0003_submission_claim`；该记录已由 2026-08-04 的新迁移基线取代。
- 11 个长期容器全部运行且重启次数为 0，新增 `platform-timeout-worker` 已持续扫描；平台与网关可用。Relay 的数据库、Redis 队列、文件存储和 Mock Provider 均健康，但总体为预期的开发环境 `degraded`，不是生产可用声明。
- 自动冒烟脚本实际创建公司与任务，同一请求幂等重放返回同一任务；预占 25 分，
  Mock 成功回调后转存并下载 8,991 字节产物，短时地址有效 300 秒，最终结算
  25 分、预占归零、测试余额由 1,000 分变为 975 分。
- 浏览器验收覆盖制作/公司/平台三套工作面、桌面端、390×844 窄屏和缺少登录令牌的真实环境门禁；成员新增交互实际走通，控制台无警告或错误，演示数据始终明确标识。

## 2026-08-01 功能闭环更新

- 公司成员接口统一返回当前角色，已完成成员启停、角色替换/撤销、自定义角色生命周期与 `/me` 有效权限查询；软停用用于保留任务、计费和审计历史。
- 平台模型目录已提供生产可用的草稿、更新、乐观版本、发布、停用和删除接口；公司只能使用已发布且已授权模型。
- 任务/消费报表支持员工、模型、状态、时间筛选与安全 CSV；下载记录分别展示“短时地址已签发”和“可信传输已完成”，没有完成事件时绝不标记已下载。
- 超时补偿采用保守策略：只有确定未派发或 Relay 已给出权威终态时结算/释放；不确定提交继续保留预占并记录不可变事件。

## 2026-08-01 门禁更新（历史快照，已由 2026-08-20 状态取代）

- 当时客户平台只有生产 HS256 JWT 验签；该历史状态已由 2026-08-20 的原生 OIDC Authorization Code + PKCE、RS256/JWKS、服务端可撤销 Cookie 会话和完整账号生命周期实现取代。
- 当时前端仍读取 `sessionStorage["ai-video.access-token"]`；生产路径现已改为同源 BFF Cookie，浏览器不再保存长期 Bearer。仍不得把 token、用户 ID 或公司身份固化到 `VITE_*` 或静态构建产物。
- Relay 支持 `RELAY_PROVIDER_FACTORIES` 动态注册适配器，并以 `RELAY_SUBMISSION_CLAIM_LEASE_SECONDS` 控制原子提交租约；`0005_provider_polling` 又加入轮询退避与供应商任务唯一约束。这些代码防线不代表真实账号渠道已经验收。
- 当时上线迁移基线为客户平台 `0014_billing_report_hardening`、Relay `0006_source_client_identity`；客户平台编号先由 `0015_channel_cost_ledger`、后由 `0016_task_artifact_audit` 取代。
- 真实 Provider、私有生产 OBS、可信支付、真实 IdP 和完整运维保障仍未完成。公网商用状态继续为 **NO-GO**，本地或白名单演示也不得连接真实支付或对外宣称生产可用。
