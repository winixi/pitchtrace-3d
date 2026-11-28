import Foundation

/// `capture.py` 写入周期目录的单机位采集任务。字段校验以 `capture.py` 为准，这里只补默认值。
struct CameraCaptureJob: Decodable {
    let matchId: String
    let cameraId: String
    let mediaEpoch: String
    let segmentSeconds: Int
    let camera: CameraCaptureSettings
}

struct CameraCaptureSettings: Decodable {
    let type: String
    let source: String
    let width: Int
    let height: Int
    let pixelFormat: CameraPixelFormat
    let framerate: Double
    let bitrate: Int
    let clock: ExposureClockSettings
    let simulate: SimulationSettings
    let mvs: MVSSettings

    enum CodingKeys: CodingKey {
        case type, source, width, height, pixelFormat, framerate, bitrate, clock, simulate, mvs
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = try c.decode(String.self, forKey: .type)
        source = try c.decode(String.self, forKey: .source)
        width = try c.decodeIfPresent(Int.self, forKey: .width) ?? 1920
        height = try c.decodeIfPresent(Int.self, forKey: .height) ?? 1080
        pixelFormat = try c.decodeIfPresent(CameraPixelFormat.self, forKey: .pixelFormat) ?? .bayerRG8
        framerate = try c.decodeIfPresent(Double.self, forKey: .framerate) ?? 30
        bitrate = try c.decodeIfPresent(Int.self, forKey: .bitrate) ?? 8_000_000
        clock = try c.decode(ExposureClockSettings.self, forKey: .clock)
        simulate = try c.decodeIfPresent(SimulationSettings.self, forKey: .simulate) ?? SimulationSettings()
        mvs = try c.decodeIfPresent(MVSSettings.self, forKey: .mvs) ?? MVSSettings()
    }
}

/// 写入 `capture-stats.json` 的采集计数，Web UI 和复核读取。
struct CaptureStats: Encodable {
    var framesReceived = 0
    var framesRecorded = 0
    /// host-fit 对齐时钟前观测、不进入录像的帧。
    var warmupFrames = 0
    /// 编码器繁忙时丢弃的帧，不进入曝光清单。
    var encoderDrops = 0
    /// 设备帧号缺口累计，即设备已曝光但主机未收到的帧。
    var deviceFrameGaps = 0
    var receiveTimeouts = 0
    var segments = 0
    var clockSource = ""
    var clockState = ""
    var firstUtcUs: String?
    var lastUtcUs: String?
    var maxTransferLatencyUs: Int64?
    var error: String?
    var updatedUnix = 0.0
}

/// SIGINT／SIGTERM 只置位标志，由采集循环在帧边界封口退出。
final class StopFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var stopped = false
    private var sources: [DispatchSourceSignal] = []

    var isSet: Bool { lock.lock(); defer { lock.unlock() }; return stopped }
    func set() { lock.lock(); stopped = true; lock.unlock() }

    func installSignalHandlers() {
        for sig in [SIGINT, SIGTERM] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .global())
            source.setEventHandler { [weak self] in self?.set() }
            source.resume()
            sources.append(source)
        }
    }
}

/// 连续多少秒收不到图像即视为断流，交由 `capture.py` 以新周期重连。
let cameraReceiveTimeoutSeconds = 5

func makeFrameSource(_ job: CameraCaptureJob) throws -> CameraFrameSource {
    let camera = job.camera
    switch camera.type {
    case "simulated":
        return try SimulatedCamera(width: camera.width, height: camera.height, pixelFormat: camera.pixelFormat,
                                   framerate: camera.framerate, taiUtcOffsetSeconds: camera.clock.taiUtcOffsetSeconds,
                                   settings: camera.simulate, name: job.cameraId)
    case "mvs":
        return try makeMVSCamera(serial: camera.source, width: camera.width, height: camera.height,
                                 pixelFormat: camera.pixelFormat, framerate: camera.framerate, settings: camera.mvs,
                                 requirePTP: camera.clock.mode == .ptpUTC)
    default:
        throw RecordingError("camera-capture 不支持输入类型 \(camera.type)")
    }
}

