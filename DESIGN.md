---
name: "旭天 AI studio"
description: "浅色媒体制作系统：邀请式入口、三类创作工作台与证据驱动的管理工作面"
colors:
  brand-deep-ocean: "#087B80"
  brand-mint-node: "#13B8A6"
  accent-violet: "#5a55d2"
  accent-violet-strong: "#4a45c2"
  accent-violet-soft: "#eeedfb"
  signal-orange: "#ce360a"
  signal-orange-strong: "#a92a06"
  signal-orange-soft: "#fff0e9"
  paper-background: "#f6f6f2"
  paper-canvas: "#fafaf7"
  surface: "#ffffff"
  surface-soft: "#f2f3f1"
  surface-hover: "#ecefed"
  stage-surface: "#e8ebe9"
  line-subtle: "#dedfdc"
  line-strong: "#c9cbc7"
  graphite: "#171a20"
  graphite-soft: "#484d55"
  graphite-muted: "#656b74"
  success: "#177654"
  success-soft: "#edf7f2"
  warning: "#9a6207"
  warning-soft: "#fff6de"
  danger: "#c33630"
  danger-soft: "#fff0ef"
  mist-background: "#eef0f2"
  mist-surface: "#fbfcfc"
  warm-background: "#f4f0e8"
  warm-surface: "#fffdf9"
  console-night: "#17212c"
  notebook-paper: "#fffaf0"
  notebook-rule: "#eee3ca"
  lineage-teal: "#238d7e"
typography:
  display:
    fontFamily: "Noto Sans SC Variable, Manrope Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "clamp(2rem, 2.6vw, 2.75rem)"
    fontWeight: 700
    lineHeight: 1.08
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Noto Sans SC Variable, Manrope Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 600
    lineHeight: 1.18
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Noto Sans SC Variable, Manrope Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 600
    lineHeight: 1.18
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Noto Sans SC Variable, Manrope Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  body-small:
    fontFamily: "Noto Sans SC Variable, Manrope Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: "Noto Sans SC Variable, Manrope Variable, PingFang SC, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0.01em"
  mono:
    fontFamily: "SFMono-Regular, Cascadia Code, Roboto Mono, Consolas, Liberation Mono, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
rounded:
  control-small: "8px"
  control: "10px"
  media-small: "10px"
  media: "12px"
  media-large: "14px"
  work-surface-small: "12px"
  work-surface: "16px"
  pill: "999px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "20px"
  6: "24px"
  8: "32px"
  10: "40px"
  12: "48px"
  16: "64px"
components:
  button-primary:
    backgroundColor: "{colors.signal-orange}"
    textColor: "{colors.surface}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 16px"
    height: "44px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.graphite}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "40px"
  input-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.graphite}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "40px"
  navigation-selected:
    backgroundColor: "{colors.accent-violet-soft}"
    textColor: "{colors.accent-violet-strong}"
    typography: "{typography.body}"
    rounded: "{rounded.media}"
    padding: "10px 14px"
    height: "48px"
  creation-path:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.graphite}"
    typography: "{typography.body}"
    rounded: "{rounded.work-surface}"
    padding: "22px"
  director-intent:
    backgroundColor: "{colors.paper-canvas}"
    textColor: "{colors.graphite}"
    typography: "{typography.body}"
    rounded: "{rounded.work-surface-small}"
    padding: "12px 16px"
---

# Design System: 旭天 AI studio

## Overview

**Creative North Star: “Light Production Rooms（浅色制作场）”**

旭天 AI studio 的当前系统不是一个换色后的通用 SaaS 壳层，而是一组共享同一浅色材料、品牌和交互底座的真实制作场。默认创作入口先邀请用户选择工作方式；3D 导演台、导演手记和谱系画布各自保留独立的空间、阅读和关系隐喻；页面底部的极速档则始终承担一句话直接开机的高频路径。它们共享数据与任务真相，但不被压成同一套预览框、参数表或卡片网格。

产品的质感来自中文排版、真实媒体、清晰留白、目的明确的圆角、选择性软投影和可核验状态。Studio 可以有作者感和场景性；Company、Operations 与 Auth 继续以 Operate 模式服务权限、账本、可靠性、身份和恢复任务，不照搬创作页的舞台，也不退回“所有东西都是直角 + 1px 灰框”的旧桌面软件语言。

