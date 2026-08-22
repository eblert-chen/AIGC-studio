# 模型能力配置 v1

客户平台把模型目录中的 `generation` 能力作为界面自适应、公司授权、任务准入和能力快照的唯一事实源。新建或更新模型时使用以下完整结构：

```json
{
  "schema_version": 1,
  "modes": {
    "text_to_video": {
      "input_media_types": ["image", "video", "audio"],
      "supports_face": true,
      "required_resource_keys": [],
      "limits": {
        "max_prompt_length": 2000,
        "max_images": 9,
        "max_videos": 3,
        "max_audio": 3,
        "duration_seconds": [5, 10],
        "aspect_ratios": ["16:9", "9:16"],
        "resolutions": ["720p", "1080p"],
        "output_counts": [1, 2, 3]
      }
    }
  }
}
```

支持的模式为 `text_to_image`、`text_to_video`、`image_to_video` 和 `video_to_video`。每个模式独立声明输入类型、人脸能力、资源前置条件和参数集合，因此同一模型可以让不同模式使用不同的 9 图 + 3 视频 + 3 音频或 4 图 + 3 视频 + 3 音频限制。

## 校验规则

- 已发布模型至少要有一个完整且可用的模式。草稿可以暂时没有能力，发布时会拒绝空配置。
- `max_images + max_videos + max_audio` 不得超过 15；单类上限不得超过 15。
- 时长范围为 1 至 3600 秒，单次产物数范围为 1 至 16。
- 当前按秒计费按单个产物的生成时长报价，因此每个模式必须声明 `output_counts: [1]`；按条计费模型可声明 1 至 16 个产物选项。
- `input_media_types` 必须与对应的输入上限一致。`image_to_video` 至少允许一张图片，`video_to_video` 至少允许一个视频。
- 比例使用正整数 `宽:高`；分辨率使用最长 32 字符的安全标识。
- 未知字段、未知模式、空参数集合和越界值在保存阶段直接拒绝。
- 对外显示名、渠道键会去除首尾空白，并拒绝纯空白值。

## 公司授权覆盖

公司授权的 `config_override` 使用相同的 `schema_version + modes` 外层，但模式字段和限制字段可以稀疏提交。覆盖只能收紧基础能力：

- 只列出部分模式时，公司只能使用这些模式。
- 列表字段只能取基础集合的子集。
- 输入数量和提示词长度只能降低。
- `supports_face` 只能从 `true` 关闭为 `false`。
- `required_resource_keys` 只能增加前置授权要求。

模型能力收紧后，如果现有启用中的公司覆盖不再兼容，重新发布会失败，直到管理员修正或停用对应授权。

## API 与任务快照

- 平台管理员模型响应和公司可用模型响应都返回服务端计算的 `effective_capabilities`。
- 制作台只读取 `effective_capabilities`，按模式显示可用输入、比例、分辨率、时长、产物数和人脸开关；缺失、空白、畸形或没有可用模式时默认禁止提交，不能回退到演示能力。
- 切换模型或模式时，制作台会同步删除超限或不支持的素材，把比例、分辨率、时长和产物数复位到新能力允许值，并在不再支持人脸时关闭开关。提交按钮还会基于当前能力再清洗一次，避免界面状态更新竞态把旧值送出。
- 任务请求可携带读取模型时得到的 `expected_capability_version`。服务端在任务创建期间核对当前已发布、启用模型的版本；版本已变化时返回 409，并且不会创建任务、预占余额或写入 Outbox。相同幂等键对已创建任务的重放仍返回原任务及原能力快照。
- 任务创建在报价和余额预占前用同一份生效能力校验请求。`request_payload` 只接受 `mode`、`prompt`、`assets`、`duration_seconds`、`aspect_ratio`、`resolution`、`output_count`、`face_enabled` 和 `metadata`；未知字段默认拒绝，`metadata` 必须为对象。
- 客户 metadata 只作为关联信息嵌入 Relay 的 `metadata.client_metadata`；公司、用户、任务、请求追踪和素材引用由平台自己生成，浏览器不能借 metadata 覆盖这些字段或向 Provider 注入未声明参数。
- 任务保存完整 `effective_capabilities` 快照；后续改模型、改授权或改价格不会改变历史任务。
- `billing_mode` 固定在全局模型目录，公司授权保存该公司的整数分单价。按秒模型以时长计价，按条模型以产物数计价。

旧数据库中的能力声明仍可读取和执行，便于渐进迁移；管理员的新写入与公司新覆盖必须通过 v1 严格校验。
