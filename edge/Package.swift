// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PitchTraceEdge",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "pitchtrace-edge", targets: ["PitchTraceEdge"])
    ],
    dependencies: [
        .package(url: "https://github.com/vapor/vapor.git", from: "4.99.0")
    ],
    targets: [
        .executableTarget(
            name: "PitchTraceEdge",
            dependencies: [
                .product(name: "Vapor", package: "vapor")
            ],
            resources: [.copy("Geometry"), .copy("WebUI/Resources")]
        ),
        .testTarget(name: "PitchTraceEdgeTests", dependencies: ["PitchTraceEdge"])
    ]
)
