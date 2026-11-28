# PitchTrace Edge 实现缺口

**复核日期**：2026-11-28（取代 2024-11-27 的旧版，旧版多项已实现的能力被误列为缺失）
**依据**：`docs/02-technical-design.md`、`docs/07-edge-development-plan.md`、`docs/08-edge-web-ui-design.md`，以及 `edge/Sources` 当前代码。
**验证**：`swift build` 通过。`Tests/Integration` 下 94 项 Python 测试通过。Swift Testing 需要完整 Xcode（仅装 Command Line Tools 时缺 `TestingMacros` 插件，测试目标无法编译）。

## 已实现

| 范围 | 实现位置 |
| --- | --- |
| 录像重放、曝光映射、缺口校验、v2 媒体索引 | `RecordingExport.swift`、`RecordingTiming.swift`、`CameraMediaPeriod.swift` |
| 对时与可见率审计 | `ExposureAudit.swift`、`VisibilityAudit.swift` |
| 采集：文件／RTSP／AVFoundation 分路、模拟相机、`camera-capture` 直采 | `Geometry/capture.py`、`Capture/*.swift`（去马赛克、VideoToolbox 编码、PTP／host-fit 曝光映射、PTP 锁定与失锁处理） |
| MVS 取图层（ROI、帧率、曝光、`GevIEEE1588` 锁定等待） | `Capture/MVSCamera.swift`，已编译，未在实机验证 |
| Core ML 人球检测、小球原图精搜、Vision 姿态与 OCR | `VisionObservation.swift`、`Geometry/vision_model.py`、`ball_search.py` |
| 观测质量（Laplacian 清晰度、遮挡） | `Geometry/observation_quality.py` |
| 跨视角关联（匈牙利算法）、球三角化与重投影剔除、0.3 秒短时预测 | `Geometry/tracking.py`、`camera_geometry.py` |
| 骨骼融合与局部四元数 | `Geometry/pose.py` |
| 名单关联、号码与队色多帧投票、五类候选事件 | `Geometry/match_analysis.py`、`match_events.py` |
| 标定版本按曝光切换 | `Geometry/match_config.py` |
| 场景与原片待办持久化、限速补传、设备凭据 | `Geometry/scene_archive.py`、`stream_match.py`、`gateway_auth.py`、`MediaPublishing.swift` |
| 曝光触发配置与定时动作命令广播（未上机） | `Capture/TriggerSchedule.swift`、`Capture/TriggerScheduler.swift`、`MVSCamera.swift` |
| Web UI：状态、开始／停止、暂停／恢复、预览、积压、CPU／内存 | `WebUI/` |

## 缺口

### 采集与对时（G1／G2）

- **定时触发与外触发已接入，未上机。** `mvs.trigger` 支持 `Line0`／`Line2` 共用外触发与动作命令触发，`trigger-scheduler` 按 PTP 整秒时间表广播定时动作命令，见 [capture.md](capture.md#曝光触发)。时间表算法有测试，节点名、选择器取值、动作队列深度和 `nActionTime` 单位须在 MV-CS050-10GC-PRO 上核对，并以共同闪光得出 P95 ≤ 5 ms。
- **MVS 取图层没有实机验证。** 只有模拟相机回归，实机重连、四路 30 分钟采集和闪光复测待现场。

### 三维跟踪（G3／G4）

- 球员关联代价含地面距离、跨队禁配和号码不一致惩罚，没有极线距离和外观特征。
- 交叉跑位没有多候选与延后绑定。
- 无卡尔曼滤波，现为限速匀速外推，无协方差。
- 球状态只有 `measured`／`predicted`／`lost`，缺滚动、飞行、可能触球，也没有触球处分段。
- 球员足点按偏离中位数剔除离群视角，球按重投影残差剔除。

### 骨骼（G4／G6）

- 骨长（每轨迹滑动估计，±15% 与绝对范围）与膝肘折返上限已约束，髋、肩等其余关节活动范围未约束。
- 原始测量（`reconstructedM`）与平滑结果（`smoothedM`）已分别保存，约束处记录 `constraint`。
- 肢体骨骼轴向扭转无观测，记录在测量的 `axialTwist: unobserved`，尚未用于降低下游置信度。

### 架构

- 设计为 C++/OpenCV 经窄 C 接口供 Swift 调用，现全部在 Python 子进程中。整场实拍吞吐待 G5 验证。

### Web UI（G7 之后，非 MVP 阻塞）

- 无 GPU 指标。
- 无 `GET／POST /api/config` 与配置页面。
- 无认证、只读／管理员角色分离和控制操作审计日志，当前依赖默认只监听 `127.0.0.1`。
- `08-edge-web-ui-design.md` 6.3 手工测试清单未勾选。

## 不属于 Edge

事件人工修订、比赛创建、公网 Fastify 网关、角色权限和 HLS 分发属于 Live 侧，见 `docs/02-technical-design.md`。
