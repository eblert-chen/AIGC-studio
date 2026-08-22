# Python Relay 适配器合同 v1（离线历史 oracle）

> **非生产接入规范**：本文只冻结 `backend/relay/` 的历史 Python 行为，供隔离合同回归和
> 差异定位。生产新渠道必须接入唯一活动的扩展 new-api Relay，提交本产品的版本化
> `/v1/generations` 能力、签名 route acceptance、真实 Provider/OBS/账单 canary，并遵守
> `relay-new-api-migration.md` 与 `new-api-production-deployment.md`。把本文的 Python factory、
> Dockerfile 或 `production_ready` 标志接入受保护环境属于发布失败。

更新时间：2026-08-05

## 1. 目标与适用边界

历史 Python Relay 通过版本化 `ProviderAdapter` 契约接入渠道；本节仅保留该实现曾经验证
过的行为不变量，供离线比较。当前生产新渠道必须在 `backend/new-api-relay/` 实现并通过
new-api route-acceptance、真实 Provider、OBS 和账单门禁，不能在 Python 目录新增工厂后
取得生产准入。浏览器与 Platform 仍只依赖供应商中立的 `/v1/generations` 合同。

三类真实渠道统一使用同一个接口：

| 渠道类型 | `channel_type` | 说明 |
| --- | --- | --- |
| 逆向渠道 | `reverse` | 公司交付已经封装好的 API 文件；本岗位只做适配，不承担逆向、补号或验证码破解 |
| 第三方 API 平台 | `third_party_api` | 聚合 API、代理平台或合作方统一接口 |
| 官方渠道 | `official` | 模型厂商或云厂商正式公开接口 |

以下情况不能只写适配器，必须升级统一契约及上下游：新增生成模式、新素材类型、新产物
类型、新的公共参数或 UI 控件；自动渠道成本事件；统一 API 当前没有表达能力的特殊工作流。
多副本共享的账号并发、每分钟限流、冷却与停用已经由 Relay 核心负责，适配器只需正确
声明账号 Manifest 和错误语义。

## 2. 代码入口

- 抽象契约：`backend/relay/relay_service/providers/base.py`
- 动态装载：`backend/relay/relay_service/providers/registry.py`
- 能力路由：`backend/relay/relay_service/providers/router.py`
- 高可用监控：`backend/relay/relay_service/provider_monitoring.py`
- 独立监控进程：`backend/relay/relay_service/provider_monitor_worker.py`
- JSON HTTP 安全传输：`backend/relay/relay_service/providers/http.py`
- 可复制模板：`backend/relay/examples/provider_adapter_template.py`
- 契约检查命令：`python -m relay_service.providers.verify`

当前适配器契约版本是 `1`。每个具体适配器必须继承 `ProviderAdapter`，并显式声明：

```python
class AcmeProviderAdapter(ProviderAdapter):
    contract_version = 1
    name = "acme"
    channel_type = ProviderChannelType.THIRD_PARTY_API
    production_ready = False
```

安全 Manifest 由基类生成，包含 `route_id`、`provider_name`、`account_id`、渠道类型、
优先级、活跃任务上限、固定窗口每分钟请求上限和生产验收状态。Manifest 不得包含密钥、
Cookie、Token、请求地址、`secret_ref` 的实际值或 Provider 原始响应。

冻结 oracle 中的 `production_ready` 必须永久保持 `False`；不得因新的 staging 结果改为
`True`，也不得把该标志当作生产接入开关。真实账号、额度、地区、模型权限、账单、异常
提交对账、限流、产物转存和凭据轮换的批准只属于当前 new-api route release。受保护环境
始终拒绝 Mock 和整个 Python artifact。

## 3. 必须实现的方法

| 方法 | 责任 |
| --- | --- |
| `capabilities()` | 返回确定、非空的模型能力声明；不得受瞬时健康度影响 |
| `healthcheck()` | 返回严格布尔值；不得抛出带密钥或 URL 的异常；会同时用于请求候选筛选和定时路由级监控 |
| `submit(job)` | 把统一任务转换为渠道请求，返回经过校验的 `ProviderSubmission` |
| `poll(job)` | 主动查询并转换为统一 `ProviderWebhookEvent`；不支持时可返回 `None` |
| `parse_webhook(body, headers)` | 只有能够验证原始请求签名和防重放时才实现 |
| `close()` | 关闭适配器拥有的 HTTP Session 或 SDK 客户端 |

