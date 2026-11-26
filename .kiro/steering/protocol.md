---
inclusion: fileMatch
fileMatchPattern: ["**/live/web/src/protocol.js", "**/live/web/src/live-frame.js", "**/live/server/server.mjs", "**/live/server/scene-protocol.mjs", "**/live/server/media-index.mjs", "**/docs/02-technical-design.md", "**/schemas/v2/**"]
---
# 数据契约规范

改动场景快照、媒体索引或网关转发前必读。完整契约见 [docs/02-technical-design.md](../../docs/02-technical-design.md) 第 1.3 与第 6 节。

## 坐标系

- 中圈地面为原点，X 沿长边、Z 沿短边、Y 向上，单位米。
- 球员用足底与地面交点定位，足球用球心高度（地滚球约 0.11 m）。
- 朝向 `yaw` 单位弧度，0 朝 +Z，正角转向 +X。
- 关节旋转用绑定骨架局部四元数 `[x,y,z,w]`，归一化。骨骼名见 `live/web/src/protocol.js` 的 `boneSet`。

## 身份

- 轨迹 ID、名单 ID、球衣号分别存储，两队同号按球队区分。队伍枚举为 `blue`、`red`、`gold`、`green`。
- 更正号码后历史轨迹沿用原轨迹 ID。
- 球衣号整数 0–99。v1 要求每名球员都有号码；v2 允许 `number: null`。

## 缺测是一等状态

- 未识别号码、球出视野、单视角高球、脚部被挡都需保留对应状态。
- v2 用 `state: measured | predicted | lost` 区分，并记录观测时间。
- 禁止用 0 号或球场原点填补未知观测。

## v1（已实现）

- 协议版本 `v` 固定为 1，时间 `t` 以秒计。
- 每帧 1–32 名球员，每人有整数球衣号，每帧都有球坐标，骨骼可选。
- 校验在 `live/web/src/protocol.js` 的 `validFrame`，网关直接 import 复用。
- 端点返回：合法 202，字段错误 422，无法解析的 JSON 400。

## v2 场景（已实现）

- JSON Schema 源：`schemas/v2/scene-frame.schema.json`，网关经 `live/server/scene-protocol.mjs` 读取。
- 帧键为 `(matchId, streamEpoch, seq)`。同键同内容幂等确认，同键异内容返回 409。
- 一帧可含零个球员；球、号码、关节可缺失，承载测量时间与状态（`measured | predicted | lost`）。
- `serializeSceneFrame` 对对象字段排序后序列化，保证幂等比较不受字段顺序影响。
- 路径前缀：`/v2/matches/{matchId}`（frames 端点与 live WebSocket）。
- 只有号码、球等字段均已知且满足 v1 约束的帧才能通过兼容适配器发到 v1。

## v2 媒体（已实现）

- JSON Schema 源：`schemas/v2/media-index.schema.json`，网关经 `live/server/media-index.mjs` 读取。
- 路径前缀：`/v2/matches/{matchId}/cameras/{cameraId}/epochs/{mediaEpoch}`。
- 三件：`init.mp4`、`segments/{n}.m4s`、`segments/{n}/index`（JSON 曝光索引）。
- 二进制上传需 `X-Content-SHA256` 头（64 位小写十六进制）。三件齐备且校验匹配后对浏览器可见。
- 同路径重传相同内容返回 202；内容或交叉校验冲突返回 409。
- Edge 侧结构：`edge/Sources/PitchTraceEdge/MediaPublishing.swift`，需 `initSha256`、`segmentSha256`、`segmentBytes` 与本地字节一致。

## 改动检查

- 改 `protocol.js` 或 `live-frame.js` 后：`cd live/web && npm test`。
- 改 `scene-protocol.mjs`、`media-index.mjs` 或对应 schema 后：`cd live/server && npm test`。
- 改任何服务端模块（`scene-archive.mjs`、`media-http.mjs`、`history-query.mjs` 等）后：`npm run build`。
- 破坏性契约变更需在 `docs/02-technical-design.md` 记录新版本号，并保持旧版兼容或过渡。
