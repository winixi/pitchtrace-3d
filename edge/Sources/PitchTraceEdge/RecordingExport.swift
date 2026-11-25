import CryptoKit
import Foundation

private struct ExposureFile: Decodable {
    let frames: [ExposureMapping]
}

private struct VideoProbe: Decodable {
    struct Stream: Decodable {
        let codecName: String
        let timeBase: String

        enum CodingKeys: String, CodingKey {
            case codecName = "codec_name"
            case timeBase = "time_base"
        }
    }

    let streams: [Stream]
    let frames: [VideoFrame]
}

private func probeVideo(
    initialization: Data, segment: Data, scratch: URL, location: String
) throws -> (MediaTimeBase, [VideoFrame]) {
    try initialization.write(to: scratch)
    let clip = try FileHandle(forWritingTo: scratch)
    do {
        try clip.seekToEnd()
        try clip.write(contentsOf: segment)
        try clip.close()
    } catch {
        try? clip.close()
        throw error
    }

    let process = Process()
    let stdout = Pipe()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    process.arguments = [
        "ffprobe", "-v", "error", "-f", "mov", "-select_streams", "v:0",
        "-show_streams", "-show_frames", "-show_entries",
        "stream=codec_name,time_base:frame=key_frame,pts,duration,pkt_duration",
        "-of", "json", scratch.path,
    ]
    process.standardOutput = stdout
    try process.run()
    let data = stdout.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    guard process.terminationStatus == 0 else {
        throw RecordingError("\(location)：ffprobe 读取失败（退出码 \(process.terminationStatus)）")
    }
    let video: VideoProbe
    do {
        video = try JSONDecoder().decode(VideoProbe.self, from: data)
    } catch {
        throw RecordingError("\(location)：无法读取视频帧信息，\(error)")
    }
    guard let stream = video.streams.first, stream.codecName == "h264" else {
        throw RecordingError("\(location)：需要 H.264 视频轨道")
    }
    let ratio = stream.timeBase.split(separator: "/")
    guard ratio.count == 2, let numerator = Int32(ratio[0]), let denominator = Int32(ratio[1]) else {
        throw RecordingError("\(location)：无法读取媒体时基")
    }
    return (MediaTimeBase(numerator: numerator, denominator: denominator), video.frames)
}

private func mediaBytes(at file: URL) throws -> Data {
    let values = try file.resourceValues(forKeys: [.fileSizeKey])
    guard let count = values.fileSize, (1...maxMediaBytes).contains(count) else {
        throw RecordingError("\(file.path)：媒体文件大小超出 v2 范围")
    }
    let data = try Data(contentsOf: file)
    guard data.count == count else {
        throw RecordingError("\(file.path)：读取期间媒体文件大小发生变化")
    }
    return data
}

private func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private func segmentNumbers(in folder: URL, cameraId: String) throws -> [Int64] {
    let names = try FileManager.default.contentsOfDirectory(atPath: folder.path)
    var media: Set<Int64> = []
    var exposure: Set<Int64> = []
    for name in names.sorted() {
        let suffix: String
        if name.hasSuffix(".m4s") {
            suffix = ".m4s"
        } else if name.hasSuffix(".exposure.json") {
            suffix = ".exposure.json"
        } else {
            continue
        }
        let stem = name.dropLast(suffix.count)
        guard let number = Int64(stem), (0...maxSafeMediaInteger).contains(number),
              String(number) == stem else {
            throw RecordingError("机位 \(cameraId)：片段文件名无效，\(name)")
        }
        if suffix == ".m4s" {
            media.insert(number)
        } else {
            exposure.insert(number)
        }
    }
    if let missing = media.subtracting(exposure).min() {
        throw RecordingError("机位 \(cameraId)，片段 \(missing)：缺少曝光映射文件")
    }
    if let missing = exposure.subtracting(media).min() {
        throw RecordingError("机位 \(cameraId)，片段 \(missing)：缺少对应媒体文件")
    }
    guard !media.isEmpty else {
        throw RecordingError("机位 \(cameraId)：没有可重放的片段")
    }
    return media.sorted()
}

