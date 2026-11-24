# 场迹 · PitchTrace 3D

场迹是一个面向足球比赛的三维跟踪与回放项目。项目目标是用球场四周的四组双目摄像头采集比赛，在 Mac mini 边缘节点重建球员、足球和骨骼动作，让观众自由切换视角回看传球、射门和拼抢。

目前可运行的子项目是 **60 秒多机位转播演示**。它用模拟比赛展示三维回放，也能接收外部程序通过 WebSocket 发送的球员、骨骼和足球数据。现场采集与多机位重建仍在规划中。

## 项目文档

| 文档 | 用途 |
| --- | --- |
| [详细需求说明](docs/01-requirements.md) | 确定比赛流程、功能范围及现场验收标准。 |
| [技术实现方案](docs/02-technical-design.md) | 设计八路采集、Mac mini 三维重建、数据分发及复核链路。 |
| [落地开发计划](docs/03-development-plan.md) | 安排场勘、开发、实拍验收与发布，明确各阶段负责人和放行条件。 |

## 演示子项目

[Three.js 足球转播演示](demos/threejs-live/) 包含独立的源码、模型、构建配置和直播接收服务。克隆仓库后，直接用浏览器打开[离线演示页面](demos/threejs-live/dist/PitchTrace-Live-60s.html)即可观看。

要试播 WebSocket 数据流，从仓库根目录运行以下命令，再打开 <http://127.0.0.1:8766/>。运行服务需要 Node.js 20 或更高版本。

```sh
cd demos/threejs-live
node server.cjs
```

机位操作、构建命令和数据帧格式见[演示使用说明](demos/threejs-live/README.md)。

## 许可证

原创代码采用 [MIT 许可证](LICENSE)。演示使用的第三方模型、动画和依赖保留各自许可，详见[素材署名与许可](demos/threejs-live/licenses/)。
