import Foundation

/// MVS 相机的曝光触发方式。`free` 为相机自由连拍，各机位曝光相位不对齐；
/// 其余三种由外部触发源决定曝光时刻，同一触发源下的相机曝光对齐。
enum MVSTriggerSource: String, Decodable, Sendable, Equatable {
    case free
    /// 共用外触发（硬件触发线 Line 0／Line 2）。
    case line0 = "Line0"
    case line2 = "Line2"
    /// GigE Vision Action Command。须同时运行 `trigger-scheduler` 按 PTP 时间表广播定时动作。
    case action
}

struct MVSTriggerSettings: Decodable, Sendable, Equatable {
    var source: MVSTriggerSource = .free
    /// 硬件触发的有效沿，仅用于 Line 触发。
    var activation: String = "RisingEdge"
    /// 省略时硬件触发用 `FrameStart`，动作命令用 `FrameBurstStart`（与海康手册 14.4 一致，每次触发一帧）。
    var selector: String?
    var actionDeviceKey: UInt32 = 1
    var actionGroupKey: UInt32 = 1
    var actionGroupMask: UInt32 = 1

    init() {}

    enum CodingKeys: CodingKey { case source, activation, selector, actionDeviceKey, actionGroupKey, actionGroupMask }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try c.decodeIfPresent(MVSTriggerSource.self, forKey: .source) ?? .free
        activation = try c.decodeIfPresent(String.self, forKey: .activation) ?? "RisingEdge"
        selector = try c.decodeIfPresent(String.self, forKey: .selector)
        actionDeviceKey = try c.decodeIfPresent(UInt32.self, forKey: .actionDeviceKey) ?? 1
        actionGroupKey = try c.decodeIfPresent(UInt32.self, forKey: .actionGroupKey) ?? 1
        actionGroupMask = try c.decodeIfPresent(UInt32.self, forKey: .actionGroupMask) ?? 1
    }

    var resolvedSelector: String { selector ?? (source == .action ? "FrameBurstStart" : "FrameStart") }
    var triggerSourceNode: String { source == .action ? "Action1" : source.rawValue }

    /// 写入清单头行 `clockSource`，现场测试须记录触发方式。
    var summary: String {
        switch source {
        case .free: return "自由连拍（曝光相位不对齐）"
        case .line0, .line2: return "硬件触发 \(source.rawValue) \(activation)"
        case .action: return "动作命令触发 key=\(actionDeviceKey) group=\(actionGroupKey) mask=\(actionGroupMask)"
        }
    }
}

/// 按 PTP 时间轴对齐到整秒的曝光时刻表。所有相机收到同一时间表，曝光时刻由相机的 PTP 时钟保证对齐，
/// 主机时钟只影响提前量，不影响各机位之间的相位。
struct ActionSchedule: Equatable {
    let framerate: Int
    let taiUtcOffsetSeconds: Int
    let tickHz: Int64

    init(framerate: Int, taiUtcOffsetSeconds: Int = 37, tickHz: Int64 = 1_000_000_000) throws {
        guard (1...240).contains(framerate) else { throw RecordingError("定时触发的帧率须为 1–240 的整数") }
        guard (0...100).contains(taiUtcOffsetSeconds) else { throw RecordingError("TAI−UTC 偏移须在 0–100 秒") }
        guard (1...9_000_000_000).contains(tickHz) else { throw RecordingError("设备时间戳频率无效") }
        self.framerate = framerate
        self.taiUtcOffsetSeconds = taiUtcOffsetSeconds
        self.tickHz = tickHz
    }

    /// 第 `index` 帧的 UTC 纳秒，index 以 Unix 纪元起算，整秒处恰好对齐。
    func utcNanos(index: Int64) -> Int64 {
        let seconds = index / Int64(framerate)
        let remainder = index % Int64(framerate)
        return seconds * 1_000_000_000 + remainder * 1_000_000_000 / Int64(framerate)
    }

    /// 严格晚于 `utcNanos` 的第一帧序号。
    func firstIndex(after utcNanos: Int64) -> Int64 {
        let seconds = utcNanos / 1_000_000_000
        let remainder = utcNanos % 1_000_000_000
        var index = seconds * Int64(framerate) + remainder * Int64(framerate) / 1_000_000_000
        while self.utcNanos(index: index) <= utcNanos { index += 1 }
        return index
    }

    /// 设备时钟计数值（PTP 时间为 TAI，加 TAI−UTC 偏移）。
    func deviceTicks(index: Int64) -> Int64 {
        let taiNanos = utcNanos(index: index) + Int64(taiUtcOffsetSeconds) * 1_000_000_000
        return (taiNanos / 1_000_000_000) * tickHz + (taiNanos % 1_000_000_000) * tickHz / 1_000_000_000
    }
}
