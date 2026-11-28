import Foundation

/// Web UI 读取的采集配置摘要。完整校验仍由 `capture` 在启动时执行。
struct CaptureSetup: Sendable, Equatable {
    let configURL: URL
    let matchId: String
    let cameraIds: [String]
    let minFreeBytes: Int64

    static func load(_ url: URL) throws -> CaptureSetup {
        let data = try Data(contentsOf: url)
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let matchId = object["matchId"] as? String, isEdgeIdentifier(matchId),
              let cameras = object["cameras"] as? [String: Any], !cameras.isEmpty else {
            throw ValidationError("采集配置需要 matchId 与 cameras 对象：\(url.path)")
        }
        guard cameras.keys.allSatisfy(isEdgeIdentifier) else {
            throw ValidationError("采集配置中的机位名需要有效的 v2 标识")
        }
        let minFree = (object["minFreeBytes"] as? NSNumber)?.int64Value ?? 0
        return CaptureSetup(configURL: url, matchId: matchId, cameraIds: cameras.keys.sorted(), minFreeBytes: minFree)
    }
}

/// 与 Geometry/calibrate.py 的 `is_identifier` 一致，也用于拒绝路径穿越。
func isEdgeIdentifier(_ value: String) -> Bool {
    (1...64).contains(value.utf8.count) && value.utf8.allSatisfy { byte in
        (48...57).contains(byte) || (65...90).contains(byte) || (97...122).contains(byte) || byte == 45 || byte == 95
    }
}

/// Web UI 启动的采集子进程状态，由 `CaptureController` 提供。
struct CaptureProcessState: Sendable, Equatable {
    var running = false
    var exitCode: Int32?
    var stderrTail = ""
}

/// `capture` 每 0.5 秒刷新 health.json，超过该秒数仍有活动机位即视为采集进程已失联。
let healthStaleSeconds: TimeInterval = 5
/// 文档 08 第 5 节阶段 3 的磁盘告警阈值。
let lowDiskWarningBytes: Int64 = 100_000_000_000

/// 判断采集根目录下是否有正在运行的 `capture`（含 Web UI 之外启动的进程）。
func captureIsLive(root: URL, now: Date) -> Bool {
    guard let health = readJSONObject(root.appendingPathComponent("health.json")),
          let active = health["active"] as? [String: Any], !active.isEmpty,
          let updated = (health["updatedUnix"] as? NSNumber)?.doubleValue else { return false }
    return now.timeIntervalSince1970 - updated < healthStaleSeconds
}

