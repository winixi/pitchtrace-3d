---
inclusion: always
---
# 仓库结构与边界

```
pitchtrace-3d/
├── docs/                        需求、技术方案、开发计划（01–07）
├── schemas/
│   ├── v1/examples/             v1 协议示例（demo-frame.json）
│   └── v2/
│       ├── scene-frame.schema.json   v2 场景快照 JSON Schema（网关校验源）
│       ├── media-index.schema.json   v2 媒体索引 JSON Schema
│       └── examples/            empty-pitch / lost-ball / unknown-number /
│                                clock-reset / invalid/duplicate-track 等样例
├── edge/                        PitchTrace Edge · 场边边缘计算（Swift/SwiftPM）
│   ├── Package.swift            SwiftPM 配置（macOS 14+，Swift 6.0+）
│   ├── Sources/PitchTraceEdge/
│   │   ├── PitchTraceEdge.swift        CLI 总入口，分发所有子命令
│   │   ├── RecordingExport.swift       2–8 机位录像重放与 fMP4 导出
│   │   ├── RecordingTiming.swift       曝光映射、时序与缺口校验
│   │   ├── CameraMediaPeriod.swift     机位媒体周期目录结构
│   │   ├── ExposureAudit.swift         闪光对时报告
│   │   ├── VisibilityAudit.swift       分区双视角可见率
│   │   ├── MediaPublishing.swift       publish-media 原片上行
│   │   ├── VisionObservation.swift     vision-worker 单视角占位
│   │   ├── AuditFrameLookup.swift      曝光帧查找（审计共用）
│   │   └── Geometry/                  Python 几何/采集/归档脚本（随包资源）
│   │       ├── calibrate.py / intrinsics.py / audit_calibration.py
│   │       ├── capture.py / stream_capture.py
│   │       ├─�� tracking.py / pose.py / observations.py
│   │       ├── process_match.py / stream_match.py / run_match.py
│   │       ├── scene_archive.py / scene_compare.py / evaluate_match.py
│   │       └── …（retention、match_config、match_events、match_analysis 等）
│   ├── Tests/
│   │   ├── PitchTraceEdgeTests/        Swift Testing 单元测试（录像、曝光、媒体、可见性等）
│   │   └── Integration/                集成测试脚本（.mjs 与 .py）
│   ├── samples/                 样例录像与四路 30 分钟基准数据
│   ├── benchmarks/              实测耗时与内存数据
│   └── *.md                     专题说明（capture / run-match / stream-match /
│                                vision / tracking / analysis / evaluation /
│                                calibration-audit / match-config / scene-archive）
└── live/                        PitchTrace Live · 直播与观赛
    ├── web/                     浏览器前端（Vite + Three.js + hls.js）
    │   ├── src/                 app / match / receiver / live-frame / pitch / rig /
    │   │                        media / protocol / archive / archive-panel /
    │   │                        review / recording / showcase / appearance /
    │   │                        anatomy / i18n
    │   ├── test/                node:test 用例（live-frame / archive / receiver /
    │   │                        rig / i18n）
    │   └── dist/index.html      构建产物（单文件，可离线打开）
    └── server/                  本地试播网关（v1 + v2）
        ├── server.mjs           HTTP/WS 路由总入口
        ├── scene-archive.mjs    v2 场景 SQLite 归档
        ├── scene-protocol.mjs   v2 场景校验（Ajv + schema）
        ├── history-query.mjs    分页历史查询
        ├── media-archive.mjs    v2 原片 SQLite 归档
        ├── media-http.mjs       v2 原片上传、播放列表与曝光定位
        ├── media-index.mjs      媒体索引解析与校验
        ├── build.mjs            esbuild 打包脚本
        ├── server.cjs           打包产物（Node.js 可直接运行）
        ├── test/gateway.test.mjs
        └── tools/review-sample.mjs   原片复核样例生成工具
```

完整目录与各子项目说明见 [AGENTS.md](../../AGENTS.md) 与子项目 README。

## 边界守则

- Edge 与 Live 各自维护 SQLite，通过协议交互，不共享进程内存状态。
- v2 场景字段与媒体路径是共同版本化的契约。破坏性变更用新协议版本过渡，并在 [docs/02-technical-design.md](../../docs/02-technical-design.md) 记录。
- v2 场景快照的校验源头是 `schemas/v2/scene-frame.schema.json`，网关经 `scene-protocol.mjs` 读取；v1 校验源头是 `live/web/src/protocol.js`，网关直接 import。改协议时前端与网关都要更新并通过测试。
- `live/server/server.mjs` 的核心功能按模块拆分。改 `scene-archive.mjs`、`media-http.mjs`、`scene-protocol.mjs` 等任何服务端模块后都需运行 `npm run build` 重新打包 `server.cjs`。

## 关键文件

- `live/web/src/protocol.js` — v1 校验器与 `validBones`，前后端共享的事实来源。
- `live/web/src/live-frame.js` — v2 快照到渲染输入（`readScene`）的转换。
- `live/web/src/match.js` — 内置 60 秒比赛生成器（`demoFrame`、`chapters`），前后端共用。
- `live/web/src/receiver.js` — WebSocket 接收与插值。
- `live/web/src/archive.js` — `fetchHistory` 与 `archiveFrameAt`，驱动归档回放。
- `live/web/src/review.js` — 原片复核面板，调用 `media.js` 的 HLS 播放器。
- `live/server/server.mjs` — 路由总入口；`server.cjs` 为打包产物。
- `live/server/scene-protocol.mjs` — v2 场景 `validSceneFrame`、`sceneRoute`、`serializeSceneFrame`。
- `schemas/v2/scene-frame.schema.json` — v2 场景写入契约，网关校验直接使用。
- `schemas/v2/media-index.schema.json` — v2 媒体索引契约。
- `edge/Sources/PitchTraceEdge/PitchTraceEdge.swift` — CLI 分发所有 Edge 子命令。
- `edge/Sources/PitchTraceEdge/MediaPublishing.swift` — `publish-media` 上行逻辑。