视觉变化不得重写产品边界：浏览器只调用 Platform；模型、能力、价格、授权、配额和并发以服务端证据为准；个人与企业钱包、任务和资产保持隔离；生成继续遵守幂等及 reserve → settle/release；发布继续使用已归档产物、人工审批和未知提交防重试。

**Key Characteristics:**

- 默认是可立即行动的邀请式创作入口，而不是空报表。
- 三个高级工作台各有自己的导航拓扑、状态位置、排版和交互节奏。
- 极速档常驻文档流，提示词优先，低频控制互斥披露。
- 选择、状态和推进动作各用一种清晰视觉语言，不重复堆叠。
- 纯白、雾灰、暖米仅改变浅色材料温度，不改变结构和语义。
- 管理面保持连续账本和证据密度，Studio 的表现力不得污染管理任务。

## Colors

颜色以暖灰纸面、白色工作表面和石墨文字为底；紫罗兰表达选择、焦点、系统进度与可追踪关系，信号橙只表达生成、交接和发布等推进动作。

### Primary

- **Selection Violet** (`accent-violet` / `accent-violet-strong` / `accent-violet-soft`)：用于导航选中、焦点、当前模式、系统进度和可追踪关系。它不是所有按钮的默认颜色。
- **Signal Orange** (`signal-orange` / `signal-orange-strong` / `signal-orange-soft`)：用于“开机”、生成、交接、发布和需要明确确认的生产动作；普通导航和只读状态不得借用它制造紧迫感。

### Secondary

- **Deep-ocean Brand Teal** (`brand-deep-ocean`)：只服务批准的 X/播放负形和品牌字标。
- **Mint Circuit Node** (`brand-mint-node`)：只用于横版字标中的两处电路节点，不扩张为界面主色。
- **Lineage Teal** (`lineage-teal`)：谱系画布的节点、种子和关系操作专色；它属于该工作台的隐喻，不替代全站选择紫。

### Tertiary

- **Console Night** (`console-night`)：仅为 3D 导演台的真实监视器外壳和场记条提供必要深色对比；它不构成深色主题。
- **Notebook Paper** (`notebook-paper` / `notebook-rule`)：仅用于导演手记的纸页与横线，让叙事文本具有册页性格。

### Neutral

- **Paper Material** (`paper-background` / `paper-canvas` / `surface`)：默认视觉验收源，建立近白背景、连续画布和白色工作面。
- **Soft Surfaces** (`surface-soft` / `surface-hover` / `stage-surface`)：承担轻分组、悬停和无媒体占位，不作为卡片套卡片的借口。
- **Graphite Scale** (`graphite` / `graphite-soft` / `graphite-muted`)：分别承担主文字、说明和弱证据；弱化不能低到不可读。
- **Rules** (`line-subtle` / `line-strong`)：用于真实结构边界、表格行和浮层边界，不是全站信息层级的唯一手段。
- **Light Skins**：`paper` 为默认；`mist` 使用 `mist-background` / `mist-surface`，`warm` 使用 `warm-background` / `warm-surface`。三者均保持 light color-scheme。

### Semantic

- **Success、Warning、Danger** 使用各自的正文色与 soft 背景；状态还必须有文字、图标或形状，不能只靠颜色。
- 数据缺失显示“未提供”“不可用”或“待核验”，不能伪造为 0、健康或成功。

**The Light-only Rule.** 产品只提供 `paper`、`mist`、`warm` 三套浅色皮肤；不增加黑色、深色或“跟随系统深色”。

**The One Selection Rule.** 一个导航项或选项只使用一种选中指示。Studio 主导航使用 soft fill + strong ink；下划线式 tab 使用单一下划线；管理模块可使用单一实色块。禁止同时叠加描边、填充、侧条和系统焦点框。

## Typography

**Display Font:** Noto Sans SC Variable，Manrope Variable 作为拉丁与数字伴随字体

**Body Font:** Noto Sans SC Variable，随后进入 PingFang SC 与系统回退

**Label/Mono Font:** SFMono-Regular / Cascadia Code / Roboto Mono / Consolas

