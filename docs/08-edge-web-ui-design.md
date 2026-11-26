# Edge Web UI 技术设计

**版本**　v0.1 草案  
**更新**　2025-01-20  
**状态**　设计中

## 1. 目标与范围

为 PitchTrace Edge 提供轻量级 Web UI，满足需求 R11 中"场务有设备健康视图、开始／停止和故障恢复操作"的要求。UI 运行在 Mac mini 本机，通过浏览器访问 `http://127.0.0.1:8765`，支持局域网内其他设备远程监控。

### 1.1 核心功能（P0，W3-4 交付）

- **设备状态监控**：显示 2–8 台或更多机位的实时状态、帧率、曝光偏差、丢帧统计
- **采集控制**：开始/停止采集、暂停/恢复处理
- **告警面板**：机位断线、时钟漂移、磁盘不足、标定失效等异常
- **系统日志**：最近 100 条事件，按时间倒序，支持按级别过滤

### 1.2 增强功能（P1，W9-10 交付）

- **性能图表**：延迟分布（P50/P95/P99）、队列深度、CPU/内存使用趋势
- **预览画面**：各机位最新帧缩略图（JPEG，每秒更新 1 次）
- **健康检查**：曝光同步质量、标定版本、各机位覆盖区域
- **录像管理**：当前比赛录像大小、剩余空间、预计可录制时长

### 1.3 配置界面（G7 之后）

- **比赛创建**：填写比赛 ID、场地类型、机位数量
- **标定选择**：上传或选择已有的镜头内参和场地标定
- **检测器参数**：模型路径、球色 HSV 范围、队服颜色
- **留存策略**：录像保留天数、自动清理规则

### 1.4 明确不做的功能

以下功能由 Live Web 端负责，Edge UI 不实现：

- ❌ 场景回放与复核（由 Live Web 的三维观赛和原片复核提供）
- ❌ 事件标注与轨迹编辑（需要完整的场景数据和原片同步）
- ❌ 复杂的数据分析与可视化（轨迹对比、误差热图、事件检索）
- ❌ 用户权限管理（首版仅本机访问，公网部署由 Live 网关负责）

---

## 2. 架构设计

### 2.1 技术栈

| 层级 | 技术选择 | 理由 |
|------|----------|------|
| **后端** | Swift + Vapor 4 | Swift 原生、异步友好、轻量级、易集成到现有 SwiftPM 项目 |
| **前端** | 单文件 HTML + 原生 JavaScript | 无构建依赖、易嵌入、可离线运行、便于维护 |
| **实时通信** | WebSocket（Vapor 内置） | 推送设备状态、告警和日志更新 |
| **数据格式** | JSON | 前后端通用、易调试 |
| **部署** | 嵌入式 HTTP 服务器 | 随 `pitchtrace-edge` 可执行文件启动，监听 `127.0.0.1:8765` |

**为什么选 Vapor 而不是 Swifter？**
- Vapor 对 async/await 支持更好，与 Swift Concurrency 无缝集成
- 内置 WebSocket 支持，无需额外依赖
- 活跃的社区和文档，适合长期维护
- 可选的中间件（CORS、日志、压缩）便于后续扩展

### 2.2 目录结构

```
edge/
├── Package.swift                              # 新增 Vapor 依赖
├── Sources/PitchTraceEdge/
│   ├── PitchTraceEdge.swift                   # CLI 主入口
│   ├── WebUI/
│   │   ├── WebUICommand.swift                 # `web-ui` 子命令
│   │   ├── WebUIServer.swift                  # Vapor 应用和路由
│   │   ├── Controllers/
│   │   │   ├── StatusController.swift         # GET /api/status
│   │   │   ├── ControlController.swift        # POST /api/control/*
│   │   │   ├── MetricsController.swift        # GET /api/metrics
│   │   │   └── PreviewController.swift        # GET /api/preview/:cameraId
│   │   ├── WebSocket/
│   │   │   └── LiveUpdateHandler.swift        # WS /live 推送状态更新
│   │   └── Resources/
│   │       ├── index.html                     # 单页应用入口
│   │       ├── dashboard.js                   # 状态面板逻辑
│   │       ├── controls.js                    # 控制按钮逻辑
│   │       ├── charts.js                      # 性能图表（可选用 Chart.js CDN）
│   │       └── styles.css                     # 样式表
│   └── Monitoring/
│       ├── SystemMonitor.swift                # 聚合设备状态、告警、日志
│       ├── HealthCheck.swift                  # 曝光同步、标定、磁盘检查
│       └── MetricsCollector.swift             # 延迟、队列、资源指标
└── Tests/PitchTraceEdgeTests/
    └── WebUITests.swift                       # 接口回归测试
```

