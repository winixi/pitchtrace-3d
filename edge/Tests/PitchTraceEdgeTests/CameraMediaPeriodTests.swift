import Foundation
import Testing
@testable import PitchTraceEdge

struct CameraMediaPeriodTests {
    @Test(arguments: ["混合布局", "空周期目录", "非法周期名", "周期是普通文件"])
    func rejectsInvalidCameraLayouts(layout: String) throws {
        let files = FileManager.default
        let camera = files.temporaryDirectory.appending(path: UUID().uuidString)
        let epochs = camera.appending(path: "epochs")
        try files.createDirectory(at: epochs, withIntermediateDirectories: true)
        defer { try? files.removeItem(at: camera) }
        switch layout {
        case "混合布局":
            try files.createDirectory(at: epochs.appending(path: "first"), withIntermediateDirectories: false)
            try Data().write(to: camera.appending(path: "init.mp4"))
        case "非法周期名":
            try files.createDirectory(at: epochs.appending(path: "bad.id"), withIntermediateDirectories: false)
        case "周期是普通文件":
            try Data().write(to: epochs.appending(path: "first"))
        default:
            break
        }
        #expect(throws: RecordingError.self) { try cameraMediaPeriods(in: camera) }
    }
}
