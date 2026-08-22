# 统一生成 API v1 双方冻结确认清单

状态：待客户平台负责人、Relay 负责人和内部 TikTok 运营系统负责人联合签字  
冻结对象：`api_version=v1`、`schema_version=1`  
用途：在各方独立发布前确认同一份接口、状态、计费和故障语义

## 1. 冻结产物

签字时必须记录以下文件的 Git commit 和 SHA-256；任何一项变化都使原确认失效：

| 产物 | SHA-256 | 已审阅 |
| --- | --- | --- |
| `contracts/relay-generation-v1.openapi.yaml` | 待填写 | ☐ |
| `contracts/callback-event-v1.schema.json` | 待填写 | ☐ |
| `contracts/error-codes-v1.json` | 待填写 | ☐ |
| `docs/generation-api-v1.md` | 待填写 | ☐ |
| `examples/internal-tiktok/polling.py` | 待填写 | ☐ |
| `examples/internal-tiktok/callback_receiver.py` | 待填写 | ☐ |

冻结 commit：`________________________`  
计划 staging 窗口：`________________________`  
计划生产窗口：`________________________`

## 2. 必须共同确认的协议决策

- [ ] 浏览器只调用客户平台；客户平台和内部 TikTok 都以服务身份调用 Relay。
- [ ] `customer-platform` 与 `internal-tiktok` 使用不同 client ID、tenant UUID 和 API key。
- [ ] 普通 TikTok 凭证没有 `operations:submission-reconciliation` scope；人工对账使用独立运维凭证。
- [ ] POST 必须携带 `/v1/models` 返回的 `expected_capability_revision`。
- [ ] 一个逻辑任务终身复用同一个 `Idempotency-Key` 和相同请求；网络结果不确定时不得换 key。
- [ ] `id == job_id`，且 expected/capability revision 在任务生命周期中不可变。
- [ ] 状态动作固定为：非终态 `hold`、`succeeded` `settle`、`failed/cancelled` `release`。
- [ ] Accepted 即使显示 `settle` 也不是结算凭证；必须取得完整 GET 或可信回调及全部转存产物。
- [ ] `reconciliation_required` 不结算、不释放、不换渠道；只有受控运维对账可以推进。
- [ ] 回调签名原文是 `<timestamp>.<event-id>.<raw-body>`，算法 HMAC-SHA256，版本 `v1`。
- [ ] 回调接收端严格拒绝未知字段、重复 JSON key、过期时间戳和同事件 ID 异消息体。
- [ ] 回调产物只有持久对象元数据；调用方另行签发短时下载 URL 并校验大小与 SHA-256。
- [ ] `schema_version=1` 是精确结构；新增字段先升 schema version 并经过兼容窗口。
- [ ] 本协议借鉴主流异步资源语义，但不是 OpenAI SDK wire/drop-in 兼容接口。

## 3. TikTok 负责人必须填写

| 决策 | 确认值 |
| --- | --- |
| TikTok 生产 client ID | `internal-tiktok` / 其他：________ |
| 独立 tenant UUID | `________________________` |
| 状态主链路 | ☐ 主动回调 + 轮询补偿　☐ 纯轮询 |
| 回调公网 HTTPS URL（若使用） | `________________________` |
| 回调签名密钥保管系统/负责人 | `________________________` |
| TikTok 请求 RPM | `________` |
| TikTok 同时活动任务上限 | `________` |
| 单任务最长业务等待时间 | `________`；到期只转后台，不写失败 |
| 允许模型与模式清单 | `________________________` |
| 输入素材 URL 有效期下限 | `________` 分钟 |
| 产物保存位置和下载审计负责人 | `________________________` |
| 失败/取消后的业务重提策略 | 必须新建逻辑任务；具体审批：________ |
| 告警和值班渠道 | `________________________` |

TikTok 的 RPM、活动任务上限和值班渠道不得留空。没有独立限流和容量边界，不得签署生产
冻结；开发共享默认值不能作为生产确认。

## 4. Staging 联合验收

- [ ] 使用 TikTok 独立凭证读取 `/v1/models`，验证 `ETag/304` 和 schema 版本。
- [ ] 缺失 revision 的 POST 返回 `422`，且真实渠道没有收到请求。
- [ ] 正确提交返回 `202`，重复相同请求得到同一任务，异体同 key 返回 `409`。
- [ ] 模拟 POST 响应丢失，用原 body/key 重试，供应商后台只有一个任务。
- [ ] TikTok 凭证不能读取客户平台 tenant 的任务或产物，返回统一 `404`。
- [ ] TikTok 普通凭证调用对账接口被拒绝；`operations:submission-reconciliation` 运维凭证仅能处理本 tenant。
- [ ] 回调正确签名可接收；坏签名、过期、重复 key、同 ID 异体分别按契约拒绝。
- [ ] 回调超时触发重复投递，同事件只推进一次业务状态和一次预占/成本动作。
- [ ] `reconciliation_required` 全程保持 `hold`，未知提交不会自动切渠道或退款。
- [ ] `succeeded` 仅在全部产物转存后出现，产物数量与 `output.count` 一致。
- [ ] `failed/cancelled` 的 `outputs=[]`，且执行一次 `release`；成功只执行一次 `settle`。
- [ ] 短时下载 URL 过期后可重新签发，下载字节通过大小和 SHA-256 校验。
- [ ] 触发 `429/5xx`、回调死信和轮询补偿，告警到达已确认的值班渠道。
- [ ] 真实渠道按每个获批 `model + mode` 完成至少一条成功和一条明确失败的账单核对。

验收报告位置：`________________________`  
故障演练记录：`________________________`  
未关闭问题：`________________________`

## 5. 签字

只有全部必填项、staging 验收和生产门禁关闭后才能签署。

| 角色 | 姓名 | 结论 | 日期 |
| --- | --- | --- | --- |
| Relay 负责人 |  | ☐ 同意　☐ 拒绝 |  |
| 客户平台负责人 |  | ☐ 同意　☐ 拒绝 |  |
| 内部 TikTok 系统负责人 |  | ☐ 同意　☐ 拒绝 |  |
| 财务/计费负责人 |  | ☐ 同意　☐ 拒绝 |  |
| 安全/运维负责人 |  | ☐ 同意　☐ 拒绝 |  |

任一方拒绝或留有未关闭阻断项时，状态保持“冻结候选”，各方不得按生产已兼容对外发布。
