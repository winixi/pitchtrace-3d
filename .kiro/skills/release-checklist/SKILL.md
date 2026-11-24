---
name: release-checklist
description: 发布或联调前构建并核对全部子项目产物。当要求发布、发版、构建全部、联调前检查或核对构建产物一致性时使用。
metadata:
  author: pitchtrace
  version: "1.0"
---

# 发布前构建核对清单

场迹有三个独立构建产物。发布或联调前按顺序构建并核对，确保源码与入库产物一致。

## 1. Live Web（浏览器前端）

```sh
cd live/web
npm ci
npm test          # 必须全绿
npm run build      # 重新生成 dist/index.html
```

确认 `live/web/dist/index.html` 已更新且可离线打开。

## 2. Live Server（试播网关）

```sh
cd live/server
npm ci
npm run build      # 重新生成 server.cjs
node server.cjs    # 启动后访问 http://127.0.0.1:8766/
```

确认能打开页面、`/demo` 有数据、`POST /frame` 返回 202。

## 3. Edge（边缘计算）

```sh
swift build --package-path edge
swift run  --package-path edge pitchtrace-edge
```

当前为空入口骨架，确认可构建、可运行即可。

## 4. 提交

- 入库的构建产物：`live/web/dist/index.html`、`live/server/server.cjs`。
- 提交信息用简体中文，说明本次改动范围。
- 跨 Edge/Live 的契约变更同步更新 `docs/02-technical-design.md`，并用新协议版本过渡。

## 5. 核对

- 阅读 [AGENTS.md](../../../AGENTS.md) 实现状态表，确认本次发布不依赖设计中（⏳）的能力。
- 改了协议的发布必须附带 `npm test` 全绿记录。
- 若任一子项目构建失败，先修复再发布；不要只发布部分产物。
