# 生产接入清单（零基础版）

更新日期：2026-08-20

这份清单聚焦仍需外部环境验收的生产项：目标 OIDC IdP、华为 OBS、真实生成渠道、真实成本、告警和第二渠道故障切换。仓库已经实现 OIDC Authorization Code + PKCE、RS256/JWKS、服务端可撤销 BFF 会话、邀请激活和全局账号生命周期；密码、找回、MFA 与 passkey 由目标 IdP 承担。代码完成不等于外部验收完成：目标 IdP、真实域名/TLS、step-up 策略和停用联动的当前候选 canary 归档前，仍不得向公网真实客户开放。

## 当前结论

代码已经具备严格门禁，但本机没有任何真实云凭据，所以现在仍是 **NO-GO**。new-api Relay 不再接受人工填写的 `staging_ready` 或 `production_ready` 作为放行依据；每条路由都必须由 Relay 运行环境之外的验收机构签发 Ed25519 证明，并精确绑定环境、路由、能力、适配器、源码快照、镜像摘要和有效期。

| 顺序 | 项目 | 代码状态 | 还缺什么 |
| --- | --- | --- | --- |
| 1 | 目标 OIDC IdP | 原生 Code+PKCE、JWKS、BFF session、邀请和账号状态机已完成 | 注册 public client；真实登录/轮换/step-up/停用/吊销 canary 与审计证据 |
| 2 | 华为 OBS | 上传、HEAD 校验、私有下载和完整性门禁已完成 | 真实私有桶、最小权限 IAM 身份、实桶 PASS 证据 |
| 3 | 第一生成渠道：阿里百炼 Wan2.7 | 当前协议与 staging 门禁已完成 | 百炼账号、同一区域 workspace/API Key、小额真实任务 |
| 4 | 渠道成本 | 成功任务自动生成不可变成本事件 | 正式合同/账单、费率、生效时间、原文件 SHA-256 |
| 5 | 渠道告警 | Relay 有持久重试；Platform 有签名接收与转发桥 | 一个正式 HTTPS 下游告警接收地址和值班人 |
| 6 | 第二生成渠道：火山方舟 Ark | 当前协议、双路能力交集和安全故障切换门禁已完成 | 方舟账号、Endpoint/API Key、同能力真实模型与小额验收 |

## 第零步：接入目标 OIDC IdP

1. 在目标 IdP 注册一个 Authorization Code + PKCE S256 的 public client；不要配置或下发浏览器 client secret。
2. 精确填写 issuer、authorization/token/JWKS 端点、client ID、Platform callback、Frontend Origin 和账号管理 HTTPS 地址，禁止通配 redirect URI。
3. 让 IdP 固定签发 RS256 ID token、稳定 `sub`、精确 audience/authorized party，并为最高权限账号提供抗钓鱼 `amr` 与可用于 recent-auth 的 `auth_time`。
4. 用真实域名执行并归档：正常登录；错误/重放 state；JWKS `kid` 轮换；邀请接受/过期/撤销；单设备和全设备吊销；全局 suspend/reactivate；owner transfer step-up；IdP 停用到 Platform 全局停用的运维联动。
5. 浏览器只能获得 Secure/HttpOnly BFF session；确认没有长期 Bearer、refresh token、邀请 token 或 OIDC code 留在 URL、Storage、访问日志和错误页。

## 第一步：创建华为 OBS

你只需要在华为云控制台操作，不需要写代码。

1. 打开“对象存储服务 OBS”，新建桶。
2. 桶区域选择将来生产服务器所在区域；桶创建后区域不能修改。
3. 桶设为私有，开启阻止公共访问，不开启静态网站。
4. 存储类型选“标准”；区域支持且预算允许时选多 AZ。
5. 默认加密先选 SSE-OBS。
6. 记录非秘密信息：区域 ID、区域 Endpoint、桶名、桶访问主机名。
7. 创建只允许编程访问的 IAM 运行账号，不给控制台密码，不给管理员权限。

运行账号最小权限：

- Platform：`inputs/*` 与 `showcase/media/*` 的 `PutObject`、`GetObject`。
- Relay：`outputs/*` 的 `PutObject`、`GetObject`，以及桶级 `HeadBucket`。
- Download Edge：不能持有 OBS AK/SK。
- 实桶验收另建临时身份，只额外给予 `outputs/*` 的 `DeleteObject`；验收结束后停用该 AK。

需要填入服务器密钥管理器的变量：

```text
HUAWEI_OBS_ENDPOINT
HUAWEI_OBS_BUCKET
HUAWEI_OBS_ACCESS_KEY_ID
HUAWEI_OBS_SECRET_ACCESS_KEY
```