**Character:** 中文优先、数字稳定、标题紧致。通用产品界面坚持无衬线；只有导演手记中的场景标题和正文可使用 `ui-serif, Songti SC, STSong, serif`，把叙事稿与系统控制明确区分。

### Hierarchy

- **Display**：邀请入口和极少数页面主句；使用 display token，创作邀请可按已实现构图放大到约 34–62px。
- **Headline**：页面标题、工作台标题和关键结果标题；紧字距，不使用装饰性英文大写堆叠。
- **Title**：分区标题、抽屉标题和证据组标题。
- **Body**：输入、说明、操作正文；正文行高保持易读，长说明控制在约 68–78ch。
- **Body Small**：表格、账本、任务元数据；不得低于 13px。
- **Label**：caption、字段名和状态辅助信息；普通可见文案的绝对下限为 12px。
- **Mono**：任务 ID、事件引用、版本、时间戳和不可变证据；金额与表格数字使用 tabular lining numerals。

共享字重以 400、500、600、700 为主。当前 Studio 邀请标题、路径标题和手记标题使用 variable font 的 650–750 中间重量属于已实现的签名排版例外，不扩散到普通控件和管理表格。

**The Chinese-first Rule.** 先检查中文断行、标点挤压和数字基线，再评估英文标签；不得用 9–11px 字号换取所谓“高级感”或密度。

**The Notebook Exception Rule.** 衬线只属于导演手记的稿页内容，不进入壳层、按钮、表格、模型选择器或 Operations。

## Layout

### Cascade ownership

浏览器只有一个样式入口：`src/design-system/index.css`。固定级联为：

1. `system.tokens`：`tokens.css`
2. `system.foundation`：`foundation.css`
3. `system.controls`：`controls.css`
4. `system.shells`：`shells.css`、`chrome.css`
5. `system.routes`：Studio、Company、Operations、Auth、品牌与移动路由文件

新样式必须进入正确层级；迁移时删除被替代声明，不重建退休样式入口，也不新增“最后覆盖一切”的高特异性文件。

### Shared shells

- Studio 桌面是 76px 顶栏、184px 左轨和最大 1840px 内容面；1180px 收窄，900px 以下转为顶栏 + 横向路由轨道 + 内容。
- Studio 左轨可滚动，使用图标 + 文字，不编号；窄屏轨道末端保留 44px 前进/回绕按钮。
- Company 使用约 68px 命令栏、224px 权限侧栏、最大 1480px 连续账本画布；工作区和公司上下文留在命令栏。
- Operations 使用横向模块导航和最大 1660px 的连续 Operations Canvas；状态流、异常、图表和可靠性表按证据关系组合，不变成 KPI 卡片墙。
- Auth 公共登录是单栏、最大约 480px 的极简入口；邀请、回调、失效和账户恢复可展示任务所需证据，但不得把说明重新塞回登录首屏。

### Studio creation composition

- **Invitation entry（默认）**：以“今天想创作什么？”为首要句，下面是 3D 导演台、导演手记和谱系画布的路径选择；近期任务用于继续真实工作，不冒充灵感热度。
- **Persistent fast lane（极速档）**：作为壳层的独立网格行参与布局，始终位于创作页面底部，不覆盖路径卡、任务记录或最终操作。入口中的极速档提示只负责把焦点送到该写作面，不复制第二个生成器。
- **3D 导演台**：监视器墙承载真实素材、当前 take 和最近结果；桌面模块展示真实比例、时长、画质和模型；场记单承载提示词与开机/继续动作。3D 只表达有功能的数据关系。
- **导演手记**：任务按场次进入暖纸页；系统状态贴着场次，正文使用克制衬线；“手记 / 成片候选”在同一册页内切换并把已核验产物交给发布流。
- **谱系画布**：真实任务成为可选择节点，种子和继续创作表达来源关系；选中节点的操作集中在画布内的 inspector，不伪造剪辑轨道或不存在的血缘边。
- **Progressive record browser**：历史筛选和完整任务列表折叠在创作面之后，默认不与创作入口争夺首屏。

### Responsive composition

