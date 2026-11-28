import Foundation

/// 控制请求无法执行时的原因，映射为 HTTP 409。
struct CaptureControlConflict: Error, CustomStringConvertible {
    let description: String
}

/// 为场务启动和结束 `capture`。
///
/// 结束采集只写入采集目录的 STOP 标记，与 `edge/docs/capture.md` 记载的有序停止方式相同，
/// 因此也能结束 Web UI 之外启动的采集。Web UI 进程退出不会结束采集。
final class CaptureController: @unchecked Sendable {
    let executable: URL
    let configURL: URL
    let root: URL

    private let lock = NSLock()
    private var process: Process?
    private var state = CaptureProcessState()
    private var stopRequested = false
    private static let stderrLimit = 2000

    /// `executable` 以 `capture <配置> <目录>` 调用，默认为当前 Edge 程序。
    init(executable: URL, configURL: URL, root: URL) {
        self.executable = executable
        self.configURL = configURL
        self.root = root
    }

    var processState: CaptureProcessState {
        lock.lock()
        defer { lock.unlock() }
        return state
    }

    private var stopMarker: URL { root.appendingPathComponent("STOP") }

    @discardableResult
    func start(now: Date = Date()) throws -> String {
        lock.lock()
        defer { lock.unlock() }
        if state.running {
            throw CaptureControlConflict(description: "Capture is already running")
        }
        if captureIsLive(root: root, now: now) {
            throw CaptureControlConflict(description: "Another capture process is writing \(root.path)")
        }
        // 上一次停止留下的标记会让 capture 拒绝启动；此时已确认没有采集在运行。
        try? FileManager.default.removeItem(at: stopMarker)

        let child = Process()
        child.executableURL = executable
        child.arguments = ["capture", configURL.path, root.path]
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = FileHandle.nullDevice
        let errors = Pipe()
        child.standardError = errors
        errors.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard let self, !data.isEmpty else { return }
            self.appendStderr(data)
        }
        child.terminationHandler = { [weak self] finished in
            errors.fileHandleForReading.readabilityHandler = nil
            let rest = errors.fileHandleForReading.readDataToEndOfFile()
            self?.finish(finished, trailing: rest)
        }
        do {
            try child.run()
        } catch {
            throw ValidationError("Failed to launch capture: \(error.localizedDescription)")
        }
        process = child
        stopRequested = false
        state = CaptureProcessState(running: true, exitCode: nil, stderrTail: "")
        return "Capture started for \(root.lastPathComponent)"
    }

    @discardableResult
    func stop(now: Date = Date()) throws -> String {
        lock.lock()
        defer { lock.unlock() }
        let live = captureIsLive(root: root, now: now)
        guard state.running || live else {
            throw CaptureControlConflict(description: "No capture is running")
        }
        // capture 在写出首份 health.json 之前若看到 STOP 会拒绝启动并报错，此时请场务稍后重试。
        guard live else {
            throw CaptureControlConflict(description: "Capture is still starting; retry Stop in a moment")
        }
        guard FileManager.default.createFile(atPath: stopMarker.path, contents: Data()) else {
            throw ValidationError("Cannot write stop marker \(stopMarker.path)")
        }
        stopRequested = true
        return "Stop requested; channels will seal their current segments"
    }

    /// 等待由本控制器启动的进程退出，供测试和关闭流程使用。
    func waitUntilExit() {
        lock.lock()
        let child = process
        lock.unlock()
        child?.waitUntilExit()
        // terminationHandler 可能稍晚于 waitUntilExit 返回。
        for _ in 0..<200 where processState.running {
            Thread.sleep(forTimeInterval: 0.01)
        }
    }

    private func appendStderr(_ data: Data) {
        lock.lock()
        defer { lock.unlock() }
        state.stderrTail = String((state.stderrTail + String(decoding: data, as: UTF8.self)).suffix(Self.stderrLimit))
    }

    private func finish(_ child: Process, trailing: Data) {
        lock.lock()
        defer { lock.unlock() }
        guard child === process else { return }
        if !trailing.isEmpty {
            state.stderrTail = String((state.stderrTail + String(decoding: trailing, as: UTF8.self)).suffix(Self.stderrLimit))
        }
        state.running = false
        state.exitCode = child.terminationStatus
        // 标记已完成使命，移走后命令行可直接在同一目录重新开始。
        if stopRequested {
            try? FileManager.default.removeItem(at: stopMarker)
            stopRequested = false
        }
    }
}
