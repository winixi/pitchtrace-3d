import Foundation

enum ExposureSampleSource: String, Codable {
    case synthetic
    case field
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
        try validateAuditCameras(cameras)
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
                try frame.validate(context: "闪光 \(event.id)，机位 \(camera)")
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
    let lookup = try await AuditFrameLookup(frames: frames, cameras: marks.cameras, references: references)
    let events = try marks.events.map { event in
        let observations = try marks.cameras.compactMap { camera -> ReplayFrame? in
            guard let reference = event.frames[camera] else { return nil }
            let frame = try lookup.resolve(camera: camera, reference: reference, context: "闪光 \(event.id)")
            let key = CameraFrameReference(cameraId: camera, frame: reference)
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
