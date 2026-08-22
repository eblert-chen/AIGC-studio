# AI 视频平台部署接管手册

> 交接基准时间：2026-08-21 12:31（Asia/Shanghai）
> 仓库：`C:\AI-agent-project-s2\AI-video`
> 当前分支 / 基线提交：`main` / `709e9b4`
> 目标服务器：腾讯云轻量应用服务器 `123.207.41.6`，SSH 用户 `ubuntu`

## 0. 接管结论

当前状态是 **受密码保护的公网前端试用环境 + 私网后端试运行环境**，不是正式生产上线。

- 公网 ngrok 前端可访问，并且启用了 HTTP Basic Auth。
- 公网 nginx 明确拒绝 `/api/`，浏览器不能访问开发鉴权的后端，这是正确的安全边界。
- Platform、PostgreSQL、Redis 正在运行；Platform 健康检查通过。
- 服务器仍运行 2026-08-15 的旧 Python Relay；其 readiness 返回 `503`，容器为 `unhealthy`。
- 本地仓库已经包含大量未提交的新 API Relay、生产认证、数据库权限、前端和迁移改动，尚未整体部署到服务器。
- 公网前端使用 2026-08-18 的 production-strict 构建，不再展示模拟经营数据；没有真实登录时必须阻断真实管理数据。

接管者的第一目标不是立即重启，而是先把“本地候选版本、服务器旧版本、数据库迁移和回滚路径”对齐，再进行可回退发布。

## 1. 必读顺序

1. 完整阅读仓库根目录 `AGENTS.md`，它是产品架构、安全边界和上线判定的最高优先级说明。
2. 阅读本文件，了解真实部署现状。
3. 阅读 `docs/deployment-runbook.md`，了解目标生产拓扑和配置约束。
4. 阅读 `docs/production-go-live-beginner-checklist.md`，了解外部资源验收项。
5. 阅读 `docs/new-api-production-deployment.md` 与 `docs/relay-new-api-migration.md`，了解 new-api-first 切换和回滚合同。
6. 阅读 `deploy/ngrok-pilot/README.md`，确认 ngrok 只是封闭试用入口，不是正式生产 ingress。

不要把聊天中的旧结论当成当前事实；以代码、服务器只读检查和带时间戳的验收证据为准。

## 2. 不可违反的安全边界

- 不得打印、复制、提交或发送任何 AK/SK、JWT、数据库密码、ngrok token、Basic Auth 密码、API Key 或私钥内容。
- `deploy/secrets/` 已被 Git 忽略。只引用秘密文件路径，不读取内容到终端输出或聊天。
- 不得把 `DEVELOPMENT_HEADER_AUTH_ENABLED=true` 的 Platform API 暴露到公网。
- 不得把数据库、Redis、Relay、原生 new-api 管理台或 OBS 永久凭据暴露到公网。
- 不得把 Mock、演示数据、无账单证据的渠道成本或未经 canary 的 route 描述成生产数据。
- 不得把 `production_ready`、数据就绪或迁移完成状态手工改成真来绕过门禁。
- 不得在未备份数据库、未验证迁移、未准备回滚命令时执行破坏性升级。
- 不得清理本地工作树；大量未提交改动属于当前项目成果。禁止 `git reset --hard`、覆盖式 checkout 或重建干净目录后冒充当前候选版本。
- 所有文件修改使用小范围补丁；先确认其他改动归属，避免覆盖并行工作。

## 3. 本地秘密与连接入口（仅路径，不含秘密）

| 用途 | 本地路径 | 规则 |
| --- | --- | --- |
| 腾讯云 SSH 私钥 | `deploy/secrets/ssh/ai-video-deploy` | 只能用于 SSH/SCP，不显示文件内容 |
| 华为 OBS 运行凭据 | `deploy/secrets/huawei-obs.runtime.env` | 已忽略；不得 `Get-Content` 或上传到 Git |
| ngrok / pilot 凭据 | `deploy/secrets/ngrok.runtime.env` | 已忽略；不得在命令行参数和日志中展开 |

安全 SSH 示例：

