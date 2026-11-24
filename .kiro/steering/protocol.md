---
inclusion: fileMatch
fileMatchPattern: ["**/live/web/src/protocol.js", "**/live/web/src/live-frame.js", "**/live/server/server.mjs", "**/docs/02-technical-design.md"]
---
# 数据契约规范

改动场景快照或网关转发前必读。完整契约见 [docs/02-technical-design.md](../../docs/02-technical-design.md) 第 1.3 与第 6 节。

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
- 校验在 `live/web/src/protocol.js` 的 `validFrame`，网关复用同一函数。
- 端点返回：合法 202，字段错误 422，无法解析的 JSON 400。

## v2（设计中，前端已支持输入）

- 帧键为 `(matchId, streamEpoch, seq)`。同键同内容幂等确认，同键异内容返回 409。
- 一帧可含零个球员；球、号码、关节可缺失，承载测量时间与状态。
- 路径以 `/v2/matches/{matchId}` 为前缀；媒体用各通道 `mediaEpoch`。
- 只有号码、球等字段均已知且满足 v1 约束的帧才能通过兼容适配器发到 v1。

## 改动检查

改 `protocol.js` 或 `live-frame.js` 后必须运行 `cd live/web && npm test`，确认内置比赛全程、空场丢球、未知号码与非法帧拒绝用例通过。
