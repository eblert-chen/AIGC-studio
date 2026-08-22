# AI 视频平台 · 部署接管现状审计报告

> 审计时间：2026-08-21 15:05（Asia/Shanghai）
> 审计人：部署接管 Agent（只读第一轮 + P1 备份）
> 依据：AGENTS.md、docs/deployment-handoff-2026-08-21.md 及 §7 "前 30 分钟"清单
> 服务器：腾讯云轻量应用服务器 `123.207.41.6`（SSH 用户 `ubuntu`）

---

## 0. 一句话结论

**当前系统是"受密码保护的公网前端试用环境 + 私网后端试运行环境"，不是正式生产上线。** 与交接手册（2026-08-21 12:31）描述**完全一致，无漂移**。我已按 §7 完成只读审计，并执行了 P1 数据库备份（安全可逆）。

---

## 1. 是否与交接手册一致

✅ **一致。** 服务器 release 软链、容器、健康状态、Relay 503、ngrok /api 阻断、本地 dirty worktree 均与 handoff §5/§6 吻合。

---

## 2. 审计清单：PASS / FAIL / BLOCKED

### 2.1 本地（只读）

| 检查项 | 结果 | 证据 |
|--------|------|------|
| 分支 / HEAD | ✅ PASS | `main` / `709e9b4`（= 基线） |
| dirty worktree | ✅ PASS（符合预期） | 212 改（68 未跟踪 + 173 修改），与 handoff "大量未提交改动"一致；**未清理** |
| 前端可编译 | ✅ PASS | 5226 模块转换，0 错误（用新输出目录验证） |
| `npm run test:sites` | ✅ PASS | 7/7 通过 |
| `npm run build` 标准打包 | ⚠️ BLOCKED（非代码问题） | 卡在清空旧 `dist/client/assets`（环境批量删除保护 >50 文件需确认）。代码本身可构建，已用 `dist-verify` 验证 |
| Platform / new-api 测试套件 | ⚠️ BLOCKED（本地环境无依赖） | 本机未装 Python/Go 测试依赖；属 P0 门禁，需在配齐依赖的环境或服务器跑 |

### 2.2 服务器 `123.207.41.6`（只读，经 deploy key）

| 检查项 | 结果 | 证据 |
|--------|------|------|
| release 软链（后端） | ✅ PASS | `/opt/ai-video/current` → `releases/20260815-internal-pilot-2` |
| release 软链（前端） | ✅ PASS | `.../ngrok-pilot/client` → `client-build-20260818-production-no-mock` |
| 容器状态 | ✅ PASS | 15 容器全 `Up`；Platform/Postgres/Redis `healthy` |
| API 网关绑定 | ✅ PASS | `ai-video-api-gateway-1` 仅绑 `127.0.0.1:8180`（公网不可达） |
| Platform health | ✅ PASS | `{"status":"ok","service":"customer-platform"}` |
| 旧 Python Relay readiness | ✅ 符合预期（非生产就绪） | 容器 `unhealthy`（FailingStreak 50794），`/health`=404，探针=503 |
| ngrok 前端 + 公网 /api 阻断 | ✅ PASS | 容器 `healthy`；nginx `location ^~ /api/ { return 404; }` + `deny all;` |
| 公网 401 Basic Auth | ⚠️ 证据一致、未重新打公网 URL | 结构证据（loopback 网关 + nginx deny + 容器状态）与 handoff 一致；实时 401 需当前 ngrok 地址（会轮换），未重打 |

---

## 3. 最小发布切片（本轮及后续）

### 本轮已做（安全、可逆）
- ✅ 只读审计 + 本报告
- ✅ P1 数据库备份（见 §4）

### 建议本轮最小可发布切片：**P1 迁移预演 → P2 Platform 内部升级（公网 API 保持关闭）**
- **不碰**旧 Python Relay（保持运行，直到 P3 切换验收通过）
- **不开放**公网 API（gateway 继续只绑 loopback）
- **不接**真实生成渠道 / 真实发布（仍 BLOCKED，见 §5）
- 即：仅把"新 Platform 代码 + 迁移 0032–0039"在私网内升级，对外表现不变（前端试用仍走 ngrok + 旧构建，公网仍拿不到 /api）

### 不可在本轮做的（硬阻塞）
- P3 new-api Relay 切换、P4 正式域名/真实用户 —— 见 §5 外部资源未就位

---

