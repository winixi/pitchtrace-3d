import Foundation

/// 系统监控器
///
/// 负责收集 Edge 运行时状态：
/// - 读取 match-dir 下的 SQLite 数据库（场景归档）
/// - 监控采集进程状态（通过 run-match/stream-match 进程）
/// - 计算磁盘使用和预估剩余时间
/// - 缓存状态数据，避免频繁查询
actor SystemMonitor {
    static let shared = SystemMonitor()
    
    private var matchDirectory: URL?
    private var cachedStatus: SystemStatus?
    private var lastUpdate: Date?
    private let cacheInterval: TimeInterval = 2.0  // 2 秒缓存
    
    private init() {}
    
    /// 配置监控目标
    func configure(matchDirectory: URL?) {
        self.matchDirectory = matchDirectory
        self.cachedStatus = nil
        self.lastUpdate = nil
    }
    
    /// 获取当前系统状态
    func currentStatus() async throws -> SystemStatus {
        // 检查缓存
        if let cached = cachedStatus,
           let lastUpdate = lastUpdate,
           Date().timeIntervalSince(lastUpdate) < cacheInterval {
            return cached
        }
        
        // 收集新状态
        let status = try await collectStatus()
        
        // 更新缓存
        self.cachedStatus = status
        self.lastUpdate = Date()
        
        return status
    }
    
    /// 收集系统状态
    private func collectStatus() async throws -> SystemStatus {
        let timestamp = ISO8601DateFormatter().string(from: Date())
        
        // 如果没有配置 match-dir，返回空闲状态
        guard let matchDir = matchDirectory else {
            return SystemStatus(
                timestamp: timestamp,
                matchId: "no-match",
                matchState: "idle",
                cameras: [],
                alerts: [],
                disk: try await collectDiskStatus(recordingDir: nil)
            )
        }
        
        // 读取比赛配置
        let matchInfo = try await readMatchInfo(from: matchDir)
        
        // 收集机位状态
        let cameras = try await collectCameraStatus(matchDir: matchDir, matchInfo: matchInfo)
        
        // 生成告警
        let alerts = generateAlerts(cameras: cameras)
        
        // 收集磁盘状态
        let disk = try await collectDiskStatus(recordingDir: matchDir)
        
        return SystemStatus(
            timestamp: timestamp,
            matchId: matchInfo.matchId,
            matchState: matchInfo.state,
            cameras: cameras,
            alerts: alerts,
            disk: disk
        )
    }
    
    /// 读取比赛信息
    private func readMatchInfo(from matchDir: URL) async throws -> MatchInfo {
        // 尝试读取 match-config.json
        let configURL = matchDir.appendingPathComponent("match-config.json")
        
        if FileManager.default.fileExists(atPath: configURL.path) {
            let data = try Data(contentsOf: configURL)
            let config = try JSONDecoder().decode(MatchConfig.self, from: data)
            
            // 检查是否有 run-match 或 stream-match 进程在运行
            let isRunning = await isMatchProcessRunning(matchId: config.matchId)
            
            return MatchInfo(
                matchId: config.matchId,
                state: isRunning ? "running" : "stopped",
                cameraIds: config.cameras
            )
        }
        
        // 如果没有配置文件，尝试从目录名推断
        let matchId = matchDir.lastPathComponent
        
        // 查找 camera_* 子目录
        let cameraIds = try FileManager.default.contentsOfDirectory(
            at: matchDir,
            includingPropertiesForKeys: [.isDirectoryKey]
        )
        .filter { url in
            url.lastPathComponent.hasPrefix("camera_") ||
            url.lastPathComponent.hasPrefix("Camera")
        }
        .map { $0.lastPathComponent.replacingOccurrences(of: "camera_", with: "")
            .replacingOccurrences(of: "Camera", with: "")
            .uppercased()
        }
        .sorted()
        
        return MatchInfo(
            matchId: matchId,
            state: "idle",
            cameraIds: cameraIds
        )
    }
    
    /// 检查是否有比赛进程在运行
    private func isMatchProcessRunning(matchId: String) async -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/ps")
        process.arguments = ["aux"]
        
        let pipe = Pipe()
        process.standardOutput = pipe
        
        do {
            try process.run()
            process.waitUntilExit()
            
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let output = String(data: data, encoding: .utf8) ?? ""
            
            // 查找包含 matchId 的 pitchtrace-edge 进程
            return output.contains("pitchtrace-edge") &&
                   (output.contains("run-match") || output.contains("stream-match")) &&
                   output.contains(matchId)
        } catch {
            return false
        }
    }
    
    /// 收集机位状态
    private func collectCameraStatus(matchDir: URL, matchInfo: MatchInfo) async throws -> [CameraStatus] {
        var cameras: [CameraStatus] = []
        
        for cameraId in matchInfo.cameraIds {
            // 查找机位目录
            let possibleDirs = [
                matchDir.appendingPathComponent("camera_\(cameraId)"),
                matchDir.appendingPathComponent("Camera\(cameraId)"),
                matchDir.appendingPathComponent(cameraId)
            ]
            
            guard let cameraDir = possibleDirs.first(where: { FileManager.default.fileExists(atPath: $0.path) }) else {
                // 机位目录不存在，标记为未配置
                cameras.append(CameraStatus(
                    cameraId: cameraId,
                    state: "not-configured",
                    fps: 0,
                    frameCount: 0,
                    lastFrameUtcUs: "0",
                    exposureDriftMs: nil,
                    droppedFrames: 0,
                    queueDepth: 0,
                    calibrationId: nil,
                    calibrationValid: false
                ))
                continue
            }
            
            // 读取机位状态
            let status = try await readCameraStatus(cameraDir: cameraDir, cameraId: cameraId)
            cameras.append(status)
        }
        
        return cameras.sorted { $0.cameraId < $1.cameraId }
    }
    
    /// 读取单个机位状态
    private func readCameraStatus(cameraDir: URL, cameraId: String) async throws -> CameraStatus {
        // 查找最新的媒体周期目录
        let epochs = try FileManager.default.contentsOfDirectory(
            at: cameraDir,
            includingPropertiesForKeys: [.isDirectoryKey, .creationDateKey]
        )
        .filter { url in
            url.lastPathComponent.hasPrefix("epoch_") &&
            url.hasDirectoryPath
        }
        .sorted { url1, url2 in
            (try? url1.resourceValues(forKeys: [.creationDateKey]).creationDate ?? Date.distantPast) ?? Date.distantPast >
            (try? url2.resourceValues(forKeys: [.creationDateKey]).creationDate ?? Date.distantPast) ?? Date.distantPast
        }
        
        guard let latestEpoch = epochs.first else {
            return CameraStatus(
                cameraId: cameraId,
                state: "idle",
                fps: 0,
                frameCount: 0,
                lastFrameUtcUs: "0",
                exposureDriftMs: nil,
                droppedFrames: 0,
                queueDepth: 0,
                calibrationId: nil,
                calibrationValid: false
            )
        }
        
        // 读取媒体索引获取帧数和最新时间戳
        let indexURL = latestEpoch.appendingPathComponent("index.ndjson")
        let frameInfo = try await readMediaIndex(indexURL: indexURL)
        
        // 计算 FPS（如果有足够的帧）
        let fps = frameInfo.count > 10 ? calculateFPS(frames: frameInfo) : 0.0
        
        // 检查状态（根据最后一帧时间判断是否活跃）
        let now = Date().timeIntervalSince1970 * 1_000_000  // 转为微秒
        let lastFrameTime = Double(frameInfo.last?.utcUs ?? "0") ?? 0
        let timeSinceLastFrame = now - lastFrameTime
        let state: String
        if timeSinceLastFrame < 5_000_000 {  // 5 秒内
            state = "active"
        } else if timeSinceLastFrame < 30_000_000 {  // 30 秒内
            state = "stale"
        } else {
            state = "disconnected"
        }
        
        return CameraStatus(
            cameraId: cameraId,
            state: state,
            fps: fps,
            frameCount: frameInfo.count,
            lastFrameUtcUs: frameInfo.last?.utcUs ?? "0",
            exposureDriftMs: nil,  // 需要从曝光审计报告读取
            droppedFrames: 0,       // 需要从缺口报告读取
            queueDepth: 0,          // 需要进程间通信获取
            calibrationId: nil,     // 需要从标定报告读取
            calibrationValid: false
        )
    }
    
    /// 读取媒体索引
    private func readMediaIndex(indexURL: URL) async throws -> [FrameIndexEntry] {
        guard FileManager.default.fileExists(atPath: indexURL.path) else {
            return []
        }
        
        let content = try String(contentsOf: indexURL, encoding: .utf8)
        let lines = content.split(separator: "\n")
        
        var entries: [FrameIndexEntry] = []
        for line in lines.prefix(1000) {  // 最多读 1000 帧（性能考虑）
            if let data = line.data(using: .utf8),
               let entry = try? JSONDecoder().decode(FrameIndexEntry.self, from: data) {
                entries.append(entry)
            }
        }
        
        return entries
    }
    
    /// 计算 FPS
    private func calculateFPS(frames: [FrameIndexEntry]) -> Double {
        guard frames.count >= 2 else { return 0.0 }
        
        let first = frames.first!
        let last = frames.last!
        
        guard let firstTime = Double(first.utcUs),
              let lastTime = Double(last.utcUs) else {
            return 0.0
        }
        
        let durationSeconds = (lastTime - firstTime) / 1_000_000.0
        guard durationSeconds > 0 else { return 0.0 }
        
        return Double(frames.count) / durationSeconds
    }
    
    /// 生成告警
    private func generateAlerts(cameras: [CameraStatus]) -> [Alert] {
        var alerts: [Alert] = []
        let now = Date()
        let formatter = ISO8601DateFormatter()
        
        for camera in cameras {
            switch camera.state {
            case "disconnected":
                alerts.append(Alert(
                    level: "error",
                    cameraId: camera.cameraId,
                    message: "Camera \(camera.cameraId) disconnected",
                    timestamp: formatter.string(from: now)
                ))
            case "stale":
                alerts.append(Alert(
                    level: "warning",
                    cameraId: camera.cameraId,
                    message: "Camera \(camera.cameraId) not receiving frames",
                    timestamp: formatter.string(from: now)
                ))
            case "not-configured":
                alerts.append(Alert(
                    level: "warning",
                    cameraId: camera.cameraId,
                    message: "Camera \(camera.cameraId) not configured",
                    timestamp: formatter.string(from: now)
                ))
            default:
                // 检查队列深度
                if camera.queueDepth > 10 {
                    alerts.append(Alert(
                        level: "warning",
                        cameraId: camera.cameraId,
                        message: "Queue depth high: \(camera.queueDepth) frames",
                        timestamp: formatter.string(from: now)
                    ))
                }
            }
        }
        
        return alerts.sorted { $0.level == "error" && $1.level != "error" }
    }
    
    /// 收集磁盘状态
    private func collectDiskStatus(recordingDir: URL?) async throws -> DiskStatus {
        let targetPath = recordingDir?.path ?? FileManager.default.currentDirectoryPath
        
        let attributes = try FileManager.default.attributesOfFileSystem(
            forPath: targetPath
        )
        
        let totalBytes = (attributes[.systemSize] as? NSNumber)?.int64Value ?? 0
        let freeBytes = (attributes[.systemFreeSize] as? NSNumber)?.int64Value ?? 0
        let usedBytes = totalBytes - freeBytes
        
        // 估算录像速率（假设 4 机位，每机位 4 Mbps）
        let estimatedRateBytesPerSecond = recordingDir != nil ? (4 * 4 * 1_000_000 / 8) : 0
        
        // 计算剩余时间
        let hoursRemaining = estimatedRateBytesPerSecond > 0
            ? Double(freeBytes) / Double(estimatedRateBytesPerSecond) / 3600.0
            : 0.0
        
        return DiskStatus(
            totalBytes: totalBytes,
            usedBytes: usedBytes,
            availableBytes: freeBytes,
            recordingRateBytes: estimatedRateBytesPerSecond,
            estimatedHoursRemaining: hoursRemaining
        )
    }
}

// MARK: - 辅助数据结构

private struct MatchInfo {
    let matchId: String
    let state: String
    let cameraIds: [String]
}

private struct MatchConfig: Codable {
    let matchId: String
    let cameras: [String]
}

private struct FrameIndexEntry: Codable {
    let utcUs: String
    let pts: Int
    
    enum CodingKeys: String, CodingKey {
        case utcUs = "utc_us"
        case pts
    }
}
