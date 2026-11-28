import Foundation
import Testing
@testable import PitchTraceEdge

struct CameraCaptureTests {
    private func frame(ticks: UInt64, exposureUs: Double = 2000, host: Int64 = 0, number: UInt64 = 0,
                       format: CameraPixelFormat = .bayerRG8, size: (Int, Int) = (2, 2),
                       pixels: [UInt8]? = nil) -> CameraFrame {
        CameraFrame(width: size.0, height: size.1, pixelFormat: format,
                    pixels: Data(pixels ?? [UInt8](repeating: 0, count: size.0 * size.1)),
                    frameNumber: number, deviceTicks: ticks, exposureUs: exposureUs, hostReceivedUtcUs: host)
    }

    @Test func deviceTicksConvertWithoutOverflow() throws {
        let nanos = try ExposureClock(settings: .init(mode: .ptpUTC), tickHz: 1_000_000_000)
        // 2026 年的 TAI 纳秒直接乘 10⁶ 会溢出 UInt64。
        #expect(nanos.deviceMicroseconds(1_795_494_634_700_000_999) == 1_795_494_634_700_000)
        let ticks100MHz = try ExposureClock(settings: .init(mode: .ptpUTC), tickHz: 100_000_000)
        #expect(ticks100MHz.deviceMicroseconds(250) == 2)
        #expect(nanos.deviceMicroseconds(UInt64.max) == 18_446_744_073_709_551)
        #expect(throws: RecordingError.self) { try ExposureClock(settings: .init(mode: .ptpUTC), tickHz: 0) }
    }

    @Test func ptpTimestampsShiftToExposureCenterAndUTC() throws {
        let taiNs: UInt64 = 1_795_494_634_700_000_000
        var start = try ExposureClock(settings: .init(mode: .ptpUTC), tickHz: 1_000_000_000)
        let f = frame(ticks: taiNs, exposureUs: 3001)
        try start.observe(f)
        #expect(try start.captureUtcUs(f) == 1_795_494_597_700_000 + 1501)
        var center = try ExposureClock(settings: .init(mode: .ptpUTC, timestampEvent: .exposureCenter,
                                                       taiUtcOffsetSeconds: 0), tickHz: 1_000_000_000)
        try center.observe(f)
        #expect(try center.captureUtcUs(f) == 1_795_494_634_700_000)
        var early = try ExposureClock(settings: .init(mode: .ptpUTC), tickHz: 1_000_000_000)
        #expect(throws: RecordingError.self) { try early.captureUtcUs(frame(ticks: 1_000)) }
        #expect(throws: RecordingError.self) { try early.exposureCenterDeviceUs(frame(ticks: 1, exposureUs: .nan)) }
    }

    @Test func repeatedOrBackwardTimestampsAreClockResets() throws {
        var clock = try ExposureClock(settings: .init(mode: .ptpUTC), tickHz: 1_000_000_000)
        try clock.observe(frame(ticks: 5_000))
        #expect(throws: RecordingError.self) { try clock.observe(frame(ticks: 5_000)) }
        #expect(throws: RecordingError.self) { try clock.observe(frame(ticks: 4_999)) }
        try clock.observe(frame(ticks: 5_001))
    }

    @Test func hostFitFreezesTheSmallestTransferDelay() throws {
        var clock = try ExposureClock(settings: .init(mode: .hostFit), tickHz: 1_000_000)
        var frames: [CameraFrame] = []
        for i in 0..<20 {
            // 设备时钟从 0 起计；主机收帧比曝光中心晚 9–13 ms，第 7 帧最快。
            let center = Int64(i) * 33_333 + 1000
            let delay: Int64 = i == 7 ? 9_000 : 13_000
            frames.append(frame(ticks: UInt64(i * 33_333), host: 1_000_000_000 + center + delay, number: UInt64(i)))
        }
        for f in frames { try clock.observe(f) }
        #expect(try clock.captureUtcUs(frames[0]) == 1_000_009_000 + 1000)
        #expect(try clock.captureUtcUs(frames[19]) == 1_000_009_000 + 19 * 33_333 + 1000)
        #expect(ExposureClockSettings(mode: .hostFit).clockSource(device: "A").hasPrefix("低精度"))
        #expect(!ExposureClockSettings(mode: .ptpUTC).clockSource(device: "A").hasPrefix("低精度"))
    }

    @Test func clockSettingsDefaultToExposureStartAndRejectBadOffsets() throws {
        let decoded = try JSONDecoder().decode(ExposureClockSettings.self, from: Data(#"{"mode":"ptp-utc"}"#.utf8))
        #expect(decoded == ExposureClockSettings(mode: .ptpUTC, timestampEvent: .exposureStart, taiUtcOffsetSeconds: 37))
        for json in [#"{"mode":"ntp"}"#, #"{"mode":"host-fit","taiUtcOffsetSeconds":-1}"#] {
            #expect(throws: (any Error).self) { try JSONDecoder().decode(ExposureClockSettings.self, from: Data(json.utf8)) }
        }
    }

    @Test func ptpTimeMustAgreeWithHostAndComeFromALockedRole() throws {
        try checkPTPAgreesWithHost(captureUtcUs: 10_000_000, hostReceivedUtcUs: 15_000_000)
        try checkPTPAgreesWithHost(captureUtcUs: 15_000_000, hostReceivedUtcUs: 10_000_000)
        // 相机自任主时钟、未授时到 UTC 时，换算结果偏离主机时间数十年。
        #expect(throws: RecordingError.self) {
            try checkPTPAgreesWithHost(captureUtcUs: 10_000_000, hostReceivedUtcUs: 15_000_001)
        }
        #expect(ptpRoleIsLocked("Slave") && ptpRoleIsLocked("Master"))
        for role in ["Listening", "Uncalibrated", "Faulty", "Disabled", "Initializing", "PreMaster", "Passive", ""] {
            #expect(!ptpRoleIsLocked(role))
        }
    }

    private func bgra(_ f: CameraFrame) throws -> [UInt8] {
        // 每行多留 4 字节，确认按 bytesPerRow 而非宽度换行。
        var out = [UInt8](repeating: 0, count: (4 * f.width + 4) * f.height)
        try out.withUnsafeMutableBytes { try convertToBGRA(f, into: $0.baseAddress!, bytesPerRow: 4 * f.width + 4) }
        return out
    }

    @Test func uniformBayerMosaicDemosaicsToOneColourIncludingEdges() throws {
        let (w, h) = (6, 4)
        var mosaic = [UInt8](repeating: 0, count: w * h)
        for y in 0..<h {
            for x in 0..<w {
                mosaic[y * w + x] = switch (y % 2, x % 2) { case (0, 0): 200; case (1, 1): 50; default: 100 }
            }
        }
        let out = try bgra(frame(ticks: 0, size: (w, h), pixels: mosaic))
        let stride = 4 * w + 4
        for y in 0..<h {
            for x in 0..<w {
                #expect(Array(out[(y * stride + 4 * x)..<(y * stride + 4 * x + 4)]) == [50, 100, 200, 255])
            }
        }
    }

    @Test func monoCopiesGrayAndBayerNeedsEvenSize() throws {
        let out = try bgra(frame(ticks: 0, format: .mono8, size: (3, 2), pixels: [1, 2, 3, 4, 5, 6]))
        #expect(Array(out[4..<8]) == [2, 2, 2, 255])
        #expect(throws: RecordingError.self) { try bgra(frame(ticks: 0, size: (3, 2))) }
        #expect(throws: RecordingError.self) { try bgra(frame(ticks: 0, size: (2, 2), pixels: [1, 2, 3])) }
    }
}
