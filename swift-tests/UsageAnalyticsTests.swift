import XCTest
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
}