Relay API、Generation Worker 和 Provider Sync Worker 启动时都会校验适配器元数据和
能力。任一适配器返回空列表、错误对象、自相矛盾的能力或重复的 `model + mode`，服务会
直接启动失败，不会带病接流量。

## 4. 模型能力声明

能力必须逐 `model + mode` 声明。同一个公开模型在文生视频和图生视频的输入限制不同
时，必须返回两个条目，不能把两种模式塞进一个共享限制后错误地放宽：

```python
return [
    ModelCapability(
        model="acme.video.v1",
        modes=[GenerationMode.TEXT_TO_VIDEO],
        input_media_types=[],
        limits=CapabilityLimits(
            max_prompt_length=3000,
            max_images=0,
            max_videos=0,
            max_audio=0,
            duration_seconds=[5, 10],
            aspect_ratios=["16:9", "9:16"],
            resolutions=["720p"],
            output_counts=[1],
        ),
        available_providers=[self.name],
    ),
    ModelCapability(
        model="acme.video.v1",
        modes=[GenerationMode.IMAGE_TO_VIDEO],
        input_media_types=["image"],
        limits=CapabilityLimits(
            max_prompt_length=3000,
            max_images=1,
            max_videos=0,
            max_audio=0,
            duration_seconds=[5, 10],
            aspect_ratios=["16:9", "9:16"],
            resolutions=["720p"],
            output_counts=[1],
        ),
        available_providers=[self.name],
    ),
]
```

约束：

- 模型 ID、模式、输入类型和所有枚举值不能为空或重复。
- 声明了某种输入类型时，对应数量必须大于零；反过来也一样。
- 图生视频至少支持一张图片，视频转视频至少支持一个视频。
- 单类素材上限是 15，所有输入素材总和不得超过 15。
- 能力必须来自经过核对的本地配置或版本化渠道目录，不能在每次请求时临时猜测。
- 同一公开模型别名由多个渠道承载时，Relay 对每种模式发布所有可故障切换路由的安全
  交集；交集为空的模式不会发布。
- Relay 也会按这份公开交集做最终请求准入，不能借较宽的私有主路由绕过。若需要保留
  某渠道独有的高分辨率、人脸或更长时长，应配置独立公共别名（例如 `standard` 与
  `premium`），不要把能力不同的备用路由塞进同一个别名。
- 健康波动只影响当次路由，不改变 `/v1/models` 的能力 revision。能力配置变化后需重启
  Relay，并由平台管理员重新确认新的 revision。
- 功能、智能体和公司授权属于客户平台；适配器只声明渠道的物理生成能力。

## 5. 提交、查询与错误语义

`submit()` 必须返回非空、无控制字符、长度不超过 256 的上游任务号。Relay 会把选中的
精确 `provider@account_id` 写入任务，后续轮询固定回到同一账号。

`ProviderError` 的标志决定是否切换渠道，必须按以下规则设置：

| 场景 | `retryable` | `account_unavailable` | `submission_outcome_unknown` | 行为 |
| --- | --- | --- | --- | --- |
| 已明确拒绝的用户参数 | `False` | `False` | `False` | 立即返回，不切换，不熔断 |
| 已明确未创建任务的账号鉴权/额度错误 | 视渠道而定 | `True` | `False` | 标记账号故障，可切到下一账号 |
| 已明确未创建任务的 429/临时拒绝 | `True` | 按是否账号级设置 | `False` | 可重试或故障切换 |
| GET 查询超时/临时 5xx | `True` | 通常 `False` | `False` | 原账号退避后继续查询 |
| POST 超时、断连、畸形成功响应，无法证明未创建 | `False` | `False` | `True` | 绝不重提或切换，进入人工对账 |
| 上游任务明确终态失败 | 不使用提交异常 | 不适用 | `False` | 返回统一失败事件 |

对于已经证明未创建任务且允许继续尝试的错误，还必须准确声明 `failure_scope`：

