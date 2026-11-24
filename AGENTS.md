# AGENTS.md

本文件供在仓库中工作的 AI 编程代理阅读，给出项目范围、目录布局、可运行的构建测试命令、架构边界和数据约定。改代码前请先读对应子项目的 README 和 `docs/`。文档均用简体中文编写，提交信息、注释和新增文档请保持同一语言；根 `README.md` 例外，采用英文默认、中文（`README.zh-CN.md`）可切换的双语形式。

## 项目概览

场迹（PitchTrace 3D）是一个面向足球比赛的三维跟踪与回放系统。球场四周架设四组双目摄像头，在 Mac mini 边缘节点上从八路画面重建球员、足球和骨骼动作，再经直播服务分发给浏览器，让观众自由切换视角回看传球、射门和拼抢。整体设计、功能需求和验收门槛见 `docs/`，实现状态见下文「实现状态」。

仓库规划两个独立构建、测试和发布的产品：

- **PitchTrace Edge**（`edge/`）—— 场边采集、三维重建与媒体片段生产。Swift / SwiftPM。
- **PitchTrace Live**（`live/`）—— 接收、归档、分发数据并提供三维观赛与复核。Node.js / TypeScript、Fastify、WebSocket、Vite、Three.js。

两端通过场景快照与媒体接口解耦，可分别用固定样例独立开发。

## 仓库结构

```
pitchtrace-3d/
├── README.md                  项目入口与本地试播（英文默认，README.zh-CN.md 为中文版）
├── README.zh-CN.md            根 README 中文版，与 README.md 一一对应
├── AGENTS.md                  本文件
├── LICENSE                    MIT
├── docs/
│   ├── 01-requirements.md     详细需求说明（R01–R12 与验收口径）
│   ├── 02-technical-design.md  技术实现方案（模块、对时、契约）
│   └── 03-development-plan.md 落地开发计划（排期、放行条件）
├── edge/                      PitchTrace Edge（SwiftPM）
│   ├── Package.swift
│   └── Sources/PitchTraceEdge/PitchTraceEdge.swift
└── live/                      PitchTrace Live
    ├── web/                   浏览器观赛前端（Vite + Three.js）
    │   ├── src/               app / match / receiver / live-frame / pitch / rig / media / protocol
    │   ├── test/              内置比赛与协议的 node:test 用例
    │   ├── assets/            .glb 模型
    │   ├── licenses/          第三方许可与署名
    │   ├── dist/index.html    构建产物（单文件，可离线打开）
    │   └── package.json
    └── server/                本地试播网关（v1）
        ├── server.mjs         源码
        ├── server.cjs         esbuild 打包产物，由 npm run build 生成
        ├── build.mjs           打包脚本
        └── package.json
```

## 构建与测试命令

下列命令均已在仓库中验证。工作目录默认为仓库根目录。需要的工具：Swift 6.0+、Node.js 20.19+ 或 22.12+。

### Edge（Swift）

```sh
swift build --package-path edge
swift run  --package-path edge pitchtrace-edge   # 当前为空入口，运行后立即退出
```

### Live Web（浏览器前端）

```sh
cd live/web
npm ci
npm run dev       # 本地开发服务器
npm test          # node --test，检查内置比赛与 v1/v2 数据协议
npm run build     # 生成单文件 dist/index.html
```

### Live Server（本地试播网关）

```sh
cd live/server
npm ci
npm run build     # 用 esbuild 把 server.mjs 打包成 server.cjs
node server.cjs   # 监听 127.0.0.1:8766
```

从仓库根目录直接试播：

```sh
node live/server/server.cjs   # 需先在 live/web 执行过 npm run build
```

启动后用浏览器打开 <http://127.0.0.1:8766/>。环境变量 `PITCHTRACE_HOST`、`PITCHTRACE_PORT` 可改监听地址。

## 架构与边界

`docs/02-technical-design.md` 给出完整模块表，要点如下。

- **Edge**：采集与对时 → 分段录制与逐帧时间索引 → 单视角检测、姿态、读号 → 跨机位关联、三维融合与动作候选 → 经 HTTPS 上发场景快照与媒体。采集、写盘、推理、上发各有独立有界队列；网关宕机时保留采集，重连先发当前快照再补历史。
- **Live**：写入并校验快照（先归档再推 WebSocket）→ 三维直播订阅 → HLS 实拍与回看 → 观赛与复核页面。订阅按比赛隔离，慢客户端跳到最新快照，回看按比赛时刻从归档补帧。
- **解耦**：两类输出——结构化场景快照与原片媒体，分别经 WebSocket 与 HTTP/HLS 交付。两端各自维护 SQLite，即使同机试点也通过协议交互，不共享进程内存状态。