private func exportCamera(
    _ camera: URL, to output: URL, scratch: URL, encoder: JSONEncoder, gaps: inout [String]
) throws -> [ReplayFrame] {
    let cameraId = camera.lastPathComponent
    let inputSegments = camera.appendingPathComponent("segments", isDirectory: true)
    let outputSegments = output.appendingPathComponent("segments", isDirectory: true)
    let initialization = try mediaBytes(at: camera.appendingPathComponent("init.mp4"))
    let initializationSHA = sha256(initialization)
    let numbers = try segmentNumbers(in: inputSegments, cameraId: cameraId)
    try FileManager.default.createDirectory(at: outputSegments, withIntermediateDirectories: true)
    try initialization.write(to: output.appendingPathComponent("init.mp4"))

    var timing = CameraTiming(cameraId: cameraId)
    var frames: [ReplayFrame] = []
    for number in numbers {
        let location = "机位 \(cameraId)，片段 \(number)"
        let bytes = try mediaBytes(at: inputSegments.appendingPathComponent("\(number).m4s"))
        let (timeBase, videoFrames) = try probeVideo(
            initialization: initialization, segment: bytes, scratch: scratch, location: location
        )
        let mapping = inputSegments.appendingPathComponent("\(number).exposure.json")
        let exposures: [ExposureMapping]
        do {
            exposures = try JSONDecoder().decode(ExposureFile.self, from: Data(contentsOf: mapping)).frames
        } catch {
            throw RecordingError("\(location)：无法读取曝光映射，\(error)")
        }
        let aligned = try timing.align(
            segment: number, timeBase: timeBase, videoFrames: videoFrames, exposures: exposures, gaps: &gaps
        )
        let index = MediaIndex(
            initSha256: initializationSHA, segmentSha256: sha256(bytes), segmentBytes: bytes.count,
            timeBase: timeBase, frames: aligned.map(\.frame)
        )
        try bytes.write(to: outputSegments.appendingPathComponent("\(number).m4s"))
        try encoder.encode(index).write(to: outputSegments.appendingPathComponent("\(number).json"))
        frames.append(contentsOf: aligned)
    }
    return frames
}

func exportRecording(from input: URL, to output: URL) throws -> (frames: Int, gaps: Int) {
    let files = FileManager.default
    guard !files.fileExists(atPath: output.path) else {
        throw RecordingError("输出目录已存在：\(output.path)")
    }
    let cameras = try files.contentsOfDirectory(
        at: input, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles]
    ).filter { try $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true }
        .sorted { $0.lastPathComponent < $1.lastPathComponent }
    guard cameras.count == 2, cameras.allSatisfy({
        $0.lastPathComponent.range(of: #"^[A-Za-z0-9_-]{1,64}\z"#, options: .regularExpression) != nil
    }) else {
        throw RecordingError("录像目录需要恰好两个以 v2 机位 ID 命名的子目录")
    }

    let stage = output.deletingLastPathComponent().appendingPathComponent(".pitchtrace-\(UUID())")
    try files.createDirectory(at: stage, withIntermediateDirectories: false)
    defer { try? files.removeItem(at: stage) }
    let scratch = stage.appendingPathComponent(".probe.mp4")
    let encoder = JSONEncoder()
    encoder.outputFormatting = .sortedKeys
    var gaps: [String] = []
    var frames: [ReplayFrame] = []
    for camera in cameras {
        let directory = stage.appendingPathComponent(camera.lastPathComponent, isDirectory: true)
        frames.append(contentsOf: try exportCamera(
            camera, to: directory, scratch: scratch, encoder: encoder, gaps: &gaps
        ))
    }
    try files.removeItem(at: scratch)

    // 曝光相同时按机位 ID 排序，便于逐字节复现同一输入。
    frames.sort { ($0.utc, $0.cameraId) < ($1.utc, $1.cameraId) }
    let replay = stage.appendingPathComponent("frames.ndjson")
    try Data().write(to: replay)
    let writer = try FileHandle(forWritingTo: replay)
    do {
        var buffer = Data()
        for frame in frames {
            buffer.append(try encoder.encode(frame))
            buffer.append(0x0A)
            if buffer.count >= 64 * 1024 {
                try writer.write(contentsOf: buffer)
                buffer.removeAll(keepingCapacity: true)
            }
        }
        try writer.write(contentsOf: buffer)
        try writer.close()
    } catch {
        try? writer.close()
        throw error
    }
    let report = gaps.isEmpty ? "" : gaps.joined(separator: "\n") + "\n"
    try Data(report.utf8).write(to: stage.appendingPathComponent("gaps.txt"))
    try files.moveItem(at: stage, to: output)
    return (frames.count, gaps.count)
}
