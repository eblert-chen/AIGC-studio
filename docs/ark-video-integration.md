# 火山方舟视频模型接入

核对日期：2026-08-31。本次接入的是火山方舟 Ark 视频生成 API，不是即梦网页接口、LAS 算子或海外 BytePlus 接口。

代码支持、账号开通、路由验收、Platform 发布、客户授权是不同状态。此文档和生成的声明材料不代表完成真实视频生成验收，也不会给未授权的用户增加模型。

## 当前目录

单一源文件为 `backend/new-api-relay/generationprofile/seedance_models.v1.json`。Relay 适配、离线声明准备、Platform 集成测试和前端回归均使用这份目录，不在客户前端维护另一份供应商名单。

| Platform 公共模型标识 | 显示名称 | 方舟精确模型 ID | 本次输出合同 | 生命周期 |
| --- | --- | --- | --- | --- |
| `seedance-2.5` | Seedance 2.5 | `doubao-seedance-2-5-260628` | 4–30 秒；480p / 720p / 1080p | 待渠道验收 |
| `seedance-2.0` | Seedance 2.0 | `doubao-seedance-2-0-260128` | 4–15 秒；480p / 720p / 1080p / 4k | 待渠道验收 |
| `seedance-2.0-fast` | Seedance 2.0 Fast | `doubao-seedance-2-0-fast-260128` | 4–15 秒；480p / 720p | 待渠道验收 |
| `seedance-2.0-mini` | Seedance 2.0 Mini | `doubao-seedance-2-0-mini-260615` | 4–15 秒；480p / 720p | 待渠道验收 |
| `seedance-1.5-pro` | Seedance 1.5 Pro | `doubao-seedance-1-5-pro-251215` | 4–12 秒；480p / 720p / 1080p | 仅已有接入；2026-09-21 14:00（北京时间）停止服务 |
| `seedance-1.0-pro` | Seedance 1.0 Pro | `doubao-seedance-1-0-pro-250528` | 2–12 秒；480p / 720p / 1080p | 待渠道验收 |
| `seedance-1.0-pro-fast` | Seedance 1.0 Pro Fast | `doubao-seedance-1-0-pro-fast-251015` | 2–12 秒；480p / 720p / 1080p | 待渠道验收 |