| `failure_scope` | 适用范围 | Router 行为 |
| --- | --- | --- |
| `request` | 当前请求的临时拒绝，不代表账号或 Provider 整体故障 | 仅在可重试时尝试下一兼容路由 |
| `account` | 当前具体账号不可用 | 记录账号失败/冷却/停用，再尝试其他账号或渠道 |
| `channel` | 同一 Provider 的其他账号也会失败 | 本次提交跳过该 Provider 的其余账号，尝试其他 Provider |

`channel` 不是“允许不确定 POST 跨渠道补单”的开关；它只能用于明确未创建任务的响应。

已明确未创建任务的永久鉴权失效可以额外设置 `disable_account=True`；该标志只能与
`account_unavailable=True` 一起使用，表示关闭这个账号的**新任务准入**。临时鉴权、额度
或限流错误应使用冷却，不应把整个账号永久停用。适配器也可为可安全重试的错误提供正数
`retry_after_seconds`，供 Relay 延迟重新入队。

Relay 会拒绝 `submission_outcome_unknown=True` 同时搭配可重试或账号切换标志；路由器还有
第二层防守，即使插件事后篡改错误对象，也不会向下一渠道重复下单。

只有供应商正式文档承诺幂等时，才把稳定 `job.id` 传入它的幂等字段。普通
`correlation_id`、备注字段或客户端任务号不等于幂等保证。没有保证时，任何无法确认的
POST 结果都必须进入 `reconciliation_required`，由运营到供应商后台核对。

Provider 原始错误消息、响应体和密钥不得写入公开错误或普通日志。公开错误使用稳定、
供应商无关的分类；详细原始信息只能进入受控、脱敏的内部诊断系统。

Router 会给每次适配器调用独立的任务副本，并移除来源 client、客户任务引用、客户回调
地址和所有可改变生成行为的 metadata；只保留经过长度与控制字符校验的
`relay_request_id`、`platform_request_id` 供内部追踪。适配器只能根据统一
`model / mode / inputs / output` 与稳定 `job.id` 映射请求，不能从 metadata 读取
`alibaba_wan`、`official` 等供应商专用开关；需要新的用户可控参数时，必须先把它加入
版本化统一契约和能力声明。

## 6. 轮询、回调与产物

- `poll()` 必须校验任务号完全一致，并把渠道状态映射为 `processing / succeeded / failed /
  cancelled`。
- 事件 ID 对同一逻辑状态必须稳定，进度不得倒退。
- 没有公开、可验证签名契约的渠道必须禁用 webhook，使用主动轮询。
- 多账号渠道的 webhook URL 必须使用精确 `provider@account_id`，不能只写模糊 provider
  名称；每个账号独立验签。
- 成功事件只能返回临时 HTTPS 图片或视频源地址。Relay 完成大小、MIME、下载和私有
  OBS 转存核验之前，任务不能变为最终成功，也不能向客户平台结算。
- 临时 Provider URL 不得作为长期产物地址透传给上层。

## 7. 冻结工厂样例与离线校验

工厂是零参数函数，返回一个适配器或同一 Provider 的多个账号：

```python
def create_providers() -> list[ProviderAdapter]:
    return [
        AcmeProviderAdapter(
            account_id="cn-a",
            priority=10,
            max_concurrency=2,
            requests_per_minute=20,
        ),
        AcmeProviderAdapter(
            account_id="cn-b",
            priority=20,
            max_concurrency=1,
            requests_per_minute=10,
        ),
    ]
```

该工厂代码只用于测试 fixture，不能配入 Compose、受保护环境、真实账号或真实 secret。
离线测试应使用临时 SQLite/PostgreSQL、临时 Redis、Mock HTTP 和合成凭据；测试进程不得
访问 Provider、OBS、Platform release proof、生产 DNS 或生产网络。一个冻结适配器实例仍
代表一个合成账号，并保留稳定 `account_id`，从而核对以下历史行为：

- `max_concurrency` 限制该账号的长时活跃任务，而不是只限制提交 HTTP；从提交开始，经过
  `processing` 和 `reconciliation_required` 都占槽，Provider 明确终态后才释放。
- `requests_per_minute` 使用每账号固定一分钟窗口；达到上限的任务延迟重新入队，不消耗
  Provider 提交重试次数。
- 连续账号级失败达到冻结阈值后进入冷却；永久账号失效或人工 drain 关闭新准入。
- 关闭准入或冷却不改变已经接受的任务；轮询始终粘在任务记录的精确
  `provider@account_id`，直到真实终态或完成对账。
