import Foundation
import Testing
@testable import PitchTraceEdge

struct RecordingTimingTests {
    private let ms = MediaTimeBase(numerator: 1, denominator: 1_000)

    @Test func presentationOrderPreservesExposureAndFrameNumber() throws {
        var timing = CameraTiming(cameraId: "A")
        var gaps: [String] = []
        let frames = try timing.align(
            segment: 0, timeBase: ms,
            videoFrames: [
                VideoFrame(pts: 40, duration: nil, packetDuration: 40, keyFrame: 0),
                VideoFrame(pts: 0, duration: 40, packetDuration: nil, keyFrame: 1),
            ],
            exposures: [
                ExposureMapping(pts: 40, captureUtcUs: "1795494600040000", frameNumber: 15),
                ExposureMapping(pts: 0, captureUtcUs: "1795494600000000", frameNumber: nil),
            ],
            gaps: &gaps
        )

        #expect(frames.map(\.frame.pts) == [0, 40])
        #expect(frames.map(\.frame.durationPts) == [40, 40])
        #expect(frames.map(\.frameNumber) == [nil, 15])
        #expect(frames.map(\.utc) == [1_795_494_600_000_000, 1_795_494_600_040_000])
        let line = try JSONEncoder().encode(frames[1])
        let record = try #require(JSONSerialization.jsonObject(with: line) as? [String: Any])
        #expect(record["cameraId"] as? String == "A")
        #expect(record["frameNumber"] as? Int == 15)
        #expect(record["captureUtcUs"] as? String == "1795494600040000")
        #expect(gaps.isEmpty)
    }

    @Test func gapsAcrossSegmentsAreReportedWithoutFillingFrames() throws {
        var timing = CameraTiming(cameraId: "B")
        var gaps: [String] = []
        _ = try timing.align(
            segment: 0, timeBase: ms,
            videoFrames: [VideoFrame(pts: 0, duration: 40, packetDuration: nil, keyFrame: 1)],
            exposures: [ExposureMapping(pts: 0, captureUtcUs: "100000", frameNumber: 10)],
            gaps: &gaps
        )
        let frames = try timing.align(
            segment: 2, timeBase: ms,
            videoFrames: [VideoFrame(pts: 160, duration: 40, packetDuration: nil, keyFrame: 1)],
            exposures: [ExposureMapping(pts: 160, captureUtcUs: "260000", frameNumber: 14)],
            gaps: &gaps
        )
        #expect(frames.map(\.frame.pts) == [160])
        #expect(gaps == [
            "机位 B，片段 2：缺少片段 1…1",
            "机位 B，片段 2，PTS 160：展示时间缺口 [40, 160)",
            "机位 B，片段 2，PTS 160：缺少原始帧 11…13",
        ])
    }

    @Test func invalidExposureMappingIdentifiesCameraSegmentAndFrame() {
        let video = [
            VideoFrame(pts: 0, duration: 40, packetDuration: nil, keyFrame: 1),
            VideoFrame(pts: 40, duration: 40, packetDuration: nil, keyFrame: 0),
        ]
        let start = ExposureMapping(pts: 0, captureUtcUs: "100000", frameNumber: nil)
        for (exposures, message) in [
            ([start], "PTS 40：缺少曝光映射"),
            ([start, ExposureMapping(pts: 40, captureUtcUs: nil, frameNumber: nil)],
             "PTS 40：曝光 UTC 缺失或无效"),
            ([start, ExposureMapping(pts: 40, captureUtcUs: "99999", frameNumber: nil)],
             "PTS 40：曝光 UTC 未严格递增"),
            ([start, start], "PTS 0：重复的曝光映射"),
            ([start, ExposureMapping(pts: 40, captureUtcUs: "140000", frameNumber: nil),
              ExposureMapping(pts: 80, captureUtcUs: "180000", frameNumber: nil)],
             "PTS 80：曝光映射没有对应的视频帧"),
        ] {
            var timing = CameraTiming(cameraId: "A")
            var gaps: [String] = []
            do {
                _ = try timing.align(
                    segment: 3, timeBase: ms, videoFrames: video, exposures: exposures, gaps: &gaps
                )
                Issue.record("预期拒绝曝光映射：\(message)")
            } catch {
                #expect(error.localizedDescription == "机位 A，片段 3，\(message)")
            }
        }
    }

    @Test func overlappingPresentationIntervalsAndUnsafeEndAreRejected() {
        let cases: [([VideoFrame], [ExposureMapping], String)] = [
            ([
                VideoFrame(pts: 0, duration: 40, packetDuration: nil, keyFrame: 1),
                VideoFrame(pts: 20, duration: 40, packetDuration: nil, keyFrame: 0),
            ], [
                ExposureMapping(pts: 0, captureUtcUs: "100000", frameNumber: nil),
                ExposureMapping(pts: 20, captureUtcUs: "120000", frameNumber: nil),
            ], "展示区间重叠"),
            ([
                VideoFrame(pts: maxSafeMediaInteger, duration: 1, packetDuration: nil, keyFrame: 1),
            ], [], "帧时长或 PTS 超出"),
        ]
        for (video, exposures, message) in cases {
            var timing = CameraTiming(cameraId: "A")
            var gaps: [String] = []
            do {
                _ = try timing.align(
                    segment: 0, timeBase: ms, videoFrames: video, exposures: exposures, gaps: &gaps
                )
                Issue.record("预期拒绝无效时序：\(message)")
            } catch {
                #expect(error.localizedDescription.contains(message))
            }
        }
    }
}
