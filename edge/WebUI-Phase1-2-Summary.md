# Web UI 阶段 1+2 实施总结

**完成时间**: 2026-11-27  
**总耗时**: ~5 小时

---

## ✅ 阶段 1：基础框架（已完成）

### 交付成果
- ✅ HTTP 服务器（Swifter 1.5.0）
- ✅ 完整前端界面（15KB 单文件 HTML）
- ✅ 3 个 API 端点：`/`、`/api/status`、`/api/health`
- ✅ 数据结构：`SystemStatus`, `CameraStatus`, `Alert`, `DiskStatus`
- ✅ 11 个测试用例 + 27 个原有测试全部通过（38/38）
- ✅ 技术设计文档（11 章）

### 验收确认
```bash
# 启动服务器
swift run --package-path edge pitchtrace-edge web-ui

# 测试端点
curl http://127.0.0.1:8765/api/health  # → OK
curl http://127.0.0.1:8765/api/status  # → 完整 JSON (模拟数据)
open http://127.0.0.1:8765              # → 4 机位网格 + 告警面板
```

---

## 🔄 阶段 2：实时数据集成（部分完成）

### 已完成工作
1. ✅ **SystemMonitor 类**（`edge/Sources/PitchTraceEdge/WebUI/SystemMonitor.swift`，400+ 行）
   - `actor` 隔离，线程安全
   - 读取 `match-dir` 下的 `match-config.json`
   - 扫描机位目录结构（`camera_*/epoch_*/index.ndjson`）
   - 计算真实磁盘使用（通过 `FileManager`）
   - 检测进程状态（通过 `ps aux`）
   - 生成告警（断线、过时、队列深度）
   - 2 秒缓存机制

2. ✅ **数据收集功能**
   - `readMatchInfo()` - 读取比赛配置
   - `collectCameraStatus()` - 扫描机位目录
   - `readMediaIndex()` - 解析 `.ndjson` 索引
   - `calculateFPS()` - 根据帧时间戳计算帧率
   - `generateAlerts()` - 根据状态生成告警
   - `collectDiskStatus()` - 真实磁盘查询

3. ✅ **集成准备**
   - `--match-dir` 参数已传递到 `SystemMonitor.shared.configure()`
   - 降级逻辑：失败时回退到模拟数据

### ⚠️ 阻塞问题

**Swift 6 并发安全与 Swifter 同步 API 冲突**

**根本原因**:
- **SystemMonitor** 是 `actor`，方法是 `async`
- **Swifter** 的路由处理器是同步函数：`(HttpRequest) -> HttpResponse`
- Swift 6 的严格并发检查不允许在同步上下文中直接等待异步任务

**尝试方案**:
1. ❌ `DispatchGroup.wait()` + `Task` - 编译器报错 "sending value of non-Sendable type"
2. ❌ `nonisolated(unsafe) var` - 仍然报错数据竞争风险
3. ❌ `@preconcurrency import Swifter` - 不影响核心问题
4. ❌ 轮询等待（`while capturedResponse == nil`）- 导致 HTTP 超时（5 秒）

**技术债**:
代码中已添加 TODO 注释（`WebUICommand.swift` 第 73-80 行）说明问题和可能的解决方案。

### 🔧 解决方案（待实施）

#### 方案 A：后台缓存任务（推荐，1-2 小时）
```swift
// 在 run() 中启动后台任务
Task.detached {
    while true {
        try? await Task.sleep(for: .seconds(2))
        let _ = try? await SystemMonitor.shared.currentStatus()  // 更新缓存
    }
}

// 路由处理器读取缓存（同步）
server["/api/status"] = { [self] request in
    let status = await SystemMonitor.shared.getCachedStatus() ?? self.getMockStatus()
    return .ok(.json(status.toJSON()))
}
```

需要在 `SystemMonitor` 中添加：
- `nonisolated func getCachedStatus() -> SystemStatus?` - 返回最新缓存
- 确保缓存读取是线程安全的

#### 方案 B：切换到 async HTTP 框架（3-4 小时）
替换 Swifter 为支持 async/await 的框架：
- **AsyncHTTPServer** (swift-server 组织)
- **Hummingbird** (新兴，原生 async/await)

优势：彻底解决问题，更符合现代 Swift 实践  
缺点：需要重写路由注册逻辑

#### 方案 C：降级到 Swift 5 模式（不推荐）
在 `Package.swift` 中设置 `.swiftLanguageVersion(.v5)`，禁用严格并发检查。

---

## 📊 当前状态

| 组件 | 状态 | 说明 |
| --- | --- | --- |
| HTTP 服务器 | ✅ 运行中 | Swifter 1.5.0，127.0.0.1:8765 |
| 前端界面 | ✅ 完整 | 4 机位网格 + 告警面板，15KB |
| API 端点 | ✅ 正常 | `/api/health` (OK), `/api/status` (JSON) |
| 数据来源 | 🟡 模拟 | 使用 `getMockStatus()`，硬编码数据 |
| SystemMonitor | ✅ 实现 | 完整功能，但未集成到路由 |
| 测试覆盖 | ✅ 38/38 | 所有测试通过 |
| 文档 | ✅ 完整 | 技术设计 + 实施记录 |

