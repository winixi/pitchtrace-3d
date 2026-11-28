import Foundation

/// 把机位最近一个已封口片段的首帧编码为 320×180 JPEG 缩略图。
///
/// 只读取 FFmpeg 已原子改名的 `<n>.m4s`，`.tmp` 是未提交的段。同一片段只解码一次。
final class PreviewRenderer: @unchecked Sendable {
    let root: URL
    let ffmpeg: String
    private let lock = NSLock()
    private var cache: [String: (segment: URL, jpeg: Data)] = [:]

    init(root: URL, ffmpeg: String = "ffmpeg") {
        self.root = root
        self.ffmpeg = ffmpeg
    }

    /// 机位没有可解码片段或解码失败时返回 nil。
    func jpeg(cameraId: String) -> Data? {
        guard let period = latestPeriodDirectory(root: root, cameraId: cameraId),
              let segment = latestSealedSegment(in: period) else { return nil }
        lock.lock()
        if let cached = cache[cameraId], cached.segment == segment {
            lock.unlock()
            return cached.jpeg
        }
        lock.unlock()
        guard let jpeg = render(init: period.appendingPathComponent("init.mp4"), segment: segment) else { return nil }
        lock.lock()
        cache[cameraId] = (segment, jpeg)
        lock.unlock()
        return jpeg
    }

    private func render(init initURL: URL, segment: URL) -> Data? {
        guard let head = try? Data(contentsOf: initURL), let body = try? Data(contentsOf: segment) else { return nil }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "mov", "-i", "pipe:0",
            "-frames:v", "1",
            "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2",
            "-q:v", "5", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
        ]
        let input = Pipe(), output = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        // 写入放在后台，避免 FFmpeg 已输出完毕而输入管道写满时互相等待。
        DispatchQueue.global().async {
            let writer = input.fileHandleForWriting
            try? writer.write(contentsOf: head)
            try? writer.write(contentsOf: body)
            try? writer.close()
        }
        let deadline = DispatchTime.now() + .seconds(5)
        let reading = DispatchGroup()
        nonisolated(unsafe) var jpeg = Data()
        reading.enter()
        DispatchQueue.global().async {
            jpeg = output.fileHandleForReading.readDataToEndOfFile()
            reading.leave()
        }
        if reading.wait(timeout: deadline) == .timedOut {
            process.terminate()
            reading.wait()
        }
        process.waitUntilExit()
        guard process.terminationStatus == 0, jpeg.starts(with: [0xFF, 0xD8]) else { return nil }
        return jpeg
    }
}

/// 周期目录中编号最大的已封口片段。
func latestSealedSegment(in period: URL) -> URL? {
    let names = (try? FileManager.default.contentsOfDirectory(atPath: period.path)) ?? []
    let numbers = names.compactMap { name -> Int? in
        guard name.hasSuffix(".m4s") else { return nil }
        return Int(name.dropLast(4))
    }
    return numbers.max().map { period.appendingPathComponent("\($0).m4s") }
}