- 账号池忙、达到 RPM 上限或账号仍在冷却时，离线 Redis fixture 延迟再投递，并保持原
  `attempt`。

`0012_generation_contract_v1`、`0011_provider_monitoring` 和
`0010_provider_account_pool` 只冻结 oracle artifact 的数据库形状；生产发布不得执行这些
迁移、构建 Python Dockerfile 或启动 Python API/Worker。当前账号池、监控和 route release
的生产实现及顺序以 [new-api 生产部署门禁](new-api-production-deployment.md)为准。

## 8. 需要由 new-api 继承的三类渠道检查

以下条目是从历史 oracle 提炼的安全需求，不是 Python 渠道安装说明。每项生产证据必须由
当前 new-api immutable image、route release 和真实 staging 资源生成。

### 逆向渠道

- 只接公司交付的合规 API 文件，不在适配器中实现逆向、补号、验证码绕过或设备破解。
- 默认把未经安全评审的交付文件运行在受限 sidecar/独立服务中，Relay 适配器只调用其
  内网窄接口；禁止直接 import 后让它接触 Relay 数据库、租户密钥、OBS 密钥或其他账号。
- Cookie、Session、设备标识和账号材料必须进入 Secret Manager，不得落库到任务或日志。
- 必须提供账号封禁、失效、冷却和人工停用信号；批量失效时支持总开关。
- 明确 API 文件的版本、负责人、状态码、临时 URL 时效和更新回滚办法。

### 第三方 API 平台

- 固定平台模型 ID 到 Relay 公共别名的映射，禁止把调用方提供的任意模型 ID直接透传。
- 核对平台额度、子账号、429 语义、失败是否收费、账单明细和任务保留期限。
- 平台若再次聚合多个底层渠道，仍只能向上暴露 Relay 的供应商无关错误。

### 官方渠道

- 只依据官方文档实现字段、签名和状态，不猜测未公开参数。
- 核对地区、Endpoint、Workspace、模型权限、配额、账单和回调验签能力。
- 官方接口没有幂等或可信 webhook 时必须明确关闭对应能力。

## 9. 离线回归与生产证据边界

在 `backend/relay` 执行：

```text
python -m pytest tests/test_provider_registry.py tests/test_router.py
```

该命令只允许在隔离测试环境验证冻结 oracle 的结构和安全语义，不能连接真实账号，也不能
作为生产 release required data plane。真实生产验收必须针对 new-api immutable image 在
staging 执行，并至少覆盖：

- 三种目标生成模式的真实低成本任务、明确 4xx、429、查询超时和终态失败。
- POST 结果未知时不切换、不重复扣费，并能完成人工对账。
- 同模型多渠道的能力交集、优先级、账号容量绕行、熔断和原账号粘性。
- 两个 Worker 竞争同一账号时不超卖活跃槽；RPM 到窗前不重提，窗口重置后恢复。
- drain/永久停用只拒绝新任务，已存在任务仍用原账号轮询；冷却到期自动恢复候选资格。
- 临时 URL 过期、错误 MIME、超限文件、OBS 转存失败和哈希/元数据核验。
- 密钥轮换、账号禁用、地区错误、模型无权限和服务降级。
- 主路由健康失败、账号级和 Provider 级安全故障切换；未知提交不得调用备用渠道。
- 定时健康样本、成功率/大面积失败/批量账号失效的触发与恢复，以及签名告警死信。
- 供应商账单与内部任务逐笔核对；真实成本事件闭环未接通前，管理员看板必须显示未对账，
  不能把缺失成本当作零成本。

完成 new-api route 验收后，还需在客户平台执行 Relay 模型目录审计、确认 capability
revision、发布平台模型、配置公司价格和公司授权。Python oracle 不参与审批或运行时。

## 10. 当前真实状态

离线 Python artifact 保留可灵、阿里百炼 Wan、火山方舟三个历史契约适配器，全部固定为
`production_ready=False`，只用于行为回归。它们不是活动渠道、生产候选或回滚路径。
当前 new-api 仍必须为每个真实 route 单独归档 Provider、OBS、账单、故障注入和轮换证据；
缺少证据时该 route 保持 `BLOCKED`，不能借用 Python 测试结果放行。
