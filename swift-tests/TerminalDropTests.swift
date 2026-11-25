import AppKit
import SwiftTerm
import XCTest
@testable import ClaudeCCHPManager

final class TerminalDropTests: XCTestCase {
    @MainActor
    func testMovingAdjacentTabsBothWaysPreservesSelectionAndAttachmentTarget() {
        let model = ManagerModel()
        let workspace = NSTemporaryDirectory()
        func session(_ name: String) -> TerminalSession {
            TerminalSession(client: .codex, profile: nil, model: name, workspace: workspace,
                sessionMode: .new, gateway: "",
                launchSpec: TerminalLaunchSpec(executable: "/bin/zsh", arguments: [], workspace: workspace, environment: []))
        }
        let first = session("a")
        let second = session("b")
        let third = session("c")
        model.terminalSessions = [first, second, third]
        let firstView = FileDropTerminalView(frame: .zero, options: TerminalOptions())
        let secondView = FileDropTerminalView(frame: .zero, options: TerminalOptions())
        first.control.attach(firstView)
        second.control.attach(secondView)
        model.selectTerminal(second.id)
        let process = secondView.process

        model.moveTerminal(first.id, to: 1)
        XCTAssertEqual(model.terminalSessions.map(\.id), [second.id, first.id, third.id])
        model.moveTerminal(first.id, to: 0)
        XCTAssertEqual(model.terminalSessions.map(\.id), [first.id, second.id, third.id])
        model.moveTerminal(first.id, to: 2)
        XCTAssertEqual(model.terminalSessions.map(\.id), [second.id, third.id, first.id])
        XCTAssertEqual(model.selectedTerminalID, second.id)
        XCTAssertTrue(model.selectedTerminal === second)
        XCTAssertTrue(secondView.process === process)
        XCTAssertTrue(secondView.registeredDraggedTypes.contains(.fileURL))
        XCTAssertFalse(firstView.registeredDraggedTypes.contains(.fileURL))
        model.moveTerminal(first.id, relativeTo: second.id, after: false)
        XCTAssertEqual(model.terminalSessions.map(\.id), [first.id, second.id, third.id])
        model.moveTerminal(first.id, relativeTo: third.id, after: true)
        XCTAssertEqual(model.terminalSessions.map(\.id), [second.id, third.id, first.id])
        XCTAssertEqual(model.selectedTerminalID, second.id)
        model.moveTerminal(UUID(), to: 0)
        XCTAssertEqual(model.terminalSessions.map(\.id), [second.id, third.id, first.id])
    }

    @MainActor
    func testScreenshotTIFFIsSavedAsPrivatePNGAndInactiveTabRejectsIt() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let pasteboard = NSPasteboard.withUniqueName()
        defer { pasteboard.releaseGlobally() }
        let bitmap = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 2, pixelsHigh: 3,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
        pasteboard.setData(try XCTUnwrap(bitmap.tiffRepresentation), forType: .tiff)
        let view = FileDropTerminalView(frame: CGRect(x: 0, y: 0, width: 640, height: 400), options: TerminalOptions())
        view.attachmentDirectory = directory
        view.setSelected(false)
        XCTAssertFalse(view.performFileDrop(from: pasteboard))
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
        view.setSelected(true)
        XCTAssertTrue(view.performFileDrop(from: pasteboard))
        let file = try XCTUnwrap(FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil).first)
        XCTAssertEqual(file.pathExtension, "png")
        let data = try Data(contentsOf: file)
        XCTAssertEqual(Array(data.prefix(8)), [137, 80, 78, 71, 13, 10, 26, 10])
        let decoded = try XCTUnwrap(NSBitmapImageRep(data: data))
        XCTAssertEqual(decoded.pixelsWide, 2)
        XCTAssertEqual(decoded.pixelsHigh, 3)
        let permissions = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(permissions?.intValue, 0o600)
        view.setSelected(false)
        XCTAssertFalse(view.performFileDrop(from: pasteboard))
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path).count, 1)
    }

    @MainActor
    func testTextPasteIsNotConsumedAsAnAttachment() throws {
        let pasteboard = NSPasteboard.withUniqueName()
        defer { pasteboard.releaseGlobally() }
        pasteboard.setString("普通文本", forType: .string)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        XCTAssertNil(try ClipboardAttachment.saveImage(from: pasteboard, in: directory))
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
    }

    @MainActor
    func testRestoringAnOpenConversationImmediatelyRoutesDropsToItsTab() {
        let frame = CGRect(x: 0, y: 0, width: 640, height: 400)
        let stack = NSView(frame: frame)
        let model = ManagerModel()
        let workspace = NSTemporaryDirectory()
        let history = LocalHistoryEntry(sessionID: UUID().uuidString, history_ref: "first-conversation",
                                        client: "codex", title: "First conversation", workspace: workspace,
                                        model: "test", configured_model: nil, profile_id: "global",
                                        profile_name: "System", gateway: "", updated_at: 0, scope: "global", preview: nil, archived: true)
        XCTAssertTrue(history.isArchived, "An archived conversation remains directly restorable.")
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