改一边时不要越界改另一边的数据格式：场景字段和媒体路径属于共同版本化的契约（计划放 `schemas/v2/`），破坏性变更用新协议版本过渡，并在 `docs/02-technical-design.md` 记录。

## 数据契约

### v1（已实现）

`POST /frame` 接收完整 JSON 快照，`/live` WebSocket 向浏览器转发，`/demo` 产出约 20 Hz 模拟数据。前端做约 100 ms 插值。校验逻辑在 `live/web/src/protocol.js` 的 `validFrame`，网关在 `live/server/server.mjs` 复用同一函数。字段约定和 curl 示例见 `live/server/README.md`。

- 协议版本 `v` 固定为 1，时间 `t` 以秒计。每帧 1–32 名球员，每人有整数球衣号，每帧都有球坐标；骨骼可选。无法表示空场、丢球或未知号码——这正是 v2 要解决的。
- 端点对合法帧返回 202，字段错误返回 422，无法解析的 JSON 返回 400。

### v2（设计中）

`POST /v2/matches/{matchId}/frames` 提交，`WS /v2/matches/{matchId}/live` 订阅。一帧可含零个球员，球、号码、关节可缺失，承载测量时间与状态。帧键为 `(matchId, streamEpoch, seq)`，同键同内容幂等确认，同键异内容返回 409。前端已支持 v2 快照输入（见 `live/web/src/live-frame.js`），v2 网关仍未实现。完整字段、示例和媒体路径见 `docs/02-technical-design.md` 第 1.3 与第 6 节。

## 约定

- **坐标系**：中圈地面为原点，X 沿长边、Z 沿短边、Y 向上，单位米。球员用足底与地面交点定位，足球用球心高度。朝向 `yaw` 单位弧度，0 朝 +Z，正角转向 +X。关节旋转用绑定骨架局部四元数 `[x,y,z,w]`。
- **身份**：轨迹 ID、名单 ID、球衣号分别存储，两队同号按球队区分。更正号码后历史轨迹沿用原轨迹 ID。
- **缺测是一等状态**：未识别号码、球出视野、单视角高球、脚部被挡都需保留对应状态，不得用 0 号或原点填补未知观测。
- **测试**：Live Web 用 Node 内置 `node --test`（无额外框架），用例覆盖内置比赛全程、空场丢球、未知号码与非法帧拒绝。Edge 目前无测试。改协议或 `live-frame.js` 后务必跑 `npm test`。
- **构建产物**：`live/web/dist/index.html` 与 `live/server/server.cjs` 由构建生成，可入库以便离线试播，但改动源码后需重新构建。

## 实现状态

改动前务必区分已实现与设计中，避免假设未完成的链路可用。

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 浏览器三维观赛前端 | ✅ 已实现 | 内置 60 秒比赛，六机位、骨骼、暂停定格、HLS 实拍；支持 v1 与 v2 快照输入。 |
| 本地 v1 试播网关 | ✅ 已实现 | `POST /frame`、`/live`、`/demo`，复用前端校验器。 |
| Edge SwiftPM 骨架 | ✅ 可构建 | 空入口，采集、推理、融合、上发均未实现。 |
| Edge 采集与对时 | ⏳ 设计中 | 厂商 SDK / AVFoundation / RTSP，fMP4 分段与曝光时间索引。 |
| Edge 检测、姿态、读号 | ⏳ 设计中 | Core ML + Vision，Python/OpenCV 做离线对照。 |
| Edge 三维融合与动作 | ⏳ 设计中 | C++/OpenCV `calib3d` 经 C 接口供 Swift 调用。 |
| 正式 v2 网关与归档 | ⏳ 设计中 | Fastify 写入校验、SQLite 归档、WebSocket 订阅、HLS 媒体。 |
| 基于曝光时刻的原片同步复核 | ⏳ 设计中 | `GET /cameras/{cameraId}/locate?captureUtcUs=…` 定位。 |

## 关键文件

- `live/web/src/protocol.js` —— v1 校验器，是前后端共享的事实来源。
- `live/web/src/live-frame.js` —— v2 快照到渲染输入的转换，测试直接覆盖。
- `live/web/src/match.js` —— 内置 60 秒比赛生成器，前后端共用。
- `live/web/src/receiver.js` —— WebSocket 接收与插值。
- `live/web/src/app.js` —— 演示与直播两种模式及控制台。
- `live/server/server.mjs` —— 网关源码；`server.cjs` 为其打包产物。
- `docs/02-technical-design.md` —— 改接口、加模块或调整延迟预算前必读。
