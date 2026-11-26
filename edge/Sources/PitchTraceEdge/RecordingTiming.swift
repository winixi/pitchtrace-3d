import Foundation

struct RecordingError: LocalizedError {
    let errorDescription: String?

    init(_ message: String) {
        errorDescription = message
    }
}

let maxSafeMediaInteger: Int64 = 9_007_199_254_740_991
let maxMediaBytes = 128 * 1024 * 1024

func exposureMicroseconds(_ timestamp: String) -> Int64? {
    guard let value = Int64(timestamp), value >= 0, String(value) == timestamp else { return nil }
    return value
}

func isMediaCameraID(_ cameraID: String) -> Bool {
    cameraID.range(of: #"^[A-Za-z0-9_-]{1,64}\z"#, options: .regularExpression) != nil
}

struct MediaTimeBase: Codable, Equatable {
    let numerator: Int32
    let denominator: Int32

    func seconds(for ticks: Int64) -> Double {
        Double(ticks) * Double(numerator) / Double(denominator)
    }
}

struct ExposureMapping: Decodable {
    let pts: Int64
    let captureUtcUs: String?
    let frameNumber: Int64?
}

struct MediaFrame: Codable, Equatable {
    let pts: Int64
    let durationPts: Int64
    let captureUtcUs: String
}

struct ReplayFrame: Encodable {
    let cameraId: String
    let segment: Int64
    let timeBase: MediaTimeBase
    let frameNumber: Int64?
    let frame: MediaFrame
    let utc: Int64

    private enum CodingKeys: String, CodingKey {
        case cameraId, segment, timeBase, frameNumber, pts, durationPts, captureUtcUs
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(cameraId, forKey: .cameraId)
        try values.encode(segment, forKey: .segment)
        try values.encode(timeBase, forKey: .timeBase)
        try values.encodeIfPresent(frameNumber, forKey: .frameNumber)
        try values.encode(frame.pts, forKey: .pts)
        try values.encode(frame.durationPts, forKey: .durationPts)
        try values.encode(frame.captureUtcUs, forKey: .captureUtcUs)
    }
}

extension ReplayFrame: Decodable {
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        cameraId = try values.decode(String.self, forKey: .cameraId)
        segment = try values.decode(Int64.self, forKey: .segment)
        timeBase = try values.decode(MediaTimeBase.self, forKey: .timeBase)
        frameNumber = try values.decodeIfPresent(Int64.self, forKey: .frameNumber)
        let timestamp = try values.decode(String.self, forKey: .captureUtcUs)
        guard let exposure = exposureMicroseconds(timestamp) else {
            throw RecordingError("曝光 UTC 无效")
        }
        utc = exposure
        frame = MediaFrame(
            pts: try values.decode(Int64.self, forKey: .pts),
            durationPts: try values.decode(Int64.self, forKey: .durationPts),
            captureUtcUs: timestamp
        )
    }
}

struct MediaIndex: Codable {
    let initSha256: String
    let segmentSha256: String
    let segmentBytes: Int
    let timeBase: MediaTimeBase
    let frames: [MediaFrame]
}

struct VideoFrame: Decodable {
    let pts: Int64?
    let duration: Int64?
    let packetDuration: Int64?
    let keyFrame: Int

    enum CodingKeys: String, CodingKey {
        case pts, duration
        case packetDuration = "pkt_duration"
        case keyFrame = "key_frame"
    }
}

struct CameraTiming {
    let cameraId: String
    private var previous: ReplayFrame?

    init(cameraId: String) {
        self.cameraId = cameraId
    }

    mutating func align(
        segment: Int64,
        timeBase: MediaTimeBase,
        videoFrames: [VideoFrame],
        exposures: [ExposureMapping],
        gaps: inout [String]
    ) throws -> [ReplayFrame] {
        let location = "机位 \(cameraId)，片段 \(segment)"
        guard timeBase.numerator > 0, timeBase.denominator > 0,
              (1...4096).contains(videoFrames.count) else {
            throw RecordingError("\(location)：时基或帧数超出 v2 范围")
        }
        var byPTS: [Int64: ExposureMapping] = [:]
        for exposure in exposures {
            guard byPTS.updateValue(exposure, forKey: exposure.pts) == nil else {
                throw RecordingError("\(location)，PTS \(exposure.pts)：重复的曝光映射")
            }
        }
        let presentation = videoFrames.sorted { ($0.pts ?? Int64.min) < ($1.pts ?? Int64.min) }
        guard let first = presentation.first, let firstPTS = first.pts, first.keyFrame == 1 else {
            throw RecordingError("\(location)：首个展示帧缺少 PTS 或关键帧标记")
        }
        if let previous, previous.timeBase != timeBase {
            throw RecordingError("\(location)：媒体时基改变，请按媒体周期拆分录像")
        }
        if let previous, segment - previous.segment > 1 {
            gaps.append("\(location)：缺少片段 \(previous.segment + 1)…\(segment - 1)")
        }

        var frames: [ReplayFrame] = []
        for video in presentation {
            guard let pts = video.pts, (-maxSafeMediaInteger...maxSafeMediaInteger).contains(pts),
                  let duration = video.duration ?? video.packetDuration,
                  (1...maxSafeMediaInteger).contains(duration),
                  pts <= maxSafeMediaInteger - duration,
                  (1...1_000_000).contains(timeBase.seconds(for: duration) * 1_000_000) else {
                throw RecordingError("\(location)，PTS \(video.pts.map(String.init) ?? "缺失")：帧时长或 PTS 超出 v2 范围")
            }
            let position = "\(location)，PTS \(pts)"
            guard let exposure = byPTS.removeValue(forKey: pts) else {
                throw RecordingError("\(position)：缺少曝光映射")
            }
            guard let timestamp = exposure.captureUtcUs, let utc = exposureMicroseconds(timestamp) else {
                throw RecordingError("\(position)：曝光 UTC 缺失或无效")
            }
            if let number = exposure.frameNumber, number < 0 {
                throw RecordingError("\(position)：原始帧号无效")
            }
            if let previous {
                guard utc > previous.utc else {
                    throw RecordingError("\(position)：曝光 UTC 未严格递增")
                }
                let previousEnd = previous.frame.pts + previous.frame.durationPts
                guard pts >= previousEnd else {
                    throw RecordingError("\(position)：展示区间重叠或 PTS 倒退")
                }
                if pts > previousEnd {
                    gaps.append("\(position)：展示时间缺口 [\(previousEnd), \(pts))")
                }
                if let old = previous.frameNumber, let new = exposure.frameNumber {
                    guard new > old else {
                        throw RecordingError("\(position)：原始帧号未严格递增")
                    }
                    if new - old > 1 {
                        gaps.append("\(position)：缺少原始帧 \(old + 1)…\(new - 1)")
                    }
                }
            }
            let frame = ReplayFrame(
                cameraId: cameraId, segment: segment, timeBase: timeBase,
                frameNumber: exposure.frameNumber,
                frame: MediaFrame(pts: pts, durationPts: duration, captureUtcUs: timestamp),
                utc: utc
            )
            frames.append(frame)
            previous = frame
        }
        if let unmatched = byPTS.keys.min() {
            throw RecordingError("\(location)，PTS \(unmatched)：曝光映射没有对应的视频帧")
        }
        let span = frames[frames.count - 1].frame
        let ticks = span.pts + span.durationPts - firstPTS
        guard ticks <= maxSafeMediaInteger, timeBase.seconds(for: ticks) <= 120 else {
            throw RecordingError("\(location)：片段持续时间超出 v2 范围")
        }
        return frames
    }
}
