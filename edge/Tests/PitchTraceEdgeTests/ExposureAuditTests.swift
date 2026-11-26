import Foundation
import Testing
@testable import PitchTraceEdge

struct ExposureAuditTests {
    private func replayFrame(_ camera: String, _ index: Int, offset: Int64 = 0) -> ReplayFrame {
        let utc = 1_795_494_600_000_000 + Int64(index) * 100_000 + offset
        let pts = Int64(camera == "A" ? index : 2 * index)
        return ReplayFrame(
            cameraId: camera, segment: camera == "A" ? 0 : 1,
            timeBase: MediaTimeBase(numerator: 1, denominator: 1_000), frameNumber: Int64(index),
            frame: MediaFrame(pts: pts, durationPts: 40, captureUtcUs: String(utc)), utc: utc
        )
    }

    private func audit(
        _ events: [[String: Any]], rows: [ReplayFrame], cameras: [String] = ["A", "B"]
    ) async throws -> ExposureAuditReport {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        let marks = directory.appendingPathComponent("flashes.json")
        let frames = directory.appendingPathComponent("frames.ndjson")
        try JSONSerialization.data(withJSONObject: [
            "sampleSource": "synthetic", "cameras": cameras, "events": events,
        ]).write(to: marks)
        let encoder = JSONEncoder()
        var body = Data()
        for row in rows {
            body.append(try encoder.encode(row))
            body.append(0x0A)
        }
        try body.write(to: frames)
        return try await auditExposure(frames: frames, annotations: marks)
    }

    @Test func reportsNearestRankP95AndMaximumSeparately() async throws {
        var rows: [ReplayFrame] = []
        var events: [[String: Any]] = []
        for index in 0..<20 {
            let offset: Int64 = switch index {
            case 0: -5_000
            case 19: 50_000
            default: Int64(index) * 250
            }
            rows.append(replayFrame("A", index))
            rows.append(replayFrame("B", index, offset: offset))
            events.append([
                "id": "flash-\(index)",
                "frames": [
                    "A": ["segment": 0, "pts": index],
                    "B": ["segment": 1, "pts": 2 * index],
                ],
            ])
        }
        rows.append(replayFrame("A", 20))
        events.append(["id": "missing-B", "frames": ["A": ["segment": 0, "pts": 20]]])
        events.append(["id": "missing-both", "frames": [:]])

        let report = try await audit(events, rows: rows)
        #expect(report.sampleSource == .synthetic)
        #expect(report.eventCount == 22)
        #expect(report.pairedCount == 20)
        #expect(report.unpairedCount == 2)
        #expect(report.p95AbsUs == 5_000)
        #expect(report.maxAbsUs == 50_000)
        #expect(report.p95WithinThreshold == true)
        #expect(report.events[0].signedSkewUs == -5_000)
        #expect(report.events[0].spanUs == 5_000)
        #expect(report.events[0].frames.map(\.segment) == [0, 1])
        #expect(report.events[20].missingCameras == ["B"])
        #expect(report.events[20].signedSkewUs == nil)
        #expect(report.events[21].missingCameras == ["A", "B"])
    }

    @Test func keepsUnpairedEventsWithoutInventingStatistics() async throws {
        let report = try await audit(
            [["id": "only-B", "frames": ["B": ["segment": 1, "pts": 0]]]],
            rows: [replayFrame("A", 0), replayFrame("B", 0)]
        )
        #expect(report.pairedCount == 0)
        #expect(report.unpairedCount == 1)
        #expect(report.p95AbsUs == nil)
        #expect(report.maxAbsUs == nil)
        #expect(report.p95WithinThreshold == nil)
        #expect(report.events[0].missingCameras == ["A"])
    }

    @Test(arguments: [["C", "A", "B"], ["C", "A", "B", "D"]])
    func usesFullExposureRangeForCompleteGroupsOnly(cameras: [String]) async throws {
        var rows = [
            replayFrame("A", 0, offset: -3_000), replayFrame("B", 0, offset: 4_000),
            replayFrame("C", 0),
        ]
        if cameras.contains("D") {
            rows.append(replayFrame("D", 0, offset: 1_000))
        }
        let complete = Dictionary(uniqueKeysWithValues: rows.map {
            ($0.cameraId, ["segment": $0.segment, "pts": $0.frame.pts])
        })
        rows.append(contentsOf: [replayFrame("A", 1), replayFrame("B", 1, offset: 70_000)])
        let report = try await audit([
            ["id": "complete", "frames": complete],
            ["id": "partial", "frames": ["A": ["segment": 0, "pts": 1], "B": ["segment": 1, "pts": 2]]],
            ["id": "none", "frames": [:]],
        ], rows: rows, cameras: cameras)

        #expect(report.pairedCount == 1)
        #expect(report.unpairedCount == 2)
        #expect(report.p95AbsUs == 7_000)
        #expect(report.maxAbsUs == 7_000)
        #expect(report.p95WithinThreshold == false)
        #expect(report.events[0].frames.map(\.cameraId) == cameras)
        #expect(report.events[0].spanUs == 7_000)
        #expect(report.events.allSatisfy { $0.signedSkewUs == nil })
        #expect(report.events[1].missingCameras == cameras.filter { !["A", "B"].contains($0) })
        #expect(report.events[1].spanUs == nil)
        #expect(report.events[2].missingCameras == cameras)
        #expect(report.events[2].spanUs == nil)
    }

    @Test(arguments: [["A"], ["A", "B", "B"], ["A", "B", "C", "D", "E"]])
    func rejectsInvalidCameraLists(cameras: [String]) async {
        do {
            _ = try await audit([], rows: [], cameras: cameras)
            Issue.record("应拒绝机位列表 \(cameras)")
        } catch {
            #expect(error.localizedDescription.contains("2 至 4 个不同的 v2 机位 ID"))
        }
    }

    @Test func rejectsUnresolvableAndReusedFrameReferences() async throws {
        let rows = [replayFrame("A", 0), replayFrame("B", 0)]
        let first = ["id": "first", "frames": ["A": ["segment": 0, "pts": 0]]] as [String: Any]
        let cases: [([[String: Any]], [ReplayFrame], String)] = [
            ([["id": "unresolved", "frames": ["A": ["segment": 0, "pts": 1]]]],
             rows, "闪光 unresolved，机位 A，片段 0，PTS 1：录像帧不存在"),
            ([first, ["id": "reused", "frames": ["A": ["segment": 0, "pts": 0]]]],
             rows, "闪光 reused，机位 A：同一录像帧已用于其他闪光"),
            ([first], rows + [rows[0]], "frames.ndjson 第 3 行：标注引用了重复的录像帧键"),
        ]
        for (events, frames, error) in cases {
            do {
                _ = try await audit(events, rows: frames)
                Issue.record("应拒绝无效帧引用：\(error)")
            } catch let actual {
                #expect(actual.localizedDescription == error)
            }
        }
    }
}
