import Foundation

private func putMedia(_ data: Data, to url: URL, digest: String? = nil, session: URLSession) async throws {
    var request = URLRequest(url: url)
    request.httpMethod = "PUT"
    request.setValue(digest == nil ? "application/json" : "video/mp4", forHTTPHeaderField: "Content-Type")
    request.setValue(digest, forHTTPHeaderField: "X-Content-SHA256")
    let body: Data
    let response: URLResponse
    do {
        (body, response) = try await session.upload(for: request, from: data)
    } catch {
        throw RecordingError("PUT \(url.path)：\(error.localizedDescription)")
    }
    guard let http = response as? HTTPURLResponse else {
        throw RecordingError("PUT \(url.path)：未收到 HTTP 回执")
    }
    guard http.statusCode == 202 else {
        let detail = String(decoding: body.prefix(512), as: UTF8.self)
        throw RecordingError("PUT \(url.path)：HTTP \(http.statusCode) \(detail)")
    }
}

func publishCameraMedia(
    from directory: URL, gateway: URL, matchID: String, mediaEpoch: String, session: URLSession
) async throws -> Int {
    let cameraID = directory.lastPathComponent
    guard [matchID, cameraID, mediaEpoch].allSatisfy(isMediaCameraID) else {
        throw RecordingError("比赛 ID、机位目录名或媒体周期不是有效的 v2 标识")
    }
    guard let scheme = gateway.scheme?.lowercased(), ["http", "https"].contains(scheme),
          let host = gateway.host, !host.isEmpty, gateway.user == nil, gateway.password == nil,
          gateway.query == nil, gateway.fragment == nil else {
        throw RecordingError("需要有效的 HTTP 或 HTTPS 网关地址")
    }
    let base = gateway.appending(path: "v2/matches/\(matchID)/cameras/\(cameraID)/epochs/\(mediaEpoch)")
    let segments = directory.appending(path: "segments")
    let numbers = try segmentNumbers(in: segments, cameraId: cameraID, indexSuffix: ".json")
    let initialization = try mediaBytes(at: directory.appending(path: "init.mp4"))
    let initDigest = sha256(initialization)
    let decoder = JSONDecoder()

    for (offset, number) in numbers.enumerated() {
        let location = "机位 \(cameraID)，片段 \(number)"
        let indexData = try mediaBytes(at: segments.appending(path: "\(number).json"), maxBytes: 1_000_000)
        let index: MediaIndex
        do {
            index = try decoder.decode(MediaIndex.self, from: indexData)
        } catch {
            throw RecordingError("\(location)：无法解析 v2 媒体索引，\(error.localizedDescription)")
        }
        let segment = try mediaBytes(at: segments.appending(path: "\(number).m4s"))
        guard index.initSha256 == initDigest, index.segmentBytes == segment.count,
              index.segmentSha256 == sha256(segment) else {
            throw RecordingError("\(location)：本地媒体字节数或 SHA-256 与索引不一致")
        }

        if offset == 0 {
            try await putMedia(initialization, to: base.appending(path: "init.mp4"), digest: initDigest, session: session)
        }
        try await putMedia(indexData, to: base.appending(path: "segments/\(number)/index"), session: session)
        try await putMedia(segment, to: base.appending(path: "segments/\(number).m4s"),
                           digest: index.segmentSha256, session: session)
    }
    return numbers.count
}