---

## 📁 文件清单

### 新增文件
```
edge/Sources/PitchTraceEdge/WebUI/
├── WebUICommand.swift           217 行，HTTP 服务器 + 路由 + 模拟数据
├── SystemMonitor.swift          418 行，实时数据收集（未集成）
└── Resources/
    └── index.html               15KB，完整前端界面

edge/Tests/PitchTraceEdgeTests/
└── WebUITests.swift             11 个测试用例

docs/
└── 08-edge-web-ui-design.md     技术设计（11 章）

edge/
├── WebUI-Phase1-Complete.md     阶段 1 完成报告
├── WebUI-Phase1-Status.md       问题跟踪与解决记录
├── WebUI-Phase1-2-Summary.md    本文件
└── WebUI-Implementation-Checklist.md  实施检查清单
```

### 修改文件
```
edge/Package.swift               Vapor → Swifter 依赖
edge/Sources/PitchTraceEdge/PitchTraceEdge.swift  注册 web-ui 命令
```

---

## 🎯 下一步行动

### 立即可选（按优先级）

1. **完成阶段 2 集成**（方案 A，1-2 小时）
   - 在 `SystemMonitor` 添加 `getCachedStatus()`
   - 启动后台任务定期更新缓存
   - 修改路由从缓存读取
   - 验证真实比赛目录

2. **添加 WebSocket 支持**（阶段 3，2-3 小时）
   - Swifter 自带 WebSocket 支持
   - 实现 `/api/stream` 端点
   - 前端修改：从轮询改为 WebSocket
   - 推送频率：1-2 Hz

3. **添加控制端点**（阶段 4，2-3 小时）
   - `POST /api/match/start` - 启动采集
   - `POST /api/match/stop` - 停止采集
   - `POST /api/calibration/reset` - 重新标定
   - 需要身份认证（阶段 5）

4. **集成测试与文档**
   - 创建完整的端到端测试脚本
   - 录制演示视频
   - 更新用户手册

---

## 📝 经验教训

### 技术选型
- ✅ **Swifter 轻量简单**，适合阶段 1 快速验证
- ⚠️ **同步 API 限制**了与现代 Swift 并发模型的集成
- 💡 **下次项目**：优先选择原生支持 async/await 的框架

### Swift 6 并发
- ✅ **严格检查**防止数据竞争，提高代码质量
- ⚠️ **迁移成本**对旧库（如 Swifter）较高
- 💡 **解决方案**：预先缓存 + 后台任务，而非同步桥接

### 开发流程
- ✅ **分阶段交付**（1 → 2 → 3）降低风险
- ✅ **完整测试覆盖**确保重构安全
- 💡 **技术债文档**比停滞不前更重要

---

## ✅ 验收确认

### 阶段 1 验收标准（全部通过）
- [x] 命令行启动 `pitchtrace-edge web-ui`
- [x] 绑定 127.0.0.1:8765
- [x] 局域网访问 `--bind 0.0.0.0:8765`
- [x] GET / 返回 index.html
- [x] GET /api/status 返回 JSON
- [x] GET /api/health 返回 OK
- [x] 4 机位网格 + 告警面板
- [x] 所有测试通过（38/38）

### 阶段 2 验收标准（部分通过）
- [x] SystemMonitor 类实现
- [x] 读取 match-dir 配置
- [x] 扫描机位目录
- [x] 计算真实磁盘状态
- [x] 检测进程状态
- [x] 生成告警
- [ ] ⚠️ 集成到 HTTP 路由（技术阻塞）

---

## 🎉 成果总结

**已交付**:
- ✅ 完整可运行的 Web UI 监控界面
- ✅ 生产级别的代码质量（测试覆盖 + 文档）
- ✅ 实时数据收集引擎（SystemMonitor）
- ✅ 清晰的技术债记录和解决路径

**价值**:
- **立即可用**：演示和开发环境监控
- **扩展就绪**：SystemMonitor 可在解决并发问题后立即启用
- **架构清晰**：分层设计便于后续迭代

**时间分配**:
- 阶段 1 基础框架：~3 小时（含架构调整）
- 阶段 2 数据集成：~2 小时（实现但未完全集成）

**投资回报**:
- 2-3 天的 GUI 工作量 → 5 小时 Web UI
- 跨平台、零安装、远程访问
- 为未来的 WebSocket 和控制功能奠定基础

---

**下一里程碑**: 完成方案 A（后台缓存任务）→ 阶段 2 完成 → 进入阶段 3 (WebSocket)

**预计完成时间**: 1-2 小时

**相关文档**:
- 技术设计：`docs/08-edge-web-ui-design.md`
- 阶段 1 报告：`edge/WebUI-Phase1-Complete.md`
- 实施检查清单：`edge/WebUI-Implementation-Checklist.md`
- 本总结：`edge/WebUI-Phase1-2-Summary.md`
