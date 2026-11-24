# 场迹 · PitchTrace 3D

场迹是一个面向足球比赛的三维跟踪与回放项目。项目目标是用球场四周的四组双目摄像头采集比赛，在 Mac mini 边缘节点重建球员、足球和骨骼动作，让观众自由切换视角回看传球、射门和拼抢。

[直播观赛前端](live/web/) 已可以运行。它内置一场 60 秒比赛，也可以切换到 WebSocket 直播数据和外部 HLS 实拍视频。克隆仓库后可直接用浏览器打开[离线观赛页](live/web/dist/index.html)。

## 正式子项目

| 子项目 | 交付物与职责 | 首版技术方案 |
| --- | --- | --- |
| [PitchTrace Edge · 边缘计算](edge/) | 已创建可构建的 SwiftPM 空项目；规划在 Mac mini 上采集、重建并输出场景快照、视频片段与曝光时间索引。 | Swift、Core ML、Vision、C++/OpenCV、FFmpeg、VideoToolbox。[边缘端实现](docs/02-technical-design.md#pitchtrace-edge) |
| PitchTrace Live · 直播（`live/`） | 接收、归档、分发上述数据，提供三维观赛、实拍回看与人工复核。内含服务端和网页端。 | Node.js/TypeScript、Fastify、WebSocket、HLS、Vite、Three.js。[直播端实现](docs/02-technical-design.md#pitchtrace-live) |

浏览器端已支持 v1 和 v2 场景快照，[示例网关](live/server/)提供 v1 试播。边缘识别、正式 v2 网关和基于原片时间索引的同步复核仍在规划中。[场景与媒体接口](docs/02-technical-design.md#edge-live-contract)

## 项目文档

| 文档 | 用途 |
| --- | --- |
| [详细需求说明](docs/01-requirements.md) | 确定比赛流程、功能范围及现场验收标准。 |
| [技术实现方案](docs/02-technical-design.md) | 设计八路采集、Mac mini 三维重建、数据分发及复核链路。 |
| [落地开发计划](docs/03-development-plan.md) | 安排场勘、开发、实拍验收与发布，明确各阶段负责人和放行条件。 |

## 本地试播

要试播 WebSocket 数据流，从仓库根目录运行以下命令，再打开 <http://127.0.0.1:8766/>。运行服务需要 Node.js 20 或更高版本。

```sh
node live/server/server.cjs
```

机位操作、构建命令和数据帧格式见[观赛前端说明](live/web/README.md)和[网关接口说明](live/server/README.md)。

## 许可证

原创代码采用 [MIT 许可证](LICENSE)。演示使用的第三方模型、动画和依赖保留各自许可，详见[素材署名与许可](live/web/licenses/)。
