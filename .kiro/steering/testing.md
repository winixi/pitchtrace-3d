---
inclusion: fileMatch
fileMatchPattern: ["**/live/web/test/**", "**/live/web/src/protocol.js", "**/live/web/src/live-frame.js", "**/live/web/src/match.js", "**/live/web/src/receiver.js", "**/live/web/src/archive.js", "**/live/server/test/**", "**/edge/Tests/**"]
---
# 测试规范

## Live Web（`live/web/test/`，`node --test`）

```sh
cd live/web && npm test
```

覆盖范围：
- 内置比赛全程（t=0..60）每帧都能被 `readScene` 解析（`live-frame.test.js`）。
- 归档回放按比赛时间选帧，保留缺测与迟到帧（`archive.test.js`）。
- 接收器插值、心跳与断线重连行为（`receiver.test.js`）。
- 六机位、骨骼与外形参数（`rig.test.js`）。
- 多语言 key 覆盖（`i18n.test.js`）。

必须保持通过：
- 空场与丢球为空状态（`players: []`、`ball: null`）。
- 未知号码（`number: null`）与预测状态（`state: predicted`）保留语义。
- 非法观测、重复 ID、不支持协议版本被 `readScene` 返回 null。

## Live Server（`live/server/test/gateway.test.mjs`，`node --test`）

```sh
cd live/server && npm test
```

覆盖范围：
- v1 帧合法返回 202，字段错误返回 422，解析失败返回 400。
- v2 场景幂等写入、冲突返回 409、同键同内容幂等确认。
- 分页历史、游标延续、空场返回空数组。
- 比赛隔离：不同 matchId 的订阅互不干扰。
- 重启恢复：关闭再重启网关后，订阅可取回已存的最新帧。
- 媒体三件（init、segment、index）校验与曝光定位。

需要 Node.js 22.13+（`node:sqlite`）。

## Edge（Swift Testing + 集成脚本）

```sh
swift test --package-path edge --disable-xctest
```

Swift 单元测试（`edge/Tests/PitchTraceEdgeTests/`）覆盖：
- 录像时序与缺口（`RecordingTimingTests.swift`、`ReplayMergeTests.swift`）。
- 曝光审计（`ExposureAuditTests.swift`）、可见率审计（`VisibilityAuditTests.swift`）。
- 媒体周期目录结构（`CameraMediaPeriodTests.swift`、`MediaEpochTimingTests.swift`）。
- 原片上行（`MediaPublishingTests.swift`）、单视角观测（`VisionObservationTests.swift`）。

集成脚本（`edge/Tests/Integration/`）：`.mjs` 和 `.py` 脚本测试 Python 工具链各模块，需 Python 3.10+、OpenCV 和 FFmpeg。

新增 Edge Swift 逻辑时优先补 Swift Testing 用例，对照固定录像输出的时间戳、轨迹和媒体索引。

## 通用约定

- 测试不依赖网络与外部服务（Live Web/Server 单元测试），Edge 集成测试使用本地临时目录。
- 模拟数据来自 `live/web/src/match.js` 的 `demoFrame`，前后端共用同一生成器。
- v2 示例快照来自 `schemas/v2/examples/`，直接 import 而非在测试里内联数据。
- 校验逻辑集中在 `protocol.js`（v1）与 `scene-protocol.mjs`（v2），测试直接引用，不在测试里重写校验。
- 新增协议分支或状态时，补一条正例和一条被拒绝的反例。
- 改 `protocol.js`、`live-frame.js`、`match.js`、`receiver.js`、`archive.js` 或数据契约后，提交前务必跑 `cd live/web && npm test` 和 `cd live/server && npm test`。
