# Edge Web UI 阶段 3 实施计划：WebSocket 实时推送

**目标**: 将轮询替换为服务器主动推送，实现真正的实时监控  
**预计时间**: 2-3 小时  
**前置条件**: ✅ 阶段 1（HTTP 服务器）+ ✅ 阶段 2（实时数据集成）

## 一、技术方案

### 1.1 架构设计

```
┌─────────────────┐
│  SystemMonitor  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Background     │  检测状态变化
│     Task        │  ├─ 更新 StatusCache (现有)
└────────┬────────┘  └─ 通知 WebSocket 管理器 (新增)
         │
         ├────────────────────┐
         ▼                    ▼
┌─────────────────┐  ┌──────────────────┐
│  StatusCache    │  │  WSConnectionMgr │
│  (HTTP 轮询用)   │  │  (WebSocket)     │
└─────────────────┘  └────────┬─────────┘
                              │
                              ├─ Client 1
                              ├─ Client 2
                              └─ Client N
```

### 1.2 关键组件

#### A. WebSocket 连接管理器
```swift
actor WebSocketConnectionManager {
    private var connections: [UUID: WebSocketSession]
    
    func add(_ ws: WebSocketSession) async
    func remove(_ id: UUID) async
    func broadcast(_ message: String) async
    func count() async -> Int
}
```

#### B. 心跳机制
- 服务器每 30 秒发送 ping
- 客户端响应 pong
- 60 秒无响应自动断开

#### C. 增量推送优化
- 仅在状态真正变化时推送
- 记录上次发送的状态
- 比较差异后决定是否广播

### 1.3 消息协议

#### 服务器 → 客户端

**1. 初始状态 (连接时)**
```json
{
  "type": "initial",
  "data": { /* SystemStatus */ }
}
```

**2. 状态更新**
```json
{
  "type": "update",
  "timestamp": "2026-11-27T...",
  "data": { /* SystemStatus */ }
}
```

**3. 心跳**
```json
{
  "type": "ping",
  "timestamp": "2026-11-27T..."
}
```

**4. 错误通知**
```json
{
  "type": "error",
  "message": "Failed to read match data"
}
```

#### 客户端 → 服务器

**1. 心跳响应**
```json
{
  "type": "pong"
}
```

**2. 订阅配置（未来扩展）**
```json
{
  "type": "subscribe",
  "cameras": ["A", "B"],
  "alertLevel": "warning"
}
```

## 二、实现步骤

### 步骤 1: WebSocket 连接管理器 (30 分钟)

**文件**: `WebSocketConnectionManager.swift`

**任务**:
- [ ] 创建 `WebSocketSession` 结构（包装 Swifter 的 WebSocket）
- [ ] 实现 `WebSocketConnectionManager` actor
- [ ] 添加 `add`、`remove`、`broadcast` 方法
- [ ] 连接追踪与清理逻辑

**测试点**:
- 添加/移除连接正确更新计数
- broadcast 发送到所有活跃连接
- 断开的连接自动移除

### 步骤 2: 后台任务改造 (30 分钟)

**文件**: `WebUICommand.swift`

**任务**:
- [ ] 修改后台任务：检测状态变化
- [ ] 状态变化时调用 `connectionManager.broadcast()`
- [ ] 保留 StatusCache 更新（HTTP 兼容）
- [ ] 添加错误处理与日志

**变更点**:
```swift
// 现有逻辑
let status = try await SystemMonitor.shared.currentStatus()
StatusCache.shared.update(status)

// 新增逻辑
if status != lastBroadcastedStatus {
    await connectionManager.broadcast(status)
    lastBroadcastedStatus = status
}
```

### 步骤 3: WebSocket 路由端点 (30 分钟)

**文件**: `WebUICommand.swift` (run 方法)

**任务**:
- [ ] 添加 `/api/live` WebSocket 路由
- [ ] 连接建立时发送初始状态
- [ ] 注册到 `connectionManager`
- [ ] 处理客户端消息（pong、订阅）
- [ ] 断开时清理连接

**示例代码**:
```swift
server["/api/live"] = { request in
    return .websocket { ws in
        let id = UUID()
        let session = WebSocketSession(id: id, socket: ws)
        
        // 发送初始状态
        let initial = StatusCache.shared.current
        ws.send(json: ["type": "initial", "data": initial])
        
        // 注册连接
        await connectionManager.add(session)
        
        // 监听断开
        ws.onClose = {
            await connectionManager.remove(id)
        }
    }
}
```

### 步骤 4: 心跳与连接监控 (30 分钟)

**文件**: `WebSocketConnectionManager.swift`

**任务**:
- [ ] 启动心跳 Task（30 秒间隔）
- [ ] 追踪每个连接的最后 pong 时间
- [ ] 60 秒无响应自动断开
- [ ] 断开时发送日志

**实现**:
```swift
Task {
    while !Task.isCancelled {
        try? await Task.sleep(for: .seconds(30))
        await connectionManager.sendPingAndCleanup()
    }
}
```

### 步骤 5: 前端集成 (30 分钟)

**文件**: `index.html` (JavaScript 部分)

