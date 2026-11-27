import Foundation
@preconcurrency import Swifter

/// WebSocket 连接管理器
///
/// 负责管理所有活跃的 WebSocket 连接，提供广播、心跳和连接清理功能。
/// 使用 actor 确保线程安全的并发访问。
actor WebSocketConnectionManager {
    
    /// WebSocket 会话包装
    struct Connection: Sendable {
        let id: UUID
        let session: WebSocketSession
        var lastPongTime: Date
        
        init(id: UUID, session: WebSocketSession) {
            self.id = id
            self.session = session
            self.lastPongTime = Date()
        }
    }
    
    /// 活跃连接字典 (UUID → Connection)
    private var connections: [UUID: Connection] = [:]
    
    /// 添加新连接
    func add(id: UUID, session: WebSocketSession) {
        let connection = Connection(id: id, session: session)
        connections[id] = connection
        print("WebSocket: Client \(id) connected. Total: \(connections.count)")
    }
    
    /// 移除连接
    func remove(id: UUID) {
        connections.removeValue(forKey: id)
        print("WebSocket: Client \(id) disconnected. Total: \(connections.count)")
    }
    
    /// 更新客户端的最后 pong 时间
    func updatePongTime(id: UUID) {
        if var connection = connections[id] {
            connection.lastPongTime = Date()
            connections[id] = connection
        }
    }
    
    /// 广播消息到所有活跃连接
    func broadcast(_ message: String) {
        let activeCount = connections.count
        guard activeCount > 0 else { return }
        
        for (id, connection) in connections {
            connection.session.writeText(message)
        }
    }
    
    /// 发送心跳 ping 并清理超时连接
    ///
    /// - Parameter timeout: 超时时间（秒），默认 60 秒
    func sendPingAndCleanup(timeout: TimeInterval = 60) {
        let now = Date()
        let pingMessage = """
        {"type":"ping","timestamp":"\(ISO8601DateFormatter().string(from: now))"}
        """
        
        var toRemove: [UUID] = []
        
        for (id, connection) in connections {
            // 检查是否超时
            let elapsed = now.timeIntervalSince(connection.lastPongTime)
            if elapsed > timeout {
                print("WebSocket: Client \(id) timeout (\(Int(elapsed))s), closing...")
                toRemove.append(id)
                continue
            }
            
            // 发送 ping
            connection.session.writeText(pingMessage)
        }
        
        // 清理超时或失败的连接
        for id in toRemove {
            connections.removeValue(forKey: id)
        }
        
        if !toRemove.isEmpty {
            print("WebSocket: Cleaned up \(toRemove.count) stale connection(s). Total: \(connections.count)")
        }
    }
    
    /// 获取当前连接数
    func count() -> Int {
        return connections.count
    }
    
    /// 获取所有连接 ID（用于调试）
    func allConnectionIDs() -> [UUID] {
        return Array(connections.keys)
    }
}

/// WebSocket 消息类型
enum WebSocketMessageType: String, Codable {
    case initial    // 初始状态
    case update     // 状态更新
    case ping       // 心跳 ping
    case pong       // 心跳 pong
    case error      // 错误通知
}

/// WebSocket 消息封装
struct WebSocketMessage: Codable {
    let type: String
    let timestamp: String?
    let data: SystemStatus?
    let message: String?
    
    /// 创建初始状态消息
    static func initial(_ status: SystemStatus) -> String {
        let msg = WebSocketMessage(
            type: "initial",
            timestamp: status.timestamp,
            data: status,
            message: nil
        )
        return msg.toJSON()
    }
    
    /// 创建状态更新消息
    static func update(_ status: SystemStatus) -> String {
        let msg = WebSocketMessage(
            type: "update",
            timestamp: status.timestamp,
            data: status,
            message: nil
        )
        return msg.toJSON()
    }
    
    /// 创建错误消息
    static func error(_ message: String) -> String {
        let msg = WebSocketMessage(
            type: "error",
            timestamp: ISO8601DateFormatter().string(from: Date()),
            data: nil,
            message: message
        )
        return msg.toJSON()
    }
    
    /// 转换为 JSON 字符串
    func toJSON() -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(self),
              let json = String(data: data, encoding: .utf8) else {
            return "{}"
        }
        return json
    }
}