/// 从 `capture` 写出的 health.json 与 raw/<机位>/<周期>/status.json 汇总场务状态。
///
/// 只报告磁盘上可核验的值：帧率、曝光偏差等采集层未记录的量不在此给出，
/// 未知的码率或剩余时长保持为空，不用估计值填补。
func readCaptureStatus(root: URL?, setup: CaptureSetup?, process: CaptureProcessState, now: Date) -> SystemStatus {
    let timestamp = isoString(now)
    let controlsEnabled = setup != nil && root != nil
    guard let root else {
        return SystemStatus(timestamp: timestamp, matchId: setup?.matchId, matchState: "idle",
                            controlsEnabled: false, cameras: [], alerts: [], disk: diskStatus(at: nil, health: nil, setup: setup, cameras: []))
    }
    var alerts: [Alert] = []
    let health = readJSONObject(root.appendingPathComponent("health.json"))
    let active = health?["active"] as? [String: Any] ?? [:]
    let finished = health?["finished"] as? [String: Any] ?? [:]
    let updated = (health?["updatedUnix"] as? NSNumber)?.doubleValue
    let healthAge = updated.map { now.timeIntervalSince1970 - $0 }
    let healthLive = !active.isEmpty && (healthAge ?? .infinity) < healthStaleSeconds
    let live = process.running || healthLive
    let stopMarker = FileManager.default.fileExists(atPath: root.appendingPathComponent("STOP").path)

    let matchState: String
    if live {
        matchState = stopMarker ? "stopping" : "capturing"
    } else if !active.isEmpty {
        matchState = "unresponsive"
        alerts.append(Alert(level: "error", cameraId: nil,
                            message: "Capture stopped updating health.json \(Int(healthAge ?? 0)) s ago; the process may have been killed. Sealed .m4s segments remain valid.",
                            timestamp: timestamp))
    } else if health != nil {
        matchState = "stopped"
    } else {
        matchState = "idle"
    }
    if !process.running, let code = process.exitCode, code != 0 {
        let detail = process.stderrTail.trimmingCharacters(in: .whitespacesAndNewlines)
        alerts.append(Alert(level: "error", cameraId: nil,
                            message: "Capture exited with status \(code)" + (detail.isEmpty ? "" : ": \(detail)"),
                            timestamp: timestamp))
    }

    let rawRoot = root.appendingPathComponent("raw", isDirectory: true)
    var ids = Set(setup?.cameraIds ?? [])
    ids.formUnion(active.keys)
    ids.formUnion(finished.keys)
    ids.formUnion(directoryNames(rawRoot).filter(isEdgeIdentifier))

    let cameras = ids.sorted().map { id -> CameraStatus in
        let periods = periodStatuses(rawRoot.appendingPathComponent(id, isDirectory: true))
        let latest = periods.last
        let progress = active[id] as? [String: Any]
        let periodStatus = latest?.status["status"] as? String
        // health.json 的 active 项在重试间隔内保留上一次失败；周期 status.json 是逐路的最终记录。
        let state: String
        if live, let progressStatus = progress?["status"] as? String, progressStatus == "starting" || progressStatus == "failed" {
            state = progressStatus == "starting" ? "starting" : "reconnecting"
        } else if periodStatus == "recording" {
            state = live ? "recording" : "interrupted"
        } else if let periodStatus {
            state = periodStatus
        } else {
            state = live ? "waiting" : "idle"
        }
        let error = latest?.status["error"] as? String
        let segments = (latest?.status["segments"] as? NSNumber)?.intValue ?? 0
        let camera = CameraStatus(
            cameraId: id,
            state: state,
            mediaEpoch: latest?.status["mediaEpoch"] as? String,
            sourceType: latest?.status["type"] as? String,
            periods: periods.count,
            segments: segments,
            bytesPerVideoSecond: (latest?.status["bytesPerVideoSecond"] as? NSNumber)?.doubleValue,
            clockSource: latest?.status["clockSource"] as? String,
            startedAt: ((latest?.status["startedUnix"] as? NSNumber)?.doubleValue).map { isoString(Date(timeIntervalSince1970: $0)) },
            endedAt: ((latest?.status["endedUnix"] as? NSNumber)?.doubleValue).map { isoString(Date(timeIntervalSince1970: $0)) },
            error: error,
            previewAvailable: segments > 0 && latest.map {
                FileManager.default.fileExists(atPath: $0.directory.appendingPathComponent("init.mp4").path)
            } == true
        )
        switch state {
        case "failed":
            alerts.append(Alert(level: "error", cameraId: id, message: "Camera \(id) failed: \(error ?? "unknown error")", timestamp: timestamp))
        case "reconnecting":
            alerts.append(Alert(level: "warning", cameraId: id, message: "Camera \(id) input ended, reconnecting in a new media period: \(error ?? "")", timestamp: timestamp))
        case "interrupted":
            alerts.append(Alert(level: "warning", cameraId: id, message: "Camera \(id) period \(camera.mediaEpoch ?? "?") was not closed; use only sealed .m4s segments", timestamp: timestamp))
        default:
            break
        }
        return camera
    }

    let disk = diskStatus(at: root, health: live ? health : nil, setup: setup, cameras: live ? cameras : [])
    if health?["lowDisk"] as? Bool == true {
        alerts.append(Alert(level: "critical", cameraId: nil, message: "Free disk space reached minFreeBytes; capture stopped all channels", timestamp: timestamp))
    } else if disk.availableBytes < lowDiskWarningBytes {
        alerts.append(Alert(level: "warning", cameraId: nil, message: "Less than 100 GB free on the capture volume", timestamp: timestamp))
    }
    if let seconds = disk.remainingRecordingSeconds, seconds < 3600 {
        alerts.append(Alert(level: "warning", cameraId: nil, message: "About \(seconds / 60) min of recording left at the current bitrate", timestamp: timestamp))
    }
    return SystemStatus(timestamp: timestamp, matchId: matchId(root: root, setup: setup), matchState: matchState,
                        controlsEnabled: controlsEnabled, cameras: cameras, alerts: sortedAlerts(alerts), disk: disk)
}

/// 按严重程度排序，同级保持原有顺序。
func sortedAlerts(_ alerts: [Alert]) -> [Alert] {
    let rank = ["critical": 0, "error": 1, "warning": 2, "info": 3]
    return alerts.enumerated()
        .sorted { ((rank[$0.element.level] ?? 4), $0.offset) < ((rank[$1.element.level] ?? 4), $1.offset) }
        .map(\.element)
}

