# Edge Web UI 实施检查清单

基于 [docs/08-edge-web-ui-design.md](../../../../docs/08-edge-web-ui-design.md) 的分阶段实施计划。

## 阶段 1：基础框架（W3，2 天）

**目标**：能启动 Web 服务器，显示模拟状态

- [ ] 在 `Package.swift` 添加 Vapor 依赖
  ```swift
  .package(url: "https://github.com/vapor/vapor.git", from: "4.99.0")
  ```
- [ ] 创建目录结构
  ```
  Sources/PitchTraceEdge/WebUI/
  ├── WebUICommand.swift
  ├── WebUIServer.swift
  ├── Controllers/
  │   └── StatusController.swift
  └── Resources/
      ├── index.html
      ├── dashboard.js
      └── styles.css
  ```
- [ ] 实现 `WebUICommand.swift`
  - [ ] 解析命令行参数 `--bind`、`--match-dir`
  - [ ] 启动 Vapor 应用
- [ ] 实现 `WebUIServer.swift`
  - [ ] 配置路由：`GET /` 返回 `index.html`
  - [ ] 嵌入静态资源（使用 `Bundle.module.url(forResource:withExtension:)` 或直接读取文件）
- [ ] 实现 `StatusController.swift`
  - [ ] `GET /api/status` 返回模拟的 4 机位状态 JSON
- [ ] 创建 `index.html`
  - [ ] 页面标题和布局
  - [ ] 4 个相机卡片（模拟数据）
  - [ ] 控制按钮（暂不可用）
- [ ] 在 `PitchTraceEdge.swift` 注册 `web-ui` 子命令

**验收**：
```sh
swift run --package-path edge pitchtrace-edge web-ui --bind 127.0.0.1:8765
# 浏览器访问 http://127.0.0.1:8765
# 能看到 4 个相机卡片显示模拟状态
```

---

## 阶段 2：实时状态集成（W3-4，3 天）

**目标**：Web UI 显示真实采集状态，支持开始/停止控制

- [ ] 创建 `Sources/PitchTraceEdge/Monitoring/SystemMonitor.swift`
  - [ ] 定义 `actor SystemMonitor`
  - [ ] 聚合各机位状态：`cameraId`、`state`、`fps`、`frameCount`、`queueDepth`
  - [ ] 提供 `func getStatus() async -> SystemStatus`
- [ ] 修改现有采集模块，向 `SystemMonitor` 报告状态
  - [ ] 在 `CameraCapture` 更新帧率和帧计数
  - [ ] 在处理队列更新 `queueDepth`
- [ ] 实现 `WebUI/WebSocket/LiveUpdateHandler.swift`
  - [ ] 处理 WebSocket 连接：`ws://localhost:8765/live`
  - [ ] 连接时立即推送完整状态
  - [ ] 每秒推送状态变化
- [ ] 实现 `WebUI/Controllers/ControlController.swift`
  - [ ] `POST /api/control/start` 触发采集启动
  - [ ] `POST /api/control/stop` 触发采集停止
  - [ ] 返回成功/失败响应
- [ ] 更新 `index.html` 和 `dashboard.js`
  - [ ] 连接 WebSocket
  - [ ] 实时更新相机卡片状态
  - [ ] 实现开始/停止按钮逻辑
  - [ ] 断线重连逻辑

**验收**：
```sh
# 启动双机位录像重放
swift run --package-path edge pitchtrace-edge web-ui --match-dir /path/to/match &
# 访问 Web UI，实时看到帧率、队列深度更新
# 点击 Stop，采集停止
```

---

## 阶段 3：告警与日志（W4，2 天）

**目标**：显示告警和系统日志

- [ ] 在 `SystemMonitor` 添加告警管理
  - [ ] 定义告警结构：`level`、`cameraId`、`message`、`timestamp`
  - [ ] 添加告警触发逻辑：
    - [ ] 机位断线（无新帧 >5 秒）
    - [ ] 曝光偏差 >10ms
    - [ ] 队列深度 >50
    - [ ] 磁盘剩余 <100GB
  - [ ] 自动清除已恢复的告警
- [ ] 创建 `Monitoring/HealthCheck.swift`
  - [ ] 检查曝光同步质量
  - [ ] 检查标定有效性
  - [ ] 检查磁盘空间
- [ ] WebSocket 推送告警消息
  - [ ] 新告警触发时立即推送
  - [ ] 告警清除时推送更新
- [ ] 更新前端
  - [ ] 告警面板显示列表
  - [ ] 按级别着色（info/warning/error/critical）
  - [ ] 支持按级别过滤
  - [ ] 点击告警展开详情

