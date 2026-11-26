import Darwin
import Foundation

@main
enum PitchTraceEdge {
    static func main() async {
        let usage = """
        用法：
          pitchtrace-edge replay <2–4 机位录像目录> <新输出目录> [--metrics]
          pitchtrace-edge audit-sync <frames.ndjson> <闪光标注.json>
          pitchtrace-edge audit-visibility <frames.ndjson> <可见性标注.json>
          pitchtrace-edge calibrate-intrinsics <棋盘格照片清单.json>
          pitchtrace-edge calibrate <场地标定.json>
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
                guard let script = Bundle.module.url(
                    forResource: scriptName, withExtension: "py", subdirectory: "Geometry"
                ) else {
                    throw RecordingError("缺少随 Edge 分发的离线标定脚本")
                }
                let process = Process()
                process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
                process.arguments = ["python3", "-B", script.path, arguments[1]]
                try process.run()
                process.waitUntilExit()
                if process.terminationStatus != 0 { exit(1) }
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
                    mediaEpoch: mediaEpoch, session: session
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
