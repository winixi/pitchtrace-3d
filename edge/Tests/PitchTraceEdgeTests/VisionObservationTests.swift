import CoreML
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

    private func packetStream(_ bytes: [UInt8]) throws -> FileHandle {
        let pipe = Pipe()
        try pipe.fileHandleForWriting.write(contentsOf: Data(bytes))
        try pipe.fileHandleForWriting.close()
        return pipe.fileHandleForReading
    }

    @Test func packetReaderKeepsBoundariesAndRejectsTruncation() throws {
        let stream = try packetStream([0, 0, 0, 2, 42, 43, 0, 0, 0, 1, 44])
        #expect(try readVisionPacket(from: stream)?.image == Data([42, 43]))
        #expect(try readVisionPacket(from: stream)?.image == Data([44]))
        #expect(try readVisionPacket(from: stream) == nil)
        for bytes: [UInt8] in [[0, 0], [0, 0, 0, 3, 1], [0, 0, 0, 0], [255, 255, 255, 255]] {
            #expect(throws: RecordingError.self) { try readVisionPacket(from: packetStream(bytes)) }
        }
    }

    @Test func roiPacketsReadFourBigEndianValuesAndKeepFrameBoundaries() throws {
        let header: [UInt8] = [0, 0, 1, 5, 0, 0, 0, 3, 0, 0, 2, 0, 0, 0, 0, 20]
        let empty = [UInt8](repeating: 0, count: 16)
        let stream = try packetStream(header + [0, 0, 0, 2, 42, 43] + empty + [0, 0, 0, 1, 44])
        let first = try #require(try readVisionPacket(from: stream, ballSearch: true))
        #expect(first.ballSearch == CGRect(x: 261, y: 3, width: 512, height: 20))
        #expect(first.image == Data([42, 43]))
        let next = try #require(try readVisionPacket(from: stream, ballSearch: true))
        #expect(next.ballSearch == nil)
        #expect(next.image == Data([44]))
        #expect(try readVisionPacket(from: stream, ballSearch: true) == nil)
        var zeroWidth = header
        zeroWidth[10] = 0
        for bytes in [Array(header.prefix(8)), header, zeroWidth + [0, 0, 0, 1, 44]] {
            #expect(throws: RecordingError.self) {
                try readVisionPacket(from: packetStream(bytes), ballSearch: true)
            }
        }
    }

    @Test func workerOptionsRequireAModelForJointDetectionAndBallSearch() throws {
        let system = try VisionWorkerOptions(arguments: [])
        #expect(system.modelURL == nil && !system.personAndBall && !system.ballSearch)
        let model = "试点.mlpackage"
        let legacy = try VisionWorkerOptions(arguments: [model])
        #expect(legacy.modelURL?.lastPathComponent == model && !legacy.personAndBall && !legacy.ballSearch)
        let ballOnly = try VisionWorkerOptions(arguments: [model, "--ball-search"])
        #expect(!ballOnly.personAndBall && ballOnly.ballSearch)
        let joint = try VisionWorkerOptions(arguments: [model, "--person-and-ball", "--ball-search"])
        #expect(joint.personAndBall && joint.ballSearch)
        for args in [["--person-and-ball"], ["--ball-search"], [model, "--bad"],
                     [model, "--ball-search", "--person-and-ball"], [model, model]] {
            #expect(throws: RecordingError.self) { try VisionWorkerOptions(arguments: args) }
        }
    }

    @Test func jointDetectionFiltersClassesClipsBoxesAndRanksBalls() throws {
        let frame = CGRect(x: 0, y: 0, width: 160, height: 80)
        let bounds = CGRect(x: -0.125, y: 0.25, width: 0.5, height: 0.625)
        var selected = SelectedDetections()
        func add(_ label: String, _ score: Float, _ box: CGRect = bounds) {
            selected.include(label: label, confidence: score, bounds: box, in: frame,
                             includePeople: true, ballSource: "coreML")
        }
        add("car", 1)
        add("person", 0.34)
        add("ball", 0.349)
        add("football", .nan)
        add("sports ball", 1.1)
        add("person", 1, CGRect(x: 2, y: 0, width: 1, height: 1))
        #expect(selected.people.isEmpty && selected.ball == nil)
        add("PERSON", 0.35)
        #expect(selected.people.count == 1)
        #expect(selected.people.first?.box == CGRect(x: 0, y: 10, width: 60, height: 50))
        #expect(selected.people.first?.confidence == 0.35)
        add("soccer ball", 0.45)
        add("SPORTS BALL", 0.8, CGRect(x: 0.25, y: 0.125, width: 0.25, height: 0.25))
        add("ball", 0.7)
        let ball = try #require(selected.ball)
        #expect(ball.pixel == [60, 60] && ball.box == [40, 50, 40, 20])
        #expect(ball.confidence == 0.8 && ball.source == "coreML")
    }

    @Test func textMustStayInsideOneTorso() {
        let tiles = [CGRect(x: 0.1, y: 0.1, width: 0.25, height: 0.25),
                     CGRect(x: 0.6, y: 0.1, width: 0.3, height: 0.35),
                     CGRect(x: 0.1, y: 0.6, width: 0.3, height: 0.3)]
        #expect(shirtOwner(of: CGRect(x: 0.7, y: 0.12, width: 0.1, height: 0.1), in: tiles) == 1)
        #expect(shirtOwner(of: CGRect(x: 0.12, y: 0.7, width: 0.1, height: 0.1), in: tiles) == 2)
        #expect(shirtOwner(of: CGRect(x: 0.3, y: 0.15, width: 0.38, height: 0.1), in: tiles) == nil)
        #expect(shirtOwner(of: CGRect(x: 0.5, y: 0.5, width: 0.01, height: 0.01), in: tiles) == nil)
        #expect(shirtOwner(of: .zero, in: tiles) == nil)
    }

    @Test func individualOCRCanBeSelectedWithOrWithoutAModel() throws {
        #expect(try VisionWorkerOptions(arguments: ["--individual-ocr"]).individualOCR)
        #expect(try VisionWorkerOptions(arguments: ["m", "--person-and-ball", "--ball-search", "--individual-ocr"]).individualOCR)
    }

    @Test func computeUnitsDefaultToAllAndRequireAModel() throws {
        #expect(try VisionWorkerOptions(arguments: ["m"]).computeUnits == .all)
        for (name, units) in [("cpuAndNeuralEngine", MLComputeUnits.cpuAndNeuralEngine),
                              ("cpuAndGPU", .cpuAndGPU), ("cpuOnly", .cpuOnly), ("all", .all)] {
            let options = try VisionWorkerOptions(arguments: ["m", "--person-and-ball", "--ocr-hints",
                                                              "--compute-units", name])
            #expect(options.computeUnits == units && options.ocrHints && options.personAndBall)
        }
        for args in [["--compute-units", "cpuOnly"], ["m", "--compute-units"], ["m", "--compute-units", "gpu"],
                     ["m", "--compute-units", "cpuOnly", "--ocr-hints"]] {
            #expect(throws: RecordingError.self) { try VisionWorkerOptions(arguments: args) }
        }
        #expect(try VisionWorkerOptions(arguments: ["--ocr-hints"]).ocrHints)
    }

    @Test func ocrHintPacketsFollowBallSearchAndKeepFrameBoundaries() throws {
        func be(_ values: [Int]) -> [UInt8] {
            values.flatMap { v in (0..<4).map { UInt8((v >> (8 * (3 - $0))) & 0xff) } }
        }
        let ball = be([1, 2, 3, 4])
        let stream = try packetStream(ball + be([2, 10, 20, 30, 40, 0, 0, 5, 6]) + be([1]) + [42]
                                      + be([0, 0, 0, 0]) + be([0]) + be([1]) + [43])
        let first = try #require(try readVisionPacket(from: stream, ballSearch: true, ocrHints: true))
        #expect(first.ballSearch == CGRect(x: 1, y: 2, width: 3, height: 4))
        #expect(first.ocrSkip == [CGRect(x: 10, y: 20, width: 30, height: 40), CGRect(x: 0, y: 0, width: 5, height: 6)])
        #expect(first.image == Data([42]))
        let second = try #require(try readVisionPacket(from: stream, ballSearch: true, ocrHints: true))
        #expect(second.ballSearch == nil && second.ocrSkip.isEmpty && second.image == Data([43]))
        #expect(try readVisionPacket(from: stream, ballSearch: true, ocrHints: true) == nil)
        for bytes in [be([1, 0, 0, 0]), be([65]), be([1, 0, 0, 0, 1]) + be([1]) + [1], be([0])] {
            #expect(throws: RecordingError.self) {
                try readVisionPacket(from: packetStream(bytes), ocrHints: true)
            }
        }
    }

    @Test func ocrSkipNeedsHalfIntersectionOverUnion() {
        let box = CGRect(x: 100, y: 100, width: 40, height: 80)
        #expect(skipsOCR(box, hints: [CGRect(x: 104, y: 100, width: 40, height: 80)]))
        #expect(!skipsOCR(box, hints: [CGRect(x: 125, y: 100, width: 40, height: 80)]))
        #expect(!skipsOCR(box, hints: [CGRect(x: 140, y: 100, width: 40, height: 80)]))
        #expect(!skipsOCR(box, hints: []))
    }

    @Test func croppedBallSearchOffsetsPixelsAndIgnoresPeople() throws {
        let crop = CGRect(x: 40, y: 20, width: 64, height: 128)
        let bounds = CGRect(x: 0.25, y: 0.75, width: 0.125, height: 0.25)
        var selected = SelectedDetections()
        for label in ["person", "FOOTBALL"] {
            selected.include(label: label, confidence: 0.5, bounds: bounds, in: crop,
                             includePeople: false, ballSource: "coreML-ROI")
        }
        let ball = try #require(selected.ball)
        #expect(selected.people.isEmpty)
        #expect(ball.pixel == [60, 36] && ball.box == [56, 20, 8, 32])
        #expect(ball.source == "coreML-ROI")
    }
}
