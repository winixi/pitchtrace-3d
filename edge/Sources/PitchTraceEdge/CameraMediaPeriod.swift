import Foundation

struct CameraMediaPeriod {
    let id: String?
    let directory: URL
}

func cameraMediaPeriods(in camera: URL) throws -> [CameraMediaPeriod] {
    let files = FileManager.default
    let cameraID = camera.lastPathComponent
    let epochs = camera.appending(path: "epochs")
    var isDirectory: ObjCBool = false
    guard files.fileExists(atPath: epochs.path, isDirectory: &isDirectory) else {
        return [CameraMediaPeriod(id: nil, directory: camera)]
    }
    guard isDirectory.boolValue else {
        throw RecordingError("机位 \(cameraID)：epochs 应为目录")
    }
    guard !files.fileExists(atPath: camera.appending(path: "init.mp4").path),
          !files.fileExists(atPath: camera.appending(path: "segments").path) else {
        throw RecordingError("机位 \(cameraID)：平铺媒体与 epochs 目录不能混用")
    }

    let directories = try files.contentsOfDirectory(
        at: epochs, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles]
    ).sorted { $0.lastPathComponent < $1.lastPathComponent }
    guard !directories.isEmpty else {
        throw RecordingError("机位 \(cameraID)：epochs 目录为空")
    }
    return try directories.map { directory in
        let id = directory.lastPathComponent
        guard isMediaCameraID(id), try directory.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true else {
            throw RecordingError("机位 \(cameraID)：媒体周期需要有效的 v2 目录名，\(id)")
        }
        return CameraMediaPeriod(id: id, directory: directory)
    }
}
