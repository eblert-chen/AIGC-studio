# 历史 Python 官方视频渠道适配器（离线 oracle）

> **冻结说明**：本文只保存 Python Relay 对三家官方渠道的历史字段映射与安全语义，不能
> 用于生产安装、配置、启动或回滚。当前渠道必须在唯一活动的 new-api Relay 中实现，并按
> `new-api-production-deployment.md` 和 `relay-real-channel-acceptance.md` 生成真实证据。

更新日期：2026-08-05

通用插件契约、逆向/第三方渠道规则、错误标志矩阵和新渠道验收命令见
[历史 Python 适配器合同（离线 oracle）](provider-adapter-v1.md)。本文只记录三家官方渠道的
供应商差异。

## 结论

冻结 Python oracle 曾实现首批三家官方渠道的请求构造、异步任务查询、统一状态、错误
脱敏和产物转存语义：可灵 3.0、阿里云百炼 Wan 2.7、火山引擎方舟 Ark。这些代码只用于
离线差异核对，不是当前活动 route。

每个适配器同时声明渠道类型、稳定账号别名、优先级、可选活跃任务上限和固定窗口每分钟
请求上限。同一工厂可以返回同一 Provider 的多个不同 `account_id`；Relay 会在同优先级
账号间结合活跃任务数、成功提交量和最近分配时间调度，在账号达到上限或进入冷却时切换
到下一账号。选中的稳定路由会在 Provider POST 前、提交 claim 的 token 围栏内写入任务，
因此后续轮询和回调仍使用创建任务的同一账号，不会在号池中漂移。账号密钥不会进入任务
或公开能力响应。

账号调度状态现已持久化在 PostgreSQL `provider_account_states`，由数据库行锁协调多个
Generation Worker。`max_concurrency` 覆盖提交、长时生成和未知提交对账，不是提交 HTTP
瞬时并发；Provider 明确终态后才释放。`requests_per_minute` 使用每账号固定一分钟窗口。
账号池忙、限流或冷却时，任务通过 Redis 延迟队列稍后重试且不增加 Provider 尝试次数；
永久失效或人工 drain 只关闭新准入，已接收任务仍粘在原账号轮询。历史数据库形状冻结在
`0012_generation_contract_v1`（依赖 `0011_provider_monitoring`），只能在临时 oracle 库
核对，不得进入生产发布时序。冻结 Monitor 行为曾保存路由健康、真实上游终态和
批量账号失效，生成触发/恢复事件并投递签名告警；运营停用入口、外部告警接收端和真实
账号 canary 仍需在生产控制面与基础设施中完成配置和验收。详见
[历史 Python Provider 监控语义（离线 oracle）](provider-monitoring.md)。

这些历史适配器永久保持 `production_ready=False`。真实密钥、模型权限、地区、额度、
账单、限流、异常提交对账和 OBS 转存必须由 new-api route 单独验收；Python 标志不能被
改成生产放行开关。

可灵和阿里百炼没有文档化的免费健康端点，当前 `healthcheck()` 只代表本地配置可构造并
返回 `True`；它不能发现真实上游停机、账号失权或区域故障。Monitor 对这两家仍会根据真实
任务终态计算成功率，但生产必须另外接入合规 synthetic canary 或经供应商确认的只读探针，
不能把 readiness 中的恒真健康项当作真实 SLA 证据。火山方舟当前使用 `/ping` 探测，仍需
在真实地区和账号下完成鉴权、限流与故障演练。

## 冻结的异步行为语义

三家均通过 Relay 的统一异步流程运行：

```text
提交一次 -> 保存 provider_task_id -> Provider Sync 主动轮询
         -> processing / failed / cancelled
         -> succeeded -> 立即转存私有 OBS -> 成功后通知客户平台结算
```

轮询使用持久化错误计数和指数退避，固定批次使用游标轮转，防止长期任务饿死队尾；
回调与轮询共享同一套事件去重和单调状态合并。供应商临时结果地址不作为长期资产地址。

官方创建接口没有公开、可核准的通用 `Idempotency-Key`。Relay 不会在 POST 超时或
断连后盲目重提；这种情况会标记为提交结果未知并进入人工/供应商对账门禁。可灵额外
使用稳定 `external_task_id=job.id` 做提交前恢复查询，但官方仍未承诺它本身就是
幂等键。

