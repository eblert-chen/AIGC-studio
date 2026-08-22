# 项目遍历地图 — AI 视频生成双平台（2026-08-12）

> **归档快照（非发布真相）**：本文保留 2026-08-12 的静态遍历结果，文件数、认证状态和
> Relay 迁移判断已经过期。当前生产合同以 `architecture.md`、
> `relay-new-api-migration.md`、`new-api-production-deployment.md` 和
> `deployment-runbook.md` 为准：new-api 是唯一活动生产 Relay，Python 只保留为离线历史
> oracle，生产回滚仅指向上一版兼容的 new-api immutable image。

> 目的：一次性看清代码库骨架、各子系统职责与关键信号，便于后续定位与决策。
> 数据来自本次对 `src/`、`backend/`、`docs/`、`contracts/` 的静态遍历（排除 pytest / codex 临时目录与 node_modules）。

---

## 0. 一句话定位

一个**前端 + Platform + 唯一 new-api Relay 数据面**的 AI 视频生成 SaaS：`src/` 是
React 客户工作室，`backend/platform/` 是 FastAPI 客户管理控制面，
`backend/new-api-relay/` 是基于 QuantumNous/new-api 的活动 Go Relay；
`backend/relay/` 仅是离线历史行为 oracle。

**核心边界**：浏览器只调 Platform；Platform 与内部 TikTok 服务通过服务凭据调 Relay；Provider 适配器与 OBS 存储隔离在 Relay 层之后。

---

## 1. 顶层目录地图

| 目录 | 文件数 | 角色 |
|---|---|---|
| `src/` | 33 | React 19 + Vite 6 前端（纯状态导航，无路由库） |
| `backend/platform/` | （源码 100+ 文件 + 53 test 文件） | Python FastAPI 客户管理平台 |
| `backend/relay/` | 30+ py + 29 test | 离线历史 Python 合同 oracle |
| `backend/new-api-relay/` | 867 个 .go（非 vendor） | 唯一活动 new-api Go Relay（含上游生态与本产品扩展） |
| `docs/` | 22 | 架构 / 契约 / 迁移 / 验收 / 追溯 文档 |
| `contracts/` | 3 | 回调 schema、错误码、Relay 生成 OpenAPI |
| `infra/` | 3 | nginx 反代、SQL 初始化 |
| `logos/` | 5 | 旭天品牌 SVG 源文件 |
| `public/` | 10 | 项目自有素材图（社区/场景） |
| `artifacts/` | 56 | 设计稿 / 截图 / JSON 产物 |
| `dist/` | 25 | 前端构建产物 |

> 注：`backend/` 总文件 12.7 万，绝大多数为 new-api-relay 的上游 Go 生态（含 `.git`、`i18n`、`electron`、`docs` 等），非本项目自研。

---

## 2. 前端 `src/` 地图

**导航机制**：`studioPathForNav` + `view` 状态切换（`studio` / `company` / `platform` 三套工作面），无 react-router。依赖极简：React 19.2、Vite 6.4、recharts（图表）、@phosphor-icons/react、@fontsource-variable/manrope（字体）。

| 文件 | 行数 | 职责 |
|---|---|---|
| `App.jsx` | **4803** | 单一 monolith：三工作面路由、视图状态、认证、demo 切换、创作器 |
| `ManagementConsole.jsx` | **3338** | 公司管理工作面（成员/角色/公司/模型授权/充值/报表） |
| `admin/OperationsConsole.jsx` | 1916 | 平台运营控制台（模块导航 + 任务流 + 可靠性表） |
| `PublishingCenter.jsx` | 1207 | 发布工作面（账号卡 / 审批 / 排期） |
| `api/platformClient.js` | 1111 | Platform API 客户端封装 |
| `admin/AdminOperationsContainer.jsx` | 650 | 运营容器壳 |
| `admin/adminApiAdapter.js` | 642 | 运营 API 适配 |
| `modelCapabilities.js` | 587 | 模型能力交集 / 参数钳制语义（核心逻辑） |
| `CreationHub.jsx` | 497 | 创作工作台（媒体 tab / 状态筛选 / 任务网格） |
| `admin/relayUnknownOperations.js` | 425 | new-api 未知提交人工处置 |
| `admin/adminConsoleUtils.js` | 286 | 运营工具函数 |
| `admin/adminDemoData.js` | 284 | 运营演示数据 |
| `taskArtifacts.js` | 209 | 任务产物桥接 |
| `publishing.js` | 192 | 发布领域逻辑 |
| `CommunityHome.jsx` | 170 | 社区灵感流首页 |
| `communityFeed.js` | 120 | 社区 feed 静态数据 |
| `demoIdentitySurfaces.js` | 69 | demo 身份面 |
| `studioNavigation.js` | 65 | 工作室导航映射 |
| `SkinSwitcher.jsx` | 62 | 浅色皮肤切换（纯白/雾灰/暖米） |
| `runtimeMode.js` | 44 | demo / live 运行模式 |
| `identitySurfaces.js` | 38 | 身份面定义 |
| `permissionCatalog.js` | 27 | 权限目录（16 项） |
| `DemoAccountSwitcher.jsx` | 20 | demo 账号切换器 |
| `main.jsx` | 15 | 入口 |

