# AI-video 全项目与创作竞品审查

日期：2026-08-10  
范围：客户创作端、发布端、Platform、Python Relay、new-api Relay、测试/迁移/部署证据，以及即梦、可灵、LibTV 的公开页面。

## 结论

当前结论是 **生产 NO-GO，Python Relay 暂不能下线**。这不是单纯的“创作页面功能少”：

- 产品层已经有清晰的浅色视觉、能力驱动生成器、历史与发布骨架，但主链路仍停留在“填写参数 → 单次生成 → 下载”，尚未形成“灵感/脚本 → 角色与素材 → 分镜/多镜头 → 生成迭代 → 编辑 → 发布”的创作闭环。
- 工程层存在 Platform 全量测试失败、生产遥测配置缺失、迁移文档落后、候选快照过期、真实 OBS/Provider 证据为零等发布阻断项。
- 后端审查发现一个会造成未经有效授权而外发的 Publishing 真缺陷，以及未知提交对账、OBS 孤儿对象、渠道成本生产者等未闭环项。

未发现可直接定为 P0 的租户越权或管理员鉴权绕过，但有多项 P1 发布阻断。

## 本轮审查路径

1. **进入本地 Demo 创作页 — 基本健康**  
   浅色 Studio shell、历史任务、浮动生成器、Demo 身份和成本预估均能显示。

2. **切换视频/图片/音频等创作类型 — 失败**  
   内容类型只改变列表局部状态；切到“图片”后，生成器仍是文生视频，图片入口仍禁用。

3. **展开高级生成设置 — 部分健康**  
   支持参考图/视频/音频、比例、分辨率、时长、数量、面部选项，并受服务端 capability 约束；但大面积浮层遮挡历史上下文，内部滚动负担较重。

4. **查看历史、任务和结果 — 部分健康**  
   任务状态和结果预览存在；但创作页只筛选历史接口当前页的 24 条，失败任务费用文案会把报价显示成已花积分。

5. **从结果继续创作 — 失败**  
   结果弹窗只有“下载成片”，没有复用提示词、重新生成、变体比较、延长、编辑、加入项目或发布等下一步。

6. **创建发布任务 — 仅 Demo 可演示**  
   审批、排期、未知提交对账的信息架构较完整，但当前没有真实官方 Publishing Adapter/OAuth；弹窗键盘焦点管理不合格，Worker 最终外发授权复检还存在真实缺陷。

7. **移动端创作 — 失败**  
   底部导航的 CSS 同时保留 `top: 0` 与 `bottom: 0`，实际固定在顶部并覆盖 topbar；多项筛选与批量能力在小屏直接隐藏。

## 视觉证据

### 当前创作页

![当前创作页](C:/AI-agent-project-s2/AI-video/artifacts/product-audit-2026-08-10/01-current-creation.png)

### 当前高级设置

![当前高级设置](C:/AI-agent-project-s2/AI-video/artifacts/product-audit-2026-08-10/02-current-details.png)

### 当前结果弹窗

![当前结果弹窗](C:/AI-agent-project-s2/AI-video/artifacts/product-audit-2026-08-10/03-current-result.png)

### 当前移动端

![当前移动端](C:/AI-agent-project-s2/AI-video/artifacts/product-audit-2026-08-10/08-current-mobile.png)

### 即梦公开创作页

![即梦公开创作页](C:/AI-agent-project-s2/AI-video/artifacts/product-audit-2026-08-10/04-jimeng-creation.png)

### 可灵公开生成工作区

![可灵公开生成工作区](C:/AI-agent-project-s2/AI-video/artifacts/product-audit-2026-08-10/05-kling-creation.png)

### LibTV 官方公开页

![LibTV 官方公开页](C:/AI-agent-project-s2/AI-video/artifacts/product-audit-2026-08-10/06-libtv.png)

## 与竞品的核心差距

