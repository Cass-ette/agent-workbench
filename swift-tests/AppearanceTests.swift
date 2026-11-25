import XCTest
import AppKit
import SwiftUI
@testable import SwiftTerm
@testable import AgentWorkbench

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
        XCTAssertEqual(style.effectiveSettings(for: .dark).background, "#EEEEEE")
        XCTAssertEqual(try String(contentsOf: config, encoding: .utf8), legacy)
        var settings = style.settings; settings.followAppearance = true
        style.update(settings)
        XCTAssertEqual(style.effectiveSettings(for: .dark).background, WorkbenchPalette.dark["base"])
        XCTAssertEqual(style.settings.background, "#EEEEEE")

        let view = FileDropTerminalView(frame: .zero, options: TerminalOptions())
        let control = EmbeddedTerminalControl(); control.attach(view)
        let process = view.process
        control.apply(style, palette: .dark, to: view)
        control.apply(style, palette: .light, to: view)
        XCTAssertTrue(view.process === process, "Changing color must not recreate the PTY")
        XCTAssertEqual(view.nativeBackgroundColor, NSColor(hex: WorkbenchPalette.light["base"]))
        view.withTerminal { terminal in
            XCTAssertEqual(terminal.paletteColor(index: 1), SwiftTerm.Color.parse(WorkbenchPalette.light.ansi[1]))
            XCTAssertEqual(terminal.colorScheme, .light)
        }
    }

    @MainActor func testFirstCLIColorProbeSeesHostPaletteBeforeLaunch() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        for (name, palette) in [("dark", WorkbenchPalette.dark), ("light", WorkbenchPalette.light)] {
            let result = folder.appendingPathComponent(name + ".txt")
            let script = """
            import os,select,sys,time,tty
            tty.setraw(0)
            os.write(1,b'\\x1b]10;?\\x07\\x1b]11;?\\x07\\x1b[?996n')
            data=b''
            end=time.monotonic()+3
            while time.monotonic()<end and b'997;' not in data:
                if select.select([0],[],[],0.1)[0]: data+=os.read(0,4096)
            with open(sys.argv[1],'wb') as f: f.write(data)
            """
            let style = SharedTerminalStyle(configURL: folder.appendingPathComponent("terminal.json"))
            let control = EmbeddedTerminalControl()
            let launch = TerminalLaunchSpec(executable: "/usr/bin/python3", arguments: ["-u", "-c", script, result.path], workspace: folder.path, environment: ["TERM=xterm-256color"])
            let content = EmbeddedAgentTerminal(control: control, style: style, launch: launch, onExit: { _ in })
                .environment(\.workbenchPalette, palette)
            let host = NSHostingView(rootView: content)
            host.frame = CGRect(x: 0, y: 0, width: 640, height: 360)
            let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.contentView = host
            host.layoutSubtreeIfNeeded()
            let deadline = Date().addingTimeInterval(6)
            while !FileManager.default.fileExists(atPath: result.path) && Date() < deadline {
                try await Task.sleep(for: .milliseconds(20))
            }
            defer { control.stop(); window.contentView = nil }
            let output = try String(contentsOf: result, encoding: .utf8)
            let foreground = try XCTUnwrap(SwiftTerm.Color.parse(palette["text"]))
            let background = try XCTUnwrap(SwiftTerm.Color.parse(palette["base"]))
            XCTAssertTrue(output.contains("10;" + foreground.formatAsXcolor()), output)
            XCTAssertTrue(output.contains("11;" + background.formatAsXcolor()), output)
            XCTAssertTrue(output.contains(name == "dark" ? "?997;1n" : "?997;2n"), output)
            XCTAssertEqual(control.existingView?.minimumContrastRatio, 4.5)
        }
    }

    @MainActor func testCachedLightInputBackgroundRemainsReadableInDarkTerminal() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let style = SharedTerminalStyle(configURL: folder.appendingPathComponent("terminal.json"))
        let view = FileDropTerminalView(frame: CGRect(x: 0, y: 0, width: 640, height: 360), options: TerminalOptions())
        style.apply(to: view, palette: .dark)
        // Codex may retain the old light input surface while using the new
        // terminal default foreground, which previously became light on light.
        let attribute = Attribute(fg: .defaultColor, bg: .trueColor(red: 235, green: 237, blue: 242), style: .none)
        let rendered = try XCTUnwrap(view.withTerminal { _ in view.getAttributes(attribute, withUrl: false) })
        let foreground = try XCTUnwrap((rendered[.foregroundColor] as? NSColor)?.usingColorSpace(.sRGB))
        let background = try XCTUnwrap((rendered[.backgroundColor] as? NSColor)?.usingColorSpace(.sRGB))
        func rgb(_ color: NSColor) -> ColorContrast.RGB {
            .init(red: color.redComponent, green: color.greenComponent, blue: color.blueComponent)
        }
        XCTAssertGreaterThanOrEqual(ColorContrast.ratio(rgb(foreground), rgb(background)), 4.49)
        XCTAssertEqual(background, NSColor(srgbRed: 235/255, green: 237/255, blue: 242/255, alpha: 1))
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