**信号**：`App.jsx` 4803 行、`ManagementConsole.jsx` 3338 行、`OperationsConsole.jsx` 1916 行——三个超大组件，是典型的"先跑通再拆分"痕迹，后续重构风险集中在这里。

---

## 3. 后端 Platform（`backend/platform/`）地图

**技术栈**：FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL（演示 SQLite）+ Redis（下载网关）。

**路由组织（重要信号）**：`platform_api/routers/` 目录**仅 2 个文件**（`admin_operations.py`、`relay_telemetry.py`）。全部 ~95 个业务路由以 `@app.get/post/...` 装饰器**直接挂在 `main.py`**（从约 697 行到 1068+ 行密集定义），全仓 `APIRouter` 实例仅 3 个。这是巨型 `main.py` 反模式，路由与中间件/异常处理混在一起，可读性差、难单测。

**业务服务层**（`platform_api/services/`，33 个模块，覆盖完整）：

- 组织：`companies` `permissions` `permission_catalog` `admin` `admin_analytics` `admin_entitlements` `entitlement_policy` `resources`
- 资产/素材：`artifacts` `input_assets` `asset_storage` `artifact_*.py`（含产物提升/预览）
- 计费：`billing` `channel_costs` `channel_cost_events` `wallet`（账本）
- 任务：`tasks` `task_admission` `task_timeouts` `relay_*.py`（回调/状态/能力/Outbox/遥测/同步）
- 下载：`download_gateway` `download_gateway_registration_worker` `download_completion_events` `download_completion_trust`
- 发布：`publishing` `publishing_adapters` `publishing_worker`
- 平台管理：`platform_admin_access_*`（7 个文件，独立子域）
- 审计/监控：`audit` `provider_alerts` `relay_telemetry` `errors` `request_ids`

**数据层**：`models.py` + `database.py` + `dependencies.py` + `dispatcher.py`。

**迁移**：`migrations/versions/` 共 **38 个** Alembic 版本（`0001` → `0038_download_evidence_checks`），覆盖初始、Relay outbox、平台管理员、计费不变量、渠道成本、发布/OAuth、逆向账号池、Relay 渠道控制流水、任务级 Relay backend affinity、TOC 个人工作区、Operations 管理证据、逐进程数据库角色边界、生产 OIDC 身份与账号生命周期，以及下载证据约束收口。发布基线 = `0038`。

**测试**：`tests/` 下 **53 个** `test_*.py` 文件（集成 + 单元），含成本验收 server、生成能力契约、计费不变量、下载审计、平台管理员权限矩阵等。

**认证现状**：HS256 Bearer JWT 服务端校验已就位（需 KMS 注入 `JWT_SIGNING_SECRET/ISSUER/AUDIENCE`）；**真实 IdP 登录/换票/吊销/轮换未接**——仍是 demo token 边界。

---

## 4. 历史双 Relay 代码地图（生产双数据面已退役）

### 4.1 Python Relay（`backend/relay/`）— 离线历史行为 oracle

| 模块 | 职责 |
|---|---|
| `relay_service/providers/` | 适配器：`alibaba_wan` `kling` `volcengine_ark` `mock` + `base` `registry` `router` `pool` `verify` `http` |
| `relay_service/`（根） | `main` `service` `dispatcher` `worker` `queue` `repository` `sql_repository` `outbox` |
| 产物与安全 | `artifacts` `downloader` `transfer` `transfer_worker` `callback` `callback_worker` |
| 监控 | `provider_monitoring` `provider_monitor_worker` `provider_sync_worker` |
| 配置 | `config` `auth` `models` `errors` `request_ids` |

