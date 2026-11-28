import AVFoundation
import CoreMedia
import CoreVideo
import Foundation
import UniformTypeIdentifiers

/// 已送入编码器、等待所属片段封口的帧。
private struct PendingExposure {
    let pts: Int64
    let captureUtcUs: Int64
    let frameNumber: UInt64
}

struct RecorderSettings {
    let width: Int
    let height: Int
    let framerate: Double
    let bitrate: Int
    let segmentSeconds: Int
    /// 实时采集时编码器繁忙即丢帧；离线模拟时等待编码器。
    let realtime: Bool
}

/// 用 AVAssetWriter（底层为 VideoToolbox H.264）写出与 FFmpeg 采集相同布局的分段 fMP4：
/// `init.mp4`、`<n>.m4s`、`preview.m3u8`，并按展示顺序追加 `exposures.ndjson`。
final class SegmentedRecorder: NSObject, AVAssetWriterDelegate, @unchecked Sendable {
    static let timescale: Int32 = 90_000

    private let directory: URL
    private let settings: RecorderSettings
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private let adaptor: AVAssetWriterInputPixelBufferAdaptor
    private let exposures: FileHandle
    private let lock = NSLock()
    private var pending: [PendingExposure] = []
    private var segments: [(number: Int, seconds: Double)] = []
    private var failure: Error?
    private var lastPts: Int64?
    private var finished = false

    /// 已封口片段数和最后封口片段的首末曝光 UTC，供状态文件使用。
    private(set) var sealedSegments = 0
    private(set) var firstUtcUs: Int64?
    private(set) var lastUtcUs: Int64?

