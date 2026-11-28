import Foundation
#if PITCHTRACE_MVS
import MvCameraControl
#endif

/// `trigger-scheduler` 的参数。各机位须使用相同的 key／mask，并配置 `mvs.trigger.source: "action"`。
struct TriggerSchedulerOptions: Equatable {
    var framerate: Int
    var taiUtcOffsetSeconds = 37
    var tickHz: Int64 = 1_000_000_000
    var deviceKey: UInt32 = 1
    var groupKey: UInt32 = 1
    var groupMask: UInt32 = 1
    var broadcast = "255.255.255.255"
    /// 指定发送网卡的 IPv4 地址，省略时由系统选择。
    var interfaceIP: String?
    /// 命令提前于曝光时刻发出的时间（毫秒）。在途命令数约为 leadMs × 帧率 / 1000，不得超过相机动作队列。
    var leadMs = 500

    static func parse(_ arguments: [String]) throws -> TriggerSchedulerOptions {
        guard let first = arguments.first, let framerate = Int(first) else {
            throw RecordingError("trigger-scheduler 需要整数帧率作为第一个参数")
        }
        var options = TriggerSchedulerOptions(framerate: framerate)
        var index = 1
        while index < arguments.count {
            let flag = arguments[index]
            guard index + 1 < arguments.count else { throw RecordingError("\(flag) 缺少参数值") }
            let value = arguments[index + 1]
            func number<T: FixedWidthInteger>(_ type: T.Type) throws -> T {
                guard let parsed = T(value) else { throw RecordingError("\(flag) 的值 \(value) 无效") }
                return parsed
            }
            switch flag {
            case "--tai-offset": options.taiUtcOffsetSeconds = try number(Int.self)
            case "--tick-hz": options.tickHz = try number(Int64.self)
            case "--device-key": options.deviceKey = try number(UInt32.self)
            case "--group-key": options.groupKey = try number(UInt32.self)
            case "--group-mask": options.groupMask = try number(UInt32.self)
            case "--lead-ms": options.leadMs = try number(Int.self)
            case "--broadcast": options.broadcast = value
            case "--interface-ip": options.interfaceIP = value
            default: throw RecordingError("trigger-scheduler 不认识参数 \(flag)")
            }
            index += 2
        }
        guard (100...5000).contains(options.leadMs) else { throw RecordingError("--lead-ms 须在 100–5000") }
        _ = try options.schedule()
        if let address = options.interfaceIP { _ = try ipv4Value(address) }
        return options
    }

    func schedule() throws -> ActionSchedule {
        try ActionSchedule(framerate: framerate, taiUtcOffsetSeconds: taiUtcOffsetSeconds, tickHz: tickHz)
    }
}

/// 点分十进制 IPv4 转 SDK 使用的 32 位值（最高字节为第一段）。
func ipv4Value(_ text: String) throws -> UInt32 {
    let parts = text.split(separator: ".", omittingEmptySubsequences: false).map { UInt8($0) }
    guard parts.count == 4, !parts.contains(where: { $0 == nil }) else { throw RecordingError("IPv4 地址无效：\(text)") }
    return parts.reduce(UInt32(0)) { $0 << 8 | UInt32($1!) }
}

#if PITCHTRACE_MVS
import Darwin

/// 按整秒对齐的时间表持续广播定时动作命令，直到收到停止信号。
/// 相机按自身 PTP 时钟在指定时刻曝光；PTP 未同步的相机会在应答中返回 0x8013。
func runTriggerScheduler(_ options: TriggerSchedulerOptions, stop: StopFlag) throws {
    guard dlsym(UnsafeMutableRawPointer(bitPattern: -2), "MV_GIGE_IssueActionCommand") != nil else {
        throw RecordingError("本机未安装海康 MVS SDK（/Library/Frameworks/MvCameraControl.framework）")
    }
    let schedule = try options.schedule()
    let lead = Int64(options.leadMs) * 1_000_000
    func nowNanos() -> Int64 { Int64(Date().timeIntervalSince1970 * 1e9) }
    var next = schedule.firstIndex(after: nowNanos() + 100_000_000)
    var sent = 0, late = 0, failedAcks = 0, consecutiveFailures = 0
    let interfaceIP = try options.interfaceIP.map(ipv4Value)
    FileHandle.standardError.write(Data("trigger-scheduler：\(options.framerate) fps，key=\(options.deviceKey) group=\(options.groupKey) mask=\(options.groupMask)，提前 \(options.leadMs) ms\n".utf8))
    try options.broadcast.withCString { broadcast in
        while !stop.isSet {
            let now = nowNanos()
            // 落后于当前时刻太近的曝光已来不及发送，跳到下一帧。
            if schedule.utcNanos(index: next) < now + 20_000_000 {
                let target = schedule.firstIndex(after: now + 50_000_000)
                late += Int(target - next)
                next = target
            }
            while schedule.utcNanos(index: next) <= now + lead, !stop.isSet {
                // 每 framerate 条命令取一次应答，用于发现未同步、队列溢出或时间已过。
                let wantsAck = sent % options.framerate == 0
                var info = MV_ACTION_CMD_INFO()
                info.nDeviceKey = options.deviceKey
                info.nGroupKey = options.groupKey
                info.nGroupMask = options.groupMask
                info.bActionTimeEnable = 1
                info.nActionTime = schedule.deviceTicks(index: next)
                info.pBroadcastAddress = broadcast
                info.nTimeOut = wantsAck ? 200 : 0
                if let interfaceIP {
                    info.bSpecialNetEnable = 1
                    info.nSpecialNetIP = interfaceIP
                }
                var results = MV_ACTION_CMD_RESULT_LIST()
                let code = MV_GIGE_IssueActionCommand(&info, &results)
                if code != Int32(MV_OK) && !(wantsAck && code == Int32(bitPattern: UInt32(MV_E_NODATA))) {
                    throw RecordingError(String(format: "发送动作命令失败，错误码 0x%08X", UInt32(bitPattern: code)))
                }
                if wantsAck {
                    var bad: [String] = []
                    for i in 0..<Int(results.nNumResults) {
                        let result = results.pResults[i]
                        if result.nStatus != 0 {
                            bad.append(String(format: "状态 0x%04X", result.nStatus))
                        }
                    }
                    if results.nNumResults == 0 { bad.append("无相机应答") }
                    if bad.isEmpty {
                        consecutiveFailures = 0
                    } else {
                        failedAcks += 1
                        consecutiveFailures += 1
                        FileHandle.standardError.write(Data("动作命令应答异常：\(bad.joined(separator: "、"))（0x8013 未同步 PTP，0x8015 队列溢出，0x8016 时间已过）\n".utf8))
                        if consecutiveFailures >= 5 { throw RecordingError("动作命令连续 5 次应答异常，已停止") }
                    }
                }
                sent += 1
                next += 1
            }
            Thread.sleep(forTimeInterval: 0.005)
        }
    }
    FileHandle.standardError.write(Data("trigger-scheduler 已停止：发送 \(sent) 条，来不及发送 \(late) 帧，异常应答 \(failedAcks) 次\n".utf8))
}
#else
func runTriggerScheduler(_ options: TriggerSchedulerOptions, stop: StopFlag) throws {
    throw RecordingError("此 Edge 构建未包含海康 MVS SDK。安装 MvCameraControl.framework 后按 edge/capture.md 重新构建")
}
#endif