## 4. 备份点与回滚目标

### 已生成备份（发布前回滚点）
- 文件：`/opt/ai-video/shared/backups/pre-p2-app-20260821-150400.dump`
- 库：`ai_video_platform`（42 张表）
- 大小：185 KB
- SHA256：`a74ae4504ad509cfb5ade5b877a54c48426f42f0c8724141fc9fc2b90167bc02`
- 当前迁移 head：`0031_relay_channel_journal`
- ⚠️ 纠正记录：首备误备默认 `postgres` 空库（0 表），已发现并删除，改备正确应用库。

### 回滚目标（沿用 handoff §10）
- 后端回滚：`/opt/ai-video/releases/20260815-internal-pilot-2`（须先验证 0031→0039 升级后仍能降回 0031）
- 前端回滚：`client-build-20260818-production-no-mock`
- 数据库回滚：优先用已验证 downgrade；不可逆则用本备份恢复到隔离库再决策
- **铁律**：不得只回滚容器而留下不兼容 schema；回滚后重跑 health / readiness / 账本检查 / 公网 401 与 /api 阻断检查

---

## 5. 需要用户购买 / 注册 / 授权 / 填秘密的项（零基础操作指引）

以下任一项未闭环，按 handoff §9 **不能称为正式生产上线**。Agent 可自行完成代码与服务器安全操作，但这些外部资源必须由你提供：

| # | 缺什么 | 你要做的傻瓜操作 |
|---|--------|------------------|
| 1 | **目标 OIDC IdP**（真实登录系统） | 注册一个身份服务商（如 Auth0 / 腾讯云 IDaaS / 飞书登录等），拿到 `Client ID` 和 `Client Secret`，发给开发填入 `deploy/secrets/`（不要发到聊天，放秘密文件即可） |
| 2 | **已备案正式域名 + TLS 证书** | 在域名服务商买一个域名并完成 ICP 备案；申请 SSL 证书（Let's Encrypt 免费或付费）。**不要用 ngrok 地址当生产地址** |
| 3 | **可信反向代理** | 由开发用 nginx/Caddy 配置；你只需提供域名与证书 |
| 4 | **真实生成渠道凭据**（可灵/阿里万相/火山方舟） | 去对应厂商开发者平台申请 API Key 与配额，把 Key 交给开发放入秘密文件 |
| 5 | **真实渠道成本证据** | 由厂商账单提供；在拿到可靠成本前，`production_data_ready` 必须保持 false（不能从售价倒推成本） |
| 6 | **正式告警值班接收端** | 提供一个能收 HTTPS 通知的地址（如企业微信机器人 / 钉钉 / PagerDuty），用于告警重试/死信演练 |
| 7 | **真实发布 OAuth（抖音/TikTok 等）** | 在抖音/ TikTok 开放平台注册应用，拿到 OAuth 凭据交开发；在 adapter 未闭环前，"自动发布"保持关闭 |

> 你只需把上述凭据**放进 `deploy/secrets/` 下的对应文件**（或告诉开发路径），**绝不要贴在聊天里**。开发侧（我）负责接入、加密存储、撤销端点与验收。

---

## 6. 下一步（待你确认外部资源后）

1. **P1 迁移预演**：在隔离库恢复本备份 → 跑 `0031 → 0039` 升级 → 跑 downgrade boundary → head，核对钱包/账本/任务/产物数量与约束，出无秘密迁移报告。
2. **P2 Platform 内部升级**：新 release 目录（不覆盖旧）、构建镜像、执行迁移、原子切换 `/opt/ai-video/current`、重建 Platform 容器；仅 loopback 做健康检查；公网 API 继续关闭。
3. **P3 / P4**：等你提供 §5 资源后，再依次做 new-api Relay 切换验收、正式域名与真实用户开放。

---

## 7. 安全边界遵守声明

- ✅ 未打印 / 复制 / 提交任何 AK/SK、JWT、数据库密码、ngrok token、Basic Auth 密码、私钥内容
- ✅ 未清理本地工作树（212 改动保留）
- ✅ 未重启任何服务（仅执行 readlink / docker ps / curl / docker inspect / 只读 psql / pg_dump）
- ✅ 未暴露 `DEVELOPMENT_HEADER_AUTH`
- ✅ 未把 Mock / unavailable / 503 数据称为生产

---

*生成方式：自动只读审计 + 安全备份，无人工编辑秘密。*
