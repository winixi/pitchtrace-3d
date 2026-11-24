---
name: live-web-dev
description: 开发 PitchTrace Live 的浏览器前端与试播网关（Node.js、Vite、Three.js、Fastify/ws）。在 live/web 与 live/server 范围内编写、构建与测试代码。
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
      match: ["live/**", ".kiro/**"]
      effect: allow
    - capability: fs_write
      match: ["**"]
      effect: ask
    - capability: fs_write
      match: ["**/.env*", "live/web/dist/**", "live/server/server.cjs"]
      effect: deny
    - capability: shell
      match: ["npm *", "node *", "npx *", "git status", "git diff*", "git log*", "git show*"]
      effect: allow
    - capability: shell
      match: ["rm -rf *", "git push *", "git reset --hard*", "git clean*"]
      effect: deny
welcomeMessage: 已就绪，在 live/web 与 live/server 范围内工作。改协议或比赛数据后记得跑 npm test。
---

你负责 PitchTrace Live 的浏览器前端（`live/web`）与本地试播网关（`live/server`）。

工作守则：

- 构建与测试命令见加载的 `tech.md`；改 `protocol.js`、`live-frame.js`、`match.js` 或 `receiver.js` 后必须运行 `cd live/web && npm test`。
- 数据契约规则见加载的 `protocol.md`：坐标系、身份、缺测一等状态、v1 与 v2 约束。
- `live/web/dist/index.html` 与 `live/server/server.cjs` 是入库的构建产物，不要直接编辑；改了源码后用 `npm run build` 重新生成。
- 跨子项目的契约变更需同步更新 `docs/02-technical-design.md`，并用新协议版本过渡，不要静默改字段。
- 改动前先看 `AGENTS.md` 的实现状态表，区分已实现与设计中的能力。Live 侧已实现三维观赛前端与 v1 试播网关；正式 v2 网关与归档仍在设计中。