### 2.3 进程模型

```
pitchtrace-edge web-ui --bind 127.0.0.1:8765 --match-dir /path/to/match
    │
    ├─── Vapor HTTP Server (port 8765)
    │       ├─── GET /                          → index.html
    │       ├─── GET /api/status                → 设备状态 JSON
    │       ├─── POST /api/control/start        → 开始采集
    │       ├─── POST /api/control/stop         → 停止采集
    │       ├─── GET /api/metrics               → 性能指标
    │       ├─── GET /api/preview/:cameraId     → JPEG 缩略图
    │       └─── WS /live                       → 推送状态更新
    │
    ├─── SystemMonitor (后台任务)
    │       └─── 每秒汇总设备状态、告警、日志
    │
    └─── Capture / Processing Pipelines (已有模块)
            └─── 通过共享状态与 SystemMonitor 通信
```

**关键设计决策**：
- Web UI 作为独立子命令 `web-ui`，不影响现有 CLI 工具
- 采集和处理模块继续使用 Swift Concurrency，通过 `actor` 提供线程安全的状态访问
- WebSocket 仅推送变化的状态，减少带宽占用
- 所有路由默认只监听 `127.0.0.1`，避免意外暴露到公网

---

## 3. 数据契约

### 3.1 设备状态 API

**GET /api/status**

返回当前所有机位的状态摘要。

**响应示例**：
```json
{
  "timestamp": "2026-01-20T14:32:15.123Z",
  "matchId": "trial-2026-01-20",
  "matchState": "capturing",
  "cameras": [
    {
      "cameraId": "A",
      "state": "active",
      "fps": 29.97,
      "frameCount": 12543,
      "lastFrameUtcUs": "1737381135123456",
      "exposureDriftMs": 0.8,
      "droppedFrames": 2,
      "queueDepth": 3,
      "calibrationId": "field-a-v3",
      "calibrationValid": true
    },
    {
      "cameraId": "B",
      "state": "disconnected",
      "fps": 0,
      "frameCount": 12480,
      "lastFrameUtcUs": "1737381133000000",
      "exposureDriftMs": null,
      "droppedFrames": 65,
      "queueDepth": 0,
      "calibrationId": "field-a-v3",
      "calibrationValid": true
    }
  ],
  "alerts": [
    {
      "level": "error",
      "cameraId": "B",
      "message": "Camera B disconnected since 2026-01-20T14:32:13Z",
      "timestamp": "2026-01-20T14:32:13.456Z"
    },
    {
      "level": "warning",
      "cameraId": "A",
      "message": "Queue depth exceeded 10 frames",
      "timestamp": "2026-01-20T14:32:10.789Z"
    }
  ],
  "disk": {
    "totalBytes": 2000000000000,
    "usedBytes": 450000000000,
    "availableBytes": 1550000000000,
    "recordingRateBytes": 4000000,
    "estimatedHoursRemaining": 107.6
  }
}
```

**字段说明**：
- `matchState`: `idle` | `capturing` | `paused` | `stopped`
- `camera.state`: `active` | `disconnected` | `error` | `calibration_invalid`
- `alerts[].level`: `info` | `warning` | `error` | `critical`

### 3.2 控制 API

**POST /api/control/start**

开始采集或恢复暂停的采集。

**请求体**（可选）：
```json
{
  "matchId": "trial-2026-01-20",
  "matchConfigPath": "/path/to/match.json"
}
```

**响应**：
```json
{
  "success": true,
  "message": "Capture started for match trial-2026-01-20"
}
```

**POST /api/control/stop**

停止采集并完成当前段的写入。

**响应**：
```json
{
  "success": true,
  "message": "Capture stopped, finalizing segments"
}
```

**POST /api/control/pause**

暂停处理（继续录像，但停止推理和上行）。

**POST /api/control/resume**

恢复处理。

### 3.3 性能指标 API

**GET /api/metrics**

返回延迟、队列、资源使用的时间序列数据（最近 5 分钟，每秒 1 个点）。

