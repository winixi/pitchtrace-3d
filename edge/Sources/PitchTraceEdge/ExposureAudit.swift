import Foundation

enum ExposureSampleSource: String, Codable {
    case synthetic
    case field
}

private struct FrameReference: Decodable, Hashable {
    let mediaEpoch: String?
    let segment: Int64
    let pts: Int64
}

private struct CameraFrameReference: Hashable {
    let cameraId: String
    let frame: FrameReference
}

private struct FlashAnnotations: Decodable {
    struct Event: Decodable {
        let id: String
        let frames: [String: FrameReference]
    }

    let sampleSource: ExposureSampleSource
    let cameras: [String]
    let events: [Event]

    func validatedReferences() throws -> Set<CameraFrameReference> {
        guard (2...4).contains(cameras.count), Set(cameras).count == cameras.count,
              cameras.allSatisfy(isMediaCameraID) else {
            throw RecordingError("闪光标注需要 2 至 4 个不同的 v2 机位 ID")
        }
        var eventIDs: Set<String> = []
        var references: Set<CameraFrameReference> = []
        for event in events {
            guard !event.id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  eventIDs.insert(event.id).inserted else {
                throw RecordingError("闪光事件 ID 为空或重复：\(event.id)")
            }
            for (camera, frame) in event.frames.sorted(by: { $0.key < $1.key }) {
                guard cameras.contains(camera) else {
                    throw RecordingError("闪光 \(event.id)：未知机位 \(camera)")
                }
                guard (0...maxSafeMediaInteger).contains(frame.segment),
                      (-maxSafeMediaInteger...maxSafeMediaInteger).contains(frame.pts) else {
                    throw RecordingError("闪光 \(event.id)，机位 \(camera)：片段或 PTS 无效")
                }
                if let epoch = frame.mediaEpoch, !isMediaCameraID(epoch) {
                    throw RecordingError("闪光 \(event.id)，机位 \(camera)：媒体周期 ID 无效")
                }
                references.insert(CameraFrameReference(cameraId: camera, frame: frame))
            }
        }
        return references
    }
}

struct ExposureAuditReport: Encodable {
    struct Event: Encodable {
        let id: String
        let frames: [ReplayFrame]
        let missingCameras: [String]
        let signedSkewUs: Int64?
        let spanUs: Int64?
    }

    let sampleSource: ExposureSampleSource
    let cameras: [String]
    let eventCount: Int
    let pairedCount: Int
    let unpairedCount: Int
    let thresholdUs: Int64
    let p95AbsUs: Int64?
    let maxAbsUs: Int64?
    let p95WithinThreshold: Bool?
    let events: [Event]
}

func auditExposure(frames: URL, annotations: URL) async throws -> ExposureAuditReport {
    let decoder = JSONDecoder()
    let marks: FlashAnnotations
    do {
        marks = try decoder.decode(FlashAnnotations.self, from: Data(contentsOf: annotations))
    } catch {
        throw RecordingError("无法读取闪光标注 \(annotations.lastPathComponent)：\(error)")
    }
    var references = try marks.validatedReferences()
    var selected: [CameraFrameReference: ReplayFrame] = [:]
    var seenCameras: Set<String> = []
    var camerasWithEpochs: Set<String> = []
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
        guard marks.cameras.contains(frame.cameraId) else {
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
    guard seenCameras == Set(marks.cameras) else {
        throw RecordingError("录像帧清单缺少已声明机位：\(marks.cameras.filter { !seenCameras.contains($0) }.joined(separator: "、"))")
    }

    let events = try marks.events.map { event in
        let observations = try marks.cameras.compactMap { camera -> ReplayFrame? in
            guard let reference = event.frames[camera] else { return nil }
            if reference.mediaEpoch == nil, camerasWithEpochs.contains(camera) {
                throw RecordingError("闪光 \(event.id)，机位 \(camera)：请在帧引用中填写 mediaEpoch")
            }
            let key = CameraFrameReference(cameraId: camera, frame: reference)
            guard let frame = selected[key] else {
                let location = recordingLocation(cameraId: camera, mediaEpoch: reference.mediaEpoch)
                throw RecordingError("闪光 \(event.id)，\(location)，片段 \(reference.segment)，PTS \(reference.pts)：录像帧不存在")
            }
            guard references.remove(key) != nil else {
                throw RecordingError("闪光 \(event.id)，机位 \(camera)：同一录像帧已用于其他闪光")
            }
            return frame
        }
        let missing = marks.cameras.filter { event.frames[$0] == nil }
        let exposures = observations.map(\.utc)
        let spanUs: Int64?
        if missing.isEmpty, let first = exposures.min(), let last = exposures.max() {
            spanUs = last - first
        } else {
            spanUs = nil
        }
        return ExposureAuditReport.Event(
            id: event.id,
            frames: observations,
            missingCameras: missing,
            signedSkewUs: missing.isEmpty && observations.count == 2 ? observations[1].utc - observations[0].utc : nil,
            spanUs: spanUs
        )
    }
    let offsets = events.compactMap(\.spanUs).sorted()
    // 最近秩的 P95 取第 ceil(0.95 × 样本数) 个值。
    let rank = offsets.count - offsets.count / 20
    let p95 = rank == 0 ? nil : offsets[rank - 1]
    let thresholdUs: Int64 = 5_000
    return ExposureAuditReport(
        sampleSource: marks.sampleSource, cameras: marks.cameras,
        eventCount: events.count, pairedCount: offsets.count, unpairedCount: events.count - offsets.count,
        thresholdUs: thresholdUs, p95AbsUs: p95, maxAbsUs: offsets.last,
        p95WithinThreshold: p95.map { $0 <= thresholdUs }, events: events
    )
}
