# 零基础教程：把官方 API 接口放进项目的 new-api 账号池

> 适用对象：完全没碰过后端的小白。本文所有路径、端口、默认值都来自本仓库真实代码（`backend/new-api-relay` 与 `backend/platform`），不是网上通用教程。

---

## 一、先搞懂三个名词（最重要，别跳过）

你这个项目里，"账号池"不是一个神秘东西，它就是 **new-api 这个管理面板**。三个核心词：

| 名词 | 在本项目里是什么 | 大白话 |
|---|---|---|
| **new-api** | `backend/new-api-relay`（Go 写的，自带网页） | 你的"账号池管理面板"，所有官方 Key 都住这里面 |
| **渠道 (Channel)** | new-api 里的一个条目 | **一个货源 = 你申请的一个官方 API Key**（或一组 Key） |
| **令牌 (Token)** | new-api 里「用户 → 令牌」下生成的一串 | 发给咱们 Platform 的"门禁卡"，Platform 凭它才能调 new-api |

**一句话**：你把官方 Key 加进 new-api 的「渠道」= 把货源放进账号池；Platform 拿「令牌」这个门禁卡去池子里取货。

数据流向（先看这张图建立全局观）：

```
你的浏览器
   │  （只调咱们自己的产品）
   ▼
Platform 后端  (backend/platform，你的产品控制面)
   │  带着「令牌」去问："帮我用 Seedance 生成一段视频"
   ▼
new-api  (backend/new-api-relay，账号池)
   │  自动挑一个健康的「渠道」(=一个官方 Key)，坏了就换下一个
   ▼
火山方舟 / 可灵 / 阿里 …  (官方厂商，真正的生成发生在那)
   │  视频生成完，原路返回
   ▼
你的产品里看到成片 ✅
```

**你从头到尾都不需要指定"用哪个号"**——挑号、轮询、失败切换，new-api 全自动。

---

## 二、动手前要准备的两样东西

1. **装好 Docker Desktop**（new-api 用 docker 一键起，没有它跑不起来）。
   - 下载：https://www.docker.com/products/docker-desktop/
   - 装完打开，看到鲸鱼图标变绿就 OK。

2. **一个官方 API Key**（推荐先用**火山方舟**，个人实名后每天白拿 200 万 tokens，免费，够你练手和跑验收）。
   - 去 https://console.volcengine.com/ark  →  开通服务  →  「API Key 管理」→  创建 Key，复制保存。
   - ⚠️ **关键坑**：火山方舟的"模型名"不是 `Seedance` 三个字。你要在方舟里先建一个「**推理接入点**」，它给你的那个接入点 ID（形如 `ep-2025xxxxxxxx`）才是要填进 new-api 的"模型名"。

> 想先不申请真实 Key 练 UI？本项目 new-api 没有内置 Mock 渠道，所以**最省事就是直接用免费火山 Key**——反正免费，还顺带把验收跑了。

---

## 三、第 1 步：启动 new-api（账号池面板）

打开终端（Windows 用 PowerShell 或 Git Bash），进入项目里的 new-api 目录：

```bash
cd backend/new-api-relay
docker compose up -d
```

- `up -d` = 后台启动。第一次会下载镜像，等 1~2 分钟。
- 启动后浏览器打开：**http://localhost:3000**
- 端口在 `docker-compose.yml` 里是 `3000:3000`（改端口就改冒号左边那个）。

---

## 四、第 2 步：登录，立刻改密码

- 用户名：`root`      密码：`123456`
  （这是 new-api 上游默认账号，**必须马上改**，否则谁都能进。）
- 登录后右上角头像 → **修改密码**，设一个你记得住的。

---

## 五、第 3 步：把 Key 加进账号池（核心动作）

左侧菜单 → **渠道** → **新建渠道**，填这几项：

