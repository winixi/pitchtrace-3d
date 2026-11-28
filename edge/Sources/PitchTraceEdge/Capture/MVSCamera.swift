import Foundation

/// 海康机器人 MVS 机型的采集参数。省略 offsetX／offsetY 时 ROI 居中，其余省略项保留相机当前设置。
struct MVSSettings: Decodable, Sendable {
    var offsetX: Int?
    var offsetY: Int?
    var exposureUs: Double?
    var gainDb: Double?
    /// GigE 包长（字节）。省略时由 SDK 探测最佳包长。
    var packetSize: Int?
    /// ptp-utc 模式开启 IEEE 1588 后等待锁定的最长秒数。
    var ptpLockTimeoutSeconds: Int?
    /// 曝光触发方式，省略时自由连拍。
    var trigger: MVSTriggerSettings?

    init() {}
}

/// PTP 时间换算的 UTC 与主机收帧时刻相差过大，说明主时钟没有同步到 UTC（例如相机自任主时钟）。
let maximumPTPHostDisagreementUs: Int64 = 5_000_000

func checkPTPAgreesWithHost(captureUtcUs: Int64, hostReceivedUtcUs: Int64) throws {
    let difference = hostReceivedUtcUs - captureUtcUs
    guard abs(difference) <= maximumPTPHostDisagreementUs else {
        throw RecordingError(String(format: "PTP 换算的曝光 UTC 与主机时间相差 %.1f 秒，主时钟可能未同步到 UTC，请改用已授时的主时钟或 host-fit",
                                    Double(difference) / 1e6))
    }
}

/// PTP 端口状态中可作为共同时基的角色。
func ptpRoleIsLocked(_ symbolic: String) -> Bool {
    ["Slave", "Master"].contains(symbolic)
}

#if PITCHTRACE_MVS
import Darwin
import MvCameraControl

private func mvsCode(_ value: Int32) -> String { String(format: "0x%08X", UInt32(bitPattern: value)) }

private func mvsCheck(_ value: Int32, _ action: String) throws {
    guard value == Int32(MV_OK) else { throw RecordingError("MVS \(action)失败，错误码 \(mvsCode(value))") }
}

/// 读取定长 C 字符数组。
private func cText<T>(_ tuple: T) -> String {
    withUnsafeBytes(of: tuple) { raw in
        String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self).trimmingCharacters(in: .whitespaces)
    }
}

/// SDK 回调线程置位的断线标志。
private final class DisconnectFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    var isSet: Bool { lock.lock(); defer { lock.unlock() }; return value }
    func set() { lock.lock(); value = true; lock.unlock() }
}

final class MVSCamera: CameraFrameSource {
    private(set) var tickHz: UInt64 = 0
    private(set) var sourceDescription: String
    private let serial: String
    private let width: Int
    private let height: Int
    private let pixelFormat: CameraPixelFormat
    private let framerate: Double
    private let settings: MVSSettings
    private let requirePTP: Bool
    private var handle: UnsafeMutableRawPointer?
    private var initialized = false
    private var grabbing = false
    private var isGigE = false
    private var configuredExposureUs = 0.0
    private let disconnected = DisconnectFlag()

    init(serial: String, width: Int, height: Int, pixelFormat: CameraPixelFormat, framerate: Double,
         settings: MVSSettings, requirePTP: Bool) {
        self.serial = serial
        self.width = width
        self.height = height
        self.pixelFormat = pixelFormat
        self.framerate = framerate
        self.settings = settings
        self.requirePTP = requirePTP
        sourceDescription = "MVS 相机 \(serial)"
    }

    deinit { stop() }

    func start() throws {
        try mvsCheck(MV_CC_Initialize(), "初始化 SDK ")
        initialized = true
        let device = try findDevice()
        try mvsCheck(MV_CC_CreateHandle(&handle, device), "创建句柄")
        try mvsCheck(MV_CC_OpenDevice(handle, UInt32(MV_ACCESS_Exclusive), 0), "独占打开相机 \(serial) ")
        MV_CC_RegisterExceptionCallBack(handle, { message, user in
            if message == UInt32(MV_EXCEPTION_DEV_DISCONNECT), let user {
                Unmanaged<DisconnectFlag>.fromOpaque(user).takeUnretainedValue().set()
            }
        }, Unmanaged.passUnretained(disconnected).toOpaque())
        try configure()
        try mvsCheck(MV_CC_SetImageNodeNum(handle, 8), "设置取图缓存数")
        try mvsCheck(MV_CC_StartGrabbing(handle), "开始取图")
        grabbing = true
    }

