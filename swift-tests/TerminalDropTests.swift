import AppKit
import SwiftTerm
import XCTest
@testable import ClaudeCCHPManager

final class TerminalDropTests: XCTestCase {
    @MainActor
    func testRestoringAnOpenConversationImmediatelyRoutesDropsToItsTab() {
        let frame = CGRect(x: 0, y: 0, width: 640, height: 400)
        let stack = NSView(frame: frame)
        let model = ManagerModel()
        let workspace = NSTemporaryDirectory()
        let history = LocalHistoryEntry(sessionID: UUID().uuidString, history_ref: "first-conversation",
                                        client: "codex", title: "First conversation", workspace: workspace,
                                        model: "test", configured_model: nil, profile_id: "global",
                                        profile_name: "System", gateway: "", updated_at: 0, scope: "global", preview: nil)
        func session(_ historyRef: String?) -> TerminalSession {
            TerminalSession(client: .codex, profile: nil, model: "test", workspace: workspace,
                            sessionMode: .new, gateway: "",
                            launchSpec: TerminalLaunchSpec(executable: "/bin/zsh", arguments: [],
                                                           workspace: workspace, environment: []),
                            isGlobal: true, sourceHistoryRef: historyRef)
        }
        let firstSession = session(history.history_ref)
        let secondSession = session(nil)
        model.terminalSessions = [firstSession, secondSession]
        let first = FileDropTerminalView(frame: frame, options: TerminalOptions())
        let second = FileDropTerminalView(frame: frame, options: TerminalOptions())
        stack.addSubview(first)
        stack.addSubview(second, positioned: .above, relativeTo: first)
        firstSession.control.attach(first)
        model.selectTerminal(secondSession.id)
        secondSession.control.attach(second)
        let point = NSPoint(x: 100, y: 100)
        let firstProcess = first.process
        let pasteboard = NSPasteboard.withUniqueName()
        defer { pasteboard.releaseGlobally() }
        XCTAssertTrue(pasteboard.writeObjects([URL(fileURLWithPath: "/tmp/example file.png") as NSURL]))

        XCTAssertTrue(stack.hitTest(point) === second)
        XCTAssertTrue(second.registeredDraggedTypes.contains(.fileURL))
        XCTAssertFalse(first.registeredDraggedTypes.contains(.fileURL))

        model.historyScope = "global"
        model.chooseHistory(history)
        XCTAssertEqual(model.selectedTerminalID, firstSession.id)
        XCTAssertEqual(model.terminalSessions.count, 2)
        XCTAssertTrue(stack.hitTest(point) === first)
        XCTAssertTrue(first.registeredDraggedTypes.contains(.fileURL))
        XCTAssertFalse(second.registeredDraggedTypes.contains(.fileURL))
        XCTAssertFalse(second.performFileDrop(from: pasteboard), "A delayed drop to the old target must be rejected.")
        XCTAssertTrue(first.performFileDrop(from: pasteboard))
        XCTAssertTrue(first.process === firstProcess)
    }
}
