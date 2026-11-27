import Testing
import Foundation
@testable import PitchTraceEdge

/// Web UI 相关测试
///
/// 阶段 1：基础框架测试
/// - 命令行参数解析
/// - 数据结构序列化
@Suite("Web UI Tests")
struct WebUITests {
    
    @Test("解析默认参数")
    func parseDefaultArguments() throws {
        let command = try WebUICommand.parse(arguments: [])
        
        #expect(command.bindAddress == "127.0.0.1:8765")
        #expect(command.matchDir == nil)
    }
    
    @Test("解析自定义绑定地址")
    func parseCustomBindAddress() throws {
        let command = try WebUICommand.parse(arguments: ["--bind", "0.0.0.0:9000"])
        
        #expect(command.bindAddress == "0.0.0.0:9000")
        #expect(command.matchDir == nil)
    }
    
    @Test("解析比赛目录")
    func parseMatchDirectory() throws {
        let command = try WebUICommand.parse(arguments: ["--match-dir", "/path/to/match"])
        
        #expect(command.bindAddress == "127.0.0.1:8765")
        #expect(command.matchDir == "/path/to/match")
    }
    
    @Test("解析完整参数")
    func parseAllArguments() throws {
        let command = try WebUICommand.parse(arguments: [
            "--bind", "192.168.1.100:8080",
            "--match-dir", "/var/pitchtrace/match-001"
        ])
        
        #expect(command.bindAddress == "192.168.1.100:8080")
        #expect(command.matchDir == "/var/pitchtrace/match-001")
    }
    
    @Test("缺少必需参数时抛出错误")
    func parseMissingRequiredArgument() {
        #expect(throws: ValidationError.self) {
            _ = try WebUICommand.parse(arguments: ["--bind"])
        }
        
        #expect(throws: ValidationError.self) {
            _ = try WebUICommand.parse(arguments: ["--match-dir"])
        }
    }
    
    @Test("未知参数时抛出错误")
    func parseUnknownArgument() {
        #expect(throws: ValidationError.self) {
            _ = try WebUICommand.parse(arguments: ["--unknown"])
        }
    }
    
    @Test("帮助参数抛出 ExitSuccess")
    func parseHelpArgument() {
        #expect(throws: ExitSuccess.self) {
            _ = try WebUICommand.parse(arguments: ["--help"])
        }
        
        #expect(throws: ExitSuccess.self) {
            _ = try WebUICommand.parse(arguments: ["-h"])
        }
    }
    
    @Test("SystemStatus 可以编码为 JSON")
    func encodeSystemStatus() throws {
        let status = SystemStatus(
            timestamp: "2025-01-20T10:00:00Z",
            matchId: "test-match",
            matchState: "capturing",
            cameras: [
                CameraStatus(
                    cameraId: "A",
                    state: "active",
                    fps: 30.0,
                    frameCount: 1000,
                    lastFrameUtcUs: "1737360000000000",
                    exposureDriftMs: 0.5,
                    droppedFrames: 0,
                    queueDepth: 2,
                    calibrationId: "cal-v1",
                    calibrationValid: true
                )
            ],
            alerts: [],
            disk: DiskStatus(
                totalBytes: 1_000_000_000_000,
                usedBytes: 500_000_000_000,
                availableBytes: 500_000_000_000,
                recordingRateBytes: 4_000_000,
                estimatedHoursRemaining: 34.7
            )
        )
        
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
        let data = try encoder.encode(status)
        let json = String(data: data, encoding: .utf8)
        
        #expect(json != nil)
        #expect(json!.contains("\"matchId\" : \"test-match\""))
        #expect(json!.contains("\"cameraId\" : \"A\""))
    }
    
    @Test("CameraStatus 可以编码为 JSON")
    func encodeCameraStatus() throws {
        let camera = CameraStatus(
            cameraId: "B",
            state: "disconnected",
            fps: 0,
            frameCount: 500,
            lastFrameUtcUs: "1737360000000000",
            exposureDriftMs: nil,
            droppedFrames: 10,
            queueDepth: 0,
            calibrationId: "cal-v1",
            calibrationValid: false
        )
        
        let encoder = JSONEncoder()
        let data = try encoder.encode(camera)
        let json = String(data: data, encoding: .utf8)
        
        #expect(json != nil)
        #expect(json!.contains("\"cameraId\":\"B\""))
        #expect(json!.contains("\"state\":\"disconnected\""))
        #expect(json!.contains("\"calibrationValid\":false"))
    }
    
    @Test("Alert 可以编码为 JSON")
    func encodeAlert() throws {
        let alert = Alert(
            level: "error",
            cameraId: "A",
            message: "Camera disconnected",
            timestamp: "2025-01-20T10:00:00Z"
        )
        
        let encoder = JSONEncoder()
        let data = try encoder.encode(alert)
        let json = String(data: data, encoding: .utf8)
        
        #expect(json != nil)
        #expect(json!.contains("\"level\":\"error\""))
        #expect(json!.contains("\"message\":\"Camera disconnected\""))
    }
}
