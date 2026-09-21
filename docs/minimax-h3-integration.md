# MiniMax H3 视频接入与火山完成度核对

核对日期：2026-08-31。

本轮完成 H3 / H3-Max 在 Go Relay、Platform 声明与任务校验、共享创作栏中的**兼容子集代码接入**。没有调用真实付费视频接口，没有修改运行路由、经营价格、客户授权或发布候选。代码支持不等于账号开通、渠道验收或客户已可用。

## 型号与公开能力

唯一型号来源为 `backend/new-api-relay/generationprofile/minimax_h3_models.v1.json`，同时供 Relay、离线声明和 Platform/前端回归使用。客户端没有第二份供应商模型名单。

| Platform 标识 | 精确供应商模型 | 本轮模式 | 时长 | 分辨率 |
| --- | --- | --- | --- | --- |
| `minimax-h3` | `MiniMax-H3` | 文生视频、图片参考、视频参考 | 4–15 整数秒 | 768p / 2k |
| `minimax-h3-max` | `MiniMax-H3-Max` | 文生视频 | 5–15 整数秒 | 480p / 768p |

两款均要求 1–7000 个 Unicode 码点的非空提示词，单次输出一个 MP4，支持六种固定比例：16:9、4:3、1:1、3:4、9:16、21:9。请求中的 `768p`、`2k` 原样保留其语义，发送给供应商时映射为 `768P`、`2K`，不替换为 720p、1080p。

