// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PitchTraceEdge",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "pitchtrace-edge", targets: ["PitchTraceEdge"])
    ],
    targets: [
        .executableTarget(name: "PitchTraceEdge", resources: [.copy("Geometry")]),
        .testTarget(name: "PitchTraceEdgeTests", dependencies: ["PitchTraceEdge"])
    ]
)
