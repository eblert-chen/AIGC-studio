# 本地视频模型整链联调

这是原项目的隔离联调入口，不是另一个前端产品，也不是生产发布候选。浏览器使用原 Studio 源码，只调用真实 Platform；Platform 使用 PostgreSQL 16、原授权/价格/任务/工作器；Relay 使用原 generations、原生适配器、轮询和成片存储。mock 只替换最外层供应商 HTTP 服务。普通本地环境、真实账户、余额和 Auth0 不会被改成演示配置。

## 当前模型集合

| Studio / Platform 标识 | 供应商标识 | 当前公共合同模式 |
| --- | --- | --- |
| minimax-h3 | MiniMax-H3 | 文生、图生、视频参考 |
| minimax-h3-max | MiniMax-H3-Max | 文生 |
| seedance-1.0-pro | doubao-seedance-1-0-pro-250528 | 文生、图生 |
| seedance-1.0-pro-fast | doubao-seedance-1-0-pro-fast-251015 | 文生、图生 |
| seedance-2.0 | doubao-seedance-2-0-260128 | 文生、图生、视频参考 |
| seedance-2.0-fast | doubao-seedance-2-0-fast-260128 | 文生、图生、视频参考 |
| seedance-2.0-mini | doubao-seedance-2-0-mini-260615 | 文生、图生、视频参考 |
| seedance-2.5 | doubao-seedance-2-5-260628 | 文生、图生、视频参考 |

8 个可新增模型、20 个模型/模式组合。这里的“视频参考”不是宣称已支持供应商所有编辑、延长、音频或草稿功能。停止服务/停止新购型号以及公共合同未涵盖的高级参数仍按 [Ark 接入边界](ark-video-integration.md)、[H3 接入边界](minimax-h3-integration.md) 处理。供应商账号是否有这些型号的实际开通权限，要在真实账号上另行确认。

## 不付费的本地入口

只使用仓库规定的启动入口：

```powershell
npm run services:start:local -- -VideoLab
npm run models:status:local
npm run models:verify:local
```

启动后打开 http://127.0.0.1:14178/__local-video-lab__ ，明确进入独立测试工作区。原服务启动方式仍是 `npm run services:start:local`（不带 `-VideoLab`）。

- mock Docker 后端在 internal 网络中，不访问官方供应商。固定 TCP 边缘只映射本机 18420/18430/18440，不提供任意 URL 代理。
- 浏览器与同源 API 网关为 14178；辅助 API 网关为 18480；Platform 为 18420；Relay 为 18430；本地 TLS 对象存储为 18440。数据库和 Redis 不向宿主公开。
- 每种模式有独立身份、PostgreSQL 数据库、Relay 状态、Redis、对象卷和会话 Cookie。配置存放在 `.tmp/local-video-lab/<lab-id>/`；前端构建也在该目录的 `frontend/`，不覆盖 `dist/client`。联调构建明确为 development，演示模式明确关闭；这样沿用原前端只在 development 允许本机 HTTP 成片的规则，不给生产构建增加例外。
- 自动配置通过原 Platform 管理 API 做模型目录同步、草稿审核/发布、公司授权与积分价格，不绕过发布证据和提交校验。
- 测试预算为 2000 非现金积分，单价 1 测试积分/秒，并发 1。不是正式售价、客户额度或供应商费用。
- route probe 的成功必须有真实 `artifact_verified`。成功证据接近原 24 小时有效期时，另记新批次；未知/失败结果保留原操作，不能靠新幂等键自动重试。
- 重跑验证使用稳定任务键：已有成功任务先核对原请求、成片和计费，不重复创建或扣分。
- 自动验证针对固定的 20 条用例清单。请先运行验证再手动体验页面；出现清单外任务、在途预留或其他预算占用时，验证器会拒绝继续，不删除任务、不补发请求，也不把混合数据当作通过。当前保留 20 条验收任务和 1 条浏览器生成任务，可直接查看结果。

## 参考素材与成片

