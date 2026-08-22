# 根仓库与 new-api 源码管理

`AI-video` 根目录是唯一发布仓库。`backend/new-api-relay` 必须以普通文件（vendored
subtree）进入根仓库；不能只提交一个 Git gitlink，也不能依赖开发机里未提交的嵌套
`.git` 工作区。否则根 CI、候选源码快照和生产镜像都无法复现本项目的 Relay 扩展。

根仓库已于 2026-08-19 建立，`backend/new-api-relay` 现在是普通目录，不再包含嵌套
`.git`，也没有 `.gitmodules` 或 mode `160000` 的 gitlink。项目扩展已经由根仓库提交保存。
首次扁平化之前没有留下可验证的嵌套仓库 bundle，因此不能声称原工作区的本地分支、stash
或 reflog 已归档；官方 upstream 历史应从 `QuantumNous/new-api` 重新取得并单独备份。

后续重新导入或更新 vendored upstream 时必须执行以下可恢复流程：

1. 记录远端 URL、分支和固定 upstream commit；当前固定 revision 也由
   `scripts/relay-migration-acceptance.mjs` 机器校验。
2. 用 `git bundle create` 把嵌套仓库历史保存到受控备份位置，并校验 bundle。
3. 如果导入源带有 `.git`，把它移出工作树；不要直接删除且不要把 bundle 提交进产品仓库。
4. 在根目录初始化/克隆正式远端，把 `backend/new-api-relay` 的所有普通文件和本项目扩展
   一起提交。
5. 推送分支后确认根级 `.github/workflows/ci.yml` 运行；其中会拒绝 mode `160000` 的
   gitlink，并要求 `backend/new-api-relay/go.mod` 是根仓库直接跟踪的普通文件。

历史 bundle 必须保存在产品仓库之外并校验 SHA-256；根 `.gitignore` 同时拒绝 `*.bundle`。
推送前仍需确认根级 `.github/workflows/ci.yml` 会拒绝 gitlink，并要求
`backend/new-api-relay/go.mod` 由根仓库直接跟踪。

## 分支保护必需检查

根仓库推送后，在分支保护中把稳定名称 `Required CI gates` 设为必需状态检查。只有前端、
Platform、new-api Web、Go race、PostgreSQL/Redis integration、构建候选到 Platform 的
跨服务成本验收、Compose/发布合同，以及隔离的历史 Python oracle 回归全部成功，它才会
通过。oracle job 只保护冻结合同，不构建或批准第二个生产数据面。绑定这个聚合检查可以
避免新增可执行门禁后忘记更新分支保护规则。
