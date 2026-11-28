import Testing
import Foundation
@testable import PitchTraceEdge

/// Web UI 相关测试
///
/// - 命令行参数解析
/// - 采集目录状态汇总与告警
/// - 开始／结束采集的状态转换与跨站防护
/// - 预览片段选择
@Suite("Web UI Tests")
struct WebUITests {
    
    @Test("解析默认参数")
    func parseDefaultArguments() throws {
        let command = try WebUICommand.parse(arguments: [])
        
        #expect(command.bindAddress == "127.0.0.1:8765")
        #expect(command.matchDir == nil)
    }
    
    @Test("解析自定义绑定地址")
    func parseCustomBindAddress() throws {
        let command = try WebUICommand.parse(arguments: ["--bind", "0.0.0.0:9000"])
        
        #expect(command.bindAddress == "0.0.0.0:9000")
        #expect(command.matchDir == nil)
    }
    
    @Test("解析比赛目录")
    func parseMatchDirectory() throws {
        let command = try WebUICommand.parse(arguments: ["--match-dir", "/path/to/match"])
        
        #expect(command.bindAddress == "127.0.0.1:8765")
        #expect(command.matchDir == "/path/to/match")
    }
    
    @Test("解析完整参数")
    func parseAllArguments() throws {
        let command = try WebUICommand.parse(arguments: [
            "--bind", "192.168.1.100:8080",
            "--match-dir", "/var/pitchtrace/match-001"
        ])
        
        #expect(command.bindAddress == "192.168.1.100:8080")
        #expect(command.matchDir == "/var/pitchtrace/match-001")
    }
    
    @Test("缺少必需参数时抛出错误")
    func parseMissingRequiredArgument() {
        #expect(throws: ValidationError.self) {
            _ = try WebUICommand.parse(arguments: ["--bind"])
        }
        
