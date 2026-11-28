import Darwin
import Foundation

/// stream-match 运行目录中 stream-state.json 的摘要。
struct ProcessingStatus: Codable, Equatable {
    /// running | completed | failed | interrupted | unresponsive
    let status: String
    let frames: Int
    let processedFramesPerSecond: Double?
    let committedRows: Int?
    let consumedRows: Int?
    let backlogRows: Int?
    /// 最老待处理行与最新已登记行的曝光时间差，即 Edge 推理队列的年龄。
    let backlogExposureSeconds: Double?
    let lastSceneUtcUs: String?
    /// 未配置网关时为空。
    let pendingScenes: Int?
    let pendingMediaSegments: Int?
    let error: String?
    var updatedAt: String?
    /// 运行目录存在 PAUSE 标记：推理与上行暂停，录像照常。
    var paused: Bool? = nil
    /// 分析或上行线程实际开始等待的时刻。
    var pausedSince: String? = nil
}

/// 通过 stream-match 运行目录中的 PAUSE 标记暂停和恢复处理，与命令行 `touch PAUSE`／删除标记等价。
final class ProcessingController: @unchecked Sendable {
    let runDir: URL
    private let lock = NSLock()

    init(runDir: URL) {
        self.runDir = runDir
    }

    private var marker: URL { runDir.appendingPathComponent("PAUSE") }
    var isPaused: Bool { FileManager.default.fileExists(atPath: marker.path) }

    @discardableResult
    func pause() throws -> String {
        lock.lock()
        defer { lock.unlock() }
        guard !isPaused else { throw CaptureControlConflict(description: "Processing is already paused") }
        guard FileManager.default.fileExists(atPath: runDir.path) else {
            throw CaptureControlConflict(description: "Run directory \(runDir.path) does not exist")
        }
        guard FileManager.default.createFile(atPath: marker.path, contents: Data()) else {
            throw ValidationError("Cannot write pause marker \(marker.path)")
        }
        return "Processing paused; recording continues and the backlog will grow"
    }

    @discardableResult
    func resume() throws -> String {
        lock.lock()
        defer { lock.unlock() }
        guard isPaused else { throw CaptureControlConflict(description: "Processing is not paused") }
        try FileManager.default.removeItem(at: marker)
        return "Processing resumed"
    }
}

/// stream-match 每 0.5 秒刷新状态，超过该秒数仍为 running 即视为失联。
let processingStaleSeconds: TimeInterval = 5
/// 曝光到浏览器首次绘制的上限是 300 秒（docs/07 第二节）。队列年龄达到这些值时分别告警。
let backlogWarningSeconds = 60.0
let backlogErrorSeconds = 240.0

