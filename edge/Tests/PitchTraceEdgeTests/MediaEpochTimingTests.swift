import Foundation
import Testing
@testable import PitchTraceEdge

struct MediaEpochTimingTests {
    private func align(
        _ timing: inout CameraTiming, segment: Int64, pts: Int64, utc: Int64,
        frameNumber: Int64, scale: Int32, gaps: inout [String]
    ) throws -> ReplayFrame {
        let frames = try timing.align(
            segment: segment, timeBase: MediaTimeBase(numerator: 1, denominator: scale),
            videoFrames: [VideoFrame(pts: pts, duration: 1, packetDuration: nil, keyFrame: 1)],
            exposures: [ExposureMapping(pts: pts, captureUtcUs: String(utc), frameNumber: frameNumber)],
            gaps: &gaps
        )
        return try #require(frames.first)
    }

    @Test func restartsMediaClockAndPreservesExposureOrder() throws {
        var timing = CameraTiming(cameraId: "A", mediaEpoch: "z-before")
        var gaps: [String] = []
        _ = try align(&timing, segment: 4, pts: 500, utc: 8000, frameNumber: 8, scale: 1000, gaps: &gaps)
        timing.mediaEpoch = "a-after"
        let first = try align(&timing, segment: 0, pts: 0, utc: 9000, frameNumber: 0, scale: 2500, gaps: &gaps)
        #expect(gaps.isEmpty)
        #expect(first.mediaEpoch == "a-after")
        let line = try JSONEncoder().encode(first)
        #expect(try JSONDecoder().decode(ReplayFrame.self, from: line).mediaEpoch == "a-after")

        _ = try align(&timing, segment: 2, pts: 2, utc: 9800, frameNumber: 2, scale: 2500, gaps: &gaps)
        #expect(gaps.count == 3)
        #expect(gaps.allSatisfy { $0.contains("机位 A，媒体周期 a-after，片段 2") })
    }

    @Test(arguments: [Int64(7999), Int64(8000)])
    func rejectsOverlappingExposureRanges(firstAfterReset: Int64) throws {
        var timing = CameraTiming(cameraId: "A", mediaEpoch: "before")
        var gaps: [String] = []
        _ = try align(&timing, segment: 4, pts: 50, utc: 8000, frameNumber: 4, scale: 1000, gaps: &gaps)
        timing.mediaEpoch = "after"
        do {
            _ = try align(&timing, segment: 0, pts: 0, utc: firstAfterReset, frameNumber: 0, scale: 1000, gaps: &gaps)
            Issue.record("应拒绝跨媒体周期重复或倒退的曝光 UTC")
        } catch {
            #expect(error.localizedDescription.contains("机位 A，媒体周期 after，片段 0，PTS 0"))
            #expect(error.localizedDescription.contains("before"))
        }
    }
}
