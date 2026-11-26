import Foundation

enum VisibilityTarget: String, Codable {
    case groundBall, highBall, ankle
}

private struct VisibilityAnnotations: Decodable {
    struct Sample: Decodable {
        struct View: Decodable {
            let frame: FrameReference
            let visible: Bool
        }

        let id: String
        let region: String
        let target: VisibilityTarget
        let views: [String: View]
    }

    let sampleSource: ExposureSampleSource
    let cameras: [String]
    let maxSkewUs: Int64
    let samples: [Sample]

    func validatedReferences() throws -> Set<CameraFrameReference> {
        try validateAuditCameras(cameras)
        guard maxSkewUs >= 0 else {
            throw RecordingError("双视角曝光容差 maxSkewUs 必须为非负微秒数")
        }
        var sampleIDs: Set<String> = []
        var references: Set<CameraFrameReference> = []
        for sample in samples {
            guard !sample.id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  sampleIDs.insert(sample.id).inserted else {
                throw RecordingError("可见性样本 ID 为空或重复：\(sample.id)")
            }
            guard !sample.region.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw RecordingError("样本 \(sample.id)：场区为空")
            }
            for (camera, view) in sample.views {
                guard cameras.contains(camera) else {
                    throw RecordingError("样本 \(sample.id)：未知机位 \(camera)")
                }
                try view.frame.validate(context: "样本 \(sample.id)，机位 \(camera)")
                references.insert(CameraFrameReference(cameraId: camera, frame: view.frame))
            }
        }
        return references
    }
}

struct VisibilityAuditReport: Encodable {
    struct View: Encodable {
        let frame: ReplayFrame
        let visible: Bool
    }

    struct Sample: Encodable {
        let id: String
        let region: String
        let target: VisibilityTarget
        let views: [View]
        let missingCameras: [String]
        let closestVisibleSkewUs: Int64?
        let dualView: Bool
    }

    struct Group: Encodable {
        let region: String
        let target: VisibilityTarget
        let sampleCount: Int
        let dualViewCount: Int
        let dualViewRate: Double
    }

    let sampleSource: ExposureSampleSource
    let cameras: [String]
    let maxSkewUs: Int64
    let sampleCount: Int
    let dualViewCount: Int
    let groups: [Group]
    let samples: [Sample]
}

private struct VisibilityGroupKey: Hashable {
    let region: String
    let target: VisibilityTarget
}

func auditVisibility(frames: URL, annotations: URL) async throws -> VisibilityAuditReport {
    let marks: VisibilityAnnotations
    do {
        marks = try JSONDecoder().decode(VisibilityAnnotations.self, from: Data(contentsOf: annotations))
    } catch {
        throw RecordingError("无法读取可见性标注 \(annotations.lastPathComponent)：\(error)")
    }
    let references = try marks.validatedReferences()
    let lookup = try await AuditFrameLookup(frames: frames, cameras: marks.cameras, references: references)
    let samples = try marks.samples.map { sample in
        let views = try marks.cameras.compactMap { camera -> VisibilityAuditReport.View? in
            guard let view = sample.views[camera] else { return nil }
            let frame = try lookup.resolve(camera: camera, reference: view.frame, context: "样本 \(sample.id)")
            return VisibilityAuditReport.View(frame: frame, visible: view.visible)
        }
        // 至少两个机位同时看清即可。按曝光排序后，最接近的一对必相邻。
        let exposures = views.filter(\.visible).map(\.frame.utc).sorted()
        let closestSkew = zip(exposures, exposures.dropFirst()).map { $1 - $0 }.min()
        return VisibilityAuditReport.Sample(
            id: sample.id, region: sample.region, target: sample.target, views: views,
            missingCameras: marks.cameras.filter { sample.views[$0] == nil },
            closestVisibleSkewUs: closestSkew, dualView: closestSkew.map { $0 <= marks.maxSkewUs } ?? false
        )
    }
    let grouped = Dictionary(grouping: samples) { VisibilityGroupKey(region: $0.region, target: $0.target) }
    let groups = grouped.map { key, samples in
        let visibleCount = samples.filter(\.dualView).count
        return VisibilityAuditReport.Group(
            region: key.region, target: key.target, sampleCount: samples.count,
            dualViewCount: visibleCount, dualViewRate: Double(visibleCount) / Double(samples.count)
        )
    }.sorted { ($0.region, $0.target.rawValue) < ($1.region, $1.target.rawValue) }
    return VisibilityAuditReport(
        sampleSource: marks.sampleSource, cameras: marks.cameras, maxSkewUs: marks.maxSkewUs,
        sampleCount: samples.count, dualViewCount: groups.reduce(0) { $0 + $1.dualViewCount },
        groups: groups, samples: samples
    )
}
