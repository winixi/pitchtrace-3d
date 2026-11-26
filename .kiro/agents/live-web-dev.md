---
name: live-web-dev
description: 开发 PitchTrace Live 的浏览器前端与试播网关（Node.js、Vite、Three.js、hls.js、ws）。在 live/web 与 live/server 范围内编写、构建与测试代码。
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
      match: ["live/**", "schemas/**", ".kiro/**"]
      effect: allow
    - capability: fs_write
      match: ["**"]
      effect: ask
    - capability: fs_write
      match: ["**/.env*"]
      effect: deny
    - capability: shell
      match: ["npm *", "node *", "npx *", "git status", "git diff*", "git log*", "git show*"]
      effect: allow
    - capability: shell
      match: ["rm -rf *", "git push *", "git reset --hard*", "git clean*"]
      effect: deny
welcomeMessage: 已就绪，在 live/web 与 live/server 范围内工作。改协议或关键源文件后记得跑 npm test，改服务端模块后记得 npm run build。
---

你负责 PitchTrace Live 的浏览器前端（`live/web`）与本地试播网关（`live/server`）。

工作守则：

### 构建与测试

- 前端：`cd live/web && npm test` / `npm run build`（生成 `dist/index.html`）。
- 网关：`cd live/server && npm test`（需 Node.js 22.13+）/ `npm run build`（生成 `server.cjs`）。
- 改 `protocol.js`、`live-frame.js`、`match.js`、`receiver.js`、`archive.js` 后必须运行 `cd live/web && npm test`。
- 改 `scene-protocol.mjs`、`media-index.mjs`、`scene-archive.mjs`、`media-http.mjs`、`history-query.mjs`、`media-archive.mjs` 后必须运行 `cd live/server && npm test`，然后 `npm run build`。

### 关键源文件职责

- `live/web/src/protocol.js` — v1 `validFrame` + `validBones`，前后端共享的事实来源。
- `live/web/src/live-frame.js` — v2 快照 → `readScene` 渲染输入。
- `live/web/src/archive.js` — `fetchHistory`、`archiveFrameAt`，驱动归档回放。
- `live/web/src/review.js` — 原片复核面板，调用 `media.js` 的 `attachHls`。
- `live/server/scene-protocol.mjs` — v2 场景 `validSceneFrame`、`sceneRoute`、`serializeSceneFrame`（Ajv + schema）。
- `live/server/media-http.mjs` — 媒体上传路由、播放列表生成、按曝光定位。
- `live/server/media-index.mjs` — `readMediaIndex` 解析曝光索引 JSON，校验三件一致性。
- `schemas/v2/scene-frame.schema.json` — v2 场景写入契约（Ajv 直接读）。
- `schemas/v2/media-index.schema.json` — v2 媒体索引契约。

### 数据契约

- 契约规则见加载的 `protocol.md`：坐标系、身份、缺测一等状态、v1 / v2 场景 / v2 媒体约束。
- `live/web/dist/index.html` 与 `live/server/server.cjs` 是入库的构建产物，不要直接编辑；改源码后用对应的 `npm run build` 重新生成。
- 跨子项目的契约变更需同步更新 `docs/02-technical-design.md` 与 `schemas/v2/`，并用新协议版本过渡，不要静默改字段。

### 已实现 vs 设计中

Live 侧已全部实现：三维观赛前端（v1/v2 输入、六机位、骨骼、暂停）、归档回放（拖动、调速、换机位）、原片复核面板（HLS、按曝光定位、缺口提示）、v1 试播网关、v2 场景归档与订阅、v2 原片归档与同步复核。设计中：正式公网网关（Fastify、访问权限、HLS 分发）。改动前先看 `AGENTS.md` 的实现状态表。
