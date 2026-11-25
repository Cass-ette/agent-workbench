import SwiftUI

enum AppearanceMode: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: String { rawValue }
    var title: String { switch self { case .system: "跟随系统"; case .light: "浅色 · Latte"; case .dark: "深色 · Mocha" } }
    var colorScheme: ColorScheme? { switch self { case .system: nil; case .light: .light; case .dark: .dark } }
}

@MainActor final class WorkbenchAppearance: ObservableObject {
    private let defaults: UserDefaults
    @Published var mode: AppearanceMode { didSet { defaults.set(mode.rawValue, forKey: "appearance.mode") } }
    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        mode = AppearanceMode(rawValue: defaults.string(forKey: "appearance.mode") ?? "") ?? .system
    }
}

private struct WorkbenchPaletteKey: EnvironmentKey {
    static let defaultValue = WorkbenchPalette.light
}
extension EnvironmentValues {
    var workbenchPalette: WorkbenchPalette {
        get { self[WorkbenchPaletteKey.self] }
        set { self[WorkbenchPaletteKey.self] = newValue }
    }
}
extension WorkbenchPalette {
    func color(_ key: String) -> Color { Color(nsColor: NSColor(hex: self[key])) }
}

struct WorkbenchRoot: View {
    @ObservedObject var model: ManagerModel
    @ObservedObject var updates: WorkbenchUpdates
    @ObservedObject var appearance: WorkbenchAppearance
    @Environment(\.colorScheme) private var systemScheme
    private var palette: WorkbenchPalette {
        (appearance.mode.colorScheme ?? systemScheme) == .dark ? .dark : .light
    }
    var body: some View {
        ManagerView(model: model, updates: updates)
            .environmentObject(appearance)
            .environment(\.workbenchPalette, palette)
            .tint(palette.color("accent"))
            .foregroundStyle(palette.color("text"))
            .background(palette.color("base"))
            .preferredColorScheme(appearance.mode.colorScheme)
            .onChange(of: palette, initial: true) { _, value in model.terminalStyle.setAppearance(value) }
    }
}

struct AppearancePicker: View {
    @EnvironmentObject private var appearance: WorkbenchAppearance
    var body: some View {
        Picker("外观", selection: $appearance.mode) {
            ForEach(AppearanceMode.allCases) { Text($0.title).tag($0) }
        }.help("Catppuccin 窗口与终端配色")
    }
}
