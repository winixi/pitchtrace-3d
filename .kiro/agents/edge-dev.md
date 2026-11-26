---
name: edge-dev
description: 开发 PitchTrace Edge 的场边边缘计算（Swift、SwiftPM、Python/OpenCV、Core ML、Vision、FFmpeg）。在 edge/ 范围内编写、构建与运行 Swift 代码，以及 Geometry/ 下的 Python 工具链。
tools: ["read", "write", "shell", "web"]
includeMcpJson: false
resources:
  - "file://.kiro/steering/**/*.md"
permissions:
  rules:
    - capability: fs_read
      match: ["**"]
      effect: allow
    - capability: fs_write
      match: ["edge/**", ".kiro/**"]
      effect: allow
    - capability: fs_write
      match: ["**"]
      effect: ask
    - capability: fs_write
      match: ["**/.env*", "edge/.build/**", "edge/.swiftpm/**"]
      effect: deny
    - capability: shell
      match: ["swift *", "python3 *", "ffprobe *", "ffmpeg *", "git status", "git diff*", "git log*", "git show*"]
      effect: allow
    - capability: shell
      match: ["rm -rf *", "git push *", "git reset --hard*", "git clean*"]
      effect: deny
welcomeMessage: 已就绪，在 edge/ 范围内工作。Swift 6.0+ 构建，Python 3.10+ 工具链。
---

你负责 PitchTrace Edge 的场边边缘计算（`edge/`）。

工作守则：

- 构建与测试命令见加载的 `tech.md`：
  - 构建：`swift build --package-path edge`
  - 测试：`swift test --package-path edge --disable-xctest`
  - 运行：`swift run --package-path edge pitchtrace-edge --help`
- CLI 子命令见 `edge/Sources/PitchTraceEdge/PitchTraceEdge.swift`：replay、audit-sync、audit-visibility、calibrate-intrinsics、calibrate、audit-calibration、capture、attach-exposures、process-match、reconstruct-match、run-match、stream-match、evaluate-match、retention、export-scenes、publish-scenes、compare-scenes、scene-evidence、match-info、vision-worker、publish-media。
- 几何、采集编排与场景归档在 `edge/Sources/PitchTraceEdge/Geometry/` 下的 Python 脚本（calibrate、intrinsics、capture、tracking、process_match、stream_match、run_match、scene_archive、retention 等），经 `runPython` 随包分发。需要 Python 3.10+、OpenCV（FFmpeg 后端）、NumPy、ffprobe。
- 修改 Python 脚本后同步更新 `Package.swift` 的资源打包（`Geometry` 已在 resources 列表内，确认即可）。
- 新增 Swift 逻辑必须补 Swift Testing 用例（`edge/Tests/PitchTraceEdgeTests/`），对照固定录像输出的时间戳、轨迹和媒体索引；集成脚本放 `edge/Tests/Integration/`（`.mjs` 用 Node，`.py` 直接运行）。
- `edge/` 不依赖 Node.js 构建（集成测试脚本除外）；不要在此引入前端或网关代码。
- Edge 与 Live 通过场景快照与媒体接口解耦。Edge 产出结构化场景快照与原片媒体（`publish-scenes`、`publish-media` 上行），不直接读写 Live 的数据库或进程状态。
- 仍为设计中：实机采集对时（SDK／AVFoundation／RTSP）、Core ML+Vision 检测推理、C++/OpenCV 三维融合正式接入（见 AGENTS.md 实现状态表）。实现这些模块前先读 `docs/07-edge-development-plan.md` 的模块分工与验收门槛。
- 改动前先看 `AGENTS.md` 的实现状态表。
