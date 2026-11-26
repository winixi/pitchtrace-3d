---
inclusion: always
---
# 技术栈与构建命令

需要 Swift 6.0+、Node.js 20.19+ 或 22.12+。v2 归档与媒体复核需要 Node.js 22.13+（内置 `node:sqlite`）。命令从仓库根目录运行。完整说明见各子项目 README 与 [AGENTS.md](../../AGENTS.md)。

## PitchTrace Edge（`edge/`，Swift / SwiftPM + Python 工具链）

```sh
swift build --package-path edge
swift test  --package-path edge --disable-xctest   # Swift Testing 单元与集成测试
swift run  --package-path edge pitchtrace-edge --help
```

- CLI 子命令覆盖：`replay`（录像重放）、`audit-sync`、`audit-visibility`、`calibrate-intrinsics`、`calibrate`、`audit-calibration`、`capture`／`attach-exposures`、`process-match`／`reconstruct-match`／`run-match`／`stream-match`、`evaluate-match`、`retention`、`export-scenes`／`publish-scenes`／`compare-scenes`／`scene-evidence`／`match-info`、`vision-worker`、`publish-media`。入口见 `edge/Sources/PitchTraceEdge/PitchTraceEdge.swift`。
- 几何、采集编排与场景归档由随包分发的 Python 脚本实现（`edge/Sources/PitchTraceEdge/Geometry/`），经 `runPython` 调用，需 Python 3.10+、OpenCV（FFmpeg 后端）、NumPy、ffprobe。
- 完整用法与样例生成见 `edge/README.md` 及 `edge/*.md` 各专题文档（capture、run-match、stream-match、scene-archive、vision、tracking、analysis、evaluation、calibration-audit、match-config）。
- 仍为设计中：厂商 SDK／AVFoundation／RTSP 的实机采集对时、Core ML+Vision 单视角检测、C++/OpenCV 三维融合的正式接入（见 AGENTS.md 实现状态表）。

## PitchTrace Live · 浏览器前端（`live/web/`，Vite + Three.js + hls.js）

```sh
cd live/web
npm ci
npm run dev       # 开发服务器
npm test          # node --test，覆盖内置比赛、协议、归档回放、接收器、i18n 与机位
npm run build     # 生成单文件 dist/index.html
```

## PitchTrace Live · 本地试播网关（`live/server/`，Node.js，v1 + v2）

```sh
cd live/server
npm ci
npm test          # v2 归档、比赛隔离、恢复、媒体三件校验与 v1 回归
npm run build     # esbuild 把 server.mjs 打包成 server.cjs
node server.cjs   # 监听 127.0.0.1:8766
```

- v1：`POST /frame`、`WS /live`、`WS /demo`，复用前端校验器。
- v2 场景：`POST/GET /v2/matches/{matchId}/frames`、`WS /v2/matches/{matchId}/live`，SQLite 归档（`scene-archive.mjs`）、分页历史（`history-query.mjs`）。
- v2 原片：`PUT/GET /v2/matches/{matchId}/cameras/...` 媒体三件（init/segment/index）、播放列表与按曝光定位（`media-http.mjs`、`media-archive.mjs`、`media-index.mjs`）。
- 环境变量：`PITCHTRACE_HOST`、`PITCHTRACE_PORT`、`PITCHTRACE_DB`、`PITCHTRACE_MEDIA_DIR`。
- 从仓库根目录直接试播（需先构建前端）：`node live/server/server.cjs`。
- 源码按模块拆分（`server.mjs` 之外另有 6 个 `.mjs`），改任何模块后都要 `npm run build` 重新打包。

## 构建产物入库

`live/web/dist/index.html` 与 `live/server/server.cjs` 由构建生成并入库，便于离线试播。改了对应源码后必须重新构建这两者再提交，避免源码与入库产物不一致。