**验收**：
```sh
# 启动采集
# 模拟 B 机位断线（拔线或停止输入）
# Web UI 在 2 秒内显示红色告警
# 恢复后告警自动消失
```

---

## 阶段 4：性能图表（W9，3 天）

**目标**：显示延迟、队列、资源使用趋势图

- [ ] 创建 `Monitoring/MetricsCollector.swift`
  - [ ] 记录时间序列数据（环形缓冲区，保留 5 分钟）
  - [ ] 每秒采样：`latencyP95Ms`、`queueDepth`、`cpuPercent`、`memoryUsedMB`
  - [ ] 计算分位数（P50/P95/P99）
- [ ] 实现 `Controllers/MetricsController.swift`
  - [ ] `GET /api/metrics` 返回时间序列 JSON
- [ ] 更新前端
  - [ ] 引入 Chart.js（CDN）
  - [ ] 创建 `charts.js`
  - [ ] 绘制三条折线图：
    - [ ] 延迟 P95（单位：秒）
    - [ ] 队列深度
    - [ ] CPU 使用率（百分比）
  - [ ] 每 5 秒刷新数据
  - [ ] 时间轴显示最近 5 分钟

**验收**：
```sh
# 连续运行 10 分钟
# 图表持续更新
# 模拟队列积压（暂停处理），图表立即反映
```

---

## 阶段 5：预览画面（W10，2 天）

**目标**：显示各机位实时画面缩略图

- [ ] 实现 `Controllers/PreviewController.swift`
  - [ ] `GET /api/preview/:cameraId` 返回 JPEG
  - [ ] 从采集模块获取最新帧
  - [ ] 缩放到 320×180
  - [ ] 编码为 JPEG（质量 70）
  - [ ] 无可用帧时返回 404
- [ ] 更新前端
  - [ ] 在每个相机卡片添加 `<img>` 标签
  - [ ] 每秒拉取 `/api/preview/:cameraId`
  - [ ] 无图片时显示灰色占位符
  - [ ] 添加加载状态指示

**验收**：
```sh
# 启动采集
# 各相机卡片显示实时画面
# 延迟 <2 秒
```

---

## 阶段 6：配置界面（G7 之后，3 天）

**目标**：通过 Web UI 创建和管理比赛配置

- [ ] 实现 `Controllers/ConfigController.swift`
  - [ ] `GET /api/config` 返回当前比赛配置
  - [ ] `POST /api/config` 保存新配置
  - [ ] 验证字段完整性
- [ ] 创建 `Resources/config.html`
  - [ ] 表单：比赛 ID、场地类型、机位数量
  - [ ] 标定文件选择器（上传或选择已有）
  - [ ] 检测器参数输入（球色 HSV、队服颜色）
  - [ ] 保存按钮
- [ ] 在 `index.html` 添加"配置"导航链接

**验收**：
```sh
# 通过 Web UI 创建新比赛配置
# 保存后能在指定目录看到 match.json
# pitchtrace-edge run-match 能加载该配置
```

---

## 测试用例

在 `Tests/PitchTraceEdgeTests/WebUITests.swift` 添加：

- [ ] 测试 `SystemMonitor` 状态聚合
  - [ ] 添加 2 个相机，验证状态列表
  - [ ] 更新帧率，验证反映在状态中
- [ ] 测试告警触发
  - [ ] 模拟机位断线，验证告警生成
  - [ ] 恢复后验证告警清除
- [ ] 测试 HTTP 接口
  - [ ] `GET /api/status` 返回正确 JSON
  - [ ] `POST /api/control/start` 改变状态
  - [ ] `GET /api/metrics` 返回时间序列
- [ ] 测试 WebSocket
  - [ ] 连接后立即收到完整状态
  - [ ] 状态变化时收到推送消息

---

## 文档更新

- [ ] 更新 `edge/README.md`
  - [ ] 新增"Web UI 监控界面"章节
  - [ ] 添加启动命令和访问说明
- [ ] 创建 `edge/WebUI-Development.md`
  - [ ] 架构说明
  - [ ] 如何添加新 API
  - [ ] 如何添加新告警类型
- [ ] 更新 `docs/01-requirements.md`
  - [ ] R11 增加 Web UI 实现说明
- [ ] 更新 `docs/03-development-plan.md`
  - [ ] W3-4 和 W9-10 增加 Web UI 交付物

---

## 部署清单

- [ ] 验证 Vapor 依赖在目标 macOS 版本可用
- [ ] 确认 Web UI 在 Safari、Chrome、Firefox 正常显示
- [ ] 确认局域网访问正常（`--bind 0.0.0.0:8765`）
- [ ] 编写运维手册：如何重启、查看日志、排查连接问题
- [ ] 准备演示视频或截图