参考素材经过原 Platform 上传 API、真实文件/哈希/状态校验和签名。mock 专用的 HTTPS 域只转发精确 input-assets 内容路径到固定 Platform，保持签名原样，不传会话/管理凭据、不跟重定向。模拟供应商会真实读取这些素材；不能把一个仅存在于请求 JSON 的 URL 算作素材链路通过。

输出存储为独立、持久化的本地 OBS-v2 协议服务：实际 SDK PUT、HEAD、签名 GET、Range 和 SHA/大小验证。它不是生产云 OBS；生成成片包含明显的 MOCK PROVIDER 标识，不代表供应商画质。

本地 mock 视频样本只覆盖 16:9、4/5 秒、480p/720p/768p 中各型号允许的组合。前端仍展示真实能力全集；其他组合不伪造样片成功。真实供应商的高分辨率、长时长、音轨和设备解码兼容性仍需后续验收。

## 接入真实 Key 前

今晚不自动调用真实供应商、不验证真实云凭据、不部署、不切生产流量。凭据仅写本机私有文件，不放在聊天、VITE 环境变量、浏览器或版本库中：

1. `.tmp/local-video-lab/keys/ark.key`：方舟北京区域 API Key。
2. `.tmp/local-video-lab/keys/minimax.key`：MiniMax 中国区 API Key（固定 `https://api.minimax.cn`，不能把国际区 Key 当作中国区 Key；旧 `api.minimaxi.com` 会失败关闭）。
3. 若要图生/视频参考，按 `.tmp/local-video-lab/keys/input-storage.template.json` 新建同目录 `input-storage.json`：新 OBS 官方 HTTPS endpoint、独立 bucket、AK、SK，可选短期 security_token。API 与 dispatcher 自动使用同一份配置。

旧 `deploy/secrets/huawei-obs.runtime.env` 已被项目标记为退役来源，本入口不会读取它。供应商必须能通过公共可信 HTTPS 获取参考素材；本机 HTTP 地址、私有 CA 和 Docker 主机名都不能当作真实供应商素材地址。因此，缺少新的公网素材存储配置时，不能承诺“只填模型 Key，全部参考模式就能跑”；live 全模式启动会明确拒绝。

填写后运行只做离线配置准备的命令：

```powershell
npm run models:prepare:live
```

它生成绑定真实 Key 状态和准确模型集合的 `paid-probe-approval.template.json`，不会调用供应商。只有操作者明确填写 actor、reason、独立 operation_id、UTC 批准/到期时间、有限 max_provider_creates，才可以通过下列入口启动独立 live 测试：

```powershell
npm run services:start:local -- -VideoLab -VideoLabProviderMode live -VideoLabPaidProbeApproval C:\absolute\operator-approved.json
```

八个首次路由探测至少占用八次供应商创建预算；额外创作同样计数。有效期最多 24 小时。预算耗尽、过期、模型不符、缺批准均拒绝；仅填 Key 不是付费授权。这个命令是未来操作说明，本轮不会执行。

续期必须使用新的批准批次，旧批准文件保留不可重写；Platform 的初始数据库身份不随续期改变。Key/能力状态变更不复用旧证据；检测到身份不符时停止，不自动删除数据重建。

## 证据与停止

`platform-receipt.json` 是实际目录/授权/能力证据；`probe-results.json` 是供应商探测操作日志；`verification.json` 是最近一次整链结果，每次运行另保存不可覆盖的 `verification-history/<UTC时间>.json`。它们可能包含本地账号/操作标识，不是生产验收凭证。运行失败时命令必须返回非零，不能只打印一份失败报告后假装通过。

`node scripts/local-video-lab.mjs stop` 仅停止身份匹配的 lab 网关与 compose 项目，不删除数据库、对象卷、原服务或用户数据。再次启动仍走唯一的 services:start:local 入口。

本轮已完成 8 模型、20 模式的真实服务/mock 验证，以及重启后的无新增请求重放；另在原 Studio 页面提交并播放 1 条 H3 模拟成片。详见 [2026-08-31 验证记录](../artifacts/local-video-lab-validation-20260831.md)。旧 candidate、历史 npm/Go/browser 结果不会自动覆盖新树；生产候选需在本轮源码、测试、文档全部稳定后由发布协调任务另行冻结。
