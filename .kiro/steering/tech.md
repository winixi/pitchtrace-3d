---
inclusion: always
---
# 技术栈与构建命令

需要 Swift 6.0+、Node.js 20.19+ 或 22.12+。命令从仓库根目录运行。完整说明见各子项目 README 与 [AGENTS.md](../../AGENTS.md)。

## PitchTrace Edge（`edge/`，Swift / SwiftPM）

```sh
swift build --package-path edge
swift run  --package-path edge pitchtrace-edge   # 当前空入口，运行后立即退出
```

当前为可构建骨架，采集、推理、融合、上发均未实现。

## PitchTrace Live · 浏览器前端（`live/web/`，Vite + Three.js）

```sh
cd live/web
npm ci
npm run dev       # 开发服务器
npm test          # node --test，检查内置比赛与数据协议
npm run build     # 生成单文件 dist/index.html
```

## PitchTrace Live · 本地试播网关（`live/server/`，Node.js，v1）

```sh
cd live/server
npm ci
npm run build     # esbuild 把 server.mjs 打包成 server.cjs
node server.cjs   # 监听 127.0.0.1:8766
```

从根目录直接试播（需先构建前端）：`node live/server/server.cjs`

## 构建产物入库

`live/web/dist/index.html` 与 `live/server/server.cjs` 由构建生成并入库，便于离线试播。改了对应源码后必须重新构建这两者再提交，避免源码与入库产物不一致。