        #expect(throws: ValidationError.self) {
            _ = try WebUICommand.parse(arguments: ["--match-dir"])
        }
    }
    
    @Test("未知参数时抛出错误")
    func parseUnknownArgument() {
        #expect(throws: ValidationError.self) {
            _ = try WebUICommand.parse(arguments: ["--unknown"])
        }
    }
    
    @Test("帮助参数抛出 ExitSuccess")
    func parseHelpArgument() {
        #expect(throws: ExitSuccess.self) {
            _ = try WebUICommand.parse(arguments: ["--help"])
        }
        
        #expect(throws: ExitSuccess.self) {
            _ = try WebUICommand.parse(arguments: ["-h"])
        }
    }
    
    @Test("采集配置必须配合采集目录")
    func captureConfigNeedsMatchDir() throws {
        #expect(throws: ValidationError.self) {
            _ = try WebUICommand.parse(arguments: ["--capture-config", "cameras.json"])
        }
        let command = try WebUICommand.parse(arguments: ["--match-dir", "/tmp/s", "--capture-config", "cameras.json",
                                                         "--run-dir", "/tmp/run"])
        #expect(command.captureConfig == "cameras.json")
        #expect(command.runDir == "/tmp/run")
    }

    @Test("机位标识与 v2 规则一致并拒绝路径穿越")
    func identifiers() {
        #expect(isEdgeIdentifier("A"))
        #expect(isEdgeIdentifier("cam_01-x"))
        #expect(!isEdgeIdentifier(".."))
        #expect(!isEdgeIdentifier("A/B"))
        #expect(!isEdgeIdentifier(""))
        #expect(!isEdgeIdentifier(String(repeating: "a", count: 65)))
    }

    @Test("控制请求需要自定义头且同源")
    func controlOrigin() {
        #expect(WebUICommand.isSameOriginControl(headers: ["x-pitchtrace-control": "1"]))
        #expect(WebUICommand.isSameOriginControl(headers: [
            "x-pitchtrace-control": "1", "host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765",
        ]))
        #expect(!WebUICommand.isSameOriginControl(headers: [:]))
        #expect(!WebUICommand.isSameOriginControl(headers: [
            "x-pitchtrace-control": "1", "host": "127.0.0.1:8765", "origin": "https://evil.example",
        ]))
    }

    @Test("未配置目录时为空闲且不提供控制")
    func idleWithoutRoot() {
        let status = readCaptureStatus(root: nil, setup: nil, process: CaptureProcessState(), now: Date())
        #expect(status.matchState == "idle")
        #expect(status.cameras.isEmpty)
        #expect(!status.controlsEnabled)
        #expect(status.disk.remainingRecordingSeconds == nil)
    }

    @Test("从 health.json 与周期 status.json 汇总录制状态")
    func capturingStatus() throws {
        let root = try temporaryDirectory()
        let now = Date()
        try writeJSON(root, "match.json", ["matchId": "training"])
        try writePeriod(root, "A", "first", ["cameraId": "A", "mediaEpoch": "first", "type": "file", "status": "recording",
                                             "clockSource": "unknown", "startedUnix": now.timeIntervalSince1970 - 10,
                                             "segments": 3, "bytesPerVideoSecond": 250_000.0])
        try writePeriod(root, "B", "p1", ["cameraId": "B", "mediaEpoch": "p1", "type": "rtsp", "status": "failed",
                                          "error": "视频输入结束", "startedUnix": now.timeIntervalSince1970 - 20, "segments": 1])
        try writePeriod(root, "B", "p2", ["cameraId": "B", "mediaEpoch": "p2", "type": "rtsp", "status": "recording",
                                          "startedUnix": now.timeIntervalSince1970 - 5, "segments": 1, "bytesPerVideoSecond": 150_000.0])
        try writeJSON(root, "health.json", ["matchId": "training", "freeBytes": 1, "lowDisk": false,
                                            "active": ["A": ["status": "recording"], "B": ["status": "recording"]],
                                            "finished": [:], "updatedUnix": now.timeIntervalSince1970 - 1,
                                            "remainingRecordingSeconds": 7200])
        let setup = CaptureSetup(configURL: root.appendingPathComponent("c.json"), matchId: "training",
                                 cameraIds: ["A", "B", "C"], minFreeBytes: 0)
        let status = readCaptureStatus(root: root, setup: setup, process: CaptureProcessState(), now: now)
        #expect(status.matchId == "training")
        #expect(status.matchState == "capturing")
        #expect(status.controlsEnabled)
        #expect(status.cameras.map(\.cameraId) == ["A", "B", "C"])
        #expect(status.cameras[0].state == "recording")
        #expect(status.cameras[0].segments == 3)
        #expect(status.cameras[0].previewAvailable)
        #expect(status.cameras[1].mediaEpoch == "p2")
        #expect(status.cameras[1].periods == 2)
        #expect(status.cameras[2].state == "waiting")
        #expect(status.disk.recordingBytesPerSecond == 400_000)
        #expect(status.disk.remainingRecordingSeconds == 7200)
        #expect(!status.alerts.contains { $0.level == "error" || $0.level == "critical" })
    }

    @Test("health.json 停止刷新时报告失联并标出未封闭周期")
    func unresponsiveCapture() throws {
        let root = try temporaryDirectory()
        let now = Date()
        try writePeriod(root, "A", "first", ["status": "recording", "mediaEpoch": "first",
                                             "startedUnix": now.timeIntervalSince1970 - 60, "segments": 2])
        try writeJSON(root, "health.json", ["active": ["A": ["status": "recording"]], "finished": [:],
                                            "updatedUnix": now.timeIntervalSince1970 - 30, "lowDisk": false,
                                            "remainingRecordingSeconds": 9999])
        let status = readCaptureStatus(root: root, setup: nil, process: CaptureProcessState(), now: now)
        #expect(status.matchState == "unresponsive")
        #expect(status.cameras.first?.state == "interrupted")
        #expect(status.alerts.first?.level == "error")
        #expect(status.disk.remainingRecordingSeconds == nil)
        #expect(!captureIsLive(root: root, now: now))
    }

    @Test("重试间隔中的机位、失败与磁盘不足告警")
    func failureAlerts() throws {
        let root = try temporaryDirectory()
        let now = Date()
        try writePeriod(root, "A", "x", ["status": "failed", "error": "FFmpeg 退出码 1",
                                         "startedUnix": now.timeIntervalSince1970 - 3, "segments": 0])
        try writePeriod(root, "B", "y", ["status": "failed", "error": "鉴权失败",
                                         "startedUnix": now.timeIntervalSince1970 - 3, "segments": 0])
        try writeJSON(root, "health.json", ["active": ["A": ["status": "failed"]], "finished": ["B": ["status": "failed"]],
                                            "updatedUnix": now.timeIntervalSince1970, "lowDisk": true])
        let status = readCaptureStatus(root: root, setup: nil, process: CaptureProcessState(), now: now)
        #expect(status.matchState == "capturing")
        #expect(status.cameras.map(\.state) == ["reconnecting", "failed"])
        #expect(status.alerts.first?.level == "critical")
        #expect(status.alerts.contains { $0.cameraId == "B" && $0.level == "error" && $0.message.contains("鉴权失败") })
        #expect(status.alerts.contains { $0.cameraId == "A" && $0.level == "warning" })
        #expect(!status.cameras[0].previewAvailable)
    }

    @Test("采集进程非零退出时附带诊断")
    func exitAlert() throws {
        let root = try temporaryDirectory()
        let process = CaptureProcessState(running: false, exitCode: 1, stderrTail: "采集处理失败：目录有停止标记\n")
        let status = readCaptureStatus(root: root, setup: nil, process: process, now: Date())
        #expect(status.matchState == "idle")
        #expect(status.alerts.contains { $0.message == "Capture exited with status 1: 采集处理失败：目录有停止标记" })
    }

    @Test("开始、重复开始、结束与 STOP 标记的状态转换")
    func controllerLifecycle() throws {
        let root = try temporaryDirectory()
        let config = root.appendingPathComponent("cameras.json")
        try Data("{}".utf8).write(to: config)
        // 替身按 capture 的约定刷新 health.json，看到 STOP 后写出最终状态并退出。
        let fake = root.appendingPathComponent("fake-edge")
        try Data("""
        #!/bin/sh
        [ "$1" = capture ] || exit 9
        [ -e "$3/STOP" ] && { echo "目录有停止标记 STOP" >&2; exit 1; }
        while [ ! -e "$3/STOP" ]; do
          printf '{"active":{"A":{"status":"recording"}},"finished":{},"updatedUnix":%s}\\n' "$(date +%s)" > "$3/health.json"
          sleep 0.1
        done
        printf '{"active":{},"finished":{"A":{"status":"stopped"}},"updatedUnix":%s}\\n' "$(date +%s)" > "$3/health.json"
        """.utf8).write(to: fake)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: fake.path)
        let session = root.appendingPathComponent("session", isDirectory: true)
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        // 上一次停止遗留的标记由开始操作移走。
        FileManager.default.createFile(atPath: session.appendingPathComponent("STOP").path, contents: Data())

        let controller = CaptureController(executable: fake, configURL: config, root: session)
        #expect(throws: CaptureControlConflict.self) { try controller.stop() }
        try controller.start()
        #expect(controller.processState.running)
        #expect(throws: CaptureControlConflict.self) { try controller.start() }
        let capturing = readCaptureStatus(root: session, setup: nil, process: controller.processState, now: Date())
        #expect(capturing.matchState == "capturing")
        // 首份 health.json 出现前拒绝结束，避免 capture 因 STOP 标记拒绝启动。
        if !captureIsLive(root: session, now: Date()) {
            #expect(throws: CaptureControlConflict.self) { try controller.stop() }
        }
        for _ in 0..<100 where !captureIsLive(root: session, now: Date()) {
            Thread.sleep(forTimeInterval: 0.05)
        }

        try controller.stop()
        let stopping = readCaptureStatus(root: session, setup: nil, process: controller.processState, now: Date())
        #expect(stopping.matchState == "stopping")
        controller.waitUntilExit()
        #expect(controller.processState == CaptureProcessState(running: false, exitCode: 0, stderrTail: ""))
        #expect(!FileManager.default.fileExists(atPath: session.appendingPathComponent("STOP").path))
        let stopped = readCaptureStatus(root: session, setup: nil, process: controller.processState, now: Date())
        #expect(stopped.matchState == "stopped")
    }

    @Test("Web UI 之外启动的采集也会阻止重复开始")
    func externalCaptureConflict() throws {
        let root = try temporaryDirectory()
        try writeJSON(root, "health.json", ["active": ["A": ["status": "recording"]], "finished": [:],
                                            "updatedUnix": Date().timeIntervalSince1970])
        let controller = CaptureController(executable: URL(fileURLWithPath: "/usr/bin/false"),
                                           configURL: root.appendingPathComponent("c.json"), root: root)
        #expect(throws: CaptureControlConflict.self) { try controller.start() }
        try controller.stop()
        #expect(FileManager.default.fileExists(atPath: root.appendingPathComponent("STOP").path))
    }

    @Test("预览只选已封口片段")
    func sealedSegments() throws {
        let root = try temporaryDirectory()
        for name in ["0.m4s", "1.m4s", "2.m4s.tmp", "init.mp4", "preview.m3u8"] {
            FileManager.default.createFile(atPath: root.appendingPathComponent(name).path, contents: Data())
        }
        #expect(latestSealedSegment(in: root)?.lastPathComponent == "1.m4s")
        #expect(latestSealedSegment(in: root.appendingPathComponent("missing")) == nil)
        #expect(PreviewRenderer(root: root).jpeg(cameraId: "../A") == nil)
    }

    @Test("读取 stream-state.json 的积压与待上传数")
    func processingStatus() throws {
        let run = try temporaryDirectory()
        let now = Date()
        try writeJSON(run, "stream-state.json", [
            "status": "running", "frames": 120, "processedFramesPerSecond": 9.5, "committedRows": 260,
            "consumedRows": 240, "backlogRows": 20, "backlogExposureSeconds": 75.0, "lastSceneUtcUs": "1795494597700000",
            "pendingUploads": ["scenes": 3, "mediaSegments": 1], "updatedUnix": now.timeIntervalSince1970,
        ])
        let (processing, alerts) = readProcessingStatus(runDir: run, now: now, timestamp: "t")
        let value = try #require(processing)
        #expect(value.status == "running")
        #expect(value.backlogRows == 20)
        #expect(value.pendingScenes == 3)
        #expect(value.pendingMediaSegments == 1)
        #expect(alerts.map(\.level) == ["warning"])
        #expect(readProcessingStatus(runDir: run.appendingPathComponent("none"), now: now, timestamp: "t").0 == nil)
    }

    @Test("积压接近 300 秒预算、失联与失败时告警")
    func processingAlerts() throws {
        let run = try temporaryDirectory()
        let now = Date()
        try writeJSON(run, "stream-state.json", ["status": "running", "frames": 1, "backlogExposureSeconds": 250.0,
                                                 "updatedUnix": now.timeIntervalSince1970])
        #expect(readProcessingStatus(runDir: run, now: now, timestamp: "t").1.map(\.level) == ["error"])

        try writeJSON(run, "stream-state.json", ["status": "running", "frames": 1, "backlogExposureSeconds": 250.0,
                                                 "updatedUnix": now.timeIntervalSince1970 - 30])
        let (stale, staleAlerts) = readProcessingStatus(runDir: run, now: now, timestamp: "t")
        #expect(stale?.status == "unresponsive")
        #expect(staleAlerts.count == 1)

        try writeJSON(run, "stream-state.json", ["status": "failed", "frames": 20, "error": "仍有待提交的场景",
                                                 "pendingDelivery": ["场景": "HTTP 503"], "pendingUploads": NSNull(),
                                                 "updatedUnix": now.timeIntervalSince1970])
        let (failed, failedAlerts) = readProcessingStatus(runDir: run, now: now, timestamp: "t")
        #expect(failed?.pendingScenes == nil)
        #expect(failed?.error == "仍有待提交的场景 · 场景: HTTP 503")
        #expect(failedAlerts.first?.message.contains("HTTP 503") == true)
    }

    @Test("指标只保留窗口内采样并给出队列年龄百分位")
    func metricsWindow() {
        let history = MetricsHistory(windowSeconds: 10)
        let start = Date(timeIntervalSince1970: 1_000)
        func status(_ age: Double?, _ state: String = "running") -> SystemStatus {
            var value = readCaptureStatus(root: nil, setup: nil, process: CaptureProcessState(), now: start)
            value.processing = ProcessingStatus(status: state, frames: 0, processedFramesPerSecond: nil, committedRows: nil,
                                                consumedRows: nil, backlogRows: nil, backlogExposureSeconds: age,
                                                lastSceneUtcUs: nil, pendingScenes: nil, pendingMediaSegments: nil,
                                                error: nil, updatedAt: nil)
            return value
        }
        for second in 0..<15 {
            history.record(status(Double(second)), now: start.addingTimeInterval(Double(second)))
        }
        // 已结束的运行不再计入队列年龄。
        history.record(status(99, "completed"), now: start.addingTimeInterval(15))
        let report = history.report(now: start.addingTimeInterval(15))
        #expect(report.timeSeries.count == 10)
        #expect(report.timeSeries.first?.timestampMs == 1_006_000)
        #expect(report.timeSeries.last?.backlogExposureSeconds == nil)
        #expect(report.summary.backlogExposureSecondsMax == 14)
        #expect(report.summary.backlogExposureSecondsP50 == 10)
        #expect(percentile([], 0.5) == nil)
        #expect(percentile([1, 2, 3, 4], 0.95) == 4)
        #expect(percentile([1, 2, 3, 4], 0.5) == 2)
    }

    @Test("告警按严重程度排序且同级保持顺序")
    func alertOrder() {
        let alerts = [("warning", "a"), ("critical", "b"), ("warning", "c"), ("error", "d")]
            .map { Alert(level: $0.0, cameraId: nil, message: $0.1, timestamp: "") }
        #expect(sortedAlerts(alerts).map(\.message) == ["b", "d", "a", "c"])
    }

    @Test("SystemStatus 编码保留缺测为 null")
    func encodeStatus() throws {
        let status = readCaptureStatus(root: nil, setup: nil, process: CaptureProcessState(), now: Date())
        let json = try #require(String(data: JSONEncoder().encode(status), encoding: .utf8))
        #expect(json.contains("\"matchState\":\"idle\""))
        #expect(json.contains("\"remainingRecordingSeconds\":null") || !json.contains("remainingRecordingSeconds"))
    }
}

private func temporaryDirectory() throws -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("webui-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

private func writeJSON(_ root: URL, _ path: String, _ object: [String: Any]) throws {
    let url = root.appendingPathComponent(path)
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try JSONSerialization.data(withJSONObject: object).write(to: url)
}

private func writePeriod(_ root: URL, _ camera: String, _ epoch: String, _ status: [String: Any]) throws {
    try writeJSON(root, "raw/\(camera)/\(epoch)/status.json", status)
    let period = root.appendingPathComponent("raw/\(camera)/\(epoch)")
    FileManager.default.createFile(atPath: period.appendingPathComponent("init.mp4").path, contents: Data())
}