/// 机位最新媒体周期目录（按 status.json 的开始时刻排序）。
func latestPeriodDirectory(root: URL, cameraId: String) -> URL? {
    guard isEdgeIdentifier(cameraId) else { return nil }
    return periodStatuses(root.appendingPathComponent("raw/\(cameraId)", isDirectory: true)).last?.directory
}

private struct PeriodStatus {
    let directory: URL
    let status: [String: Any]
    let started: Double
}

private func periodStatuses(_ cameraDirectory: URL) -> [PeriodStatus] {
    directoryNames(cameraDirectory).filter(isEdgeIdentifier).compactMap { name in
        let directory = cameraDirectory.appendingPathComponent(name, isDirectory: true)
        guard let status = readJSONObject(directory.appendingPathComponent("status.json")) else { return nil }
        return PeriodStatus(directory: directory, status: status,
                            started: (status["startedUnix"] as? NSNumber)?.doubleValue ?? 0)
    }
    .sorted { ($0.started, $0.directory.lastPathComponent) < ($1.started, $1.directory.lastPathComponent) }
}

private func matchId(root: URL, setup: CaptureSetup?) -> String? {
    readJSONObject(root.appendingPathComponent("match.json"))?["matchId"] as? String ?? setup?.matchId
}

private func diskStatus(at root: URL?, health: [String: Any]?, setup: CaptureSetup?, cameras: [CameraStatus]) -> DiskStatus {
    // 采集目录可能尚未创建，此时读取最近的已存在上级目录所在卷。
    var path = root ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    while !FileManager.default.fileExists(atPath: path.path), path.path != "/" {
        path = path.deletingLastPathComponent()
    }
    let attributes = (try? FileManager.default.attributesOfFileSystem(forPath: path.path)) ?? [:]
    let total = (attributes[.systemSize] as? NSNumber)?.int64Value ?? 0
    let free = (attributes[.systemFreeSize] as? NSNumber)?.int64Value ?? 0
    let recording = cameras.filter { $0.state == "recording" }
    let rates = recording.compactMap(\.bytesPerVideoSecond)
    return DiskStatus(
        totalBytes: total,
        usedBytes: total - free,
        availableBytes: free,
        minFreeBytes: setup?.minFreeBytes,
        recordingBytesPerSecond: !recording.isEmpty && rates.count == recording.count ? rates.reduce(0, +) : nil,
        remainingRecordingSeconds: (health?["remainingRecordingSeconds"] as? NSNumber)?.intValue
    )
}

private func directoryNames(_ url: URL) -> [String] {
    ((try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: [.isDirectoryKey])) ?? [])
        .filter { (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true }
        .map(\.lastPathComponent)
}

func readJSONObject(_ url: URL) -> [String: Any]? {
    guard let data = try? Data(contentsOf: url) else { return nil }
    return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
}

func isoString(_ date: Date) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter.string(from: date)
}

/// 系统监控器：串行汇总采集目录状态，供推送任务与 HTTP 路由共用。
actor SystemMonitor {
    static let shared = SystemMonitor()

    private var root: URL?
    private var setup: CaptureSetup?
    private var controller: CaptureController?
    private var runDir: URL?

    private init() {}

    func configure(root: URL?, setup: CaptureSetup?, controller: CaptureController?, runDir: URL? = nil) {
        self.root = root
        self.setup = setup
        self.controller = controller
        self.runDir = runDir
    }

    func currentStatus(now: Date = Date()) -> SystemStatus {
        var status = readCaptureStatus(root: root, setup: setup, process: controller?.processState ?? CaptureProcessState(), now: now)
        if let runDir {
            status = mergeProcessing(status, runDir: runDir, now: now)
        }
        return status
    }
}

/// 把 stream-match 状态并入采集状态。录像中而处理已暂停时，比赛状态显示为 paused。
func mergeProcessing(_ capture: SystemStatus, runDir: URL, now: Date) -> SystemStatus {
    var status = capture
    let (processing, alerts) = readProcessingStatus(runDir: runDir, now: now, timestamp: status.timestamp)
    status.processing = processing
    status.alerts = sortedAlerts(status.alerts + alerts)
    if status.matchState == "capturing", processing?.status == "running", processing?.paused == true {
        status.matchState = "paused"
    }
    return status
}
