import Foundation

/// Live 签发的设备凭据，取自环境变量 PITCHTRACE_DEVICE_TOKEN（run-match 由此传入）。
struct DeviceCredential: Equatable {
    let token: String

    /// 未设置时返回 nil。格式不对时报错，但错误信息不含凭据本身。
    static func fromEnvironment(_ environment: [String: String] = ProcessInfo.processInfo.environment) throws -> DeviceCredential? {
        guard let raw = environment["PITCHTRACE_DEVICE_TOKEN"], !raw.isEmpty else { return nil }
        let token = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard token.wholeMatch(of: /pt1\.[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9_-]{43}/) != nil else {
            throw RecordingError("PITCHTRACE_DEVICE_TOKEN 格式无效，应为 Live 签发的 pt1.<设备 ID>.<密钥>")
        }
        return DeviceCredential(token: token)
    }
}

/// 凭据只经 HTTPS 或回环地址发送；隔离的现场局域网需设置 PITCHTRACE_GATEWAY_ALLOW_HTTP=1。
func checkCredentialTransport(_ gateway: URL, credential: DeviceCredential?,
                              environment: [String: String] = ProcessInfo.processInfo.environment) throws {
    guard credential != nil, gateway.scheme?.lowercased() != "https" else { return }
    let host = gateway.host ?? ""
    let loopback = host == "localhost" || host == "::1" || host.hasPrefix("127.")
    guard loopback || environment["PITCHTRACE_GATEWAY_ALLOW_HTTP"] == "1" else {
        throw RecordingError("设备凭据只经 HTTPS 或回环地址发送；隔离的现场局域网可设 PITCHTRACE_GATEWAY_ALLOW_HTTP=1")
    }
}

private func putMedia(_ data: Data, to url: URL, digest: String? = nil, credential: DeviceCredential?,
                      session: URLSession) async throws {
    var request = URLRequest(url: url)
    request.httpMethod = "PUT"
    if let credential { request.setValue("Bearer \(credential.token)", forHTTPHeaderField: "Authorization") }
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
    from directory: URL, gateway: URL, matchID: String, mediaEpoch: String? = nil,
    credential: DeviceCredential? = nil, session: URLSession
) async throws -> Int {
    let cameraID = directory.lastPathComponent
    guard isMediaCameraID(matchID), isMediaCameraID(cameraID) else {
        throw RecordingError("比赛 ID 或机位目录名不是有效的 v2 标识")
    }
    guard let scheme = gateway.scheme?.lowercased(), ["http", "https"].contains(scheme),
          let host = gateway.host, !host.isEmpty, gateway.user == nil, gateway.password == nil,
          gateway.query == nil, gateway.fragment == nil else {
        throw RecordingError("需要有效的 HTTP 或 HTTPS 网关地址")
    }
    try checkCredentialTransport(gateway, credential: credential)
    let periods = try cameraMediaPeriods(in: directory)
    let base = gateway.appending(path: "v2/matches/\(matchID)/cameras/\(cameraID)/epochs")
    var total = 0
    for period in periods {
        guard period.id == nil || mediaEpoch == nil else {
            throw RecordingError("机位 \(cameraID) 已在 epochs 目录指定媒体周期，请省略命令中的媒体周期")
        }
        guard let epoch = period.id ?? mediaEpoch else {
            throw RecordingError("机位 \(cameraID) 使用平铺目录，请在命令中指定媒体周期")
        }
        guard isMediaCameraID(epoch) else {
            throw RecordingError("媒体周期不是有效的 v2 标识")
        }
        total += try await publishMediaPeriod(
            from: period.directory, cameraID: cameraID, mediaEpoch: epoch,
            to: base.appending(path: epoch), credential: credential, session: session
        )
    }
    return total
}

private func publishMediaPeriod(
    from directory: URL, cameraID: String, mediaEpoch: String, to base: URL, credential: DeviceCredential?,
    session: URLSession
) async throws -> Int {
    let segments = directory.appending(path: "segments")
    let numbers = try segmentNumbers(in: segments, cameraId: cameraID, indexSuffix: ".json", mediaEpoch: mediaEpoch)
    let initialization = try mediaBytes(at: directory.appending(path: "init.mp4"))
    let initDigest = sha256(initialization)
    let decoder = JSONDecoder()

    for (offset, number) in numbers.enumerated() {
        let location = "\(recordingLocation(cameraId: cameraID, mediaEpoch: mediaEpoch))，片段 \(number)"
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
            try await putMedia(initialization, to: base.appending(path: "init.mp4"), digest: initDigest,
                               credential: credential, session: session)
        }
        try await putMedia(indexData, to: base.appending(path: "segments/\(number)/index"),
                           credential: credential, session: session)
        try await putMedia(segment, to: base.appending(path: "segments/\(number).m4s"),
                           digest: index.segmentSha256, credential: credential, session: session)
    }
    return numbers.count
}
