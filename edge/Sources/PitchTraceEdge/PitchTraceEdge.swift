import Darwin
import Foundation

@main
enum PitchTraceEdge {
    static func main() {
        let usage = "用法：pitchtrace-edge replay <双机位录像目录> <新输出目录>"
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.isEmpty || arguments == ["--help"] {
            print(usage)
            return
        }
        guard arguments.count == 3, arguments[0] == "replay" else {
            FileHandle.standardError.write(Data((usage + "\n").utf8))
            exit(2)
        }

        do {
            let input = URL(fileURLWithPath: arguments[1], isDirectory: true)
            let output = URL(fileURLWithPath: arguments[2], isDirectory: true)
            let summary = try exportRecording(from: input, to: output)
            print("已导出 \(summary.frames) 帧，记录 \(summary.gaps) 条缺口。")
        } catch {
            let message = "录像重放失败：\(error.localizedDescription)\n"
            FileHandle.standardError.write(Data(message.utf8))
            exit(1)
        }
    }
}