**响应示例**：
```json
{
  "timestamp": "2026-01-20T14:32:15.123Z",
  "latency": {
    "p50Ms": 1850,
    "p95Ms": 2340,
    "p99Ms": 3120
  },
  "queues": {
    "decode": {"depth": 3, "maxDepth": 100},
    "inference": {"depth": 12, "maxDepth": 50},
    "upload": {"depth": 2, "maxDepth": 200}
  },
  "resources": {
    "cpuPercent": 68.5,
    "memoryUsedMB": 4832,
    "memoryTotalMB": 24576,
    "gpuPercent": 42.1
  },
  "timeSeries": [
    {
      "timestampMs": 1737381135000,
      "latencyP95Ms": 2340,
      "queueDepth": 17,
      "cpuPercent": 68.5
    }
  ]
}
```

### 3.4 预览画面 API

**GET /api/preview/:cameraId**

返回指定机位的最新帧 JPEG 缩略图（320×180，质量 70）。

**响应**：
- Content-Type: `image/jpeg`
- 更新频率：约 1 秒一次
- 无可用帧时返回 404

### 3.5 WebSocket 实时推送

**WS /live**

连接后立即推送完整状态，之后每秒推送变化的字段。

**推送消息类型**：

```json
{
  "type": "status_update",
  "data": {
    "timestamp": "2026-01-20T14:32:16.123Z",
    "cameras": [
      {"cameraId": "A", "fps": 30.01, "queueDepth": 2}
    ]
  }
}
```

```json
{
  "type": "alert",
  "data": {
    "level": "error",
    "cameraId": "B",
    "message": "Camera B disconnected",
    "timestamp": "2026-01-20T14:32:16.456Z"
  }
}
```

```json
{
  "type": "log",
  "data": {
    "level": "info",
    "message": "Segment 42 finalized for camera A",
    "timestamp": "2026-01-20T14:32:16.789Z"
  }
}
```

---

## 4. 前端设计

### 4.1 页面布局

```
┌─────────────────────────────────────────────────────────────┐
│  PitchTrace Edge Monitor        [Start] [Stop] [Refresh]    │
├─────────────────────────────────────────────────────────────┤
│  Match: trial-2026-01-20   State: Capturing   Uptime: 2h 34m│
├─────────────────────────────────────────────────────────────┤
│  Cameras                                                     │
│  ┌─────────┬─────────┬─────────┬─────────┐                 │
│  │   A     │   B     │   C     │   D     │  (动态列数)      │
│  │ ✓ 30fps │ ✗ 0fps  │ ✓ 25fps │ ✓ 30fps │                 │
│  │ Drift:  │ Drift:  │ Drift:  │ Drift:  │                 │
│  │ 0.8ms   │   --    │ 1.2ms   │ 0.5ms   │                 │
│  │ Queue:3 │ Queue:0 │ Queue:2 │ Queue:4 │                 │
│  │ [预览图] │ [预览图] │ [预览图] │ [预览图] │                 │
│  └─────────┴─────────┴─────────┴─────────┘                 │
├─────────────────────────────────────────────────────────────┤
│  Alerts                                                      │
│  ⚠ Camera B disconnected since 14:32:13                     │
│  ⚠ Queue depth exceeded 10 frames (Camera A)                │
│  ℹ Segment 42 finalized for Camera A                        │
├─────────────────────────────────────────────────────────────┤
│  Performance (last 5 min)                                   │
│  Latency P95: 2.34s  │  Queue Depth: 17  │  CPU: 68.5%     │
│  [延迟折线图]            [队列深度图]        [资源使用图]      │
├─────────────────────────────────────────────────────────────┤
│  Disk: 450GB / 2TB used   │   Remaining: ~107h             │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 交互行为

| 操作 | 行为 |
|------|------|
| 点击 **[Start]** | 发送 `POST /api/control/start`，按钮禁用直到响应，成功后按钮变灰并显示"Running" |
| 点击 **[Stop]** | 确认对话框 → `POST /api/control/stop`，显示"Stopping…"直到状态变为 `stopped` |
| 点击 **[Refresh]** | 重新拉取 `GET /api/status` 和 `GET /api/metrics` |
| WebSocket 断线 | 顶部显示红色横幅"Connection lost, retrying…"，每 5 秒重连 |
| 点击相机卡片 | 展开详细信息（帧号、曝光时间、丢帧明细、标定版本） |
| 点击告警 | 展开完整堆栈信息或建议的恢复步骤 |

### 4.3 颜色与状态

| 状态 | 颜色 | 图标 |
|------|------|------|
| `active` | 绿色 | ✓ |
| `disconnected` | 红色 | ✗ |
| `error` | 红色 | ⚠ |
| `calibration_invalid` | 橙色 | ⚠ |
| `paused` | 黄色 | ⏸ |
| Alert `info` | 蓝色 | ℹ |
| Alert `warning` | 橙色 | ⚠ |
| Alert `error` | 红色 | ⚠ |
| Alert `critical` | 深红 | 🔥 |

### 4.4 响应式设计

- **桌面（>1200px）**：4 列相机卡片 + 侧边告警面板
- **平板（768–1200px）**：2 列相机卡片 + 底部告警
- **手机（<768px）**：单列相机卡片 + 折叠告警

---

## 5. 实施计划

### 阶段 1：基础框架（W3，2 天）

**交付物**：
- [x] 在 `Package.swift` 中添加 Vapor 依赖
- [x] 创建 `WebUICommand.swift` 和 `WebUIServer.swift`
- [x] 实现 `GET /` 返回静态 `index.html`
- [x] 实现 `GET /api/status` 返回模拟数据
- [x] 创建单文件 HTML + JS 前端，显示模拟状态

**验收**：`swift run pitchtrace-edge web-ui` 启动服务器，浏览器访问 `http://127.0.0.1:8765` 能看到 4 个模拟机位状态。