    private func findDevice() throws -> UnsafeMutablePointer<MV_CC_DEVICE_INFO> {
        var list = MV_CC_DEVICE_INFO_LIST()
        try mvsCheck(MV_CC_EnumDevices(UInt32(MV_GIGE_DEVICE | MV_USB_DEVICE), &list), "枚举设备")
        var seen: [String] = []
        let count = Int(min(list.nDeviceNum, UInt32(MV_MAX_DEVICE_NUM)))
        let devices = withUnsafeBytes(of: &list.pDeviceInfo) { raw in
            Array(raw.bindMemory(to: UnsafeMutablePointer<MV_CC_DEVICE_INFO>?.self).prefix(count))
        }
        for case let info? in devices {
            let gige = info.pointee.nTLayerType == UInt32(MV_GIGE_DEVICE)
            let (found, model) = gige
                ? (cText(info.pointee.SpecialInfo.stGigEInfo.chSerialNumber), cText(info.pointee.SpecialInfo.stGigEInfo.chModelName))
                : (cText(info.pointee.SpecialInfo.stUsb3VInfo.chSerialNumber), cText(info.pointee.SpecialInfo.stUsb3VInfo.chModelName))
            seen.append(found)
            if found == serial {
                isGigE = gige
                sourceDescription = "MVS \(model) \(serial)（\(gige ? "GigE" : "USB3")）"
                return info
            }
        }
        let others = seen.isEmpty ? "未发现任何 MVS 设备" : "已发现：\(seen.joined(separator: "、"))"
        throw RecordingError("未找到序列号为 \(serial) 的相机，\(others)")
    }

    private func integer(_ key: String) throws -> MVCC_INTVALUE_EX {
        var value = MVCC_INTVALUE_EX()
        try mvsCheck(MV_CC_GetIntValueEx(handle, key, &value), "读取 \(key) ")
        return value
    }

    private func setInteger(_ key: String, _ value: Int) throws {
        try mvsCheck(MV_CC_SetIntValueEx(handle, key, Int64(value)), "设置 \(key)=\(value) ")
    }

    private func setFloat(_ key: String, _ value: Double) throws {
        try mvsCheck(MV_CC_SetFloatValue(handle, key, Float(value)), "设置 \(key)=\(value) ")
    }

    private func float(_ key: String) throws -> Double {
        var value = MVCC_FLOATVALUE()
        try mvsCheck(MV_CC_GetFloatValue(handle, key, &value), "读取 \(key) ")
        return Double(value.fCurValue)
    }

    private func setEnum(_ key: String, _ symbolic: String) throws {
        try mvsCheck(MV_CC_SetEnumValueByString(handle, key, symbolic), "设置 \(key)=\(symbolic) ")
    }

    private func enumSymbolic(_ key: String) throws -> String {
        var value = MVCC_ENUMVALUE()
        try mvsCheck(MV_CC_GetEnumValue(handle, key, &value), "读取 \(key) ")
        var entry = MVCC_ENUMENTRY()
        entry.nValue = value.nCurValue
        try mvsCheck(MV_CC_GetEnumEntrySymbolic(handle, key, &entry), "读取 \(key) 名称")
        return cText(entry.chSymbolic)
    }

    /// 按节点步进把偏移落到合法位置。
    private func offset(_ requested: Int?, size: Int, maximumKey: String, key: String) throws -> Int {
        let maximum = Int(try integer(maximumKey).nCurValue)
        guard size <= maximum else { throw RecordingError("ROI \(size) 超出相机 \(maximumKey)=\(maximum)") }
        let step = max(1, Int(try integer(key).nInc))
        let value = requested ?? (maximum - size) / 2 / step * step
        guard value >= 0, value + size <= maximum, value % step == 0 else {
            throw RecordingError("\(key)=\(value) 无效：ROI 须落在 \(maximum) 内且为 \(step) 的倍数")
        }
        return value
    }