上述七个型号来自[方舟当前模型清单](https://docs.volcengine.com/docs/82379/1330310#7571da3f)。1.5 Pro 已停止新购，不能作为普通新接入候选；Lite 两款已经停止服务。旧 `doubao-seedance-1-0-pro-fast-250610` 没有当前官方支持或别名证据，保留历史记录但不用于新接入，不静默映射成 `251015`。[方舟模型下线公告](https://docs.volcengine.com/docs/82379/1350667)

## 本次实现的能力边界

- 全部型号：文生视频，输出一个 MP4，固定整数秒数和六种比例：16:9、4:3、1:1、3:4、9:16、21:9。
- 2.5 / 2.0 / Fast / Mini：图片参考与视频参考；兼容当前公共协议，最多 9 张图、3 段视频、3 段音频，总计不超过 15 个。图生路径至少一张图，视频参考路径至少一个视频。
- 1.5 Pro / 1.0 Pro：文生、单首帧、首尾帧；按图片顺序映射第一张首帧、第二张尾帧。
- 1.0 Pro Fast：文生、单首帧，不声明首尾帧。
- 2.5 的多模态路径显式指定 `omni_reference_task_type=reference`，保持“参考素材生成新视频”的语义；不让 `auto` 猜成编辑或延长。
- 企业空间沿用已授权的多模态素材链路；个人空间仍只开放现有纯文本模式，不通过本次接入放开个人素材或人脸授权。

这是**全部当前型号的兼容子集**，不是对方舟全部功能的覆盖。2.5 官方支持的 50 素材、纯音频输入、`adaptive` 比例、`duration=-1`、视频编辑/延长、MOV，以及 seed、固定镜头、输出音频开关、联网搜索、Draft 等未纳入当前公共请求合同；不能从前端透传任意 metadata 伪装支持。后续需要版本化扩展输入角色、任务类型、报价和重试/恢复合同。[创建任务 API](https://docs.volcengine.com/docs/82379/1520757)、[Seedance 2.5 任务约束](https://docs.volcengine.com/docs/82379/2607688)

2.5 的 1080p、2.0 的 4K 使用 H.265 / 10bit。产物按真实媒体类型私有化存储；浏览器播放兼容性仍需用验收产物确认，不等于所有设备都可原生播放。真人肖像参考还需满足方舟授权素材要求，普通上传或关闭 `face_enabled` 并不构成授权证明。[输出规格](https://docs.volcengine.com/docs/82379/1330310#7571da3f)、[方舟肖像素材规则](https://docs.volcengine.com/docs/82379/2608626)

## Relay → Platform → 共享导演控制台

1. Relay 的不可变 adapter profile 给出实现上限；型号目录进一步收窄具体模型的能力。
2. 模型 release 绑定公共标识、精确供应商标识、profile revision 和能力。声明校验拒绝 Mini 的 1080p、Fast 的双帧、旧型号的视频参考等非法扩张。
3. 先审阅并签署模型 release，将其嵌入路由，再冻结候选源码与镜像；随后对真实渠道和账号执行受控验收并签署 route acceptance。仅有模型名称、API Key 或成功 HTTP 响应不构成验收。
4. Relay `/v1/models` 发布配置路由的安全能力交集。Platform 的目录同步 worker 按公共标识幂等生成未发布草稿。
5. 管理员核对差异、设置显示名称与经营价格，完成批准、发布和公司/个人授权。目录同步不会代替这些动作。
6. 企业/个人模型发现接口返回服务端计算的有效能力、报价和就绪证据。首页与创作页的同一个 Director Deck 直接使用它们，不另造方舟专用创作栏、不向浏览器暴露渠道密钥。

前端切换模型会重新约束时长、分辨率、模式和素材数量。例如 2.5 的 30 秒或 2.0 的 4K 不能残留到 Fast/Mini 请求。视频输入路径统一标注“视频参考”，不承诺像素级重绘或编辑。

## 准备声明，不修改运行配置

在 `backend/new-api-relay` 目录运行离线命令，填写本次真实审阅身份、原因和时间：

```powershell
go run ./cmd/relay-ark-video-plan `
  --created-at 2026-08-31T00:00:00Z `
  --created-by your-release-reviewer `
  --reason "Review Ark video model onboarding"
```

命令向 stdout 输出六个正常候选的 **unsigned_model_release**，包含精确 profile revision 和型号能力；不读取密钥、不联网、不签名、不写环境或数据库。可用 `--model seedance-2.5` 只准备一个型号。仅复核既有 1.5 Pro 路由时使用 `--include-existing-only`；到所填审阅时间的 EOS 边界即排除该型号。历史时间仅用于复核历史材料，不恢复当前服务资格；不会伪造 EOM 前的审阅日期。

Platform 声明预览命令从相同目录生成现有管理员模型 API 的草稿请求正文：

```powershell
.venv-ci/Scripts/python.exe backend/platform/scripts/prepare_ark_video_drafts.py
```

该命令在仓库根目录运行，只输出预览、不写数据库；默认按当前 UTC 时间筛除已经 EOS 的型号，历史复核可显式传 `--as-of` UTC 时间。正式环境仍以 Relay 自动目录同步为主；不要用 bootstrap、直接改表、前端静态模型列表或自动批准绕过发布边界。

按 [模型发布与路由签名流程](../backend/new-api-relay/docs/platform-model-release-signing.md) 完成正式签署和验收；不要覆盖已有冻结 candidate、schema 或已签署路由。目录显示名可以在 Platform 草稿编辑中采用此文件的 `display_name`，现有管理员自定义名字不应被后台同步覆盖。

## 验证范围

- Relay：型号目录严格解析、旧 profile 兼容、精确请求转换、错误参数提前拒绝、生命周期与未知提交行为。
- Platform：规范目录 revision、重复同步只产生一份草稿、无自动价格/授权/发布、未批准前不出现在客户可用模型列表。
- 前端：七型号的服务端能力、30 秒/4K 边界、切换清理、幂等请求恢复、桌面/390px/320px 操作可达和共享 composer。

模拟渠道与浏览器拦截仅用于回归，不作为真实渠道验收、供应商成本或生成成功证明。任何付费验收必须有明确的账号、模型范围与预算。

### 本轮本地验证（2026-08-31）

- Relay：`generationprofile`、`generationrelease`、`relay/channel/task/doubao`、`cmd/relay-ark-video-plan` 测试通过；受影响的 service 能力、release 与目录测试通过。
- Platform 方舟专项：10/10 通过，包括七型号草稿同步、未审批隔离、企业/个人发现和 EOS 边界。
- 前端方舟专项：59/59 单元测试、15/15 Playwright 通过。修正时长标签换行后，1440px、390px、320px 的受影响主用例再次通过；实际文字行盒保持单行，手机参数按钮至少 44×44px。截图位于 `artifacts/ark-video-creation/`，其中的账号、价格和路由是测试数据。
- `npm run build` 通过；`npm test` 尚有发布指纹门禁失败：`tests/relay-cutover-compose.test.mjs` 的本地 Platform 源码快照与 `.env.example` 冻结 revision 不一致。本轮没有重写冻结 revision、candidate 或签名证据。
- 额外 Platform 回归两次报告 `test_relay_catalog_sync_worker.py::test_periodic_worker_creates_one_system_audited_unpublished_draft` 的审计顺序断言失败，首项实际为 `model.relay_capability.candidate_sync`，期望为 `model.create`。查询按 `created_at, id` 排序，而 ID 为随机 UUID；同时间戳会打乱调用顺序是与现象一致的解释，但原日志未采集时间戳和 UUID，不能据此认定原失败原因已证实。本轮未修改该测试或审计实现，仍需独立复现。

当前运行配置仍只有 Seedream 图片路由，未将本轮视频声明应用到真实 Relay/Platform。因而以上结果是代码与合同验证，不是闭合发布候选或真实生成验收。
