import Foundation
import XCTest
@testable import AgentWorkbench

final class ReasoningEffortTests: XCTestCase {
    func testNativeCommandsChangeOnlyEffortAndNeverSubmitOrClearDraft() {
        for level in ReasoningEffort.levels.dropFirst() {
            XCTAssertEqual(ReasoningEffort.command(client: .codex, level: level), "/model --reasoning=\(level)")
            XCTAssertEqual(ReasoningEffort.command(client: .claude, level: level), "/effort \(level)")
            XCTAssertFalse(ReasoningEffort.command(client: .codex, level: level)!.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) })
        }
        XCTAssertNil(ReasoningEffort.command(client: .codex, level: "high\n/model other"))
        XCTAssertEqual(ReasoningEffort.command(client: .claude, level: "default"), "/effort")
    }

    @MainActor
    func testStartupPreferencesAreScopedAndPersistWithoutChangingModel() {
        let name = "ReasoningTests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let model = ManagerModel(modelDefaults: defaults)
        model.activeClient = .codex; model.selectedProfileID = "p-one"; model.selectedModel = "glm-5.3"
        XCTAssertEqual(model.selectedReasoningEffort, "default")
        model.selectedReasoningEffort = "high"
        XCTAssertEqual(model.selectedModel, "glm-5.3")
        model.activeClient = .claude
        XCTAssertEqual(model.selectedReasoningEffort, "default")
        model.activeClient = .codex; model.selectedModel = "other"
        XCTAssertEqual(model.selectedReasoningEffort, "default")
        let restored = ManagerModel(modelDefaults: defaults)
        restored.activeClient = .codex; restored.selectedProfileID = "p-one"; restored.selectedModel = "glm-5.3"
        XCTAssertEqual(restored.selectedReasoningEffort, "high")
        restored.selectedProfileID = "p-two"
        XCTAssertEqual(restored.selectedReasoningEffort, "default")
    }
}