| 字段 | 填什么 | 说明 |
|---|---|---|
| 类型 | 优先选 **DoubaoVideo（豆包视频）**，也可以选 **VolcEngine（火山引擎）** | 代码里 DoubaoVideo = 54、VolcEngine = 45；两者都会路由到同一个豆包视频适配器，但 DoubaoVideo 更专指。你截图里的 `doubao-seedance-1-5-pro-251215` 就是 Doubao 视频模型。 |
| 名称 | 随便，如 `火山-Seedance-主账号` | 给你自己看的管理标签 |
| **密钥** | 粘贴你的 Ark API Key | 这就是"货源"本身；只粘 Key，不要粘整段 curl |
| **多 Key 模式** | 想做真正的"池子"就打开 ✅ | 打开后密钥框可以**每行一个 Key 粘多个号**；new-api 自动轮询，单个号报错自动禁用——这就是账号池 |
| **模型** | 填 `doubao-seedance-1-5-pro-251215` | 多个模型换行填；这个值就是你截图里 curl 的 `"model"` 字段 |
| 优先级 / 权重 | 多个渠道时谁先谁后 | 只有一个渠道可不管 |

填完点 **保存**。视频任务渠道的「测试」按钮通常测的是文本接口，不一定能直接点绿，**真正的测试是下一步通过产品发一个生成任务**。

> 💡 以后想加可灵，类型选 **Kling**（编号 50）；想加阿里选 **Ali**（17）、腾讯 **Tencent**（23）、MiniMax（35），操作一模一样：填 Key + 填模型 + 测试。多家并存时，new-api 按优先级/权重自动调度，正好是你项目的多渠道容灾设计。

---

## 六、第 4 步：给 Platform 发一张"门禁卡"

new-api 左侧 → **用户** → **令牌** → **新建令牌** → 生成后**复制那串 token**。

这串就是咱们 Platform 调 new-api 用的 `api_key`（代码里 Platform 是用 `X-API-Key` 请求头带它去调 new-api 的，已核实）。

---

## 七、第 5 步：让 Platform 连上 new-api

1. 复制环境变量模板：
   ```bash
   cp backend/platform/.env.example backend/platform/.env
   ```
2. 在 `backend/platform/.env` 里写入（把 `<第4步的token>` 换成你刚复制的那串）：
   ```ini
   RELAY_DEFAULT_BACKEND_ID=new-api-v1
   RELAY_DEFAULT_CONTRACT_REVISION=generations.v1
   RELAY_BACKENDS={"new-api-v1":{"base_url":"http://localhost:3000","client_id":"platform-new-api","api_key":"<第4步的token>","contract_revision":"generations.v1"}}
   ```
3. 重启 Platform 后端。

到这里，你的产品就已经"接上货源"了。

---

## 八、第 6 步：验证跑通

两种验证方式，任选：

- **走产品（最靠谱）**：打开你产品的「创作」页 → 选模型 → 填提示词 → 点生成。能看到进度条跑完出片，就通了。
- **走面板**：new-api「渠道」里点 **测试**。但对视频任务渠道，这个按钮不一定能正确返回；如果点不绿也别慌，以上面"走产品"为准。

数据流回顾：浏览器 → Platform → new-api（自动挑渠道里的 Key）→ 火山方舟 → 视频回来。

---

## 九、小白最常卡住的几个问题

| 现象 | 大概率原因 | 怎么办 |
|---|---|---|
| 渠道「测试」失败 | 对视频任务渠道，测试按钮不一定能返回正确结果；若 Key 确实有效，以产品端真实生成为准 | 先确认 Key 没复制错、方舟里该模型已开通；然后直接走产品「创作」页发任务 |
| 产品端生成失败/提示模型不存在 | new-api 模型库没登记这个模型 / 渠道类型选错 / 模型名填错 | 去 new-api「模型」页面添加 `doubao-seedance-1-5-pro-251215`；类型选 DoubaoVideo 或 VolcEngine；模型栏填截图里 curl 的 model 值 |
| 3000 端口被占用 | 别的程序占了 | 改 `docker-compose.yml` 的 `3000:3000` 左边端口，如 `3001:3000` |
| 多个号怎么一起用 | 没开多 Key 模式 | 回到第 3 步，打开「多 Key 模式」，密钥框每行一个 Key |
| 想换其他厂商 | 类型选错 | 可灵=Kling(50)、阿里=Ali(17)、腾讯=Tencent(23)、MiniMax(35) |

---

## 十、下一步

现在池子是空的，建议你今晚就干三件事：
1. 装 Docker；
2. 去火山引擎个人实名，领 Key（每天 200 万免费）；
3. 按上面 1~6 步走完，发出你的**第一个真实生成视频**。

等你跑通第一步，我可以陪你接着做：多 Key 容灾配置、把可灵接成第二渠道、以及接上火山真实成本数据让 Platform 的毛利账本活起来。需要我带你实际敲一遍命令，随时说。
