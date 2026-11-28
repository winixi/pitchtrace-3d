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
    let updatedAt: String?
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
        updatedAt: updated.map { isoString(Date(timeIntervalSince1970: $0)) }
    )
    var alerts: [Alert] = []
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
    let summary: MetricsSummary
    let timeSeries: [MetricsPoint]
}

/// 最近 5 分钟的指标，内存保存，Web UI 重启后清空。
final class MetricsHistory: @unchecked Sendable {
    static let shared = MetricsHistory()
    let windowSeconds: Int
    private let lock = NSLock()
    private var points: [MetricsPoint] = []

    init(windowSeconds: Int = 300) {
        self.windowSeconds = windowSeconds
    }

    func record(_ status: SystemStatus, now: Date) {
        let processing = status.processing?.status == "running" ? status.processing : nil
        let point = MetricsPoint(
            timestampMs: Int64(now.timeIntervalSince1970 * 1000),
            backlogRows: processing?.backlogRows,
            backlogExposureSeconds: processing?.backlogExposureSeconds,
            processedFramesPerSecond: processing?.processedFramesPerSecond,
            pendingScenes: processing?.pendingScenes,
            pendingMediaSegments: processing?.pendingMediaSegments,
            recordingBytesPerSecond: status.disk.recordingBytesPerSecond
        )
        lock.lock()
        defer { lock.unlock() }
        points.append(point)
        let oldest = point.timestampMs - Int64(windowSeconds) * 1000
        points.removeAll { $0.timestampMs <= oldest }
    }

    func report(now: Date) -> MetricsReport {
        lock.lock()
        let series = points
        lock.unlock()
        let ages = series.compactMap(\.backlogExposureSeconds).sorted()
        return MetricsReport(
            timestamp: isoString(now),
            windowSeconds: windowSeconds,
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
