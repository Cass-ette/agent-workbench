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
    var followAppearance = true

    enum CodingKeys: String, CodingKey { case fontName, fontSize, foreground, background, cursor, scrollback, followAppearance }
    init() {}
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        fontName = try values.decodeIfPresent(String.self, forKey: .fontName) ?? "SFMono-Regular"
        fontSize = try values.decodeIfPresent(Double.self, forKey: .fontSize) ?? 13
        foreground = try values.decodeIfPresent(String.self, forKey: .foreground) ?? "#D7DEE8"
        background = try values.decodeIfPresent(String.self, forKey: .background) ?? "#101317"
        cursor = try values.decodeIfPresent(String.self, forKey: .cursor) ?? "#0A84FF"
        scrollback = try values.decodeIfPresent(Int.self, forKey: .scrollback) ?? 5000
        // Migrate the old built-in default; preserve deliberately customized colors.
        followAppearance = try values.decodeIfPresent(Bool.self, forKey: .followAppearance)
            ?? (foreground.uppercased() == "#D7DEE8" && background.uppercased() == "#101317" && cursor.uppercased() == "#0A84FF")
    }

    static let defaults = TerminalStyleSettings()
}

struct TerminalThemePreset: Identifiable {
    let id: String
    let title: String
    let foreground: String
    let background: String
    let cursor: String

    static let all = [
        TerminalThemePreset(id: "latte", title: "Catppuccin Latte", foreground: "#4C4F69", background: "#EFF1F5", cursor: "#DC8A78"),
        TerminalThemePreset(id: "mocha", title: "Catppuccin Mocha", foreground: "#CDD6F4", background: "#1E1E2E", cursor: "#F5E0DC"),
        TerminalThemePreset(id: "midnight", title: "午夜", foreground: "#D7DEE8", background: "#101317", cursor: "#0A84FF"),
        TerminalThemePreset(id: "graphite", title: "石墨", foreground: "#E6E2D8", background: "#18191A", cursor: "#E6A36F"),
        TerminalThemePreset(id: "paper", title: "纸白", foreground: "#20252B", background: "#F2F3EF", cursor: "#007AFF"),
    ]
}

@MainActor
final class SharedTerminalStyle: ObservableObject {
    @Published private(set) var settings = TerminalStyleSettings.defaults
    @Published private(set) var errorMessage = ""
    @Published private(set) var palette = WorkbenchPalette.light

    let configURL: URL

    init(configURL: URL? = nil) {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        self.configURL = configURL ?? support.appendingPathComponent("CCHPModelManager/terminal.json")
        if !FileManager.default.fileExists(atPath: self.configURL.path) {
            save(TerminalStyleSettings.defaults)
        } else {
            reload()
        }
    }

    var font: NSFont {
        NSFont(name: settings.fontName, size: settings.fontSize)
            ?? NSFont.monospacedSystemFont(ofSize: settings.fontSize, weight: .regular)
    }

    var effectiveSettings: TerminalStyleSettings {
        var value = settings
        if value.followAppearance {
            value.foreground = palette["text"]
            value.background = palette["base"]
            value.cursor = palette["rosewater"]
        }
        return value
    }

    func setAppearance(_ value: WorkbenchPalette) {
        if palette != value { palette = value }
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
        let settings = effectiveSettings
        view.font = font
        view.nativeForegroundColor = NSColor(hex: settings.foreground)
        view.nativeBackgroundColor = NSColor(hex: settings.background)
        view.caretColor = NSColor(hex: settings.cursor)
        if settings.followAppearance {
            view.installColors(palette.ansi.compactMap { SwiftTerm.Color.parse($0) })
        } else {
            view.installColors(SwiftTerm.Color.defaultInstalledColors)
        }
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
