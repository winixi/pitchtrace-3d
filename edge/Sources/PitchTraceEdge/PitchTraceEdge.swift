import Darwin
import Foundation

@main
enum PitchTraceEdge {
    static func main() async {
        let usage = """
        用法：
          pitchtrace-edge replay <双机位录像目录> <新输出目录>
          pitchtrace-edge audit-sync <frames.ndjson> <闪光标注.json>
        """
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.isEmpty || arguments == ["--help"] {
            print(usage)
            return
        }
        guard arguments.count == 3, ["replay", "audit-sync"].contains(arguments[0]) else {
            FileHandle.standardError.write(Data((usage + "\n").utf8))
            exit(2)
        }

        do {
            if arguments[0] == "replay" {
                let input = URL(fileURLWithPath: arguments[1], isDirectory: true)
                let output = URL(fileURLWithPath: arguments[2], isDirectory: true)
                let summary = try exportRecording(from: input, to: output)
                print("已导出 \(summary.frames) 帧，记录 \(summary.gaps) 条缺口。")
            } else {
                let frames = URL(fileURLWithPath: arguments[1])
                let annotations = URL(fileURLWithPath: arguments[2])
                let report = try await auditExposure(frames: frames, annotations: annotations)
                let encoder = JSONEncoder()
                encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                var json = try encoder.encode(report)
                json.append(0x0A)
                try FileHandle.standardOutput.write(contentsOf: json)
            }
        } catch {
            let operation = arguments[0] == "replay" ? "录像重放" : "曝光对时检查"
            let message = "\(operation)失败：\(error.localizedDescription)\n"
            FileHandle.standardError.write(Data(message.utf8))
            exit(1)
        }
    }
}