```powershell
$DeployKey = 'C:\AI-agent-project-s2\AI-video\deploy\secrets\ssh\ai-video-deploy'
ssh -o BatchMode=yes -o ConnectTimeout=8 -i $DeployKey ubuntu@123.207.41.6 'hostname'
```
## 4. 服务器当前拓扑与路径

```text
ngrok HTTPS
  -> HTTP Basic Auth
  -> 127.0.0.1:8080
  -> ai-video-ngrok-pilot-web（静态前端）
  -X-> /api/（刻意返回 404/拒绝，不向公网代理）

127.0.0.1:8180 -> ai-video-api-gateway-1
127.0.0.1:8200 -> ai-video-platform-api-1
127.0.0.1:8100 -> ai-video-relay-api-1（旧 Python Relay，目前 readiness 503）
内部 Docker 网络 -> PostgreSQL / Redis / workers
```

服务器路径：

| 路径 | 含义 |
| --- | --- |
| `/opt/ai-video/current` | 当前后端 release 软链接 |
| `/opt/ai-video/releases/20260815-internal-pilot-2` | 当前实际后端 release |
| `/opt/ai-video/shared/secrets` | 服务器运行秘密；不要输出或下载到聊天 |
| `/opt/ai-video/shared/backups` | 备份目录 |
| `/opt/ai-video/shared/ngrok-pilot` | ngrok 前端、Compose 和配置目录 |
| `/opt/ai-video/shared/ngrok-pilot/client` | 当前前端 release 软链接 |
| `/opt/ai-video/shared/ngrok-pilot/client-build-20260818-production-no-mock` | 当前前端实际构建 |

后端 Compose 当前从以下文件启动：

```text
/opt/ai-video/current/docker-compose.yml
/opt/ai-video/current/deploy/compose.internal-pilot.yml
```

ngrok 前端 Compose：

```text
/opt/ai-video/shared/ngrok-pilot/docker-compose.yml
```

## 5. 2026-08-21 实测运行状态

| 组件 | 状态 | 证据口径 |
| --- | --- | --- |
| Platform API | healthy | `http://127.0.0.1:8200/health` 返回 customer-platform ok |
| PostgreSQL | healthy | Docker health |
| Redis | healthy | Docker health |
| API Gateway | running | 仅绑定 `127.0.0.1:8180` |
| 旧 Python Relay API | unhealthy | readiness 持续 HTTP 503；不是进程退出 |
| Relay workers | running | 仍是旧 Python Relay worker 集合 |
| ngrok pilot web | healthy | `127.0.0.1:8080` |
| ngrok systemd | active | agent 正在建立外连隧道 |
| 公网入口 | HTTP 401（未带 Basic Auth） | 表明访问保护仍启用 |
| 根磁盘 | 40 GB，总使用约 8.2 GB | 约 30 GB 可用 |
| 内存 | 1.9 GiB + 1.9 GiB swap | 空闲内存较少，发布时避免并行重编译多个大镜像 |

临时公网试用地址：

```text
https://unblanketed-lucio-satisfiedly.ngrok-free.dev
```

该地址可能随 ngrok 配置或套餐变化，使用前必须重新验证。Basic Auth 凭据只在秘密文件/服务器配置中取得，禁止写入本文件。

## 6. 当前代码与服务器的版本差异

本地 `main@709e9b4` 工作树包含大量未提交改动，包括但不限于：

- new-api-first Relay 切换、受保护运行秘密、数据库 principal/权限、下载边缘和迁移门禁；
- Platform OIDC/BFF 会话、认证生命周期、数据库迁移 `0037` 至 `0039`；
- Platform/Relay 的真实遥测、成本、告警、OBS 与数据就绪门禁；
- Studio、Company、Operations 前端重构和生产严格模式；
- 新增/修改的大量合同与生产部署测试。

服务器后端仍是 `20260815-internal-pilot-2`。因此不能假设“本地测试通过 = 服务器可直接原地升级”。接管者必须先生成差异清单并确认：

