import XCTest
import AppKit
import SwiftUI
@testable import ClaudeCCHPManager

final class UsageAnalyticsTests: XCTestCase {
    func testProjectsUseFullPathsAndExplicitDescendants() {
        XCTAssertTrue(projectMatches("/a/project", "/a/project/"))
        XCTAssertFalse(projectMatches("/b/project", "/a/project"))
        XCTAssertFalse(projectMatches("/a/project/sub", "/a/project"))
        XCTAssertTrue(projectMatches("/a/project/sub", "/a/project", true))
        XCTAssertFalse(projectMatches("/a/project-other", "/a/project", true))
        XCTAssertTrue(projectMatches("", "__unknown"))
        XCTAssertFalse(projectMatches("", "/", true))
    }

    func testUsageDecodesUnknownCostsAndActualModelPrices() throws {
        let data = Data(#"{"date":"2026-11-22","model":"actual","input_tokens":100,"output_tokens":10,"estimated_cost":null,"unpriced_records":1,"unit_prices":[{"model":"actual","currency":"USD","unit":"per_M_tokens","charges":{"prompt":2}}]}"#.utf8)
        let point = try JSONDecoder().decode(UsagePoint.self, from: data)
        XCTAssertNil(point.estimated_cost)
        XCTAssertEqual(point.model, "actual")
        XCTAssertEqual(point.unit_prices?.first?.charges["prompt"], 2)
    }

    @MainActor
    private func chartPixels(input: Int, output: Int, series: UsageTokenSeries) throws -> (blue: [Int], orange: [Int]) {
        let point = UsagePoint(date: "2026-11-24", input_tokens: input, output_tokens: output,
            cached_input_tokens: nil, cache_creation_input_tokens: nil, reasoning_output_tokens: nil,
            estimated_cost: nil, unpriced_records: 1)
        let content = UsageChart(points: [point], money: false, series: series)
            .frame(width: 480, height: 240).padding(12).background(Color.white).environment(\.colorScheme, .light)
        let renderer = ImageRenderer(content: content)
        renderer.scale = 1
        let bitmap = NSBitmapImageRep(cgImage: try XCTUnwrap(renderer.cgImage))
        if let directory = ProcessInfo.processInfo.environment["WORKBENCH_CHART_TEST_IMAGES"] {
            let folder = URL(fileURLWithPath: directory)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try bitmap.representation(using: .png, properties: [:])?.write(to: folder.appendingPathComponent("\(series.rawValue)-\(input)-\(output).png"))
        }
        var blue: [Int] = [], orange: [Int] = []
        // Inspect the plot, excluding the axis labels and legend below it.
        for x in 0..<bitmap.pixelsWide {
            for y in 0..<(bitmap.pixelsHigh - 60) {
                guard let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.sRGB) else { continue }
                if color.blueComponent > color.redComponent + 0.2 && color.greenComponent > color.redComponent + 0.05 { blue.append(x) }
                if color.redComponent > color.blueComponent + 0.3 && color.greenComponent > color.blueComponent + 0.1 { orange.append(x) }
            }
        }
        return (blue, orange)
    }

    @MainActor
    func testInputAndOutputRenderInSeparateColumns() throws {
        let pixels = try chartPixels(input: 100, output: 80, series: .both)
        let blueMin = try XCTUnwrap(pixels.blue.min()), blueMax = try XCTUnwrap(pixels.blue.max())
        let orangeMin = try XCTUnwrap(pixels.orange.min()), orangeMax = try XCTUnwrap(pixels.orange.max())
        XCTAssertTrue(blueMax < orangeMin || orangeMax < blueMin, "Input and output must occupy separate columns")
    }

    @MainActor
    func testOutputOnlyRescalesSmallOutputsWithoutDrawingInput() throws {
        let both = try chartPixels(input: 1_000_000, output: 1_000, series: .both)
        let output = try chartPixels(input: 1_000_000, output: 1_000, series: .output)
        XCTAssertTrue(output.blue.isEmpty)
        XCTAssertGreaterThan(output.orange.count, max(500, both.orange.count * 10))
    }
}
