import XCTest
@testable import AgentWorkbench

final class AgentPermissionTests: XCTestCase {
    func testLocalTerminalKeepsProxyButDoesNotCopyOtherClientCredentials() {
        let environment = agentNetworkEnvironment([
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "NO_PROXY": "build.test",
            "no_proxy": "build.test,.internal.test",
            "OPENAI_API_KEY": "must-not-forward",
            "ANTHROPIC_API_KEY": "must-not-forward",
        ])
        XCTAssertTrue(environment.contains("HTTPS_PROXY=http://127.0.0.1:7890"))
        XCTAssertTrue(environment.contains("NO_PROXY=127.0.0.1,localhost,::1,build.test,.internal.test"))
        XCTAssertTrue(environment.contains("no_proxy=127.0.0.1,localhost,::1,build.test,.internal.test"))
        XCTAssertFalse(environment.contains { $0.contains("must-not-forward") })
    }

    func testTwoClientsKeepIndependentPermissionSettings() throws {
        let selected = AgentPermissions(codex: .standard, claude: .full)
        let copy = try JSONDecoder().decode(AgentPermissions.self, from: JSONEncoder().encode(selected))
        XCTAssertEqual(copy.mode(for: .codex), .standard)
        XCTAssertEqual(copy.mode(for: .claude), .full)
    }
}
