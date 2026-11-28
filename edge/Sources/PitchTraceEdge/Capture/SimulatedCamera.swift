import Foundation

/// 模拟相机的故障注入与画面参数，只用于无相机时的回归和场务演练。
struct SimulationSettings: Decodable, Sendable {
    /// 送完这么多帧后正常结束；省略时一直运行到收到停止信号。
    var frames: Int?
    /// 按帧率节拍送帧；false 时尽快送帧，编码器繁忙则等待。
    var realtime: Bool = true
    /// 固定设备时钟起点（UTC 微秒），便于回归比较；省略时取当前时刻。
    var startUtcUs: Int64?
    var exposureUs: Double = 2_000
    var transferLatencyUs: Int64 = 8_000
    /// 设备已曝光但未送达主机的帧序号，帧号随之出现缺口。
    var dropFrames: [Int] = []
    var clockResetAtFrame: Int?
    var ptpUnlockAtFrame: Int?
    var failAtFrame: Int?

    init() {}

    enum CodingKeys: CodingKey {
        case frames, realtime, startUtcUs, exposureUs, transferLatencyUs, dropFrames
        case clockResetAtFrame, ptpUnlockAtFrame, failAtFrame
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        frames = try c.decodeIfPresent(Int.self, forKey: .frames)
        realtime = try c.decodeIfPresent(Bool.self, forKey: .realtime) ?? true
        startUtcUs = try c.decodeIfPresent(Int64.self, forKey: .startUtcUs)
        exposureUs = try c.decodeIfPresent(Double.self, forKey: .exposureUs) ?? 2_000
        transferLatencyUs = try c.decodeIfPresent(Int64.self, forKey: .transferLatencyUs) ?? 8_000
        dropFrames = try c.decodeIfPresent([Int].self, forKey: .dropFrames) ?? []
        clockResetAtFrame = try c.decodeIfPresent(Int.self, forKey: .clockResetAtFrame)
        ptpUnlockAtFrame = try c.decodeIfPresent(Int.self, forKey: .ptpUnlockAtFrame)
        failAtFrame = try c.decodeIfPresent(Int.self, forKey: .failAtFrame)
        if let startUtcUs, startUtcUs < 0 { throw RecordingError("simulate.startUtcUs 不能为负") }
    }
}

/// 生成 Bayer RG 8 测试画面：横向灰阶加一块逐帧移动的亮块，设备时钟按 PTP（TAI）计时。
final class SimulatedCamera: CameraFrameSource {
    let tickHz: UInt64 = 1_000_000_000
    let sourceDescription: String
    private let width: Int
    private let height: Int
    private let pixelFormat: CameraPixelFormat
    private let periodNs: UInt64
    private let settings: SimulationSettings
    private let taiOffsetNs: UInt64
    private var startNs: UInt64 = 0
    private var startHost = DispatchTime.now()
    private var index = 0
    private var delivered = 0

    init(width: Int, height: Int, pixelFormat: CameraPixelFormat, framerate: Double,
         taiUtcOffsetSeconds: Int, settings: SimulationSettings, name: String) throws {
        guard framerate > 0, framerate <= 240 else { throw RecordingError("模拟帧率须在 0–240 fps 内") }
        if let frames = settings.frames, frames <= 0 { throw RecordingError("simulate.frames 须为正整数") }
        self.width = width
        self.height = height
        self.pixelFormat = pixelFormat
        periodNs = UInt64((1e9 / framerate).rounded())
        self.settings = settings
        taiOffsetNs = UInt64(taiUtcOffsetSeconds) * 1_000_000_000
        sourceDescription = "模拟相机 \(name)"
    }

    func start() throws {
        let utcUs = settings.startUtcUs ?? Int64(Date().timeIntervalSince1970 * 1e6)
        startNs = UInt64(utcUs) * 1000
        startHost = .now()
    }

    func nextFrame(timeoutMs: Int) throws -> CameraFrame? {
        while settings.dropFrames.contains(index) { index += 1 }
        if let frames = settings.frames, delivered >= frames { throw CameraSourceFinished() }
        if index == settings.failAtFrame { throw RecordingError("模拟相机在第 \(index) 帧断开") }
        let offset = UInt64(index) * periodNs
        if settings.realtime {
            let due = startHost.uptimeNanoseconds + offset
            let now = DispatchTime.now().uptimeNanoseconds
            if due > now {
                let wait = due - now
                if wait > UInt64(timeoutMs) * 1_000_000 {
                    Thread.sleep(forTimeInterval: Double(timeoutMs) / 1000)
                    return nil
                }
                Thread.sleep(forTimeInterval: Double(wait) / 1e9)
            }
        }
        var ticks = startNs + taiOffsetNs + offset
        if let reset = settings.clockResetAtFrame, index >= reset { ticks = UInt64(index - reset + 1) * periodNs }
        let exposureStartUs = Int64((startNs + offset) / 1000)
        let frame = CameraFrame(width: width, height: height, pixelFormat: pixelFormat,
                                pixels: pattern(index), frameNumber: UInt64(index), deviceTicks: ticks,
                                exposureUs: settings.exposureUs,
                                hostReceivedUtcUs: exposureStartUs + Int64(settings.exposureUs) + settings.transferLatencyUs)
        index += 1
        delivered += 1
        return frame
    }

    func clockState() throws -> CameraClockState {
        if let unlock = settings.ptpUnlockAtFrame, index >= unlock {
            return CameraClockState(locked: false, detail: "模拟 PTP 失锁")
        }
        return CameraClockState(locked: true, detail: "模拟 PTP 从钟已锁定")
    }

    func stop() {}

    private func pattern(_ n: Int) -> Data {
        var bytes = Data(count: width * height)
        let side = max(4, min(width, height) / 6)
        let left = (n * 4) % max(1, width - side), top = height / 3
        bytes.withUnsafeMutableBytes { raw in
            let p = raw.bindMemory(to: UInt8.self).baseAddress!
            for y in 0..<height {
                for x in 0..<width {
                    var v = UInt8(x * 200 / width + 20)
                    if (left..<(left + side)).contains(x) && (top..<(top + side)).contains(y) { v = 250 }
                    // Bayer 马赛克中绿色像素略亮，解码后呈偏绿的草地色。
                    if pixelFormat == .bayerRG8 && (x + y) % 2 == 1 { v = UInt8(min(255, Int(v) + 30)) }
                    p[y * width + x] = v
                }
            }
        }
        return bytes
    }
}