| 维度 | 当前产品 | 即梦公开页 | 可灵公开页 | LibTV 官方公开页 |
| --- | --- | --- | --- | --- |
| 创作入口 | 模型/模式/参数表单 | Agent 输入、技能、主体引用，并列图片/视频/音乐/配音/数字人/动作模仿 | 图片、视频、Motion Control、Avatar、Omni/Canvas | 宣称模型聚合、AI 导演 Skill 与完整创作链 |
| 多镜头/叙事 | 无项目、镜头和时间线结构 | 公开入口含脚本、主体、技能与画布 | 可见起止帧、Multi-Shot、Custom Multi-Shot、原生音频 | 宣称剧本、角色、世界观、无限画布、工作流 |
| 生成后迭代 | 结果弹窗只有下载 | 资产、画布、技能和发布形成后续入口 | 资产/Canvas/发布入口同属统一 shell | 强调从生成到剪辑、工作流沉淀与社区分享 |
| 资产复用 | 真实记录多为图标，缺可扫缩略图 | 资产和主体引用是一级入口 | Assets 与生成区并列 | 模型、工具、案例和工作流是产品叙事中心 |
| 当前优势 | 企业权限、钱包、成本、可靠 Relay、发布治理可形成差异化 | 偏消费级一站式创作 | 偏模型能力和专业控制 | 偏专业创作流程与聚合生态 |

公开证据：

