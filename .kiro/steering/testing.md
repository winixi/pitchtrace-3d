---
inclusion: fileMatch
fileMatchPattern: ["**/live/web/test/**", "**/live/web/src/protocol.js", "**/live/web/src/live-frame.js", "**/live/web/src/match.js", "**/live/web/src/receiver.js"]
---
# 测试规范

Live Web 用 Node 内置 `node --test`，无额外测试框架。用例在 `live/web/test/`。

## 运行

```sh
cd live/web && npm test
```

## 必须覆盖

- 内置比赛全程（t=0..60）每帧都能被 `readScene` 解析。
- 空场与丢球保留为空状态（`players: []`、`ball: null`）。
- 未知号码（`number: null`）与预测测量（`state: predicted`）保留语义。
- 非法观测、重复 ID、不支持的协议版本被拒绝（`readScene` 返回 null）。

## 何时跑测试

改 `protocol.js`、`live-frame.js`、`match.js`、`receiver.js` 或数据契约后，提交前务必跑 `npm test`。Edge 目前无测试；新增 Edge 逻辑时优先补 XCTest，对照固定录像输出的时间戳、轨迹和媒体索引。

## 约定

- 测试不依赖网络与外部服务。
- 模拟数据来自 `match.js` 的 `demoFrame`，前后端共用同一生成器。
- 校验逻辑集中在 `protocol.js`，测试直接引用，避免在测试里重写校验。
- 新增协议分支或状态时，补一条正例和一条被拒绝的反例。