使用临时凭据时再填 `HUAWEI_OBS_SECURITY_TOKEN`。AK、SK、SecurityToken、带签名下载 URL 都不要发到聊天、微信、截图、Git 或任何 `VITE_*` 变量。

实桶验收必须同时证明：匿名读取被拒绝；PUT 成功；HEAD 的大小、类型、SHA-256 元数据一致；签名 GET 的完整字节和 SHA-256 一致；只清理本次测试对象；证据文件不含秘密。

## 第二步：开通阿里百炼 Wan2.7

1. 注册并完成阿里云实名认证。
2. 开通百炼，创建生产 workspace；建议先固定中国北京区域。
3. 在同一 workspace 和区域创建 API Key。
4. 确认账号已经获得 Wan2.7 文生视频和图生视频模型权限。
5. 充值一笔只够 staging 验收的小额余额。
6. 从控制台记录非秘密信息：区域、workspace ID、已授权的精确模型 ID、官方并发/RPM 限额。
7. API Key 只在 new-api 的服务端渠道凭据或云密钥管理器中填写，不发到聊天，也不写进路由 JSON。

第一条路由只允许：

- 一个真实账号；
- 一个精确版本模型；
- 并发先设为 1；
- 先完成 staging canary，再用仓库的离线 signer 生成只绑定 `staging` 的短期验收证明；
- 只放行隔离测试公司。

真实 canary 必须核对供应商任务 ID、终态、OBS 对象、Platform 回调、余额预占/结算、渠道成本和审计记录。全部一致后才能针对最终生产镜像重新签发只绑定 `production` 的验收证明；staging 签名不能在生产重放，验收私钥也绝不能进入 Relay 镜像、环境变量或服务器运行时密钥空间。

## 第三步：提供真实成本证据

系统不会从客户售价、积分或供应商赠送额度反推渠道成本。

你需要向财务或供应商取得：

- 合同、正式价格表、发票或可导出的账单原文件；
- 精确的供应商、渠道、上游模型、生成模式、分辨率；
- 计费单位：按输出条数或按输出秒数；
- 人民币分单位的正整数价格；
- 生效时间和合同/账单引用编号；
- 原文件的小写 SHA-256。

每次调价都新增一个版本，不能修改旧版本。缺少匹配费率时，任务可以保留真实终态，但成本对账会继续显示不完整，`production_data_ready` 必须保持 `false`。

## 第四步：接入正式告警

需要准备一个只接收告警的规范化 HTTPS 地址，例如公司自建告警接收器。它必须支持幂等键并验证 Platform 转发的 HMAC；不要把 Sentry 的“向外发送 Service Hook”误当成接收地址。

告警链为：

```text
new-api Relay（持久重试/死信）
  -> Platform /internal/relay/provider-alerts（独立入站 HMAC）
  -> 值班告警接收器（独立出站 HMAC）
```

入站、出站、成本和遥测必须使用四把不同的随机密钥。下游未配置、签名不正确、事件过期、下游 3xx/4xx/5xx 或网络失败时，Platform 不确认成功，Relay 会继续重试并最终进入死信。

上线前至少演练：成功率下降、批量账号失效、全路由失败、恢复通知、下游短时不可用、重复投递、同事件 ID 篡改请求体。

## 第五步：接入火山方舟作为备用渠道

阿里百炼单路完整通过后，再申请火山方舟账号、视频生成 Endpoint 和 API Key。第二路必须声明与第一路相同公开模型别名下的真实能力；系统只对外发布两路能力的安全交集。

故障切换验收必须证明：

- 上游明确证明“没有创建任务”时，才允许切备用渠道；
- 请求超时且无法确认是否创建时进入 `submission_unknown`；
- `submission_unknown` 固定原渠道和账号，绝不跨渠道重提；
- 原渠道任务已经创建时，后续轮询、产物转存和成本都绑定原路由；
- 备用渠道的成本证据、OBS 和回调链同样完整。

## 你现在只做这三件事

1. 创建华为 OBS 私有桶和最小权限 IAM 账号。
2. 开通阿里百炼生产 workspace、Wan2.7 权限和小额测试余额。
3. 向财务取得对应模型的合同/账单或正式价格文件。

可以告诉开发人员的非秘密信息只有：OBS 区域 ID、区域 Endpoint、桶名；百炼区域、workspace ID、精确模型 ID、官方限额；成本文件的业务含义和引用编号。

所有 AK/SK、API Key、Webhook 密钥、数据库密码都由你本人在服务器或云密钥管理器中填写。填好后只回复“已注入”，不要把值发出来；随后运行仓库现有的真实 OBS gate 和单路 canary，得到 PASS/NO-GO 证据。
