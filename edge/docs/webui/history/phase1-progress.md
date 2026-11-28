# Web UI 阶段 1 进展报告

**日期**：2025-01-20  
**状态**：基础框架已创建，等待依赖包下载完成后进行首次构建测试

## 已完成工作

### 1. 包配置更新
- ✅ 在 `Package.swift` 添加 Vapor 4.99.0 依赖
- ✅ 配置 WebUI/Resources 为静态资源目录

### 2. 目录结构创建
```
Sources/PitchTraceEdge/WebUI/
├── WebUICommand.swift           # CLI 命令入口和参数解析
├── Controllers/
│   └── StatusController.swift   # 状态查询控制器
├── WebSocket/                   # (预留，阶段 2 实现)
└── Resources/
    └── index.html              # 单页前端应用
```

### 3. 核心文件实现

#### WebUICommand.swift
- ✅ 命令行参数解析（`--bind`、`--match-dir`、`--help`）
- ✅ Vapor 应用配置和启动
- ✅ 路由注册（`GET /`、`GET /api/status`、`GET /api/health`）
- ✅ 静态资源服务（index.html）
- ✅ 模拟数据生成（4 个相机的完整状态）
- ✅ 数据结构定义（SystemStatus、CameraStatus、Alert、DiskStatus）

#### StatusController.swift
- ✅ `getStatus` 方法返回系统状态
- ✅ 阶段 1 使用模拟数据
- ✅ 预留阶段 2 与 SystemMonitor 集成的接口

#### index.html
- ✅ 完整的 HTML/CSS/JavaScript 单页应用
- ✅ 响应式布局（桌面/平板/手机）
- ✅ 状态栏显示（比赛 ID、状态、磁盘使用）
- ✅ 相机网格（动态渲染，支持 2–8 台或更多）
- ✅ 告警面板（按级别分类和着色）
- ✅ 自动刷新（每 5 秒）
- ✅ 控制按钮（Start/Stop，阶段 2 实现功能）
- ✅ 错误处理和用户提示

### 4. 主入口更新
- ✅ 在 `PitchTraceEdge.swift` 注册 `web-ui` 子命令
- ✅ 错误处理和帮助信息

### 5. 测试用例
- ✅ 创建 `WebUITests.swift`
- ✅ 命令行参数解析测试（8 个用例）
- ✅ 数据结构序列化测试（3 个用例）
- ✅ 覆盖：默��参数、自定义参数、错误处理、JSON 编码

## 当前状态

### 构建进度
- 🔄 **Vapor 依赖包正在下载中**
  - 已获取 20+ 个依赖包
  - swift-nio、swift-crypto、swift-nio-ssl 等大型包仍在下载
  - 预计需要 5-10 分钟完成所有依赖下载
- 🔄 后台进程 PID: 69627
- 📝 构建日志：`/tmp/edge-build.log`

### 下一步操作

#### 立即待办（构建完成后）
1. ⏳ 检查构建错误并修复
2. ⏳ 运行测试：`swift test --package-path edge --disable-xctest`
3. ⏳ 确认所有现有测试通过（录像时序、曝光审计、媒体周期）
4. ⏳ 确认新增 WebUI 测试通过
5. ⏳ 手动测试：启动 Web UI 并访问浏览器

#### 阶段 1 完成验收
- [ ] `swift run pitchtrace-edge web-ui` 成功启动
- [ ] 访问 `http://127.0.0.1:8765` 看到 4 个模拟相机状态
- [ ] 状态栏显示正确信息
- [ ] 告警列表显示 2 条模拟告警
- [ ] 每 5 秒自动刷新
- [ ] 点击 Refresh 按钮手动刷新
- [ ] 所有现有测试保持通过

## 技术亮点

### 1. 轻量级设计
- 单文件 HTML（无构建依赖）
- 原生 JavaScript（无框架）
- 嵌入式静态资源（随可执行文件分发）

### 2. 灵活扩展
- 支持 2–8 台或更多相机（动态渲染）
- 预留 WebSocket 接口（阶段 2）
- 预留预览画面位置（阶段 5）

### 3. 用户友好
- 清晰的视觉层次
- 状态颜色编码（绿色=正常、红色=错误、橙色=警告）
- 自动刷新 + 手动刷新
- 错误提示横幅

### 4. 测试覆盖
- 命令行参数解析完整覆盖
- 边界情况测试（缺少参数、未知参数）
- JSON 序列化验证

## 预期文件大小

```
WebUICommand.swift      ~8KB   (含完整数据结构和路由)
StatusController.swift  ~2KB   (简单控制器)
index.html             ~12KB   (单文件应用)
WebUITests.swift       ~4KB   (11 个测试用例)
```

## 依赖关系

```
pitchtrace-edge (可执行文件)
└── Vapor 4.122.4
    ├── swift-nio (网络层)
    ├── swift-crypto (加密)
    ├── swift-nio-ssl (TLS)
    ├── websocket-kit (WebSocket 支持)
    ├── routing-kit (路由)
    ├── async-http-client (HTTP 客户端)
    ├── console-kit (终端输出)
    └── 其他传递依赖
```

## 命令示例

### 启动 Web UI（本地访问）
```sh
swift run --package-path edge pitchtrace-edge web-ui
# 访问 http://127.0.0.1:8765
```

### 启动 Web UI（局域网访问）
```sh
swift run --package-path edge pitchtrace-edge web-ui --bind 0.0.0.0:8765
# 局域网内其他设备访问 http://<Mac-IP>:8765
```

### 监控特定比赛目录
```sh
swift run --package-path edge pitchtrace-edge web-ui --match-dir /path/to/match
```

### 查看帮助
```sh
swift run --package-path edge pitchtrace-edge web-ui --help
```

## 已知限制（阶段 1）

1. **模拟数据**：所有状态都是硬编码的模拟数据
2. **控制按钮无功能**：Start/Stop 按钮点击后只显示提示
3. **无 WebSocket**：每 5 秒 HTTP 轮询，不是实时推送
4. **无预览画面**：相机卡片显示占位符
5. **无性能图表**：等待阶段 4 实现

## 阶段 2 预览

下一阶段将实现：
- 创建 `SystemMonitor` actor 聚合真实状态
- 实现 WebSocket 实时推送
- 实现 Start/Stop 控制功能
- 集成到采集模块
- 真实状态替代模拟数据

---

**构建完成后请运行**：
```sh
# 1. 检查构建结果
tail -50 /tmp/edge-build.log

# 2. 运行完整测试
cd /Users/xiaochunchen/github/pitchtrace-3d
swift test --package-path edge --disable-xctest

# 3. 手动启动 Web UI
swift run --package-path edge pitchtrace-edge web-ui

# 4. 浏览器访问
open http://127.0.0.1:8765
```
