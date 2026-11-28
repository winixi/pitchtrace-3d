// swift-tools-version: 6.0
import Foundation
import PackageDescription

// 海康 MVS SDK 只在场边机安装。检测到 framework 时编入 MVS 取图层并弱链接，
// 未安装 SDK 的机器仍可构建和启动，`"type": "mvs"` 在运行时报错。
// 设置 PITCHTRACE_MVS_SDK=off 可强制不链接，或指向 framework 所在目录。
let mvsSetting = ProcessInfo.processInfo.environment["PITCHTRACE_MVS_SDK"]
let mvsFrameworks = mvsSetting.flatMap { $0 == "off" ? nil : $0 } ?? "/Library/Frameworks"
let mvsAvailable = mvsSetting != "off" &&
    FileManager.default.fileExists(atPath: mvsFrameworks + "/MvCameraControl.framework/Headers/MvCameraControl.h")

let package = Package(
    name: "PitchTraceEdge",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "pitchtrace-edge", targets: ["PitchTraceEdge"])
    ],
    dependencies: [
        .package(url: "https://github.com/httpswift/swifter.git", from: "1.5.0")
    ],
    targets: [
        .executableTarget(
            name: "PitchTraceEdge",
            dependencies: [
                .product(name: "Swifter", package: "swifter")
            ],
            resources: [.copy("Geometry"), .copy("WebUI/Resources")],
            swiftSettings: mvsAvailable ? [.define("PITCHTRACE_MVS"), .unsafeFlags(["-F", mvsFrameworks])] : [],
            linkerSettings: mvsAvailable ? [.unsafeFlags([
                "-F", mvsFrameworks, "-Xlinker", "-weak_framework", "-Xlinker", "MvCameraControl",
                "-Xlinker", "-rpath", "-Xlinker", mvsFrameworks,
            ])] : []
        ),
        // 测试导入主模块时也需找到 MvCameraControl 模块。
        .testTarget(name: "PitchTraceEdgeTests", dependencies: ["PitchTraceEdge"],
                    swiftSettings: mvsAvailable ? [.unsafeFlags(["-F", mvsFrameworks])] : [])
    ]
)
