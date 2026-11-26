import Foundation
import Testing
@testable import PitchTraceEdge

struct VisionObservationTests {
    @Test func jerseyEvidenceMustIdentifyOneNumber() {
        #expect(jerseyNumber([" 0 ", "0"]) == 0)
        #expect(jerseyNumber(["19", "广告"]) == 19)
        #expect(jerseyNumber(["1", "9"]) == nil)
        #expect(jerseyNumber(["", "100", "+3", "４"]) == nil)
    }

    @Test func cropCoordinatesRetainPixelOrientationAndOffset() {
        let crop = CGRect(x: 30, y: 80, width: 101, height: 201)
        #expect(originalPixel(CGPoint(x: 0.5, y: 1), in: crop) == [80, 80])
        #expect(originalPixel(CGPoint(x: 1, y: 0), in: crop) == [130, 280])
        #expect(originalPixel(CGPoint(x: -0.01, y: 0), in: crop) == nil)
    }

    @Test func packetReaderKeepsBoundariesAndRejectsTruncation() throws {
        func packetStream(_ bytes: [UInt8]) throws -> FileHandle {
            let pipe = Pipe()
            try pipe.fileHandleForWriting.write(contentsOf: Data(bytes))
            try pipe.fileHandleForWriting.close()
            return pipe.fileHandleForReading
        }
        let stream = try packetStream([0, 0, 0, 2, 42, 43, 0, 0, 0, 1, 44])
        #expect(try readVisionPacket(from: stream) == Data([42, 43]))
        #expect(try readVisionPacket(from: stream) == Data([44]))
        #expect(try readVisionPacket(from: stream) == nil)
        for bytes: [UInt8] in [[0, 0], [0, 0, 0, 3, 1], [0, 0, 0, 0], [255, 255, 255, 255]] {
            #expect(throws: RecordingError.self) { try readVisionPacket(from: packetStream(bytes)) }
        }
    }
}
