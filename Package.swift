// swift-tools-version: 6.2

import PackageDescription

let package = Package(
    name: "AgentWorkbench",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "AgentWorkbench", targets: ["AgentWorkbench"])
    ],
    dependencies: [],
    targets: [
        .target(
            name: "SwiftTerm",
            path: "Vendor/SwiftTerm/Sources/SwiftTerm",
            exclude: ["Apple/Metal/Shaders.metal", "Mac/README.md"],
            swiftSettings: [.define("SWIFTTERM_BUILD_LOCAL")]
        ),
        .executableTarget(
            name: "AgentWorkbench",
            dependencies: ["SwiftTerm"],
            path: "Sources/AgentWorkbench"
        ),
        .testTarget(name: "ReleaseFeedTests", dependencies: ["AgentWorkbench"], path: "swift-tests")
    ]
)
