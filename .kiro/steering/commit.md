---
inclusion: manual
---
# 提交与分支规范

手动引用：在对话中输入 `#commit` 加载本文件。

## 提交信息

用简体中文，祈使语气，一行说明本次改动。参考现有历史风格：

- 实现 v2 场景快照校验器
- 修复远端高球丢球后未恢复追踪的问题
- 补充空场与未知号码的协议用例

涉及多个子项目或破坏性契约变更时，正文说明影响范围与迁移方式。

## 分支

- `main` 保持可构建、可试播。
- 功能分支用 `feat/`、修复用 `fix/`、契约变更用 `proto/` 前缀，例如 `feat/v2-gateway`、`proto/frame-key-dedup`。

## 构建产物

`live/web/dist/index.html` 与 `live/server/server.cjs` 入库。改了对应源码（`live/web/src/**`、`live/server/server.mjs`）后，先重新构建再提交，避免源码与入库产物不一致。

## 范围

提交前确认改动落在预期子项目内。跨 Edge/Live 的契约变更需更新 `docs/02-technical-design.md` 并用新协议版本过渡，不要静默改字段。
