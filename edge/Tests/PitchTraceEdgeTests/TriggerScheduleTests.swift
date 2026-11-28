import Foundation
import Testing
@testable import PitchTraceEdge

struct TriggerScheduleTests {
    @Test func triggerSettingsDefaultsAndSelectors() throws {
        let free = try JSONDecoder().decode(MVSTriggerSettings.self, from: Data("{}".utf8))
        #expect(free.source == .free)
        let line = try JSONDecoder().decode(MVSTriggerSettings.self, from: Data(#"{"source":"Line0"}"#.utf8))
        #expect(line.triggerSourceNode == "Line0" && line.resolvedSelector == "FrameStart" && line.activation == "RisingEdge")
        let action = try JSONDecoder().decode(MVSTriggerSettings.self,
                                              from: Data(#"{"source":"action","actionGroupKey":9}"#.utf8))
        #expect(action.triggerSourceNode == "Action1" && action.resolvedSelector == "FrameBurstStart")
        #expect(action.actionGroupKey == 9 && action.actionDeviceKey == 1)
        #expect(action.summary.contains("动作命令触发"))
        #expect(throws: DecodingError.self) {
            try JSONDecoder().decode(MVSTriggerSettings.self, from: Data(#"{"source":"Line1"}"#.utf8))
        }
        let settings = try JSONDecoder().decode(MVSSettings.self, from: Data("{}".utf8))
        #expect(settings.trigger == nil)
    }

    @Test func gridAlignsToWholeSeconds() throws {
        let schedule = try ActionSchedule(framerate: 30)
        #expect(schedule.utcNanos(index: 30 * 1_000) == 1_000_000_000_000)
        #expect(schedule.utcNanos(index: 1) == 33_333_333)
        #expect(schedule.utcNanos(index: 2) == 66_666_666)
        #expect(schedule.utcNanos(index: 30) == 1_000_000_000)
    }

    @Test func firstIndexIsStrictlyAfter() throws {
        let schedule = try ActionSchedule(framerate: 30)
        let base: Int64 = 1_795_494_597_000_000_000
        for offset: Int64 in [0, 1, 33_333_332, 33_333_333, 33_333_334, 999_999_999] {
            let index = schedule.firstIndex(after: base + offset)
            #expect(schedule.utcNanos(index: index) > base + offset)
            #expect(schedule.utcNanos(index: index - 1) <= base + offset)
        }
        // 同一时间表下，任意两台设备得到相同序号，曝光相位对齐。
        #expect(schedule.firstIndex(after: base) == schedule.firstIndex(after: base))
    }

    @Test func deviceTicksAddTaiOffset() throws {
        let nanos = try ActionSchedule(framerate: 30)
        #expect(nanos.deviceTicks(index: 30) == 1_000_000_000 + 37_000_000_000)
        let hundredMHz = try ActionSchedule(framerate: 25, taiUtcOffsetSeconds: 0, tickHz: 100_000_000)
        #expect(hundredMHz.deviceTicks(index: 1) == 4_000_000)
        // 2026 年量级不溢出。
        let index = nanos.firstIndex(after: 1_795_494_597_000_000_000)
        #expect(nanos.deviceTicks(index: index) > 1_795_494_634_000_000_000)
    }

    @Test func rejectsInvalidSchedule() {
        #expect(throws: RecordingError.self) { try ActionSchedule(framerate: 0) }
        #expect(throws: RecordingError.self) { try ActionSchedule(framerate: 30, taiUtcOffsetSeconds: -1) }
        #expect(throws: RecordingError.self) { try ActionSchedule(framerate: 30, tickHz: 0) }
    }

    @Test func schedulerOptionsParse() throws {
        let options = try TriggerSchedulerOptions.parse(["30", "--group-key", "5", "--lead-ms", "300", "--interface-ip", "192.168.1.10"])
        #expect(options.framerate == 30 && options.groupKey == 5 && options.leadMs == 300)
        #expect(try ipv4Value("192.168.1.10") == 0xC0A8_010A)
        #expect(throws: RecordingError.self) { try TriggerSchedulerOptions.parse([]) }
        #expect(throws: RecordingError.self) { try TriggerSchedulerOptions.parse(["30", "--lead-ms", "5"]) }
        #expect(throws: RecordingError.self) { try TriggerSchedulerOptions.parse(["30", "--unknown", "1"]) }
        #expect(throws: RecordingError.self) { try TriggerSchedulerOptions.parse(["30", "--interface-ip", "300.1.1.1"]) }
        #expect(throws: RecordingError.self) { try TriggerSchedulerOptions.parse(["0"]) }
    }
}
