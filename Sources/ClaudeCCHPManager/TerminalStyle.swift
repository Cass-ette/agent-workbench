import AppKit
import Combine
import Foundation
import SwiftTerm

struct TerminalStyleSettings: Codable, Equatable {
    var fontName = "SFMono-Regular"
    var fontSize = 13.0
    var foreground = "#D7DEE8"
    var background = "#101317"
    var cursor = "#0A84FF"
    var scrollback = 5000

    static let defaults = TerminalStyleSettings()
}

struct TerminalThemePreset: Identifiable {
    let id: String
    let title: String
    let foreground: String
    let background: String
    let cursor: String

    static let all = [
        TerminalThemePreset(id: "midnight", title: "午夜", foreground: "#D7DEE8", background: "#101317", cursor: "#0A84FF"),
        TerminalThemePreset(id: "graphite", title: "石墨", foreground: "#E6E2D8", background: "#18191A", cursor: "#E6A36F"),
        TerminalThemePreset(id: "paper", title: "纸白", foreground: "#20252B", background: "#F2F3EF", cursor: "#007AFF"),
    ]
}

@MainActor
final class SharedTerminalStyle: ObservableObject {
    @Published private(set) var settings = TerminalStyleSettings.defaults
    @Published private(set) var errorMessage = ""

    let configURL: URL

    init() {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        configURL = support.appendingPathComponent("CCHPModelManager/terminal.json")
        if !FileManager.default.fileExists(atPath: configURL.path) {
            save(TerminalStyleSettings.defaults)
        } else {
            reload()
        }
    }

    var font: NSFont {
        NSFont(name: settings.fontName, size: settings.fontSize)
            ?? NSFont.monospacedSystemFont(ofSize: settings.fontSize, weight: .regular)
    }

    func update(_ value: TerminalStyleSettings) {
        var normalized = value
        normalized.fontSize = min(max(normalized.fontSize, 9), 24)
        normalized.scrollback = min(max(normalized.scrollback, 500), 20_000)
        normalized.foreground = Self.validHex(normalized.foreground) ? normalized.foreground.uppercased() : settings.foreground
        normalized.background = Self.validHex(normalized.background) ? normalized.background.uppercased() : settings.background
        normalized.cursor = Self.validHex(normalized.cursor) ? normalized.cursor.uppercased() : settings.cursor
        save(normalized)
    }

    @discardableResult
    func reload() -> Bool {
        do {
            let data = try Data(contentsOf: configURL)
            let decoded = try JSONDecoder().decode(TerminalStyleSettings.self, from: data)
            guard Self.validHex(decoded.foreground), Self.validHex(decoded.background), Self.validHex(decoded.cursor) else {
                throw CocoaError(.fileReadCorruptFile)
            }
            settings = decoded
            errorMessage = ""
            return true
        } catch {
            errorMessage = "配置文件无法读取：\(error.localizedDescription)"
            return false
        }
    }

    func openConfigFile() {
        NSWorkspace.shared.open(configURL)
    }

    func apply(to view: LocalProcessTerminalView) {
        view.font = font
        view.nativeForegroundColor = NSColor(hex: settings.foreground)
        view.nativeBackgroundColor = NSColor(hex: settings.background)
        view.caretColor = NSColor(hex: settings.cursor)
    }

    private func save(_ value: TerminalStyleSettings) {
        do {
            let directory = configURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(value)
            try data.write(to: configURL, options: .atomic)
            settings = value
            errorMessage = ""
        } catch {
            errorMessage = "配置文件无法保存：\(error.localizedDescription)"
        }
    }

    private static func validHex(_ value: String) -> Bool {
        let digits = value.hasPrefix("#") ? String(value.dropFirst()) : value
        return digits.count == 6 && digits.allSatisfy(\.isHexDigit)
    }
}

extension NSColor {
    convenience init(hex: String) {
        let digits = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        let red = UInt8(digits.prefix(2), radix: 16) ?? 16
        let green = UInt8(digits.dropFirst(2).prefix(2), radix: 16) ?? 19
        let blue = UInt8(digits.suffix(2), radix: 16) ?? 23
        self.init(
            srgbRed: CGFloat(red) / 255,
            green: CGFloat(green) / 255,
            blue: CGFloat(blue) / 255,
            alpha: 1
        )
    }
}