- 即梦：[官方首页](https://www.jimeng.com/)；[公开创作入口](https://jimeng.jianying.com/ai-tool/home)
- 可灵：[官方站点](https://kling.ai/cn)；[公开生成工作区](https://kling.ai/app/video/new?ac=1)
- LibTV：[官方公开页](https://www.liblib.tv/wappro?sourceid=040004)

应学习的是完整创作工作流，而不是照搬深色皮肤、营销卡片或模型数量。当前产品更适合把“可靠生成 + 企业治理 + 成本/权限/发布”作为差异化底座，再补专业创作链。

## P1：发布前必须处理

### 测试与部署

1. **Platform 全量测试为红**：`418 passed / 21 failed / 4 errors / 14 skipped`。新增 `RELAY_TELEMETRY_SIGNING_SECRET` 未同步测试夹具；测试仍断言迁移 head 为 `0025`，实际已到 `0026`。
2. **生产遥测配置未形成部署闭环**：new-api 和 Platform 生产模式要求遥测 URL/签名密钥，但 `docker-compose.yml`、`.env.example` 和部署手册均未配置。
3. **旧候选证据过期**：8 月 7 日证据对应 1931 文件；当前源码 1934 文件，source/harness 哈希均已漂移。旧报告未被篡改，但不能证明当前代码。
4. **迁移手册危险落后**：README 仍写 `0017`，部署手册仍写 `0018`，当前 head 是 `0026_relay_telemetry`。
5. **根项目无 Git/根 CI**：仅嵌套 new-api 有 PR CI；主 package provenance 测试、Go race、真实 PostgreSQL/Redis integration 未进入持续门禁。

### 后端安全与可靠性

6. **Publishing 最终外发授权复检不完整**：Worker 未检查 grant 的 `effective_at`/`expires_at`，且复检与 Provider POST 之间仍有撤权/禁用连接竞争窗口。排期任务可能在授权已过期后外发。
7. **未知提交缺少受支持的运维闭环**：Relay 有单条 resolve POST，却没有待对账列表；所需 route/attempt 又不在普通快照中，Platform client/UI 无调用。安全上不会误重试，但余额和任务可能长期 hold。
8. **真实渠道成本没有正常运行时生产者**：目前非测试调用只在验收程序，毛利对账仍无法覆盖真实运行。

### 客户前端

9. **媒体类型与生成器脱节**：图片/音频等标签不能驱动真正 composer。
10. **创作页数据源错误**：只对历史接口当前页做客户端筛选，搜索/日期/模型过滤会错误漏数。
11. **失败任务费用语义错误**：报价被显示成已花“积分”，并与其他页面人民币单位冲突。
12. **没有真实 URL 路由**：刷新、深链、前进后退和分享具体页面均不可用。
13. **移动端导航覆盖 topbar**，且小屏隐藏关键过滤/多选能力。
14. **TOC 个人工作区及个人/企业切换尚未接入**。
15. **旧 pending-create 恢复路径可能省略 capability version 固定**。
16. **弹窗可访问性不足**：无初始聚焦、focus trap、Escape、背景 inert 和关闭后焦点归还。

## P2：应进入下一阶段

- OBS 转存租约固定 5 分钟、无续租；对象 key 含 lease token，上传后才 CAS，ArtifactStore 又无删除接口，慢上传或换租约会形成不可达 OBS 对象。
- Platform 任务没有 Relay backend affinity；candidate 接单后不能立即全局切回 Python Relay，只能排空或由 candidate 收敛。
- 创作页“历史上传”、音频、快应用是硬编码空状态；“已保存”只是成功任务，多选没有动作，所谓分组只是排序。
- 真实任务不可取消，签名预览过期后没有重新签发入口，素材加载失败无就地重试。
- “产物自动转存”设置仅保存在 React 状态，既不生效又违背产物安全不变量。
- new-api 主容器仍以 root 运行，未设置 `read_only`/`cap_drop`；Go 工具链版本不一致。

## 已有优势

- 能力声明、revision 固定和模式切换清理总体 fail-closed。
- 未知提交保留稳定幂等键且不自动跨渠道重试。
- 下载 URL 签发与“确认下载完成”明确分离。
- 发布 `submission_unknown` 必须人工核销，Demo/Mock 状态标识清楚。
- 公司权限、钱包预占/结算/释放、成本不完整标识、用户/公司产物范围的基本不变量没有发现直接绕过。
- 社区瀑布流、统一浅色 shell 和浮动 composer 已形成视觉辨识度。

## 建议路线

### Phase 0：先恢复可信发布基线

- 修复 Platform 测试夹具和迁移断言，让全量测试全绿。
- 补齐遥测部署变量、测试和最新迁移手册。
- 修复 Publishing 最终授权复检、移动导航、媒体类型联动、费用语义和历史数据源。
- 建立根 Git/CI，并把 provenance、race、PostgreSQL/Redis integration 纳入门禁。
- 重新冻结 candidate/harness 哈希，再跑真实 OBS、真实 Provider、跨服务成本与故障注入。

### Phase 1：把“单次生成页”升级为“项目创作台”

- 增加项目、场景、镜头和版本结构；保留轻量“快速生成”，新增“专业创作”。
- 将脚本、角色/主体、品牌资产、参考媒体作为可复用一级对象。
- 用固定创作工作区取代遮挡历史的大浮层；左侧项目/镜头，中间预览，右侧 capability 驱动参数，底部版本/任务流。
- 结果操作补齐：再次生成、创建变体、对比、延长、首尾帧衔接、加入镜头、编辑、发布。

### Phase 2：形成差异化闭环

- 引入多镜头/分镜和时间线、主体一致性、镜头运动、原生音频/配音、批量变体。
- 把企业已有优势接入创作过程：共享品牌资产、角色权限、预算/成本上限、审批、发布排期、渠道效果回流。
- 不以“模型数量”作为主竞争点，以“可靠地产出并可治理、可发布、可核算”为主叙事。

## 测试结果与证据边界

可信通过：根 Node 测试 181 项；前端生产构建及 Sites 三件套；Python Relay 323 项通过、4 项因真实 PostgreSQL/Redis 跳过；针对创作/发布/能力/响应式的 46 项测试通过。

证据限制：本轮没有可用的真实 OBS SDK 凭据、真实 Provider 凭据或完整生产 IdP/支付/发布 Adapter，因此未执行真实上传、生成、结算和外发；竞品审查仅覆盖无需登录的公开页面，登录后资产、生成成功态和计费没有验证。截图不能代替完整键盘/WCAG 审计。

透明说明：审查期间测试子任务误在项目根目录执行了一次成功的 `npm run build`，因此重新生成了 `dist`。根目录不是 Git 仓库，为避免破坏用户文件，没有尝试强制回滚；未修改业务源码。
