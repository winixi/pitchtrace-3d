import Foundation

/// 相机输出的未压缩像素格式。首测按 Bayer RG 8 采集，Mono8 用于黑白机型和调试。
enum CameraPixelFormat: String, Codable, Sendable {
    case bayerRG8 = "BayerRG8"
    case mono8 = "Mono8"
}

/// 设备送出的一帧原图及其时间证据。`deviceTicks` 按 `CameraFrameSource.tickHz` 计数。
struct CameraFrame: Sendable {
    let width: Int
    let height: Int
    let pixelFormat: CameraPixelFormat
    /// 每行 `width` 字节，无填充。
    let pixels: Data
    let frameNumber: UInt64
    let deviceTicks: UInt64
    /// 本帧实际曝光时长。
    let exposureUs: Double
    /// 主机收齐本帧的 UTC 微秒，只用于低精度对齐和延迟统计。
    let hostReceivedUtcUs: Int64
}

/// 设备时钟的锁定状态。PTP 模式下失锁即结束当前媒体周期。
struct CameraClockState: Sendable, Equatable {
    let locked: Bool
    let detail: String
}

/// 采集端的帧来源。实现需按设备输出顺序返回帧，超时返回 nil，设备断开时抛错。
protocol CameraFrameSource: AnyObject {
    var tickHz: UInt64 { get }
    /// 写入曝光清单头行，说明设备型号、序列号和时间戳来源。
    var sourceDescription: String { get }
    func start() throws
    func nextFrame(timeoutMs: Int) throws -> CameraFrame?
    func clockState() throws -> CameraClockState
    func stop()
}

/// 有限的帧源（模拟相机设置帧数时）在送完全部帧后抛出此错误，采集正常封口。
struct CameraSourceFinished: Error {}