/// 在周期目录内采集一路相机，直到停止信号、有限帧源结束或出错。出错前已封口的片段与曝光行保持有效。
func runCameraCapture(directory: URL, stop: StopFlag) throws {
    let jobData = try Data(contentsOf: directory.appendingPathComponent("camera.json"))
    let job = try JSONDecoder().decode(CameraCaptureJob.self, from: jobData)
    let camera = job.camera
    guard (1...60).contains(job.segmentSeconds), camera.framerate > 0, camera.bitrate > 0 else {
        throw RecordingError("段长、帧率或码率无效")
    }
    var stats = CaptureStats()
    let statsURL = directory.appendingPathComponent("capture-stats.json")
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    func saveStats(_ recorder: SegmentedRecorder?) {
        if let recorder {
            stats.segments = recorder.sealedSegments
            stats.firstUtcUs = recorder.firstUtcUs.map(String.init)
            stats.lastUtcUs = recorder.lastUtcUs.map(String.init)
        }
        stats.updatedUnix = Date().timeIntervalSince1970
        try? encoder.encode(stats).write(to: statsURL, options: .atomic)
    }

    var recorder: SegmentedRecorder?
    do {
        let source = try makeFrameSource(job)
        stats.clockSource = camera.clock.clockSource(device: source.sourceDescription)
        try source.start()
        defer { source.stop() }
        var clock = try ExposureClock(settings: camera.clock, tickHz: source.tickHz)
        let state = try source.clockState()
        stats.clockState = state.detail
        if camera.clock.mode == .ptpUTC && !state.locked {
            throw RecordingError("PTP 未锁定：\(state.detail)")
        }
        recorder = try SegmentedRecorder(
            directory: directory,
            settings: RecorderSettings(width: camera.width, height: camera.height, framerate: camera.framerate,
                                       bitrate: camera.bitrate, segmentSeconds: job.segmentSeconds,
                                       realtime: camera.type != "simulated" || camera.simulate.realtime),
            header: ["matchId": job.matchId, "cameraId": job.cameraId, "mediaEpoch": job.mediaEpoch,
                     "clockSource": stats.clockSource])
        var firstCenterUs: Int64?
        var lastFrameNumber: UInt64?
        var lastCheck = Date()
        var silentSince = Date()
        while !stop.isSet {
            if Date().timeIntervalSince(lastCheck) >= 1 {
                lastCheck = Date()
                let state = try source.clockState()
                stats.clockState = state.detail
                if camera.clock.mode == .ptpUTC && !state.locked {
                    throw RecordingError("PTP 失锁：\(state.detail)，结束本周期")
                }
                saveStats(recorder)
            }
            let next: CameraFrame?
            do {
                next = try source.nextFrame(timeoutMs: 1000)
            } catch is CameraSourceFinished {
                break
            }
            guard let frame = next else {
                stats.receiveTimeouts += 1
                if Date().timeIntervalSince(silentSince) >= Double(cameraReceiveTimeoutSeconds) {
                    throw RecordingError("\(cameraReceiveTimeoutSeconds) 秒未收到图像，按断流结束本周期")
                }
                continue
            }
            silentSince = Date()
            stats.framesReceived += 1
            try clock.observe(frame)
            if let lastFrameNumber, frame.frameNumber > lastFrameNumber + 1 {
                stats.deviceFrameGaps += Int(frame.frameNumber - lastFrameNumber - 1)
            }
            lastFrameNumber = frame.frameNumber
            if camera.clock.mode == .hostFit && stats.framesReceived <= ExposureClock.hostFitSamples {
                stats.warmupFrames += 1
                continue
            }
            let center = try clock.exposureCenterDeviceUs(frame)
            let utc = try clock.captureUtcUs(frame)
            let first = firstCenterUs ?? center
            firstCenterUs = first
            // 展示时间沿用曝光中心的真实间隔，丢帧和曝光变化都保留在 PTS 中。
            let pts = ((center - first) * Int64(SegmentedRecorder.timescale) + 500_000) / 1_000_000
            if camera.clock.mode == .ptpUTC {
                try checkPTPAgreesWithHost(captureUtcUs: utc, hostReceivedUtcUs: frame.hostReceivedUtcUs)
            }
            let latency = frame.hostReceivedUtcUs - utc
            stats.maxTransferLatencyUs = max(stats.maxTransferLatencyUs ?? latency, latency)
            if try recorder!.append(frame, pts: pts, captureUtcUs: utc) {
                stats.framesRecorded += 1
            } else {
                stats.encoderDrops += 1
            }
        }
        try recorder?.finish()
        saveStats(recorder)
    } catch {
        stats.error = error.localizedDescription
        // 封口已收下的帧；之前的片段和曝光行已完整落盘。
        if let recorder { try? recorder.finish() }
        saveStats(recorder)
        throw error
    }
}
