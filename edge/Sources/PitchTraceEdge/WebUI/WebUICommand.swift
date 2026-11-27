import Foundation
import Swifter

/// Web UI 监控界面命令
///
/// 启动嵌入式 HTTP 服务器，提供设备状态监控、采集控制和告警面板。
/// 默认监听 127.0.0.1:8765，支持局域网访问（需显式指定 --bind 0.0.0.0:8765）。
struct WebUICommand {
    let bindAddress: String
    let matchDir: String?
    
    /// 解析命令行参数
    static func parse(arguments: [String]) throws -> WebUICommand {
        var bindAddress = "127.0.0.1:8765"
        var matchDir: String?
        
        var i = 0
        while i < arguments.count {
            let arg = arguments[i]
            switch arg {
            case "--bind":
                guard i + 1 < arguments.count else {
                    throw ValidationError("--bind requires an address:port argument")
                }
                bindAddress = arguments[i + 1]
                i += 2
            case "--match-dir":
                guard i + 1 < arguments.count else {
                    throw ValidationError("--match-dir requires a directory path")
                }
                matchDir = arguments[i + 1]
                i += 2
            case "--help", "-h":
                printHelp()
                throw ExitSuccess()
            default:
                throw ValidationError("Unknown argument: \(arg)")
            }
        }
        
        return WebUICommand(bindAddress: bindAddress, matchDir: matchDir)
    }
    
    /// 执行命令，启动 Web UI 服务器
    func run() async throws {
        print("Starting PitchTrace Edge Web UI...")
        print("Bind address: \(bindAddress)")
        if let dir = matchDir {
            print("Match directory: \(dir)")
        }
        
        // 解析地址和端口
        let components = bindAddress.split(separator: ":")
        guard components.count == 2,
              let hostname = components.first.map(String.init),
              let port = Int(components.last!) else {
            throw ValidationError("Invalid bind address format. Expected host:port")
        }
        
        // 创建 HTTP 服务器
        let server = HttpServer()
        
        // 注册路由
        server["/"] = { [self] request in
            return self.serveIndexHTML()
        }
        
        server["/api/status"] = { [self] request in
            let status = self.getMockStatus()
            return .ok(.json(status.toJSON()))
        }
        
        server["/api/health"] = { request in
            return .ok(.text("OK"))
        }
        
        // 启动服务器
        print("")
        print("✓ Web UI starting...")
        print("  → Binding to http://\(hostname):\(port)")
        print("  → Press Ctrl+C to stop")
        print("")
        
        do {
            if hostname == "0.0.0.0" {
                try server.start(in_port_t(port))
            } else {
                try server.start(in_port_t(port), forceIPv4: true, priority: .default)
            }
            
            print("✓ Server is running")
            print("  → Open http://\(hostname):\(port) in your browser")
            
            // 保持服务器运行
            try await Task.sleep(for: .seconds(365 * 24 * 3600)) // 1 year
        } catch {
            throw ValidationError("Failed to start server: \(error.localizedDescription)")
        }
    }
    
    /// 返回首页 HTML
    private func serveIndexHTML() -> HttpResponse {
        // 尝试多个可能的资源路径（SwiftPM 的 .copy() 会保留目录结构）
        let possiblePaths = [
            "Resources/index",  // Resources/index.html
            "index"             // index.html（如果直接在根目录）
        ]
        
        for path in possiblePaths {
            if let resourceURL = Bundle.module.url(
                forResource: path,
                withExtension: "html"
            ), let html = try? String(contentsOf: resourceURL, encoding: .utf8) {
                return .ok(.html(html))
            }
        }
        
        // 如果找不到，返回调试信息
        let debugInfo = """
        <!DOCTYPE html>
        <html>
        <head><title>Resource Not Found</title></head>
        <body>
        <h1>Resource Loading Debug</h1>
        <p>Bundle path: \(Bundle.module.bundlePath)</p>
        <p>Tried paths: \(possiblePaths.joined(separator: ", "))</p>
        </body>
        </html>
        """
        return .ok(.html(debugInfo))
    }
    