**关键信号**：4 个真实适配器**全部 `production_ready = False`**（`alibaba_wan.py:103`、`kling.py:129`、`volcengine_ark.py:114`）。`registry.py` 与 `verify.py` 会依据该标志拒绝生产注册/校验。即：当前无任何可生产化的供应商渠道。迁移基线版本 = `0012_generation_contract_v1`。

测试：`tests/` **29 个** py 文件。

### 4.2 new-api-relay（`backend/new-api-relay/`）— 唯一活动生产 Relay

- **规模**：867 个非 vendor `.go` 文件，是上游 QuantumNous/new-api 完整仓库（含 `i18n/`、`electron/`、`docs/`、`integration/`、`.github/` CI）。
- **扩展点（本项目新增）**：`relay/`（alpha_search / audio handler 等）、`relaykit/`（dto / reasonmap / relayconvert / types）、`integration/platformrelay/`、`integration/platformobs/`（华为 OBS 对接）、`cmd/relay-download-edge/`、`cmd/relay-real-channel-acceptance/`。
- **上游生态优势**：`relay/channel/` 下已有大量现成适配器（ali、aws、baidu、claude、cohere、coze、codex、baidu_v2、cloudflare 等），可直接复用为新渠道来源，无需从零自研。
- **验收边界**：软件数据面已经收口到 new-api；真实 Provider、生产 OBS、目标 IdP、支付、外部告警、备份恢复和容量仍需部署外证据，缺失时公网商用保持 NO-GO。

---

## 5. 文档与契约

**文档 22 篇**，主线：
- 架构基线 `architecture.md`、统一生成 API `generation-api-v1.md` + 冻结清单
- 历史 Python oracle：`provider-adapter-v1.md`、`provider-monitoring.md`；当前能力配置：`model-capability-v1.md`
- 平台管理员 `platform-admin-v1.md`、逆向账号池 `reverse-account-pool.md`、下载边缘 `relay-download-edge.md`
- 迁移与验收：`relay-new-api-migration.md`、`relay-real-channel-acceptance.md`、迁移验收配置/模板 JSON
- 交付状态：`first-milestone.md`、`release-readiness.md`、`requirements-traceability.md`、`project-completeness-2026-08-07.md`
- 运维：`deployment-runbook.md`、`production-go-live-beginner-checklist.md`、`source-control.md`

**契约 3 份**：`relay-generation-v1.openapi.yaml`（生成合同）、`callback-event-v1.schema.json`、`error-codes-v1.json`。

---

## 6. 关键信号与缺口（给后续决策用）

| 类别 | 观察 | 影响 |
|---|---|---|
| 🔴 外部依赖 | IdP / 真实 Provider / 生产 OBS / 可信支付 四类均未接入 | 公网商用 NO-GO |
| 🔴 渠道就绪 | Python 适配器状态只属于离线 oracle；真实 new-api Provider/OBS 回执尚未归档 | 公网商用仍 NO-GO，但不得回接 Python 数据面 |
| ⚠️ 代码组织 | Platform `main.py` 巨型路由文件（95 路由直挂）；前端 3 个 2000–4800 行 monolith | 重构/测试/并行开发风险 |
| ⚠️ 设计漂移 | AGENTS.md 视觉决策已从"深色 PixVerse"改为"浅色纯白"，但 `prototype-home-creation.html` 仍是旧深色稿 | 原型需按新决策重做，勿与现稿混淆 |
| ✅ 工程纪律 | 29 个 Alembic 迁移、53 + 29 测试、契约先行、不可变审计/成本事件 | 内部闭环质量高 |
| ✅ 架构边界 | 浏览器→Platform→Relay→Provider/OBS 分层清晰；金额整数最小单位；HMAC 回调签名 | 安全基线扎实 |

---

## 7. 快速入口（想改哪块看这里）

- 前端创作器逻辑 → `src/modelCapabilities.js` + `src/CreationHub.jsx`
- 前端三工作面壳 → `src/App.jsx`（关注 `studioPathForNav` 与 `view` 状态）
- Platform 业务路由 → `backend/platform/platform_api/main.py`（非 routers 目录）
- Platform 业务服务 → `backend/platform/platform_api/services/`
- 计费/对账 → `services/billing.py` + `services/channel_cost_events.py`
- 当前 Relay 渠道与生成扩展 → `backend/new-api-relay/relay/` + `integration/`
- 历史 Python 行为核对（离线 only）→ `backend/relay/relay_service/providers/`
- 视觉决策真相 → `AGENTS.md`（浅色纯白，非深色）
