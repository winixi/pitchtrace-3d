---
inclusion: always
---
# 仓库结构与边界

```
pitchtrace-3d/
├── docs/                      需求、技术方案、开发计划
├── edge/                      PitchTrace Edge · 场边边缘计算（Swift/SwiftPM）
│   ├── Package.swift
│   └── Sources/PitchTraceEdge/
└── live/                      PitchTrace Live · 直播与观赛
    ├── web/                   浏览器前端（Vite + Three.js）
    │   ├── src/               app/match/receiver/live-frame/pitch/rig/media/protocol
    │   ├── test/              node:test 用例
    │   └── dist/index.html    构建产物（可离线打开）
    └── server/                本地试播网关（v1）
        ├── server.mjs         源码
        └── server.cjs         打包产物
```

完整目录与各子项目说明见 [AGENTS.md](../../AGENTS.md) 与子项目 README。

## 边界守则

- Edge 与 Live 各自维护 SQLite，通过协议交互，不共享进程内存状态。
- 场景字段与媒体路径是共同版本化的契约。破坏性变更用新协议版本过渡，并在 [docs/02-technical-design.md](../../docs/02-technical-design.md) 记录。
- 协议校验的事实来源是 `live/web/src/protocol.js` 的 `validFrame`，网关在 `live/server/server.mjs` 复用同一函数。改协议时前端与网关都要更新并通过测试。

## 关键文件

- `live/web/src/protocol.js` — v1 校验器，前后端共享的事实来源。
- `live/web/src/live-frame.js` — v2 快照到渲染输入的转换。
- `live/web/src/match.js` — 内置 60 秒比赛生成器，前后端共用。
- `live/web/src/receiver.js` — WebSocket 接收与插值。
- `live/server/server.mjs` — 网关源码。