1. 当前数据库 Alembic revision 和目标 head；
2. 每个新迁移的升级/降级与 PostgreSQL 实测；
3. 旧 Python Relay 未完成任务、outbox、callback 和预留余额是否已排空；
4. new-api Relay 的数据库、Redis、服务 principal、OBS、route release proof 是否齐备；
5. 当前前端构建是否与本地最新代码一致；
6. 回滚到当前 release 时，新迁移数据是否仍兼容。

## 7. 接管后的前 30 分钟

只做只读检查，不修改服务器：

```powershell
git status --short
git branch --show-current
git rev-parse --short HEAD
npm run build
npm run test:sites
```

然后检查服务器：

```powershell
$DeployKey = 'C:\AI-agent-project-s2\AI-video\deploy\secrets\ssh\ai-video-deploy'

ssh -o BatchMode=yes -i $DeployKey ubuntu@123.207.41.6 `
  'readlink -f /opt/ai-video/current; readlink -f /opt/ai-video/shared/ngrok-pilot/client; sudo docker ps --format "{{.Names}}|{{.Status}}|{{.Ports}}"'

ssh -o BatchMode=yes -i $DeployKey ubuntu@123.207.41.6 `
  'curl -fsS http://127.0.0.1:8200/health; echo; sudo docker inspect --format "{{json .State.Health}}" ai-video-relay-api-1'
```

首次汇报必须明确给出：

- 当前是否仍与本文件一致；
- 哪些检查 PASS、FAIL 或 BLOCKED；
- 本轮打算发布哪个最小切片；
- 发布前备份点和回滚目标；
- 是否需要用户提供新的外部资源或授权。

## 8. 推荐的最小接管任务顺序

### P0：冻结并验证候选版本

1. 记录完整 `git status`，不要清理工作树。
2. 运行与改动范围匹配的 Platform、new-api Go、Node 和部署合同测试。
3. 修复真实失败；不得用跳过测试或放宽生产门禁换绿。
4. 生成可追溯 release manifest：源码提交、工作树补丁摘要、前端产物 hash、镜像 digest、迁移 head。

### P1：备份与迁移预演

1. 对服务器 PostgreSQL 做带时间戳的逻辑备份，写入 `/opt/ai-video/shared/backups`。
2. 在隔离数据库恢复备份并执行 base/current -> head -> downgrade boundary -> head。
3. 验证钱包、预留、账本、任务、产物、身份、成本和遥测记录数量与约束。
4. 输出不含秘密的迁移报告。

### P2：部署 Platform，但保持公网 API 关闭

1. 使用新 release 目录，不覆盖 `/opt/ai-video/current` 指向的旧目录。
2. 先构建镜像和执行迁移，再切换软链接并重建 Platform 相关容器。
3. 只从服务器 loopback/内部网络做健康和数据库检查。
4. 正式 OIDC、可信代理、cookie/CSRF、step-up 未完成外部 canary 前，继续禁止公网 API。

### P3：部署 new-api Relay 候选并完成切换验收

1. 按 `docs/relay-new-api-migration.md` 完成旧 Relay 排空和切换前快照。
2. 部署 new-api API、Download Edge、durable workers 和独立数据库 principal。
3. 先在 staging route 做真实供应商 canary，不得直接启用 production route。
4. 验证未知提交不跨渠道重试、OBS 转存、回调、成本、遥测和告警闭环。
5. 只有签名 release proof 与完整验收均通过，才切换唯一活动数据面。

### P4：正式域名与真实用户开放

1. 使用已备案正式域名、TLS 和可信反向代理，不把 ngrok 当生产 ingress。
2. 完成目标 OIDC IdP 的真实登录、退出、轮换、吊销、Passkey/MFA step-up 和停用联动验收。
3. 公网只开放 Web 与 Platform 网关；Relay、数据库、Redis、管理台继续私有。
4. 关闭开发 header auth，确认生产配置 fail closed。
5. 先开放少量白名单朋友，按真实数据、成本和告警观察，再扩大流量。

## 9. 当前真实上线阻塞

以下任一项未闭环，都不能称为正式生产上线：

