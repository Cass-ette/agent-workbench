import XCTest
import AppKit
@testable import SwiftTerm
@testable import ClaudeCCHPManager

final class AppearanceTests: XCTestCase {
    @MainActor func testThemePersistsAndCustomTerminalColorsSurviveMigration() throws {
        let suite = "AppearanceTests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let appearance = WorkbenchAppearance(defaults: defaults)
        XCTAssertEqual(appearance.mode, .system)
        appearance.mode = .dark
        XCTAssertEqual(WorkbenchAppearance(defaults: defaults).mode, .dark)

        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let config = folder.appendingPathComponent("terminal.json")
        let legacy = ##"{"fontName":"SFMono-Regular","fontSize":15,"foreground":"#112233","background":"#EEEEEE","cursor":"#556677","scrollback":6000}"##
        try Data(legacy.utf8).write(to: config)
        let style = SharedTerminalStyle(configURL: config)
        XCTAssertFalse(style.settings.followAppearance)
        style.setAppearance(.dark)
        XCTAssertEqual(style.effectiveSettings.background, "#EEEEEE")
        XCTAssertEqual(try String(contentsOf: config, encoding: .utf8), legacy)
        var settings = style.settings; settings.followAppearance = true
        style.update(settings)
        XCTAssertEqual(style.effectiveSettings.background, WorkbenchPalette.dark["base"])
        XCTAssertEqual(style.settings.background, "#EEEEEE")

        let view = FileDropTerminalView(frame: .zero, options: TerminalOptions())
        let control = EmbeddedTerminalControl(); control.attach(view)
        let process = view.process
        control.apply(style, to: view)
        style.setAppearance(.light); control.apply(style, to: view)
        XCTAssertTrue(view.process === process, "Changing color must not recreate the PTY")
        XCTAssertEqual(view.nativeBackgroundColor, NSColor(hex: WorkbenchPalette.light["base"]))
        view.withTerminal { terminal in
            XCTAssertEqual(terminal.paletteColor(index: 1), SwiftTerm.Color.parse(WorkbenchPalette.light.ansi[1]))
        }
    }

    @MainActor func testProjectInsertionCoversGapsAndEdges() {
        let a = UUID(), b = UUID(), c = UUID()
        let frames = [(a, CGRect(x: 0, y: 0, width: 224, height: 40)), (b, CGRect(x: 228, y: 0, width: 224, height: 40)), (c, CGRect(x: 456, y: 0, width: 224, height: 40))]
        let before = ProjectTabDocument.destination(x: -10, moving: c, frames: frames)
        XCTAssertEqual(before?.0, a); XCTAssertEqual(before?.1, false)
        let gap = ProjectTabDocument.destination(x: 454, moving: a, frames: frames)
        XCTAssertEqual(gap?.0, c); XCTAssertEqual(gap?.1, false)
        let end = ProjectTabDocument.destination(x: 999, moving: a, frames: frames)
        XCTAssertEqual(end?.0, c); XCTAssertEqual(end?.1, true)
    }
}
