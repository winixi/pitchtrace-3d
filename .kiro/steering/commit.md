---
inclusion: manual
---
# 提交与分支规范

手动引用：在对话中输入 `#commit` 加载本文件。

## 提交信息

格式：`<scope>: <简体中文描述>`（祈使语气，说明本次改动）。

常用 scope：
- `feat(edge)` / `fix(edge)` — Edge CLI、Swift 模块或 Python 工具链
- `feat(live)` / `fix(live)` — 前端或网关（不细分时用 `live`）
- `feat(live/web)` / `fix(live/web)` — 仅前端
- `feat(live/server)` / `fix(live/server)` — 仅网关
- `feat(proto)` / `fix(proto)` — 协议或 schema 变更
- `docs` — 文档
- `chore` — 构建脚本、CI 或工程配置

示例（参考现有历史风格）：
```
feat(edge): 新增分区双视角可见率审计
fix(live/server): 修复媒体三件校验跨周期冲突
feat(proto): v2 场景快照添加 captureUtcUs 字段
docs: 更新 edge/README 联调步骤
```

涉及多个子项目或破坏性契约变更时，正文说明影响范围与迁移方式。

## 分支

- `main` 保持可构建、可试播。
- 功能分支用 `feat/`、修复用 `fix/`、契约变更用 `proto/` 前缀，例如 `feat/edge-vision-worker`、`proto/media-index-v2`。

## 构建产物

`live/web/dist/index.html` 与 `live/server/server.cjs` 入库。改了任何源码（`live/web/src/**`、`live/server/*.mjs`）后，先重新构建再提交，避免源码与入库产物不一致：

```sh
# 前端变更
cd live/web && npm run build

# 网关变更
cd live/server && npm run build
```

## 范围

提交前确认改动落在预期子项目内。跨 Edge/Live 的契约变更需更新 `docs/02-technical-design.md` 与 `schemas/v2/`，并用新协议版本过渡，不要静默改字段。Edge 的 Python 脚本（`edge/Sources/PitchTraceEdge/Geometry/`）属于 Edge 侧，改后无需重建 Live 产物。