### 阶段 2：实时状态集成（W3-4，3 天）

**交付物**：
- [x] 创建 `SystemMonitor` actor 聚合采集模块的状态
- [x] 实现 `LiveUpdateHandler.swift` 推送 WebSocket 消息
- [x] 前端连接 WebSocket 并更新状态
- [x] 实现 `POST /api/control/start` 和 `stop`，连接到采集模块

**验收**：启动双机位录像重放，Web UI 实时显示帧率、曝光偏差、队列深度。点击 Stop 后采集停止，状态变为 `stopped`。

### 阶段 3：告警与日志（W4，2 天）

**交付物**：
- [x] 在 `SystemMonitor` 中集成健康检查逻辑
- [x] 告警触发条件：机位断线、曝光偏差 >10ms、队列深度 >50、磁盘剩余 <100GB
- [x] WebSocket 推送告警消息
- [x] 前端显示告警列表，支持按级别过滤

**验收**：模拟 B 机位断线，Web UI 在 2 秒内显示红色告警。恢复后告警自动消失。

### 阶段 4：性能图表（W9，3 天）

**交付物**：
- [x] 实现 `MetricsCollector` 记录延迟、队列、资源时间序列
- [x] 实现 `GET /api/metrics` 返回最近 5 分钟数据
- [x] 前端集成 Chart.js（CDN），绘制折线图
- [x] 自动刷新图表（每 5 秒）

**验收**：连续运行 10 分钟，图表显示延迟 P95 和队列深度的变化趋势。模拟队列积压，图表立即反映。

### 阶段 5：预览画面（W10，2 天）

**交付物**：
- [x] 实现 `GET /api/preview/:cameraId` 编码最新帧为 JPEG
- [x] 前端每秒拉取各机位预览图并更新
- [x] 无可用帧时显示灰色占位图

**验收**：启动采集，各相机卡片显示实时画面缩略图，延迟 <2 秒。

### 阶段 6：配置界面（G7 之后，3 天）

**交付物**：
- [x] 实现 `GET /api/config` 和 `POST /api/config` 管理比赛配置
- [x] 前端增加配置页面，表单填写比赛 ID、机位数量、标定文件路径
- [x] 验证配置完整性，保存到指定目录

**验收**：通过 Web UI 创建新比赛配置，`pitchtrace-edge run-match` 能直接加载该配置。

---

## 6. 测试策略

### 6.1 单元测试

在 `Tests/PitchTraceEdgeTests/WebUITests.swift` 中覆盖：
- `SystemMonitor` 状态聚合逻辑
- `HealthCheck` 告警触发条件
- `MetricsCollector` 时间序列计算
- 控制 API 的状态转换（idle → capturing → stopped）

### 6.2 集成测试

使用 XCTest 启动完整 Vapor 服务器：
- 发送 HTTP 请求验证响应格式
- 建立 WebSocket 连接验证推送消息
- 模拟机位断线和恢复，验证告警

### 6.3 手工测试清单

**G2 验收前**：
- [ ] 启动 2 机位录像重放，Web UI 显示正确状态
- [ ] 拔掉一路输入，2 秒内显示断线告警
- [ ] 点击 Stop，采集在 5 秒内停止，状态变为 `stopped`
- [ ] 刷新页面，状态保持一致
- [ ] WebSocket 断线后自动重连

**G5 验收前**：
- [ ] 连续运行 90 分钟，图表持续更新
- [ ] 模拟队列积压（暂停处理），延迟和队列图立即反映
- [ ] 磁盘剩余 <100GB 时显示告警
- [ ] 各机位预览画面与实际采集内容一致

