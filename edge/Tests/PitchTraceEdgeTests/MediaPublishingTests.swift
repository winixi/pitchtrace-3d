import CryptoKit
import Foundation
import os
import Testing
@testable import PitchTraceEdge

private func digest(_ bytes: Data) -> String {
    SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
}

private struct MediaRequest: Equatable {
    let path: String
    let method: String?
    let digest: String?
    let body: Data
}

private final class MediaProtocol: URLProtocol {
    struct Log {
        var requests: [MediaRequest] = []
        var indexStatus = 202
    }

    static let log = OSAllocatedUnfairLock(initialState: Log())

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let url = request.url!
        var body = request.httpBody ?? Data()
        if let stream = request.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 4096)
            while true {
                let size = stream.read(&buffer, maxLength: buffer.count)
                guard size > 0 else { break }
                body.append(contentsOf: buffer.prefix(size))
            }
        }
        let upload = MediaRequest(
            path: url.path, method: request.httpMethod,
            digest: request.value(forHTTPHeaderField: "X-Content-SHA256"), body: body
        )
        let status = Self.log.withLock { state in
            state.requests.append(upload)
            return url.lastPathComponent == "index" ? state.indexStatus : 202
        }
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@Suite(.serialized)
struct MediaPublishingTests {
    private let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    private var camera: URL { directory.appendingPathComponent("A") }
    private let basePath = "/v2/matches/trial-1/cameras/A/epochs/replay-1/"

    private func makeCamera() throws {
        let segments = camera.appendingPathComponent("segments")
        try FileManager.default.createDirectory(at: segments, withIntermediateDirectories: true)
        let initialization = Data("init".utf8)
        try initialization.write(to: camera.appendingPathComponent("init.mp4"))
        for number in [10, 2] {
            let clip = Data("segment-\(number)".utf8)
            let index = MediaIndex(
                initSha256: digest(initialization), segmentSha256: digest(clip), segmentBytes: clip.count,
                timeBase: MediaTimeBase(numerator: 1, denominator: 1000),
                frames: [MediaFrame(pts: Int64(number * 40), durationPts: 40, captureUtcUs: String(number * 40000))]
            )
            try clip.write(to: segments.appendingPathComponent("\(number).m4s"))
            try JSONEncoder().encode(index).write(to: segments.appendingPathComponent("\(number).json"))
        }
    }

    private func publish(indexStatus: Int = 202) async throws -> Int {
        MediaProtocol.log.withLock { $0 = MediaProtocol.Log(indexStatus: indexStatus) }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MediaProtocol.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        return try await publishCameraMedia(
            from: camera, gateway: URL(string: "http://127.0.0.1:8766")!,
            matchID: "trial-1", mediaEpoch: "replay-1", session: session
        )
    }

    @Test func sendsOriginalPartsInSegmentOrderAndRepeatsIdentically() async throws {
        defer { try? FileManager.default.removeItem(at: directory) }
        try makeCamera()
        let count = try await publish()
        #expect(count == 2)
        let requests = MediaProtocol.log.withLock { $0.requests }
        let files = ["init.mp4", "segments/2.json", "segments/2.m4s", "segments/10.json", "segments/10.m4s"]
        let expected = try files.map { file in
            let bytes = try Data(contentsOf: camera.appendingPathComponent(file))
            let path = file.replacingOccurrences(of: ".json", with: "/index")
            return MediaRequest(
                path: basePath + path, method: "PUT",
                digest: file.hasSuffix(".json") ? nil : digest(bytes), body: bytes
            )
        }
        #expect(requests == expected)
        let repeated = try await publish()
        #expect(repeated == count)
        #expect(MediaProtocol.log.withLock { $0.requests } == requests)
    }

    @Test(arguments: [409, 200])
    func stopsAtFirstUnacknowledgedPart(status: Int) async throws {
        defer { try? FileManager.default.removeItem(at: directory) }
        try makeCamera()
        await #expect(throws: (any Error).self) { try await publish(indexStatus: status) }
        let paths = MediaProtocol.log.withLock { $0.requests.map(\.path) }
        #expect(paths == [basePath + "init.mp4", basePath + "segments/2/index"])
    }

    @Test func rejectsChangedBytesBeforeMakingRequests() async throws {
        defer { try? FileManager.default.removeItem(at: directory) }
        try makeCamera()
        try Data("SEGMENT-2".utf8).write(to: camera.appendingPathComponent("segments/2.m4s"))
        await #expect(throws: (any Error).self) { try await publish() }
        #expect(MediaProtocol.log.withLock { $0.requests.isEmpty })
    }
}