- 390px 与 320px 都是验收宽度；手机不是桌面缩放。
- Invitation 路径变为单列，3D 路径保持主导但不吞掉其他入口；近期任务成为横向 snap 轨。
- 3D 监视器墙在手机取消透视，变为可横向滑动的真实监看卡；参数模块横滑，场记单与动作全宽。
- 手记场次变为单列，媒体与动作仍可达；谱系节点变为水平画布，inspector 固定在画布内部而不是覆盖页面外的控件。
- 极速档手机端使用单焦点状态：收起时是 72px launcher；展开后 prompt 或一个配置面板成为唯一滚动主人。参考、规格、模型、就绪面板互斥，不把所有配置堆成一条长表单。
- 手机操作目标至少 44px；登录主动作至少 48px；文档根节点不能横向溢出，只有明确的媒体轨、节点画布和证据表可内部横滑。

## Elevation & Depth

系统采用“平面为底、选择性抬升”的混合策略。普通内容依靠留白、排版和柔和表面差建立层级；阴影只在路径卡、监视器、场记单、手记纸页、谱系节点、inspector、菜单、抽屉和对话框中说明真实空间关系。

### Shadow vocabulary

- **Panel** (`--shadow-panel`)：低对比软投影，用于轻抬升的工作面；不叠加厚边框。
- **Menu** (`--shadow-menu`)：用于 popover 和菜单，配合清晰边界及焦点管理。
- **Overlay** (`--shadow-overlay`)：用于抽屉或对话框，必须同时有 scrim 和可恢复焦点。
- **Dock** (`--shadow-dock`)：只在确实需要说明停靠层级时使用；创作极速档仍必须占据文档流空间。
- **Signature depth**：3D 监视器、手记纸页、路径卡和谱系节点使用各自已实现的柔和长阴影；这些阴影不抽象成管理表格的默认卡片样式。

Company 和 Operations 是例外的高证据密度表面：表格行、sticky header、审计分区和高风险动作可以使用精确规则线。这里的线是数据结构，不是全站视觉人格。

**The No Pervasive Box Rule.** 不用“每个分区四边 1px 灰框”建立层级；能由排版、留白、表面差或真实媒体表达的关系，不再套盒。

**The Functional Depth Rule.** 任何明显投影、透视或 3D 形态都必须承载真实数据、状态或操作；纯装饰 3D、发光和玻璃拟态不进入产品。

## Shapes

Studio 的形状层级是有意的，而不是全站统一成直角或统一成大药丸：

- **Controls：8–10px。** 输入、按钮、导航操作和紧凑触发器使用小而清晰的圆角。
- **Media：10–14px。** 缩略图、take、结果和监视器按尺度使用中等圆角，媒体裁切与容器一致。
- **Work surfaces：12–16px。** 路径卡、场记单、谱系 inspector 和主要创作工作面使用更完整的圆角与选择性抬升。
- **Signature exception。** 导演手记的纸页在当前实现中略大于通用工作面半径，以保留册页特征；它不是新的全站 token。
- **Pills。** 999px 只用于状态、计数和皮肤样本等真正胶囊语义，不把所有按钮变成 pill。
- **Structural square geometry。** 表格、连续账本、分隔规则和 Operations 的密集证据网格保持精确直线；Company/Operations 表内小动作可使用当前 2px 紧凑例外，但这种例外不得回流到 Studio 控件。

焦点统一使用 2px 高对比紫罗兰轮廓并保留 offset；焦点轮廓是键盘状态，不与选中态合并，也不能通过 `outline: none` 消失。

**The Shape Hierarchy Rule.** 同屏形状必须说明职责：控件 < 媒体 < 工作面。禁止把所有对象都做成同一半径，也禁止恢复全站零圆角。

## Components

### Brand

- 使用 `public/brand/xutian-ai-studio-wordmark.svg` 和 `public/brand/xutian-ai-studio-symbol.svg`。
- 桌面优先横版字标；紧凑、手机和 favicon 使用单色 X/播放符号。
- `AI studio` 大小写固定；播放三角是真实负形；横版字标只有两处薄荷节点。
- 不添加黑底、中心彩条、渐变、发光，也不恢复机器人、脑、sparkle、shield、camera 或 film-reel 品牌替代物。

### Buttons and inputs