## 可灵 Kling 3.0

- 地区域名必须显式选择：中国 `https://api-beijing.klingai.com`，境外
  `https://api-singapore.klingai.com`。
- T2V：`POST /text-to-video/kling-3.0`；I2V：
  `POST /image-to-video/kling-3.0`；Turbo 使用独立的 `kling-3.0-turbo` 路径。
- 查询：`GET /tasks?task_ids=...` 或 `external_task_ids=...`。
- 状态：`submitted / processing / succeeded / failed`；结果来自 `outputs[].url`。
- 官方回调没有公开验签契约，因此当前不接收它作为可信终态，只使用主动查询。

官方资料：[鉴权](https://kling.ai/document-api/api/get-started/authentication)、
[文生视频](https://kling.ai/document-api/api/video/3-0-omni/text-to-video)、
[图生视频](https://kling.ai/document-api/api/video/3-0-omni/image-to-video)、
[回调](https://kling.ai/document-api/api/get-started/callbacks)、
[错误码](https://kling.ai/document-api/api/get-started/error-codes)。

历史 Python 工厂名和环境变量不再列入本文，避免形成可部署配置。当前实现必须把 route、
credential reference、capability revision 和账号限制写入受保护的 new-api release。

## 阿里云百炼 Wan 2.7

- 提交：`POST /api/v1/services/aigc/video-generation/video-synthesis`，必须带
  `X-DashScope-Async: enable`。
- 查询：`GET /api/v1/tasks/{task_id}`。
- T2V 使用 `resolution + ratio`；I2V 使用 `input.media[]` 且跟随输入画面比例。
- 状态：`PENDING / RUNNING / SUCCEEDED / FAILED / CANCELED / UNKNOWN`。
- 结果 `output.video_url` 和任务查询只保留约 24 小时，成功后必须立即转存。
- 地区、Workspace 和 API Key 必须绑定配置，不能把不同地区的地址与密钥混用。

官方资料：[文生视频](https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference)、
[图生视频](https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference)、
[错误码](https://www.alibabacloud.com/help/en/model-studio/error-code)、
[异步通知](https://www.alibabacloud.com/help/en/model-studio/async-task-api)。

历史 Python 工厂名和环境变量不再列入本文；new-api route 必须使用独立 secret bundle 和
经过审核的模型/地区映射。

## 火山引擎方舟 Ark / Seedance

- Base URL：`https://ark.cn-beijing.volces.com/api/v3`。
- 创建：`POST /contents/generations/tasks`；查询：
  `GET /contents/generations/tasks/{id}`。
- 请求在顶层显式传 `resolution`、`ratio` 和 `duration`，不再依赖提示词尾部的弱校验参数。
- 状态：`queued / running / cancelled / succeeded / failed`；结果来自
  `content.video_url`。
- `callback_url` 虽然可提交，但官方没有公开回调验签协议，因此当前不发送。
- 模型/Endpoint 的能力不同，环境配置必须逐模型给出模式、时长、比例和分辨率，
  适配器不会把一个通用矩阵冒充为全部模型能力。

官方资料：[创建任务](https://api.volcengine.com/api-docs/view?action=CreateContentsGenerationsTasks&serviceCode=ark&version=2024-01-01)、
[查询任务](https://api.volcengine.com/api-docs/view?action=GetContentsGenerationsTask&serviceCode=ark&version=2024-01-01)、
[视频参数](https://www.volcengine.com/docs/82379/2298881?lang=zh#resolution)、
[连通性检查](https://www.volcengine.com/docs/82379/1339360?lang=zh#2-2-connectivity-test)。

历史 Python 工厂名和环境变量不再列入本文；new-api route 必须逐模型冻结 capability 和
endpoint，并保持 credential 仅存在于受保护运行时。

## new-api route 上线前验收门禁

每家渠道至少要完成：低成本真实 T2V/I2V、明确拒绝、限流、查询超时、任务失败、
任务长期运行、临时 URL 转存、账单核对、密钥轮换、地区错误、模型无权限，以及
“POST 结果未知”的人工对账演练。完成后只能批准对应的 new-api route release，不能修改
Python oracle 的 `production_ready=False`，也不能一次性给所有渠道放行。离线结构测试
不能替代真实账号 canary、账单核对和故障演练。
