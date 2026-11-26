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

func mediaBytes(at file: URL, maxBytes: Int = maxMediaBytes) throws -> Data {
    let values = try file.resourceValues(forKeys: [.fileSizeKey])
    guard let count = values.fileSize, count > 0, count <= maxBytes else {
        throw RecordingError("\(file.path)：媒体文件大小超出 v2 范围")
    }
    let data = try Data(contentsOf: file)
    guard data.count == count else {
        throw RecordingError("\(file.path)：读取期间媒体文件大小发生变化")
    }
    return data
}

func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

func segmentNumbers(
    in folder: URL, cameraId: String, indexSuffix: String = ".exposure.json"
) throws -> [Int64] {
    let names = try FileManager.default.contentsOfDirectory(atPath: folder.path)
    var media: Set<Int64> = []
    var indexes: Set<Int64> = []
    for name in names.sorted() {
        let suffix: String
        if name.hasSuffix(".m4s") {
            suffix = ".m4s"
        } else if name.hasSuffix(indexSuffix) {
            suffix = indexSuffix
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
            indexes.insert(number)
        }
    }
    if let missing = media.subtracting(indexes).min() {
        let kind = indexSuffix == ".exposure.json" ? "曝光映射" : "媒体索引"
        throw RecordingError("机位 \(cameraId)，片段 \(missing)：缺少\(kind)文件")
    }
    if let missing = indexes.subtracting(media).min() {
        throw RecordingError("机位 \(cameraId)，片段 \(missing)：缺少对应媒体文件")
    }
    guard !media.isEmpty else {
        throw RecordingError("机位 \(cameraId)：没有可重放的片段")
    }
    return media.sorted()
}

private struct CameraExport {
    private let inputSegments: URL
    private let outputSegments: URL
    private let initialization: Data
    private let initializationSHA: String
    private var segments: ArraySlice<Int64>
    private var frames: ArraySlice<ReplayFrame> = []
    private var timing: CameraTiming
    private let gapLog: FileHandle
    private(set) var gapCount = 0

    init(camera: URL, stage: URL, work: URL) throws {
        let cameraId = camera.lastPathComponent
        let output = stage.appendingPathComponent(cameraId, isDirectory: true)
        inputSegments = camera.appendingPathComponent("segments", isDirectory: true)
        outputSegments = output.appendingPathComponent("segments", isDirectory: true)
        segments = try segmentNumbers(in: inputSegments, cameraId: cameraId)[...]
        initialization = try mediaBytes(at: camera.appendingPathComponent("init.mp4"))
        initializationSHA = sha256(initialization)
        timing = CameraTiming(cameraId: cameraId)
        try FileManager.default.createDirectory(at: outputSegments, withIntermediateDirectories: true)
        try initialization.write(to: output.appendingPathComponent("init.mp4"))

        let gapFile = work.appendingPathComponent("\(cameraId).txt")
        try Data().write(to: gapFile)
        gapLog = try FileHandle(forUpdating: gapFile)
    }

    mutating func nextFrame(scratch: URL, encoder: JSONEncoder) throws -> ReplayFrame? {
        if let frame = frames.popFirst() { return frame }
        guard let number = segments.popFirst() else { return nil }

        let location = "机位 \(timing.cameraId)，片段 \(number)"
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
        var gaps: [String] = []
        let aligned = try timing.align(
            segment: number, timeBase: timeBase, videoFrames: videoFrames, exposures: exposures, gaps: &gaps
        )
        let index = MediaIndex(
            initSha256: initializationSHA, segmentSha256: sha256(bytes), segmentBytes: bytes.count,
            timeBase: timeBase, frames: aligned.map(\.frame)
        )
        try bytes.write(to: outputSegments.appendingPathComponent("\(number).m4s"))
        try encoder.encode(index).write(to: outputSegments.appendingPathComponent("\(number).json"))
        if !gaps.isEmpty {
            try gapLog.write(contentsOf: Data((gaps.joined(separator: "\n") + "\n").utf8))
            gapCount += gaps.count
        }
        frames = aligned[...]
        return frames.popFirst()
    }

    func appendGaps(to report: FileHandle) throws {
        try gapLog.seek(toOffset: 0)
        while let chunk = try gapLog.read(upToCount: 64 * 1024), !chunk.isEmpty {
            try report.write(contentsOf: chunk)
        }
        try gapLog.close()
    }
}

// 每路已按曝光严格递增，选各路首帧即可按 (曝光 UTC, 机位 ID) 归并。
func mergeReplayFrames(
    cameraCount: Int, next: (Int) throws -> ReplayFrame?, write: (ReplayFrame) throws -> Void
) throws -> Int {
    var heads = try (0..<cameraCount).map(next)
    var count = 0
    while true {
        var earliest: (index: Int, frame: ReplayFrame)?
        for (index, head) in heads.enumerated() {
            guard let head else { continue }
            if let current = earliest?.frame, (current.utc, current.cameraId) <= (head.utc, head.cameraId) {
                continue
            }
            earliest = (index, head)
        }
        guard let earliest else { return count }
        try write(earliest.frame)
        count += 1
        heads[earliest.index] = try next(earliest.index)
    }
}

private func writeFile<T>(at path: URL, _ write: (FileHandle) throws -> T) throws -> T {
    try Data().write(to: path)
    let file = try FileHandle(forWritingTo: path)
    do {
        let result = try write(file)
        try file.close()
        return result
    } catch {
        try? file.close()
        throw error
    }
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
    guard (2...4).contains(cameras.count), cameras.allSatisfy({
        isMediaCameraID($0.lastPathComponent)
    }) else {
        throw RecordingError("录像目录需要 2–4 个以 v2 机位 ID 命名的子目录")
    }

    let stage = output.deletingLastPathComponent().appendingPathComponent(".pitchtrace-\(UUID())")
    try files.createDirectory(at: stage, withIntermediateDirectories: false)
    defer { try? files.removeItem(at: stage) }
    let work = stage.appendingPathComponent(".work", isDirectory: true)
    try files.createDirectory(at: work, withIntermediateDirectories: false)
    var sources = try cameras.map { try CameraExport(camera: $0, stage: stage, work: work) }
    let scratch = work.appendingPathComponent("probe.mp4")
    let encoder = JSONEncoder()
    encoder.outputFormatting = .sortedKeys
    let frameCount = try writeFile(at: stage.appendingPathComponent("frames.ndjson")) { file in
        var buffer = Data()
        let count = try mergeReplayFrames(cameraCount: sources.count) { camera in
            try sources[camera].nextFrame(scratch: scratch, encoder: encoder)
        } write: { frame in
            buffer.append(try encoder.encode(frame))
            buffer.append(0x0A)
            if buffer.count >= 64 * 1024 {
                try file.write(contentsOf: buffer)
                buffer.removeAll(keepingCapacity: true)
            }
        }
        try file.write(contentsOf: buffer)
        return count
    }
    // 各机位交错处理时先独立记缺口，最终报告沿用机位、片段、展示帧的顺序。
    try writeFile(at: stage.appendingPathComponent("gaps.txt")) { report in
        for source in sources { try source.appendGaps(to: report) }
    }
    try files.removeItem(at: work)
    try files.moveItem(at: stage, to: output)
    return (frameCount, sources.reduce(0) { $0 + $1.gapCount })
}