**任务**:
- [ ] 添加 WebSocket 连接逻辑
- [ ] 处理 `initial`、`update`、`ping` 消息
- [ ] 自动重连（指数退避）
- [ ] 降级到 HTTP 轮询（WebSocket 不可用时）
- [ ] UI 显示连接状态

**示例代码**:
```javascript
class RealtimeMonitor {
    constructor() {
        this.ws = null;
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000;
        this.connect();
    }
    
    connect() {
        this.ws = new WebSocket('ws://127.0.0.1:8765/api/live');
        
        this.ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === 'initial' || msg.type === 'update') {
                this.updateUI(msg.data);
            } else if (msg.type === 'ping') {
                this.ws.send(JSON.stringify({type: 'pong'}));
            }
        };
        
        this.ws.onclose = () => {
            console.log('WebSocket closed, reconnecting...');
            setTimeout(() => this.connect(), this.reconnectDelay);
            this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
        };
        
        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectDelay = 1000;
        };
    }
}
```

### 步骤 6: 测试与验证 (30 分钟)

**测试用例**:
- [ ] 单客户端连接与推送
- [ ] 多客户端同时连接
- [ ] 客户端断开后不再推送
- [ ] 心跳超时自动清理
- [ ] WebSocket 不可用时 HTTP 轮询降级
- [ ] 状态无变化时不推送
- [ ] 所有 38 个单元测试仍然通过

**工具**:
```bash
# 测试 WebSocket 连接
websocat ws://127.0.0.1:8765/api/live

# 或用 Node.js
node -e "const ws = new (require('ws'))('ws://127.0.0.1:8765/api/live'); ws.on('message', console.log);"
```

## 三、优化点

### 3.1 增量更新（可选）

只发送变化的字段，减少带宽：

```json
{
  "type": "partial",
  "changes": {
    "cameras.0.fps": 29.98,
    "alerts": [/* new alert */]
  }
}
```

### 3.2 消息压缩（可选）

Swifter 支持 WebSocket 压缩扩展（permessage-deflate）

### 3.3 订阅过滤（可选）

客户端指定只接收特定相机或告警级别的更新

## 四、兼容性保证

### 4.1 HTTP 轮询保留

- `/api/status` 端点保持不变
- 旧客户端（无 WebSocket）仍可正常工作
- 浏览器不支持 WebSocket 时自动降级

### 4.2 现有测试不受影响

- WebSocket 是附加功能，不修改核心逻辑
- StatusCache 仍然更新（HTTP 路由依赖）
- 所有 38 个测试应保持通过

## 五、验收标准

- [ ] WebSocket 连接成功建立（`/api/live`）
- [ ] 初始状态立即推送
- [ ] 状态变化时 <100ms 推送到客户端
- [ ] 心跳正常工作（30s ping, 60s timeout）
- [ ] 多客户端同时连接无问题
- [ ] 断开连接自动清理
- [ ] 前端自动重连（指数退避）
- [ ] HTTP 轮询降级正常
- [ ] 所有 38 个单元测试通过
- [ ] 性能：支持 10+ 并发连接，<1% CPU

## 六、文件清单

| 文件 | 操作 | 说明 |
|-----|------|------|
| `WebSocketConnectionManager.swift` | 新建 | WebSocket 连接管理 actor |
| `WebUICommand.swift` | 修改 | 添加 `/api/live` 路由，改造后台任务 |
| `SystemStatus.swift` | 修改 | 添加 `Equatable` 以比较状态变化 |
| `index.html` | 修改 | 添加 WebSocket 客户端逻辑 |
| `WebUI-Phase3-Implementation.md` | 新建 | 实施记录与调试笔记 |

## 七、时间预算

| 任务 | 预计时间 | 累计 |
|-----|---------|------|
| WebSocket 连接管理器 | 30 分钟 | 0:30 |
| 后台任务改造 | 30 分钟 | 1:00 |
| WebSocket 路由端点 | 30 分钟 | 1:30 |
| 心跳与连接监控 | 30 分钟 | 2:00 |
| 前端集成 | 30 分钟 | 2:30 |
| 测试与验证 | 30 分钟 | 3:00 |
| **总计** | **3 小时** | |

## 八、风险与缓解

| 风险 | 影响 | 缓解措施 |
|-----|------|---------|
| Swifter WebSocket API 不熟悉 | 中 | 先查阅文档与示例 |
| Swift 6 并发错误 | 高 | 使用 actor 隔离，避免 @unchecked |
| 多客户端状态同步问题 | 中 | 通过 actor 串行化所有连接操作 |
| 前端 WebSocket 断线处理 | 低 | 指数退避重连 + HTTP 降级 |
| 现有测试失败 | 高 | 每步修改后立即跑测试 |

## 九、后续增强（Phase 4+）

- **性能监控面板**: 实时 FPS、队列深度曲线
- **告警历史**: 分页查询最近 100 条告警
- **配置 API**: `POST /api/config` 动态调整参数
- **多比赛支持**: `/api/matches/{matchId}/live`
- **权限控制**: 基于 token 的订阅鉴权
- **录像回放**: WebSocket 推送历史帧序列

---

**开始时间**: 准备就绪  
**预计完成**: 3 小时内  
**验收方式**: 浏览器连接 ws://127.0.0.1:8765/api/live，实时接收更新
