---
inclusion: always
---
# 产品与范围

场迹（PitchTrace 3D）第一阶段 MVP 通过球场四周的四台单摄像头同步采集比赛，计划在 M6 Mac mini 上重建球员、足球与骨骼动作，经直播服务分发，供浏览器自由切换视角回看传球、射门与拼抢。完整需求见 [docs/01-requirements.md](../../docs/01-requirements.md)。

## 主要使用者

- **场务人员**：安装设备、标定、建赛、开赛与归档。
- **教练／分析员**：导入名单、校正号码与身份、找事件、切机位、导出片段索引。
- **网页观众**：跟球、跟球员、自由环绕、暂停回看、返回延时直播。
- **复核员**：查看原片与骨骼对照，标记确认／排除／证据不足。

## 实现状态（改动前必读）

| 能力 | 状态 |
| --- | --- |
| 浏览器三维观赛（内置比赛、六机位、骨骼、暂停、HLS）| ✅ 已实现 |
| 浏览器场景归档回放（拖动、调速、换机位、返回直播）| ✅ 已实现 |
| 浏览器原片复核面板（HLS 播放、按曝光定位、缺口提示）| ✅ 已实现 |
| v1 试播网关（`POST /frame`、`/live`、`/demo`）| ✅ 已实现 |
| v2 场景归档与订阅（SQLite、分页历史、重启恢复）| ✅ 已实现 |
| v2 原片归档与同步复核（fMP4 三件校验、按曝光定位）| ✅ 已实现 |
| Edge CLI（replay、audit-sync、audit-visibility、calibrate、publish-media 等）| ✅ 已实现 |
| Edge 录像重放（2–8 机位，跨周期，ffprobe 校验）| ✅ 已实现 |
| Edge 闪光对时审计（`audit-sync`）| ✅ 已实现 |
| Edge 分区双视角可见率（`audit-visibility`）| ✅ 已实现 |
| Edge 离线镜头与场地标定（`calibrate-intrinsics`、`calibrate`）| ✅ 已实现 |
| Edge Python 几何工具链（tracking、pose、scene_archive 等）| ✅ 已实现 |
| Edge Swift 单元与集成测试 | ✅ 已实现 |
| Edge 实机采集与对时（厂商 SDK／AVFoundation／RTSP）| ⏳ 设计中 |
| Edge Core ML + Vision 单视角检测与姿态 | ⏳ 设计中 |
| Edge C++/OpenCV 三维融合正式接入 | ⏳ 设计中 |
| 正式公网网关（Fastify、访问权限、HLS 分发）| ⏳ 设计中 |

详细说明见 [AGENTS.md](../../AGENTS.md) 实现状态表。**不要假设「设计中」的能力已可用。**

## 范围守则

- 缺测是一等状态。未识别号码、球出视野、单视角高球、脚部被挡都需保留对应状态，不得用 0 号或原点填补未知观测。
- 两个子项目（Edge 与 Live）独立构建、测试和发布，通过场景快照与媒体接口解耦。不要让一端直接读写另一端的进程内存状态或数据库。
- 改 Live 侧数据契约时同步更新 `schemas/v2/`，前端与网关都要更新并通过 `npm test`。