    private var trigger: MVSTriggerSettings { settings.trigger ?? MVSTriggerSettings() }
    private func configureTrigger() throws {
        let trigger = self.trigger
        if trigger.source == .free {
            try mvsCheck(MV_CC_SetEnumValue(handle, "TriggerMode", 0), "关闭触发模式")
            return
        }
        guard isGigE || trigger.source != .action else { throw RecordingError("动作命令触发只适用于 GigE 相机") }
        try setEnum("TriggerSelector", trigger.resolvedSelector)
        try mvsCheck(MV_CC_SetEnumValue(handle, "TriggerMode", 1), "开启触发模式")
        try setEnum("TriggerSource", trigger.triggerSourceNode)
        if trigger.source == .action {
            // 一次触发采一帧；ActionSelector 固定用 1，与 Action1 对应。
            if trigger.resolvedSelector == "FrameBurstStart" { try setInteger("AcquisitionBurstFrameCount", 1) }
            try setInteger("ActionSelector", 1)
            try setInteger("ActionDeviceKey", Int(trigger.actionDeviceKey))
            try setInteger("ActionGroupKey", Int(trigger.actionGroupKey))
            try setInteger("ActionGroupMask", Int(trigger.actionGroupMask))
        } else {
            try setEnum("TriggerActivation", trigger.activation)
        }
        sourceDescription += "，触发：\(trigger.summary)"
    }
    private func configure() throws {
        if isGigE {
            let packet = settings.packetSize ?? Int(MV_CC_GetOptimalPacketSize(handle))
            if packet > 0 { try setInteger("GevSCPSPacketSize", packet) }
        }
        try configureTrigger()
        try setEnum("PixelFormat", pixelFormat.rawValue)
        // 先清零偏移，避免旧偏移与新宽高之和越界。
        try setInteger("OffsetX", 0)
        try setInteger("OffsetY", 0)
        try setInteger("Width", width)
        try setInteger("Height", height)
        try setInteger("OffsetX", try offset(settings.offsetX, size: width, maximumKey: "WidthMax", key: "OffsetX"))
        try setInteger("OffsetY", try offset(settings.offsetY, size: height, maximumKey: "HeightMax", key: "OffsetY"))
        // 触发模式下帧率由触发源决定，相机端帧率控制不能同时启用。
        let triggered = trigger.source != .free
        try mvsCheck(MV_CC_SetBoolValue(handle, "AcquisitionFrameRateEnable", triggered ? 0 : 1), "设置帧率控制")
        if !triggered { try setFloat("AcquisitionFrameRate", framerate) }
        if let exposure = settings.exposureUs {
            try setEnum("ExposureAuto", "Off")
            try setFloat("ExposureTime", exposure)
        }
        if let gain = settings.gainDb {
            try setEnum("GainAuto", "Off")
            try setFloat("Gain", gain)
        }
        configuredExposureUs = try float("ExposureTime")
        // GigE 设备在帧头中给出时间戳，计数频率以设备节点为准；USB3 Vision 规定以纳秒计时。
        tickHz = isGigE ? UInt64(max(0, try integer("GevTimestampTickFrequency").nCurValue)) : 1_000_000_000
        guard tickHz > 0 else { throw RecordingError("相机未报告时间戳频率") }
        if requirePTP { try waitForPTPLock() }
        let resulting = (try? float("ResultingFrameRate")) ?? 0
        sourceDescription += String(format: "，ROI %d×%d+%d+%d，设定 %.2f fps，相机报告 %.2f fps，时间戳 %llu Hz",
                                    width, height, Int(try integer("OffsetX").nCurValue),
                                    Int(try integer("OffsetY").nCurValue), framerate, resulting, tickHz)
    }

    private func ptpStatus() throws -> String {
        // 海康固件沿用 GigE Vision 的 GevIEEE1588Status；新版 SFNC 命名为 PtpStatus。
        if let status = try? enumSymbolic("GevIEEE1588Status") { return status }
        return try enumSymbolic("PtpStatus")
    }

    private func waitForPTPLock() throws {
        if MV_CC_SetBoolValue(handle, "GevIEEE1588", 1) != Int32(MV_OK) {
            try mvsCheck(MV_CC_SetBoolValue(handle, "PtpEnable", 1), "开启 IEEE 1588")
        }
        let deadline = Date().addingTimeInterval(Double(settings.ptpLockTimeoutSeconds ?? 30))
        var status = "未知"
        while Date() < deadline {
            status = (try? ptpStatus()) ?? status
            if ptpRoleIsLocked(status) { return }
            Thread.sleep(forTimeInterval: 0.5)
        }
        throw RecordingError("等待 PTP 锁定超时，当前状态 \(status)")
    }