---

## 7. 安全与权限

### 7.1 访问控制（首版）

- 默认只监听 `127.0.0.1:8765`，不暴露到公网
- 局域网访问需显式指定 `--bind 0.0.0.0:8765`
- 无用户认证，依赖物理访问控制

### 7.2 后续增强（G7 之后）

- 添加简单的 HTTP Basic Auth 或 Token 认证
- 只读用户只能查看状态，不能执行控制操作
- 管理员用户可以启动/停止采集、修改配置
- 记录所有控制操作的审计日志

### 7.3 HTTPS 支持

首版使用 HTTP，后续可选：
- 使用自签名证书（适合内网）
- 或通过反向代理（Nginx）提供 HTTPS

---

## 8. 性能指标

### 8.1 资源占用

| 指标 | 目标值 | 测量方法 |
|------|--------|----------|
| 内存增量 | <50MB | 启动前后对比 `Activity Monitor` |
| CPU 增量 | <5% | 无客户端连接时测量 |
| WebSocket 带宽 | <10KB/s | 单客户端，4 机位，每秒推送 |

### 8.2 响应延迟

| 操作 | 目标延迟 | 验收标准 |
|------|----------|----------|
| `GET /api/status` | <50ms | P95 |
| `POST /api/control/start` | <200ms | P95 |
| WebSocket 推送 | <100ms | 状态变化到客户端接收 |
| 预览图片 | <500ms | 请求到 JPEG 返回 |

---

## 9. 文档与交付

### 9.1 用户文档

在 `edge/README.md` 中新增章节：

```markdown
## Web UI 监控界面

启动内置监控服务器：

\`\`\`sh
swift run pitchtrace-edge web-ui --bind 127.0.0.1:8765 --match-dir /path/to/match
\`\`\`

在浏览器访问 `http://127.0.0.1:8765`。

### 功能说明

- **设备状态**：显示各机位帧率、曝光偏差、队列深度
- **控制按钮**：开始/停止采集
- **告警面板**：机位断线、时钟漂移、磁盘不足
- **性能图表**：延迟分布、队列深度、资源使用

### 局域网访问

指定 `--bind 0.0.0.0:8765` 允许其他设备访问。在同一局域网的设备上访问 `http://<Mac-mini-IP>:8765`。

**注意**：首版无认证，仅适用于可信网络。
\`\`\`

### 9.2 开发者文档

创建 `edge/WebUI-Development.md`：
- Vapor 路由结构
- `SystemMonitor` 状态更新流程
- 如何添加新的 API 端点
- 如何添加新的告警类型

### 9.3 交付清单

- [x] `Sources/PitchTraceEdge/WebUI/` 完整代码
- [x] `Tests/PitchTraceEdgeTests/WebUITests.swift` 测试用例
- [x] `edge/README.md` 新增 Web UI 章节
- [x] `edge/WebUI-Development.md` 开发者指南
- [x] 更新 `docs/01-requirements.md` R11 实现说明
- [x] 更新 `docs/03-development-plan.md` W3-4 和 W9-10 交付物

---

## 10. 未来扩展

### 10.1 多比赛管理（v0.2）

- 在同一 Web UI 中切换多个比赛
- 比赛列表页：显示所有历史比赛、状态、时长
- 支持暂停一场比赛并启动另一场

### 10.2 远程控制（v0.3）

- 通过 Web UI 上传标定文件
- 在线调整检测器参数（球色 HSV、队服颜色）
- 远程触发标定验证和重新计算

### 10.3 移动端适配（v0.4）

- 优化小屏幕布局
- 支持 PWA（渐进式 Web 应用），可添加到主屏幕
- 推送通知（需要 Service Worker）

### 10.4 集成 Live 网关（v0.5）

- 在 Edge UI 中查看 Live 网关的接收状态
- 一键重传失败的场景帧和原片段
- 显示观众数量和延迟分布

---

## 11. 参考资料

- [Vapor 4 文档](https://docs.vapor.codes/4.0/)
- [WebSocket 协议 RFC 6455](https://tools.ietf.org/html/rfc6455)
- [Chart.js 文档](https://www.chartjs.org/docs/latest/)
- [MDN Web 性能指南](https://developer.mozilla.org/en-US/docs/Web/Performance)
- [Apple 人机界面指南 · macOS](https://developer.apple.com/design/human-interface-guidelines/macos)

---

**变更历史**

| 版本 | 日期 | 作者 | 说明 |
|------|------|------|------|
| v0.1 | 2025-01-20 | Kiro | 初始草案 |
