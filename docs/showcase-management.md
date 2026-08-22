# 首页精选案例管理

首页精选案例由客户 Platform 管理，不属于生成 Relay。第一版只允许生产环境中受信任的
**平台所有者（Platform Owner）**管理；普通平台管理员、公司所有者和公司成员均无权读取草稿或执行变更。

## 从哪里进入

1. 使用平台所有者账号登录。
2. 完成受身份提供商签名的抗钓鱼认证；写操作还要求最近一次 step-up 认证。
3. 打开“平台管理”，选择“首页内容”。

浏览器始终只调用 Platform。上传、发布、下线、回滚、排序和撤下都由 Platform 重新鉴权，
不能依靠隐藏菜单或前端角色判断代替服务端权限。

## 支持的素材来源

- 本地图片：JPEG、PNG、WebP。Platform 会完整解码、限制像素数并重新编码，移除 EXIF、GPS、ICC、XMP 和文本元数据。
- 本人作品：只允许导入平台所有者本人个人空间中已成功、完整性已验证的 TaskArtifact。
- 视频：不接受浏览器直接上传；只能从上述本人作品导入，并由 Platform 用固定 ffmpeg 链重新编码为 H.264/AAC MP4，移除容器元数据、章节、字幕和额外数据流。

公司作品、其他用户作品、未完成任务、任意外部 URL 和未验证视频均拒绝，并且不会通过错误信息泄露其是否存在。

## 草稿、发布和回滚

- 编辑、排序和“从草稿撤下”只改变草稿，线上版本不会随之变化。
- 每次发布会生成不可变发布版本；发布必须包含至少一项，并且恰好一个 `video` 区的 Hero。
- 发布、回滚和紧急下线都同时校验草稿版本与线上指针版本。另一个标签页使用旧版本操作时会收到 `409`，需要刷新后重试，避免覆盖并发修改。
- 回滚把线上指针切到一个历史不可变版本，不偷偷改写当前草稿。
- 紧急下线清空线上指针，保留草稿和历史版本；首页收到权威空 feed 后使用随前端发布的内置案例。
- 所有写操作使用幂等键并写入不可变发布事件与平台审计日志。

第一版不提供物理删除：上传成功但尚未被草稿或发布版本引用的媒体仍保持私有，已发布过的媒体
为支持审计和回滚继续保留。运维需要监控 `showcase/media/` 容量；后续清理器必须先证明对象从未
被任何不可变发布版本引用，不能直接按“当前未展示”删除。

## 生效时间和撤回边界

- 首页 feed 使用 ETag，并通过焦点恢复、标签页恢复和短周期刷新重新验证；正常情况下约 30 秒内可见新版本。
- 生产 OBS 响应使用短时、私有、不可缓存的签名跳转。已经签发的媒体链接最长可能继续有效约 5 分钟。
- 已经下载到访客设备的副本无法远程收回。因此不得上传没有公开授权或可能含个人隐私的素材。

## 存储与部署

Showcase 对象只能写入独立的 `showcase/media/<sha256>` 前缀。Platform IAM 只需要该前缀和既有
`inputs/` 前缀的 `GetObject`、`PutObject`，不得授予删除、改 ACL 或列出整个 bucket 的权限。参考策略：
[huawei-obs-platform-policy.json](../deploy/huawei-obs-platform-policy.json)。

如果前端与 OBS 不同源，Sites 部署必须把实际 OBS HTTPS origin 配为
`PLATFORM_SHOWCASE_MEDIA_ORIGIN`。它只会进入 `img-src` 和 `media-src`，不会进入 `connect-src`。
当前 bucket/endpoint 若保持不变，值应为：

```text
https://chen-aivideo.obs.cn-south-1.myhuaweicloud.com
```

不要在源码、验收报告、截图或聊天中保存 AK/SK、Cookie、OIDC token、签名 URL或用户个人数据。

## 公开与管理 API

- `GET /api/v1/showcase/home`
- `GET /api/v1/showcase/media/{media_id}/content`
- `GET /api/v1/platform-admin/showcase`
- `POST /api/v1/platform-admin/showcase/media`
- `GET /api/v1/platform-admin/showcase/media/{media_id}/content`
- `POST /api/v1/platform-admin/showcase/items`
- `PUT /api/v1/platform-admin/showcase/items/{item_id}`
- `POST /api/v1/platform-admin/showcase/items/{item_id}/retire`
- `PUT /api/v1/platform-admin/showcase/order`
- `POST /api/v1/platform-admin/showcase/publish`
- `POST /api/v1/platform-admin/showcase/releases/{release_id}/rollback`
- `POST /api/v1/platform-admin/showcase/unpublish`

管理接口不提供任意对象代理或任意 OBS URL 签名能力。