官方依据：[V2 创建任务](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create)、[V2 查询任务](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-query)、[视频生成指南](https://platform.minimax.io/docs/guides/video-generation)、[官方 OpenAPI](https://platform.minimaxi.com/docs/api-reference/video/generation/api/v2-video-generation.json)。

### 参考素材的准确语义

- H3 图片参考：至少一张图，最多 9 图 / 0 视频 / 3 音频。
- H3 视频参考：至少一段视频，最多 6 图 / 3 视频 / 3 音频。
- 所有路径都限制总素材数不超过 12，单次输出数为 1。
- 图、视频、音频分别明确映射为 `reference_image`、`reference_video`、`reference_audio`，不是首帧/尾帧插值，也不承诺原视频内容逐帧保留。
- 官方允许最多 9 图、3 视频、3 音频，但总数最多 12。冻结的公共协议只有分类型上限，因此视频参考暂用 6/3/3 的安全子集；9 图 + 1 视频等其他组合没有一并开放。切换模型时告知素材移除；后端直接提交超限请求则拒绝，适配器不静默丢素材。
- 个人空间继续只开放已有的纯文本路径，不因接入新供应商而自动获得企业素材或人脸权限。

### 尚未声明的官方功能

H3/H3-Max 官方首尾帧生成采用自适应比例，会忽略请求中的固定比例；当前产品协议无法准确表达该行为。因此本轮不开放首尾帧，H3-Max 也没有伪装成支持图片参考。中间帧、纯音频输入、`mm_file`、Base64、Context IR 提示词增强、再次生成及其他高级选项未接入。

`/v2/h3_context_ir` 返回增强提示词，不是视频成品。本轮不会自动调用它，也不会暗中增加一笔生成。

供应商另有媒体文件大小、时长、分辨率、编码及合计片长限制，详见单一目录中的官方来源与注释。当前公共能力协议没有这些细粒度字段；现有素材服务的通用校验不等于完整 H3 媒体预检，供应商仍会做最终校验。本轮没有新增逐文件探测/转码流程，也没有把这些限制伪装成前端已验证状态。

供应商临时下载地址过期后的自动重查续期也未新增；继续沿用现有转存重试与人工核对机制，不重新 POST 生成视频。

## 各服务的实现

### Relay

- `generationprofile` 注册独立 V2 profile，`generationrelease` 将其与精确型号、公共标识、能力 revision 绑定。拒绝未知 H3 版本、无 release 的 H3 路由、Max 借普通 H3 的 2k / 4 秒 / 参考素材扩权。
- 中国区显式配置 `https://api.minimax.cn`，国际区可用 `https://api.minimax.io`。复用原生 MiniMax channel type 35；旧 `api.minimaxi.com` 会失败关闭，旧 Hailuo 的默认域名与 V1 协议不变，不把 H3 自动发到旧默认地址。
- H3 使用 `POST /v2/video_generation`、`GET /v2/query/video_generation/{task_id}`。不使用旧 `file_id` 换下载链接流程。
- 请求只发送模型、内容、时长、分辨率、比例五个白名单字段；浏览器不能透传供应商角色、模型 ID、回调或任意 metadata 覆盖已接纳任务。
- POST 与轮询都禁止重定向，未知提交不重发、不切渠道。轮询按作业原路由绑定的精确模型与加密凭据版本执行；共享 channel 中旧海螺缺少可选模型快照的历史任务仍保留原有路径。
- H3 任务 ID 按既有作业字段限制为最多 191 个 ASCII 字符；更长的 POST 确认不能冒充已安全接纳，后续也不会因此重复创建任务。
- 查询结果需提供正确任务、模型、状态、任务类型、媒体类型、输出规格及唯一 HTTPS 成品地址。重复 JSON key、超限响应、矛盾或缺失证据停止自动推进，进入人工核对。
- H3 使用独立 `minimax-h3-terminal-result-proof-v1`，旧 Ark receipt revision 保持不变。Receipt、转存清单和事务内复验共同绑定原始请求、profile、模型 release、任务、路由和产物摘要，不允许两种协议混用。
- 原始临时供应商 URL 仅供内部转存；成片经内容校验与私有存储提交成功后，才产生规范输出并清除供应商材料。失去 transfer fence 的未发布对象走持久化清理，不出现在用户作品中。
- `usage` 中的输入/输出秒数不是供应商真实账单，也不是客户积分价。没有将它直接套入旧 native token 计费；Platform 继续是客户结算的唯一所有者。

### Platform

沿用既有通用目录同步、模型 release、有效能力、报价、就绪和任务接口，不新增 H3 专用旁路 API，也不修改数据库 schema。

链路为：已审阅型号与 profile → 已绑定且验收的 Relay 路由 → `/v1/models` → 未发布 Platform 草稿 → 显式审核、定价、发布和分配 → 客户有效模型发现。

新增离线工具 `backend/platform/scripts/prepare_minimax_h3_drafts.py` 输出两款模型的管理员草稿请求预览，默认 `preview_only=true`。它不连接数据库或 HTTP，不自动创建价格、批准、发布或授权。价格的 `per_second` 类型只是经营配置结构，不是当前费率承诺。

### 共享创作栏

首页和创作页仍只有一个生产 Director Deck。新增型号经服务端有效能力进入同一个模型菜单、模式、素材、参数、报价、就绪、提交与恢复逻辑，不复制创作栏。

跨模型切换清理过期的 30 秒、4k、720p、素材和人脸值；H3-Max 不显示并不支持的素材模式。没有授权或就绪证据时保持禁用，并显示一个原因。未知提交后刷新保持原请求、报价 revision 和幂等键，不拿刷新后的能力替换已经提交的参数。

另修复既有 Unicode 边界：之前 `slice` / `length` 按 UTF-16 截断，会把提示词尾部 emoji 截成半个字符。输入、计数、校验、切换与未知提交恢复现在统一按码点计数，与 Python/Go 服务端一致；未改布局或 CSS。

## 离线准备命令

在仓库根目录运行 Platform 预览：

```powershell
.venv-ci/Scripts/python.exe backend/platform/scripts/prepare_minimax_h3_drafts.py
```

在 `backend/new-api-relay` 运行 Relay 声明预览。须填写真实审阅者、原因和 UTC 时间，不能把示例身份当成审批记录：

```powershell
go run ./cmd/relay-minimax-h3-plan --created-at <UTC-RFC3339> --created-by <reviewer> --reason <review-reason>
```

可加 `--model minimax-h3` 或 `--model minimax-h3-max`。输出是未签名 `unsigned_model_release`，状态 `review_required_not_deployed`；不接受 apply、密钥、价格、发布或授权参数。正式接入继续使用既有模型发布与路由验收流程。

## 火山本轮实况核对

本节是 2026-08-31 只读检查，不修改或取代上一版冻结验收报告。

- 代码已有 Seedance 当前型号的兼容子集、生命周期边界、声明工具及 Platform/前端测试，详见 `docs/ark-video-integration.md`。这不是对所有供应商高级功能的覆盖。
- 本机实际 Relay 镜像仍为 `ai-video/new-api-relay:v6-canary-0b0b8bf5`，上报 source 为 `0b0b8bf597aeb9e69e89a04f9b4d7b1d712e3391` / 2106 个输入，不是此前冻结的含 Seedance 源码版本。
- 实际 `/v1/models` 只返回 `seedream-5` 文生图；其目录 revision 为 `sha256:b32994530fece28341d37208cd3bcd3b44525d4e5e2ae8cec009decc80655850`。运行路由没有 Seedance 视频型号。
- Platform 只读数据库检查：schema `0048_commercial_billing`；只有一个 `seedream-5` 模型，未批准、未发布、未启用；企业授权 0、个人授权 0、生成任务 0。
- 所以上一轮的准确完成度为“代码和模拟合同接入完成，真实视频启用未完成”。不能把旧浏览器 fixture 的型号、报价或就绪状态当作真实账号已开通证据。
- 既有审计排序用例风险仍保留，原失败原因没有被时间戳与 UUID 日志确证。本轮不改审计排序逻辑或冻结证据。

## 验证与仍需外部授权的工作

定向 Platform、前端及浏览器测试用的是隔离 SQLite、模拟 HTTP 与独立测试上下文，不访问付费供应商。三屏截图位于 `artifacts/minimax-h3-creation/`，画面中的测试企业、价格和模型授权不是实际经营数据。

最终本地结果记录在 `artifacts/minimax-h3-integration/`。该目录是开发测试输出，不是签署的生产发布证据。

| 检查 | 实际结果 |
| --- | --- |
| Go 7 个受影响包完整测试 | 最终 2100 个测试/子测试事件通过，27 个跳过；没有剩余失败 |
| Platform H3 + Ark 专项 | 30/30 通过，1 条既有 Starlette/httpx 弃用提醒 |
| 前端 H3/Ark/能力/pending 专项 | 72/72 通过 |
| 独立 Playwright：1440×900、390×844、320×640 | 15/15 通过，0 跳过；9 张截图 |
| 根级 `npm test` | 764/765 通过；唯一失败为 `.env.example` 的冻结 Platform 指纹与新增源码不一致 |
| `npm run build` | 通过，901 个模块；Sites 构建产物准备成功 |
| `npm run test:sites` | 7/7 通过 |
| 本轮修改范围 `git diff --check` | 通过；仅既有 App 行尾规范化提醒 |

Go 测试使用断网容器与只读源码挂载，包含 `generationprofile`、`generationrelease`、`cmd/relay-minimax-h3-plan`、`relay/channel/task/hailuo`、`model`、`service`、`controller`。27 项跳过是显式依赖外部 PostgreSQL/MySQL、生产 release 身份或旧候选迁移的门禁，不能当作已通过。首次全包运行中，新“缺失 receipt”测试把 SQLite BLOB 夹具写成 TEXT，导致一个用例及父项失败；纠正夹具为字节后完整重跑 service，891 个测试/子测试通过、1 项外部数据库门禁跳过。初次与复跑日志均保留，没有修改断言来掩盖失败。

本轮还确认快照规则目前只枚举 `generationprofile/seedance_models.v1.json`，尚不包含新的 `generationprofile/minimax_h3_models.v1.json`。发布协调者在重新冻结前必须将这个 `go:embed` 编译输入，以及新增离线 Platform 工具、接入说明和相关验收输入纳入来源范围并重验。此处仅记录遗漏；本轮没有修改 harness 规则、旧 `.env.example` 或 candidate 来使门禁变绿。

进入真实可用状态还需：确认供应商账号/地区/模型权限，评审真实成本与客户价格，授权具体模型范围和付费验收预算，构建并验证精确候选，完成路由验收、Platform 审核发布与客户分配。本轮未执行这些动作，也不修改 `.env.example`、冻结 schema/catalog、candidate 或历史签署证据；旧 v12/v13/v14 的 UNQUALIFIED 状态不变。