    init(directory: URL, settings: RecorderSettings, header: [String: String]) throws {
        self.directory = directory
        self.settings = settings
        guard settings.width >= 16, settings.height >= 16, settings.width % 2 == 0, settings.height % 2 == 0 else {
            throw RecordingError("编码尺寸须为不小于 16 的偶数")
        }
        writer = AVAssetWriter(contentType: UTType(AVFileType.mp4.rawValue)!)
        writer.outputFileTypeProfile = .mpeg4AppleHLS
        writer.preferredOutputSegmentInterval = CMTime(value: CMTimeValue(settings.segmentSeconds), timescale: 1)
        writer.initialSegmentStartTime = .zero
        input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: settings.width,
            AVVideoHeightKey: settings.height,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: settings.bitrate,
                // 与 FFmpeg 采集的 -bf 0 一致：展示序等于解码序，逐帧 PTS 可直接配对曝光。
                AVVideoAllowFrameReorderingKey: false,
                AVVideoMaxKeyFrameIntervalDurationKey: settings.segmentSeconds,
                AVVideoExpectedSourceFrameRateKey: settings.framerate,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
            ] as [String: Any],
        ])
        input.expectsMediaDataInRealTime = settings.realtime
        input.mediaTimeScale = Self.timescale
        adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: settings.width,
            kCVPixelBufferHeightKey as String: settings.height,
        ])
        let manifest = directory.appendingPathComponent("exposures.ndjson")
        guard FileManager.default.createFile(atPath: manifest.path, contents: nil) else {
            throw RecordingError("无法创建曝光清单")
        }
        exposures = try FileHandle(forWritingTo: manifest)
        super.init()
        var head = header
        head["timeBase"] = "1/\(Self.timescale)"
        try appendLine(head)
        writer.delegate = self
        guard writer.canAdd(input) else { throw RecordingError("编码器不接受当前视频设置") }
        writer.add(input)
        guard writer.startWriting() else {
            throw RecordingError("无法启动 H.264 编码：\(writer.error?.localizedDescription ?? "未知错误")")
        }
        writer.startSession(atSourceTime: .zero)
    }

    private func appendLine(_ object: Any) throws {
        var line = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])
        line.append(0x0a)
        try exposures.write(contentsOf: line)
    }

    private func checkFailure() throws {
        lock.lock(); defer { lock.unlock() }
        if let failure { throw failure }
    }

    /// 送入一帧。返回 false 表示实时模式下编码器繁忙而丢帧，该帧不进入曝光清单。
    func append(_ frame: CameraFrame, pts: Int64, captureUtcUs: Int64) throws -> Bool {
        try checkFailure()
        guard frame.width == settings.width, frame.height == settings.height else {
            throw RecordingError("原图尺寸在采集中改变，结束本周期")
        }
        if let lastPts, pts <= lastPts { throw RecordingError("展示时间未递增") }
        while !input.isReadyForMoreMediaData {
            if settings.realtime { return false }
            Thread.sleep(forTimeInterval: 0.002)
            try checkFailure()
        }
        guard let pool = adaptor.pixelBufferPool else { throw RecordingError("编码器缓冲池不可用") }
        var buffer: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) == kCVReturnSuccess, let buffer else {
            throw RecordingError("无法分配编码缓冲")
        }
        CVPixelBufferLockBaseAddress(buffer, [])
        do {
            try convertToBGRA(frame, into: CVPixelBufferGetBaseAddress(buffer)!,
                              bytesPerRow: CVPixelBufferGetBytesPerRow(buffer))
        } catch {
            CVPixelBufferUnlockBaseAddress(buffer, [])
            throw error
        }
        CVPixelBufferUnlockBaseAddress(buffer, [])
        lock.lock()
        pending.append(PendingExposure(pts: pts, captureUtcUs: captureUtcUs, frameNumber: frame.frameNumber))
        lock.unlock()
        guard adaptor.append(buffer, withPresentationTime: CMTime(value: pts, timescale: Self.timescale)) else {
            throw RecordingError("H.264 编码失败：\(writer.error?.localizedDescription ?? "未知错误")")
        }
        lastPts = pts
        return true
    }

    /// 结束编码，等待最后一段封口并写出带结束标记的播放列表。
    func finish() throws {
        // 出错路径会再次调用；AVAssetWriter 不允许重复结束。
        guard !finished else { return }
        finished = true
        input.markAsFinished()
        let done = DispatchSemaphore(value: 0)
        writer.finishWriting { done.signal() }
        done.wait()
        try checkFailure()
        guard writer.status == .completed else {
            throw RecordingError("编码封口失败：\(writer.error?.localizedDescription ?? "未知错误")")
        }
        lock.lock(); defer { lock.unlock() }
        guard pending.isEmpty else { throw RecordingError("有 \(pending.count) 帧未归入任何片段") }
        try writePlaylist(ended: true)
        try exposures.synchronize()
        try exposures.close()
    }

    func assetWriter(_ writer: AVAssetWriter, didOutputSegmentData segmentData: Data,
                     segmentType: AVAssetSegmentType, segmentReport: AVAssetSegmentReport?) {
        lock.lock(); defer { lock.unlock() }
        guard failure == nil else { return }
        do {
            switch segmentType {
            case .initialization:
                try atomicWrite(segmentData, to: "init.mp4")
            case .separable:
                try seal(segmentData, report: segmentReport)
            @unknown default:
                throw RecordingError("编码器输出了未知类型的片段")
            }
        } catch {
            failure = error
        }
    }

    // 调用方持有 lock。
    private func seal(_ data: Data, report: AVAssetSegmentReport?) throws {
        guard let track = report?.trackReports.first else { throw RecordingError("片段缺少时间报告") }
        let start = track.earliestPresentationTimeStamp.convertScale(Self.timescale, method: .roundHalfAwayFromZero).value
        let end = start + track.duration.convertScale(Self.timescale, method: .roundHalfAwayFromZero).value
        let count = pending.prefix { $0.pts < end }.count
        guard count > 0, pending[0].pts == start else {
            throw RecordingError("片段 \(sealedSegments) 的起始展示时间与送入的帧不符")
        }
        let number = sealedSegments
        // 先原子写入片段，再追加其曝光行，读取方看到完整的段号顺序。
        try atomicWrite(data, to: "\(number).m4s")
        for item in pending.prefix(count) {
            try appendLine(["segment": number, "pts": item.pts, "captureUtcUs": String(item.captureUtcUs),
                            "frameNumber": item.frameNumber] as [String: Any])
        }
        try exposures.synchronize()
        firstUtcUs = firstUtcUs ?? pending[0].captureUtcUs
        lastUtcUs = pending[count - 1].captureUtcUs
        pending.removeFirst(count)
        segments.append((number, CMTimeGetSeconds(track.duration)))
        sealedSegments += 1
        try writePlaylist(ended: false)
    }

    private func writePlaylist(ended: Bool) throws {
        let target = max(1, Int(segments.map(\.seconds).max()?.rounded(.up) ?? Double(settings.segmentSeconds)))
        var lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:\(target)", "#EXT-X-MEDIA-SEQUENCE:0",
                     "#EXT-X-PLAYLIST-TYPE:EVENT", "#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-MAP:URI=\"init.mp4\""]
        for segment in segments {
            lines.append(String(format: "#EXTINF:%.6f,", segment.seconds))
            lines.append("\(segment.number).m4s")
        }
        if ended { lines.append("#EXT-X-ENDLIST") }
        try atomicWrite(Data((lines.joined(separator: "\n") + "\n").utf8), to: "preview.m3u8")
    }

    private func atomicWrite(_ data: Data, to name: String) throws {
        let target = directory.appendingPathComponent(name)
        let temporary = directory.appendingPathComponent(name + ".tmp")
        try data.write(to: temporary)
        guard rename(temporary.path, target.path) == 0 else {
            throw RecordingError("无法提交 \(name)：\(String(cString: strerror(errno)))")
        }
    }
}
