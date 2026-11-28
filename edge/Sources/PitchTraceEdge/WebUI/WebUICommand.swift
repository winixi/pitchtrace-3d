import Foundation
@preconcurrency import Swifter

/// 线程安全的状态缓存
/// 由后台任务定期更新，HTTP 路由同步读取
final class StatusCache: @unchecked Sendable {
    static let shared = StatusCache()

    private var _current: SystemStatus
    private let lock = NSLock()

    var current: SystemStatus {
        lock.lock()
        defer { lock.unlock() }
        return _current
    }

    private init() {
        // 首次汇总完成前不显示任何机位，避免把示例数据当成现场状态。
        _current = readCaptureStatus(root: nil, setup: nil, process: CaptureProcessState(), now: Date())
    }

    func update(_ status: SystemStatus) {
        lock.lock()
        defer { lock.unlock() }
        _current = status
    }
}

/// Web UI 监控界面命令
///
/// 启动嵌入式 HTTP 服务器，提供设备状态监控、采集控制和告警面板。
/// 默认监听 127.0.0.1:8765，支持局域网访问（需显式指定 --bind 0.0.0.0:8765）。
struct WebUICommand {
    let bindAddress: String
    let matchDir: String?
    var captureConfig: String? = nil
    var runDir: String? = nil
    
