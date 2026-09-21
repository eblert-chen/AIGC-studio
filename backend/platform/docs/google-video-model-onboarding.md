# Google Omni / Veo 模型入目录边界

`platform_api/catalog/google_video_models.v1.json` 只负责 Platform 的公共模型身份、
显示名称、`provider_key=google` 归属和待审计价形态。它不包含或证明模型能力、
Google 账号权限、API Key、真实路由、供应商价格、用户价格或工作区授权。

本轮公开草稿只允许 Gemini Developer API 的精确身份：Omni 使用
`gemini-omni-1.1-flash`，Veo 使用 `*-generate-preview`。Vertex AI 的
`*-generate-001` 没有闭环的 Platform result proof、OAuth/GCS 制品转存和付费
no-redirect 路由验收，因此不得合并进同名 Veo 公共草稿，也不得形成发布、定价或授权。
Relay 中保留的 Vertex profile 只是未来实现上限；没有受审 provider manifest 记录时，
它不能绑定 model release 或路由。

入目录顺序固定为：

1. Relay 的已签名模型发布和真实路由产生 revision-bound `/v1/models` 项目。
2. Platform catalog-sync 仅在公共模型 ID 精确命中受审清单时，使用 Relay live
   capability 创建 `active=false`、`published_at=null` 的 Google 草稿。
3. 平台所有者审阅 capability candidate；当前 revision 的真实 route evidence 未就绪
   时，批准和发布继续失败关闭。
4. 管理员分别决定用户固定积分价格和个人/企业授权。清单、同步 worker 和能力批准
   均不会创建价格版本或 grant。
5. 真实 Google staging 生成、制品转存及供应商账单对账通过后，才能形成独立发布证据。

`scripts/prepare_google_video_drafts.py` 是只读预览工具。它不接受 `--apply`，不读取
运行时密钥、不访问数据库或网络，也不会把清单内容伪装成 Relay live capability。