    /// 获取模拟状态数据（阶段 1 使用）
    private func getMockStatus() -> SystemStatus {
        SystemStatus(
            timestamp: ISO8601DateFormatter().string(from: Date()),
            matchId: "mock-match-2025-01-20",
            matchState: "idle",
            cameras: [
                CameraStatus(
                    cameraId: "A",
                    state: "active",
                    fps: 29.97,
                    frameCount: 12543,
                    lastFrameUtcUs: "1737381135123456",
                    exposureDriftMs: 0.8,
                    droppedFrames: 2,
                    queueDepth: 3,
                    calibrationId: "field-a-v3",
                    calibrationValid: true
                ),
                CameraStatus(
                    cameraId: "B",
                    state: "disconnected",
                    fps: 0,
                    frameCount: 12480,
                    lastFrameUtcUs: "1737381133000000",
                    exposureDriftMs: nil,
                    droppedFrames: 65,
                    queueDepth: 0,
                    calibrationId: "field-a-v3",
                    calibrationValid: true
                ),
                CameraStatus(
                    cameraId: "C",
                    state: "active",
                    fps: 25.0,
                    frameCount: 10452,
                    lastFrameUtcUs: "1737381135200000",
                    exposureDriftMs: 1.2,
                    droppedFrames: 0,
                    queueDepth: 2,
                    calibrationId: "field-a-v3",
                    calibrationValid: true
                ),
                CameraStatus(
                    cameraId: "D",
                    state: "active",
                    fps: 30.0,
                    frameCount: 12600,
                    lastFrameUtcUs: "1737381135150000",
                    exposureDriftMs: 0.5,
                    droppedFrames: 1,
                    queueDepth: 4,
                    calibrationId: "field-a-v3",
                    calibrationValid: true
                )
            ],
            alerts: [
                Alert(
                    level: "error",
                    cameraId: "B",
                    message: "Camera B disconnected since 2025-01-20T14:32:13Z",
                    timestamp: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-10))
                ),
                Alert(
                    level: "warning",
                    cameraId: "A",
                    message: "Queue depth exceeded 10 frames",
                    timestamp: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-5))
                )
            ],
            disk: DiskStatus(
                totalBytes: 2_000_000_000_000,
                usedBytes: 450_000_000_000,
                availableBytes: 1_550_000_000_000,
                recordingRateBytes: 4_000_000,
                estimatedHoursRemaining: 107.6
            )
        )
    }
    
    static func printHelp() {
        print("""
        USAGE: pitchtrace-edge web-ui [OPTIONS]
        
        Start the Web UI monitoring server for PitchTrace Edge.
        
        OPTIONS:
            --bind <host:port>      Bind address (default: 127.0.0.1:8765)
                                    Use 0.0.0.0:8765 for LAN access
            --match-dir <path>      Match directory to monitor (optional)
            --help, -h              Show this help message
        
        EXAMPLES:
            # Start on localhost
            pitchtrace-edge web-ui
            
            # Allow LAN access
            pitchtrace-edge web-ui --bind 0.0.0.0:8765
            
            # Monitor a specific match
            pitchtrace-edge web-ui --match-dir /path/to/match
        """)
    }
}

// MARK: - 数据结构

struct SystemStatus: Codable {
    let timestamp: String
    let matchId: String
    let matchState: String
    let cameras: [CameraStatus]
    let alerts: [Alert]
    let disk: DiskStatus
    
    /// 转换为 JSON 对象（用于 Swifter 响应）
    func toJSON() -> Any {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(self),
              let json = try? JSONSerialization.jsonObject(with: data) else {
            return [:]
        }
        return json
    }
}

struct CameraStatus: Codable {
    let cameraId: String
    let state: String
    let fps: Double
    let frameCount: Int
    let lastFrameUtcUs: String
    let exposureDriftMs: Double?
    let droppedFrames: Int
    let queueDepth: Int
    let calibrationId: String
    let calibrationValid: Bool
}

struct Alert: Codable {
    let level: String
    let cameraId: String?
    let message: String
    let timestamp: String
}

struct DiskStatus: Codable {
    let totalBytes: Int64
    let usedBytes: Int64
    let availableBytes: Int64
    let recordingRateBytes: Int
    let estimatedHoursRemaining: Double
}

// MARK: - 错误类型

struct ValidationError: Error, CustomStringConvertible {
    let message: String
    
    init(_ message: String) {
        self.message = message
    }
    
    var description: String { message }
}

struct ExitSuccess: Error {}
