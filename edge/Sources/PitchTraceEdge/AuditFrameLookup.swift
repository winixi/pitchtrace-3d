import Foundation

struct FrameReference: Codable, Hashable {
    let mediaEpoch: String?
    let segment: Int64
    let pts: Int64

    func validate(context: String) throws {
        guard (0...maxSafeMediaInteger).contains(segment),
              (-maxSafeMediaInteger...maxSafeMediaInteger).contains(pts) else {
            throw RecordingError("\(context)：片段或 PTS 无效")
        }
        if let mediaEpoch, !isMediaCameraID(mediaEpoch) {
            throw RecordingError("\(context)：媒体周期 ID 无效")
        }
    }
}

struct CameraFrameReference: Hashable {
    let cameraId: String
    let frame: FrameReference
}

func validateAuditCameras(_ cameras: [String]) throws {
    guard (2...4).contains(cameras.count), Set(cameras).count == cameras.count,
          cameras.allSatisfy(isMediaCameraID) else {
        throw RecordingError("审计标注需要 2 至 4 个不同的 v2 机位 ID")
    }
}

struct AuditFrameLookup {
    private var selected: [CameraFrameReference: ReplayFrame] = [:]
    private var camerasWithEpochs: Set<String> = []

    init(frames: URL, cameras: [String], references: Set<CameraFrameReference>) async throws {
        let decoder = JSONDecoder()
        var seenCameras: Set<String> = []
        var lineNumber = 0
        for try await line in frames.lines {
            lineNumber += 1
            if line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
            let frame: ReplayFrame
            do {
                frame = try decoder.decode(ReplayFrame.self, from: Data(line.utf8))
            } catch {
                throw RecordingError("\(frames.lastPathComponent) 第 \(lineNumber) 行：\(error.localizedDescription)")
            }
            guard cameras.contains(frame.cameraId) else {
                throw RecordingError("\(frames.lastPathComponent) 第 \(lineNumber) 行：未知机位 \(frame.cameraId)")
            }
            seenCameras.insert(frame.cameraId)
            if frame.mediaEpoch != nil { camerasWithEpochs.insert(frame.cameraId) }
            let reference = CameraFrameReference(
                cameraId: frame.cameraId,
                frame: FrameReference(mediaEpoch: frame.mediaEpoch, segment: frame.segment, pts: frame.frame.pts)
            )
            if references.contains(reference), selected.updateValue(frame, forKey: reference) != nil {
                throw RecordingError("\(frames.lastPathComponent) 第 \(lineNumber) 行：标注引用了重复的录像帧键")
            }
        }
        guard seenCameras == Set(cameras) else {
            throw RecordingError("录像帧清单缺少已声明机位：\(cameras.filter { !seenCameras.contains($0) }.joined(separator: "、"))")
        }
    }

    func resolve(camera: String, reference: FrameReference, context: String) throws -> ReplayFrame {
        if reference.mediaEpoch == nil, camerasWithEpochs.contains(camera) {
            throw RecordingError("\(context)，机位 \(camera)：请在帧引用中填写 mediaEpoch")
        }
        let key = CameraFrameReference(cameraId: camera, frame: reference)
        guard let frame = selected[key] else {
            let location = recordingLocation(cameraId: camera, mediaEpoch: reference.mediaEpoch)
            throw RecordingError("\(context)，\(location)，片段 \(reference.segment)，PTS \(reference.pts)：录像帧不存在")
        }
        return frame
    }
}