    /// 解析命令行参数
    static func parse(arguments: [String]) throws -> WebUICommand {
        var bindAddress = "127.0.0.1:8765"
        var matchDir: String?
        var captureConfig: String?
        var runDir: String?
        
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
            case "--capture-config":
                guard i + 1 < arguments.count else {
                    throw ValidationError("--capture-config requires a capture config path")
                }
                captureConfig = arguments[i + 1]
                i += 2
            case "--run-dir":
                guard i + 1 < arguments.count else {
                    throw ValidationError("--run-dir requires a stream-match run directory")
                }
                runDir = arguments[i + 1]
                i += 2
            case "--help", "-h":
                printHelp()
                throw ExitSuccess()
            default:
                throw ValidationError("Unknown argument: \(arg)")
            }
        }
        
        if captureConfig != nil && matchDir == nil {
            throw ValidationError("--capture-config requires --match-dir (the capture root directory)")
        }
        return WebUICommand(bindAddress: bindAddress, matchDir: matchDir, captureConfig: captureConfig, runDir: runDir)
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
        
        // 配置系统监控器；--match-dir 是 capture 的输出根目录
        let matchDirURL = matchDir.map { URL(fileURLWithPath: $0, isDirectory: true).standardizedFileURL }
        let setup = try captureConfig.map {
            try CaptureSetup.load(URL(fileURLWithPath: $0).standardizedFileURL)
        }
        var controller: CaptureController?
        if let setup, let root = matchDirURL {
            guard let executable = Bundle.main.executableURL else {
                throw ValidationError("Cannot locate the pitchtrace-edge executable")
            }
            controller = CaptureController(executable: executable, configURL: setup.configURL, root: root)
        }
        let previews = matchDirURL.map { PreviewRenderer(root: $0) }
        let runDirURL = runDir.map { URL(fileURLWithPath: $0, isDirectory: true).standardizedFileURL }
        let processingController = runDirURL.map { ProcessingController(runDir: $0) }
        await SystemMonitor.shared.configure(root: matchDirURL, setup: setup, controller: controller, runDir: runDirURL)
        StatusCache.shared.update(await SystemMonitor.shared.currentStatus())
        
        // 创建 WebSocket 连接管理器
        let wsManager = WebSocketConnectionManager()
        
        // 启动后台任务：定期更新状态缓存并推送 WebSocket
        // 使用后台 Task 避免阻塞 HTTP 服务器主线程
        var lastBroadcastedStatus: SystemStatus?
        
        let updateTask = Task.detached {
            while !Task.isCancelled {
                // 从采集目录汇总最新状态，更新线程安全的缓存（供 HTTP 路由使用）
                let now = Date()
                let status = await SystemMonitor.shared.currentStatus(now: now)
                StatusCache.shared.update(status)
                MetricsHistory.shared.record(status, now: now, resources: ResourceSampler.shared.sample())

                // 时间戳之外有变化时才广播到 WebSocket 客户端
                if status.ignoringTimestamp != lastBroadcastedStatus?.ignoringTimestamp {
                    await wsManager.broadcast(WebSocketMessage.update(status))
                    lastBroadcastedStatus = status
                }

                // 文档 08 第 3.5 节：每秒推送一次变化
                try? await Task.sleep(for: .seconds(1))
            }
        }
        
        // 启动心跳任务：每 30 秒发送 ping 并清理超时连接
        let heartbeatTask = Task.detached {
            // 首次延迟 30 秒启动
            try? await Task.sleep(for: .seconds(30))
            
            while !Task.isCancelled {
                await wsManager.sendPingAndCleanup(timeout: 60)
                try? await Task.sleep(for: .seconds(30))
            }
        }
        
        // 创建 HTTP 服务器
        let server = HttpServer()
        
        // 注册路由
        server["/"] = { [self] request in
            return self.serveIndexHTML()
        }
        
        server["/api/status"] = { request in
            // 从缓存读取当前状态（同步操作，无需 await）
            let status = self.getCurrentStatus()
            return .ok(.json(status.toJSON()))
        }
        
        server["/api/health"] = { request in
            return .ok(.text("OK"))
        }

        server.GET["/api/metrics"] = { request in
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            guard let body = try? encoder.encode(MetricsHistory.shared.report(now: Date())) else {
                return .internalServerError
            }
            return .raw(200, "OK", ["Content-Type": "application/json", "Cache-Control": "no-store"]) { try $0.write(body) }
        }

        server.POST["/api/control/start"] = { request in
            Self.control(request, controller: controller, unavailable: Self.captureUnavailable) { try $0.start() }
        }

        server.POST["/api/control/stop"] = { request in
            Self.control(request, controller: controller, unavailable: Self.captureUnavailable) { try $0.stop() }
        }

        server.POST["/api/control/pause"] = { request in
            Self.control(request, controller: processingController, unavailable: Self.processingUnavailable) { try $0.pause() }
        }

        server.POST["/api/control/resume"] = { request in
            Self.control(request, controller: processingController, unavailable: Self.processingUnavailable) { try $0.resume() }
        }

        server.GET["/api/preview/:cameraId"] = { request in
            guard let cameraId = request.params[":cameraId"], isEdgeIdentifier(cameraId),
                  let jpeg = previews?.jpeg(cameraId: cameraId) else {
                return .notFound
            }
            return .raw(200, "OK", ["Content-Type": "image/jpeg", "Cache-Control": "no-store"]) {
                try $0.write(jpeg)
            }
        }
        
        // WebSocket 实时推送端点
        server["/api/live"] = websocket(
            text: { session, text in
                // 处理客户端发送的文本消息
                Task {
                    if let data = text.data(using: .utf8),
                       let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let type = json["type"] as? String {
                        
                        if type == "pong" {
                            // 客户端响应心跳
                            // 注意：这里无法直接获取 session 的 ID，需要在 connected 时建立映射
                            // 暂时跳过，心跳超时由 sendPingAndCleanup 处理
                        }
                    }
                }
            },
            connected: { session in
                // 新客户端连接
                Task {
                    let id = UUID()
                    
                    // 发送初始状态
                    let initialStatus = StatusCache.shared.current
                    let initialMessage = WebSocketMessage.initial(initialStatus)
                    session.writeText(initialMessage)
                    
                    // 注册到连接管理器
                    await wsManager.add(id: id, session: session)
                    
                    print("WebSocket: Client connected, sent initial status")
                }
            },
            disconnected: { session in
                // 客户端断开
                // 注意：由于无法直接从 session 获取我们的 UUID，
                // 心跳超时会自动清理断开的连接
                Task {
                    print("WebSocket: Client disconnected")
                }
            }
        )
        
        // 启动服务器
        print("")
        print("✓ Web UI starting...")
        print("  → Binding to http://\(hostname):\(port)")
        print("  → Press Ctrl+C to stop")
        print("")
        
        do {
            // Swifter 未设监听地址时绑定全部网卡，需显式指定才能让默认值只在回环上监听。
            server.listenAddressIPv4 = hostname
            try server.start(in_port_t(port), forceIPv4: true, priority: .default)
            
            print("✓ Server is running")
            print("  → Open http://\(hostname):\(port) in your browser")
            if let runDirURL {
                print("  → Processing: \(runDirURL.path)/stream-state.json")
            }
            if let matchDirURL {
                print("  → Monitoring: \(matchDirURL.path)")
                print(controller == nil ? "  → Read-only (pass --capture-config to enable Start/Stop)"
                                        : "  → Start/Stop runs capture with \(captureConfig!)")
            } else {
                print("  → No match directory specified")
            }
            
            // 保持服务器运行
            defer {
                updateTask.cancel()
                heartbeatTask.cancel()
            }
            try await Task.sleep(for: .seconds(365 * 24 * 3600)) // 1 year
        } catch {
            updateTask.cancel()
            heartbeatTask.cancel()
            throw ValidationError("Failed to start server: \(error.localizedDescription)")
        }
    }
    
    /// 执行控制请求。
    ///
    /// 控制接口无认证（文档 08 第 7.1 节）。要求自定义请求头，并拒绝与 Host 不同源的 Origin，
    /// 使其他网页无法借场务浏览器发起跨站 POST。
    static let captureUnavailable = "Start web-ui with --capture-config and --match-dir to enable capture control"
    static let processingUnavailable = "Start web-ui with --run-dir to pause and resume stream-match"

    static func control<Controller>(_ request: HttpRequest, controller: Controller?, unavailable: String,
                                    action: (Controller) throws -> String) -> HttpResponse {
        func reply(_ status: Int, _ success: Bool, _ message: String) -> HttpResponse {
            let body = (try? JSONSerialization.data(withJSONObject: ["success": success, "message": message])) ?? Data()
            let reason = [200: "OK", 403: "Forbidden", 409: "Conflict", 500: "Internal Server Error", 501: "Not Implemented"][status] ?? ""
            return .raw(status, reason, ["Content-Type": "application/json"]) { try $0.write(body) }
        }
        guard isSameOriginControl(headers: request.headers) else {
            return reply(403, false, "Control requests need the X-PitchTrace-Control header from the Web UI origin")
        }
        guard let controller else {
            return reply(501, false, unavailable)
        }
        do {
            let message = try action(controller)
            print("Control: \(request.path) from \(request.address ?? "?") → \(message)")
            return reply(200, true, message)
        } catch let conflict as CaptureControlConflict {
            return reply(409, false, conflict.description)
        } catch {
            return reply(500, false, "\(error)")
        }
    }

    /// Swifter 已把请求头名转为小写。
    static func isSameOriginControl(headers: [String: String]) -> Bool {
        guard headers["x-pitchtrace-control"] == "1" else { return false }
        guard let origin = headers["origin"] else { return true }
        guard let host = headers["host"] else { return false }
        return origin == "http://\(host)" || origin == "https://\(host)"
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
    
    /// 从缓存获取当前状态（同步读取，用于 HTTP 路由）
    private nonisolated func getCurrentStatus() -> SystemStatus {
        // 直接从线程安全的缓存读取
        return StatusCache.shared.current
    }
    
    static func printHelp() {
        print("""
        USAGE: pitchtrace-edge web-ui [OPTIONS]
        
        Start the Web UI monitoring server for PitchTrace Edge.
        
        OPTIONS:
            --bind <host:port>      Bind address (default: 127.0.0.1:8765)
                                    Use 0.0.0.0:8765 for LAN access
            --match-dir <path>      Capture root directory to monitor (optional)
            --capture-config <file> Capture config; enables Start/Stop (needs --match-dir)
            --run-dir <path>        stream-match run directory; shows inference backlog
                                    and pending uploads, records /api/metrics, and
                                    enables Pause/Resume of processing
            --help, -h              Show this help message
        
        EXAMPLES:
            # Start on localhost
            pitchtrace-edge web-ui
            
            # Allow LAN access
            pitchtrace-edge web-ui --bind 0.0.0.0:8765
            
            # Monitor a capture directory (read-only)
            pitchtrace-edge web-ui --match-dir /path/to/session

            # Monitor and control capture
            pitchtrace-edge web-ui --match-dir /path/to/session --capture-config cameras.json
        """)
    }
}

