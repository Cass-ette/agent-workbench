// swift-tools-version: 6.2

import PackageDescription

let package = Package(
    name: "ClaudeCCHPManager",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "ClaudeCCHPManager", targets: ["ClaudeCCHPManager"])
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
            name: "ClaudeCCHPManager",
            dependencies: ["SwiftTerm"],
            path: "Sources/ClaudeCCHPManager"
        )
    ]
)