- 控件高度只有 32px 紧凑、40px 默认、44px 触控/主要动作三档；手机命中区至少 44px。
- Studio 的生成、开机、交接和发布用信号橙；普通主要按钮及管理操作使用选择紫；danger 使用独立危险语义。
- 禁用态降低强调但必须保持可读；生成按钮禁用时，其唯一一行原因与按钮同一区域关联。
- 输入使用白色或纸面填充、8–10px 圆角和明确 focus ring；textarea 可垂直调整但不能挤出主动作。
- 图标按钮保持方形命中区和可访问名称；带文字的按钮不能被通用规则压成图标方块。

### Navigation

- Studio 桌面左轨使用 soft fill + strong ink 作为唯一选中指示；手机横轨为同一语义的响应式表达。
- 页面级 tab 可使用单一下划线；Company/Operations 模块可使用单一实色选择块。每个控件只保留一种 selected affordance。
- tab 使用 roving tabindex，支持方向键、Home、End；横向轨有键盘路径和可见滚动提示。

### Light-skin switcher

- 复用一个可访问菜单按钮，不使用浏览器原生 select 或透明 select 覆盖图标。
- 菜单展示当前纸面、三份真实皮肤样本、明确选中态，并支持键盘、外部点击和 Escape 关闭。
- 紧凑顶栏可隐藏文字标签，但保留材料预览和可访问名称；手机目标为 44px。
- 公共登录入口继承本地皮肤但不显示切换器。

### Invitation entry

- 空状态本身就是创作入口：主句、短说明、三个路径和可继续的真实近期任务构成完整首屏。
- 3D 导演台路径在当前网格中拥有更高视觉重量；导演手记与谱系画布是平行高级工作台，不是它的设置面板。
- 路径卡使用 16px 工作面圆角、软投影和短距离 hover 抬升；不得增加装饰性 AI 插画或重复的第二个 prompt。

### 3D 导演台

- 三块监视器分别承载任务素材、当前 take 与最近结果；无真实数据时显示诚实占位。
- 透视地面只解释监视器的空间关系，不承载虚假设备。
- 参数模块读取当前任务/极速档设置；场记单展示真实 prompt、任务元数据和结果动作。
- “继续调整”把已存在任务恢复为能力协调后的草稿，不收费；“转存素材”和“去发布”继续服从归档证据、权限与审批流。

### 导演手记

- 每场来自真实任务；状态、提示词、take 和操作贴着该场出现，不另起全局状态墙。
- 只在稿页叙事使用宋体/serif；系统栏、按钮和状态继续使用共享 sans。
- 成片候选只聚合成功且有已核验 artifact 的场次；进入发布仍需现有审批和目标账号流程。

### 谱系画布

- 节点代表真实任务，选中使用 lineage teal 单一轮廓；节点 inspector 集中详情、继续创作和交接。
- 空画布显示可执行的新种子，不显示报表式空状态。
- 不绘制没有服务端或任务来源证据的连线、分支、协作者或编辑历史。

### Persistent fast lane / Director Deck

- 极速档是创作页底部常驻、参与布局的快速生成面；它不得 fixed 覆盖路由内容。
- prompt 是主角。媒体类型与 recipe rail 在上方提供结构，执行 spine 在下方集中模型、成本和 CTA。
- references、recipe、specs、model、readiness 是互斥的 in-flow disclosure；一次只打开一个，关闭媒体触发器时 prompt 自动回到完整宽度。
- 能力控件只显示 Platform 返回并允许的字段。模型、模式、媒体槽、比例、时长、画质、产物数、face 资源和价格都必须在提交时由服务端再次校验。
- readiness 披露展示模型、模式、workspace grants、选项和 capability revision 的人类可读证据；证据缺失时 fail closed。`face_enabled=false` 时不得因 face-library 资源阻塞默认路径。
- **一个 composer 只有一条总体状态。** `composer-submit-status` 承担 error、warning、ready/disabled reason 中当前最重要的一条；字段错误只留在对应字段，readiness 详情只留在 disclosure，模型、价格和禁用原因不得在多处重复。
- 成本只显示服务器报价；个人 points 与企业资金范围不可混用。提交保持幂等，失败任务不结算生成费用。

### Task state and server-evidenced ETA

