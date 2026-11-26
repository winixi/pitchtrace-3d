import Foundation
import Testing
@testable import PitchTraceEdge

struct VisibilityAuditTests {
    private func row(_ camera: String, pts: Int64 = 0, offsetUs: Int64 = 0, epoch: String? = nil) -> ReplayFrame {
        let utc = 1_795_494_600_000_000 + offsetUs
        return ReplayFrame(
            cameraId: camera, mediaEpoch: epoch, segment: 0,
            timeBase: MediaTimeBase(numerator: 1, denominator: 1_000), frameNumber: nil,
            frame: MediaFrame(pts: pts, durationPts: 40, captureUtcUs: String(utc)), utc: utc
        )
    }

    private func view(_ visible: Bool, pts: Int = 0, epoch: String? = nil) -> [String: Any] {
        var frame: [String: Any] = ["segment": 0, "pts": pts]
        if let epoch { frame["mediaEpoch"] = epoch }
        return ["frame": frame, "visible": visible]
    }

    private func sample(
        _ id: String, region: String = "近端", target: String = "highBall", views: [String: [String: Any]]
    ) -> [String: Any] {
        ["id": id, "region": region, "target": target, "views": views]
    }

    private func audit(
        _ samples: [[String: Any]], rows: [ReplayFrame], cameras: [String] = ["A", "B"], maxSkewUs: Int = 5_000
    ) async throws -> VisibilityAuditReport {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        let annotations = directory.appendingPathComponent("visibility.json")
        try JSONSerialization.data(withJSONObject: [
            "sampleSource": "synthetic", "cameras": cameras, "maxSkewUs": maxSkewUs, "samples": samples,
        ]).write(to: annotations)
        let frames = directory.appendingPathComponent("frames.ndjson")
        let encoder = JSONEncoder()
        var body = Data()
        for row in rows {
            body.append(try encoder.encode(row))
            body.append(0x0A)
        }
        try body.write(to: frames)
        return try await auditVisibility(frames: frames, annotations: annotations)
    }

    @Test func preservesPlannedSamplesAndGroupsByRegionAndTarget() async throws {
        let both = ["A": view(true), "B": view(true)]
        let report = try await audit([
            sample("boundary", views: both),
            sample("occluded", views: ["A": view(true, pts: 80), "B": view(false, pts: 80)]),
            sample("missing-b", views: ["A": view(true, pts: 120)]),
            sample("missing-both", views: [:]),
            sample("late", region: "远端", views: ["A": view(true, pts: 40), "B": view(true, pts: 40)]),
            sample("same-frames", target: "ankle", views: both),
        ], rows: [
            row("A"), row("B", offsetUs: 5_000),
            row("A", pts: 40, offsetUs: 40_000), row("B", pts: 40, offsetUs: 45_001),
            row("A", pts: 80, offsetUs: 80_000), row("B", pts: 80, offsetUs: 80_000),
            row("A", pts: 120, offsetUs: 120_000),
        ])

        #expect(report.sampleSource == .synthetic)
        #expect(report.sampleCount == 6)
        #expect(report.dualViewCount == 2)
        #expect(report.samples.map(\.dualView) == [true, false, false, false, false, true])
        #expect(report.samples.map(\.closestVisibleSkewUs) == [5_000, nil, nil, nil, 5_001, 5_000])
        #expect(report.samples[1].missingCameras.isEmpty)
        #expect(report.samples[1].views.map(\.visible) == [true, false])
        #expect(report.samples[2].missingCameras == ["B"])
        #expect(report.samples[3].missingCameras == ["A", "B"])
        #expect(report.samples[3].views.isEmpty)
        #expect(report.groups.count == 3)
        let nearBall = try #require(report.groups.first { $0.region == "近端" && $0.target == .highBall })
        #expect(nearBall.sampleCount == 4)
        #expect(nearBall.dualViewCount == 1)
        #expect(nearBall.dualViewRate == 0.25)
        let farBall = try #require(report.groups.first { $0.region == "远端" })
        #expect(farBall.dualViewRate == 0)
        let nearAnkle = try #require(report.groups.first { $0.target == .ankle })
        #expect(nearAnkle.dualViewRate == 1)
    }

    @Test func findsAnAlignedVisiblePairAmongFourCameras() async throws {
        let report = try await audit([
            sample("four", target: "groundBall", views: [
                "A": view(true), "B": view(true), "C": view(true), "D": view(true),
            ]),
            sample("outside", target: "ankle", views: ["B": view(true), "D": view(true, pts: 1)]),
        ], rows: [
            row("A"), row("B", offsetUs: 30_000), row("D", offsetUs: 31_000),
            row("D", pts: 1, offsetUs: 31_001), row("C", offsetUs: 60_000),
        ], cameras: ["A", "B", "C", "D"], maxSkewUs: 1_000)

        #expect(report.maxSkewUs == 1_000)
        #expect(report.samples.map(\.dualView) == [true, false])
        #expect(report.samples.map(\.closestVisibleSkewUs) == [1_000, 1_001])
        #expect(report.samples[1].missingCameras == ["A", "C"])
    }

    @Test func resolvesOriginalFramesAcrossClockReset() async throws {
        let rows = [
            row("A", epoch: "before"), row("B", offsetUs: 1), row("A", offsetUs: 40_000, epoch: "after"),
        ]
        let report = try await audit([
            sample("earlier", views: ["A": view(true, epoch: "before"), "B": view(true)]),
            sample("later", views: ["A": view(true, epoch: "after"), "B": view(true)]),
        ], rows: rows)
        #expect(report.samples.map(\.dualView) == [true, false])
        let frame = try #require(report.samples[1].views.first { $0.frame.cameraId == "A" }?.frame)
        #expect(frame.mediaEpoch == "after")
        #expect(frame.frame.captureUtcUs == "1795494600040000")

        await #expect(throws: RecordingError.self) {
            try await audit([sample("ambiguous", views: ["A": view(true)])], rows: rows)
        }
    }

    @Test func rejectsInvalidAnnotationsAndUnresolvedEvidence() async {
        let rows = [row("A"), row("B")]
        let planned = sample("one", views: [:])
        let invalid = [
            [planned, planned],
            [sample("blank-region", region: "  ", views: [:])],
            [sample("unknown-target", target: "ball", views: [:])],
            [sample("unknown-camera", views: ["C": view(true)])],
            [sample("missing-original", views: ["B": view(false, pts: 99)])],
        ]
        for samples in invalid {
            await #expect(throws: (any Error).self) { try await audit(samples, rows: rows) }
        }
        await #expect(throws: RecordingError.self) {
            try await audit([planned], rows: rows, maxSkewUs: -1)
        }
    }
}
