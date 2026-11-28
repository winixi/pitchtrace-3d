import Darwin
import Foundation

@main
enum PitchTraceEdge {
    static func main() async {
        let usage = """
        用法：
          pitchtrace-edge web-ui [--bind 地址:端口] [--match-dir 目录]
          pitchtrace-edge replay <2–8 机位录像目录> <新输出目录> [--metrics]
          pitchtrace-edge audit-sync <frames.ndjson> <闪光标注.json>
          pitchtrace-edge audit-visibility <frames.ndjson> <可见性标注.json>
          pitchtrace-edge calibrate-intrinsics <棋盘格照片清单.json>
          pitchtrace-edge calibrate <场地标定.json>
          pitchtrace-edge audit-calibration <标定报告.json> <机位巡检.json>
          pitchtrace-edge capture <采集配置.json> <采集根目录>
          pitchtrace-edge camera-capture <媒体周期目录>
          pitchtrace-edge attach-exposures <采集根目录> <机位> <媒体周期> <曝光清单.ndjson>
          pitchtrace-edge process-match <比赛配置.json> <replay 导出目录> <场景库.sqlite>
          pitchtrace-edge reconstruct-match <比赛配置.json> <二维观测.ndjson> <场景库.sqlite>
          pitchtrace-edge run-match <比赛配置.json> <运行目录>
          pitchtrace-edge stream-match <比赛配置.json> <采集根目录> <运行目录>
          pitchtrace-edge evaluate-match <场景库.sqlite> <比赛 ID> <场景周期> <真值.ndjson>
          pitchtrace-edge retention <运行目录> [--apply]
          pitchtrace-edge export-scenes <场景库.sqlite> <比赛 ID> <场景周期> [筛选项]
          pitchtrace-edge publish-scenes <场景库.sqlite> <网关地址> <比赛 ID> <场景周期> [--backfill-bytes-per-second N]
          pitchtrace-edge compare-scenes <场景库.sqlite> <比赛 ID> <旧周期> <新周期>
          pitchtrace-edge scene-evidence <场景库.sqlite> <比赛 ID> <场景周期> <seq>
          pitchtrace-edge match-info <场景库.sqlite> <比赛 ID> <场景周期>
          pitchtrace-edge vision-worker [Core ML 检测模型 [--person-and-ball] [--ball-search]] [--individual-ocr] [--ocr-hints] [--frame-control] [--compute-units 计算单元]
          pitchtrace-edge publish-media <网关地址> <比赛 ID> <含 epochs 的机位目录>
          pitchtrace-edge publish-media <网关地址> <比赛 ID> <媒体周期> <平铺机位目录>
        """
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.isEmpty || arguments == ["--help"] {
            print(usage)
            return
        }
        let operation: String
        do {
            switch (arguments[0], arguments.count) {
            case ("web-ui", _):
                operation = "Web UI 监控界面"
                do {
                    let command = try WebUICommand.parse(arguments: Array(arguments.dropFirst()))
                    try await command.run()
                } catch is ExitSuccess {
                    return
                } catch let error as ValidationError {
                    print("错误: \(error.message)")
                    print("")
                    WebUICommand.printHelp()
                    Darwin.exit(1)
                }
            case ("replay", 3),
                 ("replay", 4) where arguments[3] == "--metrics":
                operation = "录像重放"
                let input = URL(fileURLWithPath: arguments[1], isDirectory: true)
                let output = URL(fileURLWithPath: arguments[2], isDirectory: true)
                let start = ContinuousClock.now
                let summary = try exportRecording(from: input, to: output)
                if arguments.count == 4 {
                    let elapsed = start.duration(to: .now).components
                    let seconds = Double(elapsed.seconds) + Double(elapsed.attoseconds) / 1e18
                    let metrics = try ReplayMetrics(
                        frames: summary.frames, gaps: summary.gaps, elapsedSeconds: seconds,
                        framesPerSecond: Double(summary.frames) / seconds,
                        edgePeakRssBytes: peakRssBytes(RUSAGE_SELF),
                        childPeakRssBytes: peakRssBytes(RUSAGE_CHILDREN)
                    )
                    try writeJSON(metrics)
                } else {
                    print("已导出 \(summary.frames) 帧，记录 \(summary.gaps) 条缺口。")
                }
            case ("audit-sync", 3):
                operation = "曝光对时检查"
                let frames = URL(fileURLWithPath: arguments[1])
                let annotations = URL(fileURLWithPath: arguments[2])
                let report = try await auditExposure(frames: frames, annotations: annotations)
                try writeJSON(report)
            case ("audit-visibility", 3):
                operation = "分区双视角可见率检查"
                let frames = URL(fileURLWithPath: arguments[1])
                let annotations = URL(fileURLWithPath: arguments[2])
                let report = try await auditVisibility(frames: frames, annotations: annotations)
                try writeJSON(report)
            case ("calibrate", 2), ("calibrate-intrinsics", 2):
                let isLensCalibration = arguments[0] == "calibrate-intrinsics"
                operation = isLensCalibration ? "镜头内参标定" : "离线场地标定"
                let scriptName = isLensCalibration ? "intrinsics" : "calibrate"
                try runPython(scriptName, Array(arguments.dropFirst()))
            case ("capture", 3), ("attach-exposures", 5):
                operation = "分机位采集与曝光映射"
                let prefix = arguments[0] == "capture" ? [] : ["attach"]
                try runPython("capture", prefix + arguments.dropFirst())
            case ("camera-capture", 2):
                operation = "单机位相机采集"
                let stop = StopFlag()
                stop.installSignalHandlers()
                try runCameraCapture(directory: URL(fileURLWithPath: arguments[1], isDirectory: true), stop: stop)
            case ("audit-calibration", 3):
                operation = "机位巡检"
                try runPython("audit_calibration", Array(arguments.dropFirst()))
            case ("evaluate-match", 5):
                operation = "离线真值评估"
                try runPython("evaluate_match", Array(arguments.dropFirst()))
            case ("retention", 2),
                 ("retention", 3) where arguments[2] == "--apply":
                operation = "运行目录留存检查"
                try runPython("retention", Array(arguments.dropFirst()))
            case ("vision-worker", _):
                operation = "单视角观测"
                try runVisionWorker(options: VisionWorkerOptions(arguments: Array(arguments.dropFirst())))
            case ("process-match", 4), ("reconstruct-match", 4), ("stream-match", 4), ("run-match", 3):
                operation = "比赛场景重建"
                guard let executable = Bundle.main.executableURL else { throw RecordingError("无法定位 Edge 程序") }
                if arguments[0] == "run-match" {
                    try runPython("run_match", Array(arguments.dropFirst()) + [executable.path])
                } else if arguments[0] == "stream-match" {
                    try runPython("stream_match", Array(arguments.dropFirst()) + [executable.path])
                } else {
                    try runPython("process_match", arguments + [executable.path])
                }
            case ("publish-scenes", 5), ("publish-scenes", 7), ("export-scenes", 4...), ("compare-scenes", 5),
                 ("scene-evidence", 5), ("match-info", 4):
                operation = "场景归档"
                let command: String
                switch arguments[0] {
                case "publish-scenes": command = "publish"
                case "export-scenes": command = "export"
                case "scene-evidence": command = "evidence"
                case "compare-scenes": command = "compare"
                default: command = "info"
                }
                try runPython("scene_archive", [command] + arguments.dropFirst())
            case ("publish-media", 4), ("publish-media", 5):
                operation = "原片提交"
                guard let gateway = URL(string: arguments[1]) else {
                    throw RecordingError("网关地址无效")
                }
                let mediaEpoch = arguments.count == 5 ? arguments[3] : nil
                let directory = URL(fileURLWithPath: arguments[arguments.count - 1], isDirectory: true).standardizedFileURL
                let session = URLSession(configuration: .ephemeral)
                defer { session.finishTasksAndInvalidate() }
                let count = try await publishCameraMedia(
                    from: directory, gateway: gateway, matchID: arguments[2],
                    mediaEpoch: mediaEpoch, credential: try DeviceCredential.fromEnvironment(), session: session
                )
                print("已确认机位 \(directory.lastPathComponent) 的 \(count) 个片段。")
            default:
                FileHandle.standardError.write(Data((usage + "\n").utf8))
                exit(2)
            }
        } catch {
            let message = "\(operation)失败：\(error.localizedDescription)\n"
            FileHandle.standardError.write(Data(message.utf8))
            exit(1)
        }
    }
}

