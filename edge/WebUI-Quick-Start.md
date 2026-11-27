# Edge Web UI 快速开始

## 阶段 1：基础框架 ✅ 已完成代码

### 启动命令

```sh
# 本地访问（默认）
swift run --package-path edge pitchtrace-edge web-ui

# 局域网访问
swift run --package-path edge pitchtrace-edge web-ui --bind 0.0.0.0:8765

# 自定义端口
swift run --package-path edge pitchtrace-edge web-ui --bind 127.0.0.1:9000

# 监控特定比赛
swift run --package-path edge pitchtrace-edge web-ui --match-dir /path/to/match
```

### 访问地址

打开浏览器访问：`http://127.0.0.1:8765`

### 功能清单

#### ✅ 已实现（阶段 1）
- 设备状态监控（4 个模拟相机）
- 实时指标显示（帧率、队列深度、曝光偏差）
- 告警面板（错误/警告分级）
- 磁盘使用监控
- 自动刷新（每 5 秒）
- 手动刷新按钮
- 响应式布局（支持桌面/平板/手机）

#### ⏳ 计划中（后续阶段）
- 阶段 2：WebSocket 实时推送、Start/Stop 控制
- 阶段 3：告警触发逻辑、健康检查
- 阶段 4：性能图表（延迟、队列、CPU/内存）
- 阶段 5：预览画面（各机位实时缩略图）
- 阶段 6：配置界面（比赛创建、标定选择）

### API 端点

#### `GET /`
返回 Web UI 主页面

#### `GET /api/status`
返回系统完整状态（JSON）

```json
{
  "timestamp": "2025-01-20T10:00:00Z",
  "matchId": "mock-match-2025-01-20",
  "matchState": "idle",
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
    }
  ],
  "alerts": [...],
  "disk": {...}
}
```

#### `GET /api/health`
简单健康检查，返回 "OK"

### 目录结构

```
edge/Sources/PitchTraceEdge/WebUI/
├── WebUICommand.swift               # 命令入口和 Vapor 配置
├── Controllers/
│   └── StatusController.swift       # 状态 API 控制器
└── Resources/
    └── index.html                   # 单页前端应用
```

### 测试

```sh
# 运行所有测试（包括 WebUI 测试）
swift test --package-path edge --disable-xctest

# 仅 WebUI 测试
swift test --package-path edge --disable-xctest --filter WebUITests
```

### 故障排查

#### 端口被占用
```sh
# 查看端口占用
lsof -i :8765

# 使用其他端口
swift run --package-path edge pitchtrace-edge web-ui --bind 127.0.0.1:9000
```

#### 无法访问
- 确认防火墙允许端口访问
- 局域网访问需要使用 `--bind 0.0.0.0:8765`
- 检查 Mac mini 的 IP 地址：`ifconfig | grep inet`

#### 依赖安装失败
```sh
# 清理并重新解析依赖
cd edge
rm -rf .build
swift build
```

### 开发者文档

完整设计文档：[docs/08-edge-web-ui-design.md](../docs/08-edge-web-ui-design.md)

实施检查清单：[WebUI-Implementation-Checklist.md](WebUI-Implementation-Checklist.md)

进展报告：[WebUI-Phase1-Progress.md](WebUI-Phase1-Progress.md)