    func nextFrame(timeoutMs: Int) throws -> CameraFrame? {
        let deadline = Date().addingTimeInterval(Double(timeoutMs) / 1000)
        while true {
            if disconnected.isSet { throw RecordingError("相机 \(serial) 断开连接") }
            let remaining = max(1, Int(deadline.timeIntervalSinceNow * 1000))
            var out = MV_FRAME_OUT()
            let result = MV_CC_GetImageBuffer(handle, &out, UInt32(remaining))
            if result == Int32(bitPattern: UInt32(MV_E_NODATA)) || result == Int32(bitPattern: UInt32(MV_E_GC_TIMEOUT)) {
                return nil
            }
            try mvsCheck(result, "取图")
            let host = Int64(Date().timeIntervalSince1970 * 1e6)
            defer { MV_CC_FreeImageBuffer(handle, &out) }
            let info = out.stFrameInfo
            // 丢包帧的像素不完整，丢弃后由设备帧号缺口计入统计。
            if info.nLostPacket > 0 {
                if Date() >= deadline { return nil }
                continue
            }
            let expected: MvGvspPixelType = pixelFormat == .bayerRG8 ? PixelType_Gvsp_BayerRG8 : PixelType_Gvsp_Mono8
            guard info.enPixelType == expected else { throw RecordingError("相机输出的像素格式与配置不符") }
            let w = info.nExtendWidth > 0 ? Int(info.nExtendWidth) : Int(info.nWidth)
            let h = info.nExtendHeight > 0 ? Int(info.nExtendHeight) : Int(info.nHeight)
            let length = info.nFrameLenEx > 0 ? Int(info.nFrameLenEx) : Int(info.nFrameLen)
            guard let buffer = out.pBufAddr, length >= w * h else { throw RecordingError("相机图像数据长度不足") }
            // 帧水印带曝光时长时逐帧使用；否则使用固定曝光设置。
            let exposure = info.fExposureTime > 0 ? Double(info.fExposureTime) : configuredExposureUs
            return CameraFrame(width: w, height: h, pixelFormat: pixelFormat, pixels: Data(bytes: buffer, count: w * h),
                               frameNumber: UInt64(info.nFrameNum),
                               deviceTicks: UInt64(info.nDevTimeStampHigh) << 32 | UInt64(info.nDevTimeStampLow),
                               exposureUs: exposure, hostReceivedUtcUs: host)
        }
    }

    func clockState() throws -> CameraClockState {
        if disconnected.isSet { throw RecordingError("相机 \(serial) 断开连接") }
        do {
            let status = try ptpStatus()
            return CameraClockState(locked: ptpRoleIsLocked(status), detail: "IEEE 1588 \(status)")
        } catch {
            return CameraClockState(locked: false, detail: "无法读取 PTP 状态：\(error.localizedDescription)")
        }
    }

    func stop() {
        if let handle {
            if grabbing { MV_CC_StopGrabbing(handle) }
            MV_CC_CloseDevice(handle)
            MV_CC_DestroyHandle(handle)
        }
        handle = nil
        grabbing = false
        if initialized { MV_CC_Finalize() }
        initialized = false
    }
}

/// 弱链接的 framework 在未安装 SDK 的机器上不会加载，调用前先确认符号存在。
func makeMVSCamera(serial: String, width: Int, height: Int, pixelFormat: CameraPixelFormat,
                   framerate: Double, settings: MVSSettings, requirePTP: Bool) throws -> CameraFrameSource {
    guard dlsym(UnsafeMutableRawPointer(bitPattern: -2), "MV_CC_Initialize") != nil else {
        throw RecordingError("本机未安装海康 MVS SDK（/Library/Frameworks/MvCameraControl.framework）")
    }
    return MVSCamera(serial: serial, width: width, height: height, pixelFormat: pixelFormat,
                     framerate: framerate, settings: settings, requirePTP: requirePTP)
}
#else
/// 未链接 MVS SDK 的构建只能使用模拟相机和 FFmpeg 输入。
func makeMVSCamera(serial: String, width: Int, height: Int, pixelFormat: CameraPixelFormat,
                   framerate: Double, settings: MVSSettings, requirePTP: Bool) throws -> CameraFrameSource {
    throw RecordingError("此 Edge 构建未包含海康 MVS SDK。安装 MvCameraControl.framework 后按 edge/docs/capture.md 重新构建")
}
#endif
