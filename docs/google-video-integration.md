# Google 视频模型接入边界（Omni 1.1 / Veo 3.1）

更新日期：2026-09-01

本文记录代码审阅后的模型身份与最窄可执行能力，不是生产开通声明。模型目录为
`backend/new-api-relay/generationprofile/google_video_models.v1.json`；任何账号、密钥、区域、配额、价格、付费请求、产物转存或供应商账单证据都不在该文件中。

## 精确模型身份

| 稳定公共模型 | API 表面 | 精确上游 ID | Profile |
| --- | --- | --- | --- |
| `gemini-omni-1.1-flash` | Gemini Developer API | `gemini-omni-1.1-flash` | `google.gemini.omni-1.1-flash.basic-video.v1` |
| `veo-3.1` | Gemini Developer API | `veo-3.1-generate-preview` | `google.gemini.veo-3.1.video.v1` |
| `veo-3.1-fast` | Gemini Developer API | `veo-3.1-fast-generate-preview` | `google.gemini.veo-3.1.video.v1` |

本轮可执行清单只包含 Gemini Developer API。Vertex GA `-001` ID 与 Gemini Preview ID
不是别名；当前 Vertex 任务链缺少受保护结果证明、OAuth/GCS 产物转存和付费路由验收，
因此已从 Relay 可执行清单及 Platform 公共映射中移除。代码中保留的 Vertex profile
只是未来实现上限，不能绑定 release 或路由。

官方依据：

- [Gemini Omni Flash 模型卡](https://ai.google.dev/gemini-api/docs/models/gemini-omni-flash)
- [Gemini Omni 生成与编辑协议](https://ai.google.dev/gemini-api/docs/omni)
- [Gemini API Veo 3.1](https://ai.google.dev/gemini-api/docs/veo)

## 当前候选能力

| 模型族 | 候选模式 | 输入上限 | 时长 | 画幅 | 分辨率 | 输出 |
| --- | --- | --- | --- | --- | --- | --- |
| Omni 1.1 Flash | 文生视频、图生视频 | 图片最多 2；无视频/音频输入 | 3–10 秒 | 16:9、9:16 | 360p、720p、1080p、4k | 1 个 MP4 |
| Veo 3.1 / Fast | 文生视频、单图生视频 | 图片 1；无视频/音频输入 | 8 秒 | 16:9、9:16 | 720p、1080p、4k | 1 个 MP4 |

这些值是 Profile 的可执行上限，路由仍可继续收窄。`supports_face=false` 表示产品不展示人脸库控件，不代表模型无法生成人物。

Veo 上游允许 4、6、8 秒，但 1080p/4k 只允许 8 秒。当前 capability schema 只能分别列举时长和分辨率，不能表达二者条件关系；若同时发布 4/6/8 和 720p/1080p/4k，前端会产生上游必然拒绝的组合。因此 v1 先发布“8 秒 × 三种分辨率”这一完全有效的矩形子集。若要开放短视频，需要先为 capability 增加条件化规格约束，而不是在 adapter 中静默改值。

Veo 官方提示词限制是 1024 Token，而当前公共字段以字符计数。Profile 的 512 字符只是保守的交互层上限；正式提交仍需执行供应商 Token 校验，不能把字符数宣称为等价 Token 数。

## 明确阻断的语义

Omni 上游支持 `reference_to_video`、`edit` 和 `extend`，但当前通用 `video_to_video` 模式无法表达：

- `previous_interaction_id` 所绑定的精确历史视频和模型状态；
- “编辑现有内容”与“只在片尾延展”的不同含义；
- 上传视频与模型上一轮生成视频的不同区域限制；
- 不确定提交后必须继续查询原 interaction、禁止重发的幂等约束。

所以 Omni v1 只发布无状态文生视频和图生视频。图生视频可接收最多两张图：一张用于普通图生视频，两张可表达官方文档中的首尾帧或主体引用输入；adapter 保留两份独立媒体内容，不把第二张静默丢弃。编辑或延展不能伪装成普通 `video_to_video`，也不能仅靠提示词猜测。这里是当前有效实现上限，并不是声称 Omni 上游本身不支持编辑和延展。

Veo 上游还支持首尾帧插值、最多三张引用图，以及使用既有 Veo 视频延展。现有转换器只保留一个初始图片字段，没有保留 `lastFrame`、`referenceImages` 或上游视频对象，因此这些能力同样未发布。后续协议版本必须分别建模媒体角色和谱系，并在请求快照中冻结。

## 音频与产物

Gemini Developer API 的 Omni 和 Veo 视频包含音频；当前能力结构没有可切换的音频输出字段，因此界面只能把它显示为模型事实，不能展示一个实际上无法保证的开关。

每个成功结果必须先验证为单个 MP4 并转存到平台控制的持久存储，之后才能结束任务。临时 URI、base64 响应或操作完成状态本身都不是可交付成功。

## 上线前闭环

模型目录中 `acceptance_candidate` / `route_acceptance_required` 只允许进入验收流程。公开给用户前还必须完成：

1. 离线签名的 generation release，精确绑定通道、模型、Profile revision 与能力 revision；
2. Gemini `x-goog-api-key` 的受控存储与最小权限，不将凭证写入任务或产物 URL；
3. 真实付费 T2V/I2V 提交、轮询、失败、安全拦截、未知结果查询和一次性结算；
4. 带鉴权下载、内容类型/大小/MP4 校验、持久化转存和临时源清理；
5. 模型/分辨率/音频维度的供应商成本费率、实际账单与任务成本对账；
6. 桌面与手机创作页只根据 Relay 返回的 `effective_capabilities` 渲染，不复制本文或 JSON 的能力常量。

未完成这些门禁时，目录记录不能被描述为“已接入生产”。
