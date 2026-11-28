import Foundation

/// 设备时间戳对应的曝光事件。海康机型需实机核对，未确认前按配置记录。
enum TimestampEvent: String, Codable, Sendable {
    case exposureStart
    case exposureCenter
}

/// 设备时钟到 UTC 的换算方式。
enum ExposureClockMode: String, Codable, Sendable {
    /// 设备已锁定到以 TAI 计时的 PTP 主时钟，减去 TAI−UTC 偏移即得 UTC。
    case ptpUTC = "ptp-utc"
    /// 未经 PTP 核验：按主机收帧时刻的最小延迟对齐设备时钟，只能作低精度输入。
    case hostFit = "host-fit"
}

struct ExposureClockSettings: Codable, Sendable, Equatable {
    var mode: ExposureClockMode
    var timestampEvent: TimestampEvent
    /// 2017 年起 TAI−UTC 为 37 秒。主时钟直接发布 UTC 时填 0。
    var taiUtcOffsetSeconds: Int

    init(mode: ExposureClockMode, timestampEvent: TimestampEvent = .exposureStart, taiUtcOffsetSeconds: Int = 37) {
        self.mode = mode
        self.timestampEvent = timestampEvent
        self.taiUtcOffsetSeconds = taiUtcOffsetSeconds
    }

    enum CodingKeys: CodingKey { case mode, timestampEvent, taiUtcOffsetSeconds }

    init(from decoder: Decoder) throws {
        let keyed = try decoder.container(keyedBy: CodingKeys.self)
        mode = try keyed.decode(ExposureClockMode.self, forKey: .mode)
        timestampEvent = try keyed.decodeIfPresent(TimestampEvent.self, forKey: .timestampEvent) ?? .exposureStart
        taiUtcOffsetSeconds = try keyed.decodeIfPresent(Int.self, forKey: .taiUtcOffsetSeconds) ?? 37
        guard (0...100).contains(taiUtcOffsetSeconds) else {
            throw RecordingError("clock.taiUtcOffsetSeconds 须为 0–100 秒")
        }
    }

    /// 写入曝光清单 `clockSource`，供复核时区分精度。
    func clockSource(device: String) -> String {
        let event = timestampEvent == .exposureStart ? "曝光起点，加半个曝光时长取曝光中心" : "曝光中心"
        switch mode {
        case .ptpUTC:
            return "\(device)；PTP 设备时间戳（TAI−UTC \(taiUtcOffsetSeconds) s），时间戳按\(event)"
        case .hostFit:
            return "低精度：\(device)；设备时间戳按主机收帧最小延迟对齐 UTC，未经 PTP 核验，时间戳按\(event)"
        }
    }
}

/// 将设备时间戳换算为曝光中心 UTC 微秒。设备时间戳须严格递增，回退视为时钟重置。
struct ExposureClock {
    let settings: ExposureClockSettings
    let tickHz: UInt64
    /// host-fit 需要的样本数。冻结后整个周期使用同一偏移，保证曝光 UTC 单调。
    static let hostFitSamples = 15

    private var lastTicks: UInt64?
    private var hostOffsetUs: Int64?
    private var hostSamples: [Int64] = []

    init(settings: ExposureClockSettings, tickHz: UInt64) throws {
        guard tickHz > 0 else { throw RecordingError("设备时间戳频率必须大于零") }
        self.settings = settings
        self.tickHz = tickHz
    }

    /// 设备时间戳换算为微秒，避免 tick × 10⁶ 溢出。
    func deviceMicroseconds(_ ticks: UInt64) -> Int64? {
        let whole = ticks / tickHz, rest = ticks % tickHz
        let (scaled, overflow) = whole.multipliedReportingOverflow(by: 1_000_000)
        guard !overflow else { return nil }
        let micros = scaled + rest * 1_000_000 / tickHz
        return micros <= UInt64(Int64.max) ? Int64(micros) : nil
    }

    /// 曝光中心在设备时钟上的微秒值。
    func exposureCenterDeviceUs(_ frame: CameraFrame) throws -> Int64 {
        guard let device = deviceMicroseconds(frame.deviceTicks) else {
            throw RecordingError("设备时间戳超出可表示范围")
        }
        guard frame.exposureUs.isFinite, (0...10_000_000).contains(frame.exposureUs) else {
            throw RecordingError("帧 \(frame.frameNumber) 的曝光时长无效")
        }
        let half = settings.timestampEvent == .exposureStart ? Int64((frame.exposureUs / 2).rounded()) : 0
        return device + half
    }

    /// 按设备顺序登记每一帧；时间戳回退或重复时报错，由采集端结束当前周期。
    mutating func observe(_ frame: CameraFrame) throws {
        if let lastTicks, frame.deviceTicks <= lastTicks {
            throw RecordingError("设备时间戳未递增（帧 \(frame.frameNumber)），按时钟重置结束本周期")
        }
        lastTicks = frame.deviceTicks
        if settings.mode == .hostFit, hostOffsetUs == nil {
            let center = try exposureCenterDeviceUs(frame)
            // 主机收帧总晚于曝光，最小差值最接近真实偏移。
            hostSamples.append(frame.hostReceivedUtcUs - center)
            if hostSamples.count >= Self.hostFitSamples { hostOffsetUs = hostSamples.min() }
        }
    }

    /// 已登记帧的曝光中心 UTC。host-fit 在样本不足时用已有样本冻结偏移。
    mutating func captureUtcUs(_ frame: CameraFrame) throws -> Int64 {
        let center = try exposureCenterDeviceUs(frame)
        let utc: Int64
        switch settings.mode {
        case .ptpUTC:
            utc = center - Int64(settings.taiUtcOffsetSeconds) * 1_000_000
        case .hostFit:
            if hostOffsetUs == nil {
                guard let offset = hostSamples.min() else { throw RecordingError("尚无主机收帧样本") }
                hostOffsetUs = offset
            }
            utc = center + hostOffsetUs!
        }
        guard utc >= 0 else { throw RecordingError("曝光 UTC 早于 1970 年，请核对 PTP 时钟或 TAI 偏移") }
        return utc
    }
}