func readProcessingStatus(runDir: URL, now: Date, timestamp: String) -> (ProcessingStatus?, [Alert]) {
    guard let state = readJSONObject(runDir.appendingPathComponent("stream-state.json")) else {
        return (nil, [])
    }
    func int(_ key: String) -> Int? { (state[key] as? NSNumber)?.intValue }
    func double(_ key: String) -> Double? { (state[key] as? NSNumber)?.doubleValue }
    let updated = double("updatedUnix")
    var status = state["status"] as? String ?? "unknown"
    if status == "running", now.timeIntervalSince1970 - (updated ?? 0) >= processingStaleSeconds {
        status = "unresponsive"
    }
    let pending = state["pendingUploads"] as? [String: Any]
    var error = state["error"] as? String
    if let delivery = state["pendingDelivery"] as? [String: Any], !delivery.isEmpty {
        let detail = delivery.keys.sorted().map { "\($0): \(delivery[$0] ?? "")" }.joined(separator: "; ")
        error = [error, detail].compactMap { $0 }.joined(separator: " · ")
    }
    let processing = ProcessingStatus(
        status: status,
        frames: int("frames") ?? 0,
        processedFramesPerSecond: double("processedFramesPerSecond"),
        committedRows: int("committedRows"),
        consumedRows: int("consumedRows"),
        backlogRows: int("backlogRows"),
        backlogExposureSeconds: double("backlogExposureSeconds"),
        lastSceneUtcUs: state["lastSceneUtcUs"] as? String,
        pendingScenes: (pending?["scenes"] as? NSNumber)?.intValue,
        pendingMediaSegments: (pending?["mediaSegments"] as? NSNumber)?.intValue,
        error: error,
        updatedAt: updated.map { isoString(Date(timeIntervalSince1970: $0)) },
        paused: (state["paused"] as? NSNumber)?.boolValue,
        pausedSince: double("pausedSinceUnix").map { isoString(Date(timeIntervalSince1970: $0)) }
    )
    var alerts: [Alert] = []
    if status == "running", processing.paused == true {
        alerts.append(Alert(level: "info", cameraId: nil,
                            message: "Processing is paused; recording continues and the inference backlog will grow until Resume",
                            timestamp: timestamp))
    }
    switch status {
    case "unresponsive":
        alerts.append(Alert(level: "error", cameraId: nil,
                            message: "stream-match stopped updating stream-state.json; rerun it in the same run directory to resume",
                            timestamp: timestamp))
    case "failed", "interrupted":
        alerts.append(Alert(level: "error", cameraId: nil,
                            message: "stream-match \(status)" + (error.map { ": \($0)" } ?? ""), timestamp: timestamp))
    default:
        break
    }
    if status == "running", let age = processing.backlogExposureSeconds, age >= backlogWarningSeconds {
        alerts.append(Alert(level: age >= backlogErrorSeconds ? "error" : "warning", cameraId: nil,
                            message: "Inference backlog is \(Int(age)) s of exposure (\(processing.backlogRows ?? 0) rows); the 300 s live budget is at risk",
                            timestamp: timestamp))
    }
    return (processing, alerts)
}

/// /api/metrics 的单个采样点，每秒一次。
struct MetricsPoint: Codable, Equatable {
    let timestampMs: Int64
    let backlogRows: Int?
    let backlogExposureSeconds: Double?
    let processedFramesPerSecond: Double?
    let pendingScenes: Int?
    let pendingMediaSegments: Int?
    let recordingBytesPerSecond: Double?
    var cpuPercent: Double? = nil
    var memoryUsedMB: Double? = nil
}

/// 整机资源占用。CPU 取两次采样间全部核心的忙碌比例，内存按活跃、联动与压缩页计已用量。
struct ResourceUsage: Codable, Equatable {
    let cpuPercent: Double?
    let memoryUsedMB: Double
    let memoryTotalMB: Double
}

/// 每秒采样一次 Mach 主机统计。首次采样没有对照，CPU 为空。
final class ResourceSampler: @unchecked Sendable {
    static let shared = ResourceSampler()
    private let lock = NSLock()
    private var previous: (busy: UInt64, total: UInt64)?

    func sample() -> ResourceUsage? {
        lock.lock()
        defer { lock.unlock() }
        let host = mach_host_self()
        var load = host_cpu_load_info()
        var count = mach_msg_type_number_t(MemoryLayout<host_cpu_load_info>.stride / MemoryLayout<integer_t>.stride)
        let cpuResult = withUnsafeMutablePointer(to: &load) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { host_statistics(host, HOST_CPU_LOAD_INFO, $0, &count) }
        }
        var cpu: Double?
        if cpuResult == KERN_SUCCESS {
            let ticks = load.cpu_ticks
            let user = UInt64(ticks.0), system = UInt64(ticks.1), idle = UInt64(ticks.2), nice = UInt64(ticks.3)
            let busy = user + system + nice, total = busy + idle
            cpu = cpuBusyPercent(previous: previous, busy: busy, total: total)
            previous = (busy, total)
        }
        var vm = vm_statistics64()
        var vmCount = mach_msg_type_number_t(MemoryLayout<vm_statistics64>.stride / MemoryLayout<integer_t>.stride)
        let vmResult = withUnsafeMutablePointer(to: &vm) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(vmCount)) { host_statistics64(host, HOST_VM_INFO64, $0, &vmCount) }
        }
        guard vmResult == KERN_SUCCESS else { return nil }
        let page = Double(getpagesize())
        let used = Double(UInt64(vm.active_count) + UInt64(vm.wire_count) + UInt64(vm.compressor_page_count)) * page
        return ResourceUsage(cpuPercent: cpu, memoryUsedMB: used / 1_048_576,
                             memoryTotalMB: Double(ProcessInfo.processInfo.physicalMemory) / 1_048_576)
    }
}

