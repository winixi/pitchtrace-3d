import Testing
@testable import PitchTraceEdge

struct ReplayMergeTests {
    private func frame(_ camera: String, _ utc: Int64, segment: Int64 = 0) -> ReplayFrame {
        ReplayFrame(
            cameraId: camera, segment: segment,
            timeBase: MediaTimeBase(numerator: 1, denominator: 1_000_000), frameNumber: nil,
            frame: MediaFrame(pts: utc, durationPts: 1, captureUtcUs: String(utc)), utc: utc
        )
    }

    @Test func mergesAcrossSegmentBoundariesWithOnePendingFramePerCamera() throws {
        var streams = [
            [frame("D", 10), frame("D", 80)].makeIterator(),
            [frame("B", 10)].makeIterator(),
            [frame("A", 10), frame("A", 15, segment: 1), frame("A", 45, segment: 1)].makeIterator(),
            [frame("C", 5), frame("C", 10, segment: 1), frame("C", 55, segment: 3)].makeIterator(),
        ]
        var pending: Set<String> = []
        var output: [ReplayFrame] = []
        let count = try mergeReplayFrames(cameraCount: streams.count) { index in
            guard let next = streams[index].next() else { return nil }
            let inserted = pending.insert(next.cameraId).inserted
            #expect(inserted, "同一机位的前帧尚未写出")
            return next
        } write: { frame in
            pending.remove(frame.cameraId)
            output.append(frame)
        }

        #expect(count == 9)
        #expect(pending.isEmpty)
        #expect(output.map(\.utc) == [5, 10, 10, 10, 10, 15, 45, 55, 80])
        #expect(output.map(\.cameraId) == ["C", "A", "B", "C", "D", "A", "A", "C", "D"])
        #expect(output.map(\.segment) == [0, 0, 0, 1, 0, 1, 1, 3, 0])
    }

    @Test func propagatesSourceAndDestinationErrors() {
        enum Failure: Error { case source, destination }
        let sources = [frame("A", 2), frame("B", 1)]
        var written: [String] = []
        #expect(throws: Failure.source) {
            var streams = sources.map { [$0].makeIterator() }
            _ = try mergeReplayFrames(cameraCount: streams.count) { index in
                guard let frame = streams[index].next() else { throw Failure.source }
                return frame
            } write: { frame in
                written.append(frame.cameraId)
            }
        }
        #expect(written == ["B"])

        #expect(throws: Failure.destination) {
            var streams = sources.map { [$0].makeIterator() }
            _ = try mergeReplayFrames(cameraCount: streams.count) { index in
                streams[index].next()
            } write: { _ in
                throw Failure.destination
            }
        }
    }
}
