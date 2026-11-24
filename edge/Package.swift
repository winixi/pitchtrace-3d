// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PitchTraceEdge",
    products: [
        .executable(name: "pitchtrace-edge", targets: ["PitchTraceEdge"])
    ],
    targets: [
        .executableTarget(name: "PitchTraceEdge")
    ]
)