private func runPython(_ name: String, _ arguments: [String]) throws {
    guard let script = Bundle.module.url(forResource: name, withExtension: "py", subdirectory: "Geometry") else {
        throw RecordingError("缺少随 Edge 分发的 \(name).py")
    }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    process.arguments = ["python3", "-B", script.path] + arguments
    // capture.py 为 MVS 与模拟相机启动 camera-capture 子进程时使用同一程序。
    if let executable = Bundle.main.executableURL {
        var environment = ProcessInfo.processInfo.environment
        environment["PITCHTRACE_EDGE_EXECUTABLE"] = executable.path
        process.environment = environment
    }
    try process.run()
    process.waitUntilExit()
    if process.terminationStatus != 0 { exit(1) }
}

private func writeJSON(_ value: some Encodable) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    var json = try encoder.encode(value)
    json.append(0x0A)
    try FileHandle.standardOutput.write(contentsOf: json)
}

private struct ReplayMetrics: Encodable {
    let frames: Int
    let gaps: Int
    let elapsedSeconds: Double
    let framesPerSecond: Double
    let edgePeakRssBytes: Int
    let childPeakRssBytes: Int
}

// Darwin 以字节报告最高驻留内存，子进程项取已回收子进程中的最大值。
private func peakRssBytes(_ who: Int32) throws -> Int {
    var usage = rusage()
    guard getrusage(who, &usage) == 0 else {
        throw RecordingError("无法读取重放内存计量：\(String(cString: strerror(errno)))")
    }
    return usage.ru_maxrss
}