- 任务状态属于监视器、场次、节点、结果或当前 composer 上下文，不重复成为多条 banner。
- 活跃任务只有在 Platform 任务数据提供正数 `eta_seconds`、`estimated_remaining_seconds`、`remaining_seconds`，或带时区的 `eta_at` / `estimated_completion_at` 时，才显示“预计剩余 N 秒”。
- 没有这些服务端字段时只显示“任务已接收”“等待调度”或“生成处理中”等真实阶段，不在客户端发明倒计时。
- `timed_out`、`reconciliation_required`、未知提交和 callback dead-letter 按真实语义显示，并禁止误导性自动重试。

### Company and Operations

- Company 是连续企业账本与权限工作面：成员、角色、模型、消费、余额、权限继承和并发证据优先；无权模块由服务端权限裁剪，不先渲染再用 CSS 隐藏。
- Operations 是横向模块导航下的连续证据画布：任务状态流、异常队列、可靠性表、Relay fencing、unknown submission、callback DLQ 和原生高风险入口保留明确结构。
- 管理表格正文至少 13px，数字 tabular；桌面表内动作至少约 36px，手机提升到 44px；图表必须有可读数据表或详情。
- 管理面可以更直、更密、更依赖分隔规则，但不能复制 Studio 的路径卡、3D、手记纸页或谱系节点作为装饰。

### Auth and overlays

- 公共登录只保留品牌、一个标题、一句说明、真实 loading/error/retry、一个主要动作和密码处理说明；不显示双栏 assurance、皮肤选择器或重复身份边界文案。
- 邀请、回调、退出与账户管理按任务需要显示证据，并保持 Cookie、CSRF、session rotation 和接口鉴权的服务端约束。
- dialog 与 drawer 支持 Escape、Tab/Shift+Tab 循环、关闭后焦点恢复、`dvh` 与 safe-area；标题、正文滚动区和 footer 始终可达。

**The One Status Rule.** 每个任务上下文和 composer 各有一个主状态位置；相同错误、就绪、模型、价格或禁用理由不得在 banner、badge、footer 和 CTA 周围重复出现。

## Do's and Don'ts

### Do:

- **Do** 把 invitation entry 作为默认创作入口，把 3D 导演台、导演手记和谱系画布保留为三个独立工作台。
- **Do** 让极速档始终参与页面布局，并在 1056×640、390px 和 320px 检查最后一个字段、成本、禁用原因和 CTA 可见可点。
- **Do** 使用 8–10px 控件、10–14px 媒体、12–16px 工作面的形状层级；只在结构表格和管理证据中使用精确直线。
- **Do** 让 selected、focus、status 和 workflow action 各自使用清晰且不重复的视觉语义。
- **Do** 让任务 ETA、能力、授权、报价、任务状态、artifact 和发布状态来自服务端证据；证据缺失时诚实降级或 fail closed。
- **Do** 在 1440×900、1024×700、390×844、320×640 和键盘路径验证 Studio；同时验证 Company、Operations 与 Auth 的关键正向及负向权限场景。
- **Do** 视觉变更后运行 `npm test`、`npm run build`、`npm run test:sites`，并进行真实浏览器桌面/手机/键盘巡检。

### Don't:

- **Don't** 恢复旧 Director Focus 的“大舞台 + 版本条 + 参数表”单一骨架，也不要把三种工作台重新融合成一屏。
- **Don't** 把“方正”解释成全站零圆角、1px 灰框、边框套边框或 ERP 式配置面板。
- **Don't** 给一个导航选中态同时加边框、填充、侧条/底条和焦点轮廓。
- **Don't** 在 composer 重复显示错误、就绪、模型、价格和禁用状态；禁用 CTA 只配一条人类可读原因。
- **Don't** 用固定/浮动 composer 覆盖内容，或在手机同时堆叠舞台、prompt、完整版本链和全部参数。
- **Don't** 增加深色皮肤、紫色玻璃渐变、霓虹 glow、装饰性 AI 图案、无功能 3D、KPI 卡片墙或无意义 pill 群。
- **Don't** 伪造模型、渠道、发布账号、任务、热度、零值、下载完成或健康状态，也不要把 demo persona 当作生产授权证据。
- **Don't** 让视觉可见性替代 Platform/Relay 的权限、租户、钱包、幂等、计费、归档、审批和审计约束。