1. **认证外部验收**：代码已新增 OIDC/BFF 能力，但目标 IdP、正式域名、可信代理、step-up 和吊销 canary 尚未归档。
2. **new-api Relay 切换**：服务器仍是旧 Python Relay，且 readiness 为 503；new-api 候选未在该服务器完成切换。
3. **真实生成渠道**：至少一个官方渠道的真实凭据、模型权限、配额、staging canary、route release proof 未全部通过。
4. **真实渠道成本**：无可靠供应商成本证据时 `production_data_ready` 必须保持 false；不得从客户售价推断成本。
5. **完整 OBS 证据**：已有私有桶和最小权限凭据，但仍需针对最终服务器 release 重新执行实桶 PUT/HEAD/GET/完整性/匿名拒绝 gate。
6. **正式告警**：必须有独立 HTTPS 值班接收端并完成签名、重试、幂等和死信演练。
7. **自动发布**：正式抖音/TikTok OAuth adapter、媒体传输和审核未闭环时保持关闭，不阻塞“生成功能”小范围试用，但不能宣传已支持生产自动发布。

## 10. 发布与回滚原则

### 发布

- 每次发布创建不可变目录 `/opt/ai-video/releases/<timestamp>-<purpose>`。
- 上传前在本地完成测试和构建；服务器只做部署必要操作，避免在 2 GB 内存机器上并行重编译。
- 校验上传包/hash，确认 secret 文件未进入 release。
- 备份数据库、记录当前软链接和镜像 digest。
- 执行迁移和内部健康检查，通过后原子切换 `/opt/ai-video/current`。
- 逐组重建服务，先依赖、再 API、再 worker、最后网关；每一步失败即停止。

### 回滚

- 应用回滚目标默认是 `/opt/ai-video/releases/20260815-internal-pilot-2`，但只有迁移兼容性验证通过后才可直接切回。
- 前端回滚目标是 `/opt/ai-video/shared/ngrok-pilot/client-build-20260818-production-no-mock`。
- 数据库回滚优先使用已验证 downgrade；若不可逆，使用发布前备份恢复到隔离库验证后再决策。
- 不得只回滚容器而留下不兼容数据库 schema。
- 回滚后重跑 Platform health、Relay readiness、账本/预留检查和公网 401/API 阻断检查。

## 11. 交付完成定义

接管任务只有满足以下条件才算完成：

- 新 Agent 能复述当前系统是“封闭试用”而非正式生产；
- 已生成带时间戳、无秘密的现状报告；
- 本地候选版本全量或风险匹配测试有可审计结果；
- 数据库备份与恢复演练通过；
- 新 release 可发布、可回滚，旧 release 未被覆盖；
- Platform、new-api Relay、PostgreSQL、Redis、OBS、回调、成本、遥测、告警的真实链路逐项有 PASS/FAIL/BLOCKED；
- 无开发鉴权、Mock 数据、未验证成本或管理入口泄露到公网；
- 用户知道当前公开地址、适用范围、剩余阻塞和下一步。

## 12. 给接管 Agent 的首条指令

```text
你接管 C:\AI-agent-project-s2\AI-video 的部署上线任务。先完整阅读 AGENTS.md 和
docs/deployment-handoff-2026-08-21.md，再阅读其中列出的生产部署文档。

第一轮只做只读审计：核对本地 dirty worktree、当前测试状态、腾讯云 123.207.41.6
上的 release 软链接、容器、Platform health、旧 Relay readiness、ngrok 前端和公网 API
阻断。不要打印任何秘密，不要清理工作树，不要重启服务，不要暴露开发 header auth，
不要把 Mock 或 unavailable 数据标成生产。

审计后向用户输出：真实现状、PASS/FAIL/BLOCKED、最小发布切片、备份与回滚计划。
随后继续完成“候选版本冻结 -> 数据库备份/迁移预演 -> Platform 内部升级 -> new-api
Relay staging/cutover -> 正式认证与域名”的安全部署。遇到需要用户购买、注册、授权或
填入外部密钥的步骤，明确暂停并给零基础操作指引；其余安全的仓库与服务器工作自行完成。
```