/// 两次累计 tick 之差的忙碌占比；计数回绕或无增量时为空。
func cpuBusyPercent(previous: (busy: UInt64, total: UInt64)?, busy: UInt64, total: UInt64) -> Double? {
    guard let previous, total > previous.total, busy >= previous.busy else { return nil }
    return Double(busy - previous.busy) / Double(total - previous.total) * 100
}

struct MetricsSummary: Codable, Equatable {
    let samples: Int
    let backlogExposureSecondsP50: Double?
    let backlogExposureSecondsP95: Double?
    let backlogExposureSecondsMax: Double?
}

struct MetricsReport: Codable, Equatable {
    let timestamp: String
    let windowSeconds: Int
    /// 最近一次整机资源采样。
    let resources: ResourceUsage?
    let summary: MetricsSummary
    let timeSeries: [MetricsPoint]
}

/// 最近 5 分钟的指标，内存保存，Web UI 重启后清空。
final class MetricsHistory: @unchecked Sendable {
    static let shared = MetricsHistory()
    let windowSeconds: Int
    private let lock = NSLock()
    private var points: [MetricsPoint] = []
    private var latestResources: ResourceUsage?

    init(windowSeconds: Int = 300) {
        self.windowSeconds = windowSeconds
    }

    func record(_ status: SystemStatus, now: Date, resources: ResourceUsage? = nil) {
        let processing = status.processing?.status == "running" ? status.processing : nil
        let point = MetricsPoint(
            timestampMs: Int64(now.timeIntervalSince1970 * 1000),
            backlogRows: processing?.backlogRows,
            backlogExposureSeconds: processing?.backlogExposureSeconds,
            processedFramesPerSecond: processing?.processedFramesPerSecond,
            pendingScenes: processing?.pendingScenes,
            pendingMediaSegments: processing?.pendingMediaSegments,
            recordingBytesPerSecond: status.disk.recordingBytesPerSecond,
            cpuPercent: resources?.cpuPercent,
            memoryUsedMB: resources?.memoryUsedMB
        )
        lock.lock()
        defer { lock.unlock() }
        if let resources { latestResources = resources }
        points.append(point)
        let oldest = point.timestampMs - Int64(windowSeconds) * 1000
        points.removeAll { $0.timestampMs <= oldest }
    }

    func report(now: Date) -> MetricsReport {
        lock.lock()
        let series = points, resources = latestResources
        lock.unlock()
        let ages = series.compactMap(\.backlogExposureSeconds).sorted()
        return MetricsReport(
            timestamp: isoString(now),
            windowSeconds: windowSeconds,
            resources: resources,
            summary: MetricsSummary(samples: series.count,
                                    backlogExposureSecondsP50: percentile(ages, 0.50),
                                    backlogExposureSecondsP95: percentile(ages, 0.95),
                                    backlogExposureSecondsMax: ages.last),
            timeSeries: series
        )
    }
}

/// 最近秩法百分位，输入须已升序。
func percentile(_ sorted: [Double], _ fraction: Double) -> Double? {
    guard !sorted.isEmpty else { return nil }
    let rank = Int((fraction * Double(sorted.count)).rounded(.up))
    return sorted[min(sorted.count, max(1, rank)) - 1]
}
