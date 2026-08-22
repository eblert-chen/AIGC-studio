# 正式生产上线清单（2026-08-21）

> 用户已选定目标：**正式生产上线（对真实用户开放公网）**。
> 当前系统仍在腾讯云 `123.207.41.6` 上作为**封闭试用环境**运行（15 容器，公网 API 关闭）。
> 本清单分「老板待办（外部资源，必须由你购买/注册/授权）」与「我来做（技术部署，安全可验证）」两部分。

---

## ⚠️ 三条红线（任何人不得绕过）

1. **公网 API 在以下全部满足前必须保持关闭**：真实 OIDC 登录通过外部 canary、可信反向代理 + TLS 生效、cookie/CSRF/step-up 验收通过。
2. **绝不允许把 Mock / 503 / unavailable 称为"生产已就绪"**。真实渠道没接、真实账单没拿，系统就不能对外声称"能生成/能发布/成本已就绪"。
3. **凭据绝不出现在聊天/微信/QQ 里**。一律写入 `deploy/secrets/` 下的指定文件，告诉我"放好了"即可。

---

## 一、老板待办：7 样外部资源（傻瓜式）

每件都写了：去哪办 → 拿什么 → 放哪个文件（路径在 `deploy/secrets/` 下）。

### 1. 真实登录系统（OIDC IdP）
- **去哪**：腾讯云「EIAM / IDaaS」或 Auth0（auth0.com）。
- **怎么做**：注册 → 新建应用 → 类型选 **OIDC** → 回调地址先空着（我给你填 `https://你的域名/callback`）。
- **拿什么**：`Client ID`、`Client Secret`、`Issuer URL`（发现文档地址）。
- **放哪**：`deploy/secrets/oidc/client_id.txt`、`client_secret.txt`、`issuer.txt`

### 2. 已备案正式域名 + SSL 证书
- **去哪**：腾讯云 DNSPod 买域名；国内服务器**必须 ICP 备案**（未备案域名不能解析到国内 IP）。
- **SSL**：腾讯云 SSL 控制台申请**免费 DV 证书**，下载 **Nginx 格式**（含 `.crt` + `.key`）。
- **放哪**：`deploy/secrets/tls/fullchain.crt`、`privkey.key`

### 3. 可信反向代理（我配，你只需给上面 2 的域名+证书）
- 不用 ngrok 当生产入口。我写 nginx/Caddy 配置 + 自动续期。
- **你不用做额外事**，等 2 备好后我直接接。

### 4. 真实 AI 视频厂商密钥（决定"能不能真生成视频"）
- **去哪（任选其一）**：可灵 AI 开放平台（klingai.com）、阿里云百炼（万相）、火山方舟（ark.cnvolcengine.com）。
- **怎么做**：注册 → 实名 → 开通视频生成 → 拿到 **API Key** + 确认有**配额/余量**。
- **拿什么**：`API Key`（可能还有 `Access Key/Secret`）。
- **放哪**：`deploy/secrets/providers/kling.key`（或 alibaba_wan / volcengine_ark）

### 5. 厂商真实账单 / 成本证据
- **去哪**：上面 4 所选的厂商控制台，开通**按量付费**并拿到账单/配额查看方式。
- **为什么必须**：没真实成本证据，系统不能声明"成本已就绪"（production_data_ready 必须为 false）。
- **放哪**：`deploy/secrets/providers/<厂商>_billing.txt`（写明账单访问方式，不含密码）

### 6. 告警接收地址（故障通知）
- **去哪**：企业微信群 → 添加「群机器人」拿 Webhook；或钉钉群同理。
- **拿什么**：Webhook URL（HTTPS）。
- **放哪**：`deploy/secrets/alerting/webhook.txt`

### 7. 抖音 / TikTok 开放平台应用（做自动发布用）
- **去哪**：抖音开放平台（open.douyin.com）/ TikTok Developers。
- **怎么做**：创建应用 → 拿到 `Client Key`、`Client Secret`。
- **注意**：自动发布在应用闭环 + 真实 adapter 上线前**保持关闭**，不会假发。
- **放哪**：`deploy/secrets/publishing/douyin.key`、`tiktok.key`

---

## 二、我来做（技术部署，安全可验证，部分可现在并行推进）

| 阶段 | 内容 | 是否等你的凭据 |
|------|------|----------------|
| **P0 冻结验证** | 锁定候选版本、跑 Platform/Go/Node/合同测试、生成 release manifest | 否 |
| **P1 迁移预演** | 隔离库演练 DB `0031→0039` 升级 + 回退（当前未做，必须先完成） | 否 |
| **前端 P1 修复** | 移动端 4 个 P1（成员表/历史标题/双重滚动/988px 断点） | 否 |
| **P2 Platform 升级** | 新 release 不覆盖旧 current；内部 health 检查；公网仍关 | 否（但上线需 1/2） |
| **P3 Relay 切换** | new-api Relay staging canary → 真实渠道验收 → 唯一活动数据面 | 等 4/5 |
| **P4 正式开放** | 真实 OIDC 登录验收、反向代理+TLS、公网只开 Web+网关、关开发 header auth、白名单灰度 | 等 1/2/6（+3/4/7 视范围） |

---

## 三、公网开放的硬前置（全部满足才解锁）

- [ ] 真实 OIDC 登录：登录/登出/轮换/吊销/Passkey/MFA step-up 验收通过
- [ ] 已备案域名 + TLS + 可信反向代理生效（nginx/Caddy）
- [ ] 真实生成渠道 staging canary 通过，route release proof 签名
- [ ] 真实成本证据就位（production_data_ready=true）
- [ ] 告警 Webhook 接通，故障能通知到人
- [ ] 关闭开发 header auth，确认 fail-closed
- [ ] 白名单朋友灰度观察真实数据/成本/告警正常

---

## 四、灰度计划

1. 先开放少量**白名单朋友**（你指定），观察真实数据/成本/告警。
2. 稳定 3–7 天后，再逐步放开流量。
3. 任一 P0 异常 → 回滚到 `releases/20260815-internal-pilot-2` 软链 + 恢复 `pre-p2-app-*` 备份。

---

## 当前阻断总结

**BLOCKED on 外部资源**：第 1/2/4/5/6/7 项未就位前，P4 不能执行。
**技术侧可立即推进**：P0 冻结验证、P1 迁移预演、前端 P1 修复——这些不依赖你的凭据，我现在就能开工。
