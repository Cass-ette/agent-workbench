import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            guard let window = NSApp.windows.first(where: { $0.isVisible }) else { return }
            window.title = "Agent 工作台"
            window.titleVisibility = .hidden
            window.titlebarAppearsTransparent = true
            window.isOpaque = false
            window.backgroundColor = .clear
            window.isMovableByWindowBackground = false
            window.setFrameAutosaveName("ClaudeCCHPManager")
        }
    }
}

@main
struct ClaudeCCHPManagerApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = ManagerModel()
    @StateObject private var updates = WorkbenchUpdates()
    @StateObject private var appearance = WorkbenchAppearance()

    var body: some Scene {
        WindowGroup {
            WorkbenchRoot(model: model, updates: updates, appearance: appearance)
                .frame(minWidth: 960, minHeight: 680)
                .preferredColorScheme(appearance.mode.colorScheme)
                .task { await model.load(); model.restoreRemoteService() }
                .task {
                    while !Task.isCancelled {
                        await updates.check()
                        do { try await Task.sleep(for: .seconds(900)) } catch { break }
                    }
                }
        }
        .windowStyle(.hiddenTitleBar)
        .windowToolbarStyle(.unifiedCompact)
        .defaultSize(width: 1180, height: 780)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("检查更新…") { updates.open() }
            }
            CommandMenu("外观") {
                Picker("外观", selection: $appearance.mode) {
                    ForEach(AppearanceMode.allCases) { Text($0.title).tag($0) }
                }
            }
        }
    }
}