// MARK: - 数据结构

struct SystemStatus: Codable, Equatable {
    let timestamp: String
    /// 采集目录 match.json 或采集配置中的比赛 ID，均缺省时为空。
    let matchId: String?
    /// idle | capturing | paused | stopping | stopped | unresponsive。paused 表示录像中而处理已暂停。
    var matchState: String
    /// 是否提供了采集配置，可以开始／结束采集。
    let controlsEnabled: Bool
    let cameras: [CameraStatus]
    var alerts: [Alert]
    let disk: DiskStatus
    /// 指定 --run-dir 且 stream-state.json 存在时给出。
    var processing: ProcessingStatus? = nil

    var ignoringTimestamp: SystemStatus {
        SystemStatus(timestamp: "", matchId: matchId, matchState: matchState, controlsEnabled: controlsEnabled,
                     cameras: cameras, alerts: alerts.map { Alert(level: $0.level, cameraId: $0.cameraId, message: $0.message, timestamp: "") },
                     disk: disk, processing: processing.map {
                         var copy = $0
                         copy.updatedAt = nil
                         return copy
                     })
    }

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

/// 单机位的最新媒体周期状态，取自 raw/<机位>/<周期>/status.json。
struct CameraStatus: Codable, Equatable {
    let cameraId: String
    /// idle | waiting | starting | recording | reconnecting | interrupted | stopped | completed | failed
    let state: String
    let mediaEpoch: String?
    let sourceType: String?
    /// 本采集目录中该机位的媒体周期数，重连会新增周期。
    let periods: Int
    /// 最新周期已封口的片段数。
    let segments: Int
    let bytesPerVideoSecond: Double?
    let clockSource: String?
    let startedAt: String?
    let endedAt: String?
    let error: String?
    let previewAvailable: Bool
}

struct Alert: Codable, Equatable {
    let level: String
    let cameraId: String?
    let message: String
    let timestamp: String
}

struct DiskStatus: Codable, Equatable {
    let totalBytes: Int64
    let usedBytes: Int64
    let availableBytes: Int64
    let minFreeBytes: Int64?
    /// 正在录制的机位均已有实测码率时的合计值，否则为空。
    let recordingBytesPerSecond: Double?
    /// capture 按最近一段码率与 minFreeBytes 估算的剩余录制秒数。
    let remainingRecordingSeconds: Int?
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
